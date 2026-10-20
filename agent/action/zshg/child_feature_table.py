# -*- coding: utf-8 -*-
"""完整特性表（按种族分类），供「好娃提醒」界面挑特性、配组合。

数据来源（只读，不写任何文件）：
    table/各国家特性表.json   血统特性：等级 / 部位 / 效果 / 寿命
    table/正负面特性表.json   正面 / 中性 / 负面 / 表白怀孕特性，以及按种族分的平民血特性

分三类：
    bloodline  血统特性，按血统（佩尔弗因王族、高等精灵……）分组
    commoner   平民血特性，按种族（加尔提斯人、古特人……）分组
    general    通用特性，不分种族（正面 / 中性 / 负面 / 表白怀孕）

输出结构见 load_feature_table()。
"""

from __future__ import annotations

import json
from pathlib import Path

BLOODLINE_REL = Path("table") / "各国家特性表.json"
GENERAL_REL = Path("table") / "正负面特性表.json"

KIND_LABELS = {"bloodline": "血统", "commoner": "平民血", "general": "通用"}
GENERAL_SECTIONS = (
    ("正面特性", "正面"),
    ("中性特性", "中性"),
    ("负面特性", "负面"),
    ("表白怀孕特性", "表白怀孕"),
)


def _read(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value) -> str:
    return str(value or "").strip()


def _feature(
    name: str,
    *,
    level: str = "",
    position: str = "",
    effect: str = "",
    lifespan: str = "",
    titles=(),
    hint: str = "",
) -> dict:
    return {
        "name": name,
        "level": level,
        "position": position,
        "effect": effect,
        "lifespan": lifespan,
        "titles": [str(item) for item in (titles or []) if _text(item)],
        "hint": hint,
    }


def _group(groups: list[dict], *, label: str, kind: str, region: str, features: list[dict]) -> None:
    if not label or not features:
        return
    groups.append(
        {
            "id": f"g{len(groups)}",
            "label": label,
            "kind": kind,
            "kindLabel": KIND_LABELS.get(kind, ""),
            "region": region,
            "features": features,
        }
    )


def _dedupe(features: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for feature in features:
        name = feature["name"]
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(feature)
    return result


def load_feature_table(root) -> dict:
    """读取两张特性表，按种族整理成前端可直接渲染的结构。"""
    root = Path(root)
    bloodline_table = _read(root / BLOODLINE_REL)
    general_table = _read(root / GENERAL_REL)

    groups: list[dict] = []

    # 1. 血统特性：一个血统一组
    for item in bloodline_table.get("bloodlines") or []:
        if not isinstance(item, dict):
            continue
        features = [
            _feature(
                _text(feature.get("name")),
                level=_text(feature.get("level")),
                position=_text(feature.get("position")),
                effect=_text(feature.get("description")),
                lifespan=_text(feature.get("lifespan")),
            )
            for feature in item.get("features") or []
            if isinstance(feature, dict)
        ]
        _group(
            groups,
            label=_text(item.get("name")),
            kind="bloodline",
            region=_text(item.get("region")),
            features=_dedupe(features),
        )

    # 2. 平民血特性：一个种族一组
    for item in general_table.get("平民血特性") or []:
        if not isinstance(item, dict):
            continue
        features = [
            _feature(
                _text(feature.get("特性名称")),
                level="平民血",
                effect=_text(feature.get("特性描述") or feature.get("特性描述：")),
                titles=feature.get("称号"),
                hint=_text(feature.get("城堡相亲描述")),
            )
            for feature in item.get("特性列表") or []
            if isinstance(feature, dict)
        ]
        _group(
            groups,
            label=_text(item.get("所属种族")),
            kind="commoner",
            region="",
            features=_dedupe(features),
        )

    # 3. 通用特性：正面 / 中性 / 负面 / 表白怀孕
    for key, short in GENERAL_SECTIONS:
        features = [
            _feature(
                _text(feature.get("特性名称")),
                level=short,
                effect=_text(feature.get("特性描述") or feature.get("特性描述：")),
                titles=feature.get("称号"),
                hint=_text(feature.get("城堡相亲描述")),
            )
            for feature in general_table.get(key) or []
            if isinstance(feature, dict)
        ]
        _group(groups, label=f"{short}特性", kind="general", region="", features=_dedupe(features))

    # 特性名 -> 所属种族（只算血统/平民血，给组合自动打种族标签）
    feature_races: dict[str, list[str]] = {}
    names: list[str] = []
    seen_names: set[str] = set()
    for group in groups:
        for feature in group["features"]:
            name = feature["name"]
            if name not in seen_names:
                seen_names.add(name)
                names.append(name)
            if group["kind"] in ("bloodline", "commoner"):
                races = feature_races.setdefault(name, [])
                if group["label"] not in races:
                    races.append(group["label"])

    names.sort(key=lambda value: (len(value), value))

    return {
        "groups": groups,
        "featureRaces": feature_races,
        "names": names,
        "counts": {
            "groups": len(groups),
            "features": len(names),
            "bloodline": sum(1 for group in groups if group["kind"] == "bloodline"),
            "commoner": sum(1 for group in groups if group["kind"] == "commoner"),
            "general": sum(1 for group in groups if group["kind"] == "general"),
        },
    }
