# -*- coding: utf-8 -*-
"""任务包：把 interface.json 的任务拆成 tasks/<entry>/，支持导出、重建、打包、导入和单独测试。

目录结构：
    tasks/
      index.json           任务顺序（["Auto_YearlyTask", ...]）
      _options.json        所有选项定义（和 interface.json 的 option 同构）
      <entry>/task.json    单个任务（原样保存 interface.json 里的一项）
      <entry>/run.py       单独运行这个任务的脚本（已存在时不会覆盖）

常用命令：
    python tools/taskpack.py export --force        # interface.json -> tasks/
    python tools/taskpack.py build                 # tasks/ -> interface.json（自动留 .bak）
    python tools/taskpack.py check                 # 检查任务/选项/pipeline 引用
    python tools/taskpack.py pack CastleMarryTest  # 打成 dist/tasks/CastleMarryTest.zip
    python tools/taskpack.py unpack dist/tasks/CastleMarryTest.zip --build
    python tools/taskpack.py run CastleMarryTest --set 联姻方式=联姻
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERFACE = ROOT / "interface.json"
TASKS_DIR = ROOT / "tasks"
INDEX = TASKS_DIR / "index.json"
OPTIONS = TASKS_DIR / "_options.json"
PIPELINE_DIR = ROOT / "resource" / "base" / "pipeline"
PACK_FORMAT = 1

RUN_TEMPLATE = '''# -*- coding: utf-8 -*-
"""单独测试「{name}」（{entry}）。

用法：
    python tasks/{entry}/run.py
    python tasks/{entry}/run.py --set 选项=值
    python tasks/{entry}/run.py --no-wait

