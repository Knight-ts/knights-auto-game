from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from utils import logger
from utils import game_state
import json
import os
import re
import time
from typing import Any, Optional

import action.fight.fight_utils as fight_utils
import action.fight.scene_return as scene_return


# 年度任务是长流程：任何“看起来点击成功”的动作都必须在有限时间内
# 重新观察到一个支持的状态，否则停止而不是继续消耗任务/资源。
YEARLY_MONTH_RETRIES = 3
YEARLY_RECOVERY_STEPS = 12

# 口粮预交：粮食不足时点「确定」会扣士气、让孩子带上营养不良等负面特性，
# 所以只有确认「粮食充足」才点；否则暂停等玩家补粮。
RATION_SHORT_MARKER = "ration_short.json"


def _yearly_month_retries(context: Context) -> int:
    """读取「任务失败继续轮数」；兼容 OCR 节点的 expected 结构。"""
    data = context.get_node_data("YearlyMonthRetries") or {}
    expected = data.get("recognition", {}).get("param", {}).get("expected", None)
    if expected is None:
        expected = data.get("expected", YEARLY_MONTH_RETRIES)
    if isinstance(expected, (list, tuple)):
        expected = expected[0] if expected else YEARLY_MONTH_RETRIES
    try:
        value = int(str(expected).strip())
    except (TypeError, ValueError):
        value = YEARLY_MONTH_RETRIES
    # 0 = 不限（一直重试到月份推进或手动停止）；负数/脏数据按默认值；
    # 超过 999 视为旧配置里“填个超大数表示不限”的写法，也按不限处理
    if value < 0:
        value = YEARLY_MONTH_RETRIES
    if value > 999:
        return 0
    return value
YEARLY_MONTH_WAIT_SECONDS = 8.0


def _recovery_state(context: Context, img: Any = None) -> str:
    """返回年度流程可处理的稳定状态，不对未知画面做动作。"""
    if img is None:
        img = fight_utils._screencap(context)
    if img is None:
        return "unknown"
    # 交通确认弹窗覆盖在大地图上时，底层 UI_MainWindows 仍会命中。
    # 必须先处理弹窗，否则后续所有城市滑动都会落在遮罩上。
    if context.run_recognition("TravelDialog", img).hit:
        return "travel_dialog"
    if context.run_recognition("UI_MainWindows", img).hit:
        return "bigmap"
    battle_state = fight_utils._detect_battle_state(context, img)
    if battle_state != "unknown":
        return f"battle_{battle_state}"
    if context.run_recognition("BackButton_500ms", img).hit:
        return "backable"
    return "unknown"


def _recover_yearly_to_bigmap(context: Context, max_steps: int = YEARLY_RECOVERY_STEPS) -> bool:
    """有界恢复年度入口/月份间断点；未知画面先按返回路线点几次，仍不动才记录并停止。"""
    last_event = None
    repeated_event_count = 0
    unknown_rounds = 0
    for step in range(max_steps):
        img = fight_utils._screencap(context)
        if img is None:
            logger.error("年度恢复截图失败")
            return False

        # 海盗事件的航海弹窗与战斗准备页都是事件处理器的合法断点。
        # 必须在通用 TravelDialog / PopUpWindowTip 之前分流，否则通用节点
        # 会只点掉弹窗，随后把战斗准备页误判为未知画面。
        if (
            context.run_recognition("Event_PirateRaid_TravelDialog", img).hit
            or context.run_recognition("Event_PirateRaid_BattlePage", img).hit
        ):
            logger.info(
                f"年度流程恢复状态: pirate_raid "
                f"({step + 1}/{max_steps})"
            )
            result = context.run_task("Event_PirateRaid_Dispatch")
            if not fight_utils._task_succeeded(result):
                logger.error("年度流程恢复海盗事件失败")
                return False
            time.sleep(0.5)
            continue

        # 年末/丰收节「年度结算」只有确定按钮，必须先点掉；否则后续
        # _recovery_state 会把它判成未知画面并停止整个年度任务。
        if context.run_recognition("Event_HarvestFestivalAnnuaClosing", img).hit:
            logger.info(
                f"年度流程恢复状态: annual_closing ({step + 1}/{max_steps})"
            )
            result = context.run_task("Event_HarvestFestivalAnnuaClosing")
            if not fight_utils._task_succeeded(result):
                logger.error("年度结算页点击确定失败")
                return False
            time.sleep(0.5)
            continue

        # 最上层通用提示会遮住佣兵加入等事件按钮；旅行弹窗正文含“前往”，
        # 已由更严格的 TravelDialog 单独识别，不能再把所有“提示”都当旅行。
        if context.run_recognition("TravelDialog", img).hit:
            state = "travel_dialog"
        elif context.run_recognition("PopUpWindowTip", img).hit:
            logger.info(
                f"年度流程恢复状态: popup_tip ({step + 1}/{max_steps})"
            )
            result = context.run_task("PopUpWindowTip")
            if not fight_utils._task_succeeded(result):
                return False
            time.sleep(0.5)
            continue
        else:
            event_name = detect_and_manage_event(context, img)
            if event_name:
                logger.info(
                    f"年度流程恢复状态: event_{event_name} "
                    f"({step + 1}/{max_steps})"
                )
                if event_name == last_event:
                    repeated_event_count += 1
                else:
                    last_event = event_name
                    repeated_event_count = 1
                if repeated_event_count > 2:
                    logger.error(f"事件 {event_name} 连续处理无变化，停止恢复")
                    return False
                time.sleep(0.5)
                continue
            state = _recovery_state(context, img)
        logger.info(f"年度流程恢复状态: {state} ({step + 1}/{max_steps})")
        if state == "bigmap":
            return True
        if state in {"battle_start", "battle_ready", "battle_victory"}:
            if state == "battle_victory":
                battle_ok = True
            else:
                battle_ok = fight_utils._process_fighting(context)
            if not battle_ok:
                return False
            if not fight_utils._process_post(context):
                return False
            continue
        if state == "battle_fail":
            logger.error("恢复时识别到战斗失败结算页，不自动重开战斗")
            return False
        if state == "travel_dialog":
            # 只走已知的步行兜底，避免在交通资源状态不明时重复购买/消耗。
            result = context.run_task("TravelDialog_ChooseSlow")
            if not fight_utils._task_succeeded(result):
                return False
            time.sleep(1.0)
            continue
        if state == "backable":
            result = context.run_task("BackButton_500ms")
            if not fight_utils._task_succeeded(result):
                return False
            time.sleep(0.5)
            continue

        # 未知画面：先按 table/return_routes.json 的路线点几次安全位置
        # （下方 → 返回键 → 中心），每点一步都重新截图比对；画面一变就
        # 回到循环顶部重新识别。三步都没变化才记录，避免“盲点”或空转。
        unknown_rounds += 1
        if unknown_rounds > 2:
            logger.error("未知画面连续出现，停止恢复（已记录到 debug/unknown 供分析）")
            return False
        try:
            handled = scene_return.handle_unknown(
                context,
                img,
                source="_recover_yearly_to_bigmap",
                reason="年度流程停在无法识别的画面，按返回路线点了几次仍没有变化",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"未知画面处理失败：{exc}")
            return False
        clicked = " → ".join(
            f"{item.get('step')}"
            + ("（变了）" if item.get("changed") else "（没变）" if item.get("changed") is False else "")
            for item in handled.get("attempts") or []
        ) or "未点击"
        status = handled.get("status")
        if status == "changed":
            logger.info(
                f"未知画面：{clicked}；画面已变化，继续恢复（返回方式：{handled.get('route') or '默认'}）"
            )
            time.sleep(0.4)
            continue
        if status == "recorded":
            record = handled.get("record") or {}
            logger.error(
                f"未知画面：{clicked}；画面没有变化，已记录 {record.get('id', '')}"
                f"（来源 {scene_return.primary_source(handled) or '未知'}）。"
                "停止恢复；可在网页「场景处理 → 未知界面」里看截图并选返回方式"
            )
            return False
        if status == "stuck":
            record = handled.get("record") or {}
            logger.error(
                f"未知画面：{clicked}；历史记录 {record.get('id', '')} 的返回方式没有生效，"
                "已累加命中次数，停止恢复等分析"
            )
            return False
        if status == "stopped":
            logger.info("未知画面处理被手动停止")
            return False
        logger.error(f"未知画面处理结果：{status}（{clicked}），停止恢复以避免盲点")
        return False
    logger.error("年度流程恢复超过有界步数，停止")
    return False


