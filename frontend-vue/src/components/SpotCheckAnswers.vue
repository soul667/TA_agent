<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { submitClassroomAnswers, ApiError } from '../api'
import { pick } from '../i18n'
import FormulaText from './FormulaText.vue'

const props = defineProps({ paper: { type: Object, required: true }, latestAnswer: { type: Object, default: null } })
const emit = defineEmits(['submitted', 'editing', 'refresh'])
const { locale } = useI18n()
const tr = (zh, en) => locale.value === 'en' ? en : zh
const answers = reactive({})
const receipt = ref(props.latestAnswer)
const editing = ref(!props.latestAnswer)
const submitting = ref(false)
const error = ref('')
const conflict = ref(false)
const draftNote = ref('')
const draftKey = `aa_classroom_draft_${props.paper.assignment_id}_${props.paper.student_id}_${props.paper.questionnaire_id}`
const newToken = () => globalThis.crypto?.randomUUID?.() || `classroom-${Date.now()}-${Math.random().toString(36).slice(2)}`
let token = newToken()
let submittedSignature = ''
let hydrating = true
let alive = true

const letter = (index) => String.fromCharCode(65 + index)
function answerValue(q, value) {
  const options = (q.choices || []).map((_, index) => letter(index))
  if (q.type === 'multiple_choice') return Array.isArray(value) ? value.filter(v => options.includes(v)) : []
  if (q.type === 'text') return typeof value === 'string' ? value : ''
  return options.includes(value) ? value : ''
}
function fill(source) {
  hydrating = true
  for (const q of props.paper.questions) answers[q.id] = answerValue(q, source?.[q.id])
  hydrating = false
}
const answeredCount = computed(() => props.paper.questions.filter(q => {
  const value = answers[q.id]
  return Array.isArray(value) ? value.length > 0 : typeof value === 'string' && value.trim() !== ''
}).length)
const missing = computed(() => props.paper.questions.length - answeredCount.value)

function persistDraft() {
  if (hydrating || !editing.value) return
  const signature = JSON.stringify(answers)
  if (submittedSignature && signature !== submittedSignature) { token = newToken(); submittedSignature = '' }
  try {
    localStorage.setItem(draftKey, JSON.stringify({
      answers: { ...answers }, token, submittedSignature,
      base_attempt_id: receipt.value?.attempt_id || '',
    }))
    draftNote.value = tr('草稿已自动保存在此浏览器', 'Draft saved in this browser')
  } catch {
    draftNote.value = tr('浏览器无法保存草稿，请在离开前提交答案', 'Draft storage unavailable. Submit before leaving.')
  }
}
watch(answers, persistDraft, { deep: true, flush: 'sync' })
watch(editing, value => emit('editing', value), { immediate: true })
onMounted(() => {
  fill(receipt.value?.answers)
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey) || 'null')
    if (draft?.token === receipt.value?.submission_token) {
      localStorage.removeItem(draftKey)
    } else if (draft?.answers && draft.base_attempt_id === (receipt.value?.attempt_id || '')) {
      editing.value = true
      token = draft.token || newToken()
      submittedSignature = draft.submittedSignature || ''
      fill(draft.answers)
      draftNote.value = tr('已恢复上次未提交的草稿', 'Your unsubmitted draft has been restored')
    } else if (draft?.answers) {
      draftNote.value = tr('已有更新的提交，当前显示服务器保存的答案；旧草稿仍保留在此浏览器', 'A newer submission exists. Showing saved answers; the old draft remains in this browser.')
    }
  } catch { /* 无可读草稿时沿用服务器记录或空白作答 */ }
})
onUnmounted(() => { alive = false })

