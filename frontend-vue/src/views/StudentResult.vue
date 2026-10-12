<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { fetchResult, retryEvaluate, getCredentials, ApiError } from '../api'
import FormulaText from '../components/FormulaText.vue'
import { useIsMobile } from '../useMedia'

const router = useRouter()
const { t, locale } = useI18n()
const isMobile = useIsMobile()
const sid = getCredentials()?.sid || ''
const result = ref(null)
const loadError = ref('')
const retrying = ref(false)
const refreshing = ref(false)

const STATE_META = computed(() => ({
  none: { label: t('stateNone'), step: 0, color: 'muted' },
  evaluating: { label: t('stateEvaluating'), step: 1, color: 'normal' },
  eval_failed: { label: t('stateEvalFailed'), step: 1, color: 'danger' },
  awaiting_review: { label: t('stateAwaiting'), step: 2, color: 'normal' },
  feedback_ready: { label: t('stateReady'), step: 3, color: 'strong' },
}))

const stateMeta = computed(() => STATE_META.value[result.value?.state] || STATE_META.value.none)
const nextSteps = computed(() => {
  if (!result.value) return []
  return locale.value === 'en' && result.value.next_steps_en?.length
    ? result.value.next_steps_en
    : result.value.next_steps || []
})
const feedbackText = computed(() => {
  const fb = result.value?.feedback
  if (!fb) return ''
  return locale.value === 'en' && fb.text_en ? fb.text_en : fb.text
})

onMounted(load)
onBeforeUnmount(stopPolling)

// 评价异步化：提交后回执立即可见，AI 批改在后台进行。
// state === evaluating 期间每 5 秒轮询一次，直到反馈就绪/失败/待复核。
let pollHandle = null
function stopPolling() {
  if (pollHandle) {
    clearInterval(pollHandle)
    pollHandle = null
  }
}
function startPolling() {
  if (pollHandle) return
  pollHandle = setInterval(async () => {
    if (result.value?.state !== 'evaluating') {
      stopPolling()
      return
    }
    try {
      const fresh = await fetchResult(sid)
      if (fresh.state !== 'evaluating') {
        result.value = fresh
        stopPolling()
      }
    } catch { /* 网络抖动等下一轮 */ }
  }, 5000)
}

async function load() {
  try {
    result.value = await fetchResult(sid)
    if (result.value?.state === 'evaluating') startPolling()
    else stopPolling()
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      router.push('/student')
      return
    }
    loadError.value = e.message || t('loadFailed')
  }
}

async function refresh() {
  refreshing.value = true
  try {
    await load()
  } finally {
    refreshing.value = false
  }
}

async function retry() {
  retrying.value = true
  try {
    const receipt = await retryEvaluate(sid)
    ElMessage.success(receipt.evaluation_status === 'ok' ? t('evalDone') : t('evalStillFailed'))
    await load()
  } catch (e) {
    ElMessage.error(e.detail || e.message || t('evalStillFailed'))
  } finally {
    retrying.value = false
  }
}

function evalText(s) {
  if (s === 'ok') return t('evalOkShort')
  if (s === 'failed') return t('evalFailedShort')
  return t('evalRunning')
}
function evalColor(s) {
  return s === 'ok' ? 'normal' : s === 'failed' ? 'danger' : 'muted'
}
</script>

