from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
import re
import time
from typing import Any, Optional, Set

from utils import logger
from utils import task_records, task_rules
from utils.game_state import update as update_game_state
from action.zshg.battle_world_map import BattleSessionRegistry, session_key
from action.zshg.task_hud_recognizer import TaskHudRecognizer


def Map_CheckCurrentMonth(context: Context) -> int:
    """
    将月份字符串转换为整数表示

    Args:
        month (str): 月份字符串，例如 "1月"、"2月" 等

    Returns:
        int: 月份的整数表示，范围为 1 到 12
    """

    screenshot = context.tasker.controller.post_screencap().wait().get()
    candidates = []
    for i in range(1, 13):
        reco_detail = context.run_recognition(
            "Map_GetMonth",
            screenshot,
            pipeline_override={
                "Map_GetMonth": {
                    "template": f"UI/month/{i}.png",
                    # 当前月份会沿钟盘旋转，因此必须覆盖整圈；下面会比较
                    # 全部命中的得分，避免按月份顺序误取静态刻度“6”。
                    "roi": [58, 2, 610, 221],
                }
            },
        )
        if not reco_detail.hit or reco_detail.best_result is None:
            continue
        score = float(getattr(reco_detail.best_result, "score", 0.0))
        candidates.append((score, i))
    if candidates:
        score, month = max(candidates)
        logger.info(f"当前游戏月份为：{month}月 (模板得分={score:.3f})")
        year = _read_game_year(context, screenshot)
        state = update_game_state(year=year, month=month)
        logger.info(f"当前游戏时间：{state['year']}年{state['month']}月")
        return month
    logger.error("未识别到当前游戏月份")
    return -1


def _read_game_year(context: Context, screenshot) -> int:
    """从大地图顶部 OCR 年份（C.E.1790），失败返回 0。"""
    try:
        detail = context.run_recognition("HUD_Year", screenshot)
        if not detail.hit or detail.best_result is None:
            return 0
        match = re.search(r"(\d{4})", detail.best_result.text or "")
        return int(match.group(1)) if match else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"识别游戏年份失败：{exc}")
        return 0


def handle_interrupt_scene(
    context: Context, img: Any = None, include_mercenary_join: bool = True
) -> str:
    """再次确认当前场景：只处理会挡住主流程的短事件/弹窗。

    典型场景：战斗结束后弹出「孩子出生/命名」页，或佣兵加入弹窗盖住
    战利品「确定」按钮。这里处理完就返回事件名，调用方应重新截图确认
    期望的按钮/场景，而不是直接判定任务失败。

    不处理海盗/城堡等长流程事件，避免在事件处理器内部递归触发自己。
    ``include_mercenary_join=False`` 时只处理命名/夭折/提示，适合没有
    当前任务选项上下文的组合级 SceneGuard，避免误接受佣兵。
    """
    if img is None:
        img = _screencap(context)
    if img is None:
        return ""
    try:
        # 局部导入：fight_processor 顶层 import 了 fight_utils，避免循环。
        from action.fight.fight_processor import (
            handle_mercenary_baby,
            handle_mercenary_join,
        )

        if context.run_recognition("Event_MercenaryBaby", img).hit:
            logger.info("场景确认：孩子出生/命名页，先自动处理再继续")
            handle_mercenary_baby(context)
            return "mercenary_baby"
        if include_mercenary_join and context.run_recognition(
            "Event_MercenaryJoin", img
        ).hit:
            logger.info("场景确认：佣兵加入弹窗，先处理再继续")
            handle_mercenary_join(context, img)
            return "mercenary_join"
        if context.run_recognition("事件_孩子夭折了", img).hit:
            logger.info("场景确认：孩子夭折弹窗，先处理再继续")
            context.run_task("事件_孩子夭折了")
            return "child_death"
        # 旅行确认弹窗不是随机事件，交由 TravelDialog 的专用流程处理，
        # 不能当普通「提示」点掉，否则会丢失目的地/交通选择。
        if context.run_recognition("TravelDialog", img).hit:
            return ""
        if context.run_recognition("PopUpWindowTip", img).hit:
            logger.info("场景确认：提示弹窗，先关闭再继续")
            context.run_task("PopUpWindowTip")
            return "popup_tip"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"场景确认处理中断事件失败：{exc}")
    return ""


def ensure_at_bigmap(
    context: Context, auto_return: bool = True, max_attempts: int = 8
) -> bool:
    """
    检测当前是否在大地图界面，不在的话尝试返回大地图

    Args:
        context: MAA 上下文对象
        auto_return: 是否自动尝试返回大地图，默认True

    Returns:
        bool: 成功在大地图返回True，否则返回False
    """
    attempts = max(1, max_attempts if auto_return else 1)
    for _ in range(attempts):
        img = _screencap(context)
        if img is None:
            return False
        if context.run_recognition("UI_MainWindows", img).hit:
            return True
        if not auto_return:
            return False

        # 先处理孩子出生/命名、佣兵加入等会挡按钮的短事件；
        # 处理完重新截图确认，避免把“场景不对”当成任务失败。
        if handle_interrupt_scene(context, img):
            time.sleep(0.4)
            continue

        # UI_ReturnBigMap 内含 JumpBack 循环，遇到未知弹窗时会无界等待。
        # 这里只执行可证明、有上限的单步恢复。
        if context.run_recognition("BackButton_500ms", img).hit:
            context.run_task("BackButton_500ms")
        else:
            context.run_task("ClickCenter_500ms")
        time.sleep(0.4)

    return False


