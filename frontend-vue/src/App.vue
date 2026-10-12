<script setup>
import { ref, computed, onMounted, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Sunny, Moon } from '@element-plus/icons-vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import { fetchMeta } from './api'
import { authState, clearCredentials, featureFlags, metaInfo } from './store'
import { getLang, setLang } from './i18n'
import { getTheme, toggleTheme, applyTheme } from './theme'

const router = useRouter()
const { t, locale } = useI18n()
const theme = ref(getTheme())
const loggedIn = computed(() => !!authState.credentials)
const elLocale = computed(() => (locale.value === 'en' ? en : zhCn))
const dark = computed(() => theme.value === 'dark')

onMounted(async () => {
  const saved = getLang()
  if (saved !== locale.value) locale.value = saved
  applyTheme(theme.value)
  try {
    const meta = await fetchMeta()
    featureFlags.accessCodeEnabled = !!meta?.access_code_enabled
    metaInfo.course = meta?.course || ''
    metaInfo.courseEn = meta?.course_en || ''
    metaInfo.title = meta?.title || ''
    metaInfo.titleEn = meta?.title_en || ''
    metaInfo.assignmentId = meta?.assignment_id || ''
  } catch {
    /* 元信息获取失败不影响使用 */
  }
})

watchEffect(() => {
  document.title = t('brand')
  document.documentElement.lang = locale.value === 'en' ? 'en' : 'zh-CN'
})

function toggleLang() {
  const next = locale.value === 'en' ? 'zh' : 'en'
  locale.value = next
  setLang(next)
}

function switchTheme() {
  theme.value = toggleTheme()
}

function logout() {
  clearCredentials()
  router.push('/')
}
</script>

<template>
  <el-config-provider :locale="elLocale">
    <el-container class="app-shell">
      <el-header class="app-header" height="52px">
        <div class="brand" @click="router.push('/')">{{ t('brand') }}</div>
        <div class="spacer" />
        <button
          class="header-btn"
          type="button"
          :title="dark ? t('themeLight') : t('themeDark')"
          :aria-label="dark ? t('themeLight') : t('themeDark')"
          @click="switchTheme"
        >
          <el-icon><component :is="dark ? Sunny : Moon" /></el-icon>
        </button>
        <button class="header-btn" type="button" @click="toggleLang">{{ t('langToggle') }}</button>
        <button v-if="loggedIn" class="header-btn" type="button" @click="logout">{{ t('logout') }}</button>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-config-provider>
</template>

<style scoped>
.app-shell { min-height: 100vh; }

.app-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  background: var(--aa-glass-strong);
  backdrop-filter: blur(24px) saturate(1.6);
  -webkit-backdrop-filter: blur(24px) saturate(1.6);
  border-bottom: 1px solid var(--aa-line-soft);
  position: sticky;
  top: 0;
  z-index: 40;
}

.brand {
  font-weight: 600;
  font-size: 15px;
  letter-spacing: -0.015em;
  color: var(--aa-ink);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.brand:hover { color: var(--aa-primary); }

.spacer { flex: 1; }

.header-btn {
  flex: 0 0 auto;
  appearance: none;
  background: none;
  border: none;
  padding: 4px 6px;
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.4;
  color: var(--aa-text-2);
  cursor: pointer;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
}
.header-btn:hover { color: var(--aa-ink); background: var(--aa-hover); }

@media (max-width: 767px) {
  .app-header { padding: 0 14px; gap: 6px; }
  .brand { font-size: 14px; }
}
</style>

<style>
@media print {
  @page { size: A4; margin: 16mm; }
  html, body, #app, .app-shell, .el-main { background: white !important; color: black; min-height: 0 !important; }
  .app-header { display: none !important; }
  .el-main { padding: 0 !important; overflow: visible !important; }
}
</style>
