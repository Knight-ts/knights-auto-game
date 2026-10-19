# -*- coding: utf-8 -*-
"""佣兵加入事件的智能判断与弹窗处理。

真实界面有两种形态：
    1. 地图横幅：“鲍尔和上自的孩子成年了！可以加入佣兵团” —— 需要先点它
    2. 弹窗：有“取消 / 招募”按钮，以及独立的佣兵名字行（如“菲佛瑞”）

所以流程是：先找按钮；找不到就点横幅；再点按钮；最后用按钮是否消失来校验。
"""

from __future__ import annotations

import re
import time

from utils import logger
from utils.game_state import year_month
from utils.mercenary_records import append as append_record
from utils.mercenary_rules import decide, load as load_rules

PROBE_NODE = "Event_MercenaryDialogButton"
CONFIRM_NODE = "Event_MercenaryJoinConfirm"
CANCEL_NODE = "Event_MercenaryJoinCancel"

BUTTON_ROI = [40, 480, 640, 700]
BANNER_ROI = [40, 200, 640, 260]

CONFIRM_TEXTS = ["招募", "同意", "确认加入"]
CANCEL_TEXTS = ["取消", "拒绝", "取消加入"]

DEFAULT_STOPWORDS = [
    "佣兵",
    "加入",
    "确定",
    "取消",
    "招募",
    "银币",
    "年龄",
    "希望",
    "孩子",
    "成年",
    "可以",
    "拒绝",
]


def _screencap(context):
    return context.tasker.controller.post_screencap().wait().get()


def _ocr(context, img, roi, expected):
    try:
        return context.run_recognition(
            PROBE_NODE,
            img,
            pipeline_override={PROBE_NODE: {"roi": roi, "expected": expected}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵弹窗 OCR 异常：{exc}")
        return None


def _click_box(context, box) -> None:
    x = int(box[0] + box[2] / 2)
    y = int(box[1] + box[3] / 2)
    context.tasker.controller.post_click(x, y).wait()


def _find_button(context, img, accept: bool):
    for text in CONFIRM_TEXTS if accept else CANCEL_TEXTS:
        detail = _ocr(context, img, BUTTON_ROI, [text])
        if detail is not None and detail.hit and detail.best_result is not None:
            return detail.best_result.box, text
    return None, None


def buttons_visible(context, img=None) -> bool:
    img = img if img is not None else _screencap(context)
    for text in CONFIRM_TEXTS + CANCEL_TEXTS:
        detail = _ocr(context, img, BUTTON_ROI, [text])
        if detail is not None and detail.hit:
            return True
    return False


def _dialog_texts(context, img) -> list[str]:
    try:
        detail = _ocr(context, img, [40, 200, 640, 700], ["[一-龥·0-9！]{1,20}"])
        texts = []
        for result in getattr(detail, "filtered_results", None) or []:
            text = getattr(result, "text", "") or ""
            if text:
                texts.append(text)
        return texts
    except Exception:  # noqa: BLE001
        return []


def _open_banner(context, img) -> bool:
    """点地图上的“孩子成年了！可以加入佣兵团”横幅。"""
    detail = _ocr(context, img, BANNER_ROI, ["加入佣兵团", "孩子成年"])
    if detail is None or not detail.hit or detail.best_result is None:
        return False
    _click_box(context, detail.best_result.box)
    time.sleep(0.8)
    return True


def dismiss_join(context, accept: bool) -> bool:
    """关闭佣兵加入弹窗：先点按钮，没按钮就点横幅打开，再点按钮。"""
    img = _screencap(context)
    if not buttons_visible(context, img):
        _open_banner(context, img)
        img = _screencap(context)

    for choice in (accept, not accept):
        box, text = _find_button(context, img, choice)
        if box is None:
            continue
        logger.info(f"佣兵加入：点击「{text}」")
        _click_box(context, box)
        time.sleep(0.5)
        img = _screencap(context)
        if not buttons_visible(context, img):
            return True

    texts = _dialog_texts(context, img)
    logger.warning(f"佣兵弹窗未关闭，当前 OCR 文本：{texts}")
    return False


def _recognize_name(context, screenshot, rules: dict) -> tuple[str, str]:
    """从弹窗里取独立的短行作为名字；失败返回空。"""
    try:
        detail = context.run_recognition(
            PROBE_NODE,
            screenshot,
            pipeline_override={
                PROBE_NODE: {
                    "roi": rules.get("name_roi", [40, 200, 640, 700]),
                    "expected": [rules.get("name_regex", "[一-龥·]{2,5}")],
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵名字识别异常：{exc}")
        return "", ""

    if detail is None or not detail.hit or detail.best_result is None:
        return "", ""

    texts = []
    for result in getattr(detail, "filtered_results", None) or []:
        text = getattr(result, "text", "") or ""
        if text:
            texts.append(text)
    if not texts:
        texts = [detail.best_result.text or ""]

    stopwords = set(rules.get("stopwords") or []) | set(DEFAULT_STOPWORDS)
    pattern = re.compile(rules.get("name_regex", "[一-龥·]{2,5}"))
    for text in texts:
        candidate = text.strip()
        if not pattern.fullmatch(candidate):
            continue
        if candidate in stopwords or any(word in candidate for word in stopwords):
            continue
        return candidate, text

    # 兜底：名字混在句子里时，取不含“的/了/！”的短片段
    for text in texts:
        for candidate in pattern.findall(text):
            if any(ch in candidate for ch in "的了！"):
                continue
            if candidate in stopwords or any(word in candidate for word in stopwords):
                continue
            return candidate, text
    return "", " | ".join(texts)


def handle_join(context, screenshot) -> tuple[str, bool]:
    """处理一次佣兵加入事件，返回 (名字, 是否接受)。"""
    rules = load_rules()
    name, raw = _recognize_name(context, screenshot, rules)
    accept = decide(name, rules) if name else False

    closed = dismiss_join(context, accept)

    year, month = year_month()
    try:
        append_record(
            {
                "游戏年": year or "",
                "游戏月": month or "",
                "佣兵名字": name or "(未识别)",
                "结果": "接受" if accept else "拒绝",
                "匹配规则": rules.get("mode", ""),
                "备注": (raw[:80] if raw else "") + ("" if closed else "；弹窗未关闭"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写入佣兵记录失败：{exc}")

    logger.info(
        f"佣兵加入：名字={name or '未识别'}，规则={rules.get('mode')}，"
        f"{'接受' if accept else '拒绝'}，弹窗{'已关闭' if closed else '未关闭'}"
    )
    return name, accept
