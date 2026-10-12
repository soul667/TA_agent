<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchExam, fetchResult, submitExam, getCredentials, ApiError } from '../api'
import { localizeBackendDetail as localizeDetail } from '../i18n'
import CodePanel from '../components/CodePanel.vue'
import FormulaText from '../components/FormulaText.vue'
import { useIsMobile } from '../useMedia'

const router = useRouter()
const { t, locale } = useI18n()
const cred = getCredentials()
const sid = cred?.sid || ''
const isMobile = useIsMobile()

const exam = ref(null)
const loadError = ref('')
const answers = reactive({})
const activeLines = ref([])      // 当前聚焦题目对应的代码行
const focusedId = ref(null)      // 当前聚焦题目（用于卡片高亮）
const mutation = ref(null)       // 变异题的“假设修改” {line_no, replacement}
const submitting = ref(false)
const receipt = ref(null)        // 提交回执
const retryTokenKey = `aa_submit_token_${sid}`
const prefillInfo = ref(null)    // { attemptNo } 已载入上次作答
const codeWrap = ref(localStorage.getItem('aa_code_wrap') === '1') // 折行偏好跨页面持久
const savedFlash = ref(false)    // “草稿已保存”轻提示
let savedTimer = null

function toggleWrap() {
  codeWrap.value = !codeWrap.value
  try { localStorage.setItem('aa_code_wrap', codeWrap.value ? '1' : '0') } catch { /* 忽略 */ }
}
const wrapLabel = computed(() => `${t('codeWrap')}: ${codeWrap.value ? t('codeWrapOn') : t('codeWrapOff')}`)

// ---- 限时 ----
const nowTick = ref(Date.now())
let tickHandle = null
let autoSubmitted = false
const startedKey = computed(() => `exam_started_${sid}_v${exam.value?.version ?? '?'}`)

const timeLimitMinutes = computed(() => exam.value?.time_limit_minutes || 0)
const startedAt = computed(() => {
  if (!timeLimitMinutes.value) return null
  let v = Number(localStorage.getItem(startedKey.value) || 0)
  if (!v) {
    v = Date.now()
    localStorage.setItem(startedKey.value, String(v))
  }
  return v
})
const remainingSeconds = computed(() => {
  if (!startedAt.value) return null
  const end = startedAt.value + timeLimitMinutes.value * 60_000
  return Math.max(0, Math.round((end - nowTick.value) / 1000))
})
const timeCritical = computed(() => remainingSeconds.value !== null && remainingSeconds.value <= 60)
const timeWarning = computed(() => remainingSeconds.value !== null && !timeCritical.value && remainingSeconds.value <= 300)

function fmtRemaining() {
  const s = remainingSeconds.value ?? 0
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

// ---- 内容双语选取 ----
function qText(q) {
  return locale.value === 'en' && q.question_en ? q.question_en : q.question
}
function qChoices(q) {
  return locale.value === 'en' && q.choices_en ? q.choices_en : (q.choices || [])
}
function titleName() {
  return locale.value === 'en' && exam.value.title_en ? exam.value.title_en : exam.value.title
}

// 题面行内代码识别：从本题代码中提取标识符（排除 C 关键字），
// 题面/选项里出现即按等宽代码渲染（后端题面未标反引号时也能正确显示）
const C_KEYWORDS = new Set([
  'int', 'char', 'void', 'float', 'double', 'long', 'short', 'unsigned', 'signed', 'bool',
  'if', 'else', 'for', 'while', 'do', 'return', 'break', 'continue', 'switch', 'case',
  'default', 'const', 'static', 'extern', 'register', 'sizeof', 'struct', 'union', 'enum',
  'typedef', 'goto', 'include', 'define', 'main', 'printf', 'scanf', 'puts', 'NULL',
])
const codeTokenList = computed(() => {
  const src = exam.value?.code || ''
  const set = new Set()
  for (const m of src.matchAll(/[A-Za-z_][A-Za-z0-9_]*/g)) {
    const w = m[0]
    if (C_KEYWORDS.has(w)) continue
    set.add(w)
  }
  return [...set]
})
const referencedLines = computed(() => {
  const set = new Set()
  exam.value?.questions?.forEach((q) => (q.code_lines || []).forEach((n) => set.add(n)))
  return set
})
const draftKey = computed(() => `exam_draft_${sid}_v${exam.value?.version ?? '?'}`)

// ---- 进度 ----
const answeredCount = computed(() => {
  if (!exam.value) return 0
  return exam.value.questions.filter((q) => {
    const a = answers[q.id]
    return !(a === '' || a === undefined || (Array.isArray(a) && a.length === 0))
  }).length
})
onMounted(load)
onMounted(() => {
  tickHandle = setInterval(() => {
    nowTick.value = Date.now()
    if (
      timeLimitMinutes.value && !autoSubmitted && !receipt.value && exam.value &&
      remainingSeconds.value === 0
    ) {
      autoSubmitted = true
      doSubmit(true)
    }
  }, 500)
})
onBeforeUnmount(() => {
  clearInterval(tickHandle)
  clearTimeout(savedTimer)
})

async function load() {
  try {
    exam.value = await fetchExam(sid)
    await restoreAnswers()
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      router.push({ name: 'student-login', query: { redirect: '/student/exam' } })
      return
    }
    loadError.value = localizeDetail(e.message) || t('loadFailed')
  }
}

