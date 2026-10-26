# -*- coding: utf-8 -*-
"""单独测试「事件：城堡被攻打」（Event_CastleDefense）。

用法：
    python tasks/Event_CastleDefense/run.py
    python tasks/Event_CastleDefense/run.py --set 选项=值
    python tasks/Event_CastleDefense/run.py --no-wait

等价于 python tools/taskpack.py run Event_CastleDefense ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Event_CastleDefense", *sys.argv[1:]]))
