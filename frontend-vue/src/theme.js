// 主题（浅色 / 深色）：偏好持久化在 localStorage('aa_theme')；
// 未设置时跟随系统 prefers-color-scheme。深色通过 html.dark 类驱动
// Element Plus 的 dark css-vars 与本地设计令牌。
const THEME_KEY = 'aa_theme'

export function getTheme() {
  const saved = localStorage.getItem(THEME_KEY)
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ? 'dark' : 'light'
}

export function setTheme(theme) {
  localStorage.setItem(THEME_KEY, theme === 'dark' ? 'dark' : 'light')
}

export function applyTheme(theme) {
  const dark = theme === 'dark'
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', dark ? '#101220' : '#eef1f8')
}

export function toggleTheme() {
  const next = getTheme() === 'dark' ? 'light' : 'dark'
  setTheme(next)
  applyTheme(next)
  return next
}
