# -*- coding: utf-8 -*-
"""单独测试「事件：提示弹窗」（PopUpWindowTip）。

用法：
    python tasks/PopUpWindowTip/run.py
    python tasks/PopUpWindowTip/run.py --no-wait

等价于 python tools/taskpack.py run PopUpWindowTip ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "PopUpWindowTip", *sys.argv[1:]]))
