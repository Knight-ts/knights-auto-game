# -*- coding: utf-8 -*-
"""战斗记忆 / 攻击策略 / 自动学习。

目标：
1. 每个任务都记录战斗数据：我方与敌方位置、格子占领、棋子攻击决策、
   花费回合、胜负、使用的策略；
2. 攻击逻辑可由玩家修改：``table/battle_policies.json`` 里是权重式策略
   （目标评分、移动模式），网页「战斗记忆」面板可直接编辑；
3. 自动学习：按任务/任务类型统计胜率与回合数，推荐最优策略；开局位置
   做热力图，供后续策略参考；
4. 选任务时可按「预计回合数」排序，优先接打得更快的任务。

数据文件（项目根 ``table/`` 下）：

- ``battle_memory.json``   每任务的历史战斗、统计、开局位置、推荐策略
- ``battle_policies.json`` 玩家可编辑的策略与任务分配

设计原则：只做统计学习，不训练模型；所有判断都能在网页上查到依据。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # Agent 用统一 logger；网页版单独加载时退回标准 logging
    from utils import logger
except Exception:  # noqa: BLE001
    import logging

    logger = logging.getLogger("battle_memory")

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(_MODULE_DIR))
TABLE_DIR = os.path.join(ROOT_DIR, "table")

MEMORY_NAME = "battle_memory.json"
POLICY_NAME = "battle_policies.json"

MAX_RUNS_PER_TASK = 30
MAX_DECISIONS_PER_RUN = 80
MAX_OPENING_CELLS = 240
MAX_MAP_CELLS = 40
SCREEN_WIDTH = 720
SCREEN_HEIGHT = 1280
CELL_SIZE = 120
LOSS_PENALTY_ROUNDS = 40.0
UNKNOWN_ROUNDS = 18.0
EXPLORE_FACTOR = 1.15

# 八方向命名：row 增大 = 南，col 增大 = 东。
DIRECTION_DELTAS = {
    "N": (-1, 0),
    "NE": (-1, 1),
    "E": (0, 1),
    "SE": (1, 1),
    "S": (1, 0),
    "SW": (1, -1),
    "W": (0, -1),
    "NW": (-1, -1),
}
DIRECTION_LABELS = {
    "N": "北",
    "NE": "东北",
    "E": "东",
    "SE": "东南",
    "S": "南",
    "SW": "西南",
    "W": "西",
    "NW": "西北",
}


def direction_code(row_delta: int, col_delta: int) -> Optional[str]:
    if not row_delta and not col_delta:
        return None
    if abs(row_delta) >= 2 * abs(col_delta):
        return "S" if row_delta > 0 else "N"
    if abs(col_delta) >= 2 * abs(row_delta):
        return "E" if col_delta > 0 else "W"
    return ("S" if row_delta > 0 else "N") + (
        "E" if col_delta > 0 else "W"
    )


# ------------------------------------------------------------------ 默认策略


def default_policies() -> Dict[str, Any]:
    return {
        "version": 1,
        "active": "balanced",
        "policies": {
            "balanced": {
                "id": "balanced",
                "name": "均衡",
                "description": "优先近处目标，其次残血；正常 A* 接近",
                "target": {
                    "weights": {
                        "distance": -1.0,
                        "hp_ratio": -1.5,
                        "threat": 0.4,
                    }
                },
                "move": {"mode": "approach", "weights": {}},
                "flags": {"attack_occluded": True},
            },
            "focus_weak": {
                "id": "focus_weak",
                "name": "集火残血",
                "description": "优先打血量最低的敌人，距离次之",
                "target": {
                    "weights": {
                        "distance": -0.4,
                        "hp_ratio": -3.0,
                        "threat": 0.2,
                    }
                },
                "move": {"mode": "approach", "weights": {}},
                "flags": {"attack_occluded": True},
            },
            "defensive": {
                "id": "defensive",
                "name": "稳守反打",
                "description": "移动时避开敌人贴身格，优先保持距离",
                "target": {
                    "weights": {
                        "distance": -1.2,
                        "hp_ratio": -1.0,
                        "threat": 0.8,
                    }
                },
                "move": {"mode": "defensive", "weights": {"danger": -1.2}},
                "flags": {"attack_occluded": True},
            },
            "hold": {
                "id": "hold",
                "name": "原地待机",
                "description": "有敌人时不主动移动，只打进入攻击范围的目标",
                "target": {
                    "weights": {
                        "distance": -1.0,
                        "hp_ratio": -1.0,
                        "threat": 0.4,
                    }
                },
                "move": {"mode": "hold", "weights": {}},
                "flags": {"attack_occluded": True},
            },
        },
        "tasks": {},
        "settings": {"explore_every": 5},
    }


# ------------------------------------------------------------------ 工具


def _atomic_write(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    if os.path.exists(path):
        try:
            os.replace(path, f"{path}.bak")
        except OSError:
            pass
    os.replace(tmp, path)


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取 {path} 失败：{exc}")
        return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def task_key(city: str, task: str) -> str:
    return f"{(city or '未知城市').strip()}/{(task or '未知任务').strip()}"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _cell_list(cells: Sequence[Any]) -> List[List[int]]:
    result = []
    for cell in cells or []:
        if isinstance(cell, (list, tuple)) and len(cell) == 2:
            result.append([_to_int(cell[0]), _to_int(cell[1])])
    return result


# ------------------------------------------------------------------ 策略


class BattlePolicy:
    """一份可执行的攻击策略（权重评分）。"""

    VALID_TARGET_FEATURES = ("distance", "hp_ratio", "threat", "level", "is_environment")
    VALID_MOVE_MODES = ("approach", "defensive", "hold", "explore")

    def __init__(self, data: Optional[dict] = None) -> None:
        data = data if isinstance(data, dict) else {}
        self.id = str(data.get("id") or data.get("name") or "balanced")
        self.name = str(data.get("name") or self.id)
        self.description = str(data.get("description") or "")
        target = data.get("target") if isinstance(data.get("target"), dict) else {}
        weights = target.get("weights") if isinstance(target.get("weights"), dict) else {}
        self.target_weights: Dict[str, float] = {
            key: _to_float(value)
            for key, value in weights.items()
            if key in self.VALID_TARGET_FEATURES
        }
        move = data.get("move") if isinstance(data.get("move"), dict) else {}
        mode = str(move.get("mode") or "approach")
        self.move_mode = mode if mode in self.VALID_MOVE_MODES else "approach"
        self.move_weights = (
            move.get("weights") if isinstance(move.get("weights"), dict) else {}
        )
        flags = data.get("flags") if isinstance(data.get("flags"), dict) else {}
        self.attack_occluded = bool(flags.get("attack_occluded", True))

    def score_target(self, features: Dict[str, float]) -> float:
        score = 0.0
        for key, weight in self.target_weights.items():
            score += weight * _to_float(features.get(key), 0.0)
        return score

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "target": {"weights": dict(self.target_weights)},
            "move": {"mode": self.move_mode, "weights": dict(self.move_weights)},
            "flags": {"attack_occluded": self.attack_occluded},
        }


# ------------------------------------------------------------------ 记忆


class BattleMemory:
    """战斗历史、统计与策略分配（线程安全，文件热加载）。"""

    def __init__(self, table_dir: Optional[str] = None) -> None:
        self.table_dir = table_dir or TABLE_DIR
        self.memory_path = os.path.join(self.table_dir, MEMORY_NAME)
        self.policy_path = os.path.join(self.table_dir, POLICY_NAME)
        self._lock = threading.RLock()
        self._memory: dict = {}
        self._policies: dict = {}
        self._memory_mtime = -1.0
        self._policy_mtime = -1.0
        self._trace: Optional[dict] = None
        self.reload(force=True)

    # ---------------------------------------------------------- 文件

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return -1.0

    def reload(self, force: bool = False) -> None:
        memory_mtime = self._mtime(self.memory_path)
        policy_mtime = self._mtime(self.policy_path)
        if force or memory_mtime != self._memory_mtime:
            data = _read_json(self.memory_path)
            self._memory = data if isinstance(data, dict) else {}
            self._memory.setdefault("version", 1)
            self._memory.setdefault("tasks", {})
            self._memory.setdefault("types", {})
            self._memory_mtime = memory_mtime
        if force or policy_mtime != self._policy_mtime:
            data = _read_json(self.policy_path)
            if not isinstance(data, dict) or not data.get("policies"):
                data = default_policies()
            self._policies = data
            self._policy_mtime = policy_mtime

    def _save_memory(self) -> None:
        self._memory["updated"] = _now()
        _atomic_write(self.memory_path, self._memory)
        self._memory_mtime = self._mtime(self.memory_path)

    def _save_policies(self) -> None:
        _atomic_write(self.policy_path, self._policies)
        self._policy_mtime = self._mtime(self.policy_path)

    # ---------------------------------------------------------- 策略读取

    def policies(self) -> dict:
        self.reload()
        return copy.deepcopy(self._policies)

    def policy_names(self) -> List[str]:
        self.reload()
        return list((self._policies.get("policies") or {}).keys())

    def active_policy_name(self) -> str:
        self.reload()
        name = str(self._policies.get("active") or "balanced")
        if name not in (self._policies.get("policies") or {}):
            name = "balanced"
        return name

    def policy_for(self, city: str, task: str) -> BattlePolicy:
        """任务显式分配 > 自动学习推荐 > 全局 active > balanced。

        每 ``settings.explore_every`` 场还会挑一个「测得最少」的策略做探索，
        避免推荐策略固定后其它策略永远没有样本。
        """
        self.reload()
        policies = self._policies.get("policies") or {}
        key = task_key(city, task)
        assigned = (self._policies.get("tasks") or {}).get(key)
        entry = (self._memory.get("tasks") or {}).get(key) or {}
        recommended = entry.get("recommended")
        if assigned in policies:
            name = assigned
        elif recommended in policies:
            name = recommended
            explore = self._exploration_policy(entry, policies)
            if explore:
                name = explore
        else:
            name = self.active_policy_name()
        data = policies.get(name) or policies.get("balanced") or {}
        if isinstance(data, dict) and "id" not in data:
            data = dict(data, id=name)
        return BattlePolicy(data)

    def _exploration_policy(self, entry: dict, policies: dict) -> str:
        """按场次轮换一个采样最少的策略；0 表示不探索。"""
        settings = self._policies.get("settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        every = _to_int(settings.get("explore_every"), 5)
        if every <= 0:
            return ""
        stats = entry.get("stats") or {}
        runs = _to_int(stats.get("runs"))
        if runs <= 0 or runs % every != 0:
            return ""
        per_policy = stats.get("policies") or {}
        recommended = entry.get("recommended")
        candidates = [
            (name, _to_int((per_policy.get(name) or {}).get("runs")))
            for name in policies
            if name != recommended
        ]
        if not candidates:
            return ""
        return min(candidates, key=lambda item: (item[1], item[0]))[0]

    def policy_by_name(self, name: str) -> Optional[BattlePolicy]:
        """按 id 取策略；不存在返回 None（调用方回退）。"""
        self.reload()
        policies = self._policies.get("policies") or {}
        data = policies.get(name)
        if not isinstance(data, dict):
            return None
        if "id" not in data:
            data = dict(data, id=name)
        return BattlePolicy(data)

    def save_policy(self, name: str, data: dict) -> dict:
        with self._lock:
            self.reload(force=True)
            name = (name or "").strip() or "custom"
            payload = dict(data or {})
            payload["id"] = name
            payload.setdefault("name", name)
            policies = self._policies.setdefault("policies", {})
            policies[name] = payload
            self._policies.setdefault("active", name)
            self._save_policies()
            return copy.deepcopy(payload)

    def set_active(self, name: str) -> None:
        with self._lock:
            self.reload(force=True)
            if name in (self._policies.get("policies") or {}):
                self._policies["active"] = name
                self._save_policies()

    def assign(self, city: str, task: str, policy: str) -> None:
        with self._lock:
            self.reload(force=True)
            tasks = self._policies.setdefault("tasks", {})
            key = task_key(city, task)
            if policy:
                tasks[key] = policy
            else:
                tasks.pop(key, None)
            self._save_policies()

    def assign_key(self, key: str, policy: str) -> None:
        """按 ``城市/任务`` 键分配策略（网页行内下拉用）。"""
        key = (key or "").strip()
        if "/" not in key:
            return
        city, task = key.split("/", 1)
        self.assign(city, task, policy)

    def delete_policy(self, name: str) -> bool:
        with self._lock:
            self.reload(force=True)
            policies = self._policies.get("policies") or {}
            if name not in policies or name == "balanced":
                return False
            policies.pop(name, None)
            for key, value in list((self._policies.get("tasks") or {}).items()):
                if value == name:
                    self._policies["tasks"].pop(key, None)
            if self._policies.get("active") == name:
                self._policies["active"] = "balanced"
            self._save_policies()
            return True

    # ---------------------------------------------------------- 战斗轨迹

    def has_trace(self) -> bool:
        with self._lock:
            return self._trace is not None

    def has_snapshot(self) -> bool:
        with self._lock:
            return bool(self._trace and _to_int(self._trace.get("snapshots")) > 0)

    def start_trace(
        self,
        city: str,
        task: str,
        task_type: str = "",
        level: str = "",
        policy: Optional[BattlePolicy] = None,
    ) -> None:
        with self._lock:
            policy = policy or self.policy_for(city, task)
            self._trace = {
                "city": (city or "").strip(),
                "task": (task or "").strip(),
                "type": (task_type or "").strip(),
                "level": (level or "").strip(),
                "policy": policy.id,
                "started": _now(),
                "initial": None,
                "initial_world": None,
                "initial_relation": None,
                "map_bounds": None,
                "blocked_world": [],
                "occupancy": [],
                "decisions": [],
                "snapshots": 0,
            }

    def observe_world(self, world: Any) -> None:
        """记录世界地图的观测范围，用于估算实际地图尺寸（行 × 列）。

        只取观测/障碍/单位/边界/当前视野的并集；地图比屏幕大时，随着镜头
        平移，世界坐标跨度会变大，最终得到完整地图尺寸。
        """
        if world is None:
            return
        with self._lock:
            trace = self._trace
            if trace is None:
                return
            points: set = set()
            try:
                points.update(world.observed)
                points.update(world.blocked)
                for group in (world.units or {}).values():
                    points.update(group)
            except Exception:  # noqa: BLE001
                return
            if not points:
                return
            view_rows = max(1, _to_int(getattr(world, "rows", 10), 10))
            view_cols = max(1, _to_int(getattr(world, "cols", 6), 6))
            min_row = min(point[0] for point in points)
            max_row = max(point[0] for point in points)
            min_col = min(point[1] for point in points)
            max_col = max(point[1] for point in points)

            camera = getattr(world, "camera_origin", (0, 0))
            min_row = min(min_row, _to_int(camera[0]))
            min_col = min(min_col, _to_int(camera[1]))
            max_row = max(max_row, _to_int(camera[0]) + view_rows - 1)
            max_col = max(max_col, _to_int(camera[1]) + view_cols - 1)

            for origin in (getattr(world, "boundaries", {}) or {}).values():
                if not isinstance(origin, (list, tuple)) or len(origin) < 2:
                    continue
                min_row = min(min_row, _to_int(origin[0]))
                min_col = min(min_col, _to_int(origin[1]))
                max_row = max(max_row, _to_int(origin[0]) + view_rows - 1)
                max_col = max(max_col, _to_int(origin[1]) + view_cols - 1)

            if max_row - min_row + 1 > MAX_MAP_CELLS:
                max_row = min_row + MAX_MAP_CELLS - 1
            if max_col - min_col + 1 > MAX_MAP_CELLS:
                max_col = min_col + MAX_MAP_CELLS - 1
            trace["map_bounds"] = [min_row, min_col, max_row, max_col]
            trace["map_view"] = [view_rows, view_cols]
            try:
                trace["blocked_world"] = sorted(world.blocked)[:500]
            except Exception:  # noqa: BLE001
                trace["blocked_world"] = []

    def snapshot(
        self,
        round_no: int,
        allies: Sequence[Any],
        enemies: Sequence[Any],
        environment: Sequence[Any] = (),
        world: Any = None,
    ) -> None:
        with self._lock:
            trace = self._trace
            if trace is None:
                return
            record = {
                "round": _to_int(round_no),
                "allies": _cell_list(allies),
                "enemies": _cell_list(enemies),
                "environment": _cell_list(environment),
            }
            if world is not None:
                try:
                    record["allies_world"] = [
                        list(world.local_to_world(*cell))
                        for cell in record["allies"]
                    ]
                    record["enemies_world"] = [
                        list(world.local_to_world(*cell))
                        for cell in record["enemies"]
                    ]
                except Exception:  # noqa: BLE001
                    pass
                if trace.get("initial_world") is None:
                    trace["initial_world"] = {
                        "allies": record.get("allies_world", []),
                        "enemies": record.get("enemies_world", []),
                        "blocked": list(trace.get("blocked_world") or []),
                        "round": record["round"],
                    }
                relation = self._compute_relation(
                    record.get("allies_world") or [],
                    record.get("enemies_world") or [],
                )
                if relation:
                    record["relation"] = relation
                    if trace.get("initial_relation") is None:
                        trace["initial_relation"] = relation
            if trace["initial"] is None:
                trace["initial"] = record
            trace["snapshots"] = _to_int(trace.get("snapshots")) + 1
            if len(trace["occupancy"]) < 12:
                trace["occupancy"].append(record)

    @staticmethod
    def _compute_relation(
        allies: Sequence[Any], enemies: Sequence[Any]
    ) -> Optional[dict]:
        """按八方向记录敌我相对位置（中心点、方向、大致格数）。"""
        if not allies or not enemies:
            return None
        ally_row = sum(_to_float(p[0]) for p in allies) / len(allies)
        ally_col = sum(_to_float(p[1]) for p in allies) / len(allies)
        enemy_row = sum(_to_float(p[0]) for p in enemies) / len(enemies)
        enemy_col = sum(_to_float(p[1]) for p in enemies) / len(enemies)
        row_delta = int(round(enemy_row - ally_row))
        col_delta = int(round(enemy_col - ally_col))
        code = direction_code(row_delta, col_delta)
        if code is None:
            code = "S"
        return {
            "allies": len(allies),
            "enemies": len(enemies),
            "ally_center": [round(ally_row, 1), round(ally_col, 1)],
            "enemy_center": [round(enemy_row, 1), round(enemy_col, 1)],
            "vector": [row_delta, col_delta],
            "direction": code,
            "label": DIRECTION_LABELS.get(code, code),
            "distance": max(abs(row_delta), abs(col_delta)),
        }

    def relation_for(self, city: str, task: str) -> dict:
        """读取该任务上一次战斗记录的敌我相对位置。"""
        self.reload()
        entry = (self._memory.get("tasks") or {}).get(task_key(city, task)) or {}
        return entry.get("last_relation") or {}

    def opening_for(self, city: str, task: str) -> dict:
        """读取首回合走位方案（没有则返回 disabled 默认值）。"""
        self.reload()
        entry = (self._memory.get("tasks") or {}).get(task_key(city, task)) or {}
        saved = entry.get("opening_plan") or {}
        plan = {
            "enabled": False,
            "target": "all",
            "piece": "",
            "direction": "E",
            "cells": 1,
            "round": 1,
        }
        plan.update(saved if isinstance(saved, dict) else {})
        plan["enabled"] = bool(plan.get("enabled"))
        plan["target"] = "one" if plan.get("target") == "one" else "all"
        plan["direction"] = (
            str(plan.get("direction") or "E").upper()
            if str(plan.get("direction") or "E").upper() in DIRECTION_DELTAS
            else "E"
        )
        plan["cells"] = max(1, min(6, _to_int(plan.get("cells"), 1)))
        plan["round"] = max(1, min(3, _to_int(plan.get("round"), 1)))
        plan["piece"] = str(plan.get("piece") or "")
        return plan

    def save_opening(self, city: str, task: str, plan: dict) -> bool:
        with self._lock:
            self.reload(force=True)
            key = task_key(city, task)
            entry = (self._memory.get("tasks") or {}).get(key)
            if not isinstance(entry, dict):
                return False
            plan = plan if isinstance(plan, dict) else {}
            direction = str(plan.get("direction") or "E").upper()
            entry["opening_plan"] = {
                "enabled": bool(plan.get("enabled")),
                "target": "one" if plan.get("target") == "one" else "all",
                "piece": str(plan.get("piece") or ""),
                "direction": (
                    direction if direction in DIRECTION_DELTAS else "E"
                ),
                "cells": max(1, min(6, _to_int(plan.get("cells"), 1))),
                "round": max(1, min(3, _to_int(plan.get("round"), 1))),
            }
            self._save_memory()
            return True

    def decision(
        self,
        round_no: int,
        action: str,
        from_cell: Optional[Tuple[int, int]] = None,
        to_cell: Optional[Tuple[int, int]] = None,
        target: Optional[Tuple[int, int]] = None,
        piece: str = "",
        note: str = "",
    ) -> None:
        with self._lock:
            trace = self._trace
            if trace is None:
                return
            if len(trace["decisions"]) >= MAX_DECISIONS_PER_RUN:
                return
            trace["decisions"].append(
                {
                    "round": _to_int(round_no),
                    "action": str(action),
                    "from": list(from_cell) if from_cell else None,
                    "to": list(to_cell) if to_cell else None,
                    "target": list(target) if target else None,
                    "piece": str(piece or ""),
                    "note": str(note or ""),
                }
            )

    def finish_trace(
        self, rounds: int, result: str, pieces: Optional[dict] = None
    ) -> Optional[dict]:
        """把当前战斗写入历史并更新统计/推荐策略；返回本次 run。"""
        with self._lock:
            self.reload()
            trace = self._trace
            self._trace = None
            if trace is None:
                return None
            if not trace.get("task") and not trace.get("city"):
                return None

            win = str(result).startswith("胜")
            run = {
                "time": _now(),
                "rounds": max(0, _to_int(rounds)),
                "result": "胜利" if win else "失败",
                "policy": trace.get("policy") or "balanced",
                "initial": trace.get("initial"),
                "relation": trace.get("initial_relation"),
                "occupancy": trace.get("occupancy") or [],
                "decisions": trace.get("decisions") or [],
                "pieces": pieces or {},
            }

            key = task_key(trace["city"], trace["task"])
            tasks = self._memory.setdefault("tasks", {})
            entry = tasks.setdefault(
                key,
                {
                    "city": trace["city"],
                    "task": trace["task"],
                    "type": trace.get("type", ""),
                    "level": trace.get("level", ""),
                    "runs": [],
                    "stats": {},
                    "opening": {},
                },
            )
            if trace.get("type") and not entry.get("type"):
                entry["type"] = trace["type"]
            if trace.get("level") and not entry.get("level"):
                entry["level"] = trace["level"]
            relation = trace.get("initial_relation")
            if relation:
                entry["last_relation"] = relation
                relations = entry.setdefault("relations", [])
                relations.append({"time": run["time"], **relation})
                entry["relations"] = relations[-20:]
            entry["runs"] = (entry.get("runs") or [])[-(MAX_RUNS_PER_TASK - 1):] + [run]
            entry["stats"] = self._update_stats(entry.get("stats") or {}, run)
            entry["opening"] = self._merge_opening(
                entry.get("opening") or {}, trace.get("initial")
            )
            map_data = self._build_map(entry.get("map") or {}, trace)
            if map_data:
                entry["map"] = map_data
                if map_data.get("source") == "auto":
                    logger.info(
                        f"战斗记忆：地图测量 {map_data['rows']} × "
                        f"{map_data['cols']}（行 × 列）"
                        + ("，大于手机屏幕" if map_data.get("screen_limited") else "")
                    )
            entry["recommended"] = self._recommend(entry["stats"])

            type_name = entry.get("type") or "未分类"
            type_stats = self._memory.setdefault("types", {})
            type_entry = type_stats.setdefault(type_name, {"stats": {}})
            type_entry["stats"] = self._update_stats(
                type_entry.get("stats") or {}, run
            )

            self._save_memory()
            logger.info(
                f"战斗记忆：{key} {run['result']} {run['rounds']} 回合"
                f"（策略 {run['policy']}，累计 {entry['stats'].get('runs', 0)} 场）"
            )
            return run

    # ---------------------------------------------------------- 统计

    @staticmethod
    def _update_stats(stats: dict, run: dict) -> dict:
        stats = dict(stats or {})
        stats["runs"] = _to_int(stats.get("runs")) + 1
        win = run["result"] == "胜利"
        stats["wins"] = _to_int(stats.get("wins")) + (1 if win else 0)
        stats["losses"] = _to_int(stats.get("losses")) + (0 if win else 1)
        stats["last_rounds"] = run["rounds"]
        stats["last_result"] = run["result"]
        stats["last"] = run["time"]
        if win:
            stats["best_rounds"] = min(
                _to_int(stats.get("best_rounds"), run["rounds"]), run["rounds"]
            )
            stats["rounds_total"] = _to_int(stats.get("rounds_total")) + run["rounds"]
            stats["rounds_wins"] = _to_int(stats.get("rounds_wins")) + 1
            stats["avg_rounds"] = round(
                stats["rounds_total"] / max(1, stats["rounds_wins"]), 1
            )
        stats["win_rate"] = round(
            stats["wins"] / max(1, stats["runs"]), 3
        )
        policy = run.get("policy") or "balanced"
        per_policy = stats.setdefault("policies", {})
        pstats = per_policy.setdefault(policy, {})
        pstats["runs"] = _to_int(pstats.get("runs")) + 1
        pstats["wins"] = _to_int(pstats.get("wins")) + (1 if win else 0)
        if win:
            pstats["rounds_total"] = _to_int(pstats.get("rounds_total")) + run["rounds"]
            pstats["rounds_wins"] = _to_int(pstats.get("rounds_wins")) + 1
            pstats["avg_rounds"] = round(
                pstats["rounds_total"] / max(1, pstats["rounds_wins"]), 1
            )
        return stats

    @staticmethod
    def _recommend(stats: dict) -> str:
        best_name = ""
        best_key = None
        for name, pstats in (stats.get("policies") or {}).items():
            wins = _to_int(pstats.get("wins"))
            if wins <= 0:
                continue
            avg = _to_float(pstats.get("avg_rounds"), 999)
            key = (-_to_int(pstats.get("wins")), avg)
            if best_key is None or key < best_key:
                best_key = key
                best_name = name
        return best_name

    @staticmethod
    def _merge_opening(opening: dict, initial: Optional[dict]) -> dict:
        if not initial:
            return opening
        opening = dict(opening or {})
        opening["count"] = _to_int(opening.get("count")) + 1
        for field in ("allies", "enemies"):
            counts = opening.setdefault(field, {})
            for row, col in initial.get(field) or []:
                key = f"{row},{col}"
                counts[key] = _to_int(counts.get(key)) + 1
            if len(counts) > MAX_OPENING_CELLS:
                keep = sorted(counts.items(), key=lambda item: -item[1])[
                    :MAX_OPENING_CELLS
                ]
                opening[field] = dict(keep)
        return opening

    @staticmethod
    def _clean_points(raw: Any, rows: int, cols: int) -> List[List[int]]:
        result: List[List[int]] = []
        if not isinstance(raw, (list, tuple)):
            return result
        for point in raw:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            row, col = _to_int(point[0]), _to_int(point[1])
            if 0 <= row < rows and 0 <= col < cols:
                result.append([row, col])
        return result

    @staticmethod
    def _normalize_points(
        raw: Any, origin_row: int, origin_col: int, rows: int, cols: int
    ) -> List[List[int]]:
        result: List[List[int]] = []
        if not isinstance(raw, (list, tuple)):
            return result
        for point in raw:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            row = _to_int(point[0]) - origin_row
            col = _to_int(point[1]) - origin_col
            if 0 <= row < rows and 0 <= col < cols:
                result.append([row, col])
        return result

    def _build_map(self, existing: dict, trace: dict) -> Optional[dict]:
        """首次战斗自动测量地图；用户改过的地图不被自动覆盖。"""
        if existing.get("source") == "user":
            return existing
        initial = trace.get("initial_world") or {}
        bounds = trace.get("map_bounds")
        points = (
            (initial.get("allies") or [])
            + (initial.get("enemies") or [])
            + (trace.get("blocked_world") or [])
        )
        if bounds:
            min_row, min_col, max_row, max_col = bounds
        elif points:
            min_row = min(point[0] for point in points)
            min_col = min(point[1] for point in points)
            max_row = max(point[0] for point in points)
            max_col = max(point[1] for point in points)
        else:
            return existing or None

        if existing.get("origin"):
            origin_row, origin_col = (
                _to_int(existing["origin"][0]),
                _to_int(existing["origin"][1]),
            )
            rows = max(_to_int(existing.get("rows"), 1), max_row - origin_row + 1)
            cols = max(_to_int(existing.get("cols"), 1), max_col - origin_col + 1)
        else:
            origin_row, origin_col = min_row, min_col
            rows = max_row - min_row + 1
            cols = max_col - min_col + 1
        rows = max(1, min(MAX_MAP_CELLS, rows))
        cols = max(1, min(MAX_MAP_CELLS, cols))

        data = {
            "rows": rows,
            "cols": cols,
            "origin": [origin_row, origin_col],
            "source": "auto",
            "measured": True,
            "screen_limited": (
                rows * CELL_SIZE > SCREEN_HEIGHT or cols * CELL_SIZE > SCREEN_WIDTH
            ),
            "view": trace.get("map_view") or [10, 6],
            "allies": self._normalize_points(
                initial.get("allies"), origin_row, origin_col, rows, cols
            ),
            "enemies": self._normalize_points(
                initial.get("enemies"), origin_row, origin_col, rows, cols
            ),
            "blocked": self._normalize_points(
                trace.get("blocked_world"), origin_row, origin_col, rows, cols
            ),
            "updated": _now(),
        }
        if existing.get("source") == "auto":
            if not data["allies"] and existing.get("allies"):
                data["allies"] = existing["allies"]
            if not data["enemies"] and existing.get("enemies"):
                data["enemies"] = existing["enemies"]
        return data

    def save_map(self, city: str, task: str, data: dict) -> bool:
        """保存用户修正的初始位置（归一化 0 基坐标）。"""
        with self._lock:
            self.reload(force=True)
            key = task_key(city, task)
            entry = (self._memory.get("tasks") or {}).get(key)
            if not isinstance(entry, dict):
                return False
            rows = max(1, min(MAX_MAP_CELLS, _to_int((data or {}).get("rows"), 13)))
            cols = max(1, min(MAX_MAP_CELLS, _to_int((data or {}).get("cols"), 15)))
            existing = entry.get("map") or {}
            origin = existing.get("origin") or [0, 0]
            entry["map"] = {
                "rows": rows,
                "cols": cols,
                "origin": [_to_int(origin[0]), _to_int(origin[1])],
                "source": "user",
                "measured": True,
                "screen_limited": (
                    rows * CELL_SIZE > SCREEN_HEIGHT
                    or cols * CELL_SIZE > SCREEN_WIDTH
                ),
                "view": existing.get("view") or [10, 6],
                "allies": self._clean_points((data or {}).get("allies"), rows, cols),
                "enemies": self._clean_points((data or {}).get("enemies"), rows, cols),
                "blocked": self._clean_points((data or {}).get("blocked"), rows, cols),
                "updated": _now(),
            }
            self._save_memory()
            return True

    # ---------------------------------------------------------- 查询

    def task_stats(self, city: str, task: str) -> dict:
        self.reload()
        entry = (self._memory.get("tasks") or {}).get(task_key(city, task)) or {}
        return (entry.get("stats") or {}).copy()

    def recommended_policy(self, city: str, task: str) -> str:
        self.reload()
        entry = (self._memory.get("tasks") or {}).get(task_key(city, task)) or {}
        return str(entry.get("recommended") or self.active_policy_name())

    def expected_rounds(
        self, city: str, task: str, policy: Optional[str] = None
    ) -> Optional[float]:
        """预计回合数：任务/策略的历史均值；全失败按 40 回合惩罚。"""
        self.reload()
        entry = (self._memory.get("tasks") or {}).get(task_key(city, task)) or {}
        stats = entry.get("stats") or {}
        policy = policy or entry.get("recommended") or ""
        if policy:
            pstats = (stats.get("policies") or {}).get(policy) or {}
            if _to_int(pstats.get("wins")) > 0:
                return _to_float(pstats.get("avg_rounds"), None)
        if _to_int(stats.get("wins")) > 0:
            return _to_float(stats.get("avg_rounds"), None)
        if _to_int(stats.get("runs")) > 0:
            return LOSS_PENALTY_ROUNDS
        # 同任务类型兜底：未知任务也能有个大概估计
        type_name = (entry.get("type") or "").strip()
        if type_name:
            type_stats = (
                ((self._memory.get("types") or {}).get(type_name) or {}).get("stats")
                or {}
            )
            if _to_int(type_stats.get("wins")) > 0:
                return _to_float(type_stats.get("avg_rounds"), UNKNOWN_ROUNDS) * 1.1
        legacy = self._legacy_expected_rounds(city, task)
        if legacy is not None:
            return legacy
        return None

    def _legacy_expected_rounds(self, city: str, task: str) -> Optional[float]:
        """兼容旧数据：没有战斗记忆时用 table/task_records.json 的平均回合。"""
        data = _read_json(os.path.join(self.table_dir, "task_records.json"))
        entry = (
            ((data.get("cities") or {}).get(city) or {}).get(task) or {}
        )
        count = _to_int(entry.get("count"))
        avg = _to_float(entry.get("rounds_avg"))
        # 旧版本因会话 key 不稳，486 条记录里 480 条是 0 回合、6 条 1 回合，
        # 这些不是真实回合数；低于 2 的旧平均值不参与任务排序，避免把
        # 「0.1 回合」当成最快任务。
        if count > 0 and avg >= 2.0:
            return round(avg, 1)
        return None

    def rank_tasks(
        self,
        city: str,
        tasks: Sequence[Any],
        strategy: str = "fastest",
    ) -> List[Tuple[Any, float, str]]:
        """按预计回合数给任务排序，返回 [(task, estimate, reason)]。

        未知任务用「乐观初始化」：取已知最快任务 ×0.7，给它一次采样机会；
        打过一场后就有真实回合数，不再享受乐观估计。
        """
        self.reload()
        known_estimates: List[float] = []
        raw: List[Tuple[Any, Optional[float]]] = []
        for task in tasks:
            name = getattr(task, "task_name", "") or ""
            estimate = self.expected_rounds(city, name)
            raw.append((task, estimate))
            if estimate is not None:
                known_estimates.append(float(estimate))
        best_known = min(known_estimates) if known_estimates else None

        estimates = []
        for task, estimate in raw:
            if estimate is None:
                estimate = (
                    max(1.0, round(best_known * 0.7, 1))
                    if best_known is not None
                    else UNKNOWN_ROUNDS
                )
                reason = f"首次探索（乐观 {estimate:.1f} 回合）"
            elif estimate >= LOSS_PENALTY_ROUNDS:
                reason = "历史全败"
            else:
                reason = f"预计 {estimate:.1f} 回合"
            estimates.append((task, float(estimate), reason))

        if strategy == "level":
            return sorted(
                estimates,
                key=lambda item: -_level_of(item[0]),
            )
        if strategy == "balanced":
            return sorted(
                estimates,
                key=lambda item: (
                    item[1] - _level_of(item[0]) * 0.05,
                    -_level_of(item[0]),
                ),
            )
        return sorted(estimates, key=lambda item: (item[1], -_level_of(item[0])))

    def summary(self) -> dict:
        """网页展示用：任务统计、策略、分配。"""
        self.reload()
        tasks = []
        for key, entry in (self._memory.get("tasks") or {}).items():
            stats = entry.get("stats") or {}
            tasks.append(
                {
                    "key": key,
                    "city": entry.get("city", ""),
                    "task": entry.get("task", ""),
                    "type": entry.get("type", ""),
                    "level": entry.get("level", ""),
                    "runs": _to_int(stats.get("runs")),
                    "wins": _to_int(stats.get("wins")),
                    "losses": _to_int(stats.get("losses")),
                    "win_rate": _to_float(stats.get("win_rate")),
                    "avg_rounds": stats.get("avg_rounds"),
                    "best_rounds": stats.get("best_rounds"),
                    "last_rounds": stats.get("last_rounds"),
                    "recommended": entry.get("recommended", ""),
                    "assigned": (self._policies.get("tasks") or {}).get(key, ""),
                    "opening": entry.get("opening") or {},
                    "map": entry.get("map") or {},
                    "relation": entry.get("last_relation") or {},
                    "relations": entry.get("relations") or [],
                    "opening_plan": self.opening_for(
                        entry.get("city", ""), entry.get("task", "")
                    ),
                    "policies": stats.get("policies") or {},
                    "recent": (entry.get("runs") or [])[-5:],
                }
            )
        tasks.sort(key=lambda item: (item["city"], item["task"]))
        return {
            "tasks": tasks,
            "policies": self.policies(),
            "active": self.active_policy_name(),
            "updated": self._memory.get("updated", ""),
        }


def _level_of(task: Any) -> int:
    """从 HUD 任务对象里取等级（支持 HudTaskInfo 或 dict）。"""
    text = ""
    if isinstance(task, dict):
        text = str(task.get("task_level") or task.get("level") or "")
    else:
        text = str(getattr(task, "task_level", "") or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


# 模块级单例 + 当前战斗轨迹
_MEMORY: Optional[BattleMemory] = None


def memory() -> BattleMemory:
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = BattleMemory()
    return _MEMORY


def start(*args: Any, **kwargs: Any) -> None:
    memory().start_trace(*args, **kwargs)


def observe_world(*args: Any, **kwargs: Any) -> None:
    memory().observe_world(*args, **kwargs)


def snapshot(*args: Any, **kwargs: Any) -> None:
    memory().snapshot(*args, **kwargs)


def decision(*args: Any, **kwargs: Any) -> None:
    memory().decision(*args, **kwargs)


def finish(rounds: int, result: str, pieces: Optional[dict] = None):
    return memory().finish_trace(rounds, result, pieces)