def ensure_task_accepted(context: Context) -> bool:
    """
    检测任务列表中是否已接取任务（通过快速定位图标判断）

    该函数会先检查任务列表是否已打开，若未打开则先点击打开。
    然后检查任务列表中是否存在快速定位图标，若存在则表示已接取任务。

    Args:
        context: MAA 上下文对象

    Returns:
        bool: True 表示已接取任务，False 表示未接取
    """
    img = _screencap(context)
    if img is None:
        return False
    if not context.run_recognition("InTaskPannel", img).hit:
        context.run_task("UI_TaskPannelPageOpen")
        time.sleep(0.8)
        img = _screencap(context)
        if img is None:
            return False

    if context.run_recognition("TaskQuickLocation", img).hit:
        return True

    return False


def open_city_task_panel(context: Context, max_steps: int = 12) -> bool:
    """有界进入主城任务板，并处理自由日/旅行等中间页。

    旧 ``OpenCityTaskPanel`` Pipeline 会在自由日城市选择框中反复命中
    ``EnterCity``，但它的点击偏移只适用于大地图城堡图标，最终形成
    无上限的 ``EnterCity -> OpenCityTaskPanel`` 循环。这里逐步观察页面，
    每个动作后重新识别；同一状态连续三次没有变化就停止交给月度恢复。
    """
    last_state = ""
    repeated_state = 0

    def accept_state(state: str) -> bool:
        nonlocal last_state, repeated_state
        if state == last_state:
            repeated_state += 1
        else:
            last_state = state
            repeated_state = 1
        if repeated_state >= 3:
            logger.error(f"进入任务板状态连续无变化: {state}")
            return False
        return True

    for step in range(max_steps):
        img = _screencap(context)
        if img is None:
            return False
        if context.run_recognition("InTaskPannel", img).hit:
            logger.info(f"主城任务板已就绪 ({step + 1}/{max_steps})")
            return True

        if context.run_recognition("TravelDialog", img).hit:
            state = "travel_dialog"
            if not accept_state(state):
                return False
            logger.info("进入任务板途中出现旅行弹窗，选择有界步行兜底")
            if not _task_succeeded(context.run_task("TravelDialog_ChooseSlow")):
                return False
            time.sleep(0.5)
            continue

        go_button = context.run_recognition("FreeDayGoButton", img)
        if go_button.hit:
            state = "free_day"
            if not accept_state(state):
                return False
            city = context.run_recognition("EnterCity", img)
            if not city.hit or city.best_result is None:
                logger.error("自由日城市列表中没有识别到目标城市")
                return False
            _, city_y, _, city_height = (
                int(value) for value in city.best_result.box
            )
            city_center_y = city_y + city_height // 2
            candidates = (
                go_button.filtered_results
                if go_button.filtered_results
                else [go_button.best_result]
            )
            candidates = [item for item in candidates if item is not None]
            if not candidates:
                return False
            button = min(
                candidates,
                key=lambda item: abs(
                    int(item.box[1]) + int(item.box[3]) // 2 - city_center_y
                ),
            )
            button_x, button_y, button_width, button_height = (
                int(value) for value in button.box
            )
            button_center_y = button_y + button_height // 2
            if abs(button_center_y - city_center_y) > 70:
                logger.error("自由日目标城市同一行没有可信的前去按钮")
                return False
            button_x += button_width // 2
            button_y = button_center_y
            clicked = (
                context.tasker.controller.post_click(button_x, button_y)
                .wait()
                .succeeded
            )
            logger.info(
                "自由日按同行按钮进入目标城市: "
                f"({button_x}, {button_y}), succeeded={clicked}"
            )
            if not clicked:
                return False
            time.sleep(1.0)
            continue

        if context.run_recognition("EnterCity_Confirm", img).hit:
            state = "enter_confirm"
            if not accept_state(state):
                return False
            if not _task_succeeded(context.run_task("EnterCity_Confirm")):
                return False
            continue

        if context.run_recognition("FindCityTask_OCR", img).hit:
            state = "task_entry"
            if not accept_state(state):
                return False
            if not _task_succeeded(context.run_task("FindCityTask_OCR")):
                return False
            continue

        if context.run_recognition("SwitchInnerCity", img).hit:
            state = "switch_inner"
            if not accept_state(state):
                return False
            if not _task_succeeded(context.run_task("SwitchInnerCity")):
                return False
            continue

        if context.run_recognition("EnterCity", img).hit:
            state = "open_city_list"
            if not accept_state(state):
                return False
            if not _task_succeeded(context.run_task("EnterCity")):
                return False
            continue

        logger.error(
            f"进入任务板遇到未知画面 ({step + 1}/{max_steps})，停止盲点"
        )
        return False

    logger.error(f"进入任务板超过有界步数 {max_steps}")
    return False


