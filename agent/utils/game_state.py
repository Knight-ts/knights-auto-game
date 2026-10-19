# -*- coding: utf-8 -*-
"""游戏状态（当前年份 / 月份），写入 table/game_state.json。

年份来自大地图顶部的 C.E.xxxx OCR，月份来自 Map_CheckCurrentMonth。
孩子记录、佣兵记录都会带上这里的年月。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "table" / "game_state.json"
DEFAULT = {"year": 0, "month": 0}


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT)
    state = dict(DEFAULT)
    for key in DEFAULT:
        try:
            state[key] = int(data.get(key, 0) or 0)
        except (TypeError, ValueError):
            state[key] = 0
    return state


def save(state: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(
        json.dumps({"year": int(state.get("year", 0)), "month": int(state.get("month", 0))},
                   ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def update(year: int | None = None, month: int | None = None) -> dict:
    state = load()
    if year:
        state["year"] = int(year)
    if month:
        state["month"] = int(month)
    save(state)
    return state


def year_month() -> tuple[int, int]:
    state = load()
    return state["year"], state["month"]


def label() -> str:
    year, month = year_month()
    if not year:
        return ""
    return f"{year}年{month}月" if month else f"{year}年"
