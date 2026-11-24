<template>
  <section class="page" data-testid="income-evidence">
    <header class="page-head">
      <div>
        <h2>收入观测证据 · 空组出数</h2>
        <p class="page-desc">
          区分「无数据可算」与「真实测得零」：实测零参加金额实算并计入有效分组，
          空值与无法归类的文本只保留排除原因，渠道没有有效观测时单列待补。
        </p>
      </div>
      <div class="page-actions">
        <label class="period-picker">
          报告期
          <input v-model="period" data-testid="period-input" @change="reloadAll" />
        </label>
      </div>
    </header>

    <p v-if="message" class="notice" :class="messageKind" data-testid="notice">{{ message }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">有效收入渠道数</span>
        <strong class="stat-value" data-testid="qualified-count">{{ preview?.qualified_group_count ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">渠道金额合计</span>
        <strong class="stat-value" data-testid="total-amount">{{ preview ? preview.total_amount : '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待补渠道数</span>
        <strong class="stat-value" data-testid="pending-count">{{ preview?.pending_channels.length ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">当前范围版本</span>
        <strong class="stat-value">v{{ preview?.latest_scope_version ?? 0 }}</strong>
      </article>
    </div>

    <div class="evidence-grid">
      <article class="panel">
        <h3>登记观测凭据</h3>
        <form class="evidence-form" @submit.prevent="onSubmitEvidence">
          <label>
            渠道
            <input v-model="form.channel" data-testid="evidence-channel" placeholder="线上 / 线下" required />
          </label>
          <label>
            凭据编号
            <input v-model="form.voucher_id" data-testid="evidence-voucher" placeholder="同期唯一，重送幂等" required />
          </label>
          <label>
            原始观测（数字 / 0 / 留空表示空值 / 文本）
            <input v-model="form.rawText" data-testid="evidence-raw" placeholder="例如 0、128.5、线下未结算；留空=空值" />
          </label>
          <label>
            来源
            <input v-model="form.source" data-testid="evidence-source" placeholder="线上系统 / 线下补录" />
          </label>
          <div class="hint">
            空输入按「无可用观测（空值）」排除；无法解析为金额的文本按「无效值」排除；
            数字 0 是有效凭据，代表实测零收入。
          </div>
          <button class="btn primary" data-testid="evidence-submit" type="submit">登记凭据</button>
        </form>
      </article>

      <article class="panel">
        <h3>报告实算（真实观测）</h3>
        <div v-if="preview" class="report-box" data-testid="report-preview">
          <p v-if="preview.scope_conflict" class="notice conflict" data-testid="scope-conflict">
            范围冲突：当前预览钉住 v{{ preview.scope_version }}，完整证据已到
            v{{ preview.latest_scope_version }}，原领取件数据已落后。
          </p>
          <table class="data-table compact">
            <thead>
              <tr><th>渠道</th><th>实算金额</th><th>数据资格</th></tr>
            </thead>
            <tbody>
              <tr v-for="channel in preview.qualified_channels" :key="channel">
                <td>{{ channel }}</td>
                <td>{{ preview.channel_amounts[channel] }}</td>
                <td>
                  <span class="tag valid">
                    有效观测{{ preview.channel_amounts[channel] === 0 ? '（实测零）' : '' }}
                  </span>
                </td>
              </tr>
              <tr v-for="item in preview.pending_channels" :key="item.channel">
                <td>{{ item.channel }}</td>
                <td>—</td>
                <td>
                  <span class="tag pending">待补：无有效观测</span>
                  <ul class="reason-list">
                    <li v-for="reason in item.reasons" :key="reason">{{ reason }}</li>
                  </ul>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="claim-row">
            <input v-model="reviewer" data-testid="reviewer" placeholder="复核员" />
            <button class="btn" data-testid="claim-btn" type="button" @click="onClaim">领取当前范围</button>
          </div>
        </div>
        <p v-else class="hint">暂无范围，登记第一条凭据后生成。</p>
      </article>
    </div>

    <article class="panel">
      <h3>证据台账（有效行与排除行全保留）</h3>
      <table class="data-table compact" data-testid="evidence-table">
        <thead>
          <tr>
            <th>ID</th><th>渠道</th><th>凭据编号</th><th>原始观测</th>
            <th>资格</th><th>金额</th><th>排除原因 / 重送标记</th><th>来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in evidence" :key="row.id" :data-status="row.status">
            <td>{{ row.id }}</td>
            <td>{{ row.channel }}</td>
            <td>{{ row.voucher_id }}</td>
            <td>{{ formatRaw(row.raw_value) }}</td>
            <td>
              <span :class="['tag', row.status === '有效' ? 'valid' : 'excluded']">{{ row.status }}</span>
            </td>
            <td>{{ row.amount ?? '—' }}</td>
            <td>
              <template v-if="row.duplicate_of">重送，指向首次登记 #{{ row.duplicate_of }}（不计数）</template>
              <template v-else>{{ row.reason || '—' }}</template>
            </td>
            <td>{{ row.source }}</td>
          </tr>
          <tr v-if="!evidence.length">
            <td colspan="8" class="empty-state">本期暂无观测凭据</td>
          </tr>
        </tbody>
      </table>
    </article>

    <article class="panel">
      <h3>领取记录与正式出件</h3>
      <table class="data-table compact" data-testid="claim-table">
        <thead>
          <tr><th>领取ID</th><th>复核员</th><th>钉住版本</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="claim in claims" :key="claim.id">
            <td>{{ claim.id }}</td>
            <td>{{ claim.reviewer }}</td>
            <td>v{{ claim.scope_version }}</td>
            <td>{{ claim.published ? '已出件' : '待发布' }}</td>
            <td>
              <button
                class="link"
                type="button"
                :disabled="claim.published"
                :data-testid="`publish-${claim.id}`"
                @click="onPublish(claim.id)"
              >
                正式发布
              </button>
            </td>
          </tr>
          <tr v-if="!claims.length">
            <td colspan="5" class="empty-state">暂无领取记录</td>
          </tr>
        </tbody>
      </table>

      <h4>正式报告（同期只保留一份）</h4>
      <table class="data-table compact" data-testid="report-table">
        <thead>
          <tr><th>报告ID</th><th>来源版本</th><th>合格渠道数</th><th>渠道金额</th><th>复核员</th></tr>
        </thead>
        <tbody>
          <tr v-for="report in reports" :key="report.id">
            <td>{{ report.id }}</td>
            <td>v{{ report.scope_version }}</td>
            <td>{{ report.qualified_group_count }}</td>
            <td>{{ JSON.stringify(report.channel_amounts) }}</td>
            <td>{{ report.reviewer }}</td>
          </tr>
          <tr v-if="!reports.length">
            <td colspan="5" class="empty-state">尚未发布正式报告</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  claimScope,
  listClaims,
  listEvidence,
  listReports,
  previewReport,
  publishReport,
  submitEvidence,
  type ClaimRow,
  type EvidenceRow,
  type PublishedReport,
  type ReportPreview,
} from '@/api/income-service'

const period = ref('2026-W47')
const reviewer = ref('复核员甲')
const evidence = ref<EvidenceRow[]>([])
const claims = ref<ClaimRow[]>([])
const reports = ref<PublishedReport[]>([])
const preview = ref<ReportPreview | null>(null)
const message = ref('')
const messageKind = ref('info')
const form = ref({ channel: '', voucher_id: '', rawText: '', source: '人工补录' })

function formatRaw(value: unknown): string {
  if (value === null || value === '') return '（空值）'
  return String(value)
}

function notify(text: string, kind: 'info' | 'error' | 'success' = 'info') {
  message.value = text
  messageKind.value = kind
}

// 空串 -> null（无可用观测）；数字文本 -> 数字；其它文本原样送后端判定
function toRawValue(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return null
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed)
  return trimmed
}

async function reloadAll() {
  if (!period.value.trim()) return
  const [evidencePayload, claimPayload, reportPayload, previewPayload] = await Promise.all([
    listEvidence(period.value),
    listClaims(period.value),
    listReports(period.value),
    previewReport(period.value),
  ])
  evidence.value = evidencePayload.items
  claims.value = claimPayload.items
  reports.value = reportPayload.items
  preview.value = previewPayload
}

async function onSubmitEvidence() {
  try {
    const result = await submitEvidence({
      period: period.value,
      channel: form.value.channel,
      voucher_id: form.value.voucher_id,
      raw_value: toRawValue(form.value.rawText),
      source: form.value.source || '人工补录',
    })
    notify(result.message, 'success')
    form.value = { channel: '', voucher_id: '', rawText: '', source: '人工补录' }
    await reloadAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '凭据登记失败', 'error')
  }
}

async function onClaim() {
  try {
    const result = await claimScope(period.value, reviewer.value || '未署名复核员')
    notify(result.message, 'success')
    await reloadAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '领取失败', 'error')
  }
}

async function onPublish(claimId: number) {
  const result = await publishReport(claimId)
  if (!result.ok) {
    notify(result.detail ?? '发布被拒绝', 'error')
  } else {
    notify(result.message, 'success')
  }
  await reloadAll()
}

onMounted(reloadAll)
</script>

<style scoped>
.period-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.period-picker input {
  width: 140px;
}

.notice {
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  margin: 8px 0;
}

.notice.info,
.notice.success {
  background: #ecfdf5;
  border: 1px solid #34d399;
  color: #065f46;
}

.notice.error,
.notice.conflict {
  background: #fef2f2;
  border: 1px solid #f87171;
  color: #991b1b;
}

.evidence-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(360px, 1.4fr);
  gap: 12px;
  margin-bottom: 12px;
}

.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
}

.panel h3,
.panel h4 {
  margin: 4px 0 10px;
}

.evidence-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.evidence-form label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--muted);
}

.hint {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.data-table.compact {
  font-size: 13px;
}

.tag {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
}

.tag.valid {
  background: #dcfce7;
  color: #166534;
}

.tag.excluded,
.tag.pending {
  background: #fef3c7;
  color: #92400e;
}

.reason-list {
  margin: 4px 0 0;
  padding-left: 16px;
  color: var(--muted);
  font-size: 12px;
}

.claim-row {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.claim-row input {
  flex: 1;
}

button.link:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}
</style>