def abandon_noncombat_accepted_task(context: Context, max_swipes: int = 5) -> bool:
    """在当前主城任务板中放弃已接取的采购/配送任务。

    仅点击同时满足以下条件的任务：描述命中明确的非战斗关键词，且按钮
    OCR 为“放弃”。可领取的同类任务按钮是“接受”，不会被误点。
    """
    img = _screencap(context)
    if img is None:
        return False
    if not context.run_recognition("TaskQuickLocation", img).hit:
        return True

    logger.info("检测到已接取任务，进入主城任务板校验任务类型")
    if context.run_recognition("UI_TaskPannelPageClose", img).hit:
        if not _task_succeeded(context.run_task("UI_TaskPannelPageClose")):
            return False

    if not open_city_task_panel(context):
        logger.error("无法打开当前主城任务板校验已接取任务")
        return False

    recognizer = TaskHudRecognizer()
    for swipe_index in range(max_swipes + 1):
        board_img = _screencap(context)
        if board_img is None:
            return False
        tasks = recognizer.recognize_tasks(context, board_img)
        for task in tasks:
            keyword = recognizer.non_combat_keyword(task)
            if keyword is None or "放弃" not in task.action_text:
                continue
            if task.accept_button_box is None:
                continue

            logger.warning(
                f"放弃已接取非战斗任务: {task.task_name} | "
                f"{task.task_type} | 关键词={keyword}"
            )
            box = task.accept_button_box
            x, y = box[0] + box[2] // 2, box[1] + box[3] // 2
            if not context.tasker.controller.post_click(x, y).wait().succeeded:
                return False
            time.sleep(0.5)

            confirm_img = _screencap(context)
            if confirm_img is None:
                return False
            if context.run_recognition("PopUpWindowConfirm", confirm_img).hit:
                if not _task_succeeded(context.run_task("PopUpWindowConfirm")):
                    return False
                time.sleep(0.5)
            else:
                # 当前版本点击“放弃”会直接把按钮改回“接受”，没有确认框。
                # 重新识别任务板证明原任务已不再处于“放弃”状态后才继续。
                remaining = any(
                    visible.task_name == task.task_name
                    and "放弃" in visible.action_text
                    for visible in recognizer.recognize_tasks(context, confirm_img)
                )
                if remaining:
                    logger.error("非战斗任务仍显示为已接取，放弃动作未生效")
                    return False
                logger.info("非战斗任务已直接放弃（当前版本无二次确认框）")
            tip_img = _screencap(context)
            if tip_img is not None and context.run_recognition(
                "PopUpWindowTip", tip_img
            ).hit:
                context.run_task("PopUpWindowTip")
            context.run_task("BackButton_500ms")
            return ensure_at_bigmap(context)

        if swipe_index < max_swipes:
            context.run_task("FindCityTask_SwipeDown")

    logger.info("当前已接取任务未命中采购/配送规则，保留并继续执行")
    context.run_task("BackButton_500ms")
    return ensure_at_bigmap(context)


def _current_city(context: Context) -> str:
    """当前刷取任务的目标城市（来自「选择刷取任务国家」选项）。"""
    data = context.get_node_data("EnterCity")
    if not data:
        return ""
    expected = data.get("recognition", {}).get("param", {}).get("expected", [])
    if isinstance(expected, list) and expected:
        return str(expected[0])
    if isinstance(expected, str):
        return expected
    return ""


def _read_accepted_task_name(context: Context) -> str:
    """已接取任务时，从任务板里读一下任务名（按钮为「放弃」的那条）。"""
    try:
        img = _screencap(context)
        if img is None:
            return ""
        for task in TaskHudRecognizer().recognize_tasks(context, img):
            if "放弃" in task.action_text and task.task_name:
                return task.task_name
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取已接取任务名失败：{exc}")
    return ""


def _read_map_task_name(context: Context) -> str:
    """大地图左下角：女王密令下面、剩余时间上面那行就是当前任务名。"""
    try:
        img = _screencap(context)
        if img is None:
            return ""
        detail = context.run_recognition("HudTaskName", img)
        if detail.hit and detail.best_result is not None:
            text = (detail.best_result.text or "").strip()
            # 排除 HUD 里相邻的固定文案/月度目标，避免把它们当成已接取任务名
            if text and text not in ("未制定目标", "剩余时间", "女王密令"):
                if not any(
                    key in text
                    for key in (
                        "密令",
                        "敕令",
                        "剩余",
                        "未制定",
                        "诸神",
                        "阶段",
                        "任务目标",
                    )
                ):
                    return text
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取大地图任务名失败：{exc}")
    return ""


_last_fight_result: Optional[dict] = None

# 主动战斗的可配置选项节点：模式 / 待机回合数 / 棋子识别。
# 月度循环通过界面选项覆盖这些节点，但战斗是嵌套 run_task 跑的，
# 嵌套任务不保证继承父任务的 pipeline_override，因此这里显式传下去。
BATTLE_OPTION_NODES = (
    "FightModeSelect",
    "FightStandbyRounds",
    "FightPieceRecognition",
)


def _node_expected_value(data: Any) -> Optional[str]:
    """从节点定义里取 expected 的首个值，兼容两种结构。"""
    if not isinstance(data, dict):
        return None
    expected: Any = None
    recognition = data.get("recognition")
    if isinstance(recognition, dict):
        param = recognition.get("param")
        if isinstance(param, dict):
            expected = param.get("expected")
    if expected is None:
        expected = data.get("expected")
    if isinstance(expected, (list, tuple)):
        expected = expected[0] if expected else None
    if expected is None:
        return None
    return str(expected)


