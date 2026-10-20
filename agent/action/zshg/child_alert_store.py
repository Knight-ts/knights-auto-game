# -*- coding: utf-8 -*-
"""好娃提醒类型：读写、校验与三处同步。

三处指：
    1. table/child_alert_conditions.json   —— 判定条件（真正的规则）
    2. resource/base/pipeline/child_info.json —— 检测_* 开关节点
    3. interface.json 的 option["开启好娃提醒"] —— 界面勾选项（cases / default_case）

本模块不依赖 maa，便于单独测试。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import time
from pathlib import Path

ATTRS = ["力量", "体质", "技巧", "感知", "敏捷", "意志"]
GRADES = ["", "E", "D", "C", "B", "A", "S", "SS"]
ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")
OPTION_NAME = "开启好娃提醒"

TABLE_REL = Path("table") / "child_alert_conditions.json"
PIPELINE_REL = Path("resource") / "base" / "pipeline" / "child_info.json"
INTERFACE_REL = Path("interface.json")

_FEATURE_TABLE_LOADER = None


def _feature_table(root: Path) -> dict:
    """读取按种族分类的完整特性表（见 child_feature_table.py）。

    本文件既会被当作包内模块导入（action.zshg.child_alert_store），
    也会被 webapp/server.py 以单文件方式加载，所以这里做一次兜底导入。
    """
    global _FEATURE_TABLE_LOADER
    if _FEATURE_TABLE_LOADER is None:
        try:
            from .child_feature_table import load_feature_table as loader
        except ImportError:
            spec = importlib.util.spec_from_file_location(
                "child_feature_table", Path(__file__).with_name("child_feature_table.py")
            )
            if spec is None or spec.loader is None:
                raise
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loader = module.load_feature_table
        _FEATURE_TABLE_LOADER = loader
    return _FEATURE_TABLE_LOADER(root)


def project_root() -> Path:
    """从本文件位置推断项目根目录。"""
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- 基础读写


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _detect_indent(path: Path) -> int:
    """沿用原文件的缩进宽度，避免把 700 行 interface.json 整体重排。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for _ in range(20):
                line = handle.readline()
                if not line:
                    break
                stripped = line.lstrip(" ")
                if stripped and stripped != line:
                    return max(1, len(line) - len(stripped))
    except Exception:  # noqa: BLE001
        pass
    return 2


def _write_json(path: Path, data) -> None:
    """先备份 .bak，再原子替换；缩进沿用原文件。"""
    indent = _detect_indent(path) if path.exists() else 2
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent)
        handle.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 潜力转换


def split_potential(potential: dict) -> tuple[dict, dict, list[str]]:
    """把 potential 拆成 UI 友好的 grades / mins / 或条件行。"""
    grades = {attr: "" for attr in ATTRS}
    mins = {attr: "" for attr in ATTRS}
    or_lines: list[str] = []

    for key, requirement in (potential or {}).items():
        if key == "or_attributes" or not isinstance(requirement, dict):
            continue
        if key in grades:
            grades[key] = requirement.get("grade") or ""
            value = requirement.get("min_value")
            mins[key] = "" if value is None else value

    for group in (potential or {}).get("or_attributes", []) or []:
        parts = []
        for attr, requirement in group.items():
            value = requirement.get("min_value")
            parts.append(attr if value is None else f"{attr}>={value}")
        if parts:
            or_lines.append(" 且 ".join(parts))

    return grades, mins, or_lines


def build_potential(grades: dict, mins: dict, or_lines: list[str]) -> dict:
    """把 UI 数据还原成 potential；or_lines 格式「属性>=数值」，多条之间是“或”。"""
    potential: dict = {}
    for attr in ATTRS:
        grade = (grades.get(attr) or "").strip().upper()
        raw_min = mins.get(attr, "")
        requirement: dict = {}
        if grade:
            requirement["grade"] = grade
        if raw_min not in ("", None):
            requirement["min_value"] = float(raw_min)
        if requirement:
            potential[attr] = requirement

    groups = []
    for line in or_lines:
        line = (line or "").strip()
        if not line:
            continue
        group = {}
        for part in re.split(r"\s*(?:且|and|&&)\s*", line):
            match = re.match(r"^([\u4e00-\u9fff]{2})\s*(?:>=|=|≥)\s*([0-9]*\.?[0-9]+)$", part)
            if not match:
                raise ValueError(f"或条件格式不对：{part!r}，应形如 敏捷>=0.3")
            attr, value = match.groups()
            if attr not in ATTRS:
                raise ValueError(f"或条件里的属性不认识：{attr}")
            group[attr] = {"min_value": float(value)}
        groups.append(group)

    if groups:
        potential["or_attributes"] = groups
    return potential


# ---------------------------------------------------------------- 特性候选


def load_feature_catalog(root: Path) -> list[str]:
    """全部特性名（去重，短名在前，输入时优先匹配短名）。"""
    try:
        return list(_feature_table(root).get("names") or [])
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 读取状态


