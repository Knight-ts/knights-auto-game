# -*- coding: utf-8 -*-
"""单独测试「事件：佣兵退休」（Event_MercenarieRetire）。

用法：
    python tasks/Event_MercenarieRetire/run.py
    python tasks/Event_MercenarieRetire/run.py --no-wait

等价于 python tools/taskpack.py run Event_MercenarieRetire ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Event_MercenarieRetire", *sys.argv[1:]]))