def battle_option_override(context: Context) -> dict:
    """把当前任务上的战斗选项显式传给嵌套的 ``AutoFight_Start``。

    月度循环（Auto_YearlyTask）的选项 override 挂在父任务上下文里；
    嵌套跑战斗时如果不显式传，AutoFightProcessor 只能读到 pipeline 节点
    默认值（待机 20 回合）。这里先取当前上下文的有效值再传下去。
    """
    override: dict = {}
    values: dict = {}
    for node in BATTLE_OPTION_NODES:
        try:
            value = _node_expected_value(context.get_node_data(node))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"读取战斗选项 {node} 失败：{exc}")
            continue
        if value is None:
            continue
        override[node] = {"expected": [value]}
        values[node] = value
    if values:
        logger.info(
            "战斗选项：模式="
            f"{values.get('FightModeSelect', '默认')}，待机回合数="
            f"{values.get('FightStandbyRounds', '默认')}，棋子识别="
            f"{values.get('FightPieceRecognition', '默认')}"
        )
    return override


def finalize_task_record(context: Context) -> None:
    """战斗 + 战后流程结束后写任务记录；任务名缺就回大地图从左下角 HUD 补。"""
    global _last_fight_result
    if _last_fight_result is None:
        return
    info, _last_fight_result = _last_fight_result, None

    pending = task_records.get_pending() or {}
    name = (pending.get("task") or "").strip()
    if not name:
        name = _read_map_task_name(context)
        if name:
            logger.info(f"任务记录：从大地图补到任务名「{name}」")
    task_records.finish(
        rounds=info.get("rounds", 0),
        result=info.get("result", "胜利"),
        task_name=name,
    )


def _task_fail_mode(context: Context) -> str:
    """任务失败处理：switch 换任务 / retry 继续任务 / blacklist 拉黑并换任务。"""
    data = context.get_node_data("TaskFailSelect")
    mode = "switch"
    if data:
        expected = data.get("recognition", {}).get("param", {}).get(
            "expected", ["switch"]
        )
        if isinstance(expected, list) and expected:
            mode = str(expected[0])
        elif isinstance(expected, str) and expected:
            mode = expected
    return mode


def abandon_accepted_task(context: Context, max_swipes: int = 5) -> bool:
    """放弃当前已接取的任务（任务板里按钮为「放弃」的那条），然后回大地图。"""
    # 先看大地图左下角：有任务名就说明任务还在，不能因为任务板没打开
    # 就误判成“没有已接取任务”而提前返回。
    hud_name = _read_map_task_name(context)
    img = _screencap(context)
    if img is None:
        return False
    if not hud_name and not context.run_recognition("TaskQuickLocation", img).hit:
        # 大地图没任务名、任务板也没有快速定位 → 确实没有已接取任务
        return ensure_at_bigmap(context)

    if context.run_recognition("UI_TaskPannelPageClose", img).hit:
        if not _task_succeeded(context.run_task("UI_TaskPannelPageClose")):
            return False

    if not open_city_task_panel(context):
        logger.error("无法打开任务板放弃失败的任务")
        return False

    recognizer = TaskHudRecognizer()
    for swipe_index in range(max_swipes + 1):
        board_img = _screencap(context)
        if board_img is None:
            return False
        for task in recognizer.recognize_tasks(context, board_img):
            if "放弃" not in task.action_text or task.accept_button_box is None:
                continue
            box = task.accept_button_box
            x, y = box[0] + box[2] // 2, box[1] + box[3] // 2
            logger.warning(f"放弃失败的任务：{task.task_name}")
            if not context.tasker.controller.post_click(x, y).wait().succeeded:
                return False
            time.sleep(0.5)

            confirm_img = _screencap(context)
            if confirm_img is not None and context.run_recognition(
                "PopUpWindowConfirm", confirm_img
            ).hit:
                context.run_task("PopUpWindowConfirm")
                time.sleep(0.5)
            context.run_task("BackButton_500ms")
            return ensure_at_bigmap(context)

        if swipe_index < max_swipes:
            context.run_task("FindCityTask_SwipeDown")

    logger.warning("任务板里没找到「放弃」按钮，失败任务保持接取状态")
    context.run_task("BackButton_500ms")
    return ensure_at_bigmap(context)


def dismiss_battle_result(context: Context, max_steps: int = 6) -> bool:
    """点掉失败结算页和残留的确认框，尽量回到大地图。

    不点掉的话，月度恢复 `_recover_yearly_to_bigmap` 会判定“战斗失败结算页”
    并直接停止整个年度任务——失败任务也就没法“继续循环”。
    """
    for _ in range(max_steps):
        img = _screencap(context)
        if img is None:
            return False
        if context.run_recognition("UI_MainWindows", img).hit:
            return True
        if context.run_recognition("FightFail", img).hit:
            logger.info("点掉战斗失败结算页")
            context.run_task("FightFail")
            time.sleep(0.6)
            continue
        if context.run_recognition("FightResultConfirm", img).hit:
            context.run_task("FightResultConfirm")
            time.sleep(0.5)
            continue
        if context.run_recognition("PopUpWindowConfirm", img).hit:
            context.run_task("PopUpWindowConfirm")
            time.sleep(0.5)
            continue
        context.run_task("ClickCenter_500ms")
        time.sleep(0.5)

    if ensure_at_bigmap(context):
        return True
    logger.warning("失败结算页没能清干净，仍按继续循环处理")
    return False