def load_state(root: Path | None = None) -> dict:
    root = root or project_root()
    table_path = root / TABLE_REL
    pipeline_path = root / PIPELINE_REL
    interface_path = root / INTERFACE_REL

    table = _read_json(table_path)
    pipeline = _read_json(pipeline_path)
    interface = _read_json(interface_path)
    option = interface["option"][OPTION_NAME]
    cases = option.get("cases", [])
    default_case = option.get("default_case", []) or []

    case_by_node: dict[str, dict] = {}
    for case in cases:
        for node in (case.get("pipeline_override") or {}):
            case_by_node.setdefault(node, case)

    conditions = []
    for condition in table.get("conditions", []):
        node = condition.get("detection_node", "")
        case = case_by_node.get(node)
        case_name = condition.get("case_name") or (case.get("name") if case else condition.get("name", ""))
        grades, mins, or_lines = split_potential(condition.get("potential", {}))
        conditions.append(
            {
                "id": condition.get("id", ""),
                "name": condition.get("name", ""),
                "description": condition.get("description", ""),
                "note": condition.get("note", ""),
                "node": node,
                "caseName": case_name,
                "defaultChecked": case_name in default_case,
                "features": list(condition.get("features", []) or []),
                "grades": grades,
                "mins": mins,
                "orLines": or_lines,
                "nodeExists": node in pipeline,
                "caseExists": case is not None,
            }
        )

    feature_table = _feature_table(root)
    return {
        "root": str(root),
        "conditions": conditions,
        "catalog": list(feature_table.get("names") or []),
        "featureGroups": feature_table.get("groups") or [],
        "featureRaces": feature_table.get("featureRaces") or {},
        "featureCounts": feature_table.get("counts") or {},
        "optionName": OPTION_NAME,
    }


# ---------------------------------------------------------------- 校验


def normalize(condition: dict) -> dict:
    """把提交上来的 UI 数据整理成内部结构，同时触发格式校验。"""
    potential = build_potential(
        condition.get("grades", {}),
        condition.get("mins", {}),
        condition.get("orLines", []) or [],
    )
    features = []
    for feature in condition.get("features", []) or []:
        feature = (feature or "").strip()
        if feature and feature not in features:
            features.append(feature)

    return {
        "id": (condition.get("id") or "").strip(),
        "name": (condition.get("name") or "").strip(),
        "description": (condition.get("description") or "").strip(),
        "note": (condition.get("note") or "").strip(),
        "node": (condition.get("node") or "").strip(),
        "caseName": (condition.get("caseName") or "").strip(),
        "defaultChecked": bool(condition.get("defaultChecked")),
        "features": features,
        "potential": potential,
    }


def validate(conditions: list[dict]) -> list[str]:
    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    seen_nodes: dict[str, str] = {}
    seen_cases: dict[str, str] = {}

    if not conditions:
        return ["至少要有一个提醒类型"]

    for index, raw in enumerate(conditions, 1):
        try:
            condition = normalize(raw)
        except ValueError as exc:
            errors.append(f"第 {index} 条：{exc}")
            continue

        label = condition["name"] or f"第 {index} 条"

        if not ID_RE.match(condition["id"]):
            errors.append(f"{label}：内部 ID 只能用小写字母/数字/下划线，2-32 位（当前 {condition['id']!r}）")
        elif condition["id"] in seen_ids:
            errors.append(f"内部 ID 重复：{condition['id']}（{seen_ids[condition['id']]} 与 {label}）")
        else:
            seen_ids[condition["id"]] = label

        if not condition["name"]:
            errors.append(f"第 {index} 条：名称不能为空")
        if not condition["caseName"]:
            errors.append(f"{label}：界面显示名不能为空")
        elif condition["caseName"] in seen_cases:
            errors.append(f"界面显示名重复：{condition['caseName']}")
        else:
            seen_cases[condition["caseName"]] = label

        if not condition["node"]:
            errors.append(f"{label}：检测节点名不能为空")
        elif not condition["node"].startswith("检测_"):
            errors.append(f"{label}：检测节点建议以「检测_」开头（当前 {condition['node']}）")
        elif condition["node"] in seen_nodes:
            errors.append(f"检测节点重复：{condition['node']}")
        else:
            seen_nodes[condition["node"]] = label

        for attr, requirement in condition["potential"].items():
            if attr == "or_attributes":
                continue
            grade = requirement.get("grade")
            if grade and grade not in GRADES:
                errors.append(f"{label}：{attr} 的等级 {grade} 不合法")
            value = requirement.get("min_value")
            if value is not None and not (0 <= value <= 5):
                errors.append(f"{label}：{attr} 的最小值 {value} 超出范围")

        if not condition["features"] and not condition["potential"]:
            errors.append(f"{label}：至少要有「必需特性」或「属性门槛」之一，否则会无条件提醒")

    return errors


# ---------------------------------------------------------------- 保存同步


