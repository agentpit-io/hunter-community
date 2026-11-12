"""测试环境。**不连网、不连库、不连 Temporal** —— 全部靠假 HTTP 传输与纯函数。

`apps/fin-worker` 目录加入 sys.path，使 `import app.*` 在仓库任意位置可跑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # apps/fin-worker
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 默认环境：让 config 的取值确定，不受本机 env 影响。
# L06 · 两把钥匙分开：读 api 数据面用 HUNTER_INTERNAL_KEY，打 paper 下单用 HUNTER_EXEC_KEY。
os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("HUNTER_EXEC_KEY", "test-exec-key")
os.environ.setdefault("PAPER_BASE_URL", "http://paper.test")
os.environ.setdefault("API_BASE_URL", "http://api.test")
os.environ.setdefault("TEMPORAL_ADDRESS", "temporal.test:7233")
os.environ.setdefault("FIN_SAMPLE_CODE", "601398")