def _wait_for_month_advance(
    context: Context, previous_month: Optional[int], timeout: float = YEARLY_MONTH_WAIT_SECONDS
) -> Optional[int]:
    """等待地图稳定并确认月份确实前进；未知月份返回 None。"""
    deadline = time.monotonic() + timeout
    observed_month = None
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return None
        img = fight_utils._screencap(context)
        if img is None or not context.run_recognition("UI_MainWindows", img).hit:
            time.sleep(0.3)
            continue
        observed_month = check_current_month(context)
        if observed_month is not None and observed_month != previous_month:
            return observed_month
        time.sleep(0.5)
    logger.error(
        f"月份进度未确认: previous={previous_month}, observed={observed_month}"
    )
    return None


def preprocess_events(context: Context) -> bool:
    """前处理：检测并处理随机事件"""
    logger.info("检测随机事件...")

    max_iterations = 15
    no_event_count = 0
    for i in range(max_iterations):
        screenshot = context.tasker.controller.post_screencap().wait().get()
        event_type = detect_and_manage_event(context, screenshot)

        if event_type is None:
            no_event_count += 1
            if no_event_count >= 3:
                logger.info("连续3次无事件，检测完成")
                return True
        else:
            no_event_count = 0
            logger.info(f"事件处理完成: {event_type}")
            
        time.sleep(0.3)
    logger.error(f"随机事件处理超过 {max_iterations} 轮仍未收敛，停止当前任务")
    return False