async function submit() {
  if (submitting.value || !editing.value) return
  error.value = ''
  if (!answeredCount.value) { error.value = tr('请至少作答一道题。', 'Answer at least one question.'); return }
  if (missing.value && !window.confirm(tr(`还有 ${missing.value} 题未作答，仍然提交吗？`, `${missing.value} questions are unanswered. Submit anyway?`))) return
  submittedSignature = JSON.stringify(answers)
  persistDraft()
  const payload = { answers: JSON.parse(JSON.stringify(answers)),
    questionnaire_id: props.paper.questionnaire_id, questionnaire_version: props.paper.version,
    submission_token: token }
  submitting.value = true
  conflict.value = false
  try {
    const saved = await submitClassroomAnswers(props.paper.student_id, payload)
    try {
      if (JSON.parse(localStorage.getItem(draftKey) || 'null')?.token === payload.submission_token) {
        localStorage.removeItem(draftKey)
      }
    } catch { /* 服务器记录已保存；不影响其他页面中更新后的草稿 */ }
    if (!alive) return
    receipt.value = saved
    editing.value = false
    draftNote.value = ''
    emit('submitted', saved)
  } catch (e) {
    if (!alive) return
    error.value = e.detail || e.message || tr('提交失败，请重试；草稿已保留。', 'Submission failed. Your draft is kept; please retry.')
    conflict.value = e instanceof ApiError && e.status === 409
  } finally { if (alive) submitting.value = false }
}
function editAgain() {
  token = newToken()
  submittedSignature = ''
  editing.value = true
  error.value = ''
  persistDraft()
}
function questionText(q) {
  return (pick(locale.value, q, 'question') || '')
    .replace(/（点击本题可在左侧预览修改后的代码）/g, '')
    .replace(/\s*\(Click this question to preview the modified code on the left\)/g, '')
}
function choiceText(q, i) { return locale.value === 'en' && q.choices_en?.[i] ? q.choices_en[i] : q.choices[i] }
function typeText(q) {
  return { single_choice: tr('单选', 'Single choice'), multiple_choice: tr('多选', 'Multiple choice'),
    code_variant: tr('代码变式 · 单选', 'Code variant · Single choice'), text: tr('简答', 'Short answer') }[q.type]
}
const savedAt = computed(() => receipt.value ? new Date(receipt.value.submitted_at).toLocaleString(locale.value === 'en' ? 'en-US' : 'zh-CN', { hour12: false }) : '')
</script>

<template>
  <form class="interactive-answers" @submit.prevent="submit">
    <div class="answer-progress" aria-live="polite">
      <strong>{{ editing ? tr('现场作答', 'Answer here') : tr('答案已提交', 'Answers submitted') }}</strong>
      <span>{{ tr(`已作答 ${answeredCount} / ${paper.questions.length} 题`, `${answeredCount} / ${paper.questions.length} answered`) }}</span>
    </div>
    <fieldset :disabled="submitting || !editing" class="answer-fields">
      <section v-for="(q, index) in paper.questions" :key="q.id" class="paper-question" :data-question-id="q.id">
        <div class="question-heading"><span class="question-number">{{ String(index + 1).padStart(2, '0') }}</span><span class="question-type">{{ typeText(q) }}</span><span v-if="q.code_lines?.length" class="question-lines">{{ tr('代码第', 'Code lines') }} {{ q.code_lines.join(', ') }} {{ tr('行', '') }}</span></div>
        <p :id="`question-${q.id}`" class="question-text"><FormulaText :text="questionText(q)" /></p>
        <div v-if="q.variant" class="variant-preview"><span>{{ tr('假设第', 'Suppose line') }} {{ q.variant.line_no }} {{ tr('行替换为：', 'is replaced with:') }}</span><pre>{{ q.variant.replacement }}</pre></div>
        <div v-if="q.choices?.length" class="answer-choices" :role="q.type === 'multiple_choice' ? 'group' : 'radiogroup'" :aria-labelledby="`question-${q.id}`">
          <label v-for="(_, i) in q.choices" :key="i" class="answer-choice" :class="{ chosen: Array.isArray(answers[q.id]) ? answers[q.id].includes(letter(i)) : answers[q.id] === letter(i) }">
            <input v-if="q.type === 'multiple_choice'" v-model="answers[q.id]" type="checkbox" :name="q.id" :value="letter(i)" :aria-label="`${index + 1}. ${letter(i)}. ${choiceText(q, i)}`" />
            <input v-else v-model="answers[q.id]" type="radio" :name="q.id" :value="letter(i)" :aria-label="`${index + 1}. ${letter(i)}. ${choiceText(q, i)}`" />
            <span class="print-choice-box" aria-hidden="true">□</span><span class="choice-letter">{{ letter(i) }}.</span><FormulaText :text="choiceText(q, i)" />
          </label>
        </div>
        <template v-else>
          <textarea v-model="answers[q.id]" class="text-answer" :aria-label="tr(`第 ${index + 1} 题答案`, `Answer to question ${index + 1}`)" :aria-describedby="`question-${q.id}`" rows="4" maxlength="8000" :placeholder="tr('在这里填写你的回答…', 'Write your answer here…')" />
          <div class="print-answer-space" />
        </template>
      </section>
    </fieldset>
    <div class="answer-actions">
      <p v-if="error" class="answer-error" role="alert">{{ error }} <button v-if="conflict" type="button" class="text-button" @click="emit('refresh')">{{ tr('重新载入答卷', 'Reload paper') }}</button></p>
      <template v-if="editing">
        <p class="draft-note" role="status">{{ draftNote || tr('答案提交后，TA 可在评分页查看并评分。', 'After submission, your TA can review and grade your answers.') }}</p>
        <button class="submit-answers" type="submit" :disabled="submitting">{{ submitting ? tr('正在提交…', 'Submitting…') : tr('提交答案', 'Submit answers') }}</button>
      </template>
      <div v-else class="submission-receipt" role="status">
        <strong>{{ tr('提交成功，TA 可查看并评分', 'Submitted. Your TA can review and grade these answers.') }}</strong>
        <p>{{ paper.student_id }} · {{ savedAt }}</p>
        <div class="receipt-actions"><router-link :to="`/ta/${paper.student_id}`">{{ tr('进入 TA 评分 →', 'Open TA grading →') }}</router-link><button class="text-button" type="button" @click="editAgain">{{ tr('修改并重新提交', 'Edit and resubmit') }}</button></div>
      </div>
    </div>
  </form>
