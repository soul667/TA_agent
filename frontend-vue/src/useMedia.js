// 响应式媒体查询：用于按断点切换渲染结构（表格/卡片、桌面/移动操作栏）。
// 仅依赖 matchMedia，无需引入额外依赖。
import { ref, onBeforeUnmount } from 'vue'

export function useMediaQuery(query) {
  const list = window.matchMedia(query)
  const matches = ref(list.matches)
  const handler = (e) => { matches.value = e.matches }
  list.addEventListener('change', handler)
  onBeforeUnmount(() => list.removeEventListener('change', handler))
  return matches
}

export const MOBILE_QUERY = '(max-width: 767px)'

export const useIsMobile = () => useMediaQuery(MOBILE_QUERY)
