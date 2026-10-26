"""离线自检：战斗模式选项 / 粗方向推进方向 / 棋子识别范围计算。

不需要连接模拟器或游戏，也不会点击任何东西。用法：

    python tools/selftest_battle_modes.py
"""

from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.zshg.auto_fight_processor import (  # noqa: E402
    AutoFightProcessor,
    MODE_PURSUIT,
    MODE_STANDBY,
)
from action.fight.fight_utils import battle_option_override  # noqa: E402
from action.zshg.battle_grid import BattleGrid, CellType  # noqa: E402
from action.zshg.battle_pieces import PieceInfo, PieceRecognizer  # noqa: E402
from action.zshg.battle_world_map import BattleSessionState  # noqa: E402

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


class _FakeRecognition:
    hit = False
    all_results = ()
    filtered_results = ()
    best_result = None


class FakeContext:
    """只实现处理器读取选项所需的接口。"""

    def __init__(self, node_data: dict | None = None) -> None:
        self.node_data = node_data or {}
        self.tasker = types.SimpleNamespace(stopping=False)

    def get_node_data(self, node: str):
        return self.node_data.get(node)

    def run_recognition(self, node: str, img):
        return _FakeRecognition()


def test_piece_range_measurement() -> None:
    print("[棋子识别] 攻击/移动范围实测")
    grid = BattleGrid()
    ally = grid.cells[4][2]
    ally.cell_type = CellType.SELF

    attack_offsets = [(0, 1), (1, 0), (0, -1), (-1, 0), (1, 1)]
    for dr, dc in attack_offsets:
        grid.cells[4 + dr][2 + dc].is_attackable = True
    move_offsets = [(0, 1), (0, 2), (1, 0)]
    for dr, dc in move_offsets:
        grid.cells[4 + dr][2 + dc].is_moveable = True

    attack, move = PieceRecognizer.measure_ranges(grid, ally)
    check("攻击范围偏移一致", attack == sorted(attack_offsets), str(attack))
    check("移动范围偏移一致", move == sorted(move_offsets), str(move))
    check("自身格不算范围", (0, 0) not in attack and (0, 0) not in move)
    check(
        "近战标签",
        PieceRecognizer.classify_range(attack_offsets).startswith("近战"),
        PieceRecognizer.classify_range(attack_offsets),
    )
    check(
        "两格标签",
        "2" in PieceRecognizer.classify_range([(0, 1), (0, 2), (0, -1), (0, -2)]),
        PieceRecognizer.classify_range([(0, 1), (0, 2), (0, -1), (0, -2)]),
    )
    check("空范围签名", PieceRecognizer.range_signature([]) == "empty")
    sig_a = PieceRecognizer.range_signature(attack_offsets)
    sig_b = PieceRecognizer.range_signature(list(reversed(attack_offsets)))
    check("签名与顺序无关", sig_a == sig_b, f"{sig_a} != {sig_b}")


def test_piece_name_matching() -> None:
    print("[棋子识别] 卡面文字匹配")
    recognizer = PieceRecognizer()
    name, confidence = recognizer.match_name(["攻击", "技能", "贾艾斯"])
    check("精确命中姓名", name == "贾艾斯" and confidence == 1.0, f"{name}/{confidence}")
    name, confidence = recognizer.match_name(["向贾艾斯发送"])
    check(
        "包含匹配姓名",
        name == "贾艾斯" and 0.8 <= confidence < 1.0,
        f"{name}/{confidence}",
    )
    name, _ = recognizer.match_name(["攻击", "技能"])
    check("指令文字不当作姓名", name == "", name)
    skills = recognizer.match_skills_from_text(["攻击", "示例技能"])
    check("技能词表为空时不误报", skills == [], str(skills))


