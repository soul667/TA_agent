<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  fetchOverview, fetchDetail, teacherEvaluate, teacherRegenerate, teacherRegenerateStatus, teacherReview,
  setCredentials, clearCredentials, getCredentials, ApiError, apiGet,
} from '../api'
import CodePanel from '../components/CodePanel.vue'
import { featureFlags } from '../store'
import FormulaText from '../components/FormulaText.vue'
import { useIsMobile } from '../useMedia'

const { t, te, locale } = useI18n()
const isMobile = useIsMobile()

const ISSUE_ZH = {
  initialization: '初始化错误', 'initialization-oob': '初始化越界', 'edge-case': '边界情况',
  'out-of-bounds': '数组越界', 'wrong-comparison': '比较方向错误',
  'incomplete-traversal': '遍历不完整', 'missing-return': '缺少返回值',
  'index-range': '下标范围', 'loop-boundary': '循环边界', 'undefined-behavior': '未定义行为',
  'loop-invariant': '循环不变量', 'traversal-necessity': '遍历必要性',
  'traversal-role': '遍历作用', 'return-mechanism': '返回机制', 'precondition': '前置条件',
  complexity: '复杂度', 'algorithm-necessity': '算法必要性', 'understanding-check': '理解确认',
  'wrong-return-value': '返回值错误', 'premature-return': '提前返回',
  'index-vs-value': '下标与元素值', 'sentinel-invalid': '哨兵值无效',
  'sentinel-initialization': '哨兵初值', 'value-range': '取值范围',
  'no-side-effect': '副作用', 'api-contract': '接口约定', 'side-effect': '副作用',
  'unnecessary-cost': '不必要开销', 'multi-defect': '多缺陷', 'uncovered': '未知/待核验',
  'equivalent-implementation': '等价实现', 'redundant-comparison': '冗余比较',
  'comparison-count': '比较次数', 'reverse-traversal': '反向遍历',
  'coverage-completeness': '遍历覆盖', 'candidate-validity': '候选有效性',
  'failing-input': '失效输入', 'tracing': '结果追踪', 'return-value': '返回值',
  'internal-vs-result': '内部值与结果', 'test-design': '测试设计',
  'transfer-min': '最小值迁移',
}
const ISSUE_EN = {
  initialization: 'initialization', 'initialization-oob': 'initialization out of bounds',
  'edge-case': 'edge case', 'out-of-bounds': 'out of bounds access',
  'wrong-comparison': 'wrong comparison direction', 'incomplete-traversal': 'incomplete traversal',
  'missing-return': 'missing return', 'index-range': 'index range',
  'loop-boundary': 'loop boundary', 'undefined-behavior': 'undefined behavior',
  'loop-invariant': 'loop invariant', 'traversal-necessity': 'traversal necessity',
  'traversal-role': 'role of traversal', 'return-mechanism': 'return mechanism',
  precondition: 'precondition', complexity: 'complexity',
  'algorithm-necessity': 'traversal necessity (lower bound)', 'understanding-check': 'understanding check',
  'wrong-return-value': 'wrong return value', 'premature-return': 'premature return',
  'index-vs-value': 'index vs value', 'sentinel-invalid': 'invalid sentinel',
  'sentinel-initialization': 'sentinel initialization', 'value-range': 'value range',
  'no-side-effect': 'no side effect', 'api-contract': 'API contract', 'side-effect': 'side effect',
  'unnecessary-cost': 'unnecessary cost', 'multi-defect': 'multiple defects', uncovered: 'uncovered',
  'equivalent-implementation': 'equivalent implementation', 'redundant-comparison': 'redundant comparison',
  'comparison-count': 'comparison count', 'reverse-traversal': 'reverse traversal',
  'coverage-completeness': 'coverage completeness', 'candidate-validity': 'candidate validity',
  'failing-input': 'failing input', tracing: 'tracing', 'return-value': 'return value',
  'internal-vs-result': 'internal state vs result', 'test-design': 'test design',
  'transfer-min': 'transfer to minimum',
}
const KP_STATUS_CLASS = {
  mastered: 'normal', partial: 'normal', limited: 'normal',
  insufficient: 'strong', uncovered: 'muted',
}

