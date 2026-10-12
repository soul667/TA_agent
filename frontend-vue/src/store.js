// 响应式凭据 store：localStorage 不是响应式数据源，登录/退出必须同步到这里，
// 顶栏（同一路由内登录）才能随之刷新。
import { reactive } from 'vue'

const CRED_KEY = 'aa_credentials'

function read() {
  try {
    return JSON.parse(localStorage.getItem(CRED_KEY) || 'null')
  } catch {
    return null
  }
}

export const authState = reactive({ credentials: read() })

// 功能开关：由 /api/meta 驱动（App.vue 启动时写入）
export const featureFlags = reactive({ accessCodeEnabled: false })

// 全局元信息：课程/作业名（App.vue 启动时写入），供登录页等展示上下文
export const metaInfo = reactive({ course: '', courseEn: '', title: '', titleEn: '', assignmentId: '' })

export function setCredentials(cred) {
  localStorage.setItem(CRED_KEY, JSON.stringify(cred))
  authState.credentials = cred
}

export function clearCredentials() {
  localStorage.removeItem(CRED_KEY)
  authState.credentials = null
}

export function getCredentials() {
  return authState.credentials ?? read()
}
