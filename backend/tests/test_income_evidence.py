"""空组出数证据领域测试：空组、无效值、实际零、交错补记与并发确认。"""
from __future__ import annotations

import threading

import pytest

from app.income.domain import (
    EVIDENCE_EXCLUDED,
    EVIDENCE_VALID,
    REASON_DUPLICATE,
    REASON_EMPTY,
    REASON_NON_NUMERIC,
    IncomeEvidenceError,
    classify_observation,
    income_evidence_service as service,
)

PERIOD = "2026-W47"


@pytest.fixture(autouse=True)
def clean_ledger():
    service.reset()
    yield
    service.reset()


# ============================================================
# 一、观测资格判定：区分无数据可算、无效文本与真实测得零
# ============================================================

def test_classify_empty_is_not_zero():
    for raw in (None, "", "   "):
        valid, amount, reason = classify_observation(raw)
        assert valid is False
        assert amount is None
        assert reason == REASON_EMPTY


def test_classify_measured_zero_is_valid():
    for raw in (0, 0.0, "0", "0.0", " 0 "):
        valid, amount, reason = classify_observation(raw)
        assert valid is True
        assert amount == 0.0
        assert reason == ""


def test_classify_numeric_text_and_numbers():
    assert classify_observation(123.5) == (True, 123.5, "")
    assert classify_observation("99.9") == (True, 99.9, "")
    assert classify_observation(-0.0) == (True, 0.0, "")


def test_classify_unparsable_text_is_excluded():
    valid, amount, reason = classify_observation("线下未结算")
    assert valid is False
    assert amount is None
    assert reason == REASON_NON_NUMERIC
    # 布尔值不得被悄悄当成 0/1 金额
    assert classify_observation(True)[0] is False
    assert classify_observation(False)[2] == REASON_NON_NUMERIC


# ============================================================
# 二、完整叙事：线上实测零、线下空值+无效文本、交错补记与正式发布
# ============================================================

def test_full_narrative_empty_group_vs_measured_zero():
    # 1) 线上确实有一笔 0 —— 有效凭据，产生范围 v1
    online_zero = service.submit_observation(
        period=PERIOD, channel="线上", voucher_id="V-ONLINE-0", raw_value=0
    )
    assert online_zero.status == EVIDENCE_VALID
    assert online_zero.amount == 0.0
    assert service.latest_scope_version(PERIOD) == 1

    # 2) 线下只有空值 —— 排除，原因是无数据可算，范围 v2
    offline_empty = service.submit_observation(
        period=PERIOD, channel="线下", voucher_id="V-OFFLINE-EMPTY", raw_value=None
    )
    assert offline_empty.status == EVIDENCE_EXCLUDED
    assert offline_empty.reason == REASON_EMPTY

    # 3) 线下还有无法归成金额的文本 —— 排除并保留原因，范围 v3
    offline_text = service.submit_observation(
        period=PERIOD, channel="线下", voucher_id="V-OFFLINE-TXT", raw_value="线下未结算"
    )
    assert offline_text.status == EVIDENCE_EXCLUDED
    assert offline_text.reason == REASON_NON_NUMERIC
    assert service.latest_scope_version(PERIOD) == 3

    # 4) 首次出表：只有线上零收入，实际有效分组数为一
    first_view = service.build_report(PERIOD)
    assert first_view["qualified_group_count"] == 1
    assert first_view["qualified_channels"] == ["线上"]
    assert first_view["channel_amounts"] == {"线上": 0.0}
    assert first_view["total_amount"] == 0.0
    # 线下不能显示成已确认零：不在金额表中，只能单列待补并带排除原因
    assert "线下" not in first_view["channel_amounts"]
    pending = {item["channel"]: item for item in first_view["pending_channels"]}
    assert pending["线下"]["status"] == "待补"
    assert REASON_EMPTY in pending["线下"]["reasons"]
    assert REASON_NON_NUMERIC in pending["线下"]["reasons"]

    # 5) 复核员领取原报告范围（钉住 v3）
    claim_a = service.claim_scope(PERIOD, "复核员甲")
    assert claim_a.scope_version == 3
    claimed_view = service.build_report(PERIOD, claim_a.scope_version)
    assert claimed_view["qualified_group_count"] == 1
    assert claimed_view["scope_conflict"] is False

    # 6) 正式出件前，线下有效的一笔 0 补记进来 —— 完整新证据推进到 v4
    offline_zero = service.submit_observation(
        period=PERIOD, channel="线下", voucher_id="V-OFFLINE-0", raw_value=0
    )
    assert offline_zero.status == EVIDENCE_VALID
    assert service.latest_scope_version(PERIOD) == 4

    # 7) 两份可用范围冲突：继续原领取件仍只见一个有效组
    stale_view = service.build_report(PERIOD, claim_a.scope_version)
    assert stale_view["qualified_group_count"] == 1
    assert stale_view["scope_conflict"] is True
    latest_view = service.build_report(PERIOD)
    assert latest_view["qualified_group_count"] == 2
    assert latest_view["channel_amounts"] == {"线上": 0.0, "线下": 0.0}
    assert latest_view["pending_channels"] == []

    # 8) 用落后的领取件直接发布 —— 范围冲突，拒绝出件
    with pytest.raises(IncomeEvidenceError, match="范围冲突"):
        service.publish_report(claim_a.id)

    # 9) 采用完整新证据重新领取，本期可出两个零收入组
    claim_b = service.claim_scope(PERIOD, "复核员甲")
    assert claim_b.scope_version == 4
    report = service.publish_report(claim_b.id)
    assert report.qualified_group_count == 2
    assert report.channel_amounts == {"线上": 0.0, "线下": 0.0}
    assert report.total_amount == 0.0
    assert report.scope_version == 4
    # 同份正式报告的来源与合格组数对得上
    assert set(report.channel_amounts) == set(report.qualified_channels)
    assert len(report.qualified_channels) == report.qualified_group_count

    # 10) 线下补记同一凭据重送 —— 不增加有效记录、不推进范围版本
    duplicate = service.submit_observation(
        period=PERIOD, channel="线下", voucher_id="V-OFFLINE-0", raw_value=0
    )
    assert duplicate.duplicate_of == offline_zero.id
    assert duplicate.reason == REASON_DUPLICATE
    assert service.latest_scope_version(PERIOD) == 4
    after_resend = service.build_report(PERIOD)
    assert after_resend["qualified_group_count"] == 2
    valid_rows = [row for row in service.list_evidence(PERIOD)
                  if row["status"] == EVIDENCE_VALID and row["duplicate_of"] is None]
    assert len(valid_rows) == 2  # 线上 0 + 线下 0，重送未新增有效行

    # 11) 同一领取件不可重复发布；本期正式报告并发确认只保留一份
    with pytest.raises(IncomeEvidenceError, match="已出件"):
        service.publish_report(claim_b.id)
    claim_c = service.claim_scope(PERIOD, "复核员乙")
    with pytest.raises(IncomeEvidenceError, match="只保留一份"):
        service.publish_report(claim_c.id)
    assert len(service.list_reports(PERIOD)) == 1


