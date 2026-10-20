# -*- coding: utf-8 -*-
"""单独测试「启动游戏」（GameStartUp）。

用法：
    python tasks/GameStartUp/run.py
    python tasks/GameStartUp/run.py --set 选项=值
    python tasks/GameStartUp/run.py --no-wait

等价于 python tools/taskpack.py run GameStartUp ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "GameStartUp", *sys.argv[1:]]))