async function restoreAnswers() {
  // 1) 本地草稿优先；2) 无草稿的题目用上次的作答预填
  let draft = {}
  try {
    draft = JSON.parse(localStorage.getItem(draftKey.value) || '{}')
  } catch {
    draft = {}
  }
  let prefillSource = null
  if (exam.value.has_submitted) {
    try {
      const result = await fetchResult(sid)
      const matched = (result.attempts || [])
        .filter((a) => a.questionnaire_version === exam.value.version &&
          a.answers && Object.keys(a.answers).length)
      const latest = matched[matched.length - 1]
      if (latest) prefillSource = latest
    } catch {
      /* 预填失败不影响答题 */
    }
  }
  exam.value.questions.forEach((q) => {
    if (draft[q.id] !== undefined) {
      answers[q.id] = draft[q.id]
    } else if (prefillSource && prefillSource.answers[q.id] !== undefined) {
      answers[q.id] = prefillSource.answers[q.id]
      if (prefillInfo.value === null) {
        prefillInfo.value = { attemptNo: prefillSource.attempt_no }
      }
    } else if (answers[q.id] === undefined) {
      answers[q.id] = q.type === 'multiple_choice' ? [] : ''
    }
  })
}

function clearPrefill() {
  exam.value.questions.forEach((q) => {
    if (q.type === 'multiple_choice') answers[q.id] = []
    else answers[q.id] = ''
  })
  prefillInfo.value = null
  saveDraft()
}

function saveDraft() {
  localStorage.setItem(draftKey.value, JSON.stringify({ ...answers }))
  savedFlash.value = true
  clearTimeout(savedTimer)
  savedTimer = setTimeout(() => { savedFlash.value = false }, 1600)
}

function focusQuestion(q) {
  focusedId.value = q.id
  activeLines.value = q.code_lines || []
  mutation.value = q.type === 'code_variant' && q.variant ? q.variant : null
  // 桌面端左栏代码常驻：把第一处相关行滚到视野中央（行高亮 + 闪烁由 CSS 处理）
  const first = activeLines.value[0]
  if (first && !isMobile.value) nextTick(() => codeMain.value?.scrollToLine(first))
}

// 移动端代码抽屉打开后再定位（内容挂载后才有可滚动元素）
const codeMain = ref(null)      // 桌面端左栏代码面板
const codePeek = ref(null)      // 移动端代码速览抽屉面板
function onPeekOpened() {
  const first = activeLines.value[0]
  if (first) codePeek.value?.scrollToLine(first)
}

// 「查看题目」弹层：题面 + 要求（内容来自 /api/exam 下发的 assignment_prompt）
const problemVisible = ref(false)

// 点击题目：高亮相关代码行（桌面端代码在左栏常驻可见）；
// 移动端代码收进页头入口与底部“代码速览”抽屉，题目列表优先占屏。
const peekVisible = ref(false)
function openCodePeek() {
  peekVisible.value = true
}
function onCardClick(q, evt) {
  focusQuestion(q)
  if (!isMobile.value) return
  if (!(q.code_lines && q.code_lines.length)) return
  const target = evt?.target
  if (target?.closest?.('.q-choices, .el-textarea, .el-input, textarea, input, .el-radio, .el-checkbox')) return
  peekVisible.value = true
}

