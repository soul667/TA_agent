<script setup>
// 题干富文本：把「代码」与「数学」两类token分开渲染。
// - `` `code` ``：等宽字体（无底色、不染色，仅字形区分）
// - `$...$`：内容像代码表达式（含运算符/括号/下标）或命中代码标识符 → 等宽；
//   否则（单个变量、纯符号）→ 衬线斜体（数学排版惯例）
// - 纯文本段落里出现的、本题代码中存在的标识符（如 arr / n / i / max）自动套等宽，
//   这样即使后端题面没有标记反引号，行内代码也不会以普通正文字体出现。
import { computed } from 'vue'

const props = defineProps({
  text: { type: String, default: '' },
  codeTokens: { type: Array, default: () => [] },
})

const tokenSet = computed(() => new Set(props.codeTokens))

// 代码表达式特征：运算符、括号、下标、分号等
const CODE_EXPR_RE = /[[\]{}()<>;=+*/%!&|,^~]|-{1,2}|_{2}/

function isCodeLike(raw) {
  const v = String(raw).trim()
  if (!v) return false
  if (tokenSet.value.has(v)) return true
  return CODE_EXPR_RE.test(v)
}

// 纯文本中命中代码标识符的词（按整词匹配，避免 maximum 命中 max）
function splitPlain(text) {
  if (!tokenSet.value.size) return [{ t: 'plain', v: text }]
  const out = []
  const re = /[A-Za-z_][A-Za-z0-9_]*/g
  let last = 0
  let m
  while ((m = re.exec(text)) !== null) {
    const w = m[0]
    if (!tokenSet.value.has(w)) continue
    if (m.index > last) out.push({ t: 'plain', v: text.slice(last, m.index) })
    out.push({ t: 'code', v: w })
    last = m.index + w.length
  }
  if (!out.length) return [{ t: 'plain', v: text }]
  if (last < text.length) out.push({ t: 'plain', v: text.slice(last) })
  return out
}

const parts = computed(() => {
  const s = props.text || ''
  const out = []
  const re = /`([^`]+)`|\$([^$]+)\$/g
  let last = 0
  let m
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) out.push(...splitPlain(s.slice(last, m.index)))
    if (m[1] !== undefined) out.push({ t: 'code', v: m[1] })
    else out.push({ t: isCodeLike(m[2]) ? 'code' : 'math', v: m[2] })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push(...splitPlain(s.slice(last)))
  return out
})
</script>

<template>
  <span class="ftext">
    <template v-for="(p, i) in parts" :key="i">
      <code v-if="p.t === 'code'" class="ft-code">{{ p.v }}</code>
      <span v-else-if="p.t === 'math'" class="ft-math">{{ p.v }}</span>
      <template v-else>{{ p.v }}</template>
    </template>
  </span>
</template>

<style scoped>
/* 行内代码：等宽 + 同色（不染色、不加底色），靠字形与正文区分 */
.ft-code {
  font-family: var(--font-mono);
  font-size: 0.92em;
  color: inherit;
  background: transparent;
  border: none;
  padding: 0;
  margin: 0 0.5px;
  letter-spacing: -0.01em;
}
/* 数学：仅用于真正的符号/变量（$n$ 这类），保留衬线斜体的学术排版 */
.ft-math {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 1.02em;
  letter-spacing: 0.01em;
  color: inherit;
  white-space: nowrap;
}
</style>
