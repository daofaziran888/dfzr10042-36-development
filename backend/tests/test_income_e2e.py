"""通过真实 uvicorn 服务跑完整入口链路（不依赖 TestClient/httpx）。

覆盖：空组、无效值、实际零的取数识别，交错补记、领取范围冲突、
正式发布并发确认与同凭据重送，核对实际出件的金额、渠道数与数据资格。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing

import pytest
import uvicorn

from app.income.domain import (
    REASON_EMPTY,
    REASON_NON_NUMERIC,
    income_evidence_service,
)
from app.main import app

PERIOD = "2026-W48"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server_base():
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    # 等待健康检查真正可连；重试间必须让出 GIL，否则秒拒的忙循环会饿死服务线程
    for _ in range(100):
        try:
            with closing(urllib.request.urlopen(f"{base}/api/health", timeout=1)):
                break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover - 仅启动异常时走到
        pytest.fail("后端 uvicorn 服务未在预期时间内就绪")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def clean_ledger():
    income_evidence_service.reset()
    yield
    income_evidence_service.reset()


def http_request(base: str, method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with closing(urllib.request.urlopen(request, timeout=5)) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_end_to_end_income_evidence_flow(server_base):
    base = server_base

    # 1) 线上实测一笔 0：有效凭据，范围推进到 v1
    status, body = http_request(base, "POST", "/api/income/evidence", {
        "period": PERIOD, "channel": "线上", "voucher_id": "V-ON-0", "raw_value": 0,
    })
    assert status == 200 and body["ok"] is True
    assert body["evidence"]["status"] == "有效"
    assert body["evidence"]["amount"] == 0.0

    # 2) 线下空值：识别为无数据可算，不允许当作零
    status, body = http_request(base, "POST", "/api/income/evidence", {
        "period": PERIOD, "channel": "线下", "voucher_id": "V-OFF-EMPTY", "raw_value": None,
    })
    assert status == 200
    assert body["evidence"]["status"] == "排除"
    assert body["evidence"]["reason"] == REASON_EMPTY

    # 3) 线下文本：无法归成金额，保留行与排除原因
    status, body = http_request(base, "POST", "/api/income/evidence", {
        "period": PERIOD, "channel": "线下", "voucher_id": "V-OFF-TXT",
        "raw_value": "线下未结算",
    })
    assert status == 200
    assert body["evidence"]["reason"] == REASON_NON_NUMERIC

    # 4) 报告入口预览：首次出表只有线上零收入，有效分组数为一
    status, first_view = http_request(
        base, "GET", f"/api/income/report/preview?period={PERIOD}"
    )
    assert status == 200
    assert first_view["qualified_group_count"] == 1
    assert first_view["channel_amounts"] == {"线上": 0.0}
    assert "线下" not in first_view["channel_amounts"]
    pending = {item["channel"]: item for item in first_view["pending_channels"]}
    assert pending["线下"]["status"] == "待补"
    assert set(pending["线下"]["reasons"]) == {REASON_EMPTY, REASON_NON_NUMERIC}

    # 5) 复核员领取原范围（v3）
    status, claim_a = http_request(base, "POST", "/api/income/claims", {
        "period": PERIOD, "reviewer": "复核员甲",
    })
    assert status == 200
    assert claim_a["claim"]["scope_version"] == 3

    # 6) 正式出件前补记线下有效的一笔 0，范围推进到 v4
    status, body = http_request(base, "POST", "/api/income/evidence", {
        "period": PERIOD, "channel": "线下", "voucher_id": "V-OFF-0", "raw_value": 0,
    })
    assert status == 200 and body["evidence"]["status"] == "有效"

    # 7) 原领取件视角仍是一个有效组，且接口标明范围冲突
    status, stale = http_request(
        base, "GET",
        f"/api/income/report/preview?period={PERIOD}&scope_version=3",
    )
    assert status == 200
    assert stale["qualified_group_count"] == 1
    assert stale["scope_conflict"] is True

    # 完整新证据视角可出两个零收入组
    status, latest = http_request(
        base, "GET", f"/api/income/report/preview?period={PERIOD}"
    )
    assert latest["qualified_group_count"] == 2
    assert latest["pending_channels"] == []

    # 8) 用落后领取件发布 -> 409 范围冲突
    status, body = http_request(base, "POST", "/api/income/reports/publish", {
        "claim_id": claim_a["claim"]["id"],
    })
    assert status == 409
    assert "范围冲突" in body["detail"]

    # 9) 重新领取完整证据并正式发布
    status, claim_b = http_request(base, "POST", "/api/income/claims", {
        "period": PERIOD, "reviewer": "复核员甲",
    })
    assert claim_b["claim"]["scope_version"] == 4
    status, published = http_request(base, "POST", "/api/income/reports/publish", {
        "claim_id": claim_b["claim"]["id"],
    })
    assert status == 200 and published["ok"] is True
    report = published["report"]
    assert report["qualified_group_count"] == 2
    assert report["channel_amounts"] == {"线上": 0.0, "线下": 0.0}
    assert report["scope_version"] == 4

    # 10) 同凭据重送：不增加有效记录、不推进范围、正式报告仍只一份
    status, body = http_request(base, "POST", "/api/income/evidence", {
        "period": PERIOD, "channel": "线下", "voucher_id": "V-OFF-0", "raw_value": 0,
    })
    assert status == 200
    assert body["evidence"]["duplicate_of"] is not None
    status, scopes = http_request(
        base, "GET", f"/api/income/scopes?period={PERIOD}"
    )
    assert scopes["total"] == 5  # v0..v4，重送未新增版本
    status, reports = http_request(
        base, "GET", f"/api/income/reports?period={PERIOD}"
    )
    assert reports["total"] == 1

    # 11) 并发确认只保留一份：另一复核员新领取后发布被拒
    status, claim_c = http_request(base, "POST", "/api/income/claims", {
        "period": PERIOD, "reviewer": "复核员乙",
    })
    status, body = http_request(base, "POST", "/api/income/reports/publish", {
        "claim_id": claim_c["claim"]["id"],
    })
    assert status == 409 and "只保留一份" in body["detail"]

    # 12) 台账同时保留各渠道有效行与排除原因
    status, ledger = http_request(
        base, "GET", f"/api/income/evidence?period={PERIOD}"
    )
    statuses = [(row["channel"], row["status"], row["reason"]) for row in ledger["items"]]
    assert ("线上", "有效", "") in statuses
    assert ("线下", "排除", REASON_EMPTY) in statuses
    assert ("线下", "排除", REASON_NON_NUMERIC) in statuses
    assert ("线下", "有效", "") in statuses
    # 重复行也留痕，但带重复标记
    duplicates = [row for row in ledger["items"] if row["duplicate_of"] is not None]
    assert len(duplicates) == 1


def test_concurrent_publish_over_http_keeps_one_report(server_base):
    base = server_base
    period = "2026-CONC"
    http_request(base, "POST", "/api/income/evidence", {
        "period": period, "channel": "线上", "voucher_id": "C1", "raw_value": 0,
    })
    claim_ids = []
    for i in range(6):
        _, body = http_request(base, "POST", "/api/income/claims", {
            "period": period, "reviewer": f"复核员{i}",
        })
        claim_ids.append(body["claim"]["id"])

    barrier = threading.Barrier(6)
    results: list[int] = []
    lock = threading.Lock()

    def worker(claim_id: int) -> None:
        barrier.wait()
        status, _ = http_request(
            base, "POST", "/api/income/reports/publish", {"claim_id": claim_id}
        )
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker, args=(cid,)) for cid in claim_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(200) == 1
    assert results.count(409) == 5
    _, reports = http_request(base, "GET", f"/api/income/reports?period={period}")
    assert reports["total"] == 1
    assert reports["items"][0]["qualified_group_count"] == 1
