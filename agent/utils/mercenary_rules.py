# -*- coding: utf-8 -*-
"""佣兵智能接受规则。

table/mercenary_rules.json：
    mode: whitelist（名字命中才接受） / blacklist（名字命中则拒绝）
    names: 名字关键字列表
    name_roi / name_regex / stopwords: 佣兵名字 OCR 的区域与过滤词
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "table" / "mercenary_rules.json"

DEFAULT = {
    "mode": "whitelist",
    "names": [],
    "name_roi": [80, 300, 560, 420],
    "name_regex": "[一-龥·]{2,5}",
    "stopwords": ["佣兵", "加入", "确定", "取消", "招募", "队伍", "想要", "你的", "成为", "伙伴"],
}


def load() -> dict:
    config = dict(DEFAULT)
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    for key in DEFAULT:
        if key in data:
            config[key] = data[key]
    if config.get("mode") not in ("whitelist", "blacklist"):
        config["mode"] = "whitelist"
    config["names"] = [str(n).strip() for n in (config.get("names") or []) if str(n).strip()]
    config["stopwords"] = [str(n) for n in (config.get("stopwords") or [])]
    return config


def save(config: dict) -> dict:
    merged = dict(DEFAULT)
    merged.update({k: v for k, v in config.items() if k in DEFAULT})
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def matches(name: str, config: dict | None = None) -> bool:
    """名字是否命中规则列表（关键字包含即可）。"""
    config = config or load()
    name = (name or "").strip()
    if not name:
        return False
    return any(keyword in name or name in keyword for keyword in config["names"])


def decide(name: str, config: dict | None = None) -> bool:
    """根据模式返回是否接受。"""
    config = config or load()
    hit = matches(name, config)
    return hit if config["mode"] == "whitelist" else not hit