def save_state(root: Path | None, raw_conditions: list[dict]) -> dict:
    root = root or project_root()
    errors = validate(raw_conditions)
    if errors:
        return {"ok": False, "errors": errors}

    conditions = [normalize(item) for item in raw_conditions]

    table_path = root / TABLE_REL
    pipeline_path = root / PIPELINE_REL
    interface_path = root / INTERFACE_REL

    old_table = _read_json(table_path)
    old_nodes = {
        item.get("detection_node")
        for item in old_table.get("conditions", [])
        if item.get("detection_node")
    }

    pipeline = _read_json(pipeline_path)
    interface = _read_json(interface_path)
    option = interface["option"][OPTION_NAME]
    old_cases = option.get("cases", [])
    old_case_by_node = {}
    for case in old_cases:
        for node in (case.get("pipeline_override") or {}):
            old_case_by_node.setdefault(node, case)

    messages: list[str] = []
    new_nodes = {condition["node"] for condition in conditions}
    removed_nodes = old_nodes - new_nodes

    # 1. 条件表
    table_conditions = []
    for condition in conditions:
        entry = {
            "id": condition["id"],
            "name": condition["name"],
            "description": condition["description"],
            "enabled": True,
            "detection_node": condition["node"],
            "case_name": condition["caseName"],
            "default_checked": condition["defaultChecked"],
        }
        if condition["note"]:
            entry["note"] = condition["note"]
        if condition["features"]:
            entry["features"] = condition["features"]
        if condition["potential"]:
            entry["potential"] = condition["potential"]
        table_conditions.append(entry)

    table_changed = old_table.get("conditions") != table_conditions
    if table_changed:
        _write_json(table_path, {"conditions": table_conditions})
        messages.append(f"条件表已更新（{len(table_conditions)} 条）")

    # 2. 流水线开关节点
    pipeline_changed = False
    for condition in conditions:
        node = condition["node"]
        expected = [condition["id"]]
        current = pipeline.get(node)
        if current is None:
            pipeline[node] = {
                "recognition": "OCR",
                "expected": expected,
                "enabled": False,
            }
            pipeline_changed = True
            messages.append(f"新增检测节点 {node}")
        else:
            if current.get("expected") != expected:
                current["expected"] = expected
                pipeline_changed = True
                messages.append(f"检测节点 {node} 的 expected 已同步为 {condition['id']}")
            if current.get("enabled") is not False:
                current["enabled"] = False
                pipeline_changed = True

    for node in sorted(removed_nodes):
        if node in pipeline:
            del pipeline[node]
            pipeline_changed = True
            messages.append(f"删除检测节点 {node}")

    if pipeline_changed:
        _write_json(pipeline_path, pipeline)

    # 3. 界面 cases / default_case
    unrelated_cases = [
        case
        for case in old_cases
        if not (set((case.get("pipeline_override") or {})) & (old_nodes | new_nodes))
    ]
    new_cases = list(unrelated_cases)
    for condition in conditions:
        old_case = old_case_by_node.get(condition["node"])
        if old_case and old_case.get("name") and old_case["name"] != condition["caseName"]:
            messages.append(
                f"界面项改名：{old_case['name']} → {condition['caseName']}（老实例需要重新勾选）"
            )
        new_cases.append(
            {
                "name": condition["caseName"],
                "description": condition["description"] or f"启用「{condition['name']}」提醒",
                "pipeline_override": {condition["node"]: {"enabled": True}},
            }
        )

    default_case = [condition["caseName"] for condition in conditions if condition["defaultChecked"]]
    for case in unrelated_cases:
        if case.get("name") in (option.get("default_case") or []):
            default_case.append(case["name"])

    cases_changed = (
        [case.get("name") for case in old_cases] != [case.get("name") for case in new_cases]
        or old_cases != new_cases
        or (option.get("default_case") or []) != default_case
    )
    if cases_changed:
        option["cases"] = new_cases
        option["default_case"] = default_case
        _write_json(interface_path, interface)
        messages.append(f"界面选项已更新（{len(new_cases)} 个类型，默认勾选 {len(default_case)} 个）")

    if not messages:
        messages.append("没有需要写入的改动")

    return {"ok": True, "messages": messages, "conditions": load_state(root)["conditions"]}


def sync_instance_selection(root: Path | None, case_names: list[str]) -> list[str]:
    """把勾选结果写入各实例配置的 selected_cases。

    Knights Auto Game 的「开启好娃提醒」勾选是按实例保存在 config/instances/*.json 里的，
    只改 interface.json 的 default_case 对已有实例无效，所以这里直接改实例配置。
    """
    root = root or project_root()
    names = [name for name in case_names if name]
    changed_files: list[str] = []

    for path in sorted((root / "config" / "instances").glob("*.json")):
        try:
            data = _read_json(path)
        except Exception:  # noqa: BLE001
            continue

        changed = False
        for task in data.get("TaskItems", []) or []:
            for option in task.get("option", []) or []:
                if option.get("name") == OPTION_NAME and option.get("selected_cases") != names:
                    option["selected_cases"] = list(names)
                    changed = True

        if changed:
            _write_json(path, data)
            changed_files.append(path.name)

    return changed_files
