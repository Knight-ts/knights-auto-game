# -*- coding: utf-8 -*-
"""佣兵加入事件的智能判断与弹窗处理。

真实界面有两种形态：
    1. 地图横幅：“鲍尔和上自的孩子成年了！可以加入佣兵团” —— 名字不在这里，要先点开它
    2. 弹窗：一行“××× 希望加入佣兵团”、一行佣兵名字（如“菲佛瑞”）、一行“年龄15”，
       弹窗左侧还有「?」详情按钮，底部是“取消 / 招募”

名字只出现在弹窗里，所以顺序固定为：
    确保弹窗已打开 → 只在弹窗正文里取名字（夹在“希望加入佣兵团”和“年龄”之间的那一行）
    → 点「?」读角色详情面板（潜力 / 特性，和「孩子命名」同一套识别）
    → 按规则决定接受/拒绝 → 只点对应的按钮 → 用按钮是否消失来校验

判定规则见 utils/mercenary_rules.py：名字（白/黑名单）、爵位下限、橙色特性数量、
「好娃提醒」组合（特性 + 属性门槛）、必需特性、属性门槛，可全部满足 / 任一满足；
没配置的条件不参与，读不到的条件自动跳过。
"""

from __future__ import annotations

import re
import time

from utils import logger
from utils.game_state import year_month
from utils.mercenary_admit import grades_text
from utils.mercenary_profile import close_panel, read_profile
from utils.mercenary_records import append as append_record
from utils.mercenary_rules import DEFAULT_NAME_REGEX, evaluate, load as load_rules, parse_title

PROBE_NODE = "Event_MercenaryDialogButton"
# 任务选项「佣兵智能入队」的开关节点（interface.json 里 override）
SMART_ADMIT_NODE = "Flag_MercenarySmartAdmit"

# 地图横幅：整条横幅都可点，用“孩子成年/加入佣兵”文字的中心点去点
BANNER_ROI = [20, 260, 680, 260]
# 弹窗正文：横幅下方到按钮上方的整块（含“希望加入佣兵团”“名字”“年龄”三行）
BODY_ROI = [20, 220, 680, 600]
# 弹窗底部按钮行（取消在左、招募在右，同一行）
BUTTON_ROI = [90, 770, 540, 180]

# 用来定位名字上下边界的两行固定文案
NAME_ABOVE_ANCHORS = ("希望加入",)
NAME_BELOW_ANCHORS = ("年龄",)

# 兜底区域：弹窗锚点没识别到时才用它限定范围（table/mercenary_rules.json 的 name_roi）
DEFAULT_NAME_ROI = [40, 290, 640, 500]

BUTTON_REGEX = "招募|同意|确认加入|取消|拒绝|取消加入"
LINE_REGEX = "[一-龥A-Za-z·0-9！]{1,20}"

CONFIRM_TEXTS = ("招募", "同意", "确认加入")
CANCEL_TEXTS = ("取消", "拒绝", "取消加入")

DEFAULT_STOPWORDS = [
    "佣兵",
    "加入",
    "确定",
    "取消",
    "招募",
    "银币",
    "年龄",
    "希望",
    "孩子",
    "成年",
    "可以",
    "拒绝",
]

# 弹窗里的固定文案，名字不可能包含它们；用于排除整句、节日提示等误识别
DIALOG_WORDS = (
    "佣兵",
    "兵团",
    "加入",
    "希望",
    "孩子",
    "成年",
    "年龄",
    "银币",
    "取消",
    "招募",
    "拒绝",
    "确定",
    "到来",
    "节日",
    "家族",
)


def _screencap(context):
    return context.tasker.controller.post_screencap().wait().get()


