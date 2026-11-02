# -*- coding: utf-8 -*-
"""城堡资源记录（粮食存量 / 可调配量）。

功能任务、月度循环共用同一套实现：

1. 从大地图打开 城堡 页；
2. OCR 右上角资源面板的「粮食存量」和「可调配量」（可调配量作为城堡银币）；
3. 追加记录到 ``table/castle_resources.json``，并写一条日志；
4. 粮食 / 银币任一低于用户阈值时：推送提醒、弹窗、写
   ``temp/castle_resource_alert.json`` 标记，并 ``post_stop`` 暂停任务。

阈值 0 表示不检查该项；记录文件只保留最近 200 条。

对应任务：``Func_CastleResource``（功能：城堡资源记录）。
月度循环里由 ``CastleResourceRecordMonth`` 选项决定几月自动记录。
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

MONTH_NODE = "CastleResourceRecordMonth"
FOOD_THRESHOLD_NODE = "CastleFoodThreshold"
SILVER_THRESHOLD_NODE = "CastleSilverThreshold"

PAGE_CHECK_NODE = "Castle_PageCheck"
FOOD_NODE = "Castle_FoodStock"
SILVER_NODE = "Castle_SilverPool"

RECORD_FILE = "castle_resources.json"
ALERT_FILE = "castle_resource_alert.json"
MAX_RECORDS = 200

STEP_WAIT = 0.8
OPEN_TIMEOUT = 8.0


# ---------------------------------------------------------------- 配置读取


def _expected_text(context: Context, node: str, default: str = "") -> str:
    """读取 pipeline 节点的 expected（选项通过 pipeline_override 写进来）。"""
    data = context.get_node_data(node) or {}
    expected = (
        (data.get("recognition") or {}).get("param", {}).get("expected")
        or data.get("expected")
        or default
    )
    if isinstance(expected, (list, tuple)):
        expected = expected[0] if expected else default
    return str(expected).strip() if expected is not None else default


def _expected_int(context: Context, node: str, default: int = 0) -> int:
    text = _expected_text(context, node, str(default))
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def configured_record_month(context: Context) -> int:
    """几月自动记录；0 表示不记录。"""
    month = _expected_int(context, MONTH_NODE, 0)
    return month if 1 <= month <= 12 else 0


def configured_food_threshold(context: Context) -> int:
    return max(0, _expected_int(context, FOOD_THRESHOLD_NODE, 0))


def configured_silver_threshold(context: Context) -> int:
    return max(0, _expected_int(context, SILVER_THRESHOLD_NODE, 0))


# ---------------------------------------------------------------- 数值解析


def parse_number(text: str) -> Optional[int]:
    """把 OCR 文本解析成整数，兼容 1,234 / 1.2k / 3.4m / 5万。"""
    raw = (text or "").strip().replace(",", "").replace("，", "")
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM万]?)", raw)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    multiplier = {
        "k": 1_000,
        "K": 1_000,
        "m": 1_000_000,
        "M": 1_000_000,
        "万": 10_000,
    }.get(suffix, 1)
    return int(round(value * multiplier))


def parse_first_int(text: str) -> Optional[int]:
    match = re.search(r"\d+", (text or "").replace(",", "").replace("，", ""))
    return int(match.group(0)) if match else None


# ---------------------------------------------------------------- 画面读写


def _screencap(context: Context) -> Optional[Any]:
    job = context.tasker.controller.post_screencap().wait()
    if not job.succeeded:
        return None
    return job.get()


def _recognition_text(context: Context, node: str, image: Any) -> str:
    detail = context.run_recognition(node, image)
    if not detail or not detail.hit or detail.best_result is None:
        return ""
    return (detail.best_result.text or "").strip()


def _wait_node(
    context: Context, node: str, timeout: float = OPEN_TIMEOUT, interval: float = 0.4
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return False
        image = _screencap(context)
        if image is not None and context.run_recognition(node, image).hit:
            return True
        time.sleep(interval)
    return False


def open_castle_page(context: Context) -> bool:
    """确保当前停在城堡页；从大地图进入，其它页面先退回大地图。"""
    image = _screencap(context)
    if image is not None and context.run_recognition(PAGE_CHECK_NODE, image).hit:
        return True

    if not fight_utils.ensure_at_bigmap(context):
        logger.error("城堡资源：无法回到大地图")
        return False
    if context.tasker.stopping:
        return False

    if not fight_utils._task_succeeded(context.run_task("UI_CastlePage")):
        logger.error("城堡资源：点击底部「城堡」失败")
        return False
    if not _wait_node(context, PAGE_CHECK_NODE):
        logger.error("城堡资源：打开城堡页后没有识别到「粮食存量」")
        return False
    return True


def read_castle_resources(context: Context) -> Optional[dict]:
    """打开城堡页，读粮食存量与可调配量；失败返回 None。"""
    if not open_castle_page(context):
        return None

    food = silver = None
    for attempt in range(3):
        image = _screencap(context)
        if image is None:
            time.sleep(0.4)
            continue
        food_text = _recognition_text(context, FOOD_NODE, image)
        silver_text = _recognition_text(context, SILVER_NODE, image)
        if food_text:
            food = parse_number(food_text)
        if silver_text:
            silver = parse_number(silver_text)
        if food is not None and silver is not None:
            logger.info(
                f"城堡资源：粮食存量 OCR={food_text!r} → {food}，"
                f"可调配量 OCR={silver_text!r} → {silver}"
            )
            break
        logger.warning(
            f"城堡资源：第 {attempt + 1}/3 次识读不完整"
            f"（粮食={food_text!r}，可调配量={silver_text!r}）"
        )
        time.sleep(0.6)

    if food is None or silver is None:
        logger.error("城堡资源：粮食存量 / 可调配量识别失败")
        return None
    return {"food": food, "silver": silver}


# ---------------------------------------------------------------- 记录 / 暂停


def _table_path(name: str) -> str:
    return os.path.join(os.getcwd(), "table", name)


def _temp_path(name: str) -> str:
    return os.path.join(os.getcwd(), "temp", name)


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def load_records() -> dict:
    path = _table_path(RECORD_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"城堡资源：读取 {RECORD_FILE} 失败：{exc}")
    return {"records": []}


def append_record(record: dict) -> None:
    data = load_records()
    records = data.get("records") or []
    records.append(record)
    data["records"] = records[-MAX_RECORDS:]
    data["updated"] = record.get("time", "")
    try:
        _atomic_write(_table_path(RECORD_FILE), data)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"城堡资源：写入 {RECORD_FILE} 失败：{exc}")


def _write_alert(record: dict) -> None:
    note = "城堡资源低于阈值，已暂停任务，请补充粮食/银币后再继续"
    payload = dict(record)
    payload.update({"action": "pause", "note": note})
    try:
        _atomic_write(_temp_path(ALERT_FILE), payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"城堡资源：写入暂停标记失败：{exc}")


def _pause_with_alert(
    context: Context, record: dict, content: str, stop: bool = True
) -> None:
    log_text = "城堡资源低于阈值：" + "、".join(record.get("below") or [])
    logger.warning(log_text)
    try:
        from utils.notifier import notify

        notify("城堡资源低于阈值", content)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"城堡资源：推送失败：{exc}")
    try:
        context.run_task(
            "UI_PopInform",
            pipeline_override={"Node.Action.Succeeded": {"content": content}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"城堡资源：弹窗失败：{exc}")
    _write_alert(record)
    if stop:
        context.tasker.post_stop()


def record_castle_resources(
    context: Context,
    month: Optional[int] = None,
    source: str = "manual",
    stop_on_below: bool = True,
) -> Optional[dict]:
    """读取并记录城堡资源；低于阈值时暂停任务。

    返回记录 dict（含 ``paused``），识别失败返回 None。
    调用方负责把游戏带回大地图（本函数在暂停前会先尝试返回）。
    """
    values = read_castle_resources(context)
    if values is None:
        return None

    # 先离开城堡，保证暂停时游戏停在可操作的大地图。
    if not fight_utils.ensure_at_bigmap(context):
        logger.warning("城堡资源：记录后未能回到大地图")

    food_threshold = configured_food_threshold(context)
    silver_threshold = configured_silver_threshold(context)
    below = []
    if food_threshold > 0 and values["food"] < food_threshold:
        below.append("粮食")
    if silver_threshold > 0 and values["silver"] < silver_threshold:
        below.append("银币")

    year, state_month = game_state.year_month()
    if month is None:
        month = state_month
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "year": year,
        "month": month,
        "food": values["food"],
        "silver": values["silver"],
        "food_threshold": food_threshold,
        "silver_threshold": silver_threshold,
        "below": below,
        "action": "paused" if below else "ok",
        "source": source,
    }
    append_record(record)

    logger.info(
        f"城堡资源记录：{year}年{month}月 粮食={values['food']}"
        f"（阈值 {food_threshold or '不检查'}）"
        f" 银币={values['silver']}（阈值 {silver_threshold or '不检查'}）"
        f" → {'低于阈值：' + '、'.join(below) if below else '正常'}"
    )

    if below:
        content = (
            f"城堡资源低于阈值：{('、'.join(below))}。"
            f"粮食 {values['food']}（阈值 {food_threshold}），"
            f"银币 {values['silver']}（阈值 {silver_threshold}）。"
            "任务已暂停，处理后重新运行。"
        )
        _pause_with_alert(context, record, content, stop=stop_on_below)
        record["paused"] = True
    else:
        record["paused"] = False
    return record


def maybe_record_castle_resources(context: Context, month: int) -> Optional[dict]:
    """月度循环入口：配置月份与当前月一致时记录，否则返回 None（跳过）。"""
    configured = configured_record_month(context)
    if configured <= 0 or configured != month:
        return None
    logger.info(f"月度循环：{month} 月触发城堡资源记录（配置 {configured} 月）")
    return record_castle_resources(context, month=month, source="yearly")


# ---------------------------------------------------------------- 任务入口


@AgentServer.custom_action("CastleResourceProcessor")
class CastleResourceProcessor(CustomAction):
    """功能：城堡资源记录（手动运行时忽略「记录月份」，立即执行）。"""

    def run(
        self, context: Context, _argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("=== 功能：城堡资源记录 ===")

        if not fight_utils.ensure_at_bigmap(context):
            logger.error("城堡资源：无法回到大地图")
            return CustomAction.RunResult(success=False)

        month = fight_utils.Map_CheckCurrentMonth(context)
        if month < 1:
            logger.warning("城堡资源：未识别到当前月份，记录里月份留空")

        record = record_castle_resources(
            context,
            month=month if month >= 1 else None,
            source="manual",
            stop_on_below=False,
        )
        if record is None:
            return CustomAction.RunResult(success=False)

        if record.get("paused"):
            logger.info("城堡资源任务完成：已按阈值暂停")
        else:
            logger.info("城堡资源任务完成")
        return CustomAction.RunResult(success=True)
