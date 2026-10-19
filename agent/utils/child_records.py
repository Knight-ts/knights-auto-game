# -*- coding: utf-8 -*-
"""孩子记录：每次生娃命名后追加一行，并生成 Excel（.xlsx）与 .csv。

不依赖 openpyxl/pandas，直接写最小可用的 xlsx（zip + xml），Excel/WPS 可直接打开。
数据源是 table/child_records.jsonl，每次追加后整体重建 xlsx，避免并发写坏表格。
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HEADERS = [
    "时间",
    "孩子序号",
    "名字",
    "最高爵位",
    "力量",
    "体质",
    "技巧",
    "感知",
    "敏捷",
    "意志",
    "血脉",
    "特性",
    "好娃提醒",
    "匹配条件",
    "招募状态",
]

TABLE_DIR = Path("table")
JSONL_PATH = TABLE_DIR / "child_records.jsonl"
XLSX_PATH = TABLE_DIR / "child_records.xlsx"
CSV_PATH = TABLE_DIR / "child_records.csv"

_COL_WIDTHS = [20, 10, 18, 10, 8, 8, 8, 8, 8, 8, 28, 32, 10, 18, 12]


# ---------------------------------------------------------------- 读写


def _read_rows() -> list[dict]:
    if not JSONL_PATH.exists():
        return []
    rows = []
    try:
        with open(JSONL_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _write_rows(rows: list[dict]) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JSONL_PATH.with_name(JSONL_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(JSONL_PATH)


def append_record(record: dict) -> Path:
    """追加一条孩子记录，并重建 xlsx / csv。"""
    row = {key: record.get(key, "") for key in HEADERS}
    rows = _read_rows()
    rows.append(row)
    _write_rows(rows)
    rebuild_exports(rows)
    return XLSX_PATH


def mark_recruited(name: str, value: str = "已招募") -> int:
    """把指定名字（最近的同名记录）的招募状态改掉，返回修改条数。"""
    rows = _read_rows()
    changed = 0
    for row in reversed(rows):
        if row.get("名字") == name:
            row["招募状态"] = value
            changed = 1
            break
    if changed:
        _write_rows(rows)
        rebuild_exports(rows)
    return changed


def recent(limit: int = 10) -> list[dict]:
    return _read_rows()[-limit:]


# ---------------------------------------------------------------- xlsx


def _col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value) -> str:
    if isinstance(value, bool):
        value = "是" if value else "否"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" t="n"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[dict]) -> str:
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(_COL_WIDTHS)
    )
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        f"<cols>{cols}</cols>",
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>",
        "<sheetData>",
    ]

    header_cells = "".join(
        _cell(f"{_col_name(i)}1", title) for i, title in enumerate(HEADERS)
    )
    lines.append(f'<row r="1">{header_cells}</row>')

    for row_index, row in enumerate(rows, start=2):
        cells = "".join(
            _cell(f"{_col_name(i)}{row_index}", row.get(title, ""))
            for i, title in enumerate(HEADERS)
        )
        lines.append(f'<row r="{row_index}">{cells}</row>')

    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def rebuild_exports(rows: list[dict] | None = None) -> None:
    rows = rows if rows is not None else _read_rows()
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="孩子记录" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    tmp = XLSX_PATH.with_name(XLSX_PATH.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))
    tmp.replace(XLSX_PATH)

    # CSV 兜底：万一 Excel 打不开也有一份能看的
    csv_tmp = CSV_PATH.with_name(CSV_PATH.name + ".tmp")
    with open(csv_tmp, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write(",".join(HEADERS) + "\n")
        for row in rows:
            values = []
            for title in HEADERS:
                text = str(row.get(title, "")).replace('"', '""')
                values.append(f'"{text}"')
            handle.write(",".join(values) + "\n")
    csv_tmp.replace(CSV_PATH)


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
