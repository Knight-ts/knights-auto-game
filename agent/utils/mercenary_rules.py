# -*- coding: utf-8 -*-
"""佣兵智能接受规则：名字 + 爵位 + 橙色特性 + 好娃组合 + 属性门槛，可组合判断。

table/mercenary_rules.json：

    mode          名字规则：whitelist（名字命中才接受）/ blacklist（名字命中则拒绝）
    names         名字关键字列表（包含即命中，"卡尔" 不会被拆成单字）；白名单留空 = 不按名字卡
    match         多条件组合：all（启用的条件全部满足）/ any（任一满足）
    title_min     爵位下限：无 / 骑士 / 男爵 / 伯爵 / 公爵；"无" 表示不看爵位
    min_orange    橙色血统特性数量下限；0 表示不看（需要读角色详情面板）
    combos        「好娃提醒」组合名/ID 列表，命中任一个就算通过（特性 + 属性门槛）
    features      额外必需特性（全部命中）
    potential_min 额外属性门槛 {"技巧": "SS", "力量": "S"}
    read_detail   是否读详情：auto（有上述条件时才点）/ always / never
    name_roi / name_regex / stopwords: 弹窗名字 OCR 的区域与过滤词

    name_roi 只在弹窗锚点（“希望加入佣兵团”“年龄”）没识别到时兜底，
    正常读名靠锚点之间那一行，不要再把区域往上方放大，否则会读到节日提示。

判定原则：
    - 名字规则默认参与，白名单留空时不参与（否则配了组合也会被名字一票否决）；
    - 其余条件配了才参与（"无" / 0 / 空 = 关闭）；
    - 某个条件的数据没读到时（面板没打开），该条件按“未启用”跳过，
      并在记录备注里写明，避免因为读不到就把人全拒了。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "table" / "mercenary_rules.json"

TITLE_RANK = {"无": 0, "骑士": 1, "男爵": 2, "伯爵": 3, "公爵": 4}
# 机器人起名规则会把爵位首字写在名字末尾（公 / 伯 / 男 / 骑）
TITLE_ALIASES = {
    "公爵": "公爵",
    "公": "公爵",
    "伯爵": "伯爵",
    "伯": "伯爵",
    "男爵": "男爵",
    "男": "男爵",
    "骑士": "骑士",
    "骑": "骑士",
}
TITLE_WORDS = ("公爵", "伯爵", "男爵", "骑士")

DEFAULT_NAME_REGEX = "[一-龥A-Za-z·0-9]{1,8}"

# 和 utils/mercenary_admit.py 保持一致（本模块会被网页版单独加载，不 import utils.*）
ATTRIBUTE_ORDER = ("力量", "体质", "技巧", "感知", "敏捷", "意志")
ATTRIBUTE_RANK = {"SS": 7, "S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "E": 1}

DEFAULT = {
    "mode": "whitelist",
    "names": [],
    "match": "all",
    "title_min": "无",
    "min_orange": 0,
    "combos": [],
    "features": [],
    "potential_min": {},
    "read_detail": "auto",
    "name_roi": [40, 290, 640, 500],
    "name_regex": DEFAULT_NAME_REGEX,
    "stopwords": ["佣兵", "加入", "确定", "取消", "招募", "队伍", "想要", "你的", "成为", "伙伴"],
}


def _normalize_names(value) -> list[str]:
    """把字符串或列表统一成关键字列表，避免 "卡尔" 被拆成单字。"""
    if isinstance(value, str):
        parts = re.split(r"[,，;；、\n\r]+", value)
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        return []
    return [str(part).strip() for part in parts if str(part).strip()]


def _clean_int(value, default: int = 0) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _normalize_potential(value) -> dict:
    """属性门槛：{"技巧": "SS"}，等级写错的丢掉。"""
    if not isinstance(value, dict):
        return {}
    result = {}
    for attr, grade in value.items():
        attr = str(attr).strip()
        grade = str(grade).strip().upper()
        if attr in ATTRIBUTE_ORDER and grade in ATTRIBUTE_RANK:
            result[attr] = grade
    return result


def _normalize(config: dict) -> dict:
    config["mode"] = config.get("mode") if config.get("mode") in ("whitelist", "blacklist") else "whitelist"
    config["match"] = config.get("match") if config.get("match") in ("all", "any") else "all"
    title_min = str(config.get("title_min") or "无").strip()
    config["title_min"] = title_min if title_min in TITLE_RANK else "无"
    config["min_orange"] = _clean_int(config.get("min_orange"), 0)
    read_detail = str(config.get("read_detail") or "auto").strip()
    config["read_detail"] = read_detail if read_detail in ("auto", "always", "never") else "auto"
    config["names"] = _normalize_names(config.get("names"))
    config["combos"] = _normalize_names(config.get("combos"))
    config["features"] = _normalize_names(config.get("features"))
    config["potential_min"] = _normalize_potential(config.get("potential_min"))
    config["stopwords"] = [str(n) for n in (config.get("stopwords") or [])]
    return config


def load() -> dict:
    config = dict(DEFAULT)
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _normalize(config)
    for key in DEFAULT:
        if key in data:
            config[key] = data[key]
    return _normalize(config)


def save(config: dict) -> dict:
    merged = dict(DEFAULT)
    merged.update({k: v for k, v in config.items() if k in DEFAULT})
    merged = _normalize(merged)
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


def title_rank(title: str) -> int:
    return TITLE_RANK.get(title or "无", 0)


def parse_title(name: str, extra_text: str = "") -> str | None:
    """从名字 / 界面文字里认爵位。

    游戏里贵族的显示名会带上称号（如“希利赋女骑士”“慧根女公爵”），机器人起名则把
    爵位首字放在名字末尾（公 / 伯 / 男 / 骑）。都没有时返回 "无"，两处都没文字时返回 None。
    """
    sources = [text for text in ((name or "").strip(), (extra_text or "").strip()) if text]
    if not sources:
        return None
    for text in sources:
        for word in TITLE_WORDS:
            if word in text:
                return word
    last = sources[0][-1]
    if last in TITLE_ALIASES:
        return TITLE_ALIASES[last]
    return "无"


def describe(config: dict | None = None) -> str:
    """给记录用的规则摘要，例如 any(whitelist/橙≥2/公爵+/组合)。"""
    config = config or load()
    parts = [config["mode"]]
    if config.get("min_orange", 0) > 0:
        parts.append(f"橙≥{config['min_orange']}")
    if config.get("title_min", "无") != "无":
        parts.append(f"{config['title_min']}+")
    if config.get("combos"):
        parts.append(f"组合×{len(config['combos'])}")
    if config.get("features"):
        parts.append(f"特性×{len(config['features'])}")
    if config.get("potential_min"):
        parts.append(f"属性×{len(config['potential_min'])}")
    if len(parts) == 1:
        return parts[0]
    joined = "/".join(parts)
    return f"any({joined})" if config.get("match") == "any" else joined


def evaluate(profile: dict, config: dict | None = None) -> dict:
    """按配置判断是否接受。

    profile:
        name      str            佣兵名字
        title     str|None       爵位（None = 没看到，条件跳过）
        orange    int|None       橙色血统特性数量（None = 没读面板）
        detail_ok bool           详情面板是否读到（特性/组合/属性门槛依赖它）
        features  list[str]      面板里识别到的特性名
        potential dict           潜力数值 {"技巧": 0.95, ...}

    返回 {"accept": bool, "rule": str, "checks": [{"label","state","detail"}], "note": str}
        state: pass / fail / skip
    """
    from utils.mercenary_admit import (
        check_feature_list,
        check_potential_min,
        match_combos,
    )

    config = _normalize(dict(config or load()))
    name = (profile.get("name") or "").strip()
    title = profile.get("title")
    orange = profile.get("orange")
    detail_ok = bool(profile.get("detail_ok"))
    features = [str(item) for item in (profile.get("features") or [])]
    potential = profile.get("potential") or {}

    checks: list[dict] = []

    hit = matches(name, config)
    name_ok = hit if config["mode"] == "whitelist" else not hit
    if config["mode"] == "whitelist" and not config["names"]:
        # 白名单留空 = 不按名字卡（否则配了组合也会被名字一票否决）
        checks.append({"label": "名字", "state": "skip", "detail": "白名单为空，名字条件跳过"})
    elif config["mode"] == "whitelist":
        detail = f"命中白名单 {name}" if hit else f"未命中白名单（{len(config['names'])} 个关键字）"
        checks.append({"label": "名字", "state": "pass" if name_ok else "fail", "detail": detail})
    else:
        detail = f"命中黑名单 {name}" if hit else "未命中黑名单"
        checks.append({"label": "名字", "state": "pass" if name_ok else "fail", "detail": detail})

    title_min = config.get("title_min", "无")
    if title_min != "无":
        if title is None:
            checks.append({"label": "爵位", "state": "skip", "detail": "没识别到爵位，条件跳过"})
        else:
            ok = title_rank(title) >= TITLE_RANK.get(title_min, 0)
            checks.append(
                {
                    "label": "爵位",
                    "state": "pass" if ok else "fail",
                    "detail": f"{title}（要求 {title_min} 及以上）",
                }
            )

    min_orange = config.get("min_orange", 0)
    if min_orange > 0:
        if orange is None:
            checks.append({"label": "特性", "state": "skip", "detail": "没读到特性面板，条件跳过"})
        else:
            ok = orange >= min_orange
            checks.append(
                {
                    "label": "特性",
                    "state": "pass" if ok else "fail",
                    "detail": f"橙色特性 {orange} 个（要求 ≥{min_orange}）",
                }
            )

    if config.get("combos"):
        if not detail_ok:
            checks.append({"label": "组合", "state": "skip", "detail": "没读到详情面板，条件跳过"})
        else:
            ok, combo_name, combo_detail = match_combos(config["combos"], potential, features)
            checks.append(
                {
                    "label": "组合",
                    "state": "pass" if ok else "fail",
                    "detail": f"{combo_name}（{combo_detail}）" if ok else combo_detail,
                }
            )

    if config.get("features"):
        if not detail_ok:
            checks.append({"label": "必需特性", "state": "skip", "detail": "没读到详情面板，条件跳过"})
        else:
            ok, detail = check_feature_list(features, config["features"])
            checks.append(
                {"label": "必需特性", "state": "pass" if ok else "fail", "detail": detail}
            )

    if config.get("potential_min"):
        if not detail_ok:
            checks.append({"label": "属性门槛", "state": "skip", "detail": "没读到详情面板，条件跳过"})
        else:
            ok, detail = check_potential_min(potential, config["potential_min"])
            checks.append(
                {"label": "属性门槛", "state": "pass" if ok else "fail", "detail": detail}
            )

    active = [check for check in checks if check["state"] != "skip"]
    if not active:
        accept = False
    elif config.get("match") == "any":
        accept = any(check["state"] == "pass" for check in active)
    else:
        accept = all(check["state"] == "pass" for check in active)

    parts = []
    for check in checks:
        mark = {"pass": "✓", "fail": "✗", "skip": "-"}[check["state"]]
        parts.append(f"{mark}{check['label']}:{check['detail']}")
    return {
        "accept": accept,
        "rule": describe(config),
        "checks": checks,
        "note": "；".join(parts),
    }


def decide(name: str, config: dict | None = None) -> bool:
    """只看名字的旧接口（保留给外部调用）。"""
    return evaluate({"name": name}, config)["accept"]
