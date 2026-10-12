<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { preparePaper, fetchPaper, setCredentials, clearCredentials, ApiError } from '../api'
import { authState, metaInfo } from '../store'
import { pick, localizeBackendDetail } from '../i18n'
import CodePanel from '../components/CodePanel.vue'
import FormulaText from '../components/FormulaText.vue'
import SpotCheckAnswers from '../components/SpotCheckAnswers.vue'

const { locale } = useI18n()
const route = useRoute()
const router = useRouter()
const tr = (zh, en) => locale.value === 'en' ? en : zh
const studentId = ref('')
const teacherToken = ref('')
const isTeacher = computed(() => authState.credentials?.role === 'teacher')
const busy = ref(false)
const error = ref('')
const result = ref(null)
const showReference = ref(false)
const answering = ref(true)
const paper = computed(() => result.value?.paper)
const content = (obj, key) => pick(locale.value, obj, key)
const courseTitle = computed(() => locale.value === 'en'
  ? metaInfo.titleEn || metaInfo.title : metaInfo.title)
const LAST_STUDENT = 'aa_spot_check_student'
let requestId = 0
let pollTimer

function cancelWaiting() {
  requestId += 1
  clearTimeout(pollTimer)
  busy.value = false
}
function resetPaper() {
  cancelWaiting()
  result.value = null
  showReference.value = false
  answering.value = true
  error.value = ''
}
watch(() => authState.credentials, (credentials) => {
  if (credentials?.role !== 'teacher') {
    resetPaper()
    sessionStorage.removeItem(LAST_STUDENT)
  }
}, { flush: 'sync' })
watch(studentId, resetPaper)
onUnmounted(cancelWaiting)

function reportError(e, id) {
  if (id !== requestId) return
  busy.value = false
  if (e instanceof ApiError && [401, 403].includes(e.status)) {
    clearCredentials()
    teacherToken.value = ''
    error.value = tr('教师口令无效，请重新输入。', 'Please enter a valid teacher passphrase.')
  } else {
    error.value = localizeBackendDetail(e.detail || e.message, locale.value)
      || tr('暂时无法生成，请稍后重试。', 'Unable to generate the paper. Please retry.')
  }
}
function receive(data, sid, id) {
  if (id !== requestId) return
  if (data.status === 'ready') {
    result.value = data
    busy.value = false
  } else if (data.status === 'pending') {
    pollTimer = setTimeout(() => poll(sid, id), 1500)
  } else {
    busy.value = false
    error.value = data.error
      ? tr(`生成失败：${data.error}。请重试。`, `Generation failed: ${data.error}. Please retry.`)
      : tr('生成未完成，请重试。', 'Generation did not finish. Please retry.')
  }
}
async function poll(sid, id, restoring = false) {
  try { receive(await fetchPaper(sid), sid, id) }
  catch (e) {
    if (restoring && id === requestId && e instanceof ApiError && [404, 409].includes(e.status)) {
      // 切换作业后旧学号/旧答卷可能不存在；回到输入页，避免显示上一份作业的错误。
      sessionStorage.removeItem(LAST_STUDENT)
      studentId.value = ''
      resetPaper()
      return
    }
    reportError(e, id)
  }
}
async function generate() {
  if (busy.value) return
  const sid = studentId.value.trim()
  resetPaper()
  if (!/^[0-9]{4,20}$/.test(sid)) {
    error.value = tr('请输入 4–20 位数字学号。', 'Enter a student ID with 4–20 digits.')
    return
  }
  if (!isTeacher.value) {
    if (!teacherToken.value.trim()) {
      error.value = tr('首次使用请填写教师口令。', 'Enter the teacher passphrase to get started.')
      return
    }
    setCredentials({ role: 'teacher', token: teacherToken.value.trim() })
    teacherToken.value = ''
  }
  const id = ++requestId
  busy.value = true
  sessionStorage.setItem(LAST_STUDENT, sid)
  try { receive(await preparePaper(sid), sid, id) }
  catch (e) { reportError(e, id) }
}
onMounted(async () => {
  const selected = route.query.student_id
  if (typeof selected === 'string' && /^[0-9]{4,20}$/.test(selected)) {
    studentId.value = selected
    await router.replace({ path: route.path, query: {} })
    await nextTick()
    if (isTeacher.value) await generate()
    return
  }
  const sid = sessionStorage.getItem(LAST_STUDENT)
  if (!isTeacher.value || !sid || !/^[0-9]{4,20}$/.test(sid)) return
  studentId.value = sid
  await nextTick()
  const id = ++requestId
  busy.value = true
  // 刷新页面只恢复查询，不重复生成。
  await poll(sid, id, true)
})
function answerSubmitted(saved) {
  if (saved.questionnaire_id === paper.value?.questionnaire_id && saved.student_id === paper.value?.student_id) {
    result.value = { ...result.value, latest_answer: saved }
  }
}
function answerModeChanged(editing) {
  answering.value = editing
  if (editing) showReference.value = false
}
const printPaper = () => window.print()
</script>

