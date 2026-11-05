# -*- coding: utf-8 -*-
"""Knights Auto Game 网页版服务：http://127.0.0.1:8790/ 代替 MFAAvalonia。

启动：python webapp/server.py [--port 8790] [--no-browser]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from webapp import scenes as scene_rules
from webapp import source as source_view
from webapp import unknowns as unknown_view
from webapp.combos import ComboRunner, load as load_combos, normalize as normalize_combo
from webapp.combos import save as save_combos, validate as validate_combos
from webapp.repair import DEFAULTS as REPAIR_DEFAULTS, RepairManager
from webapp.runtime import ROOT, Runtime
from webapp.selfheal import ErrorStore

# 所有表/配置都用相对路径，统一到项目根目录
os.chdir(ROOT)

STATIC = Path(__file__).resolve().parent / "static"
ALERTS_DIR = ROOT / "agent" / "action" / "zshg"
CONFIG_PATH = ROOT / "config" / "webapp.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


alert_store = _load_module(
    "child_alert_store", ROOT / "agent" / "action" / "zshg" / "child_alert_store.py"
)
child_records = _load_module("child_records", ROOT / "agent" / "utils" / "child_records.py")
notifier = _load_module("notifier", ROOT / "agent" / "utils" / "notifier.py")
child_perf = _load_module("child_perf", ROOT / "agent" / "utils" / "child_perf.py")
mercenary_records = _load_module("mercenary_records", ROOT / "agent" / "utils" / "mercenary_records.py")
mercenary_rules = _load_module("mercenary_rules", ROOT / "agent" / "utils" / "mercenary_rules.py")
mercenary_admit = _load_module("mercenary_admit", ROOT / "agent" / "utils" / "mercenary_admit.py")
task_records = _load_module("task_records", ROOT / "agent" / "utils" / "task_records.py")
task_rules = _load_module("task_rules", ROOT / "agent" / "utils" / "task_rules.py")
battle_memory = _load_module(
    "battle_memory", ROOT / "agent" / "utils" / "battle_memory.py"
)
game_state = _load_module("game_state", ROOT / "agent" / "utils" / "game_state.py")
taskpack = _load_module("taskpack", ROOT / "tools" / "taskpack.py")

runtime = Runtime()


def reload_interface(result: dict) -> None:
    """tasks/ 重建/导入后，让网页版立刻用新的 interface.json。"""
    if result.get("ok"):
        runtime.interfaces = runtime._load_interface()


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 「场景处理」卡片里展示最近命中的场景日志；只做关键词筛选，不解析格式。
SCENE_LOG_HINTS = (
    "场景确认",
    "口粮预交",
    "年度结算",
    "检测到佣兵",
    "检测到孩子",
    "检测到提示",
    "检测到海盗",
    "检测到城堡",
    "检测到丰收",
    "检测到告白",
    "检测到婚礼",
    "事件处理完成",
    "好娃提醒",
    "任务失败",
    "未知画面",
    "未知界面",
)


def _scene_recent_logs(limit: int = 8) -> list[str]:
    try:
        lines = runtime.tail_logs(500)
    except Exception:  # noqa: BLE001
        return []
    hits: list[str] = []
    for line in reversed(lines):
        text = (line or "").strip()
        if text and any(hint in text for hint in SCENE_LOG_HINTS):
            hits.append(text)
        if len(hits) >= limit:
            break
    return list(reversed(hits))


# 月度循环是基础配置；这三项最早只存在于「战斗任务主动」。
# 升级后第一次启动时把专门任务里已经设置过的值搬进月度循环，
# 再把专门任务恢复成「跟随月度循环」，避免它继续当显式覆盖。
BASE_BATTLE_OPTIONS = ("战斗模式", "待机回合数", "棋子识别")


def migrate_battle_base_config() -> bool:
    config = load_config()
    selections = config.get("selections")
    if not isinstance(selections, dict):
        return False
    yearly = selections.get("Auto_YearlyTask")
    if not isinstance(yearly, dict):
        yearly = {}
        selections["Auto_YearlyTask"] = yearly
    if "待机回合数" in yearly:
        return False

    donor_entry = ""
    donor = None
    for entry, selection in selections.items():
        if entry == "Auto_YearlyTask" or not isinstance(selection, dict):
            continue
        if any(option in selection for option in BASE_BATTLE_OPTIONS):
            donor_entry, donor = entry, selection
            break
    if donor is None:
        return False

    for option in BASE_BATTLE_OPTIONS:
        if option in donor:
            yearly[option] = donor.pop(option)
    config["selections"] = selections
    save_config(config)
    print(
        f"[config] 已把 {donor_entry} 的战斗设置迁移为月度循环基础配置，"
        f"{donor_entry} 改为跟随月度循环",
        flush=True,
    )
    return True


# 问题自纠：报错现场（截图 + 日志尾巴）存到 debug/errors/，网页上可保留/删除
selfheal = ErrorStore(ROOT, config_provider=load_config)
runtime.error_sink = selfheal.capture_event

# AI 修复调度器：新报错 -> DSH Bridge -> DSH 会话；有次数/超时/指纹上限
repair = RepairManager(ROOT, selfheal, load_config)


def _capture_api_error(path: str, exc: Exception) -> None:
    """接口 500 也留一张现场；放后台线程，别拖慢错误响应。"""
    if path.startswith(("/api/selfheal", "/api/repair")):
        return  # 自纠/修复接口自己的错不再递归截图
    message = str(exc)
    # 设备没连 / 任务在跑这类“正常状态提示”不是 bug，别刷屏
    expected = (
        "设备未连接",
        "Tasker 未就绪",
        "Agent 未连接",
        "已有任务在运行",
        "资源未加载",
        "资源加载失败",
        "找不到任务",
    )
    if message.startswith(expected):
        return

    def run() -> None:
        image = None
        try:
            if runtime.controller is not None:
                image = runtime.screenshot()
        except Exception:  # noqa: BLE001
            image = None
        try:
            selfheal.capture(
                source="api",
                title=f"接口报错：{path}",
                error=f"{type(exc).__name__}: {exc}",
                image=image,
                logs=runtime.tail_logs(80),
                entry=path,
                task_name=runtime.task_name,
                state=runtime.task_state,
                extra={"path": path},
                dedupe_key=f"api|{path}|{exc}",
            )
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=run, name="selfheal-api", daemon=True).start()


def combo_selection(entry: str) -> dict:
    """组合步骤没单独存选项时，沿用「任务」页给该任务保存的选择。"""
    selections = load_config().get("selections") or {}
    return selections.get(entry) or {}


def combo_reconnect() -> bool:
    """组合规则「设备离线 → 重连」用。"""
    config = load_config()
    try:
        runtime.connect(config.get("adb_path") or "", config.get("address") or "")
        return True
    except Exception as exc:  # noqa: BLE001
        runtime.log(f"组合重连失败：{exc}")
        return False


def combo_notify(title: str, content: str) -> None:
    try:
        notifier.notify(title, content)
    except Exception:  # noqa: BLE001
        pass


combo_runner = ComboRunner(
    runtime,
    ROOT,
    selection_provider=combo_selection,
    reconnect_provider=combo_reconnect,
    notify_provider=combo_notify,
)


def _persist_history(items: list[dict]) -> None:
    config = load_config()
    config["history"] = items[:20]
    save_config(config)


runtime.history_sink = _persist_history
_saved_history = load_config().get("history")
if isinstance(_saved_history, list):
    runtime.history.extend(_saved_history[:20])


def save_notify_config(data: dict) -> dict:
    config = dict(notifier.DEFAULT_CONFIG)
    config.update({k: v for k, v in data.items() if k in notifier.DEFAULT_CONFIG})
    config["enabled"] = bool(config.get("enabled"))
    try:
        config["timeout"] = max(1.0, float(config.get("timeout") or 8))
    except (TypeError, ValueError):
        config["timeout"] = 8
    notifier.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    notifier.CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return config


def mask_webhook(url: str) -> str:
    url = (url or "").strip()
    if len(url) <= 24:
        return url
    return f"{url[:18]}…{url[-6:]}"


class Handler(BaseHTTPRequestHandler):
    server_version = "KnightsAutoGame/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    # ------------------------------------------------------------ 响应

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def _host_ok(self) -> bool:
        """只接受本机 Host，避免 127.0.0.1.evil.com 这类 DNS rebinding 绕过。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("[") and "]" in host:  # [::1]:8790
            return host[1 : host.index("]")] == "::1"
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in ("127.0.0.1", "localhost")

    def _origin_ok(self) -> bool:
        """跨站请求会带 Origin；只放行本机来源。非浏览器客户端不带 Origin，照常放行。"""
        origin = (self.headers.get("Origin") or "").strip().lower()
        if not origin:
            return True
        return urlparse(origin).hostname in ("127.0.0.1", "localhost")

    # ------------------------------------------------------------ GET

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(403, {"error": "只允许本机访问"})
            return

        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/app.js":
                self._send(200, (STATIC / "app.js").read_bytes(), "application/javascript; charset=utf-8")
            elif path == "/style.css":
                self._send(200, (STATIC / "style.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/alerts.js":
                self._send(
                    200,
                    (ALERTS_DIR / "web_alerts.js").read_bytes(),
                    "application/javascript; charset=utf-8",
                )
            elif path == "/alerts.css":
                self._send(
                    200,
                    (ALERTS_DIR / "web_alerts.css").read_bytes(),
                    "text/css; charset=utf-8",
                )
            elif path == "/api/state":
                state = runtime.status()
                state["devices"] = runtime.list_devices()
                state["config"] = load_config()
                state["game"] = game_state.load()
                self._json(200, state)
            elif path == "/api/tasks":
                config = load_config()
                self._json(
                    200,
                    runtime.tasks(
                        config.get("task_labels") or {},
                        config.get("hidden_tasks") or [],
                    ),
                )
            elif path == "/api/combos":
                config = load_config()
                combos_list = load_combos(ROOT)
                if not combos_list and not (ROOT / "table" / "task_combos.json").exists():
                    combos_list = [
                        normalize_combo(
                            {
                                "id": "monthly",
                                "name": "月度循环",
                                "loops": 1,
                                "steps": [{"entry": "Auto_YearlyTask"}],
                            }
                        )
                    ]
                modules = runtime.tasks(config.get("task_labels") or {}, [])
                self._json(
                    200,
                    {
                        "ok": True,
                        "combos": combos_list,
                        "modules": modules,
                        "selections": config.get("selections") or {},
                        "running": combo_runner.status(),
                    },
                )
            elif path == "/api/combos/status":
                self._json(200, {"ok": True, "running": combo_runner.status()})
            elif path == "/api/scenes":
                payload = scene_rules.load()
                payload["ok"] = True
                payload["guard"] = scene_rules.guard()
                payload["recent"] = _scene_recent_logs()
                self._json(200, payload)
            elif path == "/api/unknowns":
                payload = unknown_view.state()
                payload["ok"] = True
                self._json(200, payload)
            elif path == "/api/unknowns/detail":
                sid = (parse_qs(parsed.query).get("id") or [""])[0]
                record = unknown_view.detail(sid)
                if record is None:
                    self._json(404, {"ok": False, "error": "没有这条未知界面记录"})
                else:
                    self._json(200, {"ok": True, **record})
            elif path == "/api/unknowns/image":
                sid = (parse_qs(parsed.query).get("id") or [""])[0]
                data = unknown_view.image_bytes(sid)
                if data is None:
                    self._json(404, {"ok": False, "error": "没有截图"})
                else:
                    self._send(200, data, "image/png")
            elif path == "/api/source":
                query = parse_qs(parsed.query)
                symbol = (query.get("symbol") or [""])[0]
                node = (query.get("node") or [""])[0]
                rel = (query.get("path") or [""])[0]
                try:
                    line = int((query.get("line") or ["0"])[0])
                except ValueError:
                    line = 0
                payload = source_view.lookup(symbol=symbol, node=node, path=rel, line=line)
                payload["ok"] = bool(payload.get("ok"))
                self._json(200, payload)
            elif path == "/api/taskpack":
                self._json(200, taskpack.status())
            elif path == "/api/screenshot":
                self._send(200, runtime.screenshot(), "image/png")
            elif path == "/api/selfheal":
                query = parse_qs(parsed.query)
                status = (query.get("status") or ["all"])[0]
                limit = int((query.get("limit") or ["300"])[0])
                payload = selfheal.state(
                    status=status if status in ("pending", "kept", "deleted") else None,
                    limit=limit,
                )
                payload["ok"] = True
                payload["repair"] = repair.status()
                self._json(200, payload)
            elif path == "/api/selfheal/detail":
                sid = (parse_qs(parsed.query).get("id") or [""])[0]
                record = selfheal.detail(sid)
                if record is None:
                    self._json(404, {"ok": False, "error": "没有这条记录"})
                else:
                    self._json(200, {"ok": True, **record})
            elif path == "/api/selfheal/image":
                sid = (parse_qs(parsed.query).get("id") or [""])[0]
                data = selfheal.image_bytes(sid)
                if data is None:
                    self._json(404, {"ok": False, "error": "没有截图"})
                else:
                    self._send(200, data, "image/png")
            elif path == "/api/selfheal/report":
                self._send(
                    200,
                    selfheal.report().encode("utf-8"),
                    "text/markdown; charset=utf-8",
                )
            elif path == "/api/repair/status":
                self._json(200, {"ok": True, "repair": repair.status()})
            elif path == "/api/repair/escalation":
                try:
                    text = repair.escalation_path.read_text(encoding="utf-8")
                except OSError:
                    text = "# 暂无需要判断的报错\n"
                self._send(200, text.encode("utf-8"), "text/markdown; charset=utf-8")
            elif path == "/api/logs":
                limit = int(parse_qs(parsed.query).get("limit", ["200"])[0])
                self._json(200, {"logs": runtime.tail_logs(limit)})
            elif path == "/api/alerts":
                self._json(200, alert_store.load_state(ROOT))
            elif path == "/api/records":
                self._json(200, {"records": child_records.recent(100)})
            elif path == "/api/mercenary-records":
                self._json(200, {"records": mercenary_records.recent(200)})
            elif path == "/api/mercenary-combos":
                self._json(
                    200,
                    {
                        "combos": mercenary_admit.condition_choices(),
                        "attributes": list(mercenary_rules.ATTRIBUTE_ORDER),
                        "grades": ["SS", "S", "A", "B", "C", "D", "E"],
                    },
                )
            elif path == "/api/task-records":
                summary = task_records.load_summary()
                self._json(
                    200,
                    {
                        "cities": summary.get("cities") or {},
                        "updated": summary.get("updated", ""),
                        "blacklist": task_records.load_blacklist(),
                    },
                )
            elif path == "/api/task-rules":
                rules = task_rules.load_rules()
                pool = task_rules.load_pool()
                cities = set((rules.get("cities") or {}).keys())
                pool_city = str(pool.get("city") or "").strip()
                if pool_city:
                    cities.add(pool_city)
                summary = task_records.load_summary()
                cities.update((summary.get("cities") or {}).keys())
                self._json(
                    200,
                    {
                        "rules": rules,
                        "global_blacklist": task_rules.global_blacklist(),
                        "pool": pool,
                        "cities": sorted(cities),
                    },
                )
            elif path == "/api/battle-memory":
                self._json(200, battle_memory.memory().summary())
            elif path == "/api/mercenary-rules":
                self._json(200, mercenary_rules.load())
            elif path == "/api/child-name-config":
                self._json(
                    200,
                    {
                        "speed": child_perf.load_speed(),
                        "profiles": {
                            key: profile["label"]
                            for key, profile in child_perf.SPEED_PROFILES.items()
                        },
                    },
                )
            elif path == "/api/notify":
                config = notifier.load_config()
                effective = notifier.effective_webhook()
                self._json(
                    200,
                    {
                        "config": config,
                        "effective": mask_webhook(effective),
                        "hasWebhook": bool(effective),
                    },
                )
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            runtime.log(f"接口失败 {path}：{exc}")
            _capture_api_error(path, exc)
            self._json(500, {"error": str(exc)})

    # ------------------------------------------------------------ POST

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or not self._origin_ok():
            self._json(403, {"error": "只允许本机访问"})
            return

        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/connect":
                adb_path = payload.get("adb_path") or ""
                address = payload.get("address") or "127.0.0.1:16384"
                state = runtime.connect(adb_path, address)
                config = load_config()
                config.update({"adb_path": adb_path, "address": address})
                save_config(config)
                self._json(200, state)
            elif path == "/api/agent/start":
                self._json(200, runtime.start_agent())
            elif path == "/api/agent/stop":
                runtime.stop_agent()
                self._json(200, runtime.status())
            elif path == "/api/task/run":
                entry = payload.get("entry") or ""
                name = payload.get("name") or entry
                selection = payload.get("selection") or {}
                # 先保存选择：即使任务启动失败，下次打开页面仍然用这套参数
                config = load_config()
                config["last_task"] = entry
                selections = config.get("selections") or {}
                selections[entry] = selection
                config["selections"] = selections
                save_config(config)
                self._json(200, runtime.run_task(entry, name, selection))
            elif path == "/api/task/selection/save":
                entry = payload.get("entry") or ""
                selection = payload.get("selection") or {}
                if not entry:
                    self._json(400, {"ok": False, "error": "缺少 entry"})
                else:
                    config = load_config()
                    config["last_task"] = entry
                    selections = config.get("selections") or {}
                    selections[entry] = selection
                    config["selections"] = selections
                    save_config(config)
                    self._json(200, {"ok": True})
            elif path == "/api/task/stop":
                self._json(200, runtime.stop_task())
            elif path == "/api/task/rename":
                entry = payload.get("entry") or ""
                name = (payload.get("name") or "").strip()
                if not entry or not name:
                    self._json(400, {"ok": False, "error": "缺少 entry 或 name"})
                else:
                    config = load_config()
                    labels = config.get("task_labels") or {}
                    labels[entry] = name
                    config["task_labels"] = labels
                    save_config(config)
                    self._json(200, {"ok": True})
            elif path == "/api/task/delete":
                entry = payload.get("entry") or ""
                if not entry:
                    self._json(400, {"ok": False, "error": "缺少 entry"})
                else:
                    config = load_config()
                    hidden = config.get("hidden_tasks") or []
                    if entry not in hidden:
                        hidden.append(entry)
                    config["hidden_tasks"] = hidden
                    save_config(config)
                    self._json(200, {"ok": True})
            elif path == "/api/task/restore":
                entry = payload.get("entry") or ""
                if not entry:
                    self._json(400, {"ok": False, "error": "缺少 entry"})
                else:
                    config = load_config()
                    hidden = config.get("hidden_tasks") or []
                    if entry in hidden:
                        hidden.remove(entry)
                    config["hidden_tasks"] = hidden
                    save_config(config)
                    self._json(200, {"ok": True})
            elif path == "/api/logs/clear":
                runtime.clear_logs()
                self._json(200, {"ok": True})
            elif path == "/api/alerts/save":
                result = alert_store.save_state(ROOT, payload.get("conditions") or [])
                self._json(200 if result.get("ok") else 400, result)
            elif path == "/api/records/recruited":
                changed = child_records.mark_recruited(payload.get("name") or "", payload.get("value") or "已招募")
                self._json(200, {"ok": bool(changed), "changed": changed})
            elif path == "/api/child-name-config/save":
                try:
                    speed = child_perf.save_speed(payload.get("speed") or "normal")
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                else:
                    self._json(200, {"ok": True, "speed": speed})
            elif path == "/api/mercenary-rules/save":
                rules = mercenary_rules.load()
                if payload.get("mode") in ("whitelist", "blacklist"):
                    rules["mode"] = payload["mode"]
                if isinstance(payload.get("names"), list):
                    rules["names"] = payload["names"]
                if payload.get("match") in ("all", "any"):
                    rules["match"] = payload["match"]
                if "title_min" in payload:
                    rules["title_min"] = str(payload.get("title_min") or "无")
                if "min_orange" in payload:
                    rules["min_orange"] = payload.get("min_orange")
                if payload.get("read_detail") in ("auto", "always", "never"):
                    rules["read_detail"] = payload["read_detail"]
                if isinstance(payload.get("combos"), list):
                    rules["combos"] = payload["combos"]
                if isinstance(payload.get("features"), list):
                    rules["features"] = payload["features"]
                elif isinstance(payload.get("features"), str):
                    rules["features"] = payload["features"]
                if isinstance(payload.get("potential_min"), dict):
                    rules["potential_min"] = payload["potential_min"]
                saved = mercenary_rules.save(rules)
                self._json(200, {"ok": True, "rules": saved})
            elif path == "/api/task-rules/save":
                city = str(payload.get("city") or "").strip()
                rules = task_rules.set_city_levels(
                    city,
                    payload.get("min_level"),
                    payload.get("max_level"),
                )
                self._json(200, {"ok": True, "rules": rules})
            elif path == "/api/battle-memory/policy":
                name = str(payload.get("name") or "custom").strip()
                policy = payload.get("policy")
                if not isinstance(policy, dict):
                    self._json(400, {"ok": False, "error": "策略必须是 JSON 对象"})
                else:
                    saved = battle_memory.memory().save_policy(name, policy)
                    self._json(
                        200,
                        {
                            "ok": True,
                            "policy": saved,
                            "summary": battle_memory.memory().summary(),
                        },
                    )
            elif path == "/api/battle-memory/active":
                name = str(payload.get("name") or "").strip()
                battle_memory.memory().set_active(name)
                self._json(
                    200, {"ok": True, "summary": battle_memory.memory().summary()}
                )
            elif path == "/api/battle-memory/assign":
                key = str(payload.get("key") or "").strip()
                policy = str(payload.get("policy") or "").strip()
                battle_memory.memory().assign_key(key, policy)
                self._json(
                    200, {"ok": True, "summary": battle_memory.memory().summary()}
                )
            elif path == "/api/battle-memory/map":
                key = str(payload.get("key") or "").strip()
                map_data = payload.get("map")
                if "/" not in key or not isinstance(map_data, dict):
                    self._json(400, {"ok": False, "error": "缺少任务键或地图数据"})
                else:
                    city, task = key.split("/", 1)
                    ok = battle_memory.memory().save_map(city, task, map_data)
                    self._json(
                        200,
                        {
                            "ok": ok,
                            "error": "" if ok else "任务不存在",
                            "summary": battle_memory.memory().summary(),
                        },
                    )
            elif path == "/api/battle-memory/opening":
                key = str(payload.get("key") or "").strip()
                opening = payload.get("opening")
                if "/" not in key or not isinstance(opening, dict):
                    self._json(400, {"ok": False, "error": "缺少任务键或走位方案"})
                else:
                    city, task = key.split("/", 1)
                    ok = battle_memory.memory().save_opening(city, task, opening)
                    self._json(
                        200,
                        {
                            "ok": ok,
                            "error": "" if ok else "任务不存在",
                            "summary": battle_memory.memory().summary(),
                        },
                    )
            elif path == "/api/battle-memory/policy/delete":
                name = str(payload.get("name") or "").strip()
                deleted = battle_memory.memory().delete_policy(name)
                self._json(
                    200,
                    {
                        "ok": deleted,
                        "error": "" if deleted else "策略不存在或不能删除默认策略",
                        "summary": battle_memory.memory().summary(),
                    },
                )
            elif path == "/api/task-rules/blacklist":
                city = str(payload.get("city") or "").strip()
                name = str(payload.get("task") or "").strip()
                if not name:
                    self._json(400, {"ok": False, "error": "缺少任务名"})
                elif city:
                    rules = task_rules.add_city_blacklist(city, name)
                    self._json(
                        200,
                        {
                            "ok": True,
                            "rules": rules,
                            "global_blacklist": task_rules.global_blacklist(),
                        },
                    )
                else:
                    global_list = task_rules.add_global_blacklist(name)
                    self._json(
                        200,
                        {
                            "ok": True,
                            "rules": task_rules.load_rules(),
                            "global_blacklist": global_list,
                        },
                    )
            elif path == "/api/task-rules/unblacklist":
                city = str(payload.get("city") or "").strip()
                name = str(payload.get("task") or "").strip()
                if not name:
                    self._json(400, {"ok": False, "error": "缺少任务名"})
                elif city:
                    rules = task_rules.remove_city_blacklist(city, name)
                    self._json(
                        200,
                        {
                            "ok": True,
                            "rules": rules,
                            "global_blacklist": task_rules.global_blacklist(),
                        },
                    )
                else:
                    global_list = task_rules.remove_global_blacklist(name)
                    self._json(
                        200,
                        {
                            "ok": True,
                            "rules": task_rules.load_rules(),
                            "global_blacklist": global_list,
                        },
                    )
            elif path == "/api/task-records/blacklist":
                name = str(payload.get("task") or "").strip()
                if not name:
                    self._json(400, {"ok": False, "error": "缺少任务名"})
                else:
                    task_records.add_blacklist(name)
                    self._json(
                        200, {"ok": True, "blacklist": task_records.load_blacklist()}
                    )
            elif path == "/api/task-records/unblacklist":
                name = str(payload.get("task") or "").strip()
                if not name:
                    self._json(400, {"ok": False, "error": "缺少任务名"})
                else:
                    task_records.remove_blacklist(name)
                    self._json(
                        200, {"ok": True, "blacklist": task_records.load_blacklist()}
                    )
            elif path == "/api/notify/save":
                config = save_notify_config(payload)
                effective = notifier.effective_webhook()
                self._json(
                    200,
                    {"ok": True, "config": config, "effective": mask_webhook(effective), "hasWebhook": bool(effective)},
                )
            elif path == "/api/notify/test":
                config = notifier.load_config()
                ok = notifier.notify("Knights Auto Game 测试消息", "如果你收到这条消息，说明推送配置可用。")
                if ok:
                    message = "测试消息已发送，请检查平台"
                elif not config.get("enabled"):
                    message = "推送未启用，先勾选“启用推送”并保存"
                elif not notifier.effective_webhook():
                    message = "没有可用的 webhook，请填写或配置 config/config.json"
                else:
                    message = "发送失败，请检查 webhook 地址与网络"
                self._json(200, {"ok": ok, "message": message})
            elif path == "/api/taskpack/export":
                result = taskpack.export(payload.get("entry") or None, bool(payload.get("force")))
                reload_interface(result)
                self._json(200, result)
            elif path == "/api/taskpack/build":
                result = taskpack.build()
                reload_interface(result)
                self._json(200, result)
            elif path == "/api/taskpack/check":
                self._json(200, taskpack.check())
            elif path == "/api/taskpack/pack":
                entry = str(payload.get("entry") or "")
                if not entry:
                    self._json(400, {"ok": False, "error": "缺少 entry"})
                    return
                self._json(200, taskpack.pack(entry, payload.get("output")))
            elif path == "/api/taskpack/unpack":
                zip_path = str(payload.get("path") or "")
                if not zip_path:
                    self._json(400, {"ok": False, "error": "缺少 zip 路径"})
                    return
                result = taskpack.unpack(
                    zip_path,
                    bool(payload.get("force")),
                    bool(payload.get("build", True)),
                )
                reload_interface(result)
                self._json(200, result)
            elif path == "/api/unknowns/route":
                sid = str(payload.get("id") or "")
                try:
                    record = unknown_view.save_route(sid, str(payload.get("route") or ""))
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                    return
                if record is None:
                    self._json(404, {"ok": False, "error": "没有这条未知界面记录"})
                else:
                    self._json(200, {"ok": True, "item": record})
            elif path == "/api/unknowns/note":
                sid = str(payload.get("id") or "")
                record = unknown_view.note(sid, str(payload.get("note") or ""))
                if record is None:
                    self._json(404, {"ok": False, "error": "没有这条未知界面记录"})
                else:
                    self._json(200, {"ok": True, "item": record})
            elif path == "/api/unknowns/delete":
                sid = str(payload.get("id") or "")
                removed = unknown_view.remove(sid)
                self._json(
                    200,
                    {"ok": removed, "removed": bool(removed), "stats": unknown_view.store().stats()},
                )
            elif path == "/api/unknowns/clear":
                if not payload.get("confirm"):
                    self._json(400, {"ok": False, "error": "清空未知界面记录需要 confirm=true"})
                    return
                self._json(200, {"ok": True, **unknown_view.clear()})
            elif path == "/api/source/open":
                rel = str(payload.get("path") or "")
                try:
                    line = int(payload.get("line") or 1)
                except (TypeError, ValueError):
                    line = 1
                self._json(200, source_view.open_in_editor(rel, line))
            elif path == "/api/selfheal/frontend":
                message = str(payload.get("message") or "").strip()
                if not message:
                    self._json(400, {"ok": False, "error": "缺少 message"})
                    return
                image = None
                try:
                    image = runtime.screenshot()
                except Exception:  # noqa: BLE001
                    image = None
                stack = str(payload.get("stack") or "")
                url = str(payload.get("url") or "")
                where = (
                    f"{payload.get('source') or '页面'}:"
                    f"{payload.get('line') or 0}:{payload.get('column') or 0}"
                )
                record = selfheal.capture(
                    source="frontend",
                    title=str(payload.get("title") or "页面脚本报错"),
                    error="\n".join(part for part in (message, stack, where, url) if part),
                    image=image,
                    logs=runtime.tail_logs(60),
                    entry=url,
                    extra={
                        "url": url,
                        "line": payload.get("line"),
                        "column": payload.get("column"),
                    },
                    dedupe_key=f"frontend|{message}|{where}",
                )
                self._json(200, {"ok": bool(record), "item": record, "hasImage": bool(image)})
            elif path == "/api/selfheal/capture":
                image = None
                try:
                    image = runtime.screenshot()
                except Exception as exc:  # noqa: BLE001
                    runtime.log(f"手动抓取现场截图失败：{exc}")
                record = selfheal.capture(
                    source="manual",
                    title=payload.get("title") or "手动抓取现场",
                    image=image,
                    logs=runtime.tail_logs(120),
                    task_name=runtime.task_name,
                    state=runtime.task_state,
                    note=payload.get("note") or "",
                    force=True,
                )
                self._json(
                    200,
                    {
                        "ok": bool(record),
                        "item": record,
                        "hasImage": bool(image),
                        "stats": selfheal.stats(),
                        "config": selfheal.config(),
                    },
                )
            elif path == "/api/selfheal/mark":
                sid = str(payload.get("id") or "")
                status = str(payload.get("status") or "")
                if status not in ("pending", "kept", "deleted"):
                    self._json(400, {"ok": False, "error": "状态只能是 pending / kept / deleted"})
                    return
                record = selfheal.mark(sid, status, payload.get("note"))
                if record is None:
                    self._json(404, {"ok": False, "error": "没有这条记录"})
                else:
                    self._json(200, {"ok": True, "item": record, "stats": selfheal.stats()})
            elif path == "/api/selfheal/note":
                sid = str(payload.get("id") or "")
                record = selfheal.note(sid, str(payload.get("note") or ""))
                if record is None:
                    self._json(404, {"ok": False, "error": "没有这条记录"})
                else:
                    self._json(200, {"ok": True, "item": record})
            elif path == "/api/selfheal/purge":
                sid = payload.get("id")
                removed = selfheal.purge(str(sid) if sid else None)
                self._json(200, {"ok": True, "removed": removed, "stats": selfheal.stats()})
            elif path == "/api/selfheal/cleanup":
                result = selfheal.cleanup()
                self._json(200, {"ok": True, **result})
            elif path == "/api/selfheal/config":
                config = load_config()
                section = config.get("selfheal")
                if not isinstance(section, dict):
                    section = {}
                for key in (
                    "enabled",
                    "autoCapture",
                    "maxItems",
                    "maxMB",
                    "pendingDays",
                    "trashDays",
                    "dedupeSeconds",
                ):
                    if key in payload:
                        section[key] = payload[key]
                config["selfheal"] = section
                save_config(config)
                self._json(200, {"ok": True, "config": selfheal.config()})
            elif path == "/api/selfheal/clear":
                if not payload.get("confirm"):
                    self._json(400, {"ok": False, "error": "一键清除需要 confirm=true"})
                    return
                repair.stop_current()
                repair.clear_state()
                result = selfheal.clear_all()
                self._json(200, {"ok": True, **result, "repair": repair.status()})
            elif path == "/api/repair/start":
                sid = str(payload.get("id") or "")
                try:
                    result = repair.queue(sid, manual=True)
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                else:
                    self._json(200, result)
            elif path == "/api/repair/stop":
                self._json(200, {"ok": True, **repair.stop_current()})
            elif path == "/api/repair/reset":
                sid = str(payload.get("id") or "")
                try:
                    result = repair.reset(sid)
                except ValueError as exc:
                    self._json(400, {"ok": False, "error": str(exc)})
                else:
                    self._json(200, result)
            elif path == "/api/repair/config":
                config = load_config()
                section = config.get("repair")
                if not isinstance(section, dict):
                    section = {}
                old_auto = bool(section.get("auto", REPAIR_DEFAULTS["auto"]))
                for key in REPAIR_DEFAULTS:
                    if key in payload:
                        section[key] = payload[key]
                if bool(section.get("auto", REPAIR_DEFAULTS["auto"])) and not old_auto:
                    section["autoSince"] = time.time()
                config["repair"] = section
                save_config(config)
                self._json(
                    200,
                    {"ok": True, "config": repair.config(), "repair": repair.status()},
                )
            elif path == "/api/combos/save":
                incoming = payload.get("combos")
                if not isinstance(incoming, list):
                    self._json(400, {"ok": False, "error": "缺少 combos 数组"})
                    return
                items = [normalize_combo(item) for item in incoming if isinstance(item, dict)]
                entries = {
                    task.get("entry", "") for task in runtime.interfaces.get("task", [])
                }
                errors = validate_combos(items, entries)
                if errors:
                    self._json(200, {"ok": False, "errors": errors})
                    return
                saved = save_combos(ROOT, items)
                self._json(
                    200,
                    {
                        "ok": True,
                        "combos": saved,
                        "messages": [f"已保存 {len(saved)} 个组合"],
                    },
                )
            elif path == "/api/combos/run":
                combo = payload.get("combo")
                if combo is None and payload.get("id"):
                    combo = next(
                        (item for item in load_combos(ROOT) if item["id"] == payload["id"]),
                        None,
                    )
                if not isinstance(combo, dict):
                    self._json(400, {"ok": False, "error": "缺少组合"})
                    return
                normalized = normalize_combo(combo)
                entries = {
                    task.get("entry", "") for task in runtime.interfaces.get("task", [])
                }
                errors = validate_combos([normalized], entries)
                if errors:
                    self._json(200, {"ok": False, "errors": errors})
                    return
                try:
                    running = combo_runner.start(normalized)
                except Exception as exc:  # noqa: BLE001
                    self._json(400, {"ok": False, "error": str(exc)})
                    return
                self._json(200, {"ok": True, "running": running})
            elif path == "/api/combos/stop":
                self._json(200, {"ok": True, "running": combo_runner.stop()})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            runtime.log(f"接口失败 {path}：{exc}")
            _capture_api_error(path, exc)
            self._json(500, {"error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # 一次性迁移：把旧版「战斗任务主动」里的战斗设置升级为月度循环基础配置
    try:
        migrate_battle_base_config()
    except Exception as exc:  # noqa: BLE001
        print(f"[config] 战斗基础配置迁移失败：{exc}", flush=True)

    last_error = None
    httpd = None
    for port in range(args.port, args.port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            args.port = port
            break
        except OSError as exc:
            last_error = exc
    if httpd is None:
        print(f"端口被占用：{last_error}")
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Knights Auto Game 网页版已启动：{url}")
    print("按 Ctrl+C 退出。")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    def bootstrap() -> None:
        """启动后自动连上上次使用的设备并拉起 Agent，打开页面就能用。"""
        config = load_config()
        try:
            devices = runtime.list_devices()
            wanted = (config.get("address") or "127.0.0.1:16384")
            target = next((d for d in devices if d["address"] == wanted), None)
            if target is None:
                target = next((d for d in devices if d["address"].endswith(":16384")), None)
            if target is None and devices:
                target = devices[0]
            if target is None:
                runtime.log("没有发现模拟器，请启动 MuMu 后在页面上点「重新搜索」")
                return
            runtime.connect(target["adb_path"], target["address"])
            runtime.start_agent()
            runtime.log("自动连接完成，可以直接开始任务")
        except Exception as exc:  # noqa: BLE001
            runtime.log(f"自动连接失败：{exc}（可在页面上手动连接）")

    threading.Thread(target=bootstrap, name="bootstrap", daemon=True).start()
    if repair.config()["enabled"]:
        repair.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("已退出")
    finally:
        repair.stop()
        runtime.stop_agent()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
