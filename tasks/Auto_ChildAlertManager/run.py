# -*- coding: utf-8 -*-
"""单独测试「网页控制台」（Auto_ChildAlertManager）。

用法：
    python tasks/Auto_ChildAlertManager/run.py
    python tasks/Auto_ChildAlertManager/run.py --set 选项=值
    python tasks/Auto_ChildAlertManager/run.py --no-wait

等价于 python tools/taskpack.py run Auto_ChildAlertManager ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_ChildAlertManager", *sys.argv[1:]]))
