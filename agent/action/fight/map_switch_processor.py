# -*- coding: utf-8 -*-
"""功能任务：地图切换（加尔提兰大陆 ↔ 瑞格群岛）。

全局功能任务，随时可跑，用来单独验证切层逻辑，也给海盗事件以外的流程复用。
"""

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger

import action.fight.map_layer as map_layer


def _selected_target(context: Context) -> str:
    data = context.get_node_data("MapTargetSelect")
    target = "archipelago"
    if data:
        expected = data.get("recognition", {}).get("param", {}).get(
            "expected", ["archipelago"]
        )
        if isinstance(expected, list) and expected:
            target = str(expected[0])
        elif isinstance(expected, str) and expected:
            target = expected
    return target


@AgentServer.custom_action("MapSwitchProcessor")
class MapSwitchProcessor(CustomAction):
    """功能：地图切换（大陆 ↔ 群岛），两个方向走同一套「页签 → 区域面板」逻辑。"""

    def run(
        self, context: Context, _argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        target = _selected_target(context)
        current = map_layer.current_layer(context)
        logger.info(f"=== 功能：地图切换（当前={current or '未知'}，目标={target}）===")

        if target == "continent":
            ok = map_layer.switch_to_continent(context)
            ready_nodes = map_layer.CONTINENT_READY_NODES
            label = "加尔提兰"
        else:
            ok = map_layer.switch_to_archipelago(context)
            ready_nodes = map_layer.ARCHIPELAGO_READY_NODES
            label = "瑞格群岛"

        if not ok:
            logger.error("地图切换失败：没找到区域页签或目标区域")
            return CustomAction.RunResult(success=False)

        if not map_layer.wait_for_layer_ready(context, ready_nodes, label=label):
            logger.error(f"地图切换后未确认到达「{label}」")
            return CustomAction.RunResult(success=False)

        logger.info(f"地图切换完成：{label}")
        return CustomAction.RunResult(success=True)