def _ensure_at_target_city(context: Context, target_city: str) -> tuple:
    """
    检测当前城市是否为目标城市，如果不在则滑动地图寻找

    Args:
        context: MAA 上下文对象
        target_city: 目标城市名称

    Returns:
        tuple: (是否在目标城市, 是否进行了城市迁移 并点击了确认)
    """
    max_swipe_times = 10

    # 当前画面可能被佣兵加入/孩子出生等弹窗盖住，先处理再判断图层和城市；
    # 否则会把弹窗误当成“找不到目标城市”。
    for _ in range(3):
        if not fight_utils.handle_interrupt_scene(context):
            break
        time.sleep(0.4)

    # 战后结算或随机事件可能把视角留在群岛层。目标主城位于大陆层，
    # 此时继续左右滑动永远找不到目标城市；先识别底部“前往大陆”并
    # 有界切层，看到“前往群岛”或目标城市后才继续。
    layer_img = context.tasker.controller.post_screencap().wait().get()
    if context.run_recognition("ClickGoToContinent", layer_img).hit:
        logger.info("当前位于群岛层，先切换到大陆层")
        layer_result = context.run_task("ClickGoToContinent")
        if not fight_utils._task_succeeded(layer_result):
            logger.error("切换到大陆层的点击节点未成功")
            return False, False

        layer_deadline = time.monotonic() + 5.0
        layer_ready = False
        while time.monotonic() < layer_deadline:
            current_img = context.tasker.controller.post_screencap().wait().get()
            if (
                context.run_recognition("ClickGoToArchipelago", current_img).hit
                or context.run_recognition("EnterCity", current_img).hit
            ):
                layer_ready = True
                break
            time.sleep(0.2)
        if not layer_ready:
            logger.error("点击前往大陆后未确认进入大陆层")
            return False, False

    def target_city_visible() -> bool:
        current_img = context.tasker.controller.post_screencap().wait().get()
        return context.run_recognition("EnterCity", current_img).hit

    if target_city_visible():
        return True, False

    # 战后通常仍停在目标主城附近。先向左探测一格，再向右回到原位并
    # 再检测一次；只有近邻探测均未命中，才进入原有的连续右滑搜索。
    logger.debug(f"未直接识别到 {target_city}，先进行左右近邻探测")
    context.run_task("Map_MoveMainCityLeft")
    if target_city_visible():
        logger.info(f"近邻探测找到目标城市: {target_city}")
        return True, False

    context.run_task("Map_MoveMainCityRight")
    if target_city_visible():
        logger.info(f"回到原视野后找到目标城市: {target_city}")
        return True, False

    logger.info(f"不在目标城市，开始滑动寻找...")
    for swipe_count in range(max_swipe_times):
        logger.info(
            f"滑动寻找目标城市 {target_city} ({swipe_count + 1}/{max_swipe_times})"
        )
        context.run_task("Map_MoveMainCityRight")

        current_img = context.tasker.controller.post_screencap().wait().get()
        # 滑动后可能弹出佣兵加入/孩子出生页，先处理再识别城市。
        if fight_utils.handle_interrupt_scene(context, current_img):
            time.sleep(0.4)
            current_img = context.tasker.controller.post_screencap().wait().get()
        reco_detail = context.run_recognition("EnterCity", current_img)
        if reco_detail.hit and reco_detail.best_result:
            current_city = reco_detail.best_result.text
            logger.info(f"滑动后当前城市: {current_city}")
            if current_city == target_city:
                context.run_task("EnterCity")
                # 跨王国城市会触发「前往[王国]」传送弹窗。三种已知格式：
                #   - 赫雷斯特：庄园传送 (1000银) / 航海 (1月)
                #   - 佩里亚诺：乘船 (200银, 本月已用变灰) / 步行 (1月)
                #   - 瓦斯塔亚：乘船 (无此交通工具, 灰) / 步行 (1月)
                # 必须先验证弹窗真的消失，否则就是点了禁用按钮。
                travel_img = (
                    context.tasker.controller.post_screencap().wait().get()
                )
                if not context.run_recognition(
                    "TravelDialog", travel_img
                ).hit:
                    if context.run_recognition(
                        "EnterCity_Confirm",
                        context.tasker.controller.post_screencap().wait().get(),
                    ).hit:
                        context.run_task("EnterCity_Confirm")
                    logger.info("已到达目标城市")
                    return True, True

                logger.info(
                    f"检测到跨王国传送弹窗，尝试快传送进入 {target_city}"
                )
                # 先看「乘船」是否被标记为「无此交通工具」/「已使用」等。
                # 如果禁用文字出现在「乘船」行，就跳过快传送直接走步行。
                if context.run_recognition(
                    "TravelDialog_BoatDisabled", travel_img
                ).hit:
                    logger.info(
                        "检测到「乘船」禁用（无交通工具/已使用），跳过快传送"
                    )
                else:
                    context.run_task("TravelDialog_ChooseFast")
                    time.sleep(2.5)
                    post_fast_img = (
                        context.tasker.controller.post_screencap().wait().get()
                    )
                    if not context.run_recognition(
                        "TravelDialog", post_fast_img
                    ).hit:
                        logger.info("快传送成功，弹窗已关闭")
                        return True, True
                    logger.warning(
                        "快传送确认后弹窗仍存在（可能点中禁用按钮），回退到步行"
                    )
                # 回退到步行（最稳的兜底）
                context.run_task("TravelDialog_ChooseSlow")
                time.sleep(2.5)
                post_slow_img = (
                    context.tasker.controller.post_screencap().wait().get()
                )
                if context.run_recognition(
                    "TravelDialog", post_slow_img
                ).hit:
                    logger.error(
                        f"步行/航海仍未关闭弹窗，无法进入 {target_city}"
                    )
                    return False, False
                logger.info("步行/航海确认成功，弹窗已关闭")
                return True, True

    return False, False


def _confession_fail_mode(context: Context) -> str:
    """告白失败处理：giveup（放弃）/ keep（花金币挽留）。"""
    data = context.get_node_data("ConfessionFailSelect")
    mode = "giveup"
    if data:
        expected = data.get("recognition", {}).get("param", {}).get(
            "expected", ["giveup"]
        )
        if isinstance(expected, list) and expected:
            mode = expected[0]
        elif isinstance(expected, str) and expected:
            mode = expected
    return mode


def handle_confession_fail(context: Context) -> str:
    """处理告白失败弹窗：按用户选项花金币挽留，找不到按钮则回退到放弃。

    Returns:
        kept（已花金币挽留）/ gaveup（已放弃）
    """
    if _confession_fail_mode(context) == "keep":
        result = context.run_task("Event_ConfessionFailKeepGold")
        if not fight_utils._task_succeeded(result):
            # 常见界面：右下是「获取对象信息」，点了才出现用金币保留的选项
            logger.info("告白失败：先点「获取对象信息」，再找金币保留")
            if fight_utils._task_succeeded(context.run_task("Event_ConfessionFailInfo")):
                time.sleep(1.0)
                result = context.run_task("Event_ConfessionFailKeepGold")
        if fight_utils._task_succeeded(result):
            time.sleep(0.8)
            img = context.tasker.controller.post_screencap().wait().get()
            # 部分版本点完会再弹一次确认框；没有就不点，避免误触别的按钮
            if context.run_recognition("PopUpWindowConfirm", img).hit:
                context.run_task("PopUpWindowConfirm")
            logger.info("告白失败：已花金币挽留，对方被留下")
            time.sleep(1.0)
            img = context.tasker.controller.post_screencap().wait().get()
            # 挽留成功有时会直接进婚礼页，交给 WeddingProcessor（它自己会再校验界面）
            if context.run_recognition("Event_WeddingPage", img).hit:
                logger.info("挽留后进入婚礼页面，交给 WeddingProcessor")
                context.run_task("Auto_WeddingTask")
            return "kept"
        logger.warning("未找到金币挽留按钮，回退到放弃该角色")

    context.run_task("Event_ConfessionFailGiveUp")
    return "gaveup"


def handle_mercenary_join(context: Context, screenshot=None) -> bool:
    """佣兵加入事件：按「开启自动接受佣兵」选项接受 / 拒绝。"""
    from utils.mercenary import dismiss_join, handle_join

    img = screenshot
    if img is None:
        img = context.tasker.controller.post_screencap().wait().get()

    smart = bool((context.get_node_data("Flag_SmartMercenary") or {}).get("enabled"))
    if smart:
        handle_join(context, img)
        return True

    join_data = context.get_node_data("Event_MercenaryJoin") or {}
    accept = "Event_MercenaryJoinConfirm" in (join_data.get("next") or [])
    dismiss_join(context, accept)
    return True


