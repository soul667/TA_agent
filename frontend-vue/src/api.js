// 统一 API 封装：凭据注入、错误规范化、401 引导登录
// 凭据的读写统一走 ./store（响应式），顶栏登录态随之刷新。
import { getCredentials, setCredentials, clearCredentials } from './store'

export { getCredentials, setCredentials, clearCredentials }

export function authHeader() {
  const cred = getCredentials()
  if (cred && cred.token) return { Authorization: `Bearer ${cred.token}` }
  return {}
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `请求失败 (${status})`)
    this.status = status
    this.detail = detail || ''
  }
}

async function parse(res) {
  let body = {}
  try {
    body = await res.json()
  } catch {
    /* 空响应体 */
  }
  if (!res.ok) {
    const detail = typeof body.detail === 'string'
      ? body.detail
      : body.detail ? JSON.stringify(body.detail) : ''
    throw new ApiError(res.status, detail)
  }
  return body
}

export async function apiGet(path) {
  return parse(await fetch(path, { headers: authHeader() }))
}

export async function apiPost(path, body) {
  return parse(await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeader() },
    body: body === undefined ? undefined : JSON.stringify(body),
  }))
}

// ---- 业务接口 ----

export const fetchMeta = () => apiGet('/api/meta')
export const fetchExam = (sid) => apiGet(`/api/exam/${encodeURIComponent(sid)}`)
export const submitExam = (sid, payload) =>
  apiPost(`/api/exam/${encodeURIComponent(sid)}/submit`, payload)
export const fetchResult = (sid) =>
  apiGet(`/api/exam/${encodeURIComponent(sid)}/result`)
export const retryEvaluate = (sid) =>
  apiPost(`/api/exam/${encodeURIComponent(sid)}/evaluate`)

export const fetchOverview = () => apiGet('/api/teacher/overview')
export const preparePaper = (sid) =>
  apiPost(`/api/teacher/students/${encodeURIComponent(sid)}/paper`)
export const fetchPaper = (sid) =>
  apiGet(`/api/teacher/students/${encodeURIComponent(sid)}/paper`)
export const submitClassroomAnswers = (sid, payload) =>
  apiPost(`/api/teacher/students/${encodeURIComponent(sid)}/paper/answers`, payload)
export const fetchDetail = (sid, version) =>
  apiGet(`/api/teacher/students/${encodeURIComponent(sid)}${version ? `?version=${version}` : ''}`)
export const teacherEvaluate = (sid) =>
  apiPost(`/api/teacher/students/${encodeURIComponent(sid)}/evaluate`)
export const teacherRegenerate = (sid) =>
  apiPost(`/api/teacher/students/${encodeURIComponent(sid)}/regenerate`)
export const teacherRegenerateStatus = (sid) =>
  apiGet(`/api/teacher/students/${encodeURIComponent(sid)}/regenerate/status`)
export const teacherReview = (sid, payload) =>
  apiPost(`/api/teacher/students/${encodeURIComponent(sid)}/review`, payload)

export const fetchTaRoster = () => apiGet('/api/ta/students')
export const fetchTaStudent = (sid) => apiGet(`/api/ta/students/${encodeURIComponent(sid)}`)
export const saveTaGrade = (sid, payload) => apiPost(`/api/ta/students/${encodeURIComponent(sid)}/grade`, payload)
export async function downloadTaFile(path) {
  const response = await fetch(path, { headers: authHeader() })
  if (!response.ok) await parse(response)
  return response.blob()
}
