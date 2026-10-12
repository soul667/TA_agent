<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { fetchExam, setCredentials, clearCredentials, ApiError } from '../api'
import { localizeBackendDetail } from '../i18n'
import { featureFlags, metaInfo } from '../store'
import { useIsMobile } from '../useMedia'

const router = useRouter()
const route = useRoute()
const { t, locale } = useI18n()
const isMobile = useIsMobile()
const sid = ref('')
const token = ref('')
const loading = ref(false)
const sidInput = ref(null)
const sidError = ref('')     // 学号格式的行内校验
const loginError = ref('')   // 登录失败的持久行内错误（404 / 401 / 网络）
const submitted = ref(null)  // 已提交过的分流信息 { attempts }

// 课程上下文（来自 /api/meta，EN 界面优先用 _en 字段）
const contextLine = computed(() => {
  const course = locale.value === 'en' && metaInfo.courseEn ? metaInfo.courseEn : metaInfo.course
  const quiz = locale.value === 'en' && metaInfo.titleEn ? metaInfo.titleEn : metaInfo.title
  return [course, quiz].filter(Boolean).join(' · ')
})

function backendMsg(e, fallbackKey) {
  // 后端错误 detail 为中文：EN 界面下按映射表英译
  const localized = localizeBackendDetail(e.detail, locale.value)
  return localized || e.message || t(fallbackKey)
}

function validateSid() {
  if (!/^\d{4,20}$/.test(sid.value.trim())) {
    sidError.value = t('badSid')
    return false
  }
  sidError.value = ''
  return true
}

// 出错后随输入实时消除，避免“已经改对了还标红”；
// 用 watch(v-model) 而非 @input：不依赖组件事件透传，v-model 更新必触发
watch(sid, () => {
  if (sidError.value) validateSid()
  if (loginError.value) loginError.value = ''
})

async function login() {
  const id = sid.value.trim()
  if (!validateSid()) return
  // 访问码功能开关（featureFlags 由 /api/meta 驱动）：关闭时仅凭学号进入
  let code = ''
  if (featureFlags.accessCodeEnabled) {
    code = token.value.trim()
    if (!code) {
      ElMessage.warning(t('needCode'))
      return
    }
  }
  loading.value = true
  loginError.value = ''
  try {
    setCredentials({ role: 'student', sid: id, token: code })
    const exam = await fetchExam(id) // 验证学号与问卷状态
    if (route.query.redirect) {
      router.push(String(route.query.redirect))
      return
    }
    if (exam?.has_submitted) {
      // 已提交过：卡片内分流，返回访问的学生常见意图是查看反馈
      submitted.value = { attempts: exam.attempt_count || 1 }
      return
    }
    ElMessage.success(t('loginOk'))
    router.push('/student/exam')
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      clearCredentials() // 凭据无效：清掉，避免顶栏残留登录态
    }
    loginError.value = backendMsg(e, 'needCode')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  // 移动端不自动聚焦，避免一进页面就弹出键盘
  if (!isMobile.value) sidInput.value?.focus?.()
})
</script>

<template>
  <div class="login-wrap">
    <div class="login-col">
      <button class="back" type="button" @click="router.push('/')">← {{ t('home') }}</button>

      <div class="login-card">
        <p v-if="contextLine" class="context">{{ contextLine }}</p>
        <h1 class="page-title">{{ t('studentLogin') }}</h1>
        <p class="page-sub">{{ featureFlags.accessCodeEnabled ? t('loginNote') : t('loginNoteNoCode') }}</p>

        <el-form v-if="!submitted" label-position="top" size="large" @submit.prevent="login">
          <el-form-item :label="t('studentId')">
            <el-input
              ref="sidInput"
              v-model="sid"
              :placeholder="t('studentIdPh')"
              inputmode="numeric"
              autocomplete="username"
              @keyup.enter="login"
              @blur="sid.trim() && validateSid()"
            />
            <!-- 行内错误自主渲染：不依赖 el-form-item 的 error prop（其显示经过防抖，清除不跟手） -->
            <p v-if="sidError" class="field-error" aria-live="polite">{{ sidError }}</p>
          </el-form-item>
          <el-form-item v-if="featureFlags.accessCodeEnabled" :label="t('accessCode')">
            <el-input
              v-model="token"
              :placeholder="t('accessCodePh')"
              show-password
              autocomplete="current-password"
              @keyup.enter="login"
            />
          </el-form-item>
          <p v-if="loginError" class="form-error">{{ loginError }}</p>
          <el-button type="primary" size="large" :loading="loading" class="login-btn" @click="login">
            {{ t('enterExam') }}
          </el-button>
        </el-form>

        <!-- 已提交过：查看结果 / 继续作答 分流 -->
        <div v-else class="after-submit">
          <p class="after-title st-strong st-dot">{{ t('submittedBefore', { n: submitted.attempts }) }}</p>
          <p class="after-sub">{{ t('submittedHint') }}</p>
          <div class="after-actions">
            <el-button type="primary" size="large" @click="router.push('/student/result')">
              {{ t('viewMyResult') }}
            </el-button>
            <el-button size="large" @click="router.push('/student/exam')">
              {{ t('continueAnswer', { n: submitted.attempts + 1 }) }}
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 桌面端在视口内垂直居中（扣除吸顶栏），移动端保持顶部开始 */
.login-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: calc(100vh - var(--aa-header-h));
  padding: 24px 20px 48px;
}
.login-col {
  width: 100%;
  max-width: 440px;
}

.back {
  appearance: none;
  background: none;
  border: none;
  padding: 0;
  margin-bottom: 14px;
  font-family: var(--font-sans);
  font-size: 13px;
  color: var(--aa-text-2);
  cursor: pointer;
}
.back:hover { color: var(--aa-primary); }

/* 平面纯色卡片：灰底上的 surface 色块即焦点，无边框无阴影 */
.login-card {
  background: var(--aa-surface);
  border-radius: var(--aa-radius-xl);
  padding: 30px 32px 32px;
}
.context {
  margin: 0 0 6px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--aa-text-3);
}
.login-card .page-title { margin: 0 0 6px; }
.login-card .page-sub { margin-bottom: 22px; }

.field-error {
  width: 100%;
  margin: 6px 0 0;
  color: var(--aa-danger);
  font-size: 12.5px;
  line-height: 1.6;
}
.form-error {
  margin: 2px 0 10px;
  color: var(--aa-danger);
  font-size: 12.5px;
  line-height: 1.6;
}
.login-btn { width: 100%; margin-top: 4px; }

/* 已提交分流面板 */
.after-submit {
  border-top: 1px solid var(--aa-line-soft);
  padding-top: 18px;
}
.after-title { margin: 0; font-size: 14px; }
.after-sub {
  margin: 6px 0 18px;
  color: var(--aa-text-2);
  font-size: 12.5px;
  line-height: 1.6;
}
.after-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.after-actions .el-button { flex: 1; min-width: 0; }
.after-actions .el-button + .el-button { margin-left: 0; }

@media (max-width: 767px) {
  .login-wrap {
    align-items: flex-start;
    min-height: 0;
    padding: 20px 16px 48px;
  }
  .login-card { padding: 22px 20px 24px; border-radius: var(--aa-radius-lg); }
  .after-actions { flex-direction: column; }
  .after-actions .el-button { flex: 0 0 auto; }
}
</style>