function unansweredCount() {
  return exam.value.questions.filter((q) => {
    const a = answers[q.id]
    return a === '' || a === undefined || (Array.isArray(a) && a.length === 0)
  }).length
}

function submitToken() {
  // 网络重试复用同一提交标识：服务端返回同一回执，不新增作答
  let tok = sessionStorage.getItem(retryTokenKey)
  if (!tok) {
    tok = (crypto.randomUUID ? crypto.randomUUID() : `t-${Date.now()}-${Math.random()}`)
    sessionStorage.setItem(retryTokenKey, tok)
  }
  return tok
}

async function submit() {
  const n = unansweredCount()
  if (n > 0) {
    try {
      await ElMessageBox.confirm(t('unanswered', { n }), t('confirmSubmit'), {
        confirmButtonText: t('confirmSubmit'),
        cancelButtonText: t('keepWorking'),
        type: 'warning',
      })
    } catch {
      return
    }
  }
  await doSubmit(false)
}

async function doSubmit(expired) {
  submitting.value = true
  try {
    const payload = {
      answers: { ...answers },
      questionnaire_version: exam.value.version,
      submission_token: submitToken(),
      time_expired: expired,
      duration_seconds: startedAt.value
        ? Math.round((Date.now() - startedAt.value) / 1000)
        : undefined,
    }
    receipt.value = await submitExam(sid, payload)
    sessionStorage.removeItem(retryTokenKey)
    localStorage.removeItem(draftKey.value)
    localStorage.removeItem(startedKey.value)
    saveDraftAudit()
    ElMessage.success(`${t('receiptTitle')} · ${t('attemptN', { n: receipt.value.attempt })}`)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      ElMessageBox.alert(e.detail, t('conflictTitle'), {
        confirmButtonText: t('conflictRefresh'),
        type: 'warning',
      }).finally(() => window.location.reload())
    } else if (e instanceof ApiError && e.status === 401) {
      router.push('/student')
    } else {
      ElMessage.error((localizeDetail(e.detail) || e.message || t('submitFailed')) + t('draftKeptHint'))
    }
  } finally {
    submitting.value = false
  }
}

// 提交成功后留一份本地回执索引，供“我的结果”入口提示
function saveDraftAudit() {
  try {
    const key = `aa_receipts_${sid}`
    const list = JSON.parse(localStorage.getItem(key) || '[]')
    list.push({
      attempt_id: receipt.value.attempt_id,
      attempt: receipt.value.attempt,
      submitted_at: receipt.value.submitted_at,
      evaluation_status: receipt.value.evaluation_status,
    })
    localStorage.setItem(key, JSON.stringify(list.slice(-20)))
  } catch { /* 忽略本地索引失败 */ }
}

function evalStatusType(s) {
  return s === 'ok' ? 'success' : s === 'failed' ? 'danger' : 'info'
}
function evalStatusText(s) {
  if (s === 'ok') return t('evalOk')
  if (s === 'failed') return t('evalFailed')
  return t('evalPending')
}
</script>

