"""幂等键：必须从**业务身份**推导，重试/重启得到同一个字符串。"""

from __future__ import annotations

from app.bridge import idem


def test_order_key_is_deterministic():
    a = idem.order_key("prj_1", "2026-10-09", "0930", "d1")
    b = idem.order_key("prj_1", "2026-10-09", "0930", "d1")
    assert a == b


def test_order_key_changes_with_each_component():
    base = idem.order_key("prj_1", "2026-10-09", "0930", "d1")
    assert base != idem.order_key("prj_2", "2026-10-09", "0930", "d1")
    assert base != idem.order_key("prj_1", "2026-10-10", "0930", "d1")
    assert base != idem.order_key("prj_1", "2026-10-09", "1300", "d1")
    assert base != idem.order_key("prj_1", "2026-10-09", "0930", "d2")


def test_job_key_is_point_scoped():
    a = idem.point_job_key("prj_1", "2026-10-09", "1530")
    assert a == "fin-job:prj_1:2026-10-09:1530"
    assert a != idem.point_job_key("prj_1", "2026-10-09", "0930")


def test_sample_decision_id_has_no_randomness():
    a = idem.sample_decision_id("sample-fixed", "2026-10-09", "0930", "601398")
    b = idem.sample_decision_id("sample-fixed", "2026-10-09", "0930", "601398")
    assert a == b
    # 不含 uuid / 时间戳风格的分段（只是说明「确定性」这一意图）
    assert a.count("-") >= 5