def handle_mercenary_baby(context: Context) -> bool:
    """佣兵生娃事件：按「开启生娃自动起名」决定自动起名还是默认确认。"""
    AutoNameChild = (context.get_node_data("Flag_AutoNameChild") or {}).get("enabled")
    if not AutoNameChild:
        logger.info("佣兵生娃自动起名已关闭，保留游戏默认名字")
        context.run_task("Event_MercenaryBaby")
        return True

    from utils.baby_ocr import current_index

    logger.info("佣兵生娃自动起名已开启，执行起名与好苗子检测")
    done = False
    for _ in range(3):
        before = current_index(context)
        context.run_task("Auto_PannelCheck")
        remaining_img = context.tasker.controller.post_screencap().wait().get()
        event_remaining = context.run_recognition("Event_MercenaryBaby", remaining_img).hit
        if not event_remaining:
            done = True
            break
        after = current_index(context, remaining_img)
        if before and after and after != before:
            logger.info(f"检测到新的孩子出生页（{before} → {after}），继续自动起名")
            continue
        logger.warning("自动起名未完成或出生页仍存在，回退到默认确认流程")
        fallback_img = remaining_img
        if not context.run_recognition("Event_MercenaryBaby", fallback_img).hit:
            # ChildRec 失败后若详情页不再保留出生文案，有界返回一层。
            context.run_task("BackButton_500ms")
            fallback_img = context.tasker.controller.post_screencap().wait().get()
        if context.run_recognition("Event_MercenaryBaby", fallback_img).hit:
            fallback_result = context.run_task("Event_MercenaryBaby")
            if not fight_utils._task_succeeded(fallback_result):
                logger.error("佣兵生娃默认确认流程执行失败")
        done = True
        break
    if not done:
        logger.warning("连续多个孩子未能全部自动起名，回退到默认确认")
        context.run_task("Event_MercenaryBaby")
    return True


def pirate_manual_mode(context: Context) -> bool:
    """「海盗事件处理」选项：manual = 停下等玩家调整阵容，不自动开战。"""
    data = context.get_node_data("PirateSelect")
    if not data:
        return False
    expected = (data.get("recognition") or {}).get("param", {}).get("expected") or []
    mode = expected[0] if expected else "auto"
    return str(mode).strip().lower() == "manual"


def _mark_pirate_manual() -> None:
    """给网页版的组合脚本留标记：海盗事件需要手动处理。"""
    try:
        marker = os.path.join(os.getcwd(), "temp", "pirate_alert.json")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "action": "manual",
                    "note": "海盗袭击：已设为手动处理，请调整阵容后再打",
                },
                handle,
                ensure_ascii=False,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写入海盗事件标记失败：{exc}")


def detect_and_manage_event(context: Context, screenshot) -> str:
    """检测事件类型"""
    if context.run_recognition("Event_MercenaryJoin", screenshot).hit:
        logger.info("检测到佣兵加入事件")
        handle_mercenary_join(context, screenshot)
        return "mercenary_join"
    elif context.run_recognition("Event_MercenaryBaby", screenshot).hit:
        logger.info("检测到佣兵生娃事件")
        handle_mercenary_baby(context)
        return "mercenary_baby"
    elif context.run_recognition("事件_孩子夭折了", screenshot).hit:
        logger.info("检测到孩子夭折事件")
        context.run_task("事件_孩子夭折了")
        return "child_death"
    elif context.run_recognition("Event_HarvestFestivalPrePay", screenshot).hit:
        logger.info("检测到口粮预交面板")
        event = handle_ration_prepay(context, screenshot)
        return event or "ration_prepay"
    elif context.run_recognition("Event_HarvestFestivalAnnuaClosing", screenshot).hit:
        # 丰收节 / 年末的年度结算：默认点「确定」结清训练所支出与工资，
        # 页面上的「银币充足 / 银币不足」只作提示，不做额外判断。
        logger.info("检测到丰收节年度结算界面，默认点击「确定」")
        context.run_task("Event_HarvestFestivalAnnuaClosing")
        return "annual_closing"
    elif context.run_recognition("Event_HarvestFestival", screenshot).hit:
        logger.info("检测到丰收节事件")
        context.run_task("Event_HarvestFestivalDealWith")
        return "harvest_festival"
    elif context.run_recognition("Event_ConfessionSuccess", screenshot).hit:
        logger.info("检测到告白成功事件")
        context.run_task("Event_ConfessionSuccess")
        return "confession_success"
    elif context.run_recognition("Event_ConfessionFail", screenshot).hit:
        logger.info("检测到告白失败事件")
        mode = handle_confession_fail(context)
        logger.info(f"告白失败处理：{'金币挽留' if mode == 'kept' else '放弃'}")
        return "confession_fail"
    elif context.run_recognition("Event_WeddingPage", screenshot).hit:
        # 告白成功那条链走完、或结婚后直接进婚礼页时，页面可能停在婚礼上没人处理
        logger.info("检测到婚礼页面，交给 WeddingProcessor")
        context.run_task("Auto_WeddingTask")
        return "wedding"
    elif context.run_recognition("PopUpWindowTip", screenshot).hit:
        logger.info("检测到提示事件")
        context.run_task("PopUpWindowTip")
        return "PopUpWindowTip"
    elif context.run_recognition("Event_MercenarieRetire", screenshot).hit:
        logger.info("检测到佣兵退休事件")
        context.run_task("Event_MercenarieRetire")
        return "mercenary_retire"
    elif context.run_recognition("Event_PirateRaid", screenshot).hit:
        if pirate_manual_mode(context):
            logger.info("海盗事件：设为手动处理，暂停任务，等待玩家调整阵容")
            _mark_pirate_manual()
            context.tasker.post_stop()
            return "pirate_manual"
        logger.info("检测到海盗袭击庄园事件，跨图前往群岛作战")
        # PirateRaidProcessor 内部保证战斗结束回到大陆层；
        # 入口先收一次 Esc 防止横幅抖动导致重复触发。
        context.run_task("Event_PirateRaid_Dispatch")
        return "pirate_raid"
    elif (
        context.run_recognition("Event_CastleAttacked", screenshot).hit
        or context.run_recognition("Event_CastleListWindow", screenshot).hit
        or context.run_recognition("Event_CastleDefendWindow", screenshot).hit
    ):
        logger.info("检测到城堡被攻打事件")
        context.run_task("Event_CastleAttack_Handle")
        return "castle_attack"
    else:
        return None


