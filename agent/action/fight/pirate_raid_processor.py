"""海盗袭击庄园事件处理器。

「海盗正在袭扰你的庄园」是跨地图事件：横幅出现在大陆层，
玩家需要点击横幅 → 战斗准备页 → 进入战斗 → 航海确认 → 战斗画面
→ 战斗开始 → AutoFightProcessor 跑全场 → 结算收尾 → 切回大陆层。

切层统一走 ``map_layer``：底部页签 → 区域面板 → 目标区域（大陆/群岛同两套逻辑）。
"""

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger

import action.fight.fight_utils as fight_utils
import action.fight.map_layer as map_layer


BATTLE_READY_NODE = "Event_PirateRaid_BattlePage"
ACTIVE_BATTLE_NODE = "FightEndRound"


def _layer_hit(context: Context, img, nodes: tuple) -> bool:
    return map_layer.layer_hit(context, img, nodes)


def _tap(context: Context, node: str, label: str) -> bool:
    ok = map_layer.tap(context, node, label)
    if ok:
        logger.info(f"{label} 节点 {node} 点击成功")
    else:
        logger.error(f"{label} 节点 {node} 点击失败")
    return ok


@AgentServer.custom_action("PirateRaidProcessor")
class PirateRaidProcessor(CustomAction):
    """执行海盗袭击庄园事件。"""

    def run(
        self, context: Context, _argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        logger.info("=== 海盗袭击庄园事件开始 ===")

        # 0/1. 支持从大陆、已在群岛、旅行弹窗、战斗准备页或已开战状态续跑。
        initial_img = map_layer.screencap(context)
        initial_dialog = bool(
            initial_img is not None and map_layer.dialog_visible(context, initial_img)
        )
        at_victory = bool(
            initial_img is not None
            and (
                context.run_recognition("FightVictory", initial_img).hit
                or context.run_recognition(
                    "Event_PirateRaid_VictoryConfirm", initial_img
                ).hit
            )
        )
        at_battle_page = bool(
            initial_img is not None
            and context.run_recognition(BATTLE_READY_NODE, initial_img).hit
        )
        in_active_battle = bool(
            initial_img is not None
            and context.run_recognition(ACTIVE_BATTLE_NODE, initial_img).hit
        )
        already_archipelago = bool(
            initial_img is not None
            and not initial_dialog
            and _layer_hit(context, initial_img, map_layer.ARCHIPELAGO_READY_NODES)
        )

        if initial_dialog:
            logger.info("海盗事件：当前在旅行弹窗，从航海确认继续")
            if not map_layer.handle_travel_dialog(context, initial_img):
                return CustomAction.RunResult(success=False)
            if not map_layer.wait_for_layer_ready(
                context,
                map_layer.ARCHIPELAGO_READY_NODES
                + (BATTLE_READY_NODE, ACTIVE_BATTLE_NODE),
                label="群岛/战斗准备",
            ):
                return CustomAction.RunResult(success=False)
            resumed_img = map_layer.screencap(context)
            if resumed_img is None:
                return CustomAction.RunResult(success=False)
            at_battle_page = context.run_recognition(
                BATTLE_READY_NODE, resumed_img
            ).hit
            in_active_battle = context.run_recognition(
                ACTIVE_BATTLE_NODE, resumed_img
            ).hit
            already_archipelago = _layer_hit(
                context, resumed_img, map_layer.ARCHIPELAGO_READY_NODES
            )

        if at_victory:
            logger.info("海盗事件：当前已在战斗胜利页，从结算继续")
        elif at_battle_page:
            logger.info("海盗事件：当前已在战斗准备页，从战斗开始继续")
        elif in_active_battle:
            logger.info("海盗事件：当前已经开战，直接交给主动战斗处理器")
        elif already_archipelago:
            logger.info("海盗事件：当前已在群岛层，跳过重复切图")
        else:
            if not fight_utils.ensure_at_bigmap(context):
                logger.error("海盗事件：无法回到大地图，放弃")
                return CustomAction.RunResult(success=False)
            if not map_layer.switch_to_archipelago(context):
                logger.error("海盗事件：切群岛失败")
                return CustomAction.RunResult(success=False)
            # 海盗袭击期间，切层后可能直接落到战斗准备页，
            # 也可能先落到群岛地图；二者都是合法的切层终态。
            if not map_layer.wait_for_layer_ready(
                context,
                map_layer.ARCHIPELAGO_READY_NODES + (BATTLE_READY_NODE,),
                label="群岛/战斗准备",
            ):
                return CustomAction.RunResult(success=False)
            switched_img = map_layer.screencap(context)
            if switched_img is None:
                return CustomAction.RunResult(success=False)
            at_battle_page = context.run_recognition(
                BATTLE_READY_NODE, switched_img
            ).hit
            in_active_battle = context.run_recognition(
                ACTIVE_BATTLE_NODE, switched_img
            ).hit
            already_archipelago = _layer_hit(
                context, switched_img, map_layer.ARCHIPELAGO_READY_NODES
            )

        # 2. 走「点横幅 → 准备页 → 进入战斗 → 航海 → 战斗画面 →
        #    战斗开始 → AutoFightProcessor」的 pipeline 链。
        if not at_victory and not in_active_battle:
            nodes = (
                ("Event_PirateRaid_BattleStart",)
                if at_battle_page
                else (
                    "Event_PirateRaid_ClickBanner",
                    "Event_PirateRaid_EnterBattle",
                    "Event_PirateRaid_SailingConfirm",
                    "Event_PirateRaid_BattleStart",
                )
            )
            for node in nodes:
                if not _tap(context, node, "海盗链"):
                    # 任一节点失败都尝试切回大陆，避免下一轮被卡。
                    map_layer.switch_to_continent(context)
                    return CustomAction.RunResult(success=False)

        # 3. AutoFightProcessor 跑全场战斗，胜负检测由其内部完成。
        if not at_victory:
            result = context.run_task("AutoFight_Start")
            if not fight_utils._task_succeeded(result):
                logger.error("海盗事件：群岛层战斗未成功结束")
                map_layer.switch_to_continent(context)
                return CustomAction.RunResult(success=False)

        # 4. 战斗胜利 → 战利品页「确定」+ 经验页「确定」。
        #    Event_PirateRaid_VictoryConfirm 节点里 next 循环指向自身，
        #    直到命中 Event_PirateRaid_ArchipelagoBack（即回到群岛层）。
        if not _tap(context, "Event_PirateRaid_VictoryConfirm", "结算"):
            logger.warning("海盗事件：战利品/经验结算未完成，但继续切回大陆")
        if not map_layer.wait_for_layer_ready(
            context, ("Event_PirateRaid_ArchipelagoBack",), 10.0, "群岛(战结)"
        ):
            logger.warning("海盗事件：未确认回到群岛层，仍尝试切回大陆")

        # 5. 切回大陆层（和切群岛同一套两步逻辑：页签 → 加尔提兰）。
        if not map_layer.switch_to_continent(context):
            logger.error("海盗事件：未能切回大陆层")
            return CustomAction.RunResult(success=False)
        if not map_layer.wait_for_layer_ready(
            context, map_layer.CONTINENT_READY_NODES, label="大陆"
        ):
            logger.warning("海盗事件：切回大陆后未确认图层就绪")

        logger.info("=== 海盗袭击庄园事件完成 ===")
        return CustomAction.RunResult(success=True)
