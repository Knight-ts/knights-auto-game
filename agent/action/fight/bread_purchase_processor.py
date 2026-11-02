# -*- coding: utf-8 -*-
"""市场购买面包（城堡 → 市场 → 购买 → 面包 → 数量 → 确定）。

功能任务、月度循环共用：

- 「全部」= 点购买弹窗里的 ↑（最大，等于市场剩余）；
- 「指定数量」= 用 +/− 把数量调到目标值，目标超过市场剩余时按剩余封顶；
- 面包市场库存为 0 时记录「无货」并跳过，不报错。

对应任务：``Func_BuyBread``（功能：市场购买面包）。
月度循环里由 ``CastleBreadMonth`` 选项决定几月自动购买。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger
from utils import game_state

import action.fight.fight_utils as fight_utils
from action.fight.castle_resource_processor import open_castle_page

MONTH_NODE = "CastleBreadMonth"
MODE_NODE = "CastleBreadQuantityMode"
VALUE_NODE = "CastleBreadQuantityValue"

PAGE_CHECK_NODE = "Castle_PageCheck"
MARKET_ENTRY_NODE = "Castle_MarketEntry"
BUY_TAB_NODE = "Castle_MarketBuyTab"
BUY_PAGE_CHECK_NODE = "Castle_MarketBuyPageCheck"
BREAD_ROW_NODE = "Castle_BreadRow"
BUY_OPEN_NODE = "Castle_BreadBuyOpen"
DIALOG_CHECK_NODE = "Castle_BuyDialogCheck"
QUANTITY_NODE = "Castle_BuyQuantity"
REMAINING_NODE = "Castle_BuyRemaining"
BALANCE_NODE = "Castle_BuyBalance"
CONFIRM_NODE = "Castle_BuyConfirm"
CANCEL_NODE = "Castle_BuyCancel"

PLUS_XY = (455, 812)
MINUS_XY = (263, 812)
MAX_XY = (525, 812)

CLICK_INTERVAL = 0.02
MAX_CLICKS = 20000
PURCHASE_RECORD_FILE = "bread_purchases.jsonl"


# ---------------------------------------------------------------- 配置读取


def _expected_text(context: Context, node: str, default: str = "") -> str:
    data = context.get_node_data(node) or {}
    expected = (
        (data.get("recognition") or {}).get("param", {}).get("expected")
        or data.get("expected")
        or default
    )
    if isinstance(expected, (list, tuple)):
        expected = expected[0] if expected else default
    return str(expected).strip() if expected is not None else default


def configured_buy_month(context: Context) -> int:
    """几月自动购买；0 表示不购买。"""
    text = _expected_text(context, MONTH_NODE, "0")
    try:
        month = int(float(text))
    except (TypeError, ValueError):
        month = 0
    return month if 1 <= month <= 12 else 0


def configured_quantity_mode(context: Context) -> str:
    mode = _expected_text(context, MODE_NODE, "all").lower()
    return "num" if mode in {"num", "number", "指定数量"} else "all"


def configured_quantity_value(context: Context) -> int:
    text = _expected_text(context, VALUE_NODE, "0")
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- 基础动作


def _screencap(context: Context) -> Optional[Any]:
    job = context.tasker.controller.post_screencap().wait()
    if not job.succeeded:
        return None
    return job.get()


def _hit(context: Context, node: str, image: Any) -> bool:
    try:
        return bool(context.run_recognition(node, image).hit)
    except Exception:  # noqa: BLE001
        return False


def _text(context: Context, node: str, image: Any) -> str:
    try:
        detail = context.run_recognition(node, image)
        if detail and detail.hit and detail.best_result is not None:
            return (detail.best_result.text or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _wait_node(context: Context, node: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return False
        image = _screencap(context)
        if image is not None and _hit(context, node, image):
            return True
        time.sleep(0.35)
    return False


def _wait_dialog_closed(context: Context, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        image = _screencap(context)
        if image is None:
            time.sleep(0.3)
            continue
        if not _hit(context, DIALOG_CHECK_NODE, image):
            return True
        # 弹窗里也有「确定」，这里只等它关闭，避免重复点确定。
        time.sleep(0.4)
    return False


def _click_xy(context: Context, x: int, y: int) -> None:
    context.tasker.controller.post_click(x, y).wait()


def _read_quantity(context: Context) -> Optional[int]:
    image = _screencap(context)
    if image is None:
        return None
    text = _text(context, QUANTITY_NODE, image)
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else None


def _read_row_stock(context: Context) -> Optional[int]:
    """市场购买页面包行里的「(剩余 N)」；读不到返回 None。"""
    image = _screencap(context)
    if image is None:
        return None
    text = _text(context, "Castle_BreadRowStock", image)
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else None


def _read_remaining(context: Context) -> Optional[int]:
    image = _screencap(context)
    if image is None:
        return None
    text = _text(context, REMAINING_NODE, image)
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group(0)) if match else None


def _read_balance(context: Context) -> tuple[Optional[int], Optional[int]]:
    """购买弹窗「花费 / 余额」；余额可能是 1.7m 这类缩写。"""
    image = _screencap(context)
    if image is None:
        return None, None
    text = _text(context, BALANCE_NODE, image)
    parts = re.split(r"/", text)
    if len(parts) != 2:
        return None, None

    def _parse(value: str) -> Optional[int]:
        value = value.strip().replace(",", "").replace("，", "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM]?)", value)
        if not match:
            return None
        number = float(match.group(1))
        suffix = match.group(2)
        return int(round(number * {"k": 1e3, "m": 1e6}.get(suffix.lower(), 1.0)))

    return _parse(parts[0]), _parse(parts[1])


# ---------------------------------------------------------------- 打开弹窗


def _open_buy_dialog(context: Context) -> str:
    """打开面包购买弹窗。

    返回 ``"ok"`` / ``"no_stock"`` / ``"failed"``。
    """
    if not fight_utils.ensure_at_bigmap(context):
        logger.error("购买面包：无法回到大地图")
        return "failed"
    if context.tasker.stopping:
        return "failed"

    if not open_castle_page(context):
        logger.error("购买面包：无法打开城堡页")
        return "failed"
    logger.info("购买面包：已进入城堡页")

    if not fight_utils._task_succeeded(context.run_task(MARKET_ENTRY_NODE)):
        logger.error("购买面包：点击「市场」失败")
        return "failed"
    if not _wait_node(context, BUY_TAB_NODE, timeout=6):
        logger.error("购买面包：市场页没有出现「购买」")
        return "failed"
    logger.info("购买面包：已打开市场页")

    if not fight_utils._task_succeeded(context.run_task(BUY_TAB_NODE)):
        logger.error("购买面包：点击「购买」页签失败")
        return "failed"
    # 回到列表顶部，确保面包在第一行。
    try:
        context.tasker.controller.post_swipe(360, 470, 360, 1080, 350).wait()
        time.sleep(0.4)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"购买面包：列表回顶滑动失败：{exc}")

    # 以「面包」行作为购买页判据：表头 OCR 经常把“市场库存”识成“币场库仔”。
    if not _wait_node(context, BREAD_ROW_NODE, timeout=8):
        image = _screencap(context)
        debug_text = ""
        if image is not None:
            try:
                detail = context.run_recognition(
                    BUY_PAGE_CHECK_NODE,
                    image,
                    pipeline_override={
                        BUY_PAGE_CHECK_NODE: {"expected": [".*"]}
                    },
                )
                if detail and detail.best_result is not None:
                    debug_text = (detail.best_result.text or "").strip()
            except Exception:  # noqa: BLE001
                pass
        logger.error(
            "购买面包：没有进入市场购买列表"
            f"（未识别到「面包」行，表头 OCR={debug_text!r}）"
        )
        return "failed"
    logger.info("购买面包：已切到购买页")

    row_ok = False
    for attempt in range(3):
        image = _screencap(context)
        if image is not None and _hit(context, BREAD_ROW_NODE, image):
            row_ok = True
            break
        logger.warning(f"购买面包：第 {attempt + 1}/3 次没有找到「面包」行，尝试重新回顶")
        try:
            context.tasker.controller.post_swipe(360, 470, 360, 1080, 350).wait()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    if not row_ok:
        logger.error("购买面包：购买列表里没有识别到「面包」")
        return "failed"

    stock = _read_row_stock(context)
    if stock is not None:
        logger.info(f"购买面包：面包行市场剩余={stock}")
        if stock <= 0:
            logger.info("购买面包：面包无货（市场剩余 0），记录并跳过")
            return "no_stock"

    if not fight_utils._task_succeeded(context.run_task(BREAD_ROW_NODE)):
        logger.error("购买面包：选中「面包」失败")
        return "failed"

    if not fight_utils._task_succeeded(context.run_task(BUY_OPEN_NODE)):
        logger.error("购买面包：点击右下角「购买」失败")
        return "failed"
    if not _wait_node(context, DIALOG_CHECK_NODE, timeout=8):
        # 面包售罄时右下角「购买」是灰的，点了不会弹数量窗；
        # 这里按无货跳过，不报错（下个月再试）。
        logger.info("购买面包：没有弹出数量窗口（面包可能已售罄/无货），跳过")
        return "no_stock"
    return "ok"


# ---------------------------------------------------------------- 设置数量


def _click_many(context: Context, x: int, y: int, clicks: int) -> bool:
    """连续点同一个按钮；任务被停止时返回 False。"""
    for index in range(max(0, clicks)):
        if context.tasker.stopping:
            return False
        _click_xy(context, x, y)
        time.sleep(CLICK_INTERVAL)
        if index and index % 200 == 0:
            logger.info(f"购买面包：已点击 {index}/{clicks} 次")
    return True


def _set_quantity(context: Context, target: int, remaining: int) -> Optional[int]:
    """把弹窗数量调到 target；返回最终数量。

    +/− 的步长不一定固定（低数值时 1，高数值可能按 10/100 递增），
    所以先点一下量出实际步长，再按步长批量补，最后单点校正。
    """
    current = _read_quantity(context)
    if current is None:
        logger.warning("购买面包：读不到当前数量，按 1 处理")
        current = 1

    if target >= remaining:
        _click_xy(context, *MAX_XY)
        time.sleep(0.4)
        return _read_quantity(context)
    if target == current:
        return current

    total_clicks = 0
    for _round in range(8):
        if context.tasker.stopping:
            return None
        if current == target:
            break
        if current < target:
            x, y, delta = PLUS_XY[0], PLUS_XY[1], 1
        else:
            x, y, delta = MINUS_XY[0], MINUS_XY[1], -1

        # 先点一下，量出这一档的实际步长。
        _click_xy(context, x, y)
        total_clicks += 1
        time.sleep(0.15)
        measured = _read_quantity(context)
        step = 1 if measured is None else abs(measured - current)
        if step <= 0:
            step = 1
        current = current + delta * step if measured is None else measured

        diff = abs(target - current)
        if diff == 0:
            break

        # 留 1 步余量，避免正好跨过步长边界。
        bulk = max(0, diff // step - 1)
        if bulk <= 0:
            continue
        if total_clicks + bulk > MAX_CLICKS:
            logger.warning(
                f"购买面包：需要点击超过 {MAX_CLICKS} 次，改为直接点「最大」"
            )
            _click_xy(context, *MAX_XY)
            time.sleep(0.4)
            return _read_quantity(context)

        logger.info(
            f"购买面包：当前 {current}，目标 {target}，"
            f"实测步长 {step}，批量点 {bulk} 次"
        )
        if not _click_many(context, x, y, bulk):
            return None
        total_clicks += bulk
        time.sleep(0.25)
        measured = _read_quantity(context)
        if measured is not None:
            current = measured

    # 最后用单点把误差压到 0（最多 30 次；数值来回跳说明步长跨档，停止）。
    seen = set()
    for _ in range(30):
        if context.tasker.stopping or current == target:
            break
        if current in seen:
            logger.warning(f"购买面包：数量在 {current} 附近来回跳，停止调整")
            break
        seen.add(current)
        x, y = PLUS_XY if current < target else MINUS_XY
        _click_xy(context, x, y)
        time.sleep(0.08)
        measured = _read_quantity(context)
        if measured is None:
            current += 1 if current < target else -1
        else:
            current = measured

    if current != target:
        logger.warning(f"购买面包：目标 {target}，实际 {current}，按实际数量继续")
    else:
        logger.info(f"购买面包：数量已调到 {target}（共点击 {total_clicks} 次）")
    return current


# ---------------------------------------------------------------- 记录


def _append_purchase(record: dict) -> None:
    path = os.path.join(os.getcwd(), "table", PURCHASE_RECORD_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"购买面包：写购买记录失败：{exc}")


def _record_no_stock(
    month: Optional[int], source: str, remaining: int = 0
) -> None:
    year, state_month = game_state.year_month()
    _append_purchase(
        {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "year": year,
            "month": month if month is not None else state_month,
            "item": "面包",
            "quantity": 0,
            "cost": 0,
            "remaining_before": remaining,
            "mode": "all",
            "source": source,
            "status": "no_stock",
            "note": "面包市场库存为 0，跳过",
        }
    )


# ---------------------------------------------------------------- 主流程


def buy_bread(
    context: Context,
    mode: str = "all",
    amount: int = 0,
    source: str = "manual",
    month: Optional[int] = None,
) -> str:
    """执行一次面包购买。

    返回：
    - ``"ok"``：买到了（数量见日志）
    - ``"no_stock"``：市场剩余为 0，已跳过
    - ``"cancelled"``：数量为 0 / 用户任务停止，已取消
    - ``"failed"``：流程失败
    """
    logger.info(f"=== 购买面包（模式={mode}，数量={amount}）===")
    status = _open_buy_dialog(context)
    if status == "no_stock":
        _record_no_stock(month, source)
        fight_utils.ensure_at_bigmap(context)
        return "no_stock"
    if status != "ok":
        fight_utils.ensure_at_bigmap(context)
        return "failed"

    remaining = _read_remaining(context)
    quantity = _read_quantity(context)
    logger.info(f"购买面包：弹窗数量={quantity}，市场剩余={remaining}")
    if remaining is None:
        logger.warning("购买面包：读不到市场剩余，按 1 个上限处理")
        remaining = 1

    if remaining <= 0:
        logger.info("购买面包：面包市场库存为 0（无货），跳过")
        _record_no_stock(month, source, remaining)
        fight_utils._task_succeeded(context.run_task(CANCEL_NODE))
        fight_utils.ensure_at_bigmap(context)
        return "no_stock"

    cost_before, balance_before = _read_balance(context)
    logger.info(f"购买面包：银币花费={cost_before}，余额={balance_before}")

    if mode == "num":
        if amount <= 0:
            logger.warning("购买面包：指定数量为 0，取消购买")
            fight_utils._task_succeeded(context.run_task(CANCEL_NODE))
            fight_utils.ensure_at_bigmap(context)
            return "cancelled"
        target = min(amount, remaining)
        if target != amount:
            logger.info(f"购买面包：指定 {amount} 超过市场剩余，按 {target} 购买")
        final_quantity = _set_quantity(context, target, remaining)
    else:
        logger.info("购买面包：全部（点最大）")
        _click_xy(context, *MAX_XY)
        time.sleep(0.4)
        final_quantity = _read_quantity(context)

    if final_quantity is None:
        final_quantity = remaining if mode != "num" else min(max(amount, 1), remaining)
    if final_quantity <= 0:
        logger.warning("购买面包：数量为 0，取消购买")
        fight_utils._task_succeeded(context.run_task(CANCEL_NODE))
        fight_utils.ensure_at_bigmap(context)
        return "cancelled"

    cost, _balance = _read_balance(context)
    if cost is None and cost_before is not None and quantity:
        cost = int(round(cost_before / max(quantity, 1) * final_quantity))
    if cost is None:
        cost = 0

    if not fight_utils._task_succeeded(context.run_task(CONFIRM_NODE)):
        logger.error("购买面包：点击「确定」失败")
        fight_utils.ensure_at_bigmap(context)
        return "failed"

    if not _wait_dialog_closed(context, timeout=8):
        logger.warning("购买面包：确定后弹窗没有关闭，再点一次确定")
        fight_utils._task_succeeded(context.run_task(CONFIRM_NODE))
        _wait_dialog_closed(context, timeout=5)

    year, state_month = game_state.year_month()
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "year": year,
        "month": month if month is not None else state_month,
        "item": "面包",
        "quantity": final_quantity,
        "cost": cost,
        "remaining_before": remaining,
        "mode": mode,
        "source": source,
        "status": "ok",
    }
    _append_purchase(record)
    logger.info(
        f"购买面包完成：{final_quantity} 个，花费约 {cost or '未知'} 银币"
        f"（购买前市场剩余 {remaining}）"
    )

    if not fight_utils.ensure_at_bigmap(context):
        logger.warning("购买面包：购买后未能回到大地图")
    return "ok"


def maybe_buy_bread(context: Context, month: int) -> Optional[str]:
    """月度循环入口：配置月份与当前月一致时购买，否则返回 None（跳过）。"""
    configured = configured_buy_month(context)
    if configured <= 0 or configured != month:
        return None
    mode = configured_quantity_mode(context)
    amount = configured_quantity_value(context)
    logger.info(f"月度循环：{month} 月触发购买面包（模式={mode}，数量={amount}）")
    return buy_bread(context, mode=mode, amount=amount, source="yearly", month=month)


# ---------------------------------------------------------------- 任务入口


@AgentServer.custom_action("BreadPurchaseProcessor")
class BreadPurchaseProcessor(CustomAction):
    """功能：市场购买面包（手动运行时忽略「购买月份」，立即执行）。"""

    def run(
        self, context: Context, _argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        if not fight_utils.ensure_at_bigmap(context):
            logger.error("购买面包：无法回到大地图")
            return CustomAction.RunResult(success=False)
        month = fight_utils.Map_CheckCurrentMonth(context)
        mode = configured_quantity_mode(context)
        amount = configured_quantity_value(context)
        result = buy_bread(
            context,
            mode=mode,
            amount=amount,
            source="manual",
            month=month if month >= 1 else None,
        )
        return CustomAction.RunResult(success=result in {"ok", "no_stock", "cancelled"})