def test_piece_identify_without_ocr() -> None:
    print("[棋子识别] 无卡面 OCR 时仍给出实测范围")
    context = FakeContext()
    recognizer = PieceRecognizer()
    grid = BattleGrid()
    ally = grid.cells[3][3]
    ally.cell_type = CellType.SELF
    for dr, dc in [(-2, 0), (-1, 0), (1, 0), (2, 0)]:
        grid.cells[3 + dr][3 + dc].is_attackable = True

    info = recognizer.identify(context, None, grid, ally, round_seen=5)
    check("攻击范围被识别", len(info.attack_offsets) == 4, str(info.attack_offsets))
    check("范围标签为两格", "2" in info.range_class, info.range_class)
    check("未匹配姓名时置信度较低", info.confidence < 0.6, str(info.confidence))
    record = info.to_record()
    check("可序列化为会话记录", record["attack_offsets"] and record["round_seen"] == 5)


def test_options_and_modes() -> None:
    print("[战斗模式] 选项读取与模式判定")
    processor = AutoFightProcessor()
    processor._session = BattleSessionState(key="selftest")

    context = FakeContext(
        {
            "FightModeSelect": {
                "recognition": {"param": {"expected": ["standby"]}}
            },
            "FightStandbyRounds": {
                "recognition": {"param": {"expected": ["7"]}}
            },
            "FightPieceRecognition": {
                "recognition": {"param": {"expected": ["off"]}}
            },
        }
    )
    processor._load_battle_options(context)
    check("模式选项=standby", processor._mode_option == "standby", processor._mode_option)
    check("待机回合数=7", processor._standby_rounds == 7, str(processor._standby_rounds))
    check("棋子识别=关闭", processor._piece_recognition is False)

    processor._mode_option = "auto"
    processor._standby_rounds = 3
    processor._session.confirmed_rounds = 2
    check("自动模式第3回合仍待机", processor._resolve_mode() == MODE_STANDBY)
    processor._session.confirmed_rounds = 3
    check("自动模式第4回合转追击", processor._resolve_mode() == MODE_PURSUIT)

    processor._standby_rounds = 0
    processor._session.confirmed_rounds = 0
    check("待机回合数=0 立刻追击", processor._resolve_mode() == MODE_PURSUIT)

    processor._mode_option = "standby"
    processor._session.confirmed_rounds = 99
    check("只待机模式永不自动转追击", processor._resolve_mode() == MODE_STANDBY)

    processor._mode_option = "pursuit"
    processor._session.confirmed_rounds = 0
    check("只追击模式第1回合即追击", processor._resolve_mode() == MODE_PURSUIT)

    check(
        "非法回合数回退默认",
        AutoFightProcessor._normalize_mode("待机模式") == "standby",
        AutoFightProcessor._normalize_mode("待机模式"),
    )


def test_frontier_direction() -> None:
    print("[粗方向推进] 未探索边界方向")
    processor = AutoFightProcessor()
    processor._session = BattleSessionState(key="selftest")
    processor._session.confirmed_rounds = 0

    direction = processor._frontier_direction()
    check("全图未探索时给出方向", direction is not None, str(direction))
    check(
        "方向是八方向之一",
        direction in {
            (-1, -1),
            (-1, 0),
            (-1, 1),
            (0, -1),
            (0, 1),
            (1, -1),
            (1, 0),
            (1, 1),
        },
        str(direction),
    )

    # 把采样范围内的格子全部标记为已观测 → 不应再给方向
    world = processor._session.world
    for row in range(-12, 22):
        for col in range(-12, 18):
            world.observed[(row, col)] = 0
    check("全图已探索时不再给方向", processor._frontier_direction() is None)

    processor._session.last_move_direction = (1, 0)  # (row, col)
    check(
        "默认推进方向来自上次移动（转成 col,row）",
        processor._default_advance_direction() == (0, 1),
        str(processor._default_advance_direction()),
    )