def check_current_month(context: Context) -> int:
    """检查当前月份"""
    month = fight_utils.Map_CheckCurrentMonth(context)
    return month if 1 <= month <= 12 else None


def _castle_marry_month(context: Context) -> int:
    """城堡相亲月份（「城堡相亲月份」选项，默认 4 月）。"""
    data = context.get_node_data("MarryMonthSelect")
    month = 4
    if data:
        expected = data.get("recognition", {}).get("param", {}).get("expected", ["4"])
        raw = expected[0] if isinstance(expected, list) and expected else expected
        try:
            value = int(str(raw).strip())
            if 1 <= value <= 12:
                month = value
        except (TypeError, ValueError):
            pass
    return month


def handle_festival_by_month(context: Context, month: int) -> bool:
    """根据月份处理节日"""
    festival_info = {
        2: "祈灵日，跳过",
        3: "启航节",
        4: "城堡相亲（城堡主厅）",
        5: "春林节相亲",
        6: "铸魂节，跳过",
        8: "丰收节",
        10: "勇士节",
        11: "亡人节，跳过",
        12: "创元节，跳过",
    }
    castle_month = _castle_marry_month(context)
    festival_name = festival_info.get(month, "无节日")
    if month == castle_month:
        festival_name = f"城堡相亲（城堡主厅，配置为 {castle_month} 月）"
    logger.info(f"当前月份：{month}月 - 本月：{festival_name}")

    if month == castle_month:
        if castle_month != 4:
            logger.info(f"按「城堡相亲月份」配置，{castle_month} 月执行城堡相亲")
        return handle_marry_festival(context, month=castle_month)
    elif month == 3:
        return handle_sailing_festival(context)
    elif month == 5:
        return handle_marry_festival(context, month=5)
    elif month == 8:
        return handle_harvest_festival(context)
    elif month == 10:
        return handle_warrior_festival(context)
    return True


def handle_sailing_festival(context: Context) -> bool:
    """处理启航节（3月）"""
    # 检查是否开启了启航节自动购买
    EnableSailingFestivalPurchase = context.get_node_data(
        "Flag_EnableSailingFestivalPurchase"
    ).get("enabled")
    if not EnableSailingFestivalPurchase:
        logger.info("启航节自动购买已关闭，跳过")
        return True

    current_month = check_current_month(context)
    if current_month != 3:
        logger.warning(f"当前月份不是3月，而是{current_month}月，跳过启航节")
        return True

    if not context.run_recognition(
        "Event_Launch", context.tasker.controller.post_screencap().wait().get()
    ).hit:
        logger.info("启航节已过")
        return True

    context.run_task("Event_Launch")
    if context.run_recognition(
        "Event_LaunchEnter", context.tasker.controller.post_screencap().wait().get()
    ).hit:
        context.run_task("Event_LaunchEnter")
    elif context.run_recognition(
        "Event_LaunchLongDistance",
        context.tasker.controller.post_screencap().wait().get(),
    ).hit:
        logger.info("启航节城市距离过远")
        return False

    if context.run_recognition(
        "Event_LaunchPage", context.tasker.controller.post_screencap().wait().get()
    ).hit:
        context.run_task("Event_LaunchPage")
    else:
        logger.error("无法进入启航节页面")
        return False

    recoDetail = context.run_recognition(
        "Event_LaunchGoods", context.tasker.controller.post_screencap().wait().get()
    )

    if recoDetail.hit:
        logger.info(f"检测到{len(recoDetail.filtered_results)}件商品")
        for good in recoDetail.filtered_results:
            box = good.box
            rect_x, rect_y = box[0] + box[2] // 2, box[1] + box[3] // 2
            logger.info(f"点击商品：{good.text}")
            context.tasker.controller.post_click(rect_x, rect_y).wait()
            time.sleep(0.5)
            context.run_task("Event_LaunchGoodsBuy")

            if context.run_recognition(
                "Event_LaunchGoodsBuyMax",
                context.tasker.controller.post_screencap().wait().get(),
            ).hit:
                context.run_task("Event_LaunchGoodsBuyMax")

            context.run_task("Event_LaunchGoodsBuyConfirm")
    else:
        logger.info("没有商品")

    context.run_task("UI_ReturnBigMap")
    return True


