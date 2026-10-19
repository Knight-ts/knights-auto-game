# -*- coding: utf-8 -*-
"""MaaGC 网页版服务：http://127.0.0.1:8790/ 代替 MFAAvalonia。

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

from webapp.runtime import ROOT, Runtime

# 所有表/配置都用相对路径，统一到项目根目录
os.chdir(ROOT)

STATIC = Path(__file__).resolve().parent / "static"
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

runtime = Runtime()


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    server_version = "MaaGCWeb/1.0"

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
        return (self.headers.get("Host") or "").startswith("127.0.0.1")

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
            elif path == "/api/state":
                state = runtime.status()
                state["devices"] = runtime.list_devices()
                state["config"] = load_config()
                state["game"] = game_state.load()
                self._json(200, state)
            elif path == "/api/tasks":
                self._json(200, runtime.tasks())
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
        if not self._host_ok():
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
                state = runtime.run_task(entry, name, selection)
                config = load_config()
                config["last_task"] = entry
                selections = config.get("selections") or {}
                selections[entry] = selection
                config["selections"] = selections
                save_config(config)
                self._json(200, state)
            elif path == "/api/task/stop":
                self._json(200, runtime.stop_task())
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
                ok = notifier.notify("MaaGC 测试消息", "如果你收到这条消息，说明推送配置可用。")
                if ok:
                    message = "测试消息已发送，请检查平台"
                elif not config.get("enabled"):
                    message = "推送未启用，先勾选“启用推送”并保存"
                elif not notifier.effective_webhook():
                    message = "没有可用的 webhook，请填写或配置 config/config.json"
                else:
                    message = "发送失败，请检查 webhook 地址与网络"
                self._json(200, {"ok": ok, "message": message})
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
    print(f"MaaGC 网页版已启动：{url}")
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
