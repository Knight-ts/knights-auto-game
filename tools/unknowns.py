# -*- coding: utf-8 -*-
"""未知界面的命令行入口：给 Harness / 批处理用，不启动网页也能看记录、选返回方式。

常用：
    python tools/unknowns.py list                     # 最近的未知画面
    python tools/unknowns.py list --json              # 机器可读
    python tools/unknowns.py show <id>                # 截图路径 + 调用栈 + 点过什么
    python tools/unknowns.py route <id> popup_tip     # 选返回方式（table/return_routes.json）
    python tools/unknowns.py route <id> default       # 改回默认路线
    python tools/unknowns.py note <id> "第 3 步的弹窗"
    python tools/unknowns.py delete <id>
    python tools/unknowns.py clear --yes
    python tools/unknowns.py report                   # 打印 debug/unknown/REPORT.md

约定：记录里的 `source_path:line` 就是出现未知画面的代码位置；
返回方式保存后，agent 下次遇到同一画面（指纹匹配）会照这几步执行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import unknowns  # noqa: E402


def _print_item(item: dict) -> None:
    task = item.get("task") or item.get("entry") or "未知任务"
    source = item.get("source_label") or "来源未知"
    print(f"{item.get('created_at', '')} · {task} · 命中 {item.get('hits', 1)} 次")
    print(f"    来源：{source}")
    print(f"    id={item.get('id', '')}")
    if item.get("reason"):
        print("    原因：" + str(item["reason"])[:200])
    attempts = item.get("attempts") or []
    if attempts:
        text = " → ".join(
            f"{step.get('step')}"
            + ("（变了）" if step.get("changed") is True else "（没变）" if step.get("changed") is False else "")
            for step in attempts
        )
        print("    点过：" + text)
    if item.get("trace"):
        first = (item.get("trace") or [{}])[0]
        print(f"    调用栈：{first.get('file')}:{first.get('line')} {first.get('function')}")
    if item.get("route_saved"):
        print(f"    返回方式：{item.get('route_label')} {' → '.join(item.get('route_steps') or [])}")
    else:
        print("    返回方式：未选择（用默认路线）")
    if item.get("note"):
        print("    备注：" + str(item["note"]).splitlines()[0][:200])
    if item.get("image_path"):
        print("    截图：" + item["image_path"])


def cmd_list(args) -> int:
    data = unknowns.state(limit=args.limit)
    items = data["items"]
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("没有未知界面记录。")
        print("遇到认不出的画面时，agent 会先点 3 个安全位置；画面仍无变化才记录。")
        return 0
    for item in items:
        _print_item(item)
        print()
    stats = data["stats"]
    print(
        f"共 {stats['total']} 条 · 累计遇到 {stats['hits']} 次 · "
        f"{stats['routed']} 条已选返回方式 · 占用 {stats['mb']} MB"
    )
    print(f"目录：{stats['dir']}")
    print("可选返回方式：" + "、".join(preset["id"] for preset in data["presets"]))
    return 0


def cmd_show(args) -> int:
    item = unknowns.detail(args.id)
    if item is None:
        print(f"没有这条记录：{args.id}")
        return 1
    _print_item(item)
    if item.get("probes"):
        print("\n识别探测：")
        for probe in item["probes"]:
            if probe.get("hit") or float(probe.get("score") or 0) >= 0.6:
                print(f"    {probe.get('node')}  score={probe.get('score')}  hit={probe.get('hit')}")
    if item.get("trace"):
        print("\n调用栈（从下往上）：")
        for frame in item["trace"]:
            print(f"    {frame.get('file')}:{frame.get('line')}  {frame.get('function')}")
    return 0


def cmd_route(args) -> int:
    route = "" if args.route in ("default", "默认", "-") else args.route
    try:
        item = unknowns.save_route(args.id, route)
    except ValueError as exc:
        print(f"保存失败：{exc}")
        return 1
    if item is None:
        print(f"没有这条记录：{args.id}")
        return 1
    if route:
        print(f"已保存返回方式：{item.get('route_label')} → {' → '.join(item.get('route_steps') or [])}")
    else:
        print("已改回默认返回方式。")
    return 0


def cmd_note(args) -> int:
    item = unknowns.note(args.id, args.text)
    if item is None:
        print(f"没有这条记录：{args.id}")
        return 1
    print("备注已保存。")
    return 0


def cmd_delete(args) -> int:
    if not unknowns.remove(args.id):
        print(f"没有这条记录：{args.id}")
        return 1
    print("已删除。")
    return 0


def cmd_clear(args) -> int:
    if not args.yes:
        print("清空未知界面记录需要加 --yes。")
        return 1
    result = unknowns.clear()
    print(f"已清空 {result.get('removed', 0)} 条记录。")
    return 0


def cmd_report(args) -> int:
    print(unknowns.store().report())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="未知界面记录 / 返回方式")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="列出记录")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--json", action="store_true", help="输出 JSON")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="单条详情")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_route = sub.add_parser("route", help="选择返回方式")
    p_route.add_argument("id")
    p_route.add_argument("route", help="预设 id，或 default 改回默认")
    p_route.set_defaults(func=cmd_route)

    p_note = sub.add_parser("note", help="写备注")
    p_note.add_argument("id")
    p_note.add_argument("text")
    p_note.set_defaults(func=cmd_note)

    p_delete = sub.add_parser("delete", help="删除一条记录")
    p_delete.add_argument("id")
    p_delete.set_defaults(func=cmd_delete)

    p_clear = sub.add_parser("clear", help="清空全部记录")
    p_clear.add_argument("--yes", action="store_true")
    p_clear.set_defaults(func=cmd_clear)

    p_report = sub.add_parser("report", help="打印 REPORT.md")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