def test_real_amounts_sum_from_valid_rows_only():
    service.submit_observation(
        period=PERIOD, channel="线上", voucher_id="V1", raw_value=100
    )
    service.submit_observation(
        period=PERIOD, channel="线上", voucher_id="V2", raw_value="50.5"
    )
    service.submit_observation(
        period=PERIOD, channel="线上", voucher_id="V3", raw_value=None
    )
    view = service.build_report(PERIOD)
    assert view["qualified_group_count"] == 1
    assert view["channel_amounts"] == {"线上": 150.5}
    assert view["total_amount"] == 150.5
    # 空值行仍保留在台账里并标明排除原因
    excluded = [row for row in service.list_evidence(PERIOD)
                if row["status"] == EVIDENCE_EXCLUDED]
    assert excluded and excluded[0]["reason"] == REASON_EMPTY


def test_scope_versions_are_immutable_snapshots():
    service.submit_observation(
        period=PERIOD, channel="线上", voucher_id="V1", raw_value=0
    )
    service.submit_observation(
        period=PERIOD, channel="线下", voucher_id="V2", raw_value=None
    )
    scopes = service.list_scopes(PERIOD)
    assert [scope["version"] for scope in scopes] == [0, 1, 2]
    assert scopes[1]["evidence_ids"] == [1]
    # 后续补记不会改写历史版本
    service.submit_observation(
        period=PERIOD, channel="线下", voucher_id="V3", raw_value=0
    )
    scopes = service.list_scopes(PERIOD)
    assert scopes[1]["evidence_ids"] == [1]
    assert scopes[3]["evidence_ids"] == [1, 2, 3]


# ============================================================
# 三、并发确认：同一份正式报告只保留一份
# ============================================================

def test_concurrent_publish_keeps_single_report():
    period = "2026-CONC"
    service.submit_observation(
        period=period, channel="线上", voucher_id="C1", raw_value=0
    )
    claims = [service.claim_scope(period, f"复核员{i}") for i in range(8)]
    start = threading.Barrier(8)
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def worker(claim_id: int) -> None:
        start.wait()
        try:
            service.publish_report(claim_id)
            result = "ok"
        except IncomeEvidenceError:
            result = "conflict"
        with outcome_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(claim.id,)) for claim in claims]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 7
    reports = service.list_reports(period)
    assert len(reports) == 1
    published_claims = [item for item in service.list_claims(period) if item["published"]]
    assert len(published_claims) == 1
    assert reports[0]["qualified_group_count"] == 1
    assert reports[0]["channel_amounts"] == {"线上": 0.0}
