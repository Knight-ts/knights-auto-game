# -*- coding: utf-8 -*-
"""佣兵招募记录：table/mercenary_records.jsonl + .csv。

每条记录：时间、游戏年、游戏月、佣兵名字、结果（接受/拒绝）、匹配规则、备注。
用 CSV 是为了不依赖 openpyxl；Excel/WPS 可直接打开。
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE_DIR = ROOT / "table"
JSONL_PATH = TABLE_DIR / "mercenary_records.jsonl"
CSV_PATH = TABLE_DIR / "mercenary_records.csv"

HEADERS = ["时间", "游戏年", "游戏月", "佣兵名字", "结果", "匹配规则", "备注"]


def _read_rows() -> list[dict]:
    if not JSONL_PATH.exists():
        return []
    rows = []
    for line in JSONL_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_csv(rows: list[dict]) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CSV_PATH.with_name(CSV_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for row in rows:
            writer.writerow([row.get(key, "") for key in HEADERS])
    tmp.replace(CSV_PATH)


def append(record: dict) -> Path:
    """追加一条佣兵记录，返回 CSV 路径。"""
    record = dict(record)
    record.setdefault("时间", time.strftime("%Y-%m-%d %H:%M:%S"))
    row = {key: record.get(key, "") for key in HEADERS}
    rows = _read_rows()
    rows.append(row)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JSONL_PATH.with_name(JSONL_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        for item in rows:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp.replace(JSONL_PATH)
    _write_csv(rows)
    return CSV_PATH


def recent(limit: int = 100) -> list[dict]:
    return _read_rows()[-limit:]
