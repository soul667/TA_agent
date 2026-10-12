import { createRouter, createWebHistory } from 'vue-router'
import { getCredentials } from './api'

const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
  { path: '/student', name: 'student-login', component: () => import('./views/StudentLogin.vue') },
  {
    path: '/student/exam', name: 'student-exam', component: () => import('./views/StudentExam.vue'),
    meta: { role: 'student' },
  },
  {
    path: '/student/result', name: 'student-result', component: () => import('./views/StudentResult.vue'),
    meta: { role: 'student' },
  },
  // 教师页自带登录表单（未认证时渲染登录态），不能在守卫里重定向回同一路由，
  // 否则会形成同步无限重定向循环，卡死整页交互
  { path: '/teacher', name: 'teacher', component: () => import('./views/HomeView.vue') },
  { path: '/teacher/manage', name: 'teacher-manage', component: () => import('./views/TeacherPanel.vue') },
  { path: '/ta', name: 'ta-roster', component: () => import('./views/TaGrading.vue') },
  { path: '/ta/:studentId', name: 'ta-grade', component: () => import('./views/TaGrading.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const cred = getCredentials()
  if (to.meta.role === 'student' && (!cred || cred.role !== 'student')) {
    return { name: 'student-login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