TASK_FAILURE_RETRY_ATTEMPTS = 2


def _retry_failed_task(context: Context, task_name: str = "") -> bool:
    """失败后立即重试同一个已接取任务（真正再打一场）。

    ``retry`` 模式以前只是返回 False 交给月度重试，但月度重试会重新跑
    整月流程、失败两次就停掉年度任务，实际没有“再打一次”。这里直接复用
    ``_process_fight`` 重新进入同一任务，最多 ``TASK_FAILURE_RETRY_ATTEMPTS``
    次；全部失败由调用方回退到放弃并继续循环。
    """
    for attempt in range(1, TASK_FAILURE_RETRY_ATTEMPTS + 1):
        if context.tasker.stopping:
            logger.info("任务执行被停止")
            return False
        if not ensure_at_bigmap(context):
            logger.warning("继续任务：重试前无法回到大地图")
            return False

        logger.info(
            f"继续任务：第 {attempt}/{TASK_FAILURE_RETRY_ATTEMPTS} 次重试同一任务"
        )
        if task_name:
            task_records.set_pending(
                city=_current_city(context), task_name=task_name
            )

        if _process_fight(context):
            finalize_task_record(context)
            logger.info("继续任务：重试后战斗胜利，月度循环继续")
            return True

        finalize_task_record(context)
        dismiss_battle_result(context)

    return False


def _handle_task_failure(context: Context) -> bool:
    """刷取任务失败后的走向；返回 True 表示本次月度循环可以继续。"""
    mode = _task_fail_mode(context)
    pending = task_records.get_pending() or {}
    name = (pending.get("task") or "").strip() or _read_map_task_name(context)

    logger.warning(f"刷取任务失败（{name or '未知任务'}），处理方式：{mode}")

    dismiss_battle_result(context)

    if mode == "retry":
        logger.info("继续任务：不放弃当前任务，立即重试同一任务")
        if _retry_failed_task(context, name):
            return True
        # 重试仍未胜利：以大地图左下角为准。任务还没结束就保留它，
        # 下个月/下一轮继续同一个任务，不再放弃后重新接取。
        if not ensure_at_bigmap(context):
            logger.warning("继续任务：重试失败后无法回到大地图")
        still_active = _read_map_task_name(context)
        if still_active:
            logger.warning(
                f"继续任务：重试未胜利，但任务「{still_active}」还没结束，"
                "保留任务，下一轮重试同一任务，不再重新接取"
            )
        else:
            logger.info("继续任务：任务已结束，但本次没有胜利，下一轮重新接取")
        # 没打赢就不能算这个月完成；返回 False 让月度循环重试，
        # 否则上层会去等月份推进，等到超时后整轮失败。
        return False

    if mode == "blacklist":
        if name:
            task_records.add_blacklist(name)
            logger.info(f"已把失败任务「{name}」加入黑名单，以后不再接取")
        else:
            logger.warning("任务名未知，没法拉黑，按换任务处理")

    if abandon_accepted_task(context):
        logger.info("失败任务已放弃，已回到大地图，本轮未胜利，月度循环重试本月")
    else:
        logger.warning("放弃失败任务没有成功，本轮未胜利，月度循环重试本月")
    return False


def start_task(context: Context) -> bool:
    """
    开始执行任务流程

    Args:
        context: MAA 上下文对象

    Returns:
        bool: 任务执行成功返回 True，否则返回 False
    """

    if not _preprocess_accept_task(context):
        return False

    ok = _process_fight(context)
    finalize_task_record(context)
    if ok:
        return True
    return _handle_task_failure(context)


