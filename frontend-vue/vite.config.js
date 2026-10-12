import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base=/static/ ：构建产物由 FastAPI 的 /static 挂载点直接服务，
// 页面路由（/ /exam /teacher /student）由后端返回同一个 SPA 壳。
export default defineConfig({
  base: '/static/',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