<template>
  <div class="page" :class="{ 'has-actionbar': exam && !receipt }">
    <div v-if="loadError">
      <el-alert type="error" :title="loadError" show-icon :closable="false" />
      <el-button style="margin-top:12px" @click="load">{{ t('retry') }}</el-button>
    </div>

    <template v-else-if="!exam">
      <el-skeleton :rows="8" animated />
    </template>

    <template v-else>
      <!-- 提交回执 -->
      <el-result
        v-if="receipt"
        icon="success"
        :title="t('receiptTitle')"
        :sub-title="t('receiptSub', {
          attempt: receipt.attempt,
          time: new Date(receipt.submitted_at).toLocaleString(),
          version: receipt.questionnaire_version,
        })"
      >
        <template #extra>
          <p class="receipt-status">{{ evalStatusText(receipt.evaluation_status) }}</p>
          <p v-if="receipt.evaluation_status === 'pending'" class="receipt-hint">
            {{ t('receiptEvalHint') }}
          </p>
          <div class="receipt-actions">
            <el-button type="primary" @click="router.push('/student/result')">
              {{ t('viewMyResult') }}
            </el-button>
            <el-button @click="receipt = null">{{ t('backToExam') }}</el-button>
          </div>
        </template>
      </el-result>

      <template v-else>
        <!-- 页头 -->
        <header class="exam-head">
          <div class="exam-head__top">
            <h1 class="page-title">{{ titleName() }}</h1>
            <p class="exam-meta">
              <button class="text-link" type="button" @click="problemVisible = true">
                {{ t('viewProblem') }}
              </button>
              <span class="dot">·</span>
              <span>{{ exam.student_id }}</span>
              <template v-if="exam.has_submitted">
                <span class="dot">·</span>
                <button
                  class="text-link"
                  type="button"
                  @click="router.push('/student/result')"
                >{{ t('viewLastResult') }}</button>
              </template>
            </p>
          </div>

          <p v-if="exam.demo_notice" class="notice">
            <span class="notice-dot" />
            <span>{{ locale === 'en' && exam.demo_notice_en ? exam.demo_notice_en : exam.demo_notice }}</span>
          </p>

          <p v-if="timeLimitMinutes && remainingSeconds === 0" class="urgent">
            {{ t('timeExpiredBanner') }}
          </p>

          <p v-if="prefillInfo" class="notice">
            <span class="notice-dot" />
            <span>{{ t('prefilled', { n: prefillInfo.attemptNo }) }}</span>
            <button class="text-link" type="button" @click="clearPrefill">{{ t('clearPrefill') }}</button>
          </p>

          <!-- 移动端：代码不再置顶占首屏，收进这个入口与底部抽屉 -->
          <p v-if="isMobile" class="notice">
            <span class="notice-dot" />
            <span>{{ t('codePeekTip') }}</span>
            <button class="text-link" type="button" @click="openCodePeek">{{ t('viewCode') }}</button>
          </p>
        </header>

        <el-row :gutter="28" class="exam-split">
          <!-- 左：原始代码（移动端整栏隐藏，走页头入口 + 底部抽屉） -->
          <el-col v-if="!isMobile" :xs="24" :sm="10" class="exam-code-col">
            <el-card shadow="never" class="code-card">
              <div class="code-tools">
                <span class="caption">{{ t('yourSubmission') }}</span>
                <span class="spacer" />
                <button class="text-link" type="button" @click="problemVisible = true">
                  {{ t('viewProblem') }}
                </button>
                <span class="tools-sep">·</span>
                <button class="text-link" type="button" @click="toggleWrap">{{ wrapLabel }}</button>
              </div>
              <div class="code-scroll">
                <CodePanel
                  ref="codeMain"
                  :code="exam.code"
                  :language="exam.language || 'c'"
                  :referenced="[...referencedLines]"
                  :active="activeLines"
                  :mutation="mutation"
                  :wrap="codeWrap"
                />
              </div>
            </el-card>
          </el-col>

          <!-- 右：问题 -->
          <el-col :xs="24" :sm="14">
            <div
              v-for="(q, idx) in exam.questions"
              :key="q.id"
              class="question-card"
              :class="{ 'is-variant': q.type === 'code_variant', 'is-active': focusedId === q.id }"
              @click="onCardClick(q, $event)"
            >
              <div class="q-head">
                <span class="q-no">{{ idx + 1 }}</span>
                <span class="q-text"><FormulaText :text="qText(q)" :code-tokens="codeTokenList" /></span>
                <span v-if="q.type === 'multiple_choice'" class="q-flag">{{ t('multi') }}</span>
                <span v-if="q.type === 'code_variant'" class="q-flag is-variant-flag">{{ t('variantBadge') }}</span>
              </div>

              <el-radio-group
                v-if="q.type === 'single_choice' || q.type === 'code_variant'"
                v-model="answers[q.id]"
                class="q-choices"
                @change="saveDraft"
              >
                <el-radio
                  v-for="(choice, i) in qChoices(q)"
                  :key="i"
                  :value="String.fromCharCode(65 + i)"
                >
                  <span class="choice-key">{{ String.fromCharCode(65 + i) }}</span>
                  <FormulaText :text="choice" :code-tokens="codeTokenList" />
                </el-radio>
              </el-radio-group>

              <el-checkbox-group
                v-else-if="q.type === 'multiple_choice'"
                v-model="answers[q.id]"
                class="q-choices"
                @change="saveDraft"
              >
                <el-checkbox
                  v-for="(choice, i) in qChoices(q)"
                  :key="i"
                  :value="String.fromCharCode(65 + i)"
                >
                  <span class="choice-key">{{ String.fromCharCode(65 + i) }}</span>
                  <FormulaText :text="choice" :code-tokens="codeTokenList" />
                </el-checkbox>
              </el-checkbox-group>

              <el-input
                v-else
                v-model="answers[q.id]"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 8 }"
                :placeholder="locale === 'en'
                  ? 'Explain your understanding in one or two sentences'
                  : '请用一两句话说明你的理解'"
                @input="saveDraft"
              />
            </div>
          </el-col>
        </el-row>

        <!-- 移动端：点击题目弹出的代码速览 -->
        <el-drawer
          v-if="isMobile"
          v-model="peekVisible"
          direction="btt"
          size="68%"
          :with-header="false"
          class="code-peek"
          append-to-body
          @opened="onPeekOpened"
        >
          <div class="code-peek__head">
            <b>{{ t('yourSubmission') }}</b>
            <span class="spacer" />
            <button class="text-link" type="button" @click="problemVisible = true">
              {{ t('viewProblem') }}
            </button>
            <button class="text-link" type="button" @click="toggleWrap">{{ wrapLabel }}</button>
            <button class="text-link" type="button" @click="peekVisible = false">{{ t('close') }}</button>
          </div>
          <CodePanel
            ref="codePeek"
            :code="exam.code"
            :language="exam.language || 'c'"
            :referenced="[...referencedLines]"
            :active="activeLines"
            :mutation="mutation"
            :wrap="codeWrap"
          />
        </el-drawer>

        <!-- 查看题目：题面 + 要求（代码栏右侧入口，桌面/移动共用） -->
        <el-dialog
          v-model="problemVisible"
          :title="t('problemTitle')"
          width="min(680px, 92vw)"
          append-to-body
        >
          <div class="problem-body">
            <p class="problem-text">{{ exam.assignment_prompt }}</p>
            <template v-if="(exam.assignment_requirements || []).length">
              <p class="problem-sub">{{ t('problemRequirements') }}</p>
              <ul class="problem-reqs">
                <li v-for="(r, i) in exam.assignment_requirements" :key="i">{{ r }}</li>
              </ul>
            </template>
          </div>
        </el-dialog>

        <!-- 吸底操作栏（桌面与移动一致：进度 / 倒计时 / 提交） -->
        <div class="action-bar">
          <div class="action-bar__inner">
            <div class="action-bar__info">
              <b>{{ t('answeredOf', { done: answeredCount, total: exam.questions.length }) }}</b>
              <span class="bar-meta">
                <span
                  v-if="timeLimitMinutes"
                  class="timer"
                  :class="{ 'is-warning': timeWarning, 'is-critical': timeCritical }"
                >{{ fmtRemaining() }}</span>
                <span v-if="savedFlash" class="saved-flash">{{ t('autoSaved') }}</span>
              </span>
            </div>
            <span class="spacer" />
            <el-button type="primary" :loading="submitting" @click="submit">
              {{ t('submitExam') }}
            </el-button>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* ---- 回执 ---- */