function issueName(code) {
  if (locale.value === 'en') return ISSUE_EN[code] || code
  return ISSUE_ZH[code] || code
}
function kpStatusName(s) {
  const key = { mastered: 'kpMastered', partial: 'kpPartial', limited: 'kpLimited',
    insufficient: 'kpInsufficient', uncovered: 'kpUncovered' }[s]
  return key && te(key) ? t(key) : s
}
function understandingName(u) {
  const key = { mastered: 'uMastered', partial: 'uPartial', limited: 'uLimited',
    insufficient: 'uInsufficient' }[u]
  return key && te(key) ? t(key) : u
}
function codeQualityName(q) {
  return { correct: t('codeCorrect'), incorrect: t('codeIncorrect'), unknown: t('codeUnknown') }[q] || q
}
// 内容级双语：后端 *_en 字段优先（EN 界面），否则回退中文
function pickEn(obj, key) {
  if (!obj) return ''
  if (locale.value === 'en') {
    const v = obj[`${key}_en`]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return obj[key]
}
function choiceText(q, j, fallback) {
  if (locale.value === 'en' && q.choices_en && q.choices_en[j] !== undefined) {
    return q.choices_en[j]
  }
  return fallback
}

const existing = getCredentials()
const teacherToken = ref(existing?.role === 'teacher' ? existing.token : '')
const needLogin = ref(existing?.role !== 'teacher')
const rows = ref([])
const loading = ref(false)
const keyword = ref('')

// 扫码答题：二维码由后端按本机局域网 IP 生成，手机扫码直达学生登录页
const qrVisible = ref(false)
const qrLoading = ref(false)
const qrData = ref(null) // { url, lan_ip, port, svg }

async function showQr() {
  qrVisible.value = true
  if (qrData.value) return // 已取过则直接展示
  qrLoading.value = true
  try {
    qrData.value = await apiGet('/api/qr/exam')
  } catch (e) {
    ElMessage.error(e?.detail || e?.message || t('qrLoadFail'))
    qrVisible.value = false
  } finally {
    qrLoading.value = false
  }
}

async function copyQrUrl() {
  try {
    await navigator.clipboard.writeText(qrData.value.url)
    ElMessage.success(t('copied'))
  } catch {
    ElMessage.warning(t('copyFailed'))
  }
}

const detail = ref(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const viewVersion = ref(null)
const viewAttemptIndex = ref(-1) // -1 = 最新
const busy = ref('')

const reviewForm = ref({
  decision: 'confirmed', revised_understanding: 'mastered',
  comment: '', feedback_to_student: '', published: true,
})

const stats = computed(() => ({
  total: rows.value.length,
  submitted: rows.value.filter((r) => r.exam_status === 'done').length,
  pending: rows.value.filter((r) => r.needs_ta_review && r.review_status !== 'reviewed').length,
  reviewed: rows.value.filter((r) => r.review_status === 'reviewed').length,
}))

const filteredRows = computed(() => {
  const kw = keyword.value.trim()
  if (!kw) return rows.value
  return rows.value.filter((r) => String(r.student_id).includes(kw))
})

onMounted(() => {
  if (!needLogin.value) loadOverview()
})

function login() {
  if (!teacherToken.value.trim()) {
    ElMessage.warning(t('teacherLoginFirst'))
    return
  }
  setCredentials({ role: 'teacher', token: teacherToken.value.trim() })
  needLogin.value = false
  loadOverview()
}

async function guard(fn) {
  try {
    return await fn()
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      clearCredentials()
      needLogin.value = true
      ElMessage.error(t('teacherTokenInvalid'))
    } else if (e instanceof ApiError && e.status === 403) {
      // 教师侧 403 = 口令不正确：清掉凭据并退回登录表单
      clearCredentials()
      needLogin.value = true
      ElMessage.error(t('teacherTokenWrong'))
    } else {
      ElMessage.error(e.detail || e.message || 'Request failed')
    }
    return null
  }
}

async function loadOverview() {
  loading.value = true
  await guard(async () => {
    const data = await fetchOverview()
    rows.value = data.rows
  })
  loading.value = false
}

async function openDetail(sid) {
  detailVisible.value = true
  detailLoading.value = true
  viewVersion.value = null
  viewAttemptIndex.value = -1
  reviewForm.value = {
    decision: 'confirmed', revised_understanding: 'mastered',
    comment: '', feedback_to_student: '', published: true,
  }
  await guard(async () => {
    detail.value = await fetchDetail(sid)
  })
  detailLoading.value = false
}

async function reloadDetail() {
  if (!detail.value) return
  await guard(async () => {
    detail.value = await fetchDetail(
      detail.value.student_id, viewVersion.value || undefined
    )
  })
}

// 题面/反馈里的行内代码识别：从学生代码提取标识符（排除 C 关键字）
const C_KEYWORDS = new Set([
  'int', 'char', 'void', 'float', 'double', 'long', 'short', 'unsigned', 'signed', 'bool',
  'if', 'else', 'for', 'while', 'do', 'return', 'break', 'continue', 'switch', 'case',
  'default', 'const', 'static', 'extern', 'register', 'sizeof', 'struct', 'union', 'enum',
  'typedef', 'goto', 'include', 'define', 'main', 'printf', 'scanf', 'puts', 'NULL',
])
const codeTokenList = computed(() => {
  const src = detail.value?.submission?.code || ''
  const set = new Set()
  for (const m of src.matchAll(/[A-Za-z_][A-Za-z0-9_]*/g)) {
    const w = m[0]
    if (C_KEYWORDS.has(w)) continue
    set.add(w)
  }
  return [...set]
})

const currentEvaluation = computed(() => detail.value?.evaluation || null)
const currentRecord = computed(() => {
  const recs = detail.value?.evaluation_records || []
  return recs.length ? recs[recs.length - 1] : null
})
const attemptForView = computed(() => {
  const attempts = detail.value?.response?.attempts || []
  if (!attempts.length) return null
  return viewAttemptIndex.value >= 0 ? attempts[viewAttemptIndex.value] : attempts[attempts.length - 1]
})
const answersForView = computed(() => attemptForView.value?.answers || detail.value?.response?.answers || {})

function latestReviewFor(attemptId) {
  const reviews = detail.value?.reviews || []
  const matched = reviews.filter((r) => r.attempt_id === attemptId)
  return matched.length ? matched[matched.length - 1] : null
}

async function reevaluate() {
  if (!detail.value) return
  busy.value = 'evaluate'
  await guard(async () => {
    await teacherEvaluate(detail.value.student_id)
    ElMessage.success(t('reviewOk'))
    await reloadDetail()
    await loadOverview()
  })
  busy.value = ''
}

async function pollGeneration(sid) {
  // 异步 202：轮询后台生成任务（真实 LLM 最长数分钟；90 次 × 2s 足够）
  for (let i = 0; i < 90; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    const job = await teacherRegenerateStatus(sid)
    if (job.status !== 'pending') return job
  }
  return { status: 'timeout' }
}

async function regenerate() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(
      locale.value === 'en'
        ? 'A new questionnaire version will be generated from the current code; previous versions and attempts are preserved, and the student answers the new version.'
        : '将按当前代码生成新版本问卷；旧问卷与历次作答会完整保留，学生需按新版本重新作答。',
      t('regenerate'),
      { confirmButtonText: t('regenerate'), cancelButtonText: t('cancel'), type: 'warning' }
    )
  } catch { return }
  busy.value = 'regenerate'
  await guard(async () => {
    const res = await teacherRegenerate(detail.value.student_id)
    if (res.status === 'pending') {
      ElMessage.info(t('regenStarted'))
      const job = await pollGeneration(detail.value.student_id)
      if (job.status === 'ok') ElMessage.success(t('regenDone', { version: job.version }))
      else if (job.status === 'failed') ElMessage.error(t('regenFailed', { msg: job.error || '' }))
      else ElMessage.warning(t('regenTimeout'))
    } else if (res.status === 'ok') {
      ElMessage.success(t('regenDone', { version: res.version }))
    }
    await reloadDetail()
    await loadOverview()
  })
  busy.value = ''
}

