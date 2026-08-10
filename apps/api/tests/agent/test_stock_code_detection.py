"""上下文切换 · 股票码检测"""
import sys
import pytest

# orchestrator 依赖 online_analysis py3.10+ 语法，py 3.9 skip
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="orchestrator 依赖 py3.10+ 语法",
)


def test_detect_new_a_share():
    from app.services.agent.orchestrator import _detect_new_stock_code
    assert _detect_new_stock_code("那 000001 呢", current=None) == "000001"


def test_detect_new_hk_share():
    from app.services.agent.orchestrator import _detect_new_stock_code
    assert _detect_new_stock_code("00700 腾讯怎么样", current=None) == "00700"


def test_no_change_when_same_code():
    from app.services.agent.orchestrator import _detect_new_stock_code
    assert _detect_new_stock_code("茅台 600519 怎么样", current="600519") is None


def test_detect_change_when_different():
    from app.services.agent.orchestrator import _detect_new_stock_code
    assert _detect_new_stock_code("那 300750 呢", current="600519") == "300750"


def test_ignore_invalid_prefix():
    from app.services.agent.orchestrator import _detect_new_stock_code
    # 999999 不是有效 A 股前缀
    assert _detect_new_stock_code("看看 999999", current=None) is None


def test_ignore_when_no_code():
    from app.services.agent.orchestrator import _detect_new_stock_code
    assert _detect_new_stock_code("茅台呢", current="600519") is None
