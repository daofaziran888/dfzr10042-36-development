import { request } from './client'

export type EvidenceRow = {
  id: number
  period: string
  channel: string
  voucher_id: string
  raw_value: unknown
  status: string
  amount: number | null
  reason: string
  source: string
  created_at: string
  duplicate_of: number | null
}

export type PendingChannel = {
  channel: string
  status: string
  reasons: string[]
}

export type ReportPreview = {
  period: string
  scope_version: number
  latest_scope_version: number
  scope_conflict: boolean
  channel_amounts: Record<string, number>
  qualified_channels: string[]
  qualified_group_count: number
  pending_channels: PendingChannel[]
  total_amount: number
}

export type ClaimRow = {
  id: number
  period: string
  scope_version: number
  reviewer: string
  created_at: string
  published: boolean
}

export type PublishedReport = ReportPreview & {
  id: number
  claim_id: number
  reviewer: string
  created_at: string
}

type MessageResult<T> = { ok: boolean; message: string } & T

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T
}

export async function submitEvidence(payload: {
  period: string
  channel: string
  voucher_id: string
  raw_value: unknown
  source?: string
}): Promise<MessageResult<{ evidence: EvidenceRow }>> {
  const response = await request('/api/income/evidence', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return readJson(response)
}

export async function listEvidence(period: string): Promise<{ items: EvidenceRow[]; total: number }> {
  const response = await request(`/api/income/evidence?period=${encodeURIComponent(period)}`)
  return readJson(response)
}

export async function listScopes(period: string): Promise<{ items: { version: number; evidence_ids: number[] }[]; total: number }> {
  const response = await request(`/api/income/scopes?period=${encodeURIComponent(period)}`)
  return readJson(response)
}

export async function claimScope(
  period: string,
  reviewer: string,
): Promise<MessageResult<{ claim: ClaimRow }>> {
  const response = await request('/api/income/claims', {
    method: 'POST',
    body: JSON.stringify({ period, reviewer }),
  })
  return readJson(response)
}

export async function listClaims(period: string): Promise<{ items: ClaimRow[]; total: number }> {
  const response = await request(`/api/income/claims?period=${encodeURIComponent(period)}`)
  return readJson(response)
}

export async function previewReport(
  period: string,
  scopeVersion?: number,
): Promise<ReportPreview> {
  const suffix = scopeVersion === undefined ? '' : `&scope_version=${scopeVersion}`
  const response = await request(
    `/api/income/report/preview?period=${encodeURIComponent(period)}${suffix}`,
  )
  return readJson(response)
}

export async function publishReport(
  claimId: number,
): Promise<{ ok: boolean; message: string; report?: PublishedReport; detail?: string }> {
  const response = await request('/api/income/reports/publish', {
    method: 'POST',
    body: JSON.stringify({ claim_id: claimId }),
  })
  return readJson(response)
}

export async function listReports(period: string): Promise<{ items: PublishedReport[]; total: number }> {
  const response = await request(`/api/income/reports?period=${encodeURIComponent(period)}`)
  return readJson(response)
}
