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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from webapp.combos import ComboRunner, load as load_combos, normalize as normalize_combo
from webapp.combos import save as save_combos, validate as validate_combos
from webapp.runtime import ROOT, Runtime

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
            elif path == "/api/taskpack":
                self._json(200, taskpack.status())
            elif path == "/api/screenshot":
                self._send(200, runtime.screenshot(), "image/png")
            elif path == "/api/logs":
                limit = int(parse_qs(parsed.query).get("limit", ["200"])[0])
                self._json(200, {"logs": runtime.tail_logs(limit)})
            elif path == "/api/alerts":
                self._json(200, alert_store.load_state(ROOT))
            elif path == "/api/records":
                self._json(200, {"records": child_records.recent(100)})
            elif path == "/api/mercenary-records":
                self._json(200, {"records": mercenary_records.recent(200)})
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
                saved = mercenary_rules.save(rules)
                self._json(200, {"ok": True, "rules": saved})
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
            self._json(500, {"error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

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

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("已退出")
    finally:
        runtime.stop_agent()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