.receipt-status { margin: 0 0 10px; color: var(--aa-text-2); font-size: 13px; }
.receipt-hint {
  margin: 0 auto 14px;
  max-width: 420px;
  color: var(--aa-text-2);
  font-size: 12.5px;
  line-height: 1.7;
}
.receipt-actions { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }

/* ---- 页头（标题与学号同行，提示行紧随其后，与内容块拉开距离） ---- */
.exam-head { margin-bottom: 18px; }
.exam-head__top {
  display: flex;
  align-items: baseline;
  gap: 14px;
  flex-wrap: wrap;
}
.exam-head__top .page-title { margin: 0; }
.exam-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
  margin: 0 0 0 auto;
  color: var(--aa-text-2);
  font-size: 12.5px;
}
.exam-meta .dot { color: var(--aa-text-3); }
.exam-head .notice,
.exam-head .urgent { margin: 8px 0 0; }

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
.text-link:hover { text-decoration: underline; }

.urgent { margin: 0 0 10px; color: var(--el-color-danger); font-size: 12.5px; }

/* ---- 吸底栏内的进度 / 倒计时 ---- */
.saved-flash { margin-left: 8px; color: var(--aa-text-3); font-size: 12px; }
.spacer { flex: 1; }
.bar-meta { display: inline-flex; align-items: baseline; gap: 8px; }
.timer {
  font-family: var(--font-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--aa-text-2);
  flex: 0 0 auto;
}
/* 紧迫感由字重与节律表达，不做黄→红渐变 */
.timer.is-warning { color: var(--aa-ink-1); font-weight: 600; }
.timer.is-critical {
  color: var(--aa-danger);
  font-weight: 600;
  animation: timer-pulse 1.6s ease-in-out infinite;
}
@keyframes timer-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}
@media (prefers-reduced-motion: reduce) {
  .timer.is-critical { animation: none; }
}

