<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchTaRoster, fetchTaStudent, saveTaGrade, downloadTaFile, setCredentials, clearCredentials, ApiError } from '../api'
import { authState } from '../store'
import CodePanel from '../components/CodePanel.vue'
import FormulaText from '../components/FormulaText.vue'

const route = useRoute()
const router = useRouter()
const sid = computed(() => String(route.params.studentId || ''))
const isTeacher = computed(() => authState.credentials?.role === 'teacher')
const token = ref('')
const roster = ref(null)
const detail = ref(null)
const loading = ref(false)
const saving = ref(false)
const downloading = ref(false)
const error = ref('')
const conflict = ref(false)
const keyword = ref('')
const filter = ref('all')
const page = ref(1)
const activeFile = ref(0)
const form = reactive({ score: '', comment: '', grader_name: localStorage.getItem('aa_grader_name') || '' })
const baseline = ref('')
let pendingSave = null
let viewRequest = 0
const formSignature = () => JSON.stringify(form)
const dirty = computed(() => !!detail.value && formSignature() !== baseline.value)
const rows = computed(() => roster.value?.rows || [])
const counts = computed(() => ({
  all: rows.value.length,
  ungraded: rows.value.filter(r => r.grading_status !== 'graded').length,
  graded: rows.value.filter(r => r.grading_status === 'graded').length,
  generatable: rows.value.filter(r => r.can_generate).length,
  missing: rows.value.filter(r => !r.can_generate).length,
  analyzed: rows.value.filter(r => r.question_risk?.status === 'ready').length,
  flagged: rows.value.filter(r => r.question_risk?.status === 'ready' && r.question_risk.ai_assistance_cue).length,
}))
const filtered = computed(() => rows.value.filter(row => {
  if (!row.student_id.includes(keyword.value.trim())) return false
  if (filter.value === 'ungraded') return row.grading_status !== 'graded'
  if (filter.value === 'graded') return row.grading_status === 'graded'
  if (filter.value === 'generatable') return row.can_generate
  if (filter.value === 'missing') return !row.can_generate
  if (filter.value === 'ai_cue') return row.question_risk?.status === 'ready' && row.question_risk.ai_assistance_cue
  return true
}))
const visibleRows = computed(() => filtered.value.slice((page.value - 1) * 15, page.value * 15))
const files = computed(() => detail.value?.source_files?.length ? detail.value.source_files
  : detail.value?.code ? [{ filename: '提交代码', code: detail.value.code }] : [])
const displayedFile = computed(() => files.value[activeFile.value])
watch([keyword, filter], () => { page.value = 1 })

function date(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}
function statusName(status) { return { ungraded: '未评分', graded: '已评分', needs_review: '需重评' }[status] || status }
function paperName(row) { return { ready: '答卷已生成', missing_source: '缺少代码', outdated: '可重新生成', not_generated: '可生成答卷' }[row.paper_status] }
function riskName(risk) {
  if (!risk || risk.status === 'pending') return '待分析'
  if (risk.status === 'unavailable') return '无代码，无法分析'
  if (risk.status !== 'ready') return '记录需复核'
  return { normal: '常规追问', follow_up: '建议追问', verify: '重点核验' }[risk.priority] || '待分析'
}
function reportError(e) {
  if (e instanceof ApiError && [401, 403].includes(e.status)) {
    clearCredentials()
    error.value = '请使用教师 / TA 口令登录。'
  } else {
    error.value = e.detail || e.message || '请求失败，请重试。'
  }
}
function applyDetail(data) {
  detail.value = data
  form.score = data.current_grade?.score ?? ''
  form.comment = data.current_grade?.comment || ''
  baseline.value = formSignature()
  activeFile.value = 0
  pendingSave = null
  conflict.value = false
}
async function refresh() {
  const id = ++viewRequest
  error.value = ''
  detail.value = null
  if (!isTeacher.value) { roster.value = null; loading.value = false; return }
  loading.value = true
  try {
    const [list, student] = await Promise.all([fetchTaRoster(), sid.value ? fetchTaStudent(sid.value) : Promise.resolve(null)])
    if (id !== viewRequest) return
    roster.value = list
    if (student) applyDetail(student)
  } catch (e) { if (id === viewRequest) reportError(e) }
  finally { if (id === viewRequest) loading.value = false }
}
watch([sid, () => authState.credentials], refresh, { immediate: true })
onUnmounted(() => { viewRequest += 1 })
function login() {
  if (!token.value.trim()) return
  setCredentials({ role: 'teacher', token: token.value.trim() })
  token.value = ''
}
function confirmLeave() { return !dirty.value || window.confirm('评分尚未保存，确定离开并放弃本次修改？') }
onBeforeRouteLeave(confirmLeave)
onBeforeRouteUpdate((to, from) => to.params.studentId === from.params.studentId || confirmLeave())
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))

