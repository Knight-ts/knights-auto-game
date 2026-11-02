# -*- coding: utf-8 -*-
"""城堡相亲界面操作：筛选爵位、按年龄排序。

单项测试动作 CastleMarryTest 会在此基础上选第一个候选人并点联姻/速配；
自动相亲流程（MarryProcessor）也会先调用 ensure_filter_and_sort()，
避免两边各写一份坐标。

筛选勾选是**开关**：爵位已经勾着再点一次会取消，导致骑士等也进入候选。
所以这里先用模板匹配读复选框状态（Marry/check_on.png / check_off.png），
只有「未勾选」才点击；状态读不出来时按未勾选处理并复检一次。
"""

from __future__ import annotations

import time

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger

FILTER_BTN = (97, 634)       # 筛选按钮（排序面板打开时也在同一条上）
DUKE_CHECKBOX = (292, 964)   # 筛选弹窗里的「公爵」
CONFIRM_BTN = (508, 1035)    # 筛选弹窗「确定」
SORT_LABEL = (600, 630)      # 排序入口（显示「年龄↑ / 力量 …」），点开排序面板
AGE_DESC = (565, 500)        # 排序面板里的「年龄↑」（从大到小）
AGE_ASC = (300, 563)         # 排序面板里的「年龄↓」（从小到大）
FIRST_CELL = (105, 800)      # 第一个候选格
MARRY_BTN = (365, 1218)
FAST_MARRY_BTN = (635, 1218)

TITLE_BOXES = {
    "公爵": (292, 964),
    "伯爵": (592, 902),
    "男爵": (292, 902),
    "骑士": (592, 845),
    "平民": (292, 845),
}
ALL_TITLES = (292, 265)      # 筛选弹窗「全部」

# 复选框 ROI（x, y, w, h；模板 36x36，留 4px 余量）——用来读「勾没勾」
CHECKBOX_ROI = {
    "全部": [545, 237, 44, 44],
    "公爵": [277, 937, 44, 44],
    "伯爵": [545, 872, 44, 44],
    "男爵": [277, 872, 44, 44],
    "骑士": [545, 807, 44, 44],
    "平民": [277, 807, 44, 44],
}
# 点击点用复选框中心（比点标签更稳）
CHECKBOX_CLICK = {name: (roi[0] + 22, roi[1] + 22) for name, roi in CHECKBOX_ROI.items()}


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


def checkbox_state(context: Context, label: str) -> str:
    """读筛选面板里某个爵位勾没勾：on / off / unknown。"""
    roi = CHECKBOX_ROI.get(label)
    if roi is None:
        return "unknown"
    img = context.tasker.controller.post_screencap().wait().get()
    on = context.run_recognition(
        "MarryCheckOn", img, pipeline_override={"MarryCheckOn": {"roi": roi}}
    )
    if on.hit:
        return "on"
    off = context.run_recognition(
        "MarryCheckOff", img, pipeline_override={"MarryCheckOff": {"roi": roi}}
    )
    if off.hit:
        return "off"
    return "unknown"


def reset_title_filter(context: Context) -> None:
    """兜底清空爵位勾选：点两次「全部」（先全选、再取消），所有爵位都变未选中。

    模板读数不可靠时用这招，避免“已经勾着又点一下”变成取消。
    """
    point = CHECKBOX_CLICK["全部"]
    logger.info("筛选：点「全部」两次清空爵位勾选")
    _click(context, *point, wait=0.3)
    _click(context, *point, wait=0.3)


def ensure_title_checked(context: Context, label: str) -> None:
    """确保某爵位处于勾选状态；已经勾着就不再点（避免点成取消）。"""
    state = checkbox_state(context, label)
    if state == "on":
        logger.info(f"筛选：{label} 已经勾选，跳过点击")
        return

    point = CHECKBOX_CLICK.get(label) or TITLE_BOXES.get(label) or ALL_TITLES
    if state == "unknown":
        logger.warning(f"筛选：{label} 勾选状态没识别出来，先清空再勾选")
        reset_title_filter(context)
        _click(context, *point, wait=0.35)
        return

    _click(context, *point, wait=0.35)
    after = checkbox_state(context, label)
    logger.info(f"筛选：勾选{label}（{state} → {after}）")
    if after == "off":
        logger.warning(f"筛选：{label} 点一次还是没勾上，再点一次")
        _click(context, *point, wait=0.35)


def ensure_filter_and_sort(
    context: Context, title: str = "公爵", age_pref: str = "older"
) -> None:
    """在联姻界面执行：筛选爵位 → 确定 → 按年龄排序 → 关掉排序面板。"""
    label = "全部" if title == "不限" else title
    logger.info(f"联姻界面：点击筛选（爵位={label}，年龄={age_pref}）")
    _click(context, *FILTER_BTN, wait=0.8)

    ensure_title_checked(context, label)

    logger.info("联姻界面：点击确定")
    _click(context, *CONFIRM_BTN, wait=1.0)

    count = _text(context, [230, 630, 260, 40])
    logger.info(f"筛选后数量：{count or '未识别'}")
    if label != "全部" and count.startswith("0/"):
        logger.warning(f"筛选后没有{label}对象，清空后重新勾选")
        _click(context, *FILTER_BTN, wait=0.8)
        reset_title_filter(context)
        _click(context, *CHECKBOX_CLICK.get(label, ALL_TITLES), wait=0.35)
        _click(context, *CONFIRM_BTN, wait=1.0)

    logger.info("联姻界面：打开排序面板")
    _click(context, *SORT_LABEL, wait=0.8)
    sort_text = _text(context, [560, 630, 140, 40])
    logger.info(f"当前排序：{sort_text or '未识别'}")
    if age_pref == "younger":
        logger.info("联姻界面：选择年龄↓（从小到大）")
        _click(context, *AGE_ASC, wait=0.6)
    else:
        logger.info("联姻界面：选择年龄↑（从大到小）")
        _click(context, *AGE_DESC, wait=0.6)

    # 排序面板没有确定/取消，点面板外的排序图标关闭；
    # 不能点候选格，否则会选中角色并打开详情，正式流程会读不到对象数量。
    logger.info("联姻界面：关闭排序面板")
    _click(context, 678, 645, wait=0.6)
    if not _text(context, [230, 630, 260, 40]):
        logger.warning("排序面板似乎还开着，再点一次空白处")
        _click(context, 680, 420, wait=0.5)


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