/* ---- 代码栏 ---- */
.code-card { background: transparent; --el-card-bg-color: transparent; }
.code-card :deep(.el-card__body) { padding: 0; }
.caption { font-size: 12.5px; color: var(--aa-text-3); }
.code-scroll { border-radius: var(--aa-radius-sm); }
.tools-sep { margin: 0 8px; color: var(--aa-text-3); }

/* ---- 查看题目弹层 ---- */
.problem-body { font-size: 14px; line-height: 1.7; }
.problem-text { margin: 0; white-space: pre-wrap; color: var(--aa-ink); }
.problem-sub {
  margin: 14px 0 4px;
  font-size: 12.5px;
  color: var(--aa-text-3);
}
.problem-reqs {
  margin: 0;
  padding-left: 20px;
  color: var(--aa-text-2);
}
.problem-reqs li { margin: 2px 0; }

/* ---- 题目（无卡片，细分隔线） ---- */
.question-card {
  padding: 16px 0 18px;
  border-bottom: 1px solid var(--aa-line-soft);
  cursor: pointer;
}
.question-card:first-child { padding-top: 4px; }
.question-card:last-child { border-bottom: none; }
/* 题号与正文同一行：内联流 + 悬挂缩进（续行对齐正文） */
.q-head {
  display: block;
  margin-bottom: 8px;
  padding-left: 20px;
  text-indent: -20px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.6;
}
.q-no {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--aa-text-3);
  margin-right: 6px;
}
.question-card.is-active .q-no { color: var(--aa-ink-1); font-weight: 600; }
.q-text { display: inline; }
.q-flag { font-size: 12px; color: var(--aa-text-3); margin-left: 6px; }
.q-flag.is-variant-flag { color: var(--aa-ink-1); font-weight: 500; }
.q-flag.is-variant-flag::before { content: '▍'; color: var(--aa-text-3); margin-right: 2px; }

/* 选项：选中 = 左侧竖条 + 明度差 + 字重（不填充色相） */
.q-choices { display: block; width: 100%; }
.q-choices :deep(.el-radio),
.q-choices :deep(.el-checkbox) {
  display: flex;
  align-items: flex-start;
  width: 100%;
  height: auto;
  margin: 0;
  padding: 8px 10px;
  border: none;
  border-radius: 10px;
  background: transparent;
  transition: background-color 0.14s ease;
}
.q-choices :deep(.el-radio:hover),
.q-choices :deep(.el-checkbox:hover) { background: var(--aa-hover); }
.q-choices :deep(.el-radio.is-checked),
.q-choices :deep(.el-checkbox.is-checked) {
  background: var(--aa-hover);
}
.q-choices :deep(.el-radio__input),
.q-choices :deep(.el-checkbox__input) { margin-top: 2px; flex-shrink: 0; }
.q-choices :deep(.el-radio__label),
.q-choices :deep(.el-checkbox__label) {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  line-height: 1.55;
  white-space: normal;
  padding-left: 8px;
  color: var(--aa-ink);
}
.q-choices :deep(.is-checked) .el-radio__label,
.q-choices :deep(.is-checked) .el-checkbox__label { font-weight: 500; }
.choice-key {
  display: inline-block;
  min-width: 16px;
  margin-right: 4px;
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--aa-text-3);
}
.q-choices :deep(.is-checked) .choice-key { color: var(--aa-ink-1); font-weight: 600; }

/* ---- 移动端 ---- */
@media (max-width: 767px) {
  .question-card { padding: 14px 0 16px; }
  .q-text { font-size: 13.5px; }
}
</style>
