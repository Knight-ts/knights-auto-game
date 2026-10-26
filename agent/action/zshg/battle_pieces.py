"""战斗棋子识别模块。

目标：在战斗中「精确识别棋子，并确定其技能和攻击范围」。

数据来源分三层，越靠前越可信：

1. **实测**：选中棋子后同一帧的黄色攻击范围 / 蓝色移动范围，换算成
   相对棋子所在格的 ``(d_row, d_col)`` 偏移集合。这是游戏当场给出的
   真实攻击范围，不依赖任何配置表。
2. **卡面 OCR**：选中棋子时左上角人物卡上的文字（姓名 / 职业 / 技能名 /
   指令按钮）。OCR 结果会与 ``table/piece_profiles.json`` 及
   ``table/high_blood_names.json`` 中的姓名做精确/包含/相似度匹配。
3. **配置表**：``table/piece_profiles.json`` 里按棋子名或职业登记的
   技能列表与静态攻击范围；实测存在时永远以实测为准。

识别结果会写入整场战斗的 ``BattleSessionState.pieces``（跨恢复调用保留），
并追加到 ``debug/pieces/<日期>.jsonl``；实测到的攻击范围还会合并进
``table/piece_observations.json``，下次遇到同名棋子可直接复用，
同时给用户补全 ``piece_profiles.json`` 提供依据。

本模块不依赖 MaaFramework 的控制器，只读取传入的截图/网格，
因此可以在离线自检脚本里直接测试。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from utils import logger

Offset = Tuple[int, int]  # (d_row, d_col)，相对棋子所在格

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_MODULE_DIR)))
_TABLE_DIR = os.path.join(_PROJECT_ROOT, "table")
_DEBUG_DIR = os.path.join(_PROJECT_ROOT, "debug", "pieces")

PROFILE_FILE = os.path.join(_TABLE_DIR, "piece_profiles.json")
OBSERVATION_FILE = os.path.join(_TABLE_DIR, "piece_observations.json")
NAME_FILE = os.path.join(_TABLE_DIR, "high_blood_names.json")

# 人物卡上的指令文字，不可能是棋子姓名/技能名
DEFAULT_COMMAND_WORDS: Tuple[str, ...] = (
    "攻击",
    "技能",
    "待机",
    "结束回合",
    "结束",
    "撤退",
    "逃跑",
    "道具",
    "防御",
    "取消",
    "确定",
    "行动",
    "移动",
)

# 8 方向，用于判断攻击范围形状
_CARDINAL: Tuple[Offset, ...] = ((-1, 0), (1, 0), (0, -1), (0, 1))


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class SkillInfo:
    """棋子技能（来自配置表或卡面文字）。"""

    name: str
    kind: str = ""
    range: str = ""
    note: str = ""

    def label(self) -> str:
        parts = [self.name]
        if self.kind:
            parts.append(self.kind)
        if self.range:
            parts.append(f"范围:{self.range}")
        return "/".join(parts)

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "kind": self.kind,
            "range": self.range,
            "note": self.note,
        }


@dataclass
class PieceProfile:
    """配置表中一个棋子（或职业）的静态资料。"""

    name: str
    job: str = ""
    skills: List[SkillInfo] = field(default_factory=list)
    attack_range: List[Offset] = field(default_factory=list)
    note: str = ""


@dataclass
class PieceInfo:
    """一次选中棋子得到的识别结果。"""

    name: str
    job: str
    skills: List[SkillInfo]
    attack_offsets: List[Offset]
    move_offsets: List[Offset]
    range_class: str
    signature: str
    confidence: float
    source: str
    card_text: List[str]
    cell: Tuple[int, int]
    round_seen: int
    skills_source: str = ""

    @property
    def key(self) -> str:
        """整场战斗内的棋子标识：优先姓名，未识别时合并成一个占位项。"""
        return self.name or "未识别棋子"

    @property
    def reach(self) -> int:
        """攻击范围最远切比雪夫距离；0 表示未测到。"""
        if not self.attack_offsets:
            return 0
        return max(max(abs(dr), abs(dc)) for dr, dc in self.attack_offsets)

    def attack_cells(self, row: int, col: int) -> List[Tuple[int, int]]:
        """把攻击范围偏移换算成某格出发时可攻击的绝对格子。"""
        return [(row + dr, col + dc) for dr, dc in self.attack_offsets]

    def skill_names(self) -> List[str]:
        return [skill.name for skill in self.skills]

    def summary(self) -> str:
        skills = "、".join(self.skill_names()) or "未识别"
        return (
            f"名字={self.name or '未识别'} "
            f"职业={self.job or '未知'} "
            f"技能={skills} "
            f"攻击范围={self.range_class}"
            f"(实测{len(self.attack_offsets)}格/最远{self.reach}格) "
            f"移动范围={len(self.move_offsets)}格 "
            f"置信度={self.confidence:.2f} 来源={self.source}"
            f" 格子=({self.cell[0]},{self.cell[1]})"
        )

    def to_record(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "job": self.job,
            "skills": [skill.to_dict() for skill in self.skills],
            "skills_source": self.skills_source,
            "attack_offsets": [list(item) for item in self.attack_offsets],
            "move_offsets": [list(item) for item in self.move_offsets],
            "range_class": self.range_class,
            "signature": self.signature,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "cell": list(self.cell),
            "round_seen": self.round_seen,
            "card_text": list(self.card_text),
        }


# ---------------------------------------------------------------------------
# 识别器
# ---------------------------------------------------------------------------


class PieceRecognizer:
    """棋子识别器：卡面 OCR + 范围实测 + 配置表匹配。"""

    # 人物卡 OCR 的默认区域（720x1280 下的人物卡大致范围）。
    # 可在 table/piece_profiles.json 的 ``card_roi`` 覆盖。
    DEFAULT_CARD_ROI: Tuple[int, int, int, int] = (0, 120, 460, 430)
    OCR_NODE = "Battle_RoleCardText"
    NAME_MATCH_MIN_RATIO = 0.72

    def __init__(self, profile_path: Optional[str] = None) -> None:
        self.profile_path = profile_path or PROFILE_FILE
        self.observation_path = OBSERVATION_FILE
        self.command_words: Tuple[str, ...] = DEFAULT_COMMAND_WORDS
        self.card_roi: Tuple[int, int, int, int] = self.DEFAULT_CARD_ROI
        self.skill_vocabulary: List[str] = []
        self.profiles: Dict[str, PieceProfile] = {}
        self.job_profiles: Dict[str, PieceProfile] = {}
        self._known_names: Optional[set] = None
        self._observations: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load()

    # ---------------------------------------------------------------- 配置表

    def _load(self) -> None:
        self.profiles.clear()
        self.job_profiles.clear()
        self.skill_vocabulary.clear()
        self._load_observations()
        data = self._read_json(self.profile_path)
        if not isinstance(data, dict):
            logger.warning(
                f"棋子配置表不存在或格式错误: {self.profile_path}，"
                "本次仅使用实测范围"
            )
            return

        roi = data.get("card_roi")
        if isinstance(roi, (list, tuple)) and len(roi) == 4:
            self.card_roi = tuple(int(value) for value in roi)  # type: ignore[assignment]
        words = data.get("command_words")
        if isinstance(words, list) and words:
            self.command_words = tuple(str(word) for word in words)
        vocabulary = data.get("skill_vocabulary")
        if isinstance(vocabulary, list):
            self.skill_vocabulary = [
                str(item) for item in vocabulary if str(item).strip()
            ]

        for name, raw in (data.get("jobs") or {}).items():
            profile = self._parse_profile(str(name), raw)
            if profile is not None:
                self.job_profiles[str(name)] = profile
        for name, raw in (data.get("pieces") or {}).items():
            if str(name).startswith("_"):
                continue
            profile = self._parse_profile(str(name), raw)
            if profile is not None:
                self.profiles[str(name)] = profile

        logger.debug(
            "棋子配置表已加载: "
            f"棋子={len(self.profiles)}, 职业={len(self.job_profiles)}, "
            f"技能词表={len(self.skill_vocabulary)}, "
            f"卡面ROI={self.card_roi}"
        )

    def _parse_profile(self, name: str, raw: Any) -> Optional[PieceProfile]:
        if isinstance(raw, str):
            return PieceProfile(name=name, job=raw)
        if not isinstance(raw, dict):
            return None
        skills: List[SkillInfo] = []
        for item in raw.get("skills") or []:
            if isinstance(item, str):
                skills.append(SkillInfo(name=item))
            elif isinstance(item, dict) and item.get("name"):
                skills.append(
                    SkillInfo(
                        name=str(item.get("name")),
                        kind=str(item.get("kind") or ""),
                        range=str(item.get("range") or ""),
                        note=str(item.get("note") or ""),
                    )
                )
        attack_range: List[Offset] = []
        for item in raw.get("attack_range") or []:
            offset = self._parse_offset(item)
            if offset is not None:
                attack_range.append(offset)
        return PieceProfile(
            name=name,
            job=str(raw.get("job") or ""),
            skills=skills,
            attack_range=attack_range,
            note=str(raw.get("note") or ""),
        )

    @staticmethod
    def _parse_offset(item: Any) -> Optional[Offset]:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                return int(item[0]), int(item[1])
            except (TypeError, ValueError):
                return None
        if isinstance(item, str) and "," in item:
            first, second = item.split(",", 1)
            try:
                return int(first), int(second)
            except ValueError:
                return None
        return None

    @staticmethod
    def _read_json(path: str) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def reload(self) -> None:
        """配置表被用户改动后重新读取。"""
        self._known_names = None
        self._load()

    # ------------------------------------------------------------ 姓名数据库

    def _load_known_names(self) -> set:
        if self._known_names is not None:
            return self._known_names
        names = set(self.profiles)
        data = self._read_json(NAME_FILE)
        if isinstance(data, dict):
            for race, genders in data.items():
                if not isinstance(genders, dict):
                    continue
                for gender_names in genders.values():
                    if not isinstance(gender_names, list):
                        continue
                    for name in gender_names:
                        text = str(name).strip()
                        if 1 < len(text) <= 6:
                            names.add(text)
        self._known_names = names
        logger.debug(f"棋子姓名库已加载: {len(names)} 个候选名")
        return names

    # ------------------------------------------------------------ 范围实测

    @staticmethod
    def measure_ranges(
        grid: Any, ally: Any
    ) -> Tuple[List[Offset], List[Offset]]:
        """从选中帧的网格中取相对棋子的攻击/移动范围偏移。"""
        attack: set = set()
        move: set = set()
        for cell in grid:
            offset = (cell.row - ally.row, cell.col - ally.col)
            if cell.is_attackable:
                attack.add(offset)
            if cell.is_moveable:
                move.add(offset)
        attack.discard((0, 0))
        move.discard((0, 0))
        return sorted(attack), sorted(move)

    @staticmethod
    def range_signature(offsets: Sequence[Offset]) -> str:
        if not offsets:
            return "empty"
        reach = max(max(abs(dr), abs(dc)) for dr, dc in offsets)
        body = ";".join(f"{dr},{dc}" for dr, dc in sorted(offsets))
        return f"reach{reach}_n{len(offsets)}_{body}"

    @classmethod
    def classify_range(cls, offsets: Sequence[Offset]) -> str:
        """把攻击范围偏移归类成便于阅读的标签。"""
        if not offsets:
            return "未识别"
        reach = max(max(abs(dr), abs(dc)) for dr, dc in offsets)
        if reach <= 1:
            return "近战（相邻1格）"
        if all(offset in _CARDINAL for offset in offsets):
            return f"十字直线{reach}格"
        if len(offsets) >= 4 * reach:
            return f"环形{reach}格"
        return f"{reach}格范围"

    # ------------------------------------------------------------ 卡面 OCR

    def read_card_text(self, context: Any, img: Any) -> List[str]:
        """读取人物卡文字；识别节点缺失或未命中时返回空列表。"""
        override = {self.OCR_NODE: {"roi": list(self.card_roi)}}
        try:
            try:
                recognition = context.run_recognition(
                    self.OCR_NODE, img, pipeline_override=override
                )
            except TypeError:
                # 旧接口不支持 pipeline_override，退回节点自带 ROI
                recognition = context.run_recognition(self.OCR_NODE, img)
        except Exception as exc:  # noqa: BLE001 - 节点缺失不应中断战斗
            logger.debug(f"人物卡 OCR 节点不可用: {exc}")
            return []
        if not recognition:
            return []
        results = getattr(recognition, "all_results", None) or getattr(
            recognition, "filtered_results", None
        ) or []
        texts: List[str] = []
        for item in results:
            text = (getattr(item, "text", "") or "").strip()
            score = float(getattr(item, "score", 1.0) or 0.0)
            if not text or score < 0.5:
                continue
            if text not in texts:
                texts.append(text)
        return texts

    def _content_texts(self, texts: Iterable[str]) -> List[str]:
        """过滤指令按钮文字和纯数字，只保留可能是姓名/技能的内容。"""
        content: List[str] = []
        for raw in texts:
            text = raw.strip().strip("：:·。.,，")
            if not text or text.isdigit():
                continue
            compact = text.replace(" ", "")
            if compact in self.command_words:
                continue
            content.append(compact)
        return content

    def match_name(self, texts: Sequence[str]) -> Tuple[str, float]:
        """把卡面文字匹配到已知棋子姓名，返回 (姓名, 置信度)。"""
        content = self._content_texts(texts)
        if not content:
            return "", 0.0
        known = self._load_known_names()

        for text in content:
            if text in known:
                return text, 1.0

        best_name = ""
        best_ratio = 0.0
        best_confidence = 0.0
        for text in content:
            for name in known:
                if len(name) < 2:
                    continue
                if name in text:
                    confidence = 0.85
                else:
                    ratio = SequenceMatcher(None, name, text).ratio()
                    if ratio < self.NAME_MATCH_MIN_RATIO:
                        continue
                    confidence = 0.6
                if (confidence, len(name)) > (best_confidence, len(best_name)):
                    best_name = name
                    best_confidence = confidence
                    best_ratio = SequenceMatcher(None, name, text).ratio()
        if best_name:
            logger.debug(
                f"卡面姓名匹配: 命中={best_name}, 相似度={best_ratio:.2f}, "
                f"置信度={best_confidence:.2f}, 文本={content}"
            )
        return best_name, best_confidence

    def match_job(self, texts: Sequence[str]) -> str:
        content = self._content_texts(texts)
        for text in content:
            if text in self.job_profiles:
                return text
        for text in content:
            for job in self.job_profiles:
                if len(job) >= 2 and job in text:
                    return job
        return ""

    def match_skills_from_text(self, texts: Sequence[str]) -> List[SkillInfo]:
        """用技能词表从卡面文字中挑出技能名。"""
        vocabulary = list(self.skill_vocabulary)
        for profile in list(self.profiles.values()) + list(
            self.job_profiles.values()
        ):
            for skill in profile.skills:
                if skill.name not in vocabulary:
                    vocabulary.append(skill.name)
        found: List[SkillInfo] = []
        for text in texts:
            compact = text.replace(" ", "")
            for skill_name in vocabulary:
                if not skill_name:
                    continue
                if skill_name in compact and skill_name not in [
                    skill.name for skill in found
                ]:
                    found.append(
                        SkillInfo(name=skill_name, kind="卡面文字", range="")
                    )
        return found

    # ------------------------------------------------------------ 识别主流程

    def identify(
        self,
        context: Any,
        img: Any,
        grid: Any,
        ally: Any,
        round_seen: int,
        *,
        read_card: bool = True,
    ) -> PieceInfo:
        attack_offsets, move_offsets = self.measure_ranges(grid, ally)
        card_text = self.read_card_text(context, img) if read_card else []
        name, name_confidence = self.match_name(card_text)
        job = self.match_job(card_text)

        profile = self.profiles.get(name) if name else None
        if profile is None and job:
            profile = self.job_profiles.get(job)

        skills: List[SkillInfo] = []
        skills_source = "未识别"
        if profile is not None and profile.skills:
            skills = list(profile.skills)
            skills_source = "配置表"
        text_skills = self.match_skills_from_text(card_text)
        if text_skills:
            existing = {skill.name for skill in skills}
            skills.extend(
                skill for skill in text_skills if skill.name not in existing
            )
            skills_source = (
                "配置表+卡面" if skills_source == "配置表" else "卡面文字"
            )

        measured = list(attack_offsets)
        source_bits = ["实测范围"] if measured else []
        if not measured and profile is not None and profile.attack_range:
            measured = list(profile.attack_range)
            source_bits.append("配置范围")
        if not measured:
            observed = self._observed_range(name or job)
            if observed:
                measured = observed
                source_bits.append("历史实测")

        confidence = name_confidence
        if not name:
            # 没有姓名时，靠「范围签名 + 职业」推断可信度
            confidence = 0.45 if measured else 0.25
        elif profile is not None:
            confidence = min(1.0, confidence + 0.1)

        info = PieceInfo(
            name=name,
            job=job or (profile.job if profile else ""),
            skills=skills,
            attack_offsets=sorted(set(measured)),
            move_offsets=sorted(set(move_offsets)),
            range_class=self.classify_range(measured),
            signature=self.range_signature(measured),
            confidence=confidence,
            source="+".join(source_bits) or "卡面/无范围",
            card_text=list(card_text),
            cell=(ally.row, ally.col),
            round_seen=round_seen,
            skills_source=skills_source,
        )
        if not info.name and not info.attack_offsets:
            logger.debug("棋子识别：未匹配姓名，也未测到攻击范围")
        return info

    # ------------------------------------------------------------ 观测持久化

    def _load_observations(self) -> None:
        self._observations = {}
        data = self._read_json(self.observation_path)
        if isinstance(data, dict):
            pieces = data.get("pieces")
            if isinstance(pieces, dict):
                self._observations = {
                    str(key): value
                    for key, value in pieces.items()
                    if isinstance(value, dict)
                }

    def _observed_range(self, key: str) -> List[Offset]:
        if not key:
            return []
        record = self._observations.get(key)
        if not isinstance(record, dict):
            return []
        offsets: List[Offset] = []
        for item in record.get("attack_offsets") or []:
            offset = self._parse_offset(item)
            if offset is not None:
                offsets.append(offset)
        return sorted(set(offsets))

    def remember(self, info: PieceInfo) -> bool:
        """把实测范围合并进观测表；返回是否需要写盘。"""
        if not info.attack_offsets:
            return False
        key = info.name or info.job
        if not key:
            return False
        signature = info.signature
        record = self._observations.setdefault(
            key,
            {
                "name": info.name,
                "job": info.job,
                "attack_offsets": [],
                "signatures": [],
                "range_class": "",
                "last_seen_round": 0,
                "sample_count": 0,
            },
        )
        changed = False
        if record.get("signature") != signature:
            # 同一棋子的攻击范围不应变化；不同签名说明换装/换单位。
            record["signature"] = signature
            record["attack_offsets"] = [list(item) for item in info.attack_offsets]
            record["range_class"] = info.range_class
            changed = True
        signatures = record.setdefault("signatures", [])
        if signature not in signatures:
            signatures.append(signature)
            changed = True
        record["sample_count"] = int(record.get("sample_count") or 0) + 1
        record["last_seen_round"] = max(
            int(record.get("last_seen_round") or 0), info.round_seen
        )
        if info.job and not record.get("job"):
            record["job"] = info.job
            changed = True
        self._dirty = self._dirty or changed
        return changed

    def flush_observations(self) -> None:
        """原子写入观测表（保留 .bak）。"""
        if not self._dirty:
            return
        payload = {
            "_说明": (
                "战斗棋子识别的自动实测结果。attack_offsets 是相对棋子所在格的 "
                "(d_row, d_col) 偏移；可复制到 piece_profiles.json 的 attack_range。"
            ),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pieces": self._observations,
        }
        try:
            os.makedirs(os.path.dirname(self.observation_path), exist_ok=True)
            temp_path = f"{self.observation_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            if os.path.exists(self.observation_path):
                try:
                    os.replace(self.observation_path, f"{self.observation_path}.bak")
                except OSError:
                    pass
            os.replace(temp_path, self.observation_path)
            self._dirty = False
        except OSError as exc:
            logger.warning(f"写入棋子观测表失败: {exc}")

    # ------------------------------------------------------------ 调试导出

    @staticmethod
    def dump_debug(info: PieceInfo, img: Any = None) -> None:
        """把一次识别结果追加到 debug/pieces/<日期>.jsonl。"""
        try:
            os.makedirs(_DEBUG_DIR, exist_ok=True)
            path = os.path.join(
                _DEBUG_DIR, f"{time.strftime('%Y-%m-%d')}.jsonl"
            )
            record = info.to_record()
            record["time"] = time.strftime("%H:%M:%S")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug(f"写入棋子识别调试记录失败: {exc}")
