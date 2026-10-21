# -*- coding: utf-8 -*-
"""单独测试「事件：海盗袭击」（Event_PirateRaid_Dispatch）。

用法：
    python tasks/Event_PirateRaid_Dispatch/run.py
    python tasks/Event_PirateRaid_Dispatch/run.py --no-wait

等价于 python tools/taskpack.py run Event_PirateRaid_Dispatch ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Event_PirateRaid_Dispatch", *sys.argv[1:]]))