async function submitReview() {
  if (!detail.value || !attemptForView.value) return
  const attemptId = attemptForView.value.attempt_id
  busy.value = 'review'
  await guard(async () => {
    await teacherReview(detail.value.student_id, {
      attempt_id: attemptId,
      decision: reviewForm.value.decision,
      revised_understanding: reviewForm.value.decision === 'revised'
        ? reviewForm.value.revised_understanding : null,
      comment: reviewForm.value.comment,
      feedback_to_student: reviewForm.value.feedback_to_student,
      published: reviewForm.value.published,
    })
    ElMessage.success(reviewForm.value.published ? t('reviewSavedPublished') : t('reviewSaved'))
    await reloadDetail()
    await loadOverview()
  })
  busy.value = ''
}

function examStatusName(s) {
  return {
    not_generated: t('examNotGenerated'), waiting: t('examWaiting'), done: t('examDone'),
  }[s] || s
}
// 正常状态不声明（墨阶普通色），缺失状态压暗
function examStatusClass(s) {
  return s === 'done' ? 'normal' : 'muted'
}
function evalStatusName(s) {
  return s === 'ok' ? t('evalOkShort') : s === 'failed' ? t('evalFailedShort') : t('evalRunning')
}
function evalColor(s) {
  return s === 'ok' ? 'normal' : s === 'failed' ? 'danger' : 'muted'
}
// 例外行：需要 TA 复核 或 评价失败 —— 只有这类行才在列表里显形
function isAlertRow(row) {
  return (row.needs_ta_review && row.review_status !== 'reviewed') || row.evaluation_status === 'failed'
}
function rowClass({ row }) {
  return isAlertRow(row) ? 'row-alert' : ''
}
function issuesText(row) {
  const list = (row.original_issues || []).map(issueName)
  return list.length ? list.join(locale.value === 'en' ? ', ' : '、') : t('none')
}
</script>