</template>

<style scoped>
.answer-progress { display: flex; justify-content: space-between; gap: 14px; padding: 14px 0 0; font-size: 13px; }
.answer-progress span { color: var(--aa-text-2); }
.answer-fields { padding: 0; margin: 0; border: 0; min-width: 0; }
.paper-question { margin-top: 25px; padding-top: 23px; border-top: 1px solid var(--aa-line-soft); break-inside: avoid; }
.question-heading { display: flex; align-items: center; gap: 10px; }
.question-number { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
.question-type, .question-lines { color: var(--aa-text-2); font-size: 11px; }
.question-lines { margin-left: auto; }
.question-text { line-height: 1.9; font-size: 14px; white-space: pre-wrap; margin: 12px 0; overflow-wrap: anywhere; }
.variant-preview { background: var(--aa-bg); border-left: 2px solid var(--aa-line); padding: 12px 14px; font-size: 12px; margin: 12px 0; }
.variant-preview pre { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.answer-choices { display: grid; gap: 8px; margin-top: 14px; }
.answer-choice { display: flex; align-items: baseline; gap: 9px; padding: 12px 14px; border: 1px solid var(--aa-line); border-radius: 9px; cursor: pointer; line-height: 1.8; font-size: 13px; overflow-wrap: anywhere; }
.answer-choice:hover { background: var(--aa-hover); }
.answer-choice.chosen { border-color: var(--aa-primary); background: var(--aa-primary-soft); }
.answer-choice input { accent-color: var(--aa-primary); flex-shrink: 0; margin: 0; }
.choice-letter { flex-shrink: 0; }
.text-answer { display: block; width: 100%; box-sizing: border-box; border: 1px solid var(--aa-line); border-radius: 9px; padding: 13px; resize: vertical; font: inherit; font-size: 14px; line-height: 1.8; background: var(--aa-bg); color: var(--aa-ink); }
.text-answer:focus { outline: 2px solid var(--aa-primary-soft); border-color: var(--aa-primary); }
.text-answer:disabled { opacity: 1; color: var(--aa-ink); }
.answer-actions { margin-top: 28px; border-top: 1px solid var(--aa-line); padding-top: 18px; }
.draft-note { color: var(--aa-text-2); font-size: 12px; line-height: 1.7; }
.submit-answers { border: 0; background: var(--aa-primary); color: white; padding: 13px 27px; border-radius: 9px; font: inherit; font-weight: 600; cursor: pointer; }
.submit-answers:disabled { opacity: .6; cursor: wait; }
.answer-error { color: var(--aa-danger); font-size: 13px; line-height: 1.7; }
.text-button { padding: 6px 0; color: var(--aa-text-2); background: none; border: 0; font: inherit; font-size: 12px; cursor: pointer; }
.submission-receipt { font-size: 14px; line-height: 1.8; }
.submission-receipt p { font-size: 12px; color: var(--aa-text-2); }
.receipt-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 25px; font-size: 13px; }
.print-choice-box, .print-answer-space { display: none; }
@media (max-width: 600px) { .question-lines { max-width: 45%; text-align: right; } .answer-choice { padding: 11px; } .submit-answers { width: 100%; } }
@media print {
  .answer-progress, .answer-actions, .text-answer, .answer-choice input { display: none !important; }
  .print-choice-box { display: inline; }
  .print-answer-space { display: block; min-height: 100px; border-bottom: 1px solid #ccc; }
  .paper-question { margin-top: 16px; padding-top: 14px; }
  .question-text { line-height: 1.7; margin: 8px 0; font-size: 13px; }
  .answer-choices { display: block; }
  .answer-choice, .answer-choice.chosen { padding: 0; margin: 5px 0; line-height: 1.6; border: 0; background: none; color: black; }
}
</style>