等价于 python tools/taskpack.py run {entry} ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "{entry}", *sys.argv[1:]]))
'''


# ---------------------------------------------------------------- 基础读写


def _read(path: Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write(path: Path, data) -> None:
    """先备份 .bak，再原子替换；缩进统一 4 空格、LF。"""
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
        handle.write("\n")
    tmp.replace(path)


def _result(ok: bool, messages: list[str] | None = None, errors: list[str] | None = None) -> dict:
    return {"ok": ok, "messages": messages or [], "errors": errors or []}


def _entry_order(iface: dict) -> list[str]:
    return [task.get("entry", "") for task in iface.get("task", []) if task.get("entry")]


def _task_folders() -> list[str]:
    if not TASKS_DIR.exists():
        return []
    return sorted(path.name for path in TASKS_DIR.iterdir() if path.is_dir())


def _pipeline_nodes() -> set[str]:
    """粗略收集 pipeline 里出现过的节点名（JSON 带注释，用正则而不是 json）。"""
    names: set[str] = set()
    if not PIPELINE_DIR.exists():
        return names
    for path in PIPELINE_DIR.glob("*.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        names.update(re.findall(r'"([^"\n]+)"\s*:\s*\{', text))
    return names


# ---------------------------------------------------------------- 导出 / 重建


def export(entry: str | None = None, force: bool = False) -> dict:
    """把 interface.json 的任务拆到 tasks/<entry>/。已存在的文件夹默认不覆盖。"""
    if not INTERFACE.exists():
        return _result(False, errors=[f"找不到 {INTERFACE}"])
    iface = _read(INTERFACE)
    tasks = iface.get("task", []) or []
    options = iface.get("option", {}) or {}

    entries = [task.get("entry", "") for task in tasks]
    TASKS_DIR.mkdir(parents=True, exist_ok=True)

    messages: list[str] = []
    errors: list[str] = []
    exported: list[str] = []
    skipped: list[str] = []

    for task in tasks:
        name = task.get("entry", "")
        if not name:
            errors.append(f"有任务没有 entry：{task.get('name', '?')}")
            continue
        if entry and name != entry:
            continue
        folder = TASKS_DIR / name
        if folder.exists() and not force:
            skipped.append(name)
            continue
        task_file = folder / "task.json"
        if task_file.exists() and not force:
            skipped.append(name)
            continue
        _write(task_file, task)
        run_file = folder / "run.py"
        if not run_file.exists():
            run_file.write_text(
                RUN_TEMPLATE.format(name=task.get("name", name), entry=name),
                encoding="utf-8",
                newline="\n",
            )
        exported.append(name)

    if entry and entry not in entries and entry not in skipped:
        errors.append(f"interface.json 里没有这个任务：{entry}")

    # 顺序：保留已有的自定义顺序，新任务按 interface.json 的先后追加
    old_order = []
    if INDEX.exists():
        try:
            old_order = [str(item) for item in _read(INDEX).get("order", [])]
        except Exception:  # noqa: BLE001
            old_order = []
    order = [item for item in old_order if item in entries]
    for item in entries:
        if item not in order:
            order.append(item)
    if order:
        _write(INDEX, {"order": order})

    # 只有全量导出才刷新 _options.json，避免单任务导出冲掉别的选项
    if not entry:
        _write(OPTIONS, options)

    if exported:
        messages.append(f"已导出 {len(exported)} 个任务：{'、'.join(exported)}")
    if skipped:
        messages.append(f"已存在、未覆盖（加 --force 覆盖）：{'、'.join(skipped)}")
    messages.append(f"任务顺序：{INDEX.relative_to(ROOT)}")
    return _result(not errors, messages, errors)


def build(dry_run: bool = False) -> dict:
    """把 tasks/ 合回 interface.json（先做校验，有错不写）。"""
    if not INTERFACE.exists():
        return _result(False, errors=[f"找不到 {INTERFACE}"])
    if not TASKS_DIR.exists():
        return _result(False, errors=[f"找不到 {TASKS_DIR}，先运行 export"])

    iface = _read(INTERFACE)
    messages: list[str] = []
    errors: list[str] = []

    order: list[str] = []
    if INDEX.exists():
        order = [str(item) for item in _read(INDEX).get("order", [])]
    folders = _task_folders()
    for name in folders:
        if name not in order:
            order.append(name)
            messages.append(f"{name} 不在 index.json，按字母序追加")

    tasks: list[dict] = []
    seen: dict[str, str] = {}
    for name in order:
        task_file = TASKS_DIR / name / "task.json"
        if not task_file.exists():
            errors.append(f"缺少 {task_file.relative_to(ROOT)}")
            continue
        try:
            task = _read(task_file)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}/task.json 解析失败：{exc}")
            continue
        task_entry = str(task.get("entry", ""))
        if task_entry != name:
            errors.append(f"{name}/task.json 的 entry 是 {task_entry!r}，应与文件夹同名")
        if task_entry in seen:
            errors.append(f"entry 重复：{task_entry}（{seen[task_entry]} 与 {name}）")
        seen[task_entry] = name
        if not task.get("name"):
            errors.append(f"{name}/task.json 缺少 name")
        tasks.append(task)

    options: dict = {}
    if OPTIONS.exists():
        try:
            options = _read(OPTIONS)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tasks/_options.json 解析失败：{exc}")
    else:
        errors.append("缺少 tasks/_options.json")

    if isinstance(options, dict):
        for task in tasks:
            for option_name in task.get("option", []) or []:
                if option_name not in options:
                    errors.append(f"{task.get('name', '?')} 引用了不存在的选项：{option_name}")

    if errors:
        return _result(False, messages, errors)

    old_entries = _entry_order(iface)
    old_options = list((iface.get("option") or {}).keys())
    new_entries = [task.get("entry", "") for task in tasks]

    new_iface = dict(iface)
    new_iface["task"] = tasks
    new_iface["option"] = options

    if old_entries != new_entries:
        messages.append(f"任务顺序：{'、'.join(old_entries)} → {'、'.join(new_entries)}")
    if old_options != list(options.keys()):
        messages.append(f"选项顺序有变化（{len(old_options)} → {len(options)} 个）")
    if new_iface == iface:
        messages.append("interface.json 已经和 tasks/ 一致，无需修改")
        return _result(True, messages)

    messages.insert(0, f"将写入 {len(tasks)} 个任务、{len(options)} 个选项")
    if dry_run:
        messages.append("（--dry-run，没有写入文件）")
        return _result(True, messages)
    _write(INTERFACE, new_iface)
    messages.append("interface.json 已更新（旧文件备份为 interface.json.bak），重启 Knights Auto Game 后生效")
    return _result(True, messages)


# ---------------------------------------------------------------- 检查


def check() -> dict:
    """检查 tasks/ 与 interface.json 的一致性、选项引用和 pipeline 节点引用。"""
    messages: list[str] = []
    errors: list[str] = []

    if not TASKS_DIR.exists():
        return _result(False, errors=[f"找不到 {TASKS_DIR}，先运行 export"])
    if not INTERFACE.exists():
        return _result(False, errors=[f"找不到 {INTERFACE}"])

    iface = _read(INTERFACE)
    interface_entries = _entry_order(iface)
    folders = _task_folders()

    missing = [name for name in interface_entries if name not in folders]
    extra = [name for name in folders if name not in interface_entries]
    if missing:
        messages.append(f"interface.json 有、tasks/ 没有（导出后会补上）：{'、'.join(missing)}")
    if extra:
        messages.append(f"tasks/ 有、interface.json 没有（build 后会加入）：{'、'.join(extra)}")

    options = _read(OPTIONS) if OPTIONS.exists() else {}
    interface_options = iface.get("option", {}) or {}
    if not OPTIONS.exists():
        errors.append("缺少 tasks/_options.json")
    else:
        only_interface = [name for name in interface_options if name not in options]
        only_tasks = [name for name in options if name not in interface_options]
        if only_interface:
            messages.append(f"选项只在 interface.json 里：{'、'.join(only_interface)}")
        if only_tasks:
            messages.append(f"选项只在 tasks/_options.json 里：{'、'.join(only_tasks)}")

    order = []
    if INDEX.exists():
        order = [str(item) for item in _read(INDEX).get("order", [])]
    for name in folders:
        if name not in order:
            messages.append(f"{name} 不在 index.json（build 时按字母序追加）")

    nodes = _pipeline_nodes()
    referenced: set[str] = set()
    for task in iface.get("task", []) or []:
        for node in (task.get("pipeline_override") or {}):
            referenced.add(node)
    for option in interface_options.values():
        for case in option.get("cases", []) or []:
            for node in (case.get("pipeline_override") or {}):
                referenced.add(node)
    missing_nodes = sorted(node for node in referenced if node not in nodes)
    if missing_nodes:
        messages.append(
            "这些 pipeline_override 节点在 resource/base/pipeline 里找不到（任务可能秒失败）："
            + "、".join(missing_nodes)
        )

    if not errors:
        messages.insert(0, f"检查完成：{len(folders)} 个任务文件夹，{len(options)} 个选项")
    return _result(not errors, messages, errors)


# ---------------------------------------------------------------- 打包 / 导入


def pack(entry: str, output: str | None = None) -> dict:
    """把一个任务打成 zip（task.json + 它用到的选项 + run.py）。"""
    folder = TASKS_DIR / entry
    task_file = folder / "task.json"
    if not task_file.exists():
        return _result(False, errors=[f"找不到 {task_file}，先运行 export"])

    task = _read(task_file)
    all_options = _read(OPTIONS) if OPTIONS.exists() else {}
    used = {name: all_options[name] for name in task.get("option", []) or [] if name in all_options}

    out = Path(output) if output else ROOT / "dist" / "tasks" / f"{entry}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "format": PACK_FORMAT,
        "entry": entry,
        "name": task.get("name", entry),
        "packed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    readme = (
        f"# 任务包：{meta['name']}\n\n"
        f"- entry：`{entry}`\n"
        f"- 打包时间：{meta['packed_at']}\n\n"
        "导入：`python tools/taskpack.py unpack 这个文件.zip --build`\n"
        "或在网页版「任务 → 任务包」里填 zip 路径导入。\n"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("taskpack.json", json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("task.json", json.dumps(task, ensure_ascii=False, indent=4) + "\n")
        archive.writestr("options.json", json.dumps(used, ensure_ascii=False, indent=4) + "\n")
        archive.writestr("README.md", readme)
        run_file = folder / "run.py"
        if run_file.exists():
            archive.write(run_file, "run.py")

    return _result(
        True,
        [
            f"已打包：{out}",
            f"包含 {len(used)} 个选项" + ("、run.py" if (folder / 'run.py').exists() else ""),
        ],
    )


def unpack(zip_path: str, force: bool = False, rebuild: bool = False) -> dict:
    """导入一个任务包 zip 到 tasks/，可选顺便 build。"""
    path = Path(zip_path)
    if not path.exists():
        return _result(False, errors=[f"找不到 {path}"])
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "taskpack.json" not in names or "task.json" not in names:
                return _result(False, errors=["不是任务包：缺少 taskpack.json 或 task.json"])
            meta = json.loads(archive.read("taskpack.json").decode("utf-8"))
            task = json.loads(archive.read("task.json").decode("utf-8"))
            option_defs = (
                json.loads(archive.read("options.json").decode("utf-8"))
                if "options.json" in names
                else {}
            )
            run_bytes = archive.read("run.py") if "run.py" in names else None
    except zipfile.BadZipFile:
        return _result(False, errors=[f"不是合法的 zip：{path}"])

    entry = str(meta.get("entry") or task.get("entry") or "").strip()
    messages: list[str] = []
    errors: list[str] = []
    if not entry:
        return _result(False, errors=["任务包没有 entry"])
    if str(task.get("entry") or entry) != entry:
        errors.append(f"taskpack.json 的 entry（{entry}）和 task.json 的 entry（{task.get('entry')}）不一致")

    folder = TASKS_DIR / entry
    if folder.exists() and not force:
        errors.append(f"{folder.relative_to(ROOT)} 已存在；加 --force 覆盖")
    if errors:
        return _result(False, messages, errors)

    _write(folder / "task.json", task)
    if run_bytes is not None:
        (folder / "run.py").write_bytes(run_bytes)
    elif not (folder / "run.py").exists():
        (folder / "run.py").write_text(
            RUN_TEMPLATE.format(name=task.get("name", entry), entry=entry),
            encoding="utf-8",
            newline="\n",
        )
    messages.append(f"已写入 tasks/{entry}/")

    current = _read(OPTIONS) if OPTIONS.exists() else {}
    conflicts = [
        name for name, definition in (option_defs or {}).items()
        if name in current and current[name] != definition
    ]
    if conflicts and not force:
        return _result(
            False,
            messages,
            [
                "这些选项已存在且定义不同，加 --force 用任务包里的版本覆盖："
                + "、".join(conflicts)
            ],
        )
    for name, definition in (option_defs or {}).items():
        current[name] = definition
    if option_defs:
        _write(OPTIONS, current)
        messages.append(f"已合并 {len(option_defs)} 个选项定义")

    order = []
    if INDEX.exists():
        order = [str(item) for item in _read(INDEX).get("order", [])]
    if entry not in order:
        order.append(entry)
    _write(INDEX, {"order": order})
    messages.append("已加入 index.json")

    if rebuild:
        result = build()
        messages.extend(result["messages"])
        if result["errors"]:
            return _result(False, messages, result["errors"])
        if not result["ok"]:
            return _result(False, messages, ["build 失败"])
    else:
        messages.append("记得运行 build（或网页版「任务 → 任务包 → 重建 interface.json」）")
    return _result(True, messages)


# ---------------------------------------------------------------- 单独运行


def _http(base: str, path: str, payload: dict | None = None, timeout: float = 60):
    if payload is None:
        request = urllib.request.Request(base + path)
    else:
        request = urllib.request.Request(
            base + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(body).get("error") or body
        except Exception:  # noqa: BLE001
            message = body
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc


def run(entry: str, selections: list[str] | None = None, port: int = 8790, wait: bool = True) -> dict:
    """通过正在运行的网页版（默认 8790）单独跑一个任务，方便改完立刻测试。"""
    base = f"http://127.0.0.1:{port}"
    try:
        state = _http(base, "/api/state", timeout=5)
    except Exception as exc:  # noqa: BLE001
        return _result(
            False,
            errors=[f"连不上网页版 {base}（{exc}）；先双击 启动网页版.bat，或加 --port 指定端口"],
        )

    selection: dict[str, str] = {}
    for item in selections or []:
        if "=" not in item:
            return _result(False, errors=[f"--set 格式是 选项=值，收到：{item}"])
        key, value = item.split("=", 1)
        selection[key.strip()] = value.strip()

    name = None
    try:
        tasks = _http(base, "/api/tasks", timeout=10)
        for task in tasks if isinstance(tasks, list) else tasks.get("tasks", []):
            if task.get("entry") == entry:
                name = task.get("name", entry)
                break
    except Exception:  # noqa: BLE001
        pass
    if name is None:
        return _result(
            False,
            errors=[f"网页版的任务列表里没有 {entry}；是不是改了 tasks/ 还没 build？"],
        )

    messages = [f"运行「{name}」（{entry}）" + (f"，选项 {selection}" if selection else "")]
    try:
        started = _http(base, "/api/task/run", {"entry": entry, "name": name, "selection": selection})
    except Exception as exc:  # noqa: BLE001
        return _result(False, messages, [f"启动失败：{exc}"])
    if not started.get("task"):
        messages.append(f"返回：{started}")
    if not wait:
        return _result(True, messages)

    for _ in range(180):
        time.sleep(1)
        try:
            task = _http(base, "/api/state", timeout=10).get("task") or {}
        except Exception:  # noqa: BLE001
            continue
        if task.get("state") in ("done", "failed"):
            messages.append(f"结束：{task.get('state')}（{task.get('name')}）")
            return _result(task.get("state") == "done", messages)
        if task.get("state") == "idle":
            messages.append("任务空闲（可能已经结束）")
            return _result(True, messages)
    return _result(False, messages, ["等待超时（180 秒）"])


# ---------------------------------------------------------------- 命令行


def _print_result(result: dict) -> None:
    for line in result.get("messages", []):
        print(line)
    for line in result.get("errors", []):
        print("[错误] " + line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="taskpack", description="任务包：拆分/重建/打包/导入/单独运行 interface.json 的任务"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export", help="interface.json -> tasks/<entry>/")
    p.add_argument("entry", nargs="?", help="只导出某个任务")
    p.add_argument("--force", action="store_true", help="覆盖已存在的任务文件夹")

    p = sub.add_parser("build", help="tasks/ -> interface.json")
    p.add_argument("--dry-run", action="store_true", help="只检查，不写文件")

    sub.add_parser("check", help="检查任务/选项/pipeline 引用")
    sub.add_parser("list", help="列出 tasks/ 里的任务")

    p = sub.add_parser("pack", help="打包一个任务 zip")
    p.add_argument("entry")
    p.add_argument("-o", "--output", help="输出 zip 路径")

    p = sub.add_parser("unpack", help="导入一个任务包 zip")
    p.add_argument("zip")
    p.add_argument("--force", action="store_true", help="覆盖同名任务和选项")
    p.add_argument("--build", action="store_true", help="导入后立刻重建 interface.json")

    p = sub.add_parser("run", help="通过网页版单独运行某个任务")
    p.add_argument("entry")
    p.add_argument("--set", action="append", default=[], help="选项=值，可重复")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--no-wait", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "export":
        result = export(args.entry, args.force)
    elif args.command == "build":
        result = build(args.dry_run)
    elif args.command == "check":
        result = check()
    elif args.command == "list":
        result = list_tasks()
    elif args.command == "pack":
        result = pack(args.entry, args.output)
    elif args.command == "unpack":
        result = unpack(args.zip, args.force, args.build)
    elif args.command == "run":
        result = run(args.entry, args.set, args.port, not args.no_wait)
    else:  # pragma: no cover
        parser.error("未知命令")
        return 2

    _print_result(result)
    return 0 if result.get("ok") else 1


def status() -> dict:
    """给网页版用的概览：tasks/ 与 interface.json 的任务是否一致。"""
    folders = _task_folders()
    order = []
    if INDEX.exists():
        try:
            order = [str(item) for item in _read(INDEX).get("order", [])]
        except Exception:  # noqa: BLE001
            order = []
    interface_entries = _entry_order(_read(INTERFACE)) if INTERFACE.exists() else []
    options_count = 0
    if OPTIONS.exists():
        try:
            options_count = len(_read(OPTIONS))
        except Exception:  # noqa: BLE001
            options_count = 0
    return {
        "ok": True,
        "tasksDir": str(TASKS_DIR),
        "folders": folders,
        "order": order,
        "interfaceTasks": interface_entries,
        "options": options_count,
        "consistent": sorted(folders) == sorted(interface_entries),
    }


def list_tasks() -> dict:
    """list 命令：titles/文件夹/选项数量/描述。"""
    if not TASKS_DIR.exists():
        return _result(False, errors=["还没有 tasks/，先运行 export"])
    order = []
    if INDEX.exists():
        order = [str(item) for item in _read(INDEX).get("order", [])]
    folders = _task_folders()
    for name in folders:
        if name not in order:
            order.append(name)

    messages = [f"{len(order)} 个任务（tasks/）："]
    for index, name in enumerate(order, 1):
        task_file = TASKS_DIR / name / "task.json"
        if not task_file.exists():
            messages.append(f"{index:>2}. {name}  [缺少 task.json]")
            continue
        task = _read(task_file)
        options = len(task.get("option", []) or [])
        messages.append(
            f"{index:>2}. {task.get('name', name)}  ({name})  选项 {options} 个"
        )
    return _result(True, messages)


if __name__ == "__main__":
    raise SystemExit(main())
