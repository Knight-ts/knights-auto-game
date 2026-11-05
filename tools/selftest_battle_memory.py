"""离线自检：战斗记忆、策略权重、推荐策略与按回合排序。

用法：
    python tools/selftest_battle_memory.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from utils import battle_memory  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


class Task:
    def __init__(self, name: str, level: str = "50"):
        self.task_name = name
        self.task_level = level


def record(
    memory: battle_memory.BattleMemory,
    policy: str,
    rounds: int,
    win: bool,
    task: str = "任务A",
):
    memory.start_trace(
        "暮角城",
        task,
        task_type="斩杀任务",
        level="敌人等级：50",
        policy=memory.policy_by_name(policy),
    )
    memory.snapshot(1, [(5, 2), (6, 2)], [(2, 7)])
    memory.decision(1, "move", (5, 3), (5, 4), piece="甲")
    memory.finish_trace(rounds, "胜利" if win else "失败", {"甲": {"range_class": "近战"}})


def test_processor_policy(memory: battle_memory.BattleMemory) -> None:
    """验证策略真的改变了战斗处理器的目标/移动选择。"""
    print("[接入] AutoFightProcessor 目标与移动")
    import types

    from action.zshg.battle_grid import Cell
    from action.zshg.auto_fight_processor import AutoFightProcessor

    proc = AutoFightProcessor()
    ally = Cell(row=5, col=5)
    near_full = Cell(row=1, col=1)
    near_full.target_center = (100, 100)
    near_full.is_attackable = True
    far_weak = Cell(row=8, col=8)
    far_weak.target_center = (700, 700)
    far_weak.is_attackable = True
    proc._screen_distance = lambda cell, ally_cell, action: (  # type: ignore[assignment]
        50 if cell is near_full else 200
    )
    proc._target_hp_pixels = lambda img, cell: (  # type: ignore[assignment]
        90 if cell is near_full else 10
    )

    proc._policy = memory.policy_by_name("balanced")
    chosen = proc._pick_attack_target([near_full, far_weak], ally, None)
    check("均衡策略选近处满血", chosen is near_full, str(chosen))

    proc._policy = memory.policy_by_name("focus_weak")
    chosen = proc._pick_attack_target([near_full, far_weak], ally, None)
    check("集火残血选远处残血", chosen is far_weak, str(chosen))

    enemy = Cell(row=3, col=3)
    grid = types.SimpleNamespace(enemy_units=[enemy])
    adjacent = Cell(row=3, col=4)
    far = Cell(row=6, col=6)
    proc._policy = memory.policy_by_name("defensive")
    filtered = proc._apply_move_policy([adjacent, far], ally, grid)
    check("稳守策略排除贴身格", adjacent not in filtered and far in filtered, str([(c.row, c.col) for c in filtered]))

    proc._policy = memory.policy_by_name("hold")
    filtered = proc._apply_move_policy([adjacent, far], ally, grid)
    check("待机策略有敌人时不移动", filtered == [], str(filtered))

    proc._policy = memory.policy_by_name("balanced")
    filtered = proc._apply_move_policy([adjacent, far], ally, grid)
    check("均衡策略不限制移动", filtered == [adjacent, far], str(filtered))


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        memory = battle_memory.BattleMemory(table_dir=temp_dir)

        print("[策略] 默认与目标评分")
        names = memory.policy_names()
        check("默认策略齐全", {"balanced", "focus_weak", "defensive", "hold"} <= set(names), str(names))
        check("默认 active=均衡", memory.active_policy_name() == "balanced")
        balanced = memory.policy_by_name("balanced")
        focus = memory.policy_by_name("focus_weak")
        features = {"distance": 1.0, "hp_ratio": 0.2, "threat": 0.0}
        check(
            "集火残血更偏好低血量",
            focus.score_target(features) > balanced.score_target(features),
            f"{focus.score_target(features)} vs {balanced.score_target(features)}",
        )

        print("[记忆] 记录两场战斗")
        record(memory, "balanced", 10, True)
        record(memory, "focus_weak", 7, True)
        stats = memory.task_stats("暮角城", "任务A")
        check("场次累计", stats.get("runs") == 2, str(stats))
        check("胜率 100%", stats.get("win_rate") == 1.0, str(stats))
        check("最佳回合 7", stats.get("best_rounds") == 7, str(stats))
        check("平均回合 8.5", stats.get("avg_rounds") == 8.5, str(stats))
        check(
            "推荐策略=集火残血",
            memory.recommended_policy("暮角城", "任务A") == "focus_weak",
            str(stats.get("policies")),
        )
        check(
            "预计回合=推荐策略均值",
            memory.expected_rounds("暮角城", "任务A") == 7.0,
            str(memory.expected_rounds("暮角城", "任务A")),
        )
        check(
            "下一场自动使用推荐策略",
            memory.policy_for("暮角城", "任务A").id == "focus_weak",
            memory.policy_for("暮角城", "任务A").id,
        )

        entry = memory.summary()["tasks"][0]
        check("开局位置已记录", entry["opening"].get("count") == 2, str(entry["opening"]))
        check("最近 run 有决策", bool(entry["recent"][-1]["decisions"]), str(entry["recent"][-1]))
        check("记忆文件存在", os.path.exists(os.path.join(temp_dir, "battle_memory.json")))

        print("[排序] 按预计回合选任务")
        with open(os.path.join(temp_dir, "task_records.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {"cities": {"暮角城": {"任务E": {"count": 3, "rounds_avg": 9.5}}}},
                handle,
                ensure_ascii=False,
            )
        check(
            "旧任务记录可作回合兜底",
            memory.expected_rounds("暮角城", "任务E") == 9.5,
            str(memory.expected_rounds("暮角城", "任务E")),
        )
        ranked = memory.rank_tasks(
            "暮角城", [Task("任务B", "90"), Task("任务A", "50")], "fastest"
        )
        check(
            "未知任务先获得一次探索机会",
            ranked[0][0].task_name == "任务B",
            str([(t.task_name, r) for t, r, _ in ranked]),
        )
        check(
            "乐观估计=已知最快×0.7",
            ranked[0][1] == 4.9,
            str(ranked[0]),
        )
        record(memory, "balanced", 20, True, task="任务B")
        ranked_after = memory.rank_tasks(
            "暮角城", [Task("任务B", "90"), Task("任务A", "50")], "fastest"
        )
        check(
            "采样后按真实回合排序",
            ranked_after[0][0].task_name == "任务A",
            str([(t.task_name, r) for t, r, _ in ranked_after]),
        )
        ranked_level = memory.rank_tasks(
            "暮角城", [Task("任务B", "90"), Task("任务A", "50")], "level"
        )
        check("最高等级策略保持原逻辑", ranked_level[0][0].task_name == "任务B")

        print("[策略] 任务分配与自定义权重")
        memory.assign("暮角城", "任务A", "hold")
        check("任务策略分配生效", memory.policy_for("暮角城", "任务A").id == "hold")
        saved = memory.save_policy(
            "custom_weak",
            {
                "name": "只打残血",
                "target": {"weights": {"distance": 0.0, "hp_ratio": -9.0}},
                "move": {"mode": "approach"},
            },
        )
        check("保存自定义策略", saved.get("id") == "custom_weak")
        check("自定义策略可读取", memory.policy_by_name("custom_weak").name == "只打残血")

        with open(os.path.join(temp_dir, "battle_policies.json"), encoding="utf-8") as handle:
            payload = json.load(handle)
        check("策略文件合法", "policies" in payload and "custom_weak" in payload["policies"])

        test_processor_policy(memory)

        print("[学习] 探索策略轮换")
        for _ in range(5):
            record(memory, "balanced", 10, True, task="任务D")
        chosen = memory.policy_for("暮角城", "任务D")
        recommended = memory.recommended_policy("暮角城", "任务D")
        check(
            "到探索场次会换策略采样",
            chosen.id != recommended,
            f"recommended={recommended} chosen={chosen.id}",
        )

        print("[地图] 自动测量与初始位置修正")
        from action.zshg.battle_world_map import BattleSessionState, session_key

        check(
            "会话键跨 Context 稳定",
            session_key(object()) == session_key(object()) == "battle",
        )
        check(
            "回合数包含初始回合（3 次推进 = 4 回合）",
            BattleSessionState(key="x", confirmed_rounds=3).total_rounds() == 4,
        )
        check(
            "第 1 回合结束也至少记 1 回合",
            BattleSessionState(key="x").total_rounds() == 1,
        )

        battle = BattleSessionState(key="x")
        for round_no in range(1, 21):
            battle.record_round_activity(round_no)
            battle.record_round_advance()
        check(
            "20 回合结束+进入 21 回合仍记 20 回合",
            battle.total_rounds() == 20 and battle.reached_round() == 21,
            f"total={battle.total_rounds()} reached={battle.reached_round()}",
        )
        quick = BattleSessionState(key="y")
        quick.record_round_activity(1)
        check("第 1 回合击杀记 1 回合", quick.total_rounds() == 1)

        print("[八方向] 相对位置与首回合走位")
        check(
            "八方向量化",
            [
                battle_memory.direction_code(-1, 0),
                battle_memory.direction_code(-1, 1),
                battle_memory.direction_code(0, 1),
                battle_memory.direction_code(1, 1),
                battle_memory.direction_code(1, 0),
                battle_memory.direction_code(1, -1),
                battle_memory.direction_code(0, -1),
                battle_memory.direction_code(-1, -1),
                battle_memory.direction_code(0, 0),
            ]
            == ["N", "NE", "E", "SE", "S", "SW", "W", "NW", None],
        )
        relation = battle_memory.BattleMemory._compute_relation(
            [[5, 5]], [[1, 8]]
        )
        check(
            "敌我相对方向=东北",
            relation
            and relation["direction"] == "NE"
            and relation["label"] == "东北"
            and relation["distance"] == 4,
            str(relation),
        )
        check(
            "八方向位移表",
            battle_memory.DIRECTION_DELTAS["NE"] == (-1, 1)
            and battle_memory.DIRECTION_DELTAS["SW"] == (1, -1),
        )

        class FakeWorld:
            rows = 10
            cols = 6
            observed = {(0, 0), (0, 5), (9, 10)}
            blocked = {(4, 4)}
            units = {"self": {(2, 3)}, "enemy": {(1, 1)}}
            camera_origin = (0, 0)
            boundaries = {}

            def local_to_world(self, row, col):
                return row, col

        world = FakeWorld()
        memory.start_trace("暮角城", "任务M", policy=memory.policy_by_name("balanced"))
        memory.observe_world(world)
        memory.snapshot(1, [(2, 3)], [(1, 1)], world=world)
        memory.finish_trace(5, "胜利")
        entry = next(
            item for item in memory.summary()["tasks"] if item["task"] == "任务M"
        )
        check("地图尺寸按观测跨度计算", (entry["map"]["rows"], entry["map"]["cols"]) == (10, 11), str(entry["map"]))
        check("地图大于单屏时标记", entry["map"]["screen_limited"] is True, str(entry["map"]))
        check("初始我方位置归一化", entry["map"]["allies"] == [[2, 3]], str(entry["map"]["allies"]))
        check("初始敌方位置归一化", entry["map"]["enemies"] == [[1, 1]], str(entry["map"]["enemies"]))
        check(
            "战斗记录保存八方向相对位置",
            (entry["relation"] or {}).get("direction") == "W",
            str(entry.get("relation")),
        )

        saved = memory.save_map(
            "暮角城",
            "任务M",
            {"rows": 13, "cols": 15, "allies": [[0, 0]], "enemies": [[12, 14]], "blocked": []},
        )
        after = next(
            item for item in memory.summary()["tasks"] if item["task"] == "任务M"
        )
        check("用户修正可保存", saved and after["map"]["source"] == "user", str(after["map"]))
        check("用户修正地图尺寸 13×15", (after["map"]["rows"], after["map"]["cols"]) == (13, 15))
        check("用户修正敌方位置", after["map"]["enemies"] == [[12, 14]])

        saved_opening = memory.save_opening(
            "暮角城",
            "任务M",
            {
                "enabled": True,
                "target": "all",
                "direction": "E",
                "cells": 2,
                "round": 1,
            },
        )
        plan = memory.opening_for("暮角城", "任务M")
        check(
            "首回合走位可保存/读取",
            saved_opening
            and plan["enabled"]
            and plan["direction"] == "E"
            and plan["cells"] == 2,
            str(plan),
        )

        import types

        from action.zshg.auto_fight_processor import AutoFightProcessor
        from action.zshg.battle_grid import Cell

        processor = AutoFightProcessor()
        processor._opening_plan = plan
        processor._opening_moved = set()
        processor._opening_one_done = False
        processor._session = types.SimpleNamespace(confirmed_rounds=0)
        processor._active_piece = None
        ally = Cell(row=5, col=5)
        east_one = Cell(row=5, col=6)
        east_one.is_moveable = True
        east_two = Cell(row=5, col=7)
        east_two.is_moveable = True
        side = Cell(row=4, col=6)
        side.is_moveable = True
        fake_grid = types.SimpleNamespace(
            cells=[[east_one, east_two], [side]]
        )
        target = processor._opening_move_target(fake_grid, ally)
        check(
            "首回合走位选择向东 2 格",
            target is east_two,
            str(target),
        )
        processor._opening_plan = dict(
            plan, target="one", direction="N", cells=1
        )
        north = Cell(row=4, col=5)
        north.is_moveable = True
        fake_grid.cells = [[north]]
        check(
            "单棋子向北 1 格",
            processor._opening_move_target(fake_grid, ally) is north,
        )
        processor._mode_option = "auto"
        processor._standby_rounds = 20
        processor._opening_plan = dict(plan, enabled=True)
        processor._session.confirmed_rounds = 0
        check(
            "配了首回合走位则强制主动模式",
            processor._resolve_mode() == "pursuit",
            processor._resolve_mode(),
        )
        processor._opening_plan = {"enabled": False}
        check(
            "未配走位仍按待机回合数",
            processor._resolve_mode() == "standby",
            processor._resolve_mode(),
        )

        print("[失败] 全败任务按惩罚回合")
        memory.start_trace("暮角城", "任务C", policy=memory.policy_by_name("balanced"))
        memory.snapshot(1, [(5, 2)], [(2, 7)])
        memory.finish_trace(6, "失败")
        check(
            "全败任务预计 40 回合",
            memory.expected_rounds("暮角城", "任务C") == battle_memory.LOSS_PENALTY_ROUNDS,
            str(memory.expected_rounds("暮角城", "任务C")),
        )

    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
