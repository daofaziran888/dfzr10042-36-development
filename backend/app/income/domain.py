"""空组出数证据的领域逻辑（确定性、线程安全、不依赖 Web 框架）。

背景
----
收入观测数据里同时存在三种形态：

1. 有效行：可以转成金额的观测，金额为 0 也属于有效行，
   它证明该渠道「实测为零」，必须参加金额实算并计入有效分组；
2. 空行：没有任何观测（null / 空串），属于「无数据可算」，
   渠道只能列为待补，不能显示成已确认零；
3. 无效行：有内容但无法归成金额（例如纯文本），保留行并记录排除原因。

本模块维护一份不可抵赖的证据台账，每个报告期的「可用范围」以版本号
固化：复核员领取（claim）时钉住当时的范围版本，之后补记产生新版本，
领取件与完整证据之间的冲突因此可被显式识别。同份正式报告只允许发布
一次，同一凭据重送在任何情况下都不增加有效记录。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 证据处置结果
EVIDENCE_VALID = "有效"
EVIDENCE_EXCLUDED = "排除"

# 排除原因（空数据与无法归类的文本必须区分）
REASON_EMPTY = "无可用观测（空值），无数据可算，渠道列入待补"
REASON_NON_NUMERIC = "观测值无法归成金额，按无效值排除"
REASON_DUPLICATE = "凭据已登记，重复送审不重复计数"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_observation(raw_value: Any) -> tuple[bool, float | None, str]:
    """对单条观测做资格判定。

    返回 ``(是否有效, 金额或 None, 排除原因或空串)``。

    - None / 空串 / 纯空白：无数据可算（不是零，不能进实算）；
    - bool 不视作金额，避免 Python 把 True/False 当 1/0；
    - 数字或可解析的数字字符串：有效，0.0 同样有效（实测零的凭据）；
    - 其它文本：无效值，排除并保留原因。
    """
    if raw_value is None:
        return False, None, REASON_EMPTY
    if isinstance(raw_value, bool):
        return False, None, REASON_NON_NUMERIC
    if isinstance(raw_value, (int, float)):
        amount = float(raw_value)
        if amount == 0.0:
            amount = 0.0  # 归一化 -0.0，真实测得的零仍是有效行
        return True, amount, ""
    text = str(raw_value).strip()
    if not text:
        return False, None, REASON_EMPTY
    try:
        return True, float(text), ""
    except ValueError:
        return False, None, REASON_NON_NUMERIC


@dataclass
class EvidenceRecord:
    """一条收入观测凭据；有效行与被排除的行都保留在台账里。"""

    id: int
    period: str
    channel: str
    voucher_id: str
    raw_value: Any
    status: str  # EVIDENCE_VALID / EVIDENCE_EXCLUDED
    amount: float | None
    reason: str
    source: str
    created_at: str
    duplicate_of: int | None = None  # 重送指向首次登记的同凭据行

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "period": self.period,
            "channel": self.channel,
            "voucher_id": self.voucher_id,
            "raw_value": self.raw_value,
            "status": self.status,
            "amount": self.amount,
            "reason": self.reason,
            "source": self.source,
            "created_at": self.created_at,
            "duplicate_of": self.duplicate_of,
        }


@dataclass
class ScopeVersion:
    """报告期某一时刻的可用范围快照（只钉凭据 id，不复制业务数据）。"""

    version: int
    period: str
    evidence_ids: list[int]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "period": self.period,
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
        }


@dataclass
class Claim:
    """复核员对某期报告范围的领取记录，领取时钉住范围版本。"""

    id: int
    period: str
    scope_version: int
    reviewer: str
    created_at: str
    published: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "period": self.period,
            "scope_version": self.scope_version,
            "reviewer": self.reviewer,
            "created_at": self.created_at,
            "published": self.published,
        }


@dataclass
class PublishedReport:
    """正式出件；同一份报告的来源范围与合格组数必须对得上。"""

    id: int
    period: str
    claim_id: int
    scope_version: int
    reviewer: str
    channel_amounts: dict[str, float]
    qualified_channels: list[str]
    qualified_group_count: int
    pending_channels: list[dict[str, str]]
    total_amount: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "period": self.period,
            "claim_id": self.claim_id,
            "scope_version": self.scope_version,
            "reviewer": self.reviewer,
            "channel_amounts": dict(self.channel_amounts),
            "qualified_channels": list(self.qualified_channels),
            "qualified_group_count": self.qualified_group_count,
            "pending_channels": [dict(item) for item in self.pending_channels],
            "total_amount": self.total_amount,
            "created_at": self.created_at,
        }


class IncomeEvidenceError(Exception):
    """领域冲突（范围冲突、重复发布等），由路由层翻译成 409。"""


class IncomeEvidenceService:
    """空组出数证据台账与报告出件服务（进程内单例，带锁保证并发一致）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._evidence: list[EvidenceRecord] = []
        self._scopes: dict[str, list[ScopeVersion]] = {}
        self._claims: list[Claim] = []
        self._reports: list[PublishedReport] = []
        self._next_evidence_id = 1
        self._next_claim_id = 1
        self._next_report_id = 1

    # ------------------------------------------------------------------
    # 测试与复位
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._evidence.clear()
            self._scopes.clear()
            self._claims.clear()
            self._reports.clear()
            self._next_evidence_id = 1
            self._next_claim_id = 1
            self._next_report_id = 1

    # ------------------------------------------------------------------
    # 证据登记
    # ------------------------------------------------------------------
    def submit_observation(
        self,
        *,
        period: str,
        channel: str,
        voucher_id: str,
        raw_value: Any,
        source: str = "人工补录",
    ) -> EvidenceRecord:
        """登记一条观测凭据。

        同一报告期内 ``voucher_id`` 唯一：重送同一凭据只追加一条指向
        首次登记的重复行，既不增加有效记录，也不产生新的范围版本。
        """
        period = str(period).strip()
        channel = str(channel).strip()
        voucher_id = str(voucher_id).strip()
        if not period or not channel or not voucher_id:
            raise IncomeEvidenceError("报告期、渠道和凭据编号均不能为空")

        with self._lock:
            existing = self._find_voucher(period, voucher_id)
            if existing is not None:
                record = EvidenceRecord(
                    id=self._next_evidence_id,
                    period=period,
                    channel=channel,
                    voucher_id=voucher_id,
                    raw_value=raw_value,
                    status=existing.status,
                    amount=existing.amount,
                    reason=REASON_DUPLICATE,
                    source=source,
                    created_at=_now(),
                    duplicate_of=existing.id,
                )
                self._next_evidence_id += 1
                self._evidence.append(record)
                return record

            valid, amount, reason = classify_observation(raw_value)
            record = EvidenceRecord(
                id=self._next_evidence_id,
                period=period,
                channel=channel,
                voucher_id=voucher_id,
                raw_value=raw_value,
                status=EVIDENCE_VALID if valid else EVIDENCE_EXCLUDED,
                amount=amount,
                reason=reason,
                source=source,
                created_at=_now(),
            )
            self._next_evidence_id += 1
            self._evidence.append(record)
            # 新凭据（有效或排除）改变可用范围，追加一个不可变版本
            self._bump_scope(period)
            return record

    def _find_voucher(self, period: str, voucher_id: str) -> EvidenceRecord | None:
        for record in self._evidence:
            if (
                record.period == period
                and record.voucher_id == voucher_id
                and record.duplicate_of is None
            ):
                return record
        return None

    def _ensure_scopes(self, period: str) -> list[ScopeVersion]:
        return self._scopes.setdefault(
            period,
            [ScopeVersion(version=0, period=period, evidence_ids=[], created_at=_now())],
        )

    def _bump_scope(self, period: str) -> ScopeVersion:
        scopes = self._ensure_scopes(period)
        snapshot = [
            record.id
            for record in self._evidence
            if record.period == period and record.duplicate_of is None
        ]
        new_version = ScopeVersion(
            version=len(scopes),
            period=period,
            evidence_ids=snapshot,
            created_at=_now(),
        )
        scopes.append(new_version)
        return new_version

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_evidence(self, period: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._evidence
            if period is not None:
                rows = [row for row in rows if row.period == period]
            return [row.to_dict() for row in rows]

    def list_scopes(self, period: str) -> list[dict[str, Any]]:
        with self._lock:
            return [scope.to_dict() for scope in self._ensure_scopes(period)]

    def latest_scope_version(self, period: str) -> int:
        with self._lock:
            return self._ensure_scopes(period)[-1].version

    def list_claims(self, period: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._claims
            if period is not None:
                rows = [row for row in rows if row.period == period]
            return [row.to_dict() for row in rows]

    def list_reports(self, period: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._reports
            if period is not None:
                rows = [row for row in rows if row.period == period]
            return [row.to_dict() for row in rows]

    def _records_in_scope(self, period: str, scope_version: int) -> list[EvidenceRecord]:
        scopes = self._ensure_scopes(period)
        if scope_version < 0 or scope_version >= len(scopes):
            raise IncomeEvidenceError(
                f"报告期 {period} 不存在范围版本 {scope_version}"
            )
        ids = set(scopes[scope_version].evidence_ids)
        return [record for record in self._evidence if record.id in ids]

    # ------------------------------------------------------------------
    # 指标实算：从真实观测算各渠道金额与有效分组数
    # ------------------------------------------------------------------
    def build_report(
        self,
        period: str,
        scope_version: int | None = None,
    ) -> dict[str, Any]:
        """依据指定范围版本实算报告；缺省取该期最新版本。

        - 只有有效凭据进入金额实算与有效分组计数，金额 0 的有效组照算；
        - 只有排除凭据（空值/无效文本）、没有有效凭据的渠道列入待补，
          并逐行带出排除原因；
        - 空数据渠道绝不显示为「已确认零」。
        """
        with self._lock:
            if scope_version is None:
                scope_version = self.latest_scope_version(period)
            records = self._records_in_scope(period, scope_version)

            amounts: dict[str, float] = {}
            qualified: set[str] = set()
            pending_map: dict[str, list[str]] = {}

            for record in records:
                if record.status == EVIDENCE_VALID:
                    qualified.add(record.channel)
                    amounts[record.channel] = amounts.get(record.channel, 0.0) + (
                        record.amount or 0.0
                    )
                else:
                    pending_map.setdefault(record.channel, []).append(record.reason)

            for channel in qualified:
                if amounts[channel] == 0.0:
                    amounts[channel] = 0.0
                pending_map.pop(channel, None)

            qualified_channels = sorted(qualified)
            pending_channels = [
                {
                    "channel": channel,
                    "status": "待补",
                    "reasons": sorted(set(reasons)),
                }
                for channel, reasons in sorted(pending_map.items())
            ]
            total_amount = sum(amounts.values())
            if total_amount == 0.0:
                total_amount = 0.0

            return {
                "period": period,
                "scope_version": scope_version,
                "latest_scope_version": self.latest_scope_version(period),
                "scope_conflict": scope_version
                != self.latest_scope_version(period),
                "channel_amounts": {
                    channel: amounts[channel] for channel in qualified_channels
                },
                "qualified_channels": qualified_channels,
                "qualified_group_count": len(qualified_channels),
                "pending_channels": pending_channels,
                "total_amount": total_amount,
            }

    # ------------------------------------------------------------------
    # 领取与正式发布
    # ------------------------------------------------------------------
    def claim_scope(self, period: str, reviewer: str) -> Claim:
        """复核员领取当前最新可用范围，版本被钉住，后续补记不影响本领取件。"""
        period = str(period).strip()
        reviewer = str(reviewer).strip() or "未署名复核员"
        with self._lock:
            version = self.latest_scope_version(period)
            claim = Claim(
                id=self._next_claim_id,
                period=period,
                scope_version=version,
                reviewer=reviewer,
                created_at=_now(),
            )
            self._next_claim_id += 1
            self._claims.append(claim)
            return claim

    def publish_report(self, claim_id: int) -> PublishedReport:
        """按领取件正式出件。

        并发与冲突规则：
        - 同一报告期只保留一份正式报告，并发发布只有一个成功；
        - 领取版本落后于最新范围时判定为范围冲突，拒绝出件，
          必须基于完整新证据重新领取；
        - 同一领取件不可重复发布。
        """
        with self._lock:
            claim = next(
                (item for item in self._claims if item.id == claim_id), None
            )
            if claim is None:
                raise IncomeEvidenceError(f"领取记录 {claim_id} 不存在")
            if claim.published:
                raise IncomeEvidenceError(
                    f"领取记录 {claim_id} 已出件，同一报告不可重复发布"
                )
            if self._reports and any(
                report.period == claim.period for report in self._reports
            ):
                raise IncomeEvidenceError(
                    f"报告期 {claim.period} 已存在正式报告，并发确认只保留一份"
                )
            latest = self.latest_scope_version(claim.period)
            if claim.scope_version != latest:
                raise IncomeEvidenceError(
                    f"范围冲突：领取件钉住 v{claim.scope_version}，"
                    f"当前完整证据已到 v{latest}，请重新领取后再出件"
                )

            calculated = self.build_report(claim.period, claim.scope_version)
            report = PublishedReport(
                id=self._next_report_id,
                period=claim.period,
                claim_id=claim.id,
                scope_version=claim.scope_version,
                reviewer=claim.reviewer,
                channel_amounts=calculated["channel_amounts"],
                qualified_channels=calculated["qualified_channels"],
                qualified_group_count=calculated["qualified_group_count"],
                pending_channels=calculated["pending_channels"],
                total_amount=calculated["total_amount"],
                created_at=_now(),
            )
            self._next_report_id += 1
            self._reports.append(report)
            claim.published = True
            return report


# 进程内单例：路由层与测试共用同一份台账
income_evidence_service = IncomeEvidenceService()
