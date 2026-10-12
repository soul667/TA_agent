<script setup>
// 代码面板：highlight.js 语法着色（单色相墨阶，见 style.css）+ 行号 + 题目联动（referenced/active）
// + 变异题的“假设修改”预览（mutation = {line_no, replacement}，replacement 可含换行）。
// 不引入任何 highlight.js 彩色主题（atom-one-dark 等），语法层级只靠字重/斜体/明度。
import { computed, ref } from 'vue'
import hljs from 'highlight.js/lib/core'
import c from 'highlight.js/lib/languages/c'
import { useI18n } from 'vue-i18n'

hljs.registerLanguage('c', c)

const props = defineProps({
  code: { type: String, default: '' },
  language: { type: String, default: 'c' },
  referenced: { type: Array, default: () => [] },   // 题目标注的行号
  active: { type: Array, default: () => [] },       // 当前聚焦题目的行号
  mutation: { type: Object, default: null },        // {line_no, replacement}
  wrap: { type: Boolean, default: false },          // 自动换行：长行折行而非横向滚动
})

const { t } = useI18n()
const rootEl = ref(null)

// 供父组件定位题目对应行：滚动面板内可视元素（面板自身 overflow:auto）
function scrollToLine(no) {
  const el = rootEl.value?.querySelector(`[data-ln="${no}"]`)
  el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}
defineExpose({ scrollToLine })

const referencedSet = computed(() => new Set(props.referenced))
const activeSet = computed(() => new Set(props.active))

const lines = computed(() => {
  const out = []
  for (const [i, raw] of (props.code || '').split('\n').entries()) {
    const no = i + 1
    if (props.mutation && props.mutation.line_no === no) {
      const parts = String(props.mutation.replacement).split('\n')
      parts.forEach((repl, j) => {
        out.push({
          no, text: repl || ' ', html: highlight(repl || ' '),
          referenced: referencedSet.value.has(no),
          active: activeSet.value.has(no),
          mutated: j === 0,
          mutatedCont: j > 0,
        })
      })
      out.push({
        no, text: '', html: '', ghost: true,
        ghostText: raw.trim() || ' ', referenced: false, active: false,
      })
    } else {
      out.push({
        no, text: raw || ' ', html: highlight(raw || ' '),
        referenced: referencedSet.value.has(no),
        active: activeSet.value.has(no),
      })
    }
  }
  return out
})

function highlight(text) {
  try {
    return hljs.highlight(text, { language: props.language }).value
  } catch {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  }
}
</script>

<template>
  <div ref="rootEl" class="code-panel" :class="{ 'is-wrap': wrap }">
    <div
      v-for="line in lines"
      :key="`${line.no}-${line.text}`"
      :data-ln="line.no"
      class="code-line"
      :class="{
        referenced: line.referenced,
        active: line.active,
        mutated: line.mutated,
        'mutated-cont': line.mutatedCont,
        ghost: line.ghost,
      }"
    >
      <span class="no">{{ line.no }}</span>
      <span v-if="line.ghost" class="txt">{{ line.ghostText }}</span>
      <!-- eslint-disable-next-line vue/no-v-html —— 内容来自 hljs 输出与后端保存的学生代码 -->
      <span v-else class="txt" v-html="line.html" />
      <span v-if="line.mutated" class="mut-badge">{{ t('variantNew') }}</span>
      <span v-else-if="line.ghost" class="ghost-badge">{{ t('variantOriginal') }}</span>
      <span v-else-if="line.referenced" class="ref-dot" />
    </div>
  </div>
</template>

<style scoped>
/* 语法高亮为单色相墨阶（全局 .code-panel .hljs-* 定义），此处只处理行状态与徽标 */

/* 变异题：改行用实心竖条（全局），原行用虚线竖条 + 删除线 */
.code-line.ghost .txt {
  text-decoration: line-through;
  opacity: 0.55;
}
.mut-badge,
.ghost-badge {
  margin-left: auto;
  margin-right: 4px;
  font-size: 11px;
  font-family: var(--font-sans);
  font-weight: 500;
}
.mut-badge { color: var(--aa-ink-2); font-weight: 600; }
.ghost-badge { color: var(--aa-ink-3); text-decoration: line-through; }
.ref-dot {
  margin-left: auto;
  margin-right: 8px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--code-bar-ref);
}

/* 移动端：字号与行号宽度收紧，代码面板横向滑动更省空间 */
@media (max-width: 767px) {
  .code-panel { font-size: 12.5px; line-height: 1.6; border-radius: 8px; }
  .code-line .no { flex-basis: 2.6em; padding-right: 9px; }
  .code-line .txt { padding-right: 10px; }
  .mut-badge, .ghost-badge { font-size: 10.5px; }
}
</style>
