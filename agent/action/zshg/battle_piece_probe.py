"""战斗中「棋子识别探针」：选中一名棋子后单独运行，输出识别结果。

用途：
    * 校准 ``table/piece_profiles.json`` 的 ``card_roi``（人物卡 OCR 区域）；
    * 确认某名棋子实测到的攻击/移动范围与技能识别结果；
    * 不需要跑完整场战斗。

使用方式：
    1. 进入战斗，选中一名我方棋子（出现攻击/移动范围）；
    2. 运行任务「功能：棋子识别（战斗中）」。

识别结果会：
    * 打印到日志（``棋子识别: …``）；
    * 追加到 ``debug/pieces/<日期>.jsonl``；
    * 合并进 ``table/piece_observations.json``。
"""

from __future__ import annotations

from typing import Any, Optional

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

from utils import logger

from .auto_fight_processor import AutoFightProcessor
from .battle_grid import BattleGrid, Cell, GridScanner
from .battle_pieces import PieceRecognizer


@AgentServer.custom_action("BattlePieceProbe")
class BattlePieceProbe(CustomAction):
    """读取当前选中帧，识别棋子身份、技能与实测攻击范围。"""

    def run(
        self, context: Context, _argv: CustomAction.RunArg
    ) -> CustomAction.RunResult:
        img = self._screencap(context)
        if img is None:
            logger.error("棋子识别探针：截图失败")
            return CustomAction.RunResult(success=False)

        scanner = GridScanner()
        grid = BattleGrid()
        scanner.scan_grid(grid, context, img)
        scanner.scan_ranges(grid, context, img)

        range_cells = [
            cell for cell in grid if cell.is_moveable or cell.is_attackable
        ]
        if not range_cells:
            logger.warning(
                "棋子识别探针：画面里没有攻击/移动范围。"
                "请先在战斗中选中一名我方棋子，再运行本任务"
            )
            return CustomAction.RunResult(success=False)

        ally = self._selected_ally(grid, img)
        if ally is None:
            logger.warning(
                "棋子识别探针：识别到行动范围，但定位不到对应的我方棋子；"
                "请确认棋子状态条没有被遮挡"
            )
            return CustomAction.RunResult(success=False)

        logger.info(
            f"棋子识别探针：选中我方格子=({ally.row},{ally.col})，"
            f"攻击格={sum(1 for cell in grid if cell.is_attackable)}，"
            f"移动格={sum(1 for cell in grid if cell.is_moveable)}"
        )

        recognizer = PieceRecognizer()
        info = recognizer.identify(context, img, grid, ally, round_seen=0)
        logger.info(f"棋子识别: {info.summary()}")
        if info.card_text:
            logger.info(f"棋子识别探针：人物卡文字={info.card_text}")
        if not info.name:
            logger.warning(
                "棋子识别探针：未匹配到棋子姓名。可能是 card_roi 未覆盖姓名区，"
                "或姓名不在 table/high_blood_names.json 中"
            )
        if not info.skills:
            logger.warning(
                "棋子识别探针：没有技能信息。可在 table/piece_profiles.json 的 "
                "skill_vocabulary / pieces 中补充技能名"
            )
        if not info.attack_offsets:
            logger.warning("棋子识别探针：没有实测到攻击范围")
        else:
            logger.info(
                "棋子识别探针：实测攻击范围偏移="
                f"{[list(offset) for offset in info.attack_offsets]}"
            )

        try:
            recognizer.remember(info)
            recognizer.flush_observations()
            PieceRecognizer.dump_debug(info)
        except Exception as exc:  # noqa: BLE001 - 落盘失败不影响识别结果
            logger.warning(f"棋子识别探针：结果落盘失败: {exc}")

        return CustomAction.RunResult(
            success=bool(info.name or info.attack_offsets)
        )

    @staticmethod
    def _selected_ally(grid: BattleGrid, img: Any) -> Optional[Cell]:
        """优先用蓝色菱形行动点定位被选中的我方，退化为范围簇最近的我方。"""
        selected = AutoFightProcessor._selected_self_cells(img)
        for cell in grid.self_units:
            if (cell.row, cell.col) in selected:
                return cell

        range_cells = [
            cell for cell in grid if cell.is_moveable or cell.is_attackable
        ]
        if not grid.self_units or not range_cells:
            return None
        center_row = sum(cell.row for cell in range_cells) / len(range_cells)
        center_col = sum(cell.col for cell in range_cells) / len(range_cells)
        return min(
            grid.self_units,
            key=lambda cell: (
                max(
                    abs(cell.row - center_row),
                    abs(cell.col - center_col),
                ),
                cell.row,
                cell.col,
            ),
        )

    @staticmethod
    def _screencap(context: Context) -> Optional[Any]:
        job = context.tasker.controller.post_screencap().wait()
        if not job.succeeded:
            return None
        return job.get()
