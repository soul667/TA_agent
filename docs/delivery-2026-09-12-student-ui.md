# 学生端界面优化交付（登录页 + 答题页移动端）

日期：2026-09-12 · 类型：纯前端迭代（不改任何后端 API 与业务逻辑）

## 一、改动内容

### 1. /student 登录页升级（StudentLogin.vue 重写）

- **课程上下文**：store.js 新增 `metaInfo`（App.vue 启动拉取 /api/meta 时写入），
  登录页标题上方显示「课程名 · 作业名」muted 小字（EN 界面优先取 `_en` 字段），
  学生可确认进对了地方，与首页 hero 呼应。
- **视觉聚焦**：桌面端表单放入 surface 纯色卡片（无边框无阴影，遵循既有平面设计系统）
  并在视口内垂直居中（`min-height: calc(100vh - 52px)`）；移动端保持顶部开始、收紧内边距。
  桌面端自动聚焦学号输入框（移动端不聚焦，避免一进页面就弹键盘）。
- **行内校验与错误**：
  - 学号格式：blur 触发行内红字提示，修正输入后**实时消除**（watch v-model 实现，
    不依赖 el-form-item 的 error prop——其内部显示走 100ms 防抖，清除不跟手，故弃用）；
  - 登录失败（404/401/403/网络）：表单内持久行内错误（保留 `localizeBackendDetail` 英译映射），
    不再只靠转瞬即逝的 toast；401/403 时清掉无效凭据避免顶栏残留登录态。
- **已提交学生分流**：fetchExam 校验成功后，若 `has_submitted` 则不再直接跳答题页，
  卡片内显示「已提交过 N 次」信息面板，提供「查看我的结果」（→/student/result，
  返访学生最常见意图）与「继续作答（第 N+1 次）」（→/student/exam）两个动作；
  带 `redirect` 参数时仍按原路径直达，未提交学生行为不变。

### 2. 答题页移动端题目优先（StudentExam.vue）

- <768px 时移除置顶代码卡片（原先占首屏 38vh），题目列表紧随页头；
  代码收进页头「查看代码」文字入口，点击打开原有底部 code-peek 抽屉；
  点击题目弹出代码速览的联动保留。桌面左代码（sticky）右题目分栏**完全不变**。

### 3. 代码面板自动换行开关（CodePanel.vue / style.css）

- CodePanel 新增 `wrap` prop（`is-wrap` 类）：`white-space: pre-wrap` + 行号列保持 sticky，
  长行折行而非横向滚动；默认仍不折行。
- 桌面代码工具行与移动端抽屉头部均提供「自动换行：开/关」文字开关，
  偏好持久化在 localStorage `aa_code_wrap`，跨页面/跨端生效。

### 4. i18n

新增文案（中英双份，默认语言为 EN）：`submittedHint`、`continueAnswer`、`codePeekTip`、
`codeWrap/codeWrapOn/codeWrapOff`；移除已无引用的 `expandCode/collapseCode`。

## 二、验收

- `uv run pytest`：75 passed（后端零改动）。
- 独立 Playwright + 系统 Chrome 黑箱验收（隔离环境 `AA_ROOT=/tmp/aa_ui_accept`、
  `MOCK_LLM=true`、8021 端口）：**30/30 项通过**，覆盖
  桌面 1440 / 移动 390 × 浅色 / 深色 × zh / EN：
  - 登录页：课程上下文、自动聚焦、格式错误行内提示与实时消除、
    不存在学号行内错误、未提交直达答题页、已提交分流面板（EN/ZH）、
    分流「继续作答」载入上次答案提示；
  - 答题页：桌面 sticky 分栏保留、换行开关（类名 + localStorage + white-space 三重断言）、
    5 题全部作答提交出回执、结果页步骤条、移动端题目优先 + 页头代码入口 +
    底部抽屉 + 抽屉内换行开关 + 点题联动；
  - 全流程真实提交（20260002）与二次作答预填无回归。
- 关键截图存于 `/tmp/gui-test/shots-ui/`（11 张），验收脚本
  `/tmp/gui-test/acceptance-ui-login.mjs`。
- 布局几何经 DOM 实测核验（卡片 440px 居中，两侧留白相等）；IAB 截图在该环境
  按 devicePixelRatio≈1.46 渲染且只输出左上区域，不能作为布局证据（与上批
  交付记录的 IAB 输入管道故障一并记入环境限制，验收改用独立 Playwright）。

## 三、已知限制

- 「已提交 N 次」的 N 取自 `attempt_count`，仅作提示，不改变多作答语义。
- 行内错误为纯文字红字（设计系统的文字化状态），输入框不加红描边。
- 服务端计时仍为软约束（客户端时钟），与上批一致。
