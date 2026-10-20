# -*- coding: utf-8 -*-
"""城堡相亲界面操作：筛选公爵、按年龄从大到小排序。

单项测试动作 CastleMarryTest 会在此基础上选第一个候选人并点联姻/速配；
自动相亲流程（MarryProcessor）也会先调用 ensure_filter_and_sort()，
避免两边各写一份坐标。
"""

from __future__ import annotations

import time

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger

FILTER_BTN = (95, 645)
DUKE_CHECKBOX = (300, 963)
CONFIRM_BTN = (505, 1070)
SORT_ICON = (678, 645)
AGE_DESC = (600, 505)
FIRST_CELL = (72, 740)
MARRY_BTN = (365, 1218)
FAST_MARRY_BTN = (635, 1218)


def _click(context: Context, x: int, y: int, wait: float = 0.5) -> None:
    context.tasker.controller.post_click(x, y).wait()
    time.sleep(wait)


def _text(context: Context, roi, expected=None) -> str:
    try:
        detail = context.run_recognition(
            "MarryUIText",
            context.tasker.controller.post_screencap().wait().get(),
            pipeline_override={
                "MarryUIText": {
                    "roi": roi,
                    "expected": expected or ["[一-龥0-9↑↓/]{1,20}"],
                }
            },
        )
        if detail.hit and detail.best_result is not None:
            return detail.best_result.text or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"界面 OCR 失败：{exc}")
    return ""


def ensure_filter_and_sort(context: Context) -> None:
    """在联姻界面执行：筛选公爵 → 确定 → 年龄↑（从大到小）。"""
    logger.info("联姻界面：点击筛选")
    _click(context, *FILTER_BTN, wait=0.8)

    logger.info("联姻界面：勾选公爵")
    _click(context, *DUKE_CHECKBOX, wait=0.3)
    logger.info("联姻界面：点击确定")
    _click(context, *CONFIRM_BTN, wait=1.0)

    count = _text(context, [230, 630, 260, 40])
    logger.info(f"筛选后数量：{count or '未识别'}")
    if count.startswith("0/"):
        logger.warning("筛选后没有对象，重新勾选公爵")
        _click(context, *FILTER_BTN, wait=0.8)
        _click(context, *DUKE_CHECKBOX, wait=0.3)
        _click(context, *CONFIRM_BTN, wait=1.0)

    logger.info("联姻界面：打开排序菜单")
    _click(context, *SORT_ICON, wait=0.8)
    sort_text = _text(context, [560, 630, 140, 40])
    logger.info(f"当前排序：{sort_text or '未识别'}")
    if "↑" not in (sort_text or ""):
        logger.info("联姻界面：选择年龄↑（从大到小）")
        _click(context, *AGE_DESC, wait=0.8)


@AgentServer.custom_action("CastleMarryTest")
class CastleMarryTestProcessor(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 只在该界面执行，避免 CastleHall 把已经打开的联姻页关掉
        if not _text(context, [280, 0, 160, 70], ["联姻"]):
            logger.error("当前不在联姻界面，请先打开 城堡-城堡主厅-联姻")
            return CustomAction.RunResult(success=False)

        ensure_filter_and_sort(context)

        logger.info("单项测试：选择第一个候选人")
        _click(context, *FIRST_CELL, wait=0.6)

        action = "联姻"
        data = context.get_node_data("MarryActionSelect")
        if data:
            expected = data.get("recognition", {}).get("param", {}).get(
                "expected", ["联姻"]
            )
            action = expected[0] if isinstance(expected, list) else expected

        if action == "速配联姻":
            logger.info("单项测试：点击速配联姻")
            _click(context, *FAST_MARRY_BTN, wait=1.0)
        else:
            logger.info("单项测试：点击联姻")
            _click(context, *MARRY_BTN, wait=1.0)

        logger.info("城堡相亲单项测试完成")
        return CustomAction.RunResult(success=True)
