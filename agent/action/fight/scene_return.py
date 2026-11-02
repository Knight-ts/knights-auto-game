# -*- coding: utf-8 -*-
"""未知界面处理：先点几次安全位置，画面真的没变化才记录，再按记录好的路线返回。

为什么这样设计：
    - 认不出的画面不能直接盲点：乱点可能买到东西、花掉金币、跳过选择。
      所以只点三个「大概率安全」的位置：屏幕下方（确定/继续）、安卓返回键、
      屏幕中心，每点一步都重新截图比对，画面一变就立刻停手交回上层识别；
    - 只比日志没用：卡住的画面长什么样、点了哪里、是不是同一个画面反复出现，
      必须把截图和画面指纹一起留下，所以记录到 ``debug/unknown``；
    - 「返回逻辑」不是写死在代码里的一句话，而是 ``table/return_routes.json``
      里每个场景 / 每条记录一条路线。网页给记录选好返回方式后，agent 下次
      遇到同一画面会直接照那几步执行，而不是重新试错；
    - 记录里带调用栈（``trace``），网页点一下就能跳到出现未知画面的源代码行。

画面「有没有变化」用 32×32 灰度指纹的平均绝对差判断：静态弹窗基本是 0，
时钟 / 人物走动通常小于阈值；阈值在 ``table/return_routes.json`` 的
``settings.changeThreshold`` 里调。
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from utils import logger, unknown_store

ROOT = unknown_store.project_root()
STORE = unknown_store.Store(ROOT)

SIGNATURE_SIZE = (32, 32)
_OWN_FILES = ("scene_return.py", "unknown_store.py")


# ---------------------------------------------------------------- 画面指纹


def _gray(img: Any) -> np.ndarray:
    if img is None:
        return np.zeros(SIGNATURE_SIZE, dtype=np.uint8)
    if getattr(img, "ndim", 0) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    return cv2.resize(gray, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA)


def signature(img: Any) -> str:
    """32×32 灰度指纹（hex）；同一画面每次算出来都一样，便于 dedupe / 复用路线。"""
    return _gray(img).tobytes().hex()


def frame_diff(before: Any, after: Any) -> float:
    """两张截图的平均绝对差（0~255）。"""
    a = _gray(before).astype(np.int16)
    b = _gray(after).astype(np.int16)
    return float(np.abs(a - b).mean())


def frame_changed(before: Any, after: Any, threshold: float = 2.0) -> tuple[bool, float]:
    diff = frame_diff(before, after)
    return diff > float(threshold), diff


# ---------------------------------------------------------------- 调用栈 / 截图


def _trace() -> list[dict]:
    """记下进入未知画面的调用链，网页可以直接跳到源代码行。"""
    frames = traceback.extract_stack()
    entries: list[dict] = []
    for frame in reversed(frames):
        name = Path(frame.filename).name
        if name in _OWN_FILES:
            continue
        try:
            rel = str(Path(frame.filename).resolve().relative_to(ROOT)).replace("\\", "/")
        except (ValueError, OSError):
            rel = str(frame.filename).replace("\\", "/")
        entries.append(
            {
                "file": rel,
                "line": int(frame.lineno or 0),
                "function": str(frame.name or ""),
                "text": (frame.line or "").strip()[:200],
            }
        )
        if len(entries) >= 8:
            break
    return entries


def primary_source(result: dict) -> str:
    """记录/结果里最能说明「哪行代码卡住了」的位置，形如 ``a/b.py:12 f``。"""
    result = result if isinstance(result, dict) else {}
    record = result.get("record") or {}
    trace = record.get("trace") or result.get("trace") or []
    first = trace[0] if trace and isinstance(trace[0], dict) else {}
    if first.get("file"):
        return f"{first['file']}:{first.get('line') or 0} {first.get('function') or ''}".strip()
    return str(record.get("source") or result.get("source") or "")


def _screencap(context) -> Any:
    try:
        job = context.tasker.controller.post_screencap().wait()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"未知画面：截图失败 {exc}")
        return None
    if not job.succeeded:
        return None
    return job.get()


# ---------------------------------------------------------------- 动作原语


def _task_ok(result: Any) -> bool:
    status = getattr(result, "status", None)
    return bool(status is not None and getattr(status, "succeeded", False))


def _run_step(context, step: str, primitives: dict) -> bool:
    """执行一步返回动作；认不出的步骤只记日志，不乱点。"""
    step = str(step or "").strip()
    if not step:
        return False
    if step.startswith("task:"):
        name = step[5:].strip()
        if not name:
            return False
        try:
            return _task_ok(context.run_task(name))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"未知画面：执行任务 {name} 失败 {exc}")
            return False

    primitive = primitives.get(step) or {}
    kind = str(primitive.get("kind") or "").strip()
    try:
        if kind == "click":
            x = int(primitive.get("x", 360))
            y = int(primitive.get("y", 640))
            context.tasker.controller.post_click(x, y).wait()
            return True
        if kind == "key":
            keycode = int(primitive.get("keycode", 4))
            context.tasker.controller.post_click_key(keycode).wait()
            return True
        if kind == "task":
            name = str(primitive.get("task") or "").strip()
            return _task_ok(context.run_task(name)) if name else False
        if kind == "wait":
            time.sleep(float(primitive.get("seconds") or 0.8))
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"未知画面：动作 {step} 执行失败 {exc}")
        return False

    logger.warning(f"未知画面：不认识的返回动作 {step}，跳过（可在 table/return_routes.json 里补）")
    return False


def attempt_steps(
    context,
    steps: list[str],
    *,
    primitives: dict,
    gap: float,
    threshold: float,
) -> dict:
    """按顺序点，每一步点完重新截图；画面一变立即返回 changed。"""
    before = _screencap(context)
    if before is None:
        return {"status": "no_screen", "attempts": [], "img": None}

    attempts: list[dict] = []
    for step in steps:
        if getattr(context.tasker, "stopping", False):
            return {"status": "stopped", "attempts": attempts, "img": before}
        ok = _run_step(context, step, primitives)
        time.sleep(max(0.2, float(gap)))
        after = _screencap(context)
        if after is None:
            attempts.append({"step": step, "ok": bool(ok), "changed": None, "diff": None})
            break
        changed, diff = frame_changed(before, after, threshold)
        attempts.append(
            {"step": step, "ok": bool(ok), "changed": changed, "diff": round(diff, 2)}
        )
        before = after
        if changed:
            return {"status": "changed", "attempts": attempts, "img": after}
    return {"status": "unchanged", "attempts": attempts, "img": before}


# ---------------------------------------------------------------- 识别探测


def probe_nodes(context, img: Any, nodes: list[str]) -> list[dict]:
    """把「这个画面上能命中的节点」一并记下来，方便补识别 / 查遮罩。"""
    probes: list[dict] = []
    for node in nodes[:24]:
        node = str(node or "").strip()
        if not node:
            continue
        entry: dict = {"node": node, "hit": False, "score": 0.0}
        try:
            detail = context.run_recognition(node, img)
            entry["hit"] = bool(getattr(detail, "hit", False))
            best = getattr(detail, "best_result", None)
            if best is not None:
                entry["score"] = round(float(getattr(best, "score", 0.0) or 0.0), 3)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:80]
        probes.append(entry)
    return probes


# ---------------------------------------------------------------- 记录 / 处理


def record_unknown(
    context,
    img: Any = None,
    *,
    source: str = "",
    reason: str = "",
    attempts: list[dict] | None = None,
    task: str = "",
    entry: str = "",
    probe: bool | None = None,
    note: str = "",
) -> dict | None:
    """把当前画面存成一条未知记录；同一画面（指纹相同）只累加命中次数。"""
    routes = STORE.routes()
    settings = routes["settings"]
    if not settings.get("enabled", True):
        return None
    if img is None:
        img = _screencap(context)
    if img is None:
        logger.warning("未知画面：没有截图，跳过记录")
        return None

    sig = signature(img)
    ok, buffer = cv2.imencode(".png", img)
    image = buffer.tobytes() if ok else None
    run = STORE.current_run()
    want_probe = settings.get("probeOnRecord", True) if probe is None else bool(probe)
    meta = {
        "source": source,
        "reason": reason,
        "task": task or run.get("name", ""),
        "entry": entry or run.get("entry", ""),
        "attempts": list(attempts or []),
        "trace": _trace(),
        "probes": probe_nodes(context, img, routes["probeNodes"]) if want_probe else [],
        "width": int(getattr(img, "shape", [0, 0])[1] if getattr(img, "ndim", 0) == 3 else 0),
        "height": int(getattr(img, "shape", [0, 0])[0] if getattr(img, "ndim", 0) == 3 else 0),
        "note": note,
    }
    try:
        record = STORE.record(sig, image=image, meta=meta)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"未知画面：写入记录失败 {exc}")
        return None
    logger.warning(
        f"未知画面已记录：{record['id']}（命中 {record['hits']} 次，"
        f"来源 {primary_source({'record': record}) or '未知'}）"
    )
    return record


def handle_unknown(
    context,
    img: Any = None,
    *,
    source: str = "",
    reason: str = "",
    task: str = "",
    entry: str = "",
    record_if_stuck: bool = True,
) -> dict:
    """未知画面统一入口：匹配旧记录 → 按路线点 → 变化就返回，没变化就记录。

    返回值 ``status``：
        changed    画面变了（已经点出了效果，调用方应重新识别状态）
        recorded   点了多次仍没变化，已写入 debug/unknown，需要人工看
        stuck      命中了旧记录，但记录的返回方式这次没点动画面
        disabled   返回路线表里关掉了未知界面处理
        no_screen  截图失败 / 没有画面
        stopped    任务被手动停止
    """
    routes = STORE.routes()
    settings = routes["settings"]
    if not settings.get("enabled", True):
        return {"status": "disabled", "attempts": [], "route": "", "record": None}

    if img is None:
        img = _screencap(context)
    if img is None:
        return {"status": "no_screen", "attempts": [], "route": "", "record": None}

    sig = signature(img)
    known = STORE.find(sig, threshold=settings.get("matchThreshold", 3.0))
    route = None
    if known is not None:
        route = routes["by_id"].get(f"unknown:{known.get('key')}") or routes["by_id"].get(
            known.get("route_id") or ""
        )
    steps = list(route["steps"]) if route else list(routes["default"]["steps"])
    steps = steps[: int(settings.get("maxClicks", 3))] or list(routes["default"]["steps"])
    label = route["label"] if route else routes["default"]["label"]

    logger.info(
        "未知画面："
        + (f"命中历史记录 {known['id']}（差 {known.get('match_diff')}），" if known else "第一次遇到，")
        + f"按「{label}」点 {len(steps)} 次"
    )
    result = attempt_steps(
        context,
        steps,
        primitives=routes["primitives"],
        gap=settings.get("clickGapSeconds", 0.7),
        threshold=settings.get("changeThreshold", 2.0),
    )
    payload = {
        "status": result["status"],
        "attempts": result["attempts"],
        "steps": steps,
        "route": label,
        "matched": known["id"] if known else "",
        "record": None,
    }

    if result["status"] in ("changed", "stopped", "no_screen"):
        return payload

    if known is not None:
        try:
            record = STORE.bump(
                known["id"],
                attempts=result["attempts"],
                reason=reason or "记录的返回方式点了之后画面没有变化",
                source=source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"未知画面：更新记录失败 {exc}")
            record = known
        payload["status"] = "stuck"
        payload["record"] = record
        return payload

    if not record_if_stuck:
        payload["status"] = "unchanged"
        return payload

    final_img = result.get("img")
    if final_img is None:
        final_img = img
    record = record_unknown(
        context,
        final_img,
        source=source,
        reason=reason or f"按「{label}」点了 {len(steps)} 次，画面没有变化",
        attempts=result["attempts"],
        task=task,
        entry=entry,
    )
    payload["record"] = record
    payload["status"] = "recorded" if record else "unchanged"
    return payload
