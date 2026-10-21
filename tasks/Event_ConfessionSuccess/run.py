# -*- coding: utf-8 -*-
"""单独测试「事件：告白成功（含婚礼）」（Event_ConfessionSuccess）。

用法：
    python tasks/Event_ConfessionSuccess/run.py
    python tasks/Event_ConfessionSuccess/run.py --no-wait

等价于 python tools/taskpack.py run Event_ConfessionSuccess ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Event_ConfessionSuccess", *sys.argv[1:]]))
