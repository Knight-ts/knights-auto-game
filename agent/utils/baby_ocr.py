# -*- coding: utf-8 -*-
"""出生页识别辅助：用“第 N 个孩子”区分同一个事件里的多个孩子。"""

from __future__ import annotations


def current_index(context, img=None) -> str:
    """返回出生页上的“第N个孩子”文本，识别失败返回空串。"""
    try:
        if img is None:
            img = context.tasker.controller.post_screencap().wait().get()
        detail = context.run_recognition("PannelGetChildIndex", img)
        if detail.hit and detail.best_result is not None:
            return (detail.best_result.text or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""
