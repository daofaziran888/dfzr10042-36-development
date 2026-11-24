"""空组出数证据的 HTTP 入口：证据登记、范围版本、领取、预览与正式发布。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.income.domain import IncomeEvidenceError, income_evidence_service

router = APIRouter(prefix="/api/income", tags=["空组出数证据"])


class EvidencePayload(BaseModel):
    period: str = Field(..., description="报告期，例如 2026-W47")
    channel: str = Field(..., description="收入渠道，例如 线上 / 线下")
    voucher_id: str = Field(..., description="凭据编号，同期内唯一，重送幂等")
    raw_value: Any = Field(..., description="原始观测：数字、0、null/空串或文本")
    source: str = Field(default="人工补录")


class ClaimPayload(BaseModel):
    period: str
    reviewer: str = Field(default="未署名复核员")


class PublishPayload(BaseModel):
    claim_id: int


@router.post("/evidence")
def submit_evidence(payload: EvidencePayload) -> dict[str, Any]:
    try:
        record = income_evidence_service.submit_observation(
            period=payload.period,
            channel=payload.channel,
            voucher_id=payload.voucher_id,
            raw_value=payload.raw_value,
            source=payload.source,
        )
    except IncomeEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    message = (
        f"凭据已登记为{record.status}行"
        if record.duplicate_of is None
        else f"凭据重复，指向首次登记行 {record.duplicate_of}，不增加有效记录"
    )
    return {"ok": True, "message": message, "evidence": record.to_dict()}


@router.get("/evidence")
def list_evidence(
    period: str | None = Query(default=None),
) -> dict[str, Any]:
    items = income_evidence_service.list_evidence(period)
    return {"items": items, "total": len(items)}


@router.get("/scopes")
def list_scopes(period: str = Query(...)) -> dict[str, Any]:
    items = income_evidence_service.list_scopes(period)
    return {"items": items, "total": len(items)}


@router.post("/claims")
def claim_scope(payload: ClaimPayload) -> dict[str, Any]:
    try:
        claim = income_evidence_service.claim_scope(payload.period, payload.reviewer)
    except IncomeEvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "message": f"已领取 {payload.period} 报告范围，钉住版本 v{claim.scope_version}",
        "claim": claim.to_dict(),
    }


@router.get("/claims")
def list_claims(period: str | None = Query(default=None)) -> dict[str, Any]:
    items = income_evidence_service.list_claims(period)
    return {"items": items, "total": len(items)}


@router.get("/report/preview")
def preview_report(
    period: str = Query(...),
    scope_version: int | None = Query(default=None),
) -> dict[str, Any]:
    try:
        return income_evidence_service.build_report(period, scope_version)
    except IncomeEvidenceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reports/publish")
def publish_report(payload: PublishPayload) -> dict[str, Any]:
    try:
        report = income_evidence_service.publish_report(payload.claim_id)
    except IncomeEvidenceError as exc:
        # 范围冲突、重复发布、并发二次确认都属于状态冲突
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "message": (
            f"正式报告已出件：合格渠道 {report.qualified_group_count} 个，"
            f"来源范围 v{report.scope_version}"
        ),
        "report": report.to_dict(),
    }


@router.get("/reports")
def list_reports(period: str | None = Query(default=None)) -> dict[str, Any]:
    items = income_evidence_service.list_reports(period)
    return {"items": items, "total": len(items)}
