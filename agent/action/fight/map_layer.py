# -*- coding: utf-8 -*-
"""地图层切换：大陆（加尔提兰） ↔ 群岛（瑞格群岛）。

新版界面底部中间的页签显示**当前区域**，点它弹出区域面板，面板里选目标区域：

    大陆 → 群岛：点页签「加尔提兰」 → 点面板「瑞格群岛」
    群岛 → 大陆：点页签「瑞格群岛」 → 点面板「加尔提兰」

两个方向逻辑完全一致，所以海盗事件、功能任务共用本模块。
"""

import time
from typing import Optional

from maa.context import Context

import action.fight.fight_utils as fight_utils

# 群岛层就绪：页签显示「瑞格群岛」，或旧版「前往大陆」按钮
ARCHIPELAGO_READY_NODES = ("RegionTabArchipelago", "ClickGoToContinent")
# 大陆层就绪：页签显示「加尔提兰」、主城可见，或旧版「前往群岛」按钮
CONTINENT_READY_NODES = ("ClickRegionTab", "EnterCity", "ClickGoToArchipelago")
LAYER_SWITCH_TIMEOUT = 10.0

LAYER_ARCHIPELAGO = "archipelago"
LAYER_CONTINENT = "continent"


def screencap(context: Context):
    job = context.tasker.controller.post_screencap().wait()
    if not job.succeeded:
        return None
    return job.get()


def layer_hit(context: Context, img, nodes: tuple) -> bool:
    return any(context.run_recognition(node, img).hit for node in nodes)


def dialog_visible(context: Context, img) -> bool:
    """旅行弹窗存在时，不能使用弹窗背后的地图按钮作为就绪信号。"""
    return bool(
        context.run_recognition("Event_PirateRaid_TravelDialog", img).hit
        or context.run_recognition("TravelDialog", img).hit
    )


def wait_dialog_closed(context: Context, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return False
        img = screencap(context)
        if img is not None and not dialog_visible(context, img):
            return True
        time.sleep(0.25)
    return False


def tap(context: Context, node: str, label: str) -> bool:
    """点击节点并验证成功。"""
    result = context.run_task(node)
    if not fight_utils._task_succeeded(result):
        return False
    return True


def handle_travel_dialog(context: Context, img) -> bool:
    """处理已经确认存在的旅行弹窗，并验证它消失。"""
    if context.run_recognition("Event_PirateRaid_TravelDialog", img).hit:
        if context.run_recognition("Event_PirateRaid_ChooseEstate", img).hit:
            node = "Event_PirateRaid_ChooseEstate"
        else:
            node = "Event_PirateRaid_ChooseSailSingle"
        if not tap(context, node, "旅行弹窗"):
            return False
        return wait_dialog_closed(context)

    if context.run_recognition("TravelDialog", img).hit:
        if context.run_recognition("TravelDialog_ChooseFast", img).hit:
            node = "TravelDialog_ChooseFast"
        else:
            node = "TravelDialog_ChooseSlow"
        if not tap(context, node, "旅行弹窗"):
            return False
        return wait_dialog_closed(context)

    return True


def wait_for_layer_ready(
    context: Context,
    ready_nodes: tuple,
    timeout: float = LAYER_SWITCH_TIMEOUT,
    label: str = "目标",
) -> bool:
    """轮询截图：先处理旅行弹窗，再接受无遮挡的图层就绪信号。"""
    deadline = time.monotonic() + timeout
    accept_ready_after = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return False
        img = screencap(context)
        if img is None:
            time.sleep(0.2)
            continue
        if dialog_visible(context, img):
            if not handle_travel_dialog(context, img):
                return False
            accept_ready_after = time.monotonic() + 0.5
            continue
        if time.monotonic() < accept_ready_after:
            time.sleep(0.25)
            continue
        if layer_hit(context, img, ready_nodes):
            return True
        time.sleep(0.25)
    return False


def current_layer(context: Context, img=None) -> Optional[str]:
    """当前图层：archipelago / continent / None（判断不了）。"""
    if img is None:
        img = screencap(context)
    if img is None or dialog_visible(context, img):
        return None
    if layer_hit(context, img, ARCHIPELAGO_READY_NODES):
        return LAYER_ARCHIPELAGO
    if layer_hit(context, img, CONTINENT_READY_NODES):
        return LAYER_CONTINENT
    return None


def _open_region_panel(context: Context, target_node: str) -> bool:
    """确保区域面板已经打开：面板已开（能识别到目标项）就直接返回。"""
    img = screencap(context)
    if img is None:
        return False
    if context.run_recognition(target_node, img).hit:
        return True
    if not tap(context, "ClickRegionTabAny", "区域页签"):
        return False
    time.sleep(0.6)
    return True


def switch_to_archipelago(context: Context) -> bool:
    """切到瑞格群岛：页签 → 面板「瑞格群岛」。"""
    if current_layer(context) == LAYER_ARCHIPELAGO:
        return True
    if not _open_region_panel(context, "ClickRegionArchipelago"):
        return False
    if not tap(context, "ClickRegionArchipelago", "瑞格群岛"):
        return False
    wait_dialog_closed(context)
    return True


def switch_to_continent(context: Context) -> bool:
    """切回加尔提兰大陆：页签 → 面板「加尔提兰」。"""
    if current_layer(context) == LAYER_CONTINENT:
        return True
    if not _open_region_panel(context, "ClickRegionContinent"):
        return False
    if not tap(context, "ClickRegionContinent", "加尔提兰"):
        return False
    wait_dialog_closed(context)
    return True
