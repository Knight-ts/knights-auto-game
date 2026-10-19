# -*- coding: utf-8 -*-
"""佣兵加入事件的智能判断。

流程：
    1. 在加入弹窗上 OCR 佣兵名字（ROI 可在 table/mercenary_rules.json 调整）
    2. 按白名单/黑名单规则决定接受或拒绝
    3. 直接执行 Event_MercenaryJoinConfirm / Event_MercenaryJoinCancel
    4. 把名字、结果、游戏年月写入 table/mercenary_records.csv
"""

from __future__ import annotations

import re

from utils import logger
from utils.game_state import year_month
from utils.mercenary_records import append as append_record
from utils.mercenary_rules import decide, load as load_rules

NAME_NODE = "Event_MercenaryName"


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

    node = "Event_MercenaryJoinConfirm" if accept else "Event_MercenaryJoinCancel"
    result = context.run_task(node)

    year, month = year_month()
    try:
        append_record(
            {
                "游戏年": year or "",
                "游戏月": month or "",
                "佣兵名字": name or "(未识别)",
                "结果": "接受" if accept else "拒绝",
                "匹配规则": rules.get("mode", ""),
                "备注": (raw[:80] if raw else "") + ("" if result else "；点击失败"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写入佣兵记录失败：{exc}")

    logger.info(
        f"佣兵加入：名字={name or '未识别'}，规则={rules.get('mode')}，"
        f"{'接受' if accept else '拒绝'}"
    )
    return name, accept
