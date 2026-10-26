"""城堡被攻打事件处理器。

实机流程（720x1280）：

1. 大地图顶部横幅：「1座城堡被攻打中。」（可点）
2. 点击后弹出「城堡」窗口：「下列城堡正在被攻打中」，每行城堡后面有「前去」
3. 点「前去」进入防守窗口：「敌方攻击中！」「防守剩余时间：5个月」「防守要塞」
4. 点「防守要塞」进入战斗；打完后处理结算并回大地图

选项「城堡被攻打处理」：

- ``defend``（默认）：按上面流程点进去并交给 ``AutoFight_Start`` 打完整场；
- ``ignore``：按返回键关掉窗口，放弃本次防守。

所有 OCR 命中文字都会写进日志，按钮文案变化时可据此校准
``resource/base/pipeline/event_utils.json`` 里的对应节点。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger

import action.fight.fight_utils as fight_utils

BANNER_NODE = "Event_CastleAttacked"
LIST_NODE = "Event_CastleListWindow"
DEFEND_WINDOW_NODE = "Event_CastleDefendWindow"
BANNER_CLICK_NODE = "Event_CastleAttackBannerClick"
LIST_GO_NODE = "Event_CastleListGo"
DEFEND_BUTTON_NODE = "Event_CastleDefendButton"
TRAVEL_NODE = "Event_CastleTravelDialog"
TRAVEL_CONFIRM_NODE = "Event_CastleTravelConfirm"
INTERRUPT_CANCEL_NODE = "Event_CastleInterruptCancel"
TEXT_NODE = "Event_CastleAttackText"
POLICY_NODE = "CastleDefenseSelect"

POLICY_DEFEND = "defend"
POLICY_IGNORE = "ignore"


@AgentServer.custom_action("CastleDefenseProcessor")
class CastleDefenseProcessor(CustomAction):
    """处理「城堡被攻打」：前往防守并打完，或按设置忽略。"""

    MAX_WAIT_SECONDS = 90
    STEP_INTERVAL = 0.9

    def run(
        self, context: Context, _argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        image = self._screencap(context)
        if image is None:
            logger.error("城堡被攻打：截图失败")
            return CustomAction.RunResult(success=False)

        banner = context.run_recognition(BANNER_NODE, image)
        castle_list = context.run_recognition(LIST_NODE, image)
        defend_window = context.run_recognition(DEFEND_WINDOW_NODE, image)
        travel_dialog = context.run_recognition(TRAVEL_NODE, image)
        if not any(
            item and item.hit
            for item in (banner, castle_list, defend_window, travel_dialog)
        ):
            logger.warning(
                "城堡被攻打：当前画面没有识别到横幅 / 城堡列表 / 防守窗口"
            )
            self._log_screen_text(context, image)
            return CustomAction.RunResult(success=False)

        policy = self._policy(context)
        logger.info(
            "城堡被攻打：检测到事件，处理方式="
            f"{'前往防守' if policy == POLICY_DEFEND else '忽略'}"
        )
        self._log_screen_text(context, image)

        if policy == POLICY_IGNORE:
            self._close_windows(context)
            logger.info("城堡被攻打：已按设置忽略本次事件")
            fight_utils.ensure_at_bigmap(context)
            return CustomAction.RunResult(success=True)

        # 1) 横幅 → 弹窗；已经在弹窗里则不点
        if (banner and banner.hit) and not (
            (castle_list and castle_list.hit)
            or (defend_window and defend_window.hit)
            or (travel_dialog and travel_dialog.hit)
        ):
            if self._click_node(context, BANNER_CLICK_NODE):
                logger.info("城堡被攻打：已点击顶部横幅")
            else:
                logger.warning("城堡被攻打：横幅 OCR 点击失败，继续等待弹窗")

        # 2) 弹窗列表 → 前去 → 防守要塞 → 行路提示 → 战斗
        deadline = time.monotonic() + self.MAX_WAIT_SECONDS
        travel_grace_until = 0.0
        while time.monotonic() < deadline:
            if context.tasker.stopping:
                logger.info("任务执行被停止")
                return CustomAction.RunResult(success=False)

            current = self._screencap(context)
            if current is None:
                time.sleep(self.STEP_INTERVAL)
                continue

            if self._battle_ready(context, current):
                logger.info("城堡被攻打：进入防守战斗，交给 AutoFight_Start")
                battle_result = context.run_task(
                    "AutoFight_Start",
                    pipeline_override=fight_utils.battle_option_override(context),
                )
                if not fight_utils._task_succeeded(battle_result):
                    logger.warning(
                        "城堡被攻打：AutoFight_Start 未正常结束，继续尝试收尾"
                    )
                self._finish_battle(context)
                return CustomAction.RunResult(success=True)

            if self._terminal_page(context, current):
                logger.info("城堡被攻打：战斗已直接结束，进入结算")
                self._finish_battle(context)
                return CustomAction.RunResult(success=True)

            if context.run_recognition(LIST_NODE, current).hit:
                if self._click_node(context, LIST_GO_NODE):
                    logger.info("城堡被攻打：已点击「前去」")
                    travel_grace_until = time.monotonic() + 6.0
                    continue
                logger.warning("城堡被攻打：列表窗口点击「前去」失败")

            elif context.run_recognition(DEFEND_WINDOW_NODE, current).hit:
                if self._click_node(context, DEFEND_BUTTON_NODE):
                    logger.info("城堡被攻打：已点击「防守要塞」")
                    continue
                logger.warning("城堡被攻打：防守窗口点击「防守要塞」失败")

            elif context.run_recognition(TRAVEL_NODE, current).hit:
                if self._click_node(context, TRAVEL_CONFIRM_NODE):
                    logger.info("城堡被攻打：已确认步行前往（消耗 1 个月）")
                    continue
                logger.warning("城堡被攻打：行路提示点击「确定」失败")

            elif context.run_recognition("UI_MainWindows", current).hit:
                # 还在地图：先确认不是「地图被防守窗口盖住」的误判。
                # 防守窗口的 OCR 框曾偏上/偏下，导致地图模板命中后过早
                # 返回；这里用画面文字兜底，看到城堡事件文字就继续等。
                if context.run_recognition(BANNER_NODE, current).hit:
                    self._click_node(context, BANNER_CLICK_NODE)
                elif self._has_castle_text(context, current):
                    logger.info(
                        "城堡被攻打：地图上仍有城堡事件文字，继续等待防守窗口"
                    )
                elif time.monotonic() < travel_grace_until:
                    logger.info("城堡被攻打：刚点完「前去」，等待防守窗口出现")
                else:
                    logger.info("城堡被攻打：已回到大地图")
                    return CustomAction.RunResult(success=True)

            else:
                # 行路途中可能弹出随机事件（如佣兵加入）。这里只做保守处理：
                # 点「取消」把它收掉，避免卡住防守流程。
                if self._click_node(context, INTERRUPT_CANCEL_NODE):
                    logger.info("城堡被攻打：行路途中弹窗已取消，继续防守流程")
                    continue

            time.sleep(self.STEP_INTERVAL)

        logger.warning("城堡被攻打：等待防守流程超时，尝试退出并回大地图")
        self._log_screen_text(context, self._screencap(context))
        self._close_windows(context)
        fight_utils.ensure_at_bigmap(context)
        return CustomAction.RunResult(success=False)

    # ------------------------------------------------------------------ 选项

    @classmethod
    def _policy(cls, context: Context) -> str:
        """读取「城堡被攻打处理」：defend / ignore。"""
        data = context.get_node_data(POLICY_NODE)
        expected: Any = None
        if isinstance(data, dict):
            recognition = data.get("recognition")
            if isinstance(recognition, dict):
                param = recognition.get("param")
                if isinstance(param, dict):
                    expected = param.get("expected")
            if expected is None:
                expected = data.get("expected")
        if isinstance(expected, (list, tuple)):
            expected = expected[0] if expected else None
        value = str(expected or POLICY_DEFEND).strip().lower()
        return POLICY_IGNORE if value in {"ignore", "忽略", "off"} else POLICY_DEFEND

    # ------------------------------------------------------------------ 动作

    @staticmethod
    def _click_node(context: Context, node: str) -> bool:
        try:
            result = context.run_task(node)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"城堡被攻打：执行 {node} 失败：{exc}")
            return False
        return fight_utils._task_succeeded(result)

    @staticmethod
    def _close_windows(context: Context) -> None:
        """按返回键关闭列表 / 防守窗口（最多两次）。"""
        for _ in range(2):
            try:
                context.tasker.controller.post_press_key(4).wait()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"城堡被攻打：返回键失败：{exc}")
                break
            time.sleep(0.6)

    # ------------------------------------------------------------------ 识别

    @staticmethod
    def _battle_ready(context: Context, image: Any) -> bool:
        # 防守要塞可能直接进战斗，也可能先停在「战斗开始」准备页；
        # 后者交给 AutoFightProcessor 的场景确认去点开始。
        return bool(
            context.run_recognition("FightEndRound", image).hit
            or context.run_recognition("FightStart", image).hit
        )

    @staticmethod
    def _terminal_page(context: Context, image: Any) -> bool:
        for node in ("FightVictory", "FightFail"):
            if context.run_recognition(node, image).hit:
                return True
        return False

    def _finish_battle(self, context: Context) -> None:
        """点掉防守战斗的胜利/失败结算，尽量回到大地图。"""
        for _ in range(10):
            if context.tasker.stopping:
                return
            image = self._screencap(context)
            if image is None:
                time.sleep(self.STEP_INTERVAL)
                continue
            if context.run_recognition("UI_MainWindows", image).hit:
                logger.info("城堡被攻打：战后已回到大地图")
                return
            clicked = False
            for node in (
                "FightVictory",
                "FightResult_LearnSkill",
                "FightResult_ReturnBigMap",
                "FightPopUp",
                "PopUpWindowConfirm",
            ):
                try:
                    if context.run_recognition(node, image).hit:
                        context.run_task(node)
                        clicked = True
                        break
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"城堡被攻打：结算节点 {node} 失败：{exc}")
            if not clicked:
                time.sleep(self.STEP_INTERVAL)
        fight_utils.ensure_at_bigmap(context)

    def _has_castle_text(self, context: Context, image: Any) -> bool:
        """用画面 OCR 判断是否仍有城堡事件，避免地图模板误判。"""
        try:
            detail = context.run_recognition(TEXT_NODE, image)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"城堡被攻打：检测城堡文字失败：{exc}")
            return False
        if not detail or not getattr(detail, "all_results", None):
            return False
        text = " ".join(
            (item.text or "").strip()
            for item in detail.all_results
            if (item.text or "").strip()
        )
        return any(
            key in text
            for key in ("城堡被攻打", "防守要塞", "敌方攻击中", "防守剩余时间", "前去")
        )

    def _log_screen_text(
        self, context: Context, image: Optional[Any]
    ) -> None:
        if image is None:
            return
        try:
            detail = context.run_recognition(TEXT_NODE, image)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"城堡被攻打：读取画面文字失败：{exc}")
            return
        if not detail or not getattr(detail, "all_results", None):
            return
        texts = [
            (item.text or "").strip()
            for item in detail.all_results
            if (item.text or "").strip()
        ]
        if texts:
            logger.info("城堡被攻打：画面文字=" + " | ".join(texts[:20]))

    @staticmethod
    def _screencap(context: Context) -> Optional[Any]:
        job = context.tasker.controller.post_screencap().wait()
        if not job.succeeded:
            return None
        return job.get()