def test_piece_attack_targets() -> None:
    print("[棋子识别] 用实测攻击范围补齐被遮挡目标")
    processor = AutoFightProcessor()
    grid = BattleGrid()
    ally = grid.cells[5][2]
    ally.cell_type = CellType.SELF
    in_range = grid.cells[5][4]  # 偏移 (0, 2)
    in_range.cell_type = CellType.ENEMY
    out_of_range = grid.cells[8][5]  # 偏移 (3, 3)
    out_of_range.cell_type = CellType.ENEMY

    info = PieceInfo(
        name="测试棋子",
        job="弓箭手",
        skills=[],
        attack_offsets=[(-2, 0), (-1, 0), (0, 1), (0, 2)],
        move_offsets=[(0, 1)],
        range_class="十字直线2格",
        signature="reach2_n4",
        confidence=0.9,
        source="实测范围",
        card_text=[],
        cell=(5, 2),
        round_seen=1,
    )
    processor._active_piece = info
    promoted = processor._piece_attack_targets(grid, ally)
    check(
        "范围内的敌人被补齐为攻击目标",
        [(cell.row, cell.col) for cell in promoted] == [(5, 4)],
        str([(cell.row, cell.col) for cell in promoted]),
    )
    check("补齐后标记为可攻击", in_range.is_attackable)
    check("补齐后绑定攻击框中心", in_range.attack_center != (0, 0))
    check("范围外敌人不被补齐", not out_of_range.is_attackable)

    processor._active_piece = None
    check("无棋子识别结果时不补齐", processor._piece_attack_targets(grid, ally) == [])


def test_observation_persistence() -> None:
    print("[棋子识别] 实测范围写入观测表")
    import json
    import tempfile

    recognizer = PieceRecognizer()
    with tempfile.TemporaryDirectory() as tmp:
        recognizer.observation_path = os.path.join(tmp, "obs.json")
        info = PieceInfo(
            name="贾艾斯",
            job="弓箭手",
            skills=[],
            attack_offsets=[(-1, 0), (1, 0)],
            move_offsets=[],
            range_class="近战（相邻1格）",
            signature="reach1_n2_-1,0;1,0",
            confidence=1.0,
            source="实测范围",
            card_text=["贾艾斯"],
            cell=(2, 2),
            round_seen=4,
        )
        check("新范围需要写盘", recognizer.remember(info) is True)
        recognizer.flush_observations()
        with open(recognizer.observation_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        record = payload["pieces"].get("贾艾斯") or {}
        check("观测表已写入", bool(record), str(payload.get("pieces")))
        check(
            "观测表记录实测偏移",
            record.get("attack_offsets") == [[-1, 0], [1, 0]],
            str(record.get("attack_offsets")),
        )
        check("观测表记录回合", record.get("last_seen_round") == 4)
        check("重复同一范围不再写盘", recognizer.remember(info) is False)


def test_battle_option_override() -> None:
    print("[战斗选项] 嵌套任务显式传递")
    context = FakeContext(
        {
            "FightModeSelect": {
                "recognition": {"param": {"expected": ["pursuit"]}}
            },
            "FightStandbyRounds": {"expected": ["0"]},
            "FightPieceRecognition": {
                "recognition": {"param": {"expected": ["off"]}}
            },
        }
    )
    override = battle_option_override(context)
    check(
        "模式覆盖",
        override.get("FightModeSelect") == {"expected": ["pursuit"]},
        str(override),
    )
    check(
        "待机回合覆盖",
        override.get("FightStandbyRounds") == {"expected": ["0"]},
        str(override),
    )
    check(
        "棋子识别覆盖",
        override.get("FightPieceRecognition") == {"expected": ["off"]},
        str(override),
    )
    check("缺节点时为空", battle_option_override(FakeContext()) == {})


def test_yearly_interface_options() -> None:
    print("[战斗选项] 月度循环暴露同名选项")
    import json

    path = os.path.join(ROOT, "interface.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    task = next(
        (
            item
            for item in data.get("task", [])
            if item.get("entry") == "Auto_YearlyTask"
        ),
        {},
    )
    options = task.get("option") or []
    for name in ("战斗模式", "待机回合数", "棋子识别"):
        check(f"月度循环包含选项 {name}", name in options, str(options))


def main() -> int:
    test_piece_range_measurement()
    test_piece_name_matching()
    test_piece_identify_without_ocr()
    test_options_and_modes()
    test_frontier_direction()
    test_piece_attack_targets()
    test_observation_persistence()
    test_battle_option_override()
    test_yearly_interface_options()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