def handle_marry_festival(context: Context, month: int = 4) -> bool:
    """城堡主厅相亲（月份可配） / 5月春林节相亲。"""
    if month == 5:
        label = "5月春林节相亲"
        enable_node = "Flag_EnableMaySpringMarry"
        manual_node = "Flag_MaySpringManual"
    else:
        label = f"{month}月城堡相亲（城堡主厅）"
        enable_node = "Flag_EnableAprCastleMarry"
        manual_node = "Flag_AprCastleManual"

    def node_enabled(name: str) -> bool:
        data = context.get_node_data(name)
        return bool(data and data.get("enabled"))

    if node_enabled(manual_node):
        content = f"{label} 已到，请手动处理。处理完成后继续任务。"
        logger.info(f"{label} 设为手动，暂停任务")
        try:
            from utils.notifier import notify

            notify(label, content)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{label} 推送失败：{exc}")
        try:
            context.run_task(
                "UI_PopInform",
                pipeline_override={"Node.Action.Succeeded": {"content": content}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{label} 弹窗失败：{exc}")
        context.tasker.post_stop()
        return True

    if not node_enabled(enable_node):
        logger.info(f"{label} 已跳过")
        return True

    logger.info(f"处理 {label}")
    context.run_task("Auto_MarryTask")

    # 返回大地图
    if not fight_utils.ensure_at_bigmap(context):
        logger.error("无法回到大地图界面")
        return False

    return True


def _parse_ration_amount(text: str) -> Optional[tuple[int, int]]:
    """把「拥有 / 消耗」解析成 (拥有, 消耗)。

    实际面板：``粮食 69449 / 5232``，左边是当前粮食、右边是每年消耗；
    所以左边 >= 右边时显示「粮食充足」。
    """
    match = re.search(r"([0-9][0-9,，.kKmM万]*)\s*/\s*([0-9][0-9,，.kKmM万]*)", text or "")
    if not match:
        return None
    from action.fight.castle_resource_processor import parse_number

    have = parse_number(match.group(1))
    need = parse_number(match.group(2))
    if have is None or need is None:
        return None
    return have, need


def _ration_condition(context: Context, img: Any) -> tuple[str, dict]:
    """判断口粮预交面板里的粮食够不够。

    返回 (state, detail)，state = enough / short / unknown。
    优先认面板上的「粮食充足」；读不到再用「消耗 / 拥有」数字兜底，
    拥有 >= 消耗才算充足。两者都没有就返回 unknown，由调用方按不足处理。
    """
    if context.run_recognition("Event_RationPrepayEnough", img).hit:
        return "enough", {"reason": "面板显示「粮食充足」"}
    if context.run_recognition("Event_RationPrepayShort", img).hit:
        return "short", {"reason": "面板显示「粮食不足」"}

    detail = context.run_recognition("Event_RationPrepayAmount", img)
    if detail and detail.hit:
        texts = []
        if detail.best_result is not None and detail.best_result.text:
            texts.append(detail.best_result.text)
        for item in detail.filtered_results or []:
            text = getattr(item, "text", "")
            if text and text not in texts:
                texts.append(text)
        for text in texts:
            pair = _parse_ration_amount(text)
            if pair is None:
                continue
            have, need = pair
            return (
                "enough" if have >= need else "short",
                {
                    "reason": f"数值判断：拥有 {have} / 消耗 {need}",
                    "need": need,
                    "have": have,
                    "text": text.strip(),
                },
            )
    return "unknown", {"reason": "没有识别到「粮食充足」，也没有读到可用的数字"}


def _pause_for_ration(context: Context, state: str, detail: dict) -> None:
    """口粮不够/判断不了：不点确定，留标记、推送并暂停等补粮。"""
    payload = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": state,
        "action": "pause",
        "reason": detail.get("reason", ""),
        "note": "口粮预交未确认粮食充足，已暂停；请先补粮/调配后再继续",
    }
    for key in ("need", "have", "text"):
        if key in detail:
            payload[key] = detail[key]

    marker = os.path.join(os.getcwd(), "temp", RATION_SHORT_MARKER)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:  # noqa: BLE001
        logger.warning(f"口粮预交：写暂停标记失败：{exc}")

    content = (
        "口粮预交：没有确认粮食充足，已暂停任务。"
        "请先在城堡调配或购买粮食，再重新运行。"
    )
    try:
        from utils.notifier import notify

        notify("口粮预交", content)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"口粮预交：推送失败：{exc}")
    try:
        context.run_task(
            "UI_PopInform",
            pipeline_override={"Node.Action.Succeeded": {"content": content}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"口粮预交：弹窗失败：{exc}")
    context.tasker.post_stop()


def handle_ration_prepay(context: Context, img: Any = None) -> str:
    """口粮预交：只有检测到粮食充足才点「确定」，否则暂停等补粮。

    返回事件名；当前画面不是口粮预交时返回空串。
    """
    if img is None:
        img = fight_utils._screencap(context)
    if img is None:
        return ""
    if not context.run_recognition("Event_HarvestFestivalPrePay", img).hit:
        return ""

    state, detail = _ration_condition(context, img)
    logger.info(f"口粮预交：粮食判定 = {state}（{detail.get('reason', '')}）")

    if state == "enough":
        result = context.run_task("Event_RationPrepayConfirm")
        if not fight_utils._task_succeeded(result):
            logger.error("口粮预交：粮食充足，但点「确定」失败")
            return "ration_prepay_failed"
        logger.info("口粮预交：粮食充足，已点「确定」提交")
        return "ration_prepay"

    if state == "unknown":
        logger.warning("口粮预交：没能确认粮食是否充足，按不足处理，不点「确定」")
    else:
        logger.warning(f"口粮预交：粮食不足（{detail.get('reason', '')}），不点「确定」")
    _pause_for_ration(context, state, detail)
    return "ration_short"


def handle_harvest_festival(context: Context) -> bool:
    """处理丰收节（8月）"""
    logger.info("处理丰收节")
    return True


def handle_warrior_festival(context: Context) -> bool:
    """处理勇士节（10月）"""
    logger.info("处理勇士节")
    return True


_CASTLE_FEATURE_STATE = "castle_features_state.json"


def _castle_feature_state_path() -> str:
    return os.path.join(os.getcwd(), "temp", _CASTLE_FEATURE_STATE)


def _load_castle_feature_state() -> dict:
    try:
        with open(_castle_feature_state_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_castle_feature_state(state: dict) -> None:
    path = _castle_feature_state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001
        logger.warning(f"城堡附加功能：写月份状态失败：{exc}")


def _run_castle_features(context: Context, month: int) -> bool:
    """按月份执行城堡资源记录 / 市场购买面包。

    这两个功能是“附加动作”：识别失败只记日志，不拖垮整个月度流程；
    只有「资源低于阈值」会通过 post_stop 主动暂停年度任务。

    同一轮年度任务里，同一年月只执行一次（月份失败重试不会重复买面包）。
    """
    try:
        from action.fight.castle_resource_processor import (
            configured_record_month,
            maybe_record_castle_resources,
        )
        from action.fight.bread_purchase_processor import (
            configured_buy_month,
            maybe_buy_bread,
        )

        year, _state_month = game_state.year_month()
        stamp = f"{year}-{month}"
        state = _load_castle_feature_state()

        if configured_record_month(context) == month:
            if state.get("resource") == stamp:
                logger.info(f"城堡资源记录：{stamp} 本轮已执行过，跳过重复记录")
            else:
                maybe_record_castle_resources(context, month)
                state["resource"] = stamp
                _save_castle_feature_state(state)
                if context.tasker.stopping:
                    logger.info("城堡资源低于阈值：月度流程按要求暂停")
                    return False

        if configured_buy_month(context) == month:
            if state.get("bread") == stamp:
                logger.info(f"购买面包：{stamp} 本轮已执行过，跳过重复购买")
            else:
                maybe_buy_bread(context, month)
                state["bread"] = stamp
                _save_castle_feature_state(state)
                if context.tasker.stopping:
                    logger.info("购买面包过程中任务被停止")
                    return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"城堡附加功能执行失败（不影响主流程）：{exc}")
    return True


def process_single_month(context: Context) -> bool:
    """处理单个月份的完整流程"""

    if not preprocess_events(context):
        logger.error("月度任务前置事件未能在有界轮次内收敛")
        return False

    # 城堡资源记录 / 购买面包：按配置月份在每月流程最前面执行。
    month = check_current_month(context)
    if month is None:
        return False
    if not _run_castle_features(context, month):
        return False
    if context.tasker.stopping:
        return False

    EnableGrothTrial = context.get_node_data("Flag_GrowthTrialMode").get("enabled")
    if EnableGrothTrial:
        month = check_current_month(context)
        if month is None:
            logger.error("成长试炼模式无法识别当前月份")
            return False
        if not handle_festival_by_month(context, month):
            logger.error(f"成长试炼模式下 {month} 月节日处理失败")
            return False
        if not fight_utils.ensure_at_bigmap(context):
            logger.error("节日结束后无法回到大地图界面")
            return False
        logger.info("成长试炼模式已开启：执行 GrowthTrial_Start")
        growth_result = context.run_task("GrowthTrial_Start")
        if not fight_utils._task_succeeded(growth_result):
            logger.error("成长试炼入口执行失败")
            return False
        if not fight_utils._process_fighting(context):
            return False
        return fight_utils._process_post(context)
    else :
        target_city_data = context.get_node_data("EnterCity")
        target_city = (
            target_city_data.get("recognition", {})
            .get("param", {})
            .get("expected", ["王座堡"])[0]
            if target_city_data
            else "王座堡"
        )
        logger.info(f"目标城市: {target_city}")
        reached, traveled = _ensure_at_target_city(context, target_city)
        if not reached:
            logger.error(f"无法到达目标城市: {target_city}")
            return False

        if traveled:
            if not preprocess_events(context):
                return False
            if not fight_utils._task_succeeded(context.run_task("BackButton_500ms")):
                return False

        month = check_current_month(context)
        if month is None:
            return False

        if not handle_festival_by_month(context, month):
            return False

        # 月度节日不会改变当前城市，节日处理完直接接任务，
        # 不再重复确认/切换目标城市。

        # 采购/配送等非战斗任务在扫描任务板时已按描述关键词分类排除，
        # 不会再被接取；已接取的任务也不再额外打开任务板“校验类型”。
        # 万一接到的任务进不了战斗，_process_pre 会按「放弃」按钮兜底。

        # 选择进入的关卡
        return fight_utils.start_task(context)


@AgentServer.custom_action("TaskProcessor")
class TaskProcessor(CustomAction):
    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:

        # 月度入口可能正停在上一场战斗触发的随机事件页。
        # 先有界处理事件，再执行大地图恢复，避免返回节点空点。
        if not _recover_yearly_to_bigmap(context):
            logger.error("年度任务无法安全恢复到大地图界面")
            return CustomAction.RunResult(success=False)

        logger.info("团长大人, 您回来了！")

        success = process_single_month(context)
        return CustomAction.RunResult(success=success)


@AgentServer.custom_action("YearlyTaskProcessor")
class YearlyTaskProcessor(CustomAction):
    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("开始年度任务处理")

        # 新一轮年度任务：清掉上一轮的城堡功能月份标记，
        # 让配置月份在本轮重新执行（轮内重试仍然只执行一次）。
        try:
            state_path = _castle_feature_state_path()
            if os.path.exists(state_path):
                os.remove(state_path)
        except OSError as exc:  # noqa: BLE001
            logger.warning(f"清理城堡功能月份状态失败：{exc}")

        # 读取用户自定义的任务黑名单
        blacklist_data: dict = context.get_node_data("CustomTaskBlacklist")
        if blacklist_data:
            custom_blacklist = (
                blacklist_data.get("recognition", {})
                .get("param", {})
                .get("expected", [""])[0]
            )
            if custom_blacklist:
                from action.zshg.task_hud_recognizer import TaskBlacklist

                TaskBlacklist().add_to_blacklist(custom_blacklist)
                logger.info(f"已加载自定义任务黑名单: {custom_blacklist}")

        if not _recover_yearly_to_bigmap(context):
            logger.error("无法回到大地图界面")
            return CustomAction.RunResult(success=False)

        months_data = context.get_node_data("YearlyTaskMonths")
        # logger.info(f"YearlyTaskMonths node_data: {months_data}")
        total_months = (
            int(
                months_data.get("recognition", {})
                .get("param", {})
                .get("expected", ["12"])[0]
            )
            if months_data
            else 12
        )
        logger.info(f"年度任务执行月份数: {total_months}")

        logger.info("团长大人, 您回来了！")

        if total_months <= 0:
            logger.error(f"无效的年度任务月份数: {total_months}")
            return CustomAction.RunResult(success=False)

        month_retries = _yearly_month_retries(context)
        logger.info(
            f"任务失败继续轮数：{month_retries if month_retries > 0 else '不限'}"
            "（可在设置里调整）"
        )
        for month_offset in range(total_months):
            if context.tasker.stopping:
                logger.info(f"已停止处理第 {month_offset + 1}/{total_months} 个月")
                return CustomAction.RunResult(success=False)
            logger.info(f"开始处理第 {month_offset + 1}/{total_months} 个月")
            completed = False
            attempt = 0
            while month_retries <= 0 or attempt < month_retries:
                attempt += 1
                if context.tasker.stopping:
                    logger.info("任务已停止，年度任务结束")
                    return CustomAction.RunResult(success=True)
                if not _recover_yearly_to_bigmap(context):
                    logger.error(
                        f"第 {month_offset + 1}/{total_months} 个月恢复失败，停止年度任务"
                    )
                    return CustomAction.RunResult(success=False)
                previous_month = check_current_month(context)
                if previous_month is None:
                    logger.error("无法识别月度任务开始月份，停止而不盲目重试")
                    return CustomAction.RunResult(success=False)
                if process_single_month(context):
                    next_month = _wait_for_month_advance(context, previous_month)
                    if next_month is not None:
                        logger.info(
                            f"第 {month_offset + 1}/{total_months} 个月完成: "
                            f"{previous_month} -> {next_month}"
                        )
                        completed = True
                        break
                if context.tasker.stopping:
                    logger.info(
                        "任务已按要求暂停（城堡资源低于阈值或手动停止），"
                        "年度任务正常结束"
                    )
                    return CustomAction.RunResult(success=True)
                logger.warning(
                    f"第 {month_offset + 1}/{total_months} 个月未确认进度 "
                    f"({attempt}/{month_retries if month_retries > 0 else '不限'})"
                )
                time.sleep(0.5)
            if not completed:
                logger.error(
                    f"第 {month_offset + 1}/{total_months} 个月达到重试上限，停止年度任务"
                )
                return CustomAction.RunResult(success=False)

        logger.info("年度任务处理完成")
        return CustomAction.RunResult(success=True)


# ==================== 事件任务的单独入口 ====================
# 这些 Custom Action 和事件扫描跑的是同一批处理函数，只是给任务列表一个入口，
# 方便停在对应事件页面单独跑、单独调选项。


@AgentServer.custom_action("MercenaryJoinTask")
class MercenaryJoinTask(CustomAction):
    """任务入口：佣兵加入事件（当前画面是加入弹窗时运行）。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("任务入口：处理佣兵加入事件")
        if not context.run_recognition(
            "Event_MercenaryJoin",
            context.tasker.controller.post_screencap().wait().get(),
        ).hit:
            logger.error("当前画面不是佣兵加入弹窗，任务入口要求先停在弹窗上")
            return CustomAction.RunResult(success=False)
        return CustomAction.RunResult(success=handle_mercenary_join(context))


@AgentServer.custom_action("MercenaryBabyTask")
class MercenaryBabyTask(CustomAction):
    """任务入口：佣兵生娃事件（当前画面是孩子出生页时运行）。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("任务入口：处理佣兵生娃事件")
        if not context.run_recognition(
            "Event_MercenaryBaby",
            context.tasker.controller.post_screencap().wait().get(),
        ).hit:
            logger.error("当前画面不是孩子出生页，任务入口要求先停在出生页上")
            return CustomAction.RunResult(success=False)
        return CustomAction.RunResult(success=handle_mercenary_baby(context))


@AgentServer.custom_action("ConfessionFailTask")
class ConfessionFailTask(CustomAction):
    """任务入口：告白失败事件（当前画面是告白失败弹窗时运行）。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("任务入口：处理告白失败事件")
        if not context.run_recognition(
            "Event_ConfessionFail",
            context.tasker.controller.post_screencap().wait().get(),
        ).hit:
            logger.error("当前画面不是告白失败弹窗，任务入口要求先停在弹窗上")
            return CustomAction.RunResult(success=False)
        mode = handle_confession_fail(context)
        return CustomAction.RunResult(success=mode in ("kept", "gaveup"))


@AgentServer.custom_action("SceneGuardTask")
class SceneGuardTask(CustomAction):
    """组合步骤失败后再次确认场景：只在识别到短事件时处理，不盲点。

    没有短事件时再看一眼是不是「认不出来的画面」：按返回路线点几次，
    画面变了算恢复（组合可以重试这一步）；还是没变化就记录并返回失败，
    让组合按失败规则停下来，而不是反复重试同一个卡点。
    """

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        events: list[str] = []
        for _ in range(3):
            img = context.tasker.controller.post_screencap().wait().get()
            name = fight_utils.handle_interrupt_scene(
                context, img, include_mercenary_join=False
            )
            if not name:
                break
            events.append(name)
            time.sleep(0.5)

        if not events:
            img = fight_utils._screencap(context)
            if (
                img is not None
                and not context.run_recognition("UI_MainWindows", img).hit
                # 战斗页（准备 / 回合 / 结算）不是「未知画面」，乱点可能
                # 变成撤退或误操作；交给战斗流程自己处理。
                and fight_utils._detect_battle_state(context, img) == "unknown"
            ):
                try:
                    handled = scene_return.handle_unknown(
                        context,
                        img,
                        source="SceneGuardTask",
                        reason="场景确认后仍停在无法识别的画面",
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"场景确认：未知画面处理失败 {exc}")
                    handled = {"status": "error"}
                status = handled.get("status")
                clicked = " → ".join(
                    str(item.get("step"))
                    + ("（变了）" if item.get("changed") else "（没变）" if item.get("changed") is False else "")
                    for item in handled.get("attempts") or []
                ) or "未点击"
                if status == "changed":
                    events.append("unknown_recovered")
                    logger.info(f"场景确认：未知画面 {clicked}，画面已变化")
                elif status in ("recorded", "stuck"):
                    events.append("unknown_recorded")
                    record = handled.get("record") or {}
                    logger.error(
                        f"场景确认：未知画面 {clicked}，画面没有变化，"
                        f"记录 {record.get('id', '')}（来源 "
                        f"{scene_return.primary_source(handled) or '未知'}）"
                    )
                else:
                    logger.info(f"场景确认：未知画面处理结果 {status}")

        marker = os.path.join(os.getcwd(), "temp", "scene_guard.json")
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "events": events,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    handle,
                    ensure_ascii=False,
                )
        except OSError as exc:
            logger.warning(f"场景确认结果写入失败：{exc}")

        if events:
            logger.info(f"场景确认：已处理打断事件 {events}")
        else:
            logger.info("场景确认：当前没有需要处理的短事件")
        # unknown_recorded 表示「记录下来但没恢复」，不能算场景已确认，
        # 否则组合会重试同一步并再次卡在同一个画面上。
        return CustomAction.RunResult(
            success=any(event != "unknown_recorded" for event in events)
        )


@AgentServer.custom_action("RationPrepayTask")
class RationPrepayTask(CustomAction):
    """口粮预交面板：粮食充足才点确定，不足/读不到就暂停等补粮。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("场景处理：口粮预交")
        event = handle_ration_prepay(context)
        return CustomAction.RunResult(success=bool(event))


@AgentServer.custom_action("SailingFestivalTask")
class SailingFestivalTask(CustomAction):
    """任务入口：3 月启航节购买（开启「开启3月启航节购买」才执行）。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("任务入口：处理启航节购买")
        return CustomAction.RunResult(success=handle_sailing_festival(context))