<template>
  <div class="page page-narrow">
    <div v-if="loadError">
      <el-alert type="error" :title="loadError" show-icon :closable="false" />
      <el-button style="margin-top:12px" @click="load">{{ t('retry') }}</el-button>
    </div>

    <template v-else-if="!result">
      <el-skeleton :rows="6" animated />
    </template>

    <template v-else>
      <header class="result-head">
        <h1 class="page-title">{{ t('myResult') }}</h1>
        <button class="text-link" type="button" :disabled="refreshing" @click="refresh">
          {{ t('refreshState') }}
        </button>
      </header>

      <section class="block">
        <p class="state-line" :class="`is-${stateMeta.color}`">{{ stateMeta.label }}</p>
        <p v-if="result.state === 'evaluating'" class="evaluating-hint">
          <span class="hint-spinner" />{{ t('evalAutoRefresh') }}
        </p>
        <el-steps
          :active="stateMeta.step + 1"
          :direction="isMobile ? 'vertical' : 'horizontal'"
          :align-center="!isMobile"
          finish-status="success"
        >
          <el-step :title="t('stepSubmitted')" />
          <el-step :title="t('stepEvaluate')" />
          <el-step :title="t('stepReview')" />
          <el-step :title="t('stepFeedback')" />
        </el-steps>
      </section>

      <section v-if="result.state === 'none'" class="block">
        <p class="block-text">{{ t('noAttempts') }}</p>
        <el-button type="primary" @click="router.push('/student/exam')">{{ t('goExam') }}</el-button>
      </section>

      <section v-if="result.feedback" class="block">
        <h2 class="section-title">{{ t('feedbackTitle') }}</h2>
        <p class="block-text">
          <FormulaText :text="feedbackText" />
        </p>
        <p v-if="result.feedback.source === 'review'" class="caption">{{ t('feedbackByReview') }}</p>
      </section>

      <section v-if="nextSteps.length" class="block">
        <h2 class="section-title">{{ t('nextSteps') }}</h2>
        <ul class="plain-list">
          <li v-for="(s, i) in nextSteps" :key="i">{{ s }}</li>
        </ul>
        <el-button
          v-if="result.state === 'eval_failed'"
          class="retry-btn"
          type="primary"
          :loading="retrying"
          @click="retry"
        >{{ t('retryEval') }}</el-button>
      </section>

      <section class="block">
        <h2 class="section-title">{{ t('attemptsTitle') }}</h2>

        <el-table v-if="!isMobile" :data="result.attempts" size="small">
          <el-table-column :label="t('attemptNo')" width="80">
            <template #default="{ row }">{{ t('attemptN', { n: row.attempt_no }) }}</template>
          </el-table-column>
          <el-table-column :label="t('submittedAt')">
            <template #default="{ row }">{{ new Date(row.submitted_at).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column :label="t('examVersion')" width="90">
            <template #default="{ row }">v{{ row.questionnaire_version }}</template>
          </el-table-column>
          <el-table-column :label="t('evalStatus')" min-width="150">
            <template #default="{ row }">
              <span class="eval-text" :class="`is-${evalColor(row.evaluation_status)}`">
                {{ evalText(row.evaluation_status) }}
              </span>
              <span v-if="row.time_expired" class="eval-text is-danger" style="margin-left:8px">
                {{ t('expired') }}
              </span>
            </template>
          </el-table-column>
        </el-table>

        <div v-else class="attempt-list">
          <div v-for="row in result.attempts" :key="row.attempt_id" class="attempt-item">
            <div class="attempt-item__top">
              <b>{{ t('attemptN', { n: row.attempt_no }) }}</b>
              <span class="eval-text" :class="`is-${evalColor(row.evaluation_status)}`">
                {{ evalText(row.evaluation_status) }}
              </span>
            </div>
            <div class="attempt-item__meta">
              <span>{{ new Date(row.submitted_at).toLocaleString() }}</span>
              <span>v{{ row.questionnaire_version }}</span>
              <span v-if="row.time_expired" class="eval-text is-danger">{{ t('expired') }}</span>
            </div>
          </div>
        </div>
      </section>

      <div class="result-actions">
        <el-button @click="router.push('/student/exam')">{{ t('reanswer') }}</el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.result-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.result-head .page-title { margin: 0; }

.text-link {
  appearance: none;
  background: none;
  border: none;
  padding: 0;
  font-family: var(--font-sans);
  font-size: 12.5px;
  color: var(--aa-primary);
  cursor: pointer;
}
.text-link:disabled { color: var(--aa-text-3); cursor: default; }
.text-link:hover:not(:disabled) { text-decoration: underline; }

.block {
  background: var(--aa-surface);
  border-radius: var(--aa-radius-lg);
  padding: 20px;
  margin-bottom: 14px;
}
.section-title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--aa-text-3);
}
.block-text { margin: 0; font-size: 14px; line-height: 1.75; white-space: pre-wrap; }
.caption { margin: 8px 0 0; font-size: 12.5px; color: var(--aa-text-3); }

.state-line { margin: 0 0 18px; font-size: 14px; font-weight: 500; }
.state-line.is-strong { color: var(--aa-ink-1); }
.state-line.is-normal { color: var(--aa-ink-2); }
.state-line.is-muted { color: var(--aa-ink-3); }
.state-line.is-danger { color: var(--aa-danger); }

.evaluating-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: -8px 0 16px;
  font-size: 12.5px;
  color: var(--aa-text-2);
}
.hint-spinner {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  border: 2px solid var(--aa-line);
  border-top-color: var(--aa-primary);
  border-radius: 50%;
  animation: hint-spin 0.9s linear infinite;
}
@keyframes hint-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .hint-spinner { animation-duration: 2.4s; }
}

.plain-list { margin: 0; padding-left: 18px; }
.plain-list li { line-height: 1.7; margin-bottom: 4px; font-size: 13.5px; }
.retry-btn { margin-top: 14px; }

.eval-text { font-size: 12.5px; }
.eval-text.is-normal { color: var(--aa-ink-2); }
.eval-text.is-muted { color: var(--aa-ink-3); }
.eval-text.is-danger { color: var(--aa-danger); }

.attempt-list { display: flex; flex-direction: column; }
.attempt-item { padding: 12px 0; border-bottom: 1px solid var(--aa-line-soft); }
.attempt-item:last-child { border-bottom: none; padding-bottom: 0; }
.attempt-item__top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  font-size: 13.5px;
}
.attempt-item__meta {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--aa-text-3);
  font-size: 12.5px;
}

.result-actions { display: flex; gap: 10px; flex-wrap: wrap; }

@media (max-width: 767px) {
  .block { border-radius: var(--aa-radius); padding: 16px; }
}
</style>
