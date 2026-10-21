# -*- coding: utf-8 -*-
"""问题自纠的命令行入口：给 Harness / 批处理用，不启动网页也能整理报错截图。

常用：
    python tools/selfheal.py list                      # 看待判定 + 保留 + 已删除
    python tools/selfheal.py list --status kept --json # 机器可读，交给 Harness 复盘
    python tools/selfheal.py show <id>                  # 单条详情（含日志尾巴）
    python tools/selfheal.py keep <id> --note "第 3 步识别不到按钮"
    python tools/selfheal.py delete <id>                # 进回收站
    python tools/selfheal.py purge                      # 清空回收站
    python tools/selfheal.py report                     # 打印 REPORT.md

状态约定：pending=待判定，kept=用户确认值得修，deleted=用户认为没价值。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.selfheal import ErrorStore  # noqa: E402

CONFIG = ROOT / "config" / "webapp.json"


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _store() -> ErrorStore:
    return ErrorStore(ROOT, config_provider=_config)


def _print(item: dict) -> None:
    status = item.get("status", "pending")
    source = item.get("source_label") or item.get("source", "")
    size = item.get("size") or 0
    size_text = f"{size / 1024:.0f} KB" if size else "-"
    image = "有图" if item.get("has_image") else "无图"
    task = item.get("task_name") or item.get("entry") or "-"
    print(f"[{status}] {item.get('created_at', '')} · {item.get('title', '')}")
    print(f"    {source} · {task} · {image} · {size_text} · id={item.get('id', '')}")
    error = (item.get("error") or "").strip().splitlines()
    if error:
        print("    错误：" + error[0][:160])
    if item.get("note"):
        print("    备注：" + str(item["note"]).splitlines()[0][:160])


def cmd_list(store: ErrorStore, args) -> int:
    status = args.status if args.status in ("pending", "kept", "deleted") else None
    items = store.list(status=status, limit=args.limit)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("没有记录。")
        return 0
    for item in items:
        _print(item)
        print()
    stats = store.stats()
    print(
        f"共 {stats['total']} 条：待判定 {stats['pending']} · 保留 {stats['kept']}"
        f" · 已删除 {stats['deleted']} · 占用 {stats['mb']} MB"
    )
    print(f"目录：{stats['dir']}    报告：{stats['report']}")
    return 0


def cmd_show(store: ErrorStore, args) -> int:
    record = store.detail(args.id)
    if record is None:
        print(f"没有这条记录：{args.id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    _print(record)
    if record.get("path"):
        print(f"    目录：{record['path']}")
    if record.get("error"):
        print("\n错误：\n" + record["error"])
    if record.get("note"):
        print("\n备注：\n" + record["note"])
    if record.get("log_text"):
        print("\n日志（尾部）：\n" + record["log_text"])
    return 0


def cmd_mark(store: ErrorStore, args) -> int:
    record = store.mark(args.id, args.status, getattr(args, "note", None))
    if record is None:
        print(f"没有这条记录：{args.id}", file=sys.stderr)
        return 1
    print(f"{args.id} → {args.status}（{record.get('created_at', '')} {record.get('title', '')}）")
    return 0


def cmd_purge(store: ErrorStore, args) -> int:
    removed = store.purge(args.id)
    print(f"已彻底删除 {removed} 条")
    return 0


def cmd_clean(store: ErrorStore, args) -> int:
    result = store.cleanup()
    print(
        f"已清理 {result['removed']} 条（彻底删除），"
        f"{result.get('trashed', 0)} 条进入回收站"
    )
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    return 0


def cmd_report(store: ErrorStore, args) -> int:
    text = store.report()
    if args.write:
        print(store.report_path)
    else:
        print(text)
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(description="问题自纠：查看 / 保留 / 删除报错截图")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="列出记录")
    p_list.add_argument("--status", default="all", choices=["all", "pending", "kept", "deleted"])
    p_list.add_argument("--limit", type=int, default=300)
    p_list.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="单条详情（含日志）")
    p_show.add_argument("id")
    p_show.add_argument("--json", action="store_true")

    for name, status, help_text in (
        ("keep", "kept", "保留（值得修）"),
        ("pending", "pending", "退回待判定"),
        ("delete", "deleted", "删除（进回收站）"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id")
        if name == "keep":
            p.add_argument("--note", default=None)

    p_purge = sub.add_parser("purge", help="彻底删除（默认清空回收站）")
    p_purge.add_argument("id", nargs="?")

    sub.add_parser("clean", help="按设置清理过期 / 超限记录")

    p_report = sub.add_parser("report", help="打印 REPORT.md")
    p_report.add_argument("--write", action="store_true", help="只输出报告路径")

    sub.add_parser("path", help="输出报错目录")

    args = parser.parse_args(argv)
    if not args.cmd:
        args.cmd = "list"
        args.status = "all"
        args.limit = 300
        args.json = False

    store = _store()
    if args.cmd == "list":
        return cmd_list(store, args)
    if args.cmd == "show":
        return cmd_show(store, args)
    if args.cmd == "keep":
        args.status = "kept"
        return cmd_mark(store, args)
    if args.cmd == "pending":
        args.status = "pending"
        return cmd_mark(store, args)
    if args.cmd == "delete":
        args.status = "deleted"
        return cmd_mark(store, args)
    if args.cmd == "purge":
        return cmd_purge(store, args)
    if args.cmd == "clean":
        return cmd_clean(store, args)
    if args.cmd == "report":
        return cmd_report(store, args)
    if args.cmd == "path":
        print(store.base)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
