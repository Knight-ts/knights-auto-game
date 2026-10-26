# -*- coding: utf-8 -*-
"""佣兵详情面板：点弹窗里的「?」读爵位 / 特性，并数橙色血统特性。

面板就是「角色信息」面板（左侧 潜力 / 血统 / 特性 三栏），和佣兵生娃流程用的是同一个，
所以特性识别直接复用 ``action.zshg.role_utils.extract_features``。

橙色特性按 ``table/各国家特性表.json`` 的等级判断（高级橙 / 普通橙 都算橙色），
和「好娃提醒」用的是同一份特性表。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from utils import logger
from utils.mercenary_admit import grades_of
from utils.mercenary_rules import parse_title

ROOT = Path(__file__).resolve().parents[2]
BLOODLINE_TABLE = ROOT / "table" / "各国家特性表.json"

# 弹窗左侧「?」详情按钮。720x1280 固定布局，4 张真实截图实测中心都在 (250,521)；
# 模板匹配只是保险（DetailButton.png 原图是 1.0 比例，和游戏里的 1.15 比例差一点，
# 匹配不到就退回固定坐标）。
DETAIL_BUTTON_ROI = [165, 450, 180, 140]
DETAIL_BUTTON_POINT = (250, 521)
DETAIL_TEMPLATE_NODE = "PannelChildInfoButton"

# 角色信息面板左侧栏目（潜力 / 血统 / 特性），用于判断面板是否打开
PANEL_ANCHOR_ROI = [15, 462, 206, 797]
PANEL_ANCHOR_REGEX = "潜力|血统|特性"
# 面板顶部（头像 / 名字 / 称号），尽力而为地再确认一次爵位
PANEL_HEADER_ROI = [20, 70, 680, 210]

_level_cache: dict | None = None


def load_level_map() -> dict:
    """特性名 -> 等级（高级橙 / 普通橙 / 蓝特性 / 负面 …）。"""
    global _level_cache
    if _level_cache is not None:
        return _level_cache
    mapping: dict[str, str] = {}
    try:
        data = json.loads(BLOODLINE_TABLE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"读取血统特性表失败：{exc}")
        _level_cache = mapping
        return mapping
    for group in data.get("bloodlines") or []:
        for feature in group.get("features") or []:
            name = str(feature.get("name") or "").strip()
            level = str(feature.get("level") or "").strip()
            if name and level:
                mapping[name] = level
    _level_cache = mapping
    return mapping


def _match_level(name: str, mapping: dict) -> str | None:
    """特性名 -> 等级，容忍 OCR 少字/多字。"""
    cleaned = (name or "").replace(" ", "").replace("·", "").strip()
    if not cleaned:
        return None
    if cleaned in mapping:
        return mapping[cleaned]
    for key, level in mapping.items():
        short_key = key.replace(" ", "").replace("·", "")
        if len(short_key) >= 3 and (short_key in cleaned or cleaned in short_key):
            return level
    return None


def count_orange(features: list[str], mapping: dict | None = None) -> tuple[int, list[str]]:
    """数橙色（高级橙 / 普通橙）血统特性。返回 (数量, 橙色特性名列表)。"""
    mapping = mapping if mapping is not None else load_level_map()
    orange: list[str] = []
    for name in features:
        level = _match_level(name, mapping) or ""
        if "橙" in level:
            orange.append(name)
    return len(orange), orange


# ------------------------------------------------------------------ 面板操作


def _screencap(context):
    return context.tasker.controller.post_screencap().wait().get()


def _find_detail_button(context, img):
    try:
        detail = context.run_recognition(
            DETAIL_TEMPLATE_NODE,
            img,
            pipeline_override={
                DETAIL_TEMPLATE_NODE: {"roi": DETAIL_BUTTON_ROI, "threshold": 0.7}
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵详情按钮模板匹配失败：{exc}")
        return None
    if detail is not None and detail.hit and detail.best_result is not None:
        return [int(value) for value in list(detail.best_result.box)[:4]]
    return None


def panel_visible(context, img=None) -> bool:
    img = img if img is not None else _screencap(context)
    try:
        detail = context.run_recognition(
            "Event_MercenaryDialogButton",
            img,
            pipeline_override={
                "Event_MercenaryDialogButton": {
                    "roi": PANEL_ANCHOR_ROI,
                    "expected": [PANEL_ANCHOR_REGEX],
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵详情面板识别异常：{exc}")
        return False
    return bool(detail is not None and detail.hit)


def _click(context, x: int, y: int) -> None:
    context.tasker.controller.post_click(int(x), int(y)).wait()


def _open_panel(context, img=None) -> bool:
    """点「?」打开角色信息面板（最多点两次，避免误触别的地方）。"""
    img = img if img is not None else _screencap(context)
    box = _find_detail_button(context, img)
    if box is not None:
        x, y = int(box[0] + box[2] / 2), int(box[1] + box[3] / 2)
        logger.info(f"佣兵详情：点模板命中的详情按钮 ({x},{y})")
    else:
        x, y = DETAIL_BUTTON_POINT
        logger.info(f"佣兵详情：模板没命中，点固定位置 ({x},{y})")

    for attempt in range(2):
        _click(context, x, y)
        for _ in range(6):
            time.sleep(0.4)
            if panel_visible(context):
                return True
        logger.warning(f"点了详情按钮但面板没出现（第 {attempt + 1} 次）")
    return False


def read_panel_text(context) -> str:
    """面板顶部文字（名字 / 称号），用来兜底认爵位。"""
    img = _screencap(context)
    try:
        detail = context.run_recognition(
            "Event_MercenaryDialogButton",
            img,
            pipeline_override={
                "Event_MercenaryDialogButton": {
                    "roi": PANEL_HEADER_ROI,
                    "expected": ["[一-龥·A-Za-z0-9]{1,20}"],
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵面板顶部识别异常：{exc}")
        return ""
    if detail is None or not getattr(detail, "hit", False):
        return ""
    texts = [
        (getattr(item, "text", "") or "").strip()
        for item in (getattr(detail, "filtered_results", None) or [])
    ]
    return " ".join(text for text in texts if text)


def close_panel(context) -> None:
    """返回键退出面板，回到加入弹窗。"""
    try:
        context.run_task("BackButton_500ms")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵详情返回失败：{exc}")
        try:
            context.tasker.controller.post_click_key(4).wait()
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.4)


def read_profile(context, name: str, max_swipe: int = 2) -> dict:
    """打开详情面板读属性（和「孩子命名」同一套识别流程）。

    返回 {"panel_ok": bool, "features": [特性名], "orange": int|None,
          "orange_names": [橙色特性名], "potential": {属性: 数值},
          "grades": {属性: 等级}, "title": str|None}
        orange / potential 为空表示没读到（规则里按“未启用”跳过）。
    """
    result = {
        "panel_ok": False,
        "features": [],
        "orange": None,
        "orange_names": [],
        "potential": {},
        "grades": {},
        "title": None,
    }
    if not _open_panel(context):
        logger.warning("佣兵详情面板打不开，跳过爵位/特性判断")
        try:  # 留一张图方便校准「?」位置（延迟导入，避免和 utils.mercenary 循环引用）
            from utils.mercenary import _dump_screenshot

            _dump_screenshot(_screencap(context), "nopanel")
        except Exception:  # noqa: BLE001
            pass
        return result

    result["panel_ok"] = True

    # 1. 面板顶部：名字/称号（要滑动的话顶部会滚走，先读）
    header = read_panel_text(context)

    # 2. 潜力六维（面板最上面一块）
    try:
        from action.zshg.role_utils import extract_potential

        potential = extract_potential(context)
        values = dict(getattr(potential, "values", None) or {})
        if not values or all(abs(float(value)) <= 1e-6 for value in values.values()):
            # 面板可能停在下面（上次识别特性时滑过），回滚一屏再读一次
            try:
                context.run_task("PropertyPanelSwipeUp")
                time.sleep(0.3)
                potential = extract_potential(context)
                values = dict(getattr(potential, "values", None) or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"佣兵潜力回滚重读失败：{exc}")
        if values and any(abs(float(value)) > 1e-6 for value in values.values()):
            result["potential"] = values
            result["grades"] = grades_of(values)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵潜力识别失败：{exc}")

    # 3. 人物特性（可能要下滑）
    features: list[str] = []
    try:
        from action.zshg.role_utils import extract_features

        feature_objects = extract_features(context, max_swipe_count=max_swipe) or []
        features = [
            str(getattr(item, "name", "")).strip()
            for item in feature_objects
            if str(getattr(item, "name", "")).strip()
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵特性识别失败：{exc}")

    result["features"] = features
    if features:
        count, names = count_orange(features)
        result["orange"] = count
        result["orange_names"] = names

    title = parse_title(name, header)
    result["title"] = title
    logger.info(
        f"佣兵详情：爵位={title or '未识别'}，特性 {len(features)} 个"
        f"（橙色 {result['orange'] if result['orange'] is not None else '未识别'}），"
        f"潜力 {result['grades'] or '未识别'}"
    )
    return result
