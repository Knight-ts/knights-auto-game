# -*- coding: utf-8 -*-
"""单独测试「好娃提醒」（Auto_ChildAlertWindow）。

用法：
    python tasks/Auto_ChildAlertWindow/run.py
    python tasks/Auto_ChildAlertWindow/run.py --set 选项=值
    python tasks/Auto_ChildAlertWindow/run.py --no-wait

等价于 python tools/taskpack.py run Auto_ChildAlertWindow ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_ChildAlertWindow", *sys.argv[1:]]))