<template>
  <div class="spot-check">
    <section class="intro">
      <p class="eyebrow">{{ tr('课堂抽查', 'IN-CLASS SPOT CHECK') }}</p>
      <h1>{{ tr('输入学号，生成专属答卷', 'One student ID. One personalized paper.') }}</h1>
      <p class="subtitle">{{ tr('根据这位学生的作业代码出题，在页面上选择、填写并提交答案。', 'Questions based on this student’s code. Select, write and submit your answers here.') }}</p>
    </section>
    <form class="lookup" @submit.prevent="generate">
      <label class="field-label" for="spot-student">{{ tr('学生学号', 'Student ID') }}</label>
      <div class="lookup-row">
        <input id="spot-student" v-model="studentId" class="student-input" inputmode="numeric"
          autocomplete="off" maxlength="20" :disabled="busy"
          :placeholder="tr('输入学生学号', 'Enter student ID')" required />
        <button class="generate-button" type="submit" :disabled="busy">
          {{ busy ? tr('正在生成…', 'Preparing…') : tr('生成答卷', 'Generate paper') }}
          <span v-if="!busy" aria-hidden="true">↗</span>
        </button>
      </div>
      <div v-if="!isTeacher" class="login-field">
        <label class="field-label" for="spot-token">{{ tr('教师口令', 'Teacher passphrase') }}</label>
        <input id="spot-token" v-model="teacherToken" type="password" autocomplete="current-password"
          :placeholder="tr('首次使用填写，登录后只需输入学号', 'Sign in once; then just enter student IDs')" required />
      </div>
      <p class="lookup-note">{{ courseTitle || tr('读取系统中已保存的学生作答', 'Uses student submissions already saved in the system') }}</p>
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
    </form>
    <div v-if="busy" class="progress" role="status" aria-live="polite">
      <span class="spinner" aria-hidden="true" />
      <div>
        <strong>{{ tr('正在准备这位学生的答卷', 'Preparing this student’s paper') }}</strong>
        <p>{{ tr('首次生成可能需要几分钟，可以保持页面打开。', 'The first generation may take a few minutes. You can leave this page open.') }}</p>
      </div>
      <button class="text-button" type="button" @click="cancelWaiting">{{ tr('换个学号', 'Another student') }}</button>
    </div>
    <template v-if="paper">
      <div class="paper-toolbar">
        <span class="ready-label">{{ tr('可直接作答', 'Ready to answer') }} · {{ paper.questions.length }} {{ tr('题', 'questions') }}</span>
        <button class="print-button" type="button" @click="printPaper">{{ tr('打印 / 保存 PDF', 'Print / Save PDF') }}</button>
      </div>
      <p v-if="paper.generated_by === 'mock'" class="mode-note" role="status">
        {{ tr('当前为演示模式：题目来自内置案例匹配。', 'Demo mode: questions are matched from built-in examples.') }}
        <span v-if="!paper.case_matched">{{ tr('此份代码未命中案例，当前为通用题，请核对后使用。', 'This code has no matching case. Review these generic questions before use.') }}</span>
      </p>
      <article class="paper-sheet" aria-label="个性化抽查答卷">
        <header class="paper-heading">
          <p class="paper-course">{{ content(paper, 'course') }}</p>
          <h2>{{ tr('个性化抽查答卷', 'Personalized spot-check paper') }}</h2>
          <p class="paper-title">{{ content(paper, 'title') }}</p>
          <div class="paper-meta">
            <strong>{{ tr('学号', 'Student ID') }} {{ paper.student_id }}</strong>
            <span>{{ tr('姓名', 'Name') }} ______________</span>
            <span>{{ tr('日期', 'Date') }} ______________</span>
          </div>
          <p class="paper-instruction"><span class="screen-instruction">{{ tr('请结合你提交的代码作答。点击选项、填写简答后，在页末提交答案。', 'Answer based on your submitted code. Select your choices, write short answers, then submit below.') }}</span><span class="print-instruction">{{ tr('请结合你提交的代码作答。选择题勾选选项，简答题写在下方空白处。', 'Mark your choices and write short answers in the spaces below.') }}</span></p>
        </header>
        <section class="submission-code">
          <h3>{{ tr('你的原始作答', 'Your submitted code') }}</h3>
          <p v-if="paper.source_files?.length" class="source-files">
            {{ paper.source_files.map(source => source.filename).join(' · ') }}
          </p>
          <CodePanel :code="paper.code" :language="paper.language" wrap />
        </section>
        <SpotCheckAnswers :key="paper.questionnaire_id" :paper="paper" :latest-answer="result.latest_answer"
          @submitted="answerSubmitted" @editing="answerModeChanged" @refresh="generate" />
        <footer class="paper-footer">TA Agent · {{ paper.student_id }} · {{ tr('第', 'Version') }} {{ paper.version }} {{ tr('版', '') }}</footer>
      </article>
      <section v-if="!answering && result.latest_answer" class="teacher-reference">
        <button class="reference-toggle" type="button" :aria-expanded="showReference" @click="showReference = !showReference">
          {{ showReference ? tr('收起教师参考', 'Hide teacher reference') : tr('查看教师参考', 'View teacher reference') }}
          <span>{{ tr('不随答卷打印', 'Excluded from printing') }}</span>
        </button>
        <div v-if="showReference" class="reference-content">
          <p v-for="note in result.teacher_reference.import_notes || []" :key="note" class="import-note">{{ note }}</p>
          <p><FormulaText :text="content(result.teacher_reference.diagnosis, 'summary')" /></p>
          <ol>
            <li v-for="answer in result.teacher_reference.answers" :key="answer.id"><FormulaText :text="answer.answer" /></li>
          </ol>
        </div>
      </section>
    </template>
    <footer class="secondary-nav">
      <router-link to="/ta">{{ tr('全部学号 / TA 评分', 'Student list / TA grading') }}</router-link>
      <span aria-hidden="true">·</span>
      <router-link to="/student">{{ tr('学生在线作答', 'Student access') }}</router-link>
      <span aria-hidden="true">·</span>
      <router-link to="/teacher/manage">{{ tr('管理记录', 'Manage records') }}</router-link>
    </footer>
  </div>