<template>
  <div class="page">
    <!-- 登录 -->
    <div v-if="needLogin" class="login-wrap">
      <div class="login-col">
        <h1 class="page-title">{{ t('teacherLogin') }}</h1>
        <el-input
          v-model="teacherToken"
          type="password"
          size="large"
          :placeholder="t('teacherTokenPh')"
          show-password
          @keyup.enter="login"
        />
        <el-button type="primary" size="large" class="login-btn" :loading="loading" @click="login">
          {{ t('enter') }}
        </el-button>
      </div>
    </div>

    <template v-else>
      <header class="panel-head">
        <h1 class="page-title">{{ t('overview') }}</h1>
        <span class="spacer" />
        <button class="text-link" type="button" @click="showQr">{{ t('qrBtn') }}</button>
        <button class="text-link" type="button" :disabled="loading" @click="loadOverview">
          {{ t('refresh') }}
        </button>
      </header>

      <!-- 统计（纯数字，无卡片） -->
      <div class="stats">
        <div class="stat">
          <span class="stat-num">{{ stats.total }}</span>
          <span class="stat-label">{{ t('statsTotal') }}</span>
        </div>
        <div class="stat">
          <span class="stat-num">{{ stats.submitted }}</span>
          <span class="stat-label">{{ t('statsSubmitted') }}</span>
        </div>
        <div class="stat">
          <span class="stat-num">{{ stats.pending }}</span>
          <span class="stat-label">{{ t('statsPending') }}</span>
        </div>
        <div class="stat">
          <span class="stat-num">{{ stats.reviewed }}</span>
          <span class="stat-label">{{ t('statsReviewed') }}</span>
        </div>
      </div>

      <section class="block">
        <div class="toolbar">
          <el-input
            v-model="keyword"
            :placeholder="t('searchPh')"
            clearable
            class="search-input"
          />
          <span class="spacer" />
          <span class="muted">{{ filteredRows.length }} / {{ rows.length }}</span>
        </div>

        <!-- 桌面：表格（例外行才显形：左侧竖条 + 极淡底） -->
        <el-table
          v-if="!isMobile"
          :data="filteredRows"
          v-loading="loading"
          :row-class-name="rowClass"
          @row-click="(r) => openDetail(r.student_id)"
          class="overview-table"
        >
          <el-table-column prop="student_id" :label="t('colStudent')" width="110" />
          <el-table-column :label="t('colCase')" width="70">
            <template #default="{ row }">{{ row.case_id || t('none') }}</template>
          </el-table-column>
          <el-table-column :label="t('colIssues')" min-width="150">
            <template #default="{ row }">{{ issuesText(row) }}</template>
          </el-table-column>
          <el-table-column :label="t('colSubmit')" width="96">
            <template #default="{ row }">
              <span class="eval-text" :class="`is-${examStatusClass(row.exam_status)}`">
                {{ examStatusName(row.exam_status) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column :label="t('colEval')" width="110">
            <template #default="{ row }">
              <span v-if="row.evaluation_status" class="eval-text" :class="`is-${evalColor(row.evaluation_status)}`">
                {{ evalStatusName(row.evaluation_status) }}
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('colUnderstanding')" width="128">
            <template #default="{ row }">
              {{ row.understanding ? understandingName(row.understanding) : t('none') }}
            </template>
          </el-table-column>
          <el-table-column :label="t('colScore')" width="90">
            <template #default="{ row }">
              {{ row.score === null ? t('none') : `${row.score}/${row.max_score}` }}
            </template>
          </el-table-column>
          <el-table-column :label="t('colReview')" width="120">
            <template #default="{ row }">
              <span v-if="row.needs_ta_review && row.review_status !== 'reviewed'" class="eval-text is-strong st-dot">
                {{ t('needTaReview') }}
              </span>
              <span v-else-if="row.review_status === 'reviewed'" class="eval-text is-muted">{{ t('reviewed') }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('colAttempts')" width="90" prop="attempt_count" />
        </el-table>

        <!-- 移动端：细分隔线列表 -->
        <div v-else class="stu-list" v-loading="loading">
          <div
            v-for="row in filteredRows"
            :key="row.student_id"
            class="stu-row"
            :class="{ 'is-alert': isAlertRow(row) }"
            @click="openDetail(row.student_id)"
          >
            <div class="stu-row__top">
              <b class="stu-id">{{ row.student_id }}</b>
              <span class="muted">{{ row.case_id || t('none') }}</span>
              <span class="spacer" />
              <span class="eval-text" :class="`is-${examStatusClass(row.exam_status)}`">
                {{ examStatusName(row.exam_status) }}
              </span>
            </div>
            <div class="stu-row__issues">{{ issuesText(row) }}</div>
            <div class="stu-row__bottom">
              <span v-if="row.evaluation_status" class="eval-text" :class="`is-${evalColor(row.evaluation_status)}`">
                {{ evalStatusName(row.evaluation_status) }}
              </span>
              <span v-if="row.understanding" class="muted">{{ understandingName(row.understanding) }}</span>
              <span v-if="row.score !== null" class="muted">{{ row.score }}/{{ row.max_score }}</span>
              <span v-if="row.needs_ta_review && row.review_status !== 'reviewed'" class="eval-text is-strong st-dot">
                {{ t('needTaReview') }}
              </span>
              <span v-else-if="row.review_status === 'reviewed'" class="eval-text is-muted">{{ t('reviewed') }}</span>
              <span class="spacer" />
              <span class="muted">{{ t('colAttempts') }} {{ row.attempt_count }}</span>
            </div>
          </div>
          <p v-if="!filteredRows.length" class="muted">{{ t('emptyRows') }}</p>
        </div>
      </section>

      <!-- 详情抽屉 -->
      <el-drawer
        v-model="detailVisible"
        :size="isMobile ? '100%' : '72%'"
        :title="detail ? `${t('colStudent')} ${detail.student_id}` : ''"
        class="detail-drawer"
      >
        <div v-if="detailLoading || !detail">
          <el-skeleton :rows="10" animated />
        </div>
        <template v-else>
          <p v-if="featureFlags.accessCodeEnabled" class="notice">
            <span class="notice-dot" />
            <span>{{ t('accessCodeFor', { code: detail.student_access_code || t('accessCodeMissing') }) }}</span>
          </p>

          <!-- 操作 -->
          <div class="detail-actions">
            <el-button size="small" :loading="busy === 'evaluate'" @click="reevaluate">
              {{ t('reEvaluate') }}
            </el-button>
            <el-button size="small" :loading="busy === 'regenerate'" @click="regenerate">
              {{ t('regenerate') }}
            </el-button>
            <span v-if="detail.case" class="muted detail-case">
              {{ t('colCase') }} {{ detail.case.case_id }} · {{ codeQualityName(detail.case.code_quality) }}
            </span>
          </div>
          <p v-if="detail.case && detail.case.behavior_notes" class="notice">
            <span class="notice-dot" />
            <span>{{ pickEn(detail.case, 'behavior_notes') }}</span>
          </p>

          <!-- 代码 -->
          <section class="d-section">
            <h2 class="section-title">{{ t('yourSubmission') }}</h2>
            <CodePanel :code="detail.submission.code" :language="detail.submission.language || 'c'" />
          </section>

          <!-- 诊断 -->
          <section class="d-section">
            <h2 class="section-title">{{ t('aiDiagnosis') }}</h2>
            <p class="block-text">
              <FormulaText :text="pickEn(detail.questionnaire.diagnosis, 'summary')" :code-tokens="codeTokenList" />
            </p>
            <ul v-if="detail.questionnaire.diagnosis.issues.length" class="issue-list">
              <li v-for="(iss, i) in detail.questionnaire.diagnosis.issues" :key="i">
                <p class="issue-head">
                  <span class="eval-text is-strong">{{ issueName(iss.type) }}</span>
                  <span class="muted">{{ iss.severity }}</span>
                </p>
                <FormulaText :text="pickEn(iss, 'description')" :code-tokens="codeTokenList" />
                <div v-if="iss.evidence" class="evidence-quote">{{ iss.evidence }}</div>
              </li>
            </ul>
          </section>

          <!-- 作答 / 版本选择 -->
          <section class="d-section">
            <div class="section-head">
              <h2 class="section-title">{{ t('qaByVersion') }}</h2>
              <span class="spacer" />
              <el-select v-model="viewVersion" size="small" class="version-select"
                         :placeholder="t('latestVersion')" clearable @change="reloadDetail">
                <el-option v-for="v in detail.questionnaire_versions" :key="v" :label="`v${v}`" :value="v" />
              </el-select>
              <el-select v-model="viewAttemptIndex" size="small" class="attempt-select">
                <el-option :value="-1" :label="t('latestAttempt')" />
                <el-option v-for="(a, i) in (detail.response?.attempts || [])" :key="a.attempt_id"
                           :value="i" :label="`${t('attemptNv', { n: a.attempt_no, v: a.questionnaire_version })}${a.time_expired ? ' ⏱' : ''}`" />
              </el-select>
            </div>

            <div v-for="(q, i) in detail.questionnaire.questions" :key="q.id" class="q-row">
              <p class="q-row__head">
                <span class="q-idx">Q{{ i + 1 }}</span>
                <span class="muted">{{ q.type }}</span>
                <span class="muted">{{ t('colIssues') }}：{{ issueName(q.target_issue) }}</span>
                <span v-if="q.type === 'code_variant'" class="eval-text is-strong">
                  {{ t('variantBadge') }} L{{ q.variant?.line_no }} → {{ q.variant?.replacement }}
                </span>
              </p>
              <p class="q-row__text"><FormulaText :text="pickEn(q, 'question')" :code-tokens="codeTokenList" /></p>
              <ol v-if="q.choices" class="q-row__choices">
                <li v-for="(c, j) in q.choices" :key="j" :value="j + 1">
                  <FormulaText :text="choiceText(q, j, c)" :code-tokens="codeTokenList" />
                </li>
              </ol>
              <p class="q-row__answers">
                <span>{{ t('expectedAnswer') }} <b>{{ q.expected_answer }}</b></span>
                <span>{{ t('studentAnswer') }}
                  <b :class="{ 'is-empty': !answersForView[q.id] }">
                    {{ Array.isArray(answersForView[q.id]) ? answersForView[q.id].join(', ') : (answersForView[q.id] || t('notSubmitted')) }}
                  </b>
                </span>
              </p>
            </div>
          </section>

          <!-- 评价 -->
          <section class="d-section">
            <div class="section-head">
              <h2 class="section-title">{{ t('evalCard') }}</h2>
              <span v-if="currentEvaluation" class="muted">
                {{ t('basedOnAttempt', { n: currentEvaluation.based_on_attempt, v: currentEvaluation.questionnaire_version }) }}
              </span>
            </div>
            <template v-if="currentEvaluation">
              <p class="eval-meta">
                <span :class="currentEvaluation.code_quality === 'correct' ? 'is-normal' : 'is-strong'" class="eval-text">
                  {{ codeQualityName(currentEvaluation.code_quality) }}
                </span>
                <span class="muted">{{ t('understandingLabel') }}：{{ understandingName(currentEvaluation.understanding) }}</span>
                <span class="muted">
                  {{ t('internalScore') }} {{ currentEvaluation.score }}/{{ currentEvaluation.max_score }}
                  · {{ t('confidence') }} {{ currentEvaluation.confidence }}
                  · {{ t('basis') }} {{ currentEvaluation.basis || t('none') }}
                </span>
              </p>
              <p class="block-text">
                <FormulaText :text="pickEn(currentEvaluation, 'feedback')" :code-tokens="codeTokenList" />
              </p>

              <el-table v-if="!isMobile && currentEvaluation.knowledge_points.length" :data="currentEvaluation.knowledge_points" size="small">
                <el-table-column :label="t('kpPoint')" min-width="110">
                  <template #default="{ row }">
                    {{ locale === 'en' ? (row.point_en || row.point) : (row.point_zh || row.point) }}
                  </template>
                </el-table-column>
                <el-table-column :label="t('kpStatus')" width="150">
                  <template #default="{ row }">
                    <span class="eval-text" :class="`is-${KP_STATUS_CLASS[row.status] || 'muted'}`">
                      {{ kpStatusName(row.status) }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('kpBasis')" width="110">
                  <template #default="{ row }">{{ row.basis }}</template>
                </el-table-column>
                <el-table-column :label="t('kpCode')" min-width="140">
                  <template #default="{ row }">
                    <code v-if="row.code_evidence" class="kp-code">{{ row.code_evidence }}</code>
                    <span v-else class="muted">—</span>
                  </template>
                </el-table-column>
                <el-table-column :label="t('kpEvidence')" min-width="180">
                  <template #default="{ row }">
                    <div class="evidence-quote">{{ row.student_evidence || t('none') }}</div>
                  </template>
                </el-table-column>
              </el-table>

              <div v-else-if="isMobile && currentEvaluation.knowledge_points.length" class="kp-list">
                <div v-for="(kp, i) in currentEvaluation.knowledge_points" :key="i" class="kp-item">
                  <div class="kp-item__head">
                    <b>{{ locale === 'en' ? (kp.point_en || kp.point) : (kp.point_zh || kp.point) }}</b>
                    <span class="eval-text" :class="`is-${KP_STATUS_CLASS[kp.status] || 'muted'}`">
                      {{ kpStatusName(kp.status) }}
                    </span>
                  </div>
                  <div class="kp-item__row">
                    <span class="muted">{{ t('kpBasis') }}</span> {{ kp.basis }}
                    <template v-if="kp.code_evidence">
                      · <code class="kp-code">{{ kp.code_evidence }}</code>
                    </template>
                  </div>
                  <div class="evidence-quote">{{ kp.student_evidence || t('none') }}</div>
                </div>
              </div>
            </template>
            <el-alert
              v-else-if="currentRecord && currentRecord.status === 'failed'"
              type="error" :closable="false"
              :title="t('evalFailedCard', { err: currentRecord.error })"
              :description="t('evalFailedHint')"
            />
            <p v-else class="muted">{{ t('noEvalYet') }}</p>
          </section>

          <!-- 复核闭环 -->
          <section class="d-section">
            <h2 class="section-title">{{ t('taReview') }}</h2>
            <template v-if="attemptForView">
              <p class="muted target-attempt">
                {{ t('targetAttempt', { n: attemptForView.attempt_no, v: attemptForView.questionnaire_version }) }}
                <span v-if="attemptForView.time_expired" class="eval-text is-danger">· ⏱ {{ t('expired') }}</span>
                <template v-if="latestReviewFor(attemptForView.attempt_id)">
                  · {{ latestReviewFor(attemptForView.attempt_id).decision === 'revised' ? t('lastReviewRevised') : t('lastReviewConfirmed') }}
                  {{ latestReviewFor(attemptForView.attempt_id).published ? t('feedbackPublished') : '' }}
                </template>
              </p>
              <el-form label-position="top">
                <el-radio-group v-model="reviewForm.decision" class="decision-group">
                  <el-radio value="confirmed">{{ t('confirmSystem') }}</el-radio>
                  <el-radio value="revised">{{ t('reviseConclusion') }}</el-radio>
                </el-radio-group>
                <template v-if="reviewForm.decision === 'revised'">
                  <el-select v-model="reviewForm.revised_understanding" class="revised-select" style="margin-top:10px">
                    <el-option :label="t('uMastered')" value="mastered" />
                    <el-option :label="t('uPartial')" value="partial" />
                    <el-option :label="t('uLimited')" value="limited" />
                    <el-option :label="t('uInsufficient')" value="insufficient" />
                  </el-select>
                  <el-input
                    v-model="reviewForm.comment" type="textarea" :rows="2"
                    :placeholder="t('reviseReasonPh')" style="margin-top:8px"
                  />
                </template>
                <el-input
                  v-model="reviewForm.feedback_to_student" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }"
                  :placeholder="t('feedbackPh')"
                  style="margin-top:10px"
                />
                <div class="review-actions">
                  <el-switch v-model="reviewForm.published" :active-text="t('publishNow')" />
                  <span class="spacer" />
                  <el-button type="primary" :loading="busy === 'review'" @click="submitReview">
                    {{ t('saveReview') }}
                  </el-button>
                </div>
              </el-form>
            </template>
            <p v-else class="muted">{{ t('noAttemptYet') }}</p>
          </section>

          <!-- 评价历史 -->
          <section v-if="detail.evaluation_records.length > 1" class="d-section">
            <h2 class="section-title">{{ t('evalHistory') }}</h2>
            <el-table v-if="!isMobile" :data="detail.evaluation_records" size="small">
              <el-table-column :label="t('time')">
                <template #default="{ row }">{{ new Date(row.started_at).toLocaleString() }}</template>
              </el-table-column>
              <el-table-column :label="t('attemptNo')" width="80">
                <template #default="{ row }">{{ t('attemptN', { n: row.attempt_no }) }}</template>
              </el-table-column>
              <el-table-column :label="t('examVersion')" width="80">
                <template #default="{ row }">v{{ row.questionnaire_version }}</template>
              </el-table-column>
              <el-table-column :label="t('colStatus')" width="90">
                <template #default="{ row }">
                  <span class="eval-text" :class="`is-${evalColor(row.status)}`">{{ row.status }}</span>
                </template>
              </el-table-column>
              <el-table-column :label="t('note')">
                <template #default="{ row }">{{ row.error || (row.result ? pickEn(row.result, 'feedback') : '') }}</template>
              </el-table-column>
            </el-table>
            <div v-else class="history-list">
              <div v-for="(rec, i) in detail.evaluation_records" :key="i" class="history-item">
                <div class="history-item__top">
                  <span>{{ new Date(rec.started_at).toLocaleString() }}</span>
                  <span class="eval-text" :class="`is-${evalColor(rec.status)}`">{{ rec.status }}</span>
                </div>
                <div class="muted">{{ t('attemptN', { n: rec.attempt_no }) }} · v{{ rec.questionnaire_version }}</div>
                <div v-if="rec.error || rec.result" class="history-note">
                  {{ rec.error || pickEn(rec.result, 'feedback') }}
                </div>
              </div>
            </div>
          </section>
        </template>
      </el-drawer>

      <!-- 扫码答题二维码 -->
      <el-dialog v-model="qrVisible" :title="t('qrTitle')" width="360px" align-center>
        <div v-loading="qrLoading" class="qr-body">
          <template v-if="qrData">
            <div class="qr-box" v-html="qrData.svg"></div>
            <p class="qr-tip">{{ t('qrTip') }}</p>
            <div class="qr-url-row">
              <code class="qr-url">{{ qrData.url }}</code>
              <el-button size="small" @click="copyQrUrl">{{ t('copy') }}</el-button>
            </div>
          </template>
        </div>
      </el-dialog>
    </template>
  </div>
</template>

<style scoped>
/* ---- 登录 ---- */
.login-wrap { display: flex; justify-content: center; padding: 48px 20px 64px; }
.login-col { width: 100%; max-width: 400px; }
.login-col .page-title { margin-bottom: 20px; }
.login-btn { width: 100%; margin-top: 14px; }

/* ---- 页头 ---- */
.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}
.panel-head .page-title { margin: 0; }

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

/* ---- 统计（纯数字） ---- */
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}
.stat { display: flex; flex-direction: column; gap: 2px; }
.stat-num {
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.stat-label { font-size: 12.5px; color: var(--aa-text-3); }

/* ---- 表格 / 列表 ---- */
.block {
  background: var(--aa-surface);
  border-radius: var(--aa-radius-lg);
  padding: 16px 20px 20px;
}
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.toolbar .spacer { flex: 1; }
.search-input { width: 220px; }
.overview-table :deep(.el-table__row) { cursor: pointer; }
/* 例外行：左侧 2px 竖条 + 极淡底，一屏只在该行显形 */
.overview-table :deep(.row-alert td.el-table__cell) { background: var(--aa-hover); }
.overview-table :deep(.row-alert td.el-table__cell:first-child) {
  box-shadow: inset 2px 0 0 var(--aa-ink-1);
}

.stu-list { display: flex; flex-direction: column; }
.stu-row {
  padding: 14px 0 14px 2px;
  border-bottom: 1px solid var(--aa-line-soft);
  cursor: pointer;
}
.stu-row:last-child { border-bottom: none; }
.stu-row.is-alert {
  padding-left: 12px;
  box-shadow: inset 2px 0 0 var(--aa-ink-1);
}
.stu-row__top { display: flex; align-items: baseline; gap: 8px; }
.stu-row__top .spacer { flex: 1; }
.stu-id { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stu-row__issues { margin: 4px 0 6px; font-size: 12.5px; color: var(--aa-text-2); line-height: 1.5; }
.stu-row__bottom { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; font-size: 12.5px; }
.stu-row__bottom .spacer { flex: 1; }

.eval-text { font-size: 12.5px; }
.eval-text.is-strong { color: var(--aa-ink-1); font-weight: 500; }
.eval-text.is-normal { color: var(--aa-ink-2); }
.eval-text.is-muted { color: var(--aa-ink-3); }
.eval-text.is-danger { color: var(--aa-danger); }

/* ---- 抽屉 ---- */
.d-section { padding: 18px 0; border-top: 1px solid var(--aa-line-soft); }
.d-section:first-of-type { border-top: none; padding-top: 4px; }
.section-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.section-head .spacer { flex: 1; }
.section-title {
  margin: 0 0 10px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--aa-text-3);
}
.section-head .section-title { margin: 0; }

.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 4px 0 10px; }
.detail-case { font-size: 12.5px; }
.block-text { margin: 0; font-size: 13.5px; line-height: 1.7; }

.issue-list { margin: 0; padding: 0; list-style: none; }
.issue-list li { padding: 10px 0; border-top: 1px solid var(--aa-line-soft); font-size: 13.5px; line-height: 1.65; }
.issue-head { display: flex; gap: 8px; margin: 0 0 4px; }

.version-select { width: 110px; }
.attempt-select { width: 170px; }

.q-row { padding: 12px 0; border-top: 1px solid var(--aa-line-soft); }
.q-row__head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin: 0 0 4px; font-size: 12.5px; }
.q-idx { font-family: var(--font-mono); font-size: 12px; color: var(--aa-text-3); }
.q-row__text { margin: 0 0 4px; font-size: 13.5px; line-height: 1.6; }
.q-row__choices { margin: 0 0 6px; padding-left: 20px; font-size: 13px; line-height: 1.7; }
.q-row__answers { display: flex; gap: 16px; flex-wrap: wrap; margin: 0; font-size: 12.5px; color: var(--aa-text-2); }
.q-row__answers b { font-weight: 600; color: var(--aa-ink); }
.q-row__answers b.is-empty { color: var(--aa-text-3); font-weight: 400; }

