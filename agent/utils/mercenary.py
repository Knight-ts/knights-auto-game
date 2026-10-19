# -*- coding: utf-8 -*-
"""佣兵加入事件的智能判断与弹窗处理。

流程：
    1. OCR 佣兵名字（ROI 可在 table/mercenary_rules.json 调整）
    2. 按白名单/黑名单规则决定接受或拒绝
    3. 点确认/取消，并校验弹窗是否消失（失败则换另一个按钮）
    4. 把名字、结果、游戏年月写入 table/mercenary_records.csv
"""

from __future__ import annotations

import re

from utils import logger
from utils.game_state import year_month
from utils.mercenary_records import append as append_record
from utils.mercenary_rules import decide, load as load_rules

NAME_NODE = "Event_MercenaryName"
CONFIRM_NODE = "Event_MercenaryJoinConfirm"
CANCEL_NODE = "Event_MercenaryJoinCancel"


def _screencap(context):
    return context.tasker.controller.post_screencap().wait().get()


def join_visible(context, img=None) -> bool:
    """加入弹窗是否还在。"""
    try:
        img = img if img is not None else _screencap(context)
        return bool(context.run_recognition("Event_MercenaryJoin", img).hit)
    except Exception:  # noqa: BLE001
        return False


def _dialog_texts(context, img) -> list[str]:
    """识别弹窗整块区域的所有文字，点击失败时用于诊断。"""
    try:
        detail = context.run_recognition(
            NAME_NODE,
            img,
            pipeline_override={
                NAME_NODE: {"roi": [40, 250, 640, 700], "expected": ["[一-龥·]{1,8}"]}
            },
        )
        texts = []
        for result in getattr(detail, "filtered_results", None) or []:
            text = getattr(result, "text", "") or ""
            if text:
                texts.append(text)
        return texts
    except Exception:  # noqa: BLE001
        return []


def dismiss_join(context, accept: bool) -> bool:
    """点确认/取消，并确认弹窗消失；失败时尝试另一个按钮。返回是否成功关闭。"""
    prefer = CONFIRM_NODE if accept else CANCEL_NODE
    fallback = CANCEL_NODE if accept else CONFIRM_NODE

    for node in (prefer, fallback):
        try:
            context.run_task(node)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"佣兵弹窗点击 {node} 异常：{exc}")
        img = _screencap(context)
        if not join_visible(context, img):
            return True

    img = _screencap(context)
    texts = _dialog_texts(context, img)
    logger.warning(f"佣兵弹窗未关闭，当前 OCR 文本：{texts}")
    return False


def _recognize_name(context, screenshot, rules: dict) -> tuple[str, str]:
    """返回 (名字, 原始 OCR 文本)。"""
    try:
        detail = context.run_recognition(
            NAME_NODE,
            screenshot,
            pipeline_override={
                NAME_NODE: {
                    "roi": rules.get("name_roi", [80, 300, 560, 420]),
                    "expected": [rules.get("name_regex", "[一-龥·]{2,5}")],
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵名字识别异常：{exc}")
        return "", ""

    if not detail.hit or detail.best_result is None:
        return "", ""

    texts = []
    for result in getattr(detail, "filtered_results", None) or []:
        text = getattr(result, "text", "") or ""
        if text:
            texts.append(text)
    if not texts:
        texts = [detail.best_result.text or ""]

    stopwords = set(rules.get("stopwords") or [])
    pattern = re.compile(rules.get("name_regex", "[一-龥·]{2,5}"))
    for text in texts:
        for candidate in pattern.findall(text):
            if candidate not in stopwords and not any(word in candidate for word in stopwords):
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
