# -*- coding: utf-8 -*-
"""单独测试「事件：孩子夭折」（事件_孩子夭折了）。

用法：
    python tasks/事件_孩子夭折了/run.py
    python tasks/事件_孩子夭折了/run.py --no-wait

等价于 python tools/taskpack.py run 事件_孩子夭折了 ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "事件_孩子夭折了", *sys.argv[1:]]))
