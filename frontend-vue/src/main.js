import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import { applyTheme, getTheme } from './theme'
import './style.css'

// 挂载前应用主题，避免深色偏好下的白屏闪烁
applyTheme(getTheme())

const app = createApp(App)
app.use(ElementPlus, { locale: zhCn })
app.use(i18n)
app.use(router)
app.mount('#app')