async function reloadDetail() {
  if (confirmLeave()) await refresh()
}
async function save(andNext = false) {
  if (saving.value || !detail.value) return
  if (typeof form.score !== 'number' || !Number.isFinite(form.score) || form.score < 0 || form.score > 100) {
    error.value = '请填写 0–100 之间的分数，0 分也是有效评分。'
    return
  }
  if (!form.grader_name.trim()) { error.value = '请填写评分人姓名或工号。'; return }
  saving.value = true
  error.value = ''
  const currentSid = detail.value.student_id
  const viewId = viewRequest
  const payload = { score: form.score, comment: form.comment, grader_name: form.grader_name.trim(),
    revision: detail.value.revision, submission_fingerprint: detail.value.submission_fingerprint,
    answer_attempt_id: detail.value.latest_answer_attempt_id || '' }
  const signature = JSON.stringify(payload)
  if (!pendingSave || pendingSave.signature !== signature) pendingSave = { signature, payload: { ...payload, save_token: crypto.randomUUID() } }
  try {
    const data = await saveTaGrade(currentSid, pendingSave.payload)
    if (viewId !== viewRequest) return
    localStorage.setItem('aa_grader_name', payload.grader_name)
    applyDetail(data)
    ElMessage.success(`已保存 ${currentSid} 的评分：${data.current_grade.score} / 100`)
    roster.value = await fetchTaRoster()
    if (andNext && viewId === viewRequest) {
      const next = rows.value.find(row => row.student_id !== currentSid && row.grading_status !== 'graded')
      await router.push(next ? `/ta/${next.student_id}` : '/ta')
    }
  } catch (e) {
    if (viewId === viewRequest) {
      conflict.value = e instanceof ApiError && e.status === 409
      reportError(e)
    }
  } finally { saving.value = false }
}
function openPaper(studentId) { router.push({ path: '/', query: { student_id: studentId } }) }
function studentAnswer(question) {
  const answer = question.answer
  if (question.type === 'text') return answer || '未作答'
  const choices = Array.isArray(answer) ? answer : answer ? [answer] : []
  return choices.map(value => `${value}. ${question.choices?.[value.charCodeAt(0) - 65] || ''}`).join('\n') || '未作答'
}
function referenceAnswer(question) {
  return studentAnswer({ ...question, answer: question.type === 'multiple_choice'
    ? question.expected_answer.split(',') : question.expected_answer })
}
async function download(path, filename) {
  downloading.value = true
  error.value = ''
  try {
    const blob = await downloadTaFile(path)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (e) { reportError(e) }
  finally { downloading.value = false }
}
</script>

<template>
  <main class="ta-page">
    <header class="ta-heading">
      <div>
        <p class="eyebrow">{{ roster?.assignment_title || 'PA1 · 四则运算' }}</p>
        <h1>{{ sid ? `学号 ${sid}` : 'TA 评分工作台' }}<small v-if="sid && detail?.student_name" class="student-name">{{ detail.student_name }}</small></h1>
        <p class="subtitle">{{ sid ? '查看原始提交，记录你的评分与评语。' : '全部学号、原始提交和评分记录，集中在这里。' }}</p>
      </div>
      <router-link :to="sid ? '/ta' : '/'" class="back-link">{{ sid ? '← 返回全班名单' : '返回抽查首页 ↗' }}</router-link>
    </header>

    <form v-if="!isTeacher" class="login-card" @submit.prevent="login">
      <h2>教师 / TA 登录</h2>
      <p>使用原来的教师口令，即可查看名单和评分。</p>
      <label for="ta-token">访问口令</label>
      <input id="ta-token" v-model="token" type="password" autocomplete="current-password" required />
      <button class="primary" type="submit">进入评分工作台</button>
    </form>
    <div v-if="error" class="error-box" role="alert">
      {{ error }}
      <button v-if="conflict" type="button" class="link-button" @click="reloadDetail">重新载入最新记录</button>
    </div>
    <p v-if="loading" class="loading" role="status">正在读取学生提交…</p>

    <template v-if="isTeacher && roster && !sid">
      <section class="stat-strip" aria-label="班级统计">
        <button :class="{ selected: filter === 'all' }" @click="filter = 'all'"><strong>{{ counts.all }}</strong><span>全部学生</span></button>
        <button :class="{ selected: filter === 'ungraded' }" @click="filter = 'ungraded'"><strong>{{ counts.ungraded }}</strong><span>待评分</span></button>
        <button :class="{ selected: filter === 'graded' }" @click="filter = 'graded'"><strong>{{ counts.graded }}</strong><span>已评分</span></button>
        <button :class="{ selected: filter === 'generatable' }" @click="filter = 'generatable'"><strong>{{ counts.generatable }}</strong><span>可生成答卷</span></button>
      </section>
      <div class="roster-tools">
        <label class="search-field"><span>搜索学号</span><input v-model="keyword" aria-label="搜索学号" placeholder="输入全部或部分学号" inputmode="numeric" /></label>
        <label class="status-filter"><span>筛选</span><select v-model="filter" aria-label="筛选"><option value="all">全部学生</option><option value="ungraded">未评分 / 需重评</option><option value="graded">已评分</option><option value="generatable">可生成答卷</option><option value="ai_cue">需核验 AI 使用线索（{{ counts.flagged }}）</option><option value="missing">缺少代码（{{ counts.missing }}）</option></select></label>
        <button class="secondary export-button" :disabled="downloading" @click="download('/api/ta/grades.csv', 'PA1-评分记录.csv')">导出评分 CSV</button>
      </div>
      <p class="risk-count">追问风险已分析 {{ counts.analyzed }} / {{ counts.generatable }} 位。线索只用于安排追问，不能凭代码确定是否使用 AI。</p>
      <div class="table-wrap">
        <table class="roster-table">
          <thead><tr><th>学号</th><th>原始提交</th><th>答卷</th><th>追问风险</th><th>源码检查</th><th>评分状态</th><th>得分 / 100</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.student_id" :data-student-id="row.student_id">
              <td><router-link class="sid-link" :to="`/ta/${row.student_id}`">{{ row.student_id }}</router-link><small v-if="row.student_name">{{ row.student_name }}</small></td>
              <td><span>{{ row.source_count ? `${row.source_count} 个源文件` : '无可读代码' }}</span><small>{{ date(row.submitted_at) }}</small></td>
              <td><span :class="{ muted: !row.can_generate }">{{ paperName(row) }}</span><small v-if="row.has_answers">抽查答案已提交</small></td>
              <td><span :class="{ 'risk-flag': row.question_risk?.ai_assistance_cue }">{{ riskName(row.question_risk) }}</span><small v-if="row.question_risk?.ai_assistance_cue">AI 使用线索待核验</small></td>
              <td><span v-if="row.inspection_status === 'reviewed'">已检查</span><span v-else-if="row.inspection_status === 'outdated'" class="status needs_review">源码已更新，需复查</span><span v-else class="muted">—</span></td>
              <td><span class="status" :class="row.grading_status">{{ statusName(row.grading_status) }}</span><small v-if="row.grade">{{ row.grade.grader_name }}</small></td>
              <td class="score-cell">{{ row.grade?.score ?? '—' }}</td>
              <td class="row-actions"><router-link :to="`/ta/${row.student_id}`">查看 / 评分</router-link><button type="button" :disabled="!row.can_generate" @click="openPaper(row.student_id)">{{ row.paper_status === 'ready' ? '打开答卷作答' : '生成并作答' }}</button></td>
            </tr>
            <tr v-if="!visibleRows.length"><td colspan="8" class="empty">没有符合条件的学生。</td></tr>
          </tbody>
        </table>
      </div>
      <div class="pagination"><span>共 {{ filtered.length }} 位学生 · 点击学号查看提交</span><el-pagination v-model:current-page="page" :page-size="15" :total="filtered.length" layout="prev, pager, next" /></div>
    </template>

    <div v-if="isTeacher && detail" class="grading-layout">
      <section class="submission-card">
        <div class="submission-heading"><h2>原始提交</h2><span>{{ date(detail.submitted_at) }}</span></div>
        <div class="submission-actions">
          <button class="secondary" :disabled="!detail.can_generate || saving" @click="openPaper(detail.student_id)">{{ detail.paper_status === 'ready' ? '打开答卷 / 现场作答 ↗' : '生成答卷并作答 ↗' }}</button>
          <button v-if="detail.download_available" class="link-button" :disabled="downloading" @click="download(`/api/ta/students/${detail.student_id}/download`, `PA1-${detail.student_id}-原始提交.zip`)">下载原始提交包</button>
        </div>
        <div v-if="files.length" class="source-view">
          <div class="file-tabs" role="tablist" aria-label="源文件">
            <button v-for="(file, index) in files" :key="`${file.filename}-${index}`" type="button" role="tab" :aria-selected="activeFile === index" :class="{ active: activeFile === index }" @click="activeFile = index">{{ file.filename }}</button>
          </div>
          <CodePanel v-if="displayedFile" :code="displayedFile.code" :language="detail.language" wrap />
        </div>
        <div v-else class="no-code"><strong>这次提交没有可读取的 C 源代码</strong><p>可以核对原始提交包，并由 TA 手动记录评分与说明。</p></div>
        <details v-if="detail.attachments.length" class="extra-detail"><summary>原始附件（{{ detail.attachments.length }}）</summary><ul><li v-for="(attachment, i) in detail.attachments" :key="i">{{ attachment.name }} <span>{{ Math.ceil(attachment.size / 1024) }} KB</span></li></ul></details>
        <details v-if="detail.submission_text" class="extra-detail"><summary>Blackboard 提交文本</summary><pre class="submission-text">{{ detail.submission_text }}</pre></details>
        <details v-if="detail.import_notes.length" class="extra-detail"><summary>提交整理说明</summary><ul><li v-for="note in detail.import_notes" :key="note">{{ note }}</li></ul></details>
        <section v-if="detail.inspection" class="inspection-card">
          <div class="inspection-heading"><h2>源码检查记录</h2><span>{{ detail.inspection.reviewer }} · {{ date(detail.inspection.reviewed_at) }}</span></div>
          <p v-if="!detail.inspection.is_current" class="stale-notice">学生提交已更新，这份检查对应旧代码，请重新核对。</p>
          <p class="inspection-summary">{{ detail.inspection.summary }}</p>
          <p v-if="detail.inspection.score_recommendation !== undefined" class="inspection-score">{{ detail.inspection.is_current ? '本次初审建议' : '历史初审建议' }}：<strong>{{ detail.inspection.score_recommendation }} / 100</strong></p>
          <details v-if="detail.inspection.rubric" class="extra-detail"><summary>查看本次评分口径</summary><p>{{ detail.inspection.rubric }}</p></details>
          <ul v-if="detail.inspection.deductions?.length" class="inspection-deductions"><li v-for="deduction in detail.inspection.deductions" :key="deduction.reason">{{ deduction.reason }}：-{{ deduction.points }} 分</li></ul>
          <article v-for="file in detail.inspection.files" :key="file.filename" class="inspection-file">
            <strong>{{ file.filename }}</strong>
            <div class="inspection-tags">
              <span :class="file.explicit_return ? '' : 'attention'">{{ file.explicit_return ? '有显式 return' : '未显式写 return' }}</span>
              <span :class="file.comments ? '' : 'attention'">{{ file.comments ? '有注释' : '无代码注释' }}</span>
              <span :class="file.c_syntax_ok ? '' : 'attention'">{{ file.c_syntax_ok ? 'C 语法检查通过' : '按 C 语法检查未通过' }}</span>
            </div>
            <ul v-if="file.notes.length"><li v-for="note in file.notes" :key="note">{{ note }}</li></ul>
          </article>
          <p class="inspection-caveat">{{ detail.inspection.method_note }}</p>
        </section>
        <section class="question-risk-card">
          <h2>追问风险</h2>
          <p v-if="detail.question_risk?.status === 'pending'" class="risk-unavailable">这位学生尚未完成风险分析。</p>
          <p v-else-if="detail.question_risk?.status === 'unavailable'" class="risk-unavailable">没有可读代码，无法评估追问线索。</p>
          <p v-else-if="detail.question_risk?.status !== 'ready'" class="risk-unavailable">原始代码或风险记录已更新，需重新分析。</p>
          <template v-else>
            <p class="risk-level">{{ riskName(detail.question_risk) }}<span v-if="detail.question_risk.ai_assistance_cue"> · AI 使用线索待人工核验</span></p>
            <article v-for="(signal, index) in detail.question_risk.signals" :key="index" class="risk-signal">
              <strong>{{ signal.filename }} · 第 {{ signal.global_line }} 行</strong>
              <code>{{ signal.code_excerpt }}</code>
              <p>{{ signal.observation }}</p>
              <p><strong>可追问：</strong>{{ signal.follow_up_question }}</p>
            </article>
            <p v-if="!detail.question_risk.signals.length" class="risk-unavailable">当前未记录特定代码线索，可按答卷正常核验理解。</p>
            <p class="risk-unavailable">{{ detail.question_risk.uncertainty }}</p>
            <small>生成来源：{{ detail.question_risk.model_id }} · {{ date(detail.question_risk.reviewed_at) }}</small>
          </template>
        </section>
        <section class="classroom-responses">
          <h2>课堂抽查作答</h2>
          <p v-if="!detail.answer_attempts?.length" class="no-answers">尚未提交抽查答案。可打开个性化答卷直接作答。</p>
          <details v-for="(attempt, attemptIndex) in detail.answer_attempts || []" :key="attempt.attempt_id" class="answer-attempt" :open="attemptIndex === 0">
            <summary>第 {{ attempt.attempt }} 次作答 · {{ date(attempt.submitted_at) }} · 答卷 v{{ attempt.questionnaire_version }}{{ attempt.is_current_paper ? '' : '（历史答卷）' }}</summary>
            <p v-if="!attempt.questions.length" class="no-answers">该次答卷快照不可用，原始答案：{{ attempt.answers }}</p>
            <article v-for="(question, index) in attempt.questions" :key="question.id" class="review-answer">
              <p class="review-question"><strong>{{ index + 1 }}.</strong> <FormulaText :text="question.question" /></p>
              <pre v-if="question.variant" class="submission-text">假设第 {{ question.variant.line_no }} 行替换为：{{ question.variant.replacement }}</pre>
              <div class="student-answer"><span>学生作答</span><p><FormulaText :text="studentAnswer(question)" /></p></div>
              <details class="expected-answer"><summary>查看参考答案</summary><p><FormulaText :text="referenceAnswer(question)" /></p></details>
            </article>
            <details v-if="attempt.source_code && attempt.source_code !== detail.code" class="extra-detail"><summary>该次作答对应的历史代码</summary><CodePanel :code="attempt.source_code" :language="detail.language" wrap /></details>
          </details>
        </section>
      </section>
      <aside class="grade-sidebar">
        <form class="grade-card" @submit.prevent="save(false)">
          <div class="grade-title"><h2>TA 评分</h2><span>100 分制</span></div>
          <p v-if="detail.grading_status === 'needs_review'" class="stale-notice">学生的原始提交或抽查答案已更新，下方是之前的评分。请核对后重新评分。</p>
          <label for="grade-score">得分</label>
          <div class="score-input"><input id="grade-score" v-model.number="form.score" type="number" min="0" max="100" step="0.01" placeholder="0–100" :disabled="saving" required /><span>/ 100</span></div>
          <label for="grade-grader">评分人</label>
          <input id="grade-grader" v-model="form.grader_name" maxlength="80" placeholder="TA 姓名或工号（填写一次即可）" :disabled="saving" required />
          <label for="grade-comment">评语 <span class="optional">选填</span></label>
          <textarea id="grade-comment" v-model="form.comment" rows="5" maxlength="8000" placeholder="记录扣分原因、掌握情况或现场抽查结果" :disabled="saving" />
          <p class="save-state">{{ dirty ? '有尚未保存的修改' : detail.current_grade ? `已保存 · ${date(detail.current_grade.saved_at)}` : '尚未评分' }}</p>
          <button class="primary save-button" type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存评分' }}</button>
          <button class="secondary next-button" type="button" :disabled="saving" @click="save(true)">保存并评下一位</button>
          <p class="grade-note">分数由 TA 手动填写。修改会保留历史记录。</p>
        </form>
        <details v-if="detail.grade_history.length" class="grade-history">
          <summary>评分历史（{{ detail.grade_history.length }}）</summary>
          <article v-for="record in detail.grade_history" :key="record.record_id">
            <div><strong>{{ record.score }} / 100</strong><span>{{ record.grader_name }}</span></div>
            <small>{{ date(record.saved_at) }}</small>
            <p v-if="record.comment">{{ record.comment }}</p>
          </article>
        </details>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.ta-page { max-width: 1200px; margin: 0 auto; padding: 38px 26px 60px; }
.ta-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 27px; }
.eyebrow { margin: 0 0 9px; color: var(--aa-primary); font-size: 12px; font-weight: 600; }
h1 { font-size: 28px; letter-spacing: -.03em; margin: 0; }
.student-name { display: inline; font-size: 14px; font-weight: 400; color: var(--aa-text-2); margin-left: 11px; letter-spacing: normal; }
h2 { font-size: 17px; margin: 0; }
.subtitle { margin: 10px 0 0; color: var(--aa-text-2); font-size: 14px; }
.back-link { white-space: nowrap; font-size: 13px; }
button, input, textarea, select { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .5; }
input, textarea, select { color: var(--aa-ink); background: var(--aa-bg); border: 1px solid var(--aa-line); border-radius: 8px; padding: 11px 12px; box-sizing: border-box; max-width: 100%; }
input:focus, textarea:focus, select:focus { outline: 2px solid var(--aa-primary-soft); border-color: var(--aa-primary); }
.primary, .secondary { display: inline-flex; justify-content: center; align-items: center; padding: 11px 17px; border-radius: 8px; font-size: 13px; font-weight: 600; }
.primary { background: var(--aa-primary); border: 1px solid var(--aa-primary); color: white; }
.secondary { background: var(--aa-surface); border: 1px solid var(--aa-line); color: var(--aa-ink); }
.link-button { background: none; border: none; color: var(--aa-primary); padding: 8px 0; font-size: 12px; }
.login-card { max-width: 390px; padding: 27px; background: var(--aa-surface); border-radius: 16px; }
.login-card p { color: var(--aa-text-2); font-size: 13px; line-height: 1.7; }
.login-card label { display: block; margin: 19px 0 8px; font-size: 13px; }
.login-card input { width: 100%; margin-bottom: 16px; }
.error-box { background: var(--el-color-danger-light-9); border: 1px solid var(--el-color-danger-light-5); color: var(--el-color-danger); padding: 14px 17px; border-radius: 10px; margin-bottom: 18px; line-height: 1.7; }
.error-box button { display: block; }
.loading { color: var(--aa-text-2); }
.stat-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 25px; }
.stat-strip button { background: var(--aa-surface); border: 1px solid transparent; border-radius: 12px; text-align: left; padding: 19px 21px; color: var(--aa-ink); }
.stat-strip button.selected { border-color: var(--aa-primary); }
.stat-strip strong { font-size: 27px; font-variant-numeric: tabular-nums; display: block; margin-bottom: 5px; }
.stat-strip span { font-size: 12px; color: var(--aa-text-2); }
.roster-tools { display: flex; gap: 14px; align-items: flex-end; margin-bottom: 16px; }
.risk-count { color: var(--aa-text-2); font-size: 12px; line-height: 1.7; margin: -3px 0 13px; }
.risk-flag { color: var(--aa-danger); }
.roster-tools label span { display: block; margin-bottom: 8px; font-size: 12px; }
.search-field { flex: 1; max-width: 320px; }
.search-field input { width: 100%; }
.export-button { margin-left: auto; height: 42px; }
.table-wrap { background: var(--aa-surface); border: 1px solid var(--aa-line-soft); border-radius: 12px; overflow: auto; }
.roster-table { width: 100%; border-collapse: collapse; white-space: nowrap; text-align: left; font-size: 13px; }
.roster-table th { font-size: 12px; font-weight: 500; color: var(--aa-text-2); background: var(--aa-surface-2); padding: 15px; }
.roster-table td { border-top: 1px solid var(--aa-line-soft); padding: 15px; }
.roster-table tr:hover td { background: var(--aa-hover); }
.sid-link { font-weight: 600; font-variant-numeric: tabular-nums; }
small { display: block; margin-top: 5px; font-size: 11px; color: var(--aa-text-3); }
.muted { color: var(--aa-text-3); }
.status { font-size: 12px; }
.status.graded { color: var(--aa-primary); }
.status.needs_review { color: var(--aa-danger); }
.score-cell { font-weight: 600; font-variant-numeric: tabular-nums; }
.row-actions button { display: block; background: none; border: 0; padding: 6px 0 0; color: var(--aa-text-2); font-size: 12px; }
.empty { text-align: center; padding: 35px !important; color: var(--aa-text-2); }
.pagination { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-top: 18px; }
.pagination > span { font-size: 12px; color: var(--aa-text-2); }
.grading-layout { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 24px; align-items: start; }
.submission-card { padding: 24px; background: var(--aa-surface); border-radius: 14px; min-width: 0; }
.submission-heading { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: center; }
.submission-heading > span { font-size: 12px; color: var(--aa-text-2); }
.submission-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; margin: 20px 0; }
.file-tabs { display: flex; flex-wrap: wrap; gap: 4px; padding: 6px; background: var(--aa-bg); border-radius: 8px 8px 0 0; }
.file-tabs button { padding: 9px 13px; background: none; border: 1px solid transparent; border-radius: 6px; color: var(--aa-text-2); font-size: 12px; overflow-wrap: anywhere; max-width: 100%; }
.file-tabs button.active { background: var(--aa-surface); border-color: var(--aa-line); color: var(--aa-ink); }
.source-view :deep(.code-panel) { padding: 14px 0; font-size: 12px; border-radius: 0 0 8px 8px; max-height: 70vh; }
.no-code { background: var(--aa-bg); padding: 25px; border-radius: 10px; line-height: 1.8; font-size: 14px; }
.no-code p { color: var(--aa-text-2); margin-bottom: 0; }
.extra-detail { margin-top: 20px; border-top: 1px solid var(--aa-line-soft); padding-top: 17px; font-size: 12px; }
summary { cursor: pointer; font-weight: 500; }
.extra-detail ul { padding-left: 19px; line-height: 1.9; color: var(--aa-text-2); }
.extra-detail li { overflow-wrap: anywhere; }
.extra-detail li span { color: var(--aa-text-3); margin-left: 8px; }
.submission-text { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--aa-text-2); line-height: 1.8; }
.inspection-card { margin-top: 26px; padding-top: 24px; border-top: 1px solid var(--aa-line); }
.inspection-heading { display: flex; align-items: baseline; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.inspection-heading span { font-size: 11px; color: var(--aa-text-2); }
.inspection-summary { font-size: 13px; line-height: 1.8; }
.inspection-score { font-size: 13px; color: var(--aa-text-2); }
.inspection-score strong { color: var(--aa-ink); }
.inspection-deductions { padding-left: 19px; font-size: 12px; color: var(--aa-text-2); line-height: 1.8; }
.inspection-file { margin-top: 17px; padding: 15px; background: var(--aa-bg); border-radius: 9px; font-size: 12px; line-height: 1.8; }
.inspection-file > strong { display: block; margin-bottom: 10px; }
.inspection-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.inspection-tags > span { padding: 2px 8px; border-radius: 7px; background: var(--aa-surface); color: var(--aa-text-2); }
.inspection-tags > span.attention { color: var(--aa-danger); }
.inspection-file ul { padding-left: 20px; margin-bottom: 0; }
.inspection-caveat { font-size: 11px; color: var(--aa-text-2); line-height: 1.7; }
.question-risk-card { margin-top: 26px; padding-top: 24px; border-top: 1px solid var(--aa-line); }
.risk-unavailable { color: var(--aa-text-2); font-size: 12px; line-height: 1.8; }
.risk-level { font-size: 13px; font-weight: 600; }
.risk-level span { color: var(--aa-danger); }
.risk-signal { padding: 14px; margin: 12px 0; background: var(--aa-bg); border-radius: 8px; font-size: 12px; line-height: 1.8; }
.risk-signal code { display: block; overflow-wrap: anywhere; white-space: pre-wrap; margin-top: 7px; }
.risk-signal p { margin: 7px 0 0; }
.classroom-responses { margin-top: 26px; padding-top: 24px; border-top: 1px solid var(--aa-line); }
.no-answers { font-size: 13px; color: var(--aa-text-2); line-height: 1.8; }
.answer-attempt { margin-top: 18px; font-size: 12px; }
.answer-attempt > summary { line-height: 1.8; }
.review-answer { margin-top: 18px; padding-top: 15px; border-top: 1px solid var(--aa-line-soft); font-size: 13px; line-height: 1.9; }
.review-question { white-space: pre-wrap; overflow-wrap: anywhere; }
.student-answer { padding: 11px 14px; background: var(--aa-bg); border-radius: 8px; }
.student-answer > span { color: var(--aa-text-2); font-size: 11px; }
.student-answer p, .expected-answer p { white-space: pre-wrap; overflow-wrap: anywhere; margin: 5px 0 0; }
.expected-answer { margin-top: 11px; color: var(--aa-text-2); font-size: 12px; }
.grade-sidebar { position: sticky; top: 76px; min-width: 0; }
.grade-card { background: var(--aa-surface); border: 1px solid var(--aa-line-soft); padding: 24px; border-radius: 14px; }
.grade-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 21px; }
.grade-title > span { font-size: 12px; color: var(--aa-text-2); }
.grade-card label { display: block; font-size: 13px; font-weight: 500; margin: 17px 0 8px; }
.grade-card input, .grade-card textarea { width: 100%; }
.grade-card textarea { resize: vertical; line-height: 1.7; }
.score-input { position: relative; }
.score-input input { font-size: 28px; font-weight: 600; padding-right: 70px; }
.score-input > span { position: absolute; right: 23px; top: 23px; color: var(--aa-text-3); font-size: 13px; pointer-events: none; }
.optional { font-size: 11px; color: var(--aa-text-3); font-weight: 400; margin-left: 6px; }
.save-state { color: var(--aa-text-2); font-size: 11px; line-height: 1.6; margin: 15px 0; }
.save-button, .next-button { width: 100%; margin-top: 5px; }
.next-button { margin-top: 9px; }
.grade-note { font-size: 11px; color: var(--aa-text-3); line-height: 1.7; margin: 15px 0 0; }
.stale-notice { color: var(--aa-danger); font-size: 12px; line-height: 1.8; }
.grade-history { margin-top: 17px; padding: 20px; border: 1px solid var(--aa-line-soft); border-radius: 12px; font-size: 12px; }
.grade-history article { border-top: 1px solid var(--aa-line-soft); margin-top: 16px; padding-top: 16px; }
.grade-history article > div { display: flex; justify-content: space-between; gap: 12px; }
.grade-history p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
@media (max-width: 900px) { .grading-layout { grid-template-columns: minmax(0, 1fr); } .grade-sidebar { position: static; } }
@media (max-width: 600px) {
  .ta-page { padding: 25px 14px 40px; }
  .ta-heading { align-items: flex-start; flex-direction: column; gap: 12px; }
  h1 { font-size: 24px; }
  .stat-strip { grid-template-columns: repeat(2, 1fr); gap: 9px; }
  .stat-strip button { padding: 16px; }
  .roster-tools { flex-wrap: wrap; gap: 12px; }
  .search-field { max-width: none; min-width: 150px; }
  .export-button { margin-left: 0; }
  .pagination { align-items: flex-start; flex-direction: column; }
  .submission-card, .grade-card { padding: 19px; }
}
</style>