@AgentServer.custom_action("SingleFightTaskProcessor")
class SingleFightTaskProcessor(CustomAction):
    """完成恰好一个普通战斗任务，不处理节日、城市移动或月度事件。"""

    def run(
        self, context: Context, argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("====== 单次战斗任务开始 ======")

        # 战后可能停在群岛层；普通任务在配置的主城接取。
        # 仅复用定位逻辑，不执行月度事件或节日处理。
        from action.fight.fight_processor import _ensure_at_target_city

        target_data = context.get_node_data("EnterCity") or {}
        expected = target_data.get("recognition", {}).get("param", {}).get(
            "expected", []
        )
        target_city = expected[0] if expected else ""
        reached, _ = _ensure_at_target_city(context, target_city)
        if not reached:
            logger.error("无法回到任务目标城市，停止本次单战任务")
            return CustomAction.RunResult(success=False)

        success = start_task(context)
        if success:
            logger.info("====== 单次战斗任务完成，已回到大地图 ======")
        else:
            logger.error("单次战斗任务失败，停止后续任务")
        return CustomAction.RunResult(success=success)


def _preprocess_accept_task(context: Context) -> bool:
    """
    前处理阶段：检测并接取任务

    Args:
        context: MAA 上下文对象

    Returns:
        bool: 前处理成功返回 True
    """
    logger.info("====== 接取任务 ======")

    if not ensure_at_bigmap(context):
        return False

    # 大地图左下角那行并不总是“已接取任务”：很多时候显示的是月度目标
    # 「王族敕令（24/24）」/「女王密令」。直接用它会误判成已有任务，
    # 跳过任务板，随后又找不到「快速定位」。这里只把它当日志线索，
    # 是否已接取一律以任务板里的「快速定位/放弃」按钮为准。
    hud_name = _read_map_task_name(context)
    if hud_name:
        logger.info(f"大地图左下角读到「{hud_name}」，到任务板确认是否已接取")

    if ensure_task_accepted(context):
        # HUD 不可靠时，用任务板里真正的已接取任务名
        name = _read_accepted_task_name(context)
        task_records.set_pending(city=_current_city(context), task_name=name)
        return True

    # 左右滑动会快速锁定当前任务城市的主城
    # context.run_task("Map_MoveMainCityLeft")
    # context.run_task("Map_MoveMainCityRight")
    if not open_city_task_panel(context):
        return False
    return _accept_new_task(context)


def _accept_new_task(context: Context) -> bool:
    """
    接取新任务 - 默认使用HUD动态识别模式

    按当前城市读取 ``table/task_accept_rules.json`` 的等级区间与城市黑名单，
    每次扫描任务板都会把结果写入 ``table/task_pool.json``，供网页「任务接取」
    面板显示每条任务是否可接以及原因。

    Args:
        context: MAA 上下文对象

    Returns:
        bool: 接取成功返回 True
    """
    max_swipe_times = 5

    hud_recognizer = TaskHudRecognizer()
    city = _current_city(context)
    rule = task_rules.city_rule(city)
    hud_min_level = rule["min_level"]
    hud_max_level = rule["max_level"]
    blacklist = set(task_rules.effective_blacklist(city))
    logger.info(
        f"任务规则：城市={city or '未知'}，可接等级 "
        f"{hud_min_level}~{hud_max_level}，黑名单 {len(blacklist)} 个"
        f"（全局 + 城市）"
    )

    def scan_current_pool() -> bool:
        # 任务板会保留上一个月的滚动位置。如果上次扫到了列表底部，
        # 继续单向下滑只会在原地重复识别。每轮先有界地回到顶部，
        # 再按既有方向扫到底，保证 HUD 覆盖整个任务池。
        for _ in range(max_swipe_times):
            context.run_task("FindCityTask_SwipeUp")

        # 任务板分屏显示：把每次下滑看到的任务合并起来，网页上才能看到
        # 整块任务池，而不是最后一屏。
        seen_items: dict = {}
        for swipe_count in range(max_swipe_times + 1):
            screenshot = context.tasker.controller.post_screencap().wait().get()
            items, eligible = hud_recognizer.scan_pool(
                context,
                screenshot,
                min_level=hud_min_level,
                max_level=hud_max_level,
                city=city,
                blacklist=blacklist,
            )
            for item in items:
                key = (item.get("name"), item.get("level"), item.get("type"))
                seen_items[key] = item
            if seen_items:
                task_rules.save_pool(city, list(seen_items.values()))
            best_task = hud_recognizer.pick_best(eligible)

            if best_task and best_task.accept_button_box:
                accept_box = best_task.accept_button_box
                accept_x = accept_box[0] + accept_box[2] // 2
                accept_y = accept_box[1] + accept_box[3] // 2
                context.tasker.controller.post_click(accept_x, accept_y).wait()
                time.sleep(0.5)
                task_records.set_pending(
                    city=city,
                    task_name=best_task.task_name,
                    task_type=best_task.task_type,
                    task_level=best_task.task_level,
                )
                logger.info(
                    f"接取任务：{best_task.task_name}"
                    f"（{best_task.task_type} / {best_task.task_level} / "
                    f"{city or '未知城市'}）"
                )
                return True

            if seen_items:
                reason = _pool_reason_summary(list(seen_items.values()))
                if reason:
                    logger.info(f"任务板无候选：{reason}")

            if swipe_count < max_swipe_times:
                logger.info(
                    f"HUD未识别到有效任务，正在滑动刷新... "
                    f"({swipe_count + 1}/{max_swipe_times})"
                )
                context.run_task("FindCityTask_SwipeDown")
        return False

    if scan_current_pool():
        return True

    # 整个列表只有黑名单、保护或非战斗任务时，使用页面提供的
    # 10 水晶刷新一次，然后从顶部重新扫描。单次调用最多消耗一次，
    # 年度层仍保留自己的两次月度上限，不会无界刷新。
    logger.warning("HUD扫完整个任务池仍无候选，尝试水晶刷新一次")
    refresh_result = context.run_task("FindCityTask_Refresh")
    if not _task_succeeded(refresh_result):
        logger.error("任务池刷新节点执行失败")
        return False
    if scan_current_pool():
        return True

    logger.error("刷新任务池后仍未检测到可接取的任务")
    return False


def _pool_reason_summary(items: list) -> str:
    """把任务板里不可接的原因汇总成一行，便于日志和排查。"""
    counts: dict = {}
    for item in items:
        if item.get("eligible"):
            continue
        reason = str(item.get("reason") or "未知")
        key = reason.split("（")[0]
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    return "，".join(f"{key}×{count}" for key, count in counts.items())


def _process_fight(context: Context) -> bool:
    """
    战斗阶段：寻找任务点并完成战斗

    Args:
        context: MAA 上下文对象

    Returns:
        bool: 战斗成功返回 True，失败返回 False
    """
    logger.info("====== 战斗阶段 ======")

    if not _process_pre(context):
        logger.error("战斗前置流程失败，未进入战斗准备页")
        return False

    if not _process_fighting(context):
        logger.error("主动战斗未胜利，停止战后奖励流程")
        return False

    return _process_post(context)


def _process_pre(context: Context) -> bool:
    # 先确保任务板真的打开，再找「快速定位」。
    # 不能用底部按钮的 Open/Close 模板判断：地图状态偶尔会误命中 Close，
    # 结果面板没开却直接去找快速定位，必然失败。
    img = _screencap(context)
    if img is None:
        logger.error("战斗前置：截图失败")
        return False
    for attempt in range(2):
        if context.run_recognition("TaskQuickLocation", img).hit:
            break
        if not context.run_recognition("InTaskPannel", img).hit:
            context.run_task("UI_TaskPannelPageOpen")
            time.sleep(0.8)
        else:
            time.sleep(0.5)
        img = _screencap(context)
        if img is None:
            logger.error("战斗前置：打开任务板后截图失败")
            return False

    recoDetail = context.run_recognition("TaskQuickLocation", img)
    if not recoDetail or not recoDetail.hit:
        logger.error("任务面板中未识别到快速定位按钮")
        return False

    rect = recoDetail.best_result.box
    rect_x, rect_y = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
    click_job = context.tasker.controller.post_click(rect_x, rect_y).wait()
    if not click_job.succeeded:
        logger.error("点击任务快速定位按钮失败")
        return False
    time.sleep(0.5)

    if not _task_succeeded(context.run_task("TaskDetailOpen")):
        logger.error("打开任务详情失败")
        return False
    if not _task_succeeded(context.run_task("TaskDetailFight")):
        logger.error("点击进入战斗失败")
        detail_img = _screencap(context)
        if detail_img is not None and context.run_recognition(
            "TravelDialog", detail_img
        ).hit:
            logger.warning("任务定位打开了旅行弹窗，当前任务不是就地战斗任务")
            context.run_task("TravelDialog_Close")
            return False
        abandon = (
            context.run_recognition("TaskClaim", detail_img)
            if detail_img is not None
            else None
        )
        if abandon and abandon.hit:
            logger.warning("任务详情没有进入战斗，放弃当前非战斗/异常任务")
            if _task_succeeded(context.run_task("TaskClaim")):
                time.sleep(0.5)
                confirm_img = _screencap(context)
                if (
                    confirm_img is not None
                    and context.run_recognition(
                        "PopUpWindowConfirm", confirm_img
                    ).hit
                ):
                    context.run_task("PopUpWindowConfirm")
                time.sleep(0.5)
        return False
    return True


def _process_fighting(context: Context) -> bool:
    """从战斗准备页或已开始的战场接管，并运行主动战斗处理器。"""
    logger.info("====== 主动战斗 ======")

    initial_state = _wait_for_battle_state(
        context,
        {"start", "ready", "victory", "fail"},
        timeout=5.0,
    )
    battle_session_key = session_key(context)
    session_ref: dict = {}

    def _finish(ok: bool) -> bool:
        """收尾：结束战斗会话，暂存胜负与回合数（真正写盘在 finalize_task_record）。"""
        global _last_fight_result
        session = session_ref.get("session") or BattleSessionRegistry.end(
            battle_session_key
        )
        _last_fight_result = {
            "rounds": session.confirmed_rounds if session else 0,
            "result": "胜利" if ok else "失败",
        }
        if session is not None and session.pieces:
            # 棋子识别结果：整场战斗里每名棋子只保留最后一次识别。
            summary = "、".join(
                f"{name}（{record.get('range_class') or '范围未知'}，"
                f"第{record.get('round_seen', 0)}回合）"
                for name, record in session.pieces.items()
            )
            logger.info(f"本场棋子识别: {summary}")
        return ok

    if initial_state == "victory":
        logger.info("进入主动战斗前已识别到胜利结算页")
        return _finish(True)
    if initial_state == "fail":
        logger.error("进入主动战斗前已识别到失败结算页")
        return _finish(False)
    entered_from_start = initial_state == "start"
    if initial_state == "start":
        initial_state = _start_battle_and_wait_ready(context)

    if initial_state == "victory":
        logger.info("战斗开始后直接进入胜利结算页")
        return _finish(True)
    if initial_state == "fail":
        logger.error("战斗开始后直接进入失败结算页")
        return _finish(False)
    if initial_state != "ready":
        logger.error("等待稳定战斗界面超时，未识别到结束回合按钮")
        return False

    session = BattleSessionRegistry.begin(
        battle_session_key,
        force_new=entered_from_start,
    )
    session_ref["session"] = session
    logger.info(
        "稳定战斗界面已就绪，接入 AutoFightProcessor: "
        f"new={entered_from_start}, confirmed_rounds={session.confirmed_rounds}, "
        f"explored={len(session.world.observed)}"
    )
    auto_result = context.run_task(
        "AutoFight_Start", pipeline_override=battle_option_override(context)
    )

    # CustomAction 返回后仍以持久结算页为最终依据，避免仅凭节点被尝试过
    # 或控制器点击成功就把整场战斗判为成功。
    terminal_state = _wait_for_battle_state(
        context,
        {"victory", "fail"},
        timeout=5.0,
    )
    if terminal_state == "victory":
        logger.info("AutoFightProcessor 已完成正式战斗并识别到胜利结算页")
        return _finish(True)
    if terminal_state == "fail":
        logger.error("AutoFightProcessor 结束后识别到战斗失败结算页")
        return _finish(False)
    if not _task_succeeded(auto_result):
        logger.error("AutoFightProcessor 执行失败，且未出现胜负结算页")
        return False

    logger.error("AutoFightProcessor 返回成功，但未识别到胜利结算页")
    return False


def _start_battle_and_wait_ready(context: Context, max_attempts: int = 3) -> str:
    """点击开战后必须观察到开始页消失，否则按整块按钮区域重试。"""
    for attempt in range(max_attempts):
        node = "FightStart" if attempt == 0 else "FightStartFallback"
        result = context.run_task(node)
        if not _task_succeeded(result):
            logger.warning(f"{node} 执行失败 ({attempt + 1}/{max_attempts})")

        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if context.tasker.stopping:
                return "stopped"
            state = _detect_battle_state(context, _screencap(context))
            if state in {"ready", "victory", "fail"}:
                return state
            time.sleep(0.2)

        logger.warning(
            f"开战点击后页面仍未进入战场 ({attempt + 1}/{max_attempts})"
        )

    return "unknown"


def _process_post(context: Context) -> bool:
    victory_img = _screencap(context)
    if victory_img is not None and context.run_recognition(
        "FightVictory", victory_img
    ).hit:
        context.run_task("FightVictory")

    # 检测升级技能
    for _ in range(10):
        img = _screencap(context)
        if img is None or not context.run_recognition(
            "FightResultLearnSkill", img
        ).hit:
            break
        context.run_task("FightResultLearnSkill")

    # 检测是否有弹窗
    popup_img = _screencap(context)
    if popup_img is not None and context.run_recognition(
        "FightPopUp", popup_img
    ).hit:
        context.run_task("FightPopUp")

    # 结束确认前再确认一次场景：战利品/经验结算期间可能弹出孩子出生页。
    for _ in range(2):
        if not handle_interrupt_scene(context):
            break
        time.sleep(0.5)
    context.run_task("FightResult_ReturnBigMap")
    if _wait_for_recognition(context, "UI_MainWindows", timeout=6.0):
        logger.info("战后流程完成，已返回大地图")
        return True

    logger.warning("战后奖励流程结束后未直接识别到大地图，尝试处理战后中断事件")
    if _recover_post_battle_to_bigmap(context):
        logger.info("已处理战后中断事件并回到大地图")
        return True

    logger.error("战后流程未能回到大地图")
    return False


def _recover_post_battle_to_bigmap(context: Context, max_steps: int = 8) -> bool:
    """有界处理战斗结算后立即弹出的随机事件，并回到大地图。"""
    # 局部导入避免 fight_processor -> fight_utils 的模块加载环。
    from action.fight.fight_processor import detect_and_manage_event

    for step in range(max_steps):
        img = _screencap(context)
        if img is None:
            return False
        if context.run_recognition("UI_MainWindows", img).hit:
            return True

        event_name = detect_and_manage_event(context, img)
        if event_name:
            logger.info(f"战后中断事件已处理: {event_name} ({step + 1}/{max_steps})")
            time.sleep(0.5)
            continue

        # 非事件页面仅做一次有界恢复，下一轮必须重新识别状态。
        if context.run_recognition("BackButton_500ms", img).hit:
            context.run_task("BackButton_500ms")
        else:
            context.run_task("ClickCenter_500ms")
        time.sleep(0.5)

    return ensure_at_bigmap(context, max_attempts=2)


def _screencap(context: Context) -> Optional[Any]:
    job = context.tasker.controller.post_screencap().wait()
    if not job.succeeded:
        return None
    return job.get()


def _detect_battle_state(context: Context, img: Any) -> str:
    if img is None:
        return "unknown"
    if context.run_recognition("FightFail", img).hit:
        return "fail"
    if context.run_recognition("FightVictory", img).hit:
        return "victory"
    if context.run_recognition("FightEndRound", img).hit:
        return "ready"
    if context.run_recognition("FightStart", img).hit:
        return "start"
    return "unknown"


def _wait_for_battle_state(
    context: Context, expected: Set[str], timeout: float
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return "stopped"
        state = _detect_battle_state(context, _screencap(context))
        if state in expected:
            return state
        time.sleep(0.2)
    return "unknown"


def _wait_for_recognition(context: Context, node: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        img = _screencap(context)
        if img is not None and context.run_recognition(node, img).hit:
            return True
        time.sleep(0.2)
    return False


def _task_succeeded(result: Any) -> bool:
    status = getattr(result, "status", None)
    return bool(status is not None and getattr(status, "succeeded", False))
