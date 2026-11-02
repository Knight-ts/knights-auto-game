# -*- coding: utf-8 -*-
"""单独测试「功能：城堡资源记录」（Func_CastleResource）。

用法：
    python tasks/Func_CastleResource/run.py
    python tasks/Func_CastleResource/run.py --set 选项=值
    python tasks/Func_CastleResource/run.py --no-wait

等价于 python tools/taskpack.py run Func_CastleResource ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Func_CastleResource", *sys.argv[1:]]))