.eval-meta { display: flex; gap: 12px; flex-wrap: wrap; align-items: baseline; margin: 0 0 8px; font-size: 12.5px; }
.kp-code { font-size: 12px; color: var(--aa-text-2); }

.kp-list { display: flex; flex-direction: column; }
.kp-item { padding: 10px 0; border-top: 1px solid var(--aa-line-soft); }
.kp-item__head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 4px; font-size: 13.5px; }
.kp-item__row { font-size: 12.5px; color: var(--aa-text-2); margin-bottom: 4px; }

.target-attempt { margin: 0 0 10px; font-size: 12.5px; }
.decision-group { display: flex; flex-direction: column; }
.revised-select { width: 220px; }
.review-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
.review-actions .spacer { flex: 1; }

.history-list { display: flex; flex-direction: column; }
.history-item { padding: 10px 0; border-top: 1px solid var(--aa-line-soft); }
.history-item__top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-size: 13px; }
.history-note { margin-top: 4px; font-size: 12.5px; color: var(--aa-text-2); line-height: 1.6; }

.qr-body { display: flex; flex-direction: column; align-items: center; min-height: 220px; padding: 4px 0 8px; }
.qr-box { width: 240px; height: 240px; background: #fff; border: 1px solid var(--aa-line-soft); border-radius: 8px; padding: 10px; }
.qr-box svg { width: 100%; height: 100%; display: block; }
.qr-tip { margin: 12px 0 8px; font-size: 13px; color: var(--aa-text-2); text-align: center; line-height: 1.6; }
.qr-url-row { display: flex; align-items: center; gap: 8px; width: 100%; justify-content: center; }
.qr-url { font-size: 12.5px; word-break: break-all; }

@media (max-width: 767px) {
  .login-wrap { padding: 20px 16px 48px; }
  .stats { grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 18px; }
  .stat-num { font-size: 22px; }
  .block { border-radius: var(--aa-radius); padding: 12px 16px 16px; }
  .search-input { width: 100%; }
  .version-select { width: 92px; }
  .attempt-select { width: 140px; }
  .review-actions .el-button { flex: 1; }
}
</style>
