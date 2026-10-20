# -*- coding: utf-8 -*-
"""Knights Auto Game 网页控制台（只监听 127.0.0.1）。

页面分四块：主页 / 总控台 / 截图 / 好娃提醒。
    - 主页：设备、模拟器、Knights Auto Game、提醒类型、最近日志
    - 总控台：ADB 连接 / 断开 / 重启，设备列表，常用按键
    - 截图：实时画面（adb screencap），可暂停、可下载
    - 好娃提醒：增删改提醒类型，保存时同步三处配置

不依赖 maa，可直接用 tools/打开控制台.bat 启动；Agent 启动时也会自动拉起。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from utils import logger

from .child_alert_store import load_state, project_root, save_state, sync_instance_selection

ROOT = project_root()
HTML_PATH = Path(__file__).with_name("web_console.html")
ASSET_DIR = Path(__file__).parent
ASSETS = {
    "/alerts.js": ("web_alerts.js", "application/javascript; charset=utf-8"),
    "/alerts.css": ("web_alerts.css", "text/css; charset=utf-8"),
}
HOST = "127.0.0.1"
PORT_RANGE = range(8790, 8801)
URL_FILE = Path("temp") / "web_console_url.txt"

DEFAULT_SERIAL = "127.0.0.1:16384"
MUMU_MANAGER = Path(r"C:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe")

_server: ThreadingHTTPServer | None = None
_port: int | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------- ADB / 进程


def _instances() -> list[dict]:
    result = []
    for path in sorted((ROOT / "config" / "instances").glob("*.json")):
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return result


def project_adb() -> Path | None:
    """项目自带的 adb（tools/adb/adb.exe），随仓库分发，避免依赖本机路径。"""
    candidate = ROOT / "tools" / "adb" / "adb.exe"
    return candidate if candidate.exists() else None


def device_config() -> tuple[str, str]:
    """返回 (adb 路径, 序列号)，优先用项目自带的 adb，其次 GUI 里保存的设备配置。"""
    bundled = project_adb()
    if bundled is not None:
        for instance in _instances():
            serial = ((instance.get("AdbDevice") or {}).get("AdbSerial") or "").strip()
            if serial:
                return str(bundled), serial
        return str(bundled), DEFAULT_SERIAL

    for instance in _instances():
        device = instance.get("AdbDevice") or {}
        adb_path = (device.get("AdbPath") or "").strip()
        serial = (device.get("AdbSerial") or "").strip()
        if adb_path:
            return adb_path, serial or DEFAULT_SERIAL

    for candidate in (
        r"C:/Program Files/Netease/MuMu/nx_main/adb.exe",
    ):
        if Path(candidate).exists():
            return candidate, DEFAULT_SERIAL
    return "adb", DEFAULT_SERIAL


def run_adb(args: list[str], timeout: int = 20) -> tuple[int, str]:
    adb, _ = device_config()
    try:
        proc = subprocess.run(
            [adb, *args],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return -1, f"找不到 adb：{adb}"
    except subprocess.TimeoutExpired:
        return -2, "adb 执行超时"
    except Exception as exc:  # noqa: BLE001
        return -3, f"{type(exc).__name__}: {exc}"


def list_devices() -> list[dict]:
    code, output = run_adb(["devices", "-l"])
    devices = []
    if code != 0:
        return devices
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        info = {"serial": parts[0], "state": parts[1], "model": ""}
        for token in parts[2:]:
            if token.startswith("model:"):
                info["model"] = token.split(":", 1)[1]
        devices.append(info)
    return devices


def screenshot_png() -> bytes:
    adb, serial = device_config()
    try:
        proc = subprocess.run(
            [adb, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode == 0 and proc.stdout[:8] == b"\x89PNG\r\n\x1a\n":
            return proc.stdout
    except Exception:  # noqa: BLE001
        pass
    return b""


def mumu_info() -> dict:
    if not MUMU_MANAGER.exists():
        return {"available": False}
    try:
        proc = subprocess.run(
            [str(MUMU_MANAGER), "info", "-v", "0"],
            capture_output=True,
            timeout=8,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        data = json.loads(proc.stdout or "{}")
        return {
            "available": True,
            "state": data.get("player_state", ""),
            "started": bool(data.get("is_android_started")),
            "port": data.get("adb_port"),
        }
    except Exception:  # noqa: BLE001
        return {"available": True, "state": "unknown"}


def maagc_process() -> dict:
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq MFAAvalonia.exe"],
            capture_output=True,
            timeout=8,
            text=True,
            encoding="gbk",
            errors="replace",
        )
        lines = [line for line in (proc.stdout or "").splitlines() if "MFAAvalonia" in line]
        if not lines:
            return {"running": False}
        parts = [part.strip().strip('"') for part in lines[0].split(",")]
        return {"running": True, "pid": parts[1] if len(parts) > 1 else ""}
    except Exception:  # noqa: BLE001
        return {"running": False}


def recent_logs(limit: int = 8) -> list[str]:
    files = sorted((ROOT / "logs").glob("log-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return []
    try:
        lines = files[0].read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except Exception:  # noqa: BLE001
        return []
    return [line[-220:] for line in lines[-limit:]]


def overview() -> dict:
    adb_path, serial = device_config()
    devices = list_devices()
    connected = any(item["state"] == "device" for item in devices)
    alerts = load_state(ROOT)["conditions"]
    return {
        "adbPath": adb_path,
        "serial": serial,
        "devices": devices,
        "connected": connected,
        "mumu": mumu_info(),
        "maagc": maagc_process(),
        "alerts": {"count": len(alerts), "cases": [item["caseName"] for item in alerts]},
        "logs": recent_logs(),
        "time": time.strftime("%H:%M:%S"),
    }


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    root: Path = ROOT

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        logger.debug("网页控制台 HTTP: " + (fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        return host.startswith(HOST)

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(403, {"ok": False, "errors": ["只允许本机访问"]})
            return

        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif path in ASSETS:
                name, content_type = ASSETS[path]
                self._send(200, (ASSET_DIR / name).read_bytes(), content_type)
            elif path == "/api/ping":
                self._json(200, {"ok": True, "app": "maagc-console"})
            elif path == "/api/overview":
                self._json(200, overview())
            elif path == "/api/state":
                self._json(200, load_state(self.root))
            elif path == "/api/screenshot":
                data = screenshot_png()
                if data:
                    self._send(200, data, "image/png")
                else:
                    self._json(503, {"ok": False, "errors": ["截图失败：设备未连接"]})
            else:
                self._json(404, {"ok": False, "errors": ["not found"]})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"网页控制台 GET {path} 失败: {exc}")
            self._json(500, {"ok": False, "errors": [str(exc)]})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._json(403, {"ok": False, "errors": ["只允许本机访问"]})
            return

        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"ok": False, "errors": [f"请求体不是合法 JSON：{exc}"]})
            return

        try:
            if path == "/api/save":
                conditions = payload.get("conditions")
                if not isinstance(conditions, list):
                    self._json(400, {"ok": False, "errors": ["缺少 conditions 数组"]})
                    return
                result = save_state(self.root, conditions)
                if result.get("ok"):
                    logger.info("好娃提醒配置已保存：" + "；".join(result.get("messages", [])))
                self._json(200 if result.get("ok") else 400, result)
            elif path == "/api/instance/sync":
                names = payload.get("caseNames")
                if not isinstance(names, list):
                    self._json(400, {"ok": False, "errors": ["缺少 caseNames 数组"]})
                    return
                changed = sync_instance_selection(self.root, [str(name) for name in names])
                self._json(
                    200,
                    {
                        "ok": True,
                        "changed": changed,
                        "message": (
                            "已更新实例勾选：" + "、".join(changed)
                            if changed
                            else "实例勾选已经一致，无需修改"
                        ),
                    },
                )
            elif path == "/api/device/connect":
                _, serial = device_config()
                code, output = run_adb(["connect", serial])
                self._json(200, {"ok": code == 0, "output": output.strip(), "devices": list_devices()})
            elif path == "/api/device/disconnect":
                _, serial = device_config()
                code, output = run_adb(["disconnect", serial])
                self._json(200, {"ok": code == 0, "output": output.strip(), "devices": list_devices()})
            elif path == "/api/device/reset":
                run_adb(["kill-server"], timeout=10)
                time.sleep(1)
                _, serial = device_config()
                code, output = run_adb(["connect", serial], timeout=20)
                self._json(200, {"ok": code == 0, "output": output.strip(), "devices": list_devices()})
            elif path == "/api/device/key":
                _, serial = device_config()
                key = str(payload.get("key") or "BACK")
                code, output = run_adb(["-s", serial, "shell", "input", "keyevent", key])
                self._json(200, {"ok": code == 0, "output": output.strip()})
            else:
                self._json(404, {"ok": False, "errors": ["not found"]})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"网页控制台 POST {path} 失败: {exc}")
            self._json(500, {"ok": False, "errors": [str(exc)]})


# ---------------------------------------------------------------- 生命周期


def existing_port() -> int | None:
    for port in PORT_RANGE:
        try:
            with urlopen(f"http://{HOST}:{port}/api/ping", timeout=0.3) as resp:
                if json.loads(resp.read().decode("utf-8")).get("app") == "maagc-console":
                    return port
        except Exception:  # noqa: BLE001
            continue
    return None


def ensure_server() -> int:
    global _server, _port
    with _lock:
        if _server is not None and _port is not None:
            return _port

        running = existing_port()
        if running is not None:
            _port = running
            return running

        last_error: Exception | None = None
        for port in PORT_RANGE:
            try:
                server = ThreadingHTTPServer((HOST, port), Handler)
            except OSError as exc:
                last_error = exc
                continue

            server.daemon_threads = True
            threading.Thread(
                target=server.serve_forever,
                name="maagc-web-console",
                daemon=True,
            ).start()
            _server, _port = server, port
            logger.info(f"网页控制台已启动: http://{HOST}:{port}")
            return port

        raise RuntimeError(f"{PORT_RANGE.start}-{PORT_RANGE.stop - 1} 端口都被占用：{last_error}")


def console_url() -> str:
    port = ensure_server()
    return f"http://{HOST}:{port}/"


def open_console() -> str:
    url = console_url()
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"自动打开浏览器失败，请手动访问 {url}：{exc}")
    try:
        path = ROOT / URL_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(url, encoding="utf-8")
        logger.info(f"网页控制台地址：{url}")
    except Exception:  # noqa: BLE001
        pass
    return url


def start_in_background() -> None:
    def _run() -> None:
        try:
            url = console_url()
            logger.info(f"网页控制台已就绪：{url}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"网页控制台启动失败: {exc}")

    threading.Thread(target=_run, name="web-console-start", daemon=True).start()


def shutdown() -> None:
    global _server, _port
    with _lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None
            _port = None
