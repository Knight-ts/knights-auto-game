"""离线自检：任务接取相关 HTTP 接口与前端静态资源。

不连接设备、不启动 Agent：导入 server 后替换 runtime，用临时目录承接
规则文件，再起一个只监听本机随机端口的测试服务。

用法：python tools/selftest_task_web.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from webapp import server as srv  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


class StubRuntime:
    def status(self):
        return {"task": {"state": "idle"}}


def request(port: int, path: str, payload=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {"Host": f"127.0.0.1:{port}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Origin"] = f"http://127.0.0.1:{port}"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def test_base_inheritance(runtime) -> None:
    task = {
        "entry": "AutoFight_Start",
        "option": ["战斗模式", "待机回合数", "棋子识别"],
    }
    base = {
        "战斗模式": "只追击",
        "待机回合数": {"回合数": "10"},
        "棋子识别": "关闭",
    }
    inherited = runtime.build_override(
        task,
        {
            "战斗模式": "跟随月度循环",
            "待机回合数": {"回合数": ""},
            "棋子识别": "跟随月度循环",
        },
        base,
    )
    check(
        "继承模式",
        inherited.get("FightModeSelect") == {"expected": "pursuit"},
        str(inherited),
    )
    check(
        "继承待机回合数",
        inherited.get("FightStandbyRounds") == {"expected": ["10"]},
        str(inherited),
    )
    check(
        "继承棋子识别",
        inherited.get("FightPieceRecognition") == {"expected": "off"},
        str(inherited),
    )

    overridden = runtime.build_override(
        task, {"战斗模式": "只待机", "待机回合数": {"回合数": "0"}}, base
    )
    check(
        "显式覆盖模式",
        overridden.get("FightModeSelect") == {"expected": "standby"},
        str(overridden),
    )
    check(
        "显式覆盖回合数",
        overridden.get("FightStandbyRounds") == {"expected": ["0"]},
        str(overridden),
    )

    defaulted = runtime.build_override(task, {}, {})
    check(
        "没有基础配置时用默认",
        defaulted.get("FightStandbyRounds") == {"expected": ["20"]},
        str(defaulted),
    )


def test_base_migration() -> None:
    print("[基础配置] 旧版战斗设置迁移")
    import pathlib

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "webapp.json"
        path.write_text(
            json.dumps(
                {
                    "selections": {
                        "Auto_YearlyTask": {},
                        "AutoFight_Start": {
                            "战斗模式": "只追击",
                            "待机回合数": {"回合数": "10"},
                            "棋子识别": "关闭",
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        original = srv.CONFIG_PATH
        srv.CONFIG_PATH = path
        try:
            changed = srv.migrate_battle_base_config()
            data = json.loads(path.read_text(encoding="utf-8"))
            yearly = data["selections"]["Auto_YearlyTask"]
            donor = data["selections"]["AutoFight_Start"]
            check("迁移执行", changed is True)
            check(
                "基础配置拿到待机回合数",
                yearly.get("待机回合数") == {"回合数": "10"},
                json.dumps(yearly, ensure_ascii=False),
            )
            check("基础配置拿到模式", yearly.get("战斗模式") == "只追击")
            check("专门任务已恢复跟随", "待机回合数" not in donor, str(donor))
            check("重复调用不再迁移", srv.migrate_battle_base_config() is False)
        finally:
            srv.CONFIG_PATH = original


def main() -> int:
    runtime = srv.runtime
    with tempfile.TemporaryDirectory() as temp_dir:
        srv.runtime = StubRuntime()
        srv.task_rules.TABLE_DIR = temp_dir
        srv.task_rules._rules_cache = None
        srv.task_rules._pool_cache = None

        httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        try:
            print("[接口] 规则读取")
            status, body = request(port, "/api/task-rules")
            data = json.loads(body)
            check("GET 规则 200", status == 200)
            check("返回 defaults", "defaults" in data.get("rules", {}), body[:120])
            check("返回全局黑名单", isinstance(data.get("global_blacklist"), list))
            check("返回任务板", "pool" in data)
            check("返回城市列表", isinstance(data.get("cities"), list))

            print("[接口] 保存城市等级")
            status, body = request(
                port,
                "/api/task-rules/save",
                {"city": "暮角城", "min_level": 40, "max_level": 90},
            )
            check("POST 保存等级 200", status == 200, body[:160])
            rules = json.loads(body)["rules"]
            check(
                "城市等级已保存",
                rules["cities"]["暮角城"]["min_level"] == 40
                and rules["cities"]["暮角城"]["max_level"] == 90,
                json.dumps(rules, ensure_ascii=False),
            )

            print("[接口] 城市 / 全局黑名单")
            status, body = request(
                port,
                "/api/task-rules/blacklist",
                {"city": "暮角城", "task": "观察者"},
            )
            check("城市拉黑 200", status == 200, body[:160])
            rules = json.loads(body)["rules"]
            check(
                "城市名单包含任务",
                "观察者" in rules["cities"]["暮角城"]["blacklist"],
                json.dumps(rules, ensure_ascii=False),
            )

            status, body = request(
                port, "/api/task-rules/blacklist", {"task": "危机四伏"}
            )
            check("全局拉黑 200", status == 200, body[:160])
            check("全局名单包含任务", "危机四伏" in json.loads(body)["global_blacklist"])

            status, body = request(
                port,
                "/api/task-rules/unblacklist",
                {"city": "暮角城", "task": "观察者"},
            )
            check("城市解除 200", status == 200, body[:160])
            check(
                "城市名单已移除",
                "观察者"
                not in json.loads(body)["rules"]["cities"]["暮角城"]["blacklist"],
            )

            print("[页面] 静态资源")
            status, body = request(port, "/")
            check("首页 200", status == 200)
            check("首页含城市选择", 'id="tprecCity"' in body)
            check("首页含任务板", 'id="tprecPool"' in body)
            check("首页含规则保存", 'id="btnTprecSaveRule"' in body)

            status, body = request(port, "/app.js")
            check("app.js 200", status == 200)
            check("前端含任务规则加载", "loadTaskRecords" in body)
            check("前端含城市判定", "tprecEvaluate" in body)
        finally:
            httpd.shutdown()

    test_base_inheritance(runtime)
    test_base_migration()

    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