</template>

<style scoped>
.spot-check { max-width: 860px; margin: 0 auto; padding: 58px 22px 30px; }
.intro { margin-bottom: 28px; }
.eyebrow { color: var(--aa-primary); font-size: 12px; letter-spacing: .12em; font-weight: 600; margin: 0 0 14px; }
h1 { margin: 0; font-size: clamp(25px, 4vw, 34px); font-weight: 650; letter-spacing: -.035em; line-height: 1.3; }
.subtitle { margin: 13px 0 0; color: var(--aa-text-2); line-height: 1.7; font-size: 15px; }
.lookup { background: var(--aa-surface); border: 1px solid var(--aa-line-soft); padding: 25px; border-radius: 18px; }
.field-label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 9px; }
.lookup-row { display: flex; gap: 10px; }
.lookup input { min-width: 0; width: 100%; box-sizing: border-box; background: var(--aa-bg); color: var(--aa-ink); border: 1px solid var(--aa-line); border-radius: 9px; padding: 13px 14px; font: inherit; outline: none; }
.lookup input:focus { border-color: var(--aa-primary); box-shadow: 0 0 0 3px var(--aa-primary-soft); }
.student-input { flex: 1; font-variant-numeric: tabular-nums; }
button { font: inherit; cursor: pointer; }
button:focus-visible { outline: 2px solid var(--aa-primary); outline-offset: 3px; }
button:disabled { opacity: .6; cursor: wait; }
.generate-button, .print-button { display: inline-flex; align-items: center; justify-content: center; gap: 20px; border-radius: 9px; padding: 12px 22px; border: 0; white-space: nowrap; font-size: 14px; font-weight: 600; }
.generate-button { color: white; background: var(--aa-primary); }
.generate-button:hover:not(:disabled) { filter: brightness(.9); }
.login-field { margin-top: 20px; max-width: 400px; }
.lookup-note { margin: 13px 0 0; color: var(--aa-text-3); font-size: 12px; }
.error-message { color: var(--el-color-danger); font-size: 14px; margin: 16px 0 0; line-height: 1.7; overflow-wrap: anywhere; }
.progress { display: flex; align-items: center; gap: 16px; padding: 28px 4px; }
.progress strong { font-size: 14px; }
.progress p { color: var(--aa-text-2); font-size: 13px; margin: 7px 0 0; }
.spinner { width: 19px; height: 19px; flex-shrink: 0; border: 2px solid var(--aa-line); border-top-color: var(--aa-primary); border-radius: 50%; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.text-button { margin-left: auto; flex-shrink: 0; border: 0; background: transparent; color: var(--aa-text-2); font-size: 12px; padding: 8px; }
.paper-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 32px 0 13px; }
.ready-label { font-size: 13px; font-weight: 600; }
.print-button { background: var(--aa-primary-soft); color: var(--aa-primary); padding: 10px 15px; }
.mode-note { color: var(--aa-text-2); font-size: 12px; line-height: 1.6; margin: 0 0 14px; }
.paper-sheet { background: var(--aa-surface); border: 1px solid var(--aa-line-soft); border-radius: 14px; padding: 38px 42px; }
.paper-heading { border-bottom: 1px solid var(--aa-line); padding-bottom: 20px; }
.paper-course { color: var(--aa-text-2); font-size: 12px; margin: 0 0 8px; }
h2 { margin: 0; font-size: 25px; letter-spacing: -.02em; }
.paper-title { margin: 10px 0 22px; font-size: 14px; }
.paper-meta { display: flex; flex-wrap: wrap; gap: 16px 22px; font-size: 12px; }
.paper-instruction { color: var(--aa-text-2); font-size: 12px; line-height: 1.8; margin: 18px 0 0; }
.print-instruction { display: none; }
.submission-code { margin: 24px 0; }
.source-files { font-size: 12px; color: var(--aa-text-2); overflow-wrap: anywhere; margin: 0 0 12px; }
.import-note { color: var(--aa-text-2); font-size: 12px; }
h3 { font-size: 13px; margin: 0 0 12px; }
.submission-code :deep(.code-panel) { max-height: none; border: 1px solid var(--aa-line-soft); border-radius: 8px; padding: 12px 0; font-size: 12px; overflow: visible; }
.paper-question { margin-top: 25px; padding-top: 23px; border-top: 1px solid var(--aa-line-soft); break-inside: avoid; }
.question-heading { display: flex; align-items: center; gap: 10px; }
.question-number { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.question-type, .question-lines { color: var(--aa-text-2); font-size: 11px; }
.question-lines { margin-left: auto; }
.question-text { line-height: 1.9; font-size: 14px; white-space: pre-wrap; margin: 12px 0; overflow-wrap: anywhere; }
.variant-preview { background: var(--aa-bg); border-left: 2px solid var(--aa-line); padding: 12px 14px; font-size: 12px; margin: 12px 0; }
.variant-preview pre { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.paper-choices { list-style: none; padding: 0; margin: 12px 0 0; }
.paper-choices li { display: flex; align-items: baseline; gap: 8px; line-height: 1.8; margin: 9px 0; font-size: 13px; overflow-wrap: anywhere; }
.choice-box, .choice-letter { flex-shrink: 0; }
.answer-space { min-height: 110px; background: repeating-linear-gradient(transparent, transparent 35px, var(--aa-line-soft) 35px, var(--aa-line-soft) 36px); }
.paper-footer { color: var(--aa-text-3); font-size: 10px; text-align: center; margin-top: 35px; }
.teacher-reference { margin: 16px 0 0; border: 1px solid var(--aa-line-soft); border-radius: 10px; }
.reference-toggle { width: 100%; padding: 17px 20px; background: none; border: 0; color: var(--aa-ink); font-size: 13px; text-align: left; display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; }
.reference-toggle span { color: var(--aa-text-3); font-size: 12px; }
.reference-content { padding: 0 20px 18px; font-size: 13px; line-height: 1.9; }
.reference-content li { margin-top: 12px; white-space: pre-wrap; }
.secondary-nav { display: flex; justify-content: center; gap: 15px; margin-top: 32px; font-size: 12px; color: var(--aa-text-3); }
.secondary-nav a { color: inherit; text-decoration: none; }
.secondary-nav a:hover { color: var(--aa-primary); }
@media (max-width: 600px) {
  .spot-check { padding: 32px 6px 24px; }
  .lookup { padding: 19px; }
  .lookup-row { flex-direction: column; }
  .generate-button { padding: 14px; }
  .paper-sheet { padding: 25px 19px; }
  .progress { flex-wrap: wrap; }
  .question-lines { max-width: 45%; text-align: right; }
}
@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
@media print {
  .screen-instruction { display: none; }
  .print-instruction { display: inline; }
  .spot-check { max-width: none; padding: 0; }
  .spot-check > :not(.paper-sheet) { display: none !important; }
  .paper-sheet { --aa-ink: #000; --aa-text-2: #444; --aa-text-3: #666; --aa-line: #bbb; --aa-line-soft: #ddd; --aa-bg: #fff; --aa-surface: #fff; color: #000; border: 0; border-radius: 0; padding: 0; background: white; }
  .paper-heading { break-inside: avoid; }
  .submission-code :deep(.code-panel), .submission-code :deep(.code-line) { background: white !important; color: #000 !important; }
  .submission-code :deep(.no), .submission-code :deep([class*="hljs-"]) { color: #333 !important; }
  .submission-code :deep(.code-line) { break-inside: avoid; }
  .paper-question { margin-top: 16px; padding-top: 14px; }
  .question-text { line-height: 1.7; margin: 8px 0; font-size: 13px; }
  .paper-choices li { margin: 5px 0; line-height: 1.6; }
  .answer-space { min-height: 100px; background: none; border-bottom: 1px solid #ccc; }
}
</style>