def _ocr(context, img, roi, expected):
    try:
        return context.run_recognition(
            PROBE_NODE,
            img,
            pipeline_override={PROBE_NODE: {"roi": roi, "expected": expected}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"佣兵弹窗 OCR 异常：{exc}")
        return None


def _lines(context, img, roi, expected=None) -> list[tuple[str, list[int]]]:
    """OCR 一个区域，返回 [(文本, [x, y, w, h])]；box 是截图的绝对坐标。"""
    detail = _ocr(context, img, roi, expected or [LINE_REGEX])
    if detail is None:
        return []
    results = list(getattr(detail, "filtered_results", None) or [])
    if not results:
        best = getattr(detail, "best_result", None)
        results = [best] if best is not None else []
    items: list[tuple[str, list[int]]] = []
    for result in results:
        text = (getattr(result, "text", "") or "").strip()
        box = getattr(result, "box", None)
        if not text or box is None or len(box) < 4:
            continue
        items.append((text, [int(value) for value in box[:4]]))
    return items


def _click_box(context, box) -> None:
    x = int(box[0] + box[2] / 2)
    y = int(box[1] + box[3] / 2)
    context.tasker.controller.post_click(x, y).wait()


def _buttons(context, img) -> list[tuple[str, list[int]]]:
    return _lines(context, img, BUTTON_ROI, [BUTTON_REGEX])


def _find_button(context, img, accept: bool):
    wanted = CONFIRM_TEXTS if accept else CANCEL_TEXTS
    for text, box in _buttons(context, img):
        if any(word in text for word in wanted):
            return box, text
    return None, None


def buttons_visible(context, img=None) -> bool:
    img = img if img is not None else _screencap(context)
    return bool(_buttons(context, img))


def dialog_visible(context, img=None) -> bool:
    """弹窗是否已经打开：正文里有“希望加入”，或底部按钮行里有取消/招募。"""
    img = img if img is not None else _screencap(context)
    texts = [text for text, _ in _lines(context, img, BODY_ROI)]
    if any("希望加入" in text for text in texts):
        return True
    return buttons_visible(context, img)


def _open_banner(context, img) -> bool:
    """点地图上的“孩子成年了！可以加入佣兵团”横幅，把弹窗打开。"""
    for text in ("孩子成年", "加入佣兵"):
        detail = _ocr(context, img, BANNER_ROI, [text])
        if detail is None or not detail.hit or detail.best_result is None:
            continue
        box = [int(value) for value in list(detail.best_result.box)[:4]]
        _click_box(context, box)
        time.sleep(0.8)
        return True
    return False


def _ensure_dialog(context, img) -> tuple[object, bool]:
    """确保佣兵弹窗已经打开，返回 (最新截图, 是否已打开)。"""
    if dialog_visible(context, img):
        return img, True
    for attempt in range(3):
        if not _open_banner(context, img):
            break
        img = _screencap(context)
        if dialog_visible(context, img):
            return img, True
        logger.info(f"点了横幅但佣兵弹窗还没出现（第 {attempt + 1} 次）")
    return img, False


def dismiss_join(context, accept: bool, img=None) -> bool:
    """只点“本次决定”的按钮，没关掉就在同一个按钮上重试，绝不改点另一个。"""
    img = img if img is not None else _screencap(context)
    if not dialog_visible(context, img):
        img, opened = _ensure_dialog(context, img)
        if not opened:
            logger.warning("佣兵弹窗没能打开，点不到「取消 / 招募」按钮")
            return False
    for attempt in range(3):
        box, text = _find_button(context, img, accept)
        if box is None:
            logger.warning(f"佣兵弹窗里没找到「{CONFIRM_TEXTS[0] if accept else CANCEL_TEXTS[0]}」按钮")
            break
        logger.info(f"佣兵加入：点击「{text}」（第 {attempt + 1} 次）")
        _click_box(context, box)
        time.sleep(0.7)
        img = _screencap(context)
        if not buttons_visible(context, img):
            return True

    texts = [text for text, _ in _lines(context, img, BODY_ROI)]
    logger.warning(f"佣兵弹窗未关闭，当前 OCR 文本：{texts}")
    return False


def _dump_screenshot(img, tag: str = "") -> None:
    """名字没读到时留一张弹窗截图，方便事后校准 ROI。"""
    try:
        if img is None or not hasattr(img, "shape") or len(img.shape) != 3:
            return
        import cv2
        from pathlib import Path

        folder = (Path("debug") / "mercenary").resolve()
        folder.mkdir(parents=True, exist_ok=True)
        data = img
        if not data.flags["C_CONTIGUOUS"]:
            data = data.copy()
        if data.dtype != "uint8":
            data = data.clip(0, 255).astype("uint8")
        ok, buf = cv2.imencode(".png", data)
        if not ok:
            logger.warning("佣兵弹窗截图编码失败，跳过保存")
            return
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        path = folder / f"{stamp}{('_' + tag) if tag else ''}.png"
        path.write_bytes(buf.tobytes())
        logger.info(f"佣兵弹窗截图已保存：{path}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"保存佣兵弹窗截图失败：{exc}")


def _in_roi(box, roi) -> bool:
    left, top, width, height = [int(value) for value in list(roi)[:4]]
    return (
        box[0] >= left - 4
        and box[0] + box[2] <= left + width + 4
        and box[1] >= top - 4
        and box[1] + box[3] <= top + height + 4
    )


def _prefix_name(lines, pattern, plausible) -> tuple[str, str]:
    """名字和“希望加入”被 OCR 连成一行时，取锚点之前的部分。"""
    for text, _box in lines:
        for anchor in NAME_ABOVE_ANCHORS:
            index = text.find(anchor)
            if index <= 0:
                continue
            for candidate in pattern.findall(text[:index].strip()):
                if plausible(candidate):
                    return candidate, text
    return "", ""


def _recognize_name(context, screenshot, rules: dict) -> tuple[str, str]:
    """从弹窗正文里取佣兵名字；失败返回空字符串。

    名字夹在“希望加入佣兵团”和“年龄NN”两行之间，用这两行做上下边界，
    就不会读到上方的节日提示或横幅文案。
    """
    lines = _lines(context, screenshot, BODY_ROI)
    if not lines:
        logger.warning("佣兵弹窗正文里没有可用的 OCR 文本，可能弹窗没打开")
        _dump_screenshot(screenshot, "empty")
        return "", ""

    name_pattern = re.compile(rules.get("name_regex") or DEFAULT_NAME_REGEX)
    stopwords = set(rules.get("stopwords") or []) | set(DEFAULT_STOPWORDS)

    def plausible(text: str) -> bool:
        if not name_pattern.fullmatch(text):
            return False
        # 名字至少含一个汉字；纯数字/纯字母是资源数字或等级，不是名字
        if not any("\u4e00" <= char <= "\u9fff" for char in text):
            return False
        if any(word in text for word in stopwords):
            return False
        if any(word in text for word in DIALOG_WORDS):
            return False
        return True

    def top(box) -> int:
        return box[1]

    def bottom(box) -> int:
        return box[1] + box[3]

    upper_edge = max(
        (bottom(box) for text, box in lines if any(key in text for key in NAME_ABOVE_ANCHORS)),
        default=None,
    )
    lower_edge = min(
        (top(box) for text, box in lines if any(key in text for key in NAME_BELOW_ANCHORS)),
        default=None,
    )

    candidates = [(text, box) for text, box in lines if plausible(text)]

    def inside_band(box) -> bool:
        if upper_edge is not None and top(box) < upper_edge - 6:
            return False
        if lower_edge is not None and bottom(box) > lower_edge + 6:
            return False
        return True

    band = [item for item in candidates if inside_band(item[1])]

    if not band and candidates and (upper_edge is not None or lower_edge is not None):
        # 弹窗结构和预期不同（名字不在两行锚点之间）：取最贴近锚点的那一行
        def distance(box) -> int:
            if upper_edge is not None and bottom(box) < upper_edge:
                return upper_edge - bottom(box)
            if lower_edge is not None and top(box) > lower_edge:
                return top(box) - lower_edge
            return 0

        near = sorted(
            ((distance(item[1]), item) for item in candidates if distance(item[1]) <= 140),
            key=lambda pair: (pair[0], -pair[1][1][1]),
        )
        if near:
            band = [near[0][1]]
            logger.info(
                f"佣兵名字不在锚点之间，改取最贴近锚点的一行：{near[0][1][0]}"
                f"（距离 {near[0][0]}）"
            )

    if not band:
        # 弹窗结构和预期不同，或锚点没读到：退回手工校准区域，并记录诊断信息
        roi = rules.get("name_roi") or DEFAULT_NAME_ROI
        band = [item for item in candidates if _in_roi(item[1], roi)]
        if band:
            logger.warning(
                f"佣兵名字按 name_roi 兜底取，候选：{[(text, box[1]) for text, box in band]}；"
                f"弹窗 OCR 行：{[(text, box[1]) for text, box in lines]}"
            )

    if band:
        # 名字紧贴“年龄”行，取带内最靠下的一行
        text, box = max(band, key=lambda item: bottom(item[1]))
        logger.info(
            f"佣兵名字取自弹窗第 {box[1]} 行：{text}"
            f"（边界 {upper_edge if upper_edge is not None else '-'}~"
            f"{lower_edge if lower_edge is not None else '-'}）"
        )
        return text, text

    name, raw = _prefix_name(lines, name_pattern, plausible)
    if name:
        logger.info(f"佣兵名字从合并行里拆出：{name}（原文 {raw}）")
        return name, raw

    logger.warning(f"佣兵名字未识别，弹窗 OCR 行：{[(text, box[1]) for text, box in lines]}")
    _dump_screenshot(screenshot, "noname")
    return "", " | ".join(text for text, _box in lines)


def _smart_admit(context) -> bool:
    """任务选项「佣兵智能入队」是否打开（打开就按特性/爵位判断）。"""
    try:
        node = context.get_node_data(SMART_ADMIT_NODE) or {}
    except Exception:  # noqa: BLE001
        return False
    return bool(node.get("enabled"))


def _need_detail(rules: dict, smart_admit: bool = False) -> bool:
    """要不要点「?」读角色详情（爵位 / 特性 / 潜力）。"""
    mode = rules.get("read_detail") or "auto"
    if mode == "never":
        return False
    if smart_admit or mode == "always":
        return True
    return (
        rules.get("min_orange", 0) > 0
        or rules.get("title_min", "无") != "无"
        or bool(rules.get("combos"))
        or bool(rules.get("features"))
        or bool(rules.get("potential_min"))
    )


def _feature_swipe() -> int:
    try:
        from utils.child_perf import get_profile

        return int(get_profile().get("feature_max_swipe") or 2)
    except Exception:  # noqa: BLE001
        return 2


def _back_to_dialog(context) -> bool:
    """从详情面板回到加入弹窗；回不去就补按返回键，返回最终是否还看得到弹窗。"""
    for attempt in range(3):
        img = _screencap(context)
        if dialog_visible(context, img):
            return True
        if attempt < 2:
            logger.warning("退出佣兵详情后没看到加入弹窗，补按返回键")
            try:
                context.tasker.controller.post_click_key(4).wait()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
    return False


def handle_join(context, screenshot) -> tuple[str, bool]:
    """处理一次佣兵加入事件，返回 (名字, 是否接受)。"""
    rules = load_rules()
    smart_admit = _smart_admit(context)

    img, opened = _ensure_dialog(context, screenshot)
    name, raw = _recognize_name(context, img, rules)

    profile = {
        "name": name,
        "title": parse_title(name) if name else None,
        "orange": None,
        "features": [],
        "potential": {},
        "grades": {},
        "detail_ok": False,
    }

    if name and _need_detail(rules, smart_admit):
        logger.info("佣兵智能入队：打开详情面板读取特性 / 爵位 / 潜力")
        info = read_profile(context, name, max_swipe=_feature_swipe())
        if info.get("panel_ok"):
            profile["detail_ok"] = True
            profile["orange"] = info.get("orange")
            profile["features"] = info.get("features") or []
            profile["potential"] = info.get("potential") or {}
            profile["grades"] = info.get("grades") or {}
            if info.get("title"):
                profile["title"] = info["title"]
        close_panel(context)
        back_ok = _back_to_dialog(context)
    else:
        back_ok = True

    verdict = evaluate(profile, rules)
    accept = verdict["accept"]

    closed = dismiss_join(context, accept) if back_ok else False

    notes = []
    if raw and raw != name:
        notes.append(raw[:60])
    notes.append(verdict["note"][:160])
    if not opened:
        notes.append("弹窗未打开")
    if not back_ok:
        notes.append("详情面板未返回")
    if not closed:
        notes.append("弹窗未关闭")

    year, month = year_month()
    try:
        append_record(
            {
                "游戏年": year or "",
                "游戏月": month or "",
                "佣兵名字": name or "(未识别)",
                "结果": "接受" if accept else "拒绝",
                "匹配规则": verdict["rule"],
                "爵位": profile.get("title") or "",
                "橙色特性": "" if profile.get("orange") is None else profile["orange"],
                "属性": grades_text(profile["potential"]) if profile.get("potential") else "",
                "特性": "、".join(profile.get("features") or [])[:80],
                "备注": "；".join(notes),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写入佣兵记录失败：{exc}")

    logger.info(
        f"佣兵加入：名字={name or '未识别'}，规则={verdict['rule']}，"
        f"爵位={profile.get('title') or '未识别'}，"
        f"橙色特性={profile['orange'] if profile['orange'] is not None else '未识别'}，"
        f"{'接受' if accept else '拒绝'}，弹窗{'已关闭' if closed else '未关闭'}"
    )
    return name, accept
