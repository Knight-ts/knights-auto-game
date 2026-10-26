from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
from maa.define import Rect
from maa.context import Context
import os
import json
import re

from utils import logger, task_rules


@dataclass
class HudTaskInfo:
    """HUD动态识别出的任务信息"""

    task_type: str = ""  # 任务图标类型：斩杀任务、生存任务等
    task_name: str = ""  # 任务名称
    task_level: str = ""  # 敌人等级
    task_description: str = ""  # 任务描述
    reward: str = ""  # 奖励
    accept_button_box: Optional[Rect] = None  # 接受按钮位置
    action_text: str = ""  # 接受按钮文字：接受/放弃
    icon_box: Optional[Rect] = None  # 图标位置


class TaskBlacklist:
    """任务黑名单单例类"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TaskBlacklist, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # 使用 getattr 避免 _initialized 不存在的 AttributeError
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        cwd_dir = os.getcwd()
        logger.debug(f"TaskBlacklist初始化，当前工作目录: {cwd_dir}")

        self.blacklist_file = os.path.join(cwd_dir, "table", "task_blacklist.json")
        self._blacklist_cache = self._load_blacklist()
        self._mtime = self._file_mtime()
        logger.debug(f"黑名单文件路径: {self.blacklist_file}")

    def _file_mtime(self) -> float:
        try:
            return os.path.getmtime(self.blacklist_file)
        except OSError:
            return -1.0

    def _reload_if_changed(self) -> None:
        """网页上「一键拉黑」直接改文件，这里按 mtime 自动重载，不必重启。"""
        mtime = self._file_mtime()
        if mtime == self._mtime:
            return
        self._mtime = mtime
        self._blacklist_cache = self._load_blacklist()
        logger.info(f"检测到黑名单文件变化，已重新载入（{len(self._blacklist_cache)} 个）")

    def _load_blacklist(self):
        try:
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    blacklist = set(data.get("blacklist", []))
                    logger.info(f"载入黑名单成功，共 {len(blacklist)} 个任务")
                    return blacklist
            else:
                logger.info(f"黑名单文件不存在")
                return set()
        except Exception as e:
            logger.error(f"载入黑名单失败: {e}")
            return set()

    def add_to_blacklist(self, task_names: str):
        """动态添加任务名到黑名单（同时持久化到文件）"""
        if not task_names:
            return
        self._reload_if_changed()
        names = [
            name.strip()
            for name in task_names.replace("，", ",").split(",")
            if name.strip()
        ]
        if not names:
            return

        for name in names:
            self._blacklist_cache.add(name)
        logger.info(
            f"动态添加黑名单任务: {names}，当前黑名单共 {len(self._blacklist_cache)} 个"
        )

        try:
            with open(self.blacklist_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"blacklist": list(self._blacklist_cache)},
                    f,
                    ensure_ascii=False,
                    indent=4,
                )
            self._mtime = self._file_mtime()
            logger.info(f"黑名单已保存到文件: {self.blacklist_file}")
        except Exception as e:
            logger.error(f"保存黑名单到文件失败: {e}")

    def is_in_blacklist(self, task_name: str, city: str = "") -> bool:
        """检查任务是否在黑名单中（全局 + 城市名单，文件改动自动生效）。"""
        self._reload_if_changed()
        if task_name in self._blacklist_cache:
            return True
        return task_name in task_rules.city_rule(city)["blacklist"]


class TaskHudRecognizer:
    """基于任务图标位置的动态HUD识别器"""

    # 任务类型黑名单（硬编码，不加入文件）
    TASK_TYPE_BLACKLIST = {"保护任务", "保护任务1"}
    # 部分“混战任务”图标实际是采购/送货委托，详情页没有“进入战斗”。
    # 只按描述排除这些非战斗任务，保留真正的混战关卡。
    NON_COMBAT_DESCRIPTION_KEYWORDS = {
        "购买",
        "杂货铺",
        "送往",
        "送到",
    }

    # 7个任务图标文件
    TASK_ICONS = [
        "Task/斩杀任务.png",
        "Task/生存任务.png",
        "Task/清场任务.png",
        "Task/混战任务.png",
        "Task/保护任务.png",
        "Task/拦截任务.png",
        "Task/保护任务1.png",
    ]

    # 4个核心区域的动态offset配置（相对于图标box的左上角）
    TASK_VALUE_OFFSETS = {
        "task_name": {"x_offset": 75, "y_offset": 0, "width": 350, "height": 50},
        "task_level": {"x_offset": 530, "y_offset": 0, "width": 140, "height": 90},
        "task_description": {
            "x_offset": 80,
            "y_offset": 100,
            "width": 570,
            "height": 110,
        },
        "reward": {"x_offset": 85, "y_offset": 255, "width": 350, "height": 120},
        "accept_button": {
            "x_offset": 480,
            "y_offset": 250,
            "width": 180,
            "height": 120,
        },
    }

    def __init__(self):
        self.blacklist = TaskBlacklist()

    def _calc_dynamic_roi(
        self, icon_box, offset_config: Dict[str, int], image_shape=None
    ) -> Optional[List[int]]:
        """根据图标box和offset配置计算动态ROI

        Args:
            icon_box: 图标的位置 [x, y, w, h] 或 Rect对象
            offset_config: offset配置，包含x_offset, y_offset, width, height
            image_shape: 截图尺寸（numpy 的 shape），用于把 ROI 裁进画面

        Returns:
            [x, y, width, height] 格式的ROI；区域完全在画面外时返回 None。
        """
        # 支持列表 [x, y, w, h] 或 Rect对象
        if isinstance(icon_box, (list, tuple)):
            bx, by_ = icon_box[0], icon_box[1]
        else:
            bx, by_ = icon_box.x, icon_box.y

        roi = [
            bx + offset_config["x_offset"],
            by_ + offset_config["y_offset"],
            offset_config["width"],
            offset_config["height"],
        ]
        return self._clamp_roi(roi, image_shape)

    @staticmethod
    def _clamp_roi(roi: List[int], image_shape) -> Optional[List[int]]:
        """把 ROI 裁剪到截图范围内；完全越界时返回 None。

        任务图标滚到屏幕底部时，「接受按钮」等相对偏移区域会落到画面外，
        MAA 的识别节点会因为 ROI 非法而启动失败、返回 None。这里提前裁掉，
        只识别画面上真实存在的那部分。
        """
        x, y, w, h = (int(value) for value in roi[:4])
        if image_shape is not None and len(image_shape) >= 2:
            height, width = int(image_shape[0]), int(image_shape[1])
            x2, y2 = min(width, x + w), min(height, y + h)
            x, y = max(0, x), max(0, y)
            w, h = x2 - x, y2 - y
        if w <= 0 or h <= 0:
            return None
        return [x, y, w, h]

    def _recognize(self, context: Context, entry: str, screenshot, roi, override=None):
        """执行一次识别，ROI 不可用或识别没启动起来时返回 None。

        ``run_recognition`` 只在识别流程未能启动时返回 None（节点 disabled、
        图片为空、ROI 非法等），调用方必须容忍这种情况，否则整轮月度循环会被
        一次越界识别打断。
        """
        if screenshot is None or roi is None:
            return None
        payload = {"roi": list(roi)}
        if override:
            payload.update(override)
        try:
            return context.run_recognition(
                entry, screenshot, pipeline_override={entry: payload}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{entry} 识别异常：{exc}")
            return None

    def scan_task_icon(
        self, context: Context, screenshot, template_path: str
    ) -> Optional[Rect]:
        """在截图中扫描特定任务图标

        Args:
            context: MAA上下文
            screenshot: 截图
            template_path: 图标模板路径

        Returns:
            找到则返回图标box，否则返回None
        """
        if screenshot is None:
            return None
        try:
            reco_detail = context.run_recognition(
                "Hud_TaskIconMatch",
                screenshot,
                pipeline_override={
                    "Hud_TaskIconMatch": {
                        "template": template_path,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"任务图标匹配异常（{template_path}）：{exc}")
            return None

        if reco_detail and reco_detail.hit and reco_detail.best_result:
            return reco_detail.best_result.box
        return None

    def recognize_all_task_icons(self, context: Context, screenshot) -> List[tuple]:
        """遍历所有任务图标，返回匹配到的图标信息

        Returns:
            [(task_type, icon_box), ...] 匹配到的图标列表
        """
        matched = []
        for icon_template in self.TASK_ICONS:
            task_type = icon_template.replace("Task/", "").replace(".png", "")
            icon_box = self.scan_task_icon(context, screenshot, icon_template)
            if icon_box:
                matched.append((task_type, icon_box))
        return matched

    def recognize_tasks(self, context: Context, screenshot) -> List[HudTaskInfo]:
        """识别当前任务板内可见的全部任务，不做类型/等级过滤。"""
        task_list: List[HudTaskInfo] = []
        for task_type, icon_box in self.recognize_all_task_icons(context, screenshot):
            task_list.append(
                self.recognize_task_info_by_icon(
                    context, screenshot, task_type, icon_box
                )
            )
        return task_list

    @classmethod
    def non_combat_keyword(cls, task: HudTaskInfo) -> Optional[str]:
        """返回命中的非战斗描述关键词；没有命中时返回 None。"""
        return next(
            (
                keyword
                for keyword in cls.NON_COMBAT_DESCRIPTION_KEYWORDS
                if keyword in task.task_description
            ),
            None,
        )

    def recognize_task_info_by_icon(
        self, context: Context, screenshot, task_type: str, icon_box: Rect
    ) -> HudTaskInfo:
        """根据任务图标位置识别任务的5个核心区域信息

        Args:
            context: MAA上下文
            screenshot: 截图
            task_type: 任务类型名称
            icon_box: 图标位置

        Returns:
            HudTaskInfo对象
        """
        task_info = HudTaskInfo(task_type=task_type, icon_box=icon_box)
        image_shape = getattr(screenshot, "shape", None)

        # 识别任务名称
        roi = self._calc_dynamic_roi(
            icon_box, self.TASK_VALUE_OFFSETS["task_name"], image_shape
        )
        reco = self._recognize(context, "Hud_TaskNameOcr", screenshot, roi)
        if reco and reco.hit and reco.best_result:
            task_info.task_name = reco.best_result.text.strip()

        # 识别敌人等级
        roi = self._calc_dynamic_roi(
            icon_box, self.TASK_VALUE_OFFSETS["task_level"], image_shape
        )
        reco = self._recognize(context, "Hud_TaskLevelOcr", screenshot, roi)
        if reco and reco.hit and reco.best_result:
            task_info.task_level = reco.best_result.text.strip()

        # 识别任务描述
        roi = self._calc_dynamic_roi(
            icon_box, self.TASK_VALUE_OFFSETS["task_description"], image_shape
        )
        reco = self._recognize(context, "Hud_TaskDescOcr", screenshot, roi)
        if reco and reco.hit and reco.all_results:
            # 合并多行描述
            texts = [r.text.strip() for r in reco.all_results]
            task_info.task_description = "".join(texts)

        # 识别接受按钮位置（图标靠近屏幕底部时该区域可能完全在画面外）
        roi = self._calc_dynamic_roi(
            icon_box, self.TASK_VALUE_OFFSETS["accept_button"], image_shape
        )
        reco = self._recognize(context, "Hud_AcceptButtonOcr", screenshot, roi)
        if reco and reco.hit and reco.best_result:
            task_info.accept_button_box = reco.best_result.box
            task_info.action_text = reco.best_result.text.strip()

        return task_info

    def evaluate_task(
        self,
        task: HudTaskInfo,
        *,
        min_level: int = 0,
        max_level: int = 120,
        blacklist: Optional[set] = None,
        city: str = "",
    ) -> Tuple[bool, str]:
        """判断单条任务是否可接，返回 (可接?, 原因)。

        网页任务列表、接取逻辑共用同一套规则：类型/非战斗/黑名单/等级区间。
        """
        if not task.task_name:
            return False, "未识别到任务名"
        if task.task_type in self.TASK_TYPE_BLACKLIST:
            return False, f"类型排除（{task.task_type}）"
        matched_keyword = self.non_combat_keyword(task)
        if matched_keyword is not None:
            return False, f"非战斗任务（{matched_keyword}）"
        names = (
            set(blacklist)
            if blacklist is not None
            else set(task_rules.effective_blacklist(city))
        )
        if task.task_name in names:
            return False, "黑名单"
        level = self._parse_level(task.task_level)
        if level < int(min_level or 0):
            return False, f"低于最低等级 {int(min_level)}"
        if max_level and level > int(max_level):
            return False, f"高于最高等级 {int(max_level)}"
        return True, "可接取"

    def scan_pool(
        self,
        context: Context,
        screenshot,
        *,
        min_level: int = 0,
        max_level: int = 120,
        city: str = "",
        blacklist: Optional[set] = None,
    ) -> Tuple[List[dict], List[HudTaskInfo]]:
        """扫描当前任务板，返回 (任务列表快照, 可接任务列表)。

        快照给网页「任务接取」面板展示，带每条任务的可接状态和原因。
        """
        tasks = self.recognize_tasks(context, screenshot)
        names = (
            set(blacklist)
            if blacklist is not None
            else set(task_rules.effective_blacklist(city))
        )
        items: List[dict] = []
        eligible: List[HudTaskInfo] = []
        for task in tasks:
            ok, reason = self.evaluate_task(
                task,
                min_level=min_level,
                max_level=max_level,
                blacklist=names,
                city=city,
            )
            items.append(
                {
                    "name": task.task_name,
                    "type": task.task_type,
                    "level": task.task_level,
                    "description": task.task_description,
                    "eligible": ok,
                    "reason": reason,
                }
            )
            if ok:
                eligible.append(task)
        logger.debug(
            f"任务板扫描：{len(tasks)} 条，可接 {len(eligible)} 条"
            f"（城市={city or '未知'}，等级 {min_level}-{max_level}，"
            f"黑名单 {len(names)} 个）"
        )
        return items, eligible

    def pick_best(self, tasks: List[HudTaskInfo]) -> Optional[HudTaskInfo]:
        """从可接任务里挑等级最高的一条（同等级取识别顺序靠前的）。"""
        if not tasks:
            return None
        return max(tasks, key=lambda item: self._parse_level(item.task_level))

    def recognize_and_get_best_task(
        self,
        context: Context,
        screenshot,
        max_level: int = None,
        min_level: int = 0,
        city: str = "",
        blacklist: Optional[set] = None,
    ) -> Optional[HudTaskInfo]:
        """识别任务板，返回等级最高的可接任务。

        ``max_level`` / ``min_level`` / ``city`` / ``blacklist`` 由调用方按
        城市规则传入；不传时使用 task_accept_rules.json 里该城市的规则。
        """
        items, eligible = self.scan_pool(
            context,
            screenshot,
            min_level=min_level,
            max_level=(120 if max_level is None else max_level),
            city=city,
            blacklist=blacklist,
        )
        if not items:
            logger.info("未找到任何任务图标")
            return None
        if not eligible:
            logger.info(
                f"任务板 {len(items)} 条均不符合条件（城市="
                f"{city or '未知'}，等级 {min_level}-{max_level}）"
            )
            return None

        best = self.pick_best(eligible)
        level = self._parse_level(best.task_level)
        logger.info(
            f"选择任务: {best.task_name} | {best.task_type} | {level}级 | "
            f"{best.task_description}"
        )
        return best

    def _parse_level(self, level_str: str) -> int:
        """从等级字符串中提取数字"""
        if not level_str:
            return 0
        # "敌人等级：75" -> 75

        match = re.search(r"\d+", level_str)
        return int(match.group()) if match else 0
