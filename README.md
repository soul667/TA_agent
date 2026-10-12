# adaptive-assessment

基于学生原始作业生成**个性化抽查答卷**的教学系统。

## 当前课程：CS111 2026 秋 PA1

PA1 已按实际提交包中的**四则运算**配置。启动这一班的抽查页面：

```bash
bash scripts/run_pa1.sh
```

打开 <http://127.0.0.1:8000/>，输入真实学号即可生成答卷。默认读取项目中的 `.env.local`
模型配置，沿用原教师口令。PA1 数据位于 `datasets/pa1_2026fa/`，含 90 位学生、89 份可读代码提交、
96 个源文件；学号 `12611333` 的提交没有可读代码，系统会明确提示。

抽查按每位学生实际的数值、变量、运算表达式与输出定制，至少保留一道解释题；
整数除法、不同常数或不同 `printf` 写法不会仅因与示例不同而判错。
多文件保持独立程序边界，参考答案默认折叠且不随答卷打印。当前配置使用真实模型，
模型未配置时不会用旧 `find_max` 演示题替代。

下文的通用快速开始使用项目根目录的旧演示数据；课堂 PA1 请使用上面的启动方式。

### TA 人工评分

打开 <http://127.0.0.1:8000/ta>，使用教师口令登录。名单包含全部 90 个学号，可按学号搜索，
筛选待评分、已评分、可生成答卷或缺少代码。点进学号后可逐个查看 C 源文件、阅读提交文本、
下载**该生**原始附件包，并按 **100 分制**填写得分、评分人和评语。支持保存并评下一位、
查看修改历史和导出全班评分 CSV。

分数由 TA 手动填写，保存在 `datasets/pa1_2026fa/grades/<学号>.json`；
不会自动填写 AI 分数，也不会发布或回写 Blackboard。每次修改保留分数、评语、填写的评分人、
时间及当时的提交快照。评分人是 TA 自填标识，访问权限仍由共享教师口令控制。
并发评分会提示版本冲突；若学生提交发生变化，旧评分会标记为需重评。

全部可生成学号另见 `datasets/pa1_2026fa/可生成学号.txt`。

| API | 说明 |
|---|---|
| `GET /api/ta/students` | 全班名单、答卷状态、人工评分状态 |
| `GET /api/ta/students/{sid}` | 原始作答、附件清单、当前分数与评分历史，无需先生成答卷 |
| `POST /api/ta/students/{sid}/grade` | 保存评分，含版本与提交校验、重试幂等 |
| `GET /api/ta/students/{sid}/download` | 下载该生最近一次原始提交包 |
| `GET /api/ta/grades.csv` | 导出评分 CSV；未评分保持空白，0 分保留 |

上述接口均需要教师 / TA 口令；学生访问码不可访问。

2026-09-27 应用户要求对指定的 16 个学号进行了源码复核；15 个有源码的学号已按
“C 语法 40 + 四则运算 40 + 输出 10 + 注释 5 + 显式 return 5”的辅助口径写入本地 TA 评分。
另一个学号在提供的提交包中没有记录，未给分。逐人证据、扣分说明与不能核对的原因见
`datasets/pa1_2026fa/inspections/batch-2026-09-27.md`。这批成绩保存在本地 TA Agent，
没有回写 Blackboard；TA 可查看历史并复核调整。

默认流程：**打开首页 → 输入学号 → 生成答卷 → 直接选择 / 填写 → 提交答案 → TA 评分**。
选择题支持单选、多选和代码变式，简答题可直接输入；草稿按学号和答卷编号保存在浏览器，
刷新后可以继续作答。提交后的正式答案保存在 `responses/<学号>.json`，每次提交绑定答卷编号与版本，
重新提交保留历史。现场作答提交不启动 AI 评价，由 TA 在 `/ta/<学号>` 逐题查看答案和人工评分；
新增作答会把旧评分标成需重评。作答期间隐藏首页教师参考，提交后可展开。
仍支持打印空白答卷或保存 PDF，打印时排除输入控件、已选答案和教师参考。
首次使用填写教师口令，同一浏览器后续抽查只需输入学号。系统自动读取
`data/submissions/<学号>.json` 中保存的原始作业代码，按需生成 3–5 道题；
无须提前给全班生成问卷，也无须经过总览、评价或复核。相同作答再次抽查直接打开原卷，
代码或作业内容更新后自动生成新版本。教师参考默认收起，打印时始终排除。

当前仓库内置 `20260001`–`20260006` 六份示例作答；实际学生需先将其作业导入上述目录，
学号不存在或代码为空时会明确提示，不会拿其他学生的作答代替。
沿用已有模型配置，无 API Key 时显示演示模式（内置案例匹配；未匹配的代码使用通用题）。

闭环：`学生原始 submission → AI 诊断错误 → 按错误生成不同的诊断问题 → 学生网页作答 → AI 评价新回答 → 教师/助教复核并发布反馈 → 学生查看反馈`

- 后端：Python + FastAPI + Pydantic（结构化输出校验 + 重试）
- 前端：Vue 3 + Element Plus（Vite 构建，产物随项目分发，`frontend/`）
- 存储：JSON 文件（按学生加锁 + 原子写，无数据库；敏感文件 0600）
- 鉴权：最小化方案——教师口令 + 每生访问码。**访问码默认开启**（`AA_ACCESS_CODE_ENABLED=0`
  可关闭用于课堂演示，风险自负）；未配置教师口令时自动生成随机口令并写入
  `data/access/tokens.json`（历史默认值 `ta-demo-token` 会被自动轮换）
- LLM：统一 `LLMProvider` 接口；**无 API Key 时自动使用 Mock，整个系统可离线完整运行**

评价规则与数据关联的硬性约定见 **[docs/evaluation-rules.md](docs/evaluation-rules.md)**（代码质量 × 理解程度两维正交、逐知识点证据、版本关联、复核闭环）。

## 快速开始

```bash
# 1. 安装依赖（需要 Python >= 3.10）
uv sync

# 2. 配置教师口令（强烈建议；不配置时自动生成随机口令并打印到启动日志，
#    同时保存在 data/access/tokens.json）
export AA_TEACHER_TOKEN="换成你自己的口令"

# 3. 启动（输入学号时自动生成，无需提前批量生成）
uv run uvicorn app.main:app --reload
```

打开 <http://localhost:8000>：

- **抽查**：首页输入学号 → 生成答卷 → 直接填写并提交答案 → TA 查看评分；也可打印空白卷。首次使用同时填写教师口令。
- **学生在线作答**：首页底部入口 → 学号 + 访问码登录 → 答题 → 提交。
- **管理记录**：首页底部入口 → 原有总览、证据链、复核与发布反馈。

学生访问码在首次启动时为全部已知学号自动生成并保存在 `data/access/tokens.json`
（格式 `{"teacher_token": "...", "students": {"20260003": "s-..."}}`，可手工改），
教师详情页可直接查看每生的访问码。教师口令优先取环境变量 `AA_TEACHER_TOKEN`。

没有 uv？用 pip 也可以：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn pydantic pytest httpx
export AA_TEACHER_TOKEN="..."
python -m app.generate_all
uvicorn app.main:app --reload
```

## 页面与 API

所有 `/api/*` 均需鉴权：教师口令放行全部；学生访问码只能访问自己的数据（跨学号 403）。
`Authorization: Bearer <口令或访问码>`。

| 路由 | 说明 |
|---|---|
| `/`、`/teacher` | 抽查工作台：输入学号、按需生成、查看 / 打印答卷 |
| `/student` | 学生登录（学号 + 访问码） |
| `/student/exam` | 答题页：桌面代码与题目并排，手机顺序展示（代码在前）；草稿按问卷版本本地暂存 |
| `/student/result` | 我的结果：状态步骤条、反馈、提交记录（可重试失败的评价） |
| `/teacher/manage` | 管理记录：总览 + 单人详情抽屉（证据链 + TA 复核表单） |

| API | 鉴权 | 说明 |
|---|---|---|
| `GET /api/meta` | 无 | 演示模式 / 课程信息 |
| `POST /api/teacher/students/{sid}/paper` | 教师 | 一键准备抽查答卷：已有匹配答卷返回 `ready`；否则异步 202，重复点击复用任务 |
| `GET /api/teacher/students/{sid}/paper` | 教师 | 查询生成进度 / 答卷与独立教师参考；不启动学生答题计时、不触发评价 |
| `POST /api/teacher/students/{sid}/paper/answers` | 教师 | 保存 TA 开启的现场作答，版本校验与重试幂等；答案交给 TA 人工评分 |
| `GET /api/exam/{sid}` | 学生本人 / 教师 | 问卷（不含任何参考答案与诊断；含版本号） |
| `POST /api/exam/{sid}/submit` | 学生本人 / 教师 | `{answers, questionnaire_version, submission_token}`；旧版本 → 409；同 token 重复提交 → 同一回执不新增作答；答案先落盘再评价 |
| `GET /api/exam/{sid}/result` | 学生本人 / 教师 | 状态机（submitted/evaluating/eval_failed/awaiting_review/feedback_ready）+ 反馈 + 提交记录 |
| `POST /api/exam/{sid}/evaluate` | 学生本人 / 教师 | 评价失败后重试（不新增作答） |
| `GET /api/teacher/overview` | 教师 | 全部学生状态总览 |
| `GET /api/teacher/students/{sid}` | 教师 | 单人完整详情：诊断、逐版本问答、评价记录、复核历史、学生访问码 |
| `POST /api/teacher/students/{sid}/evaluate` | 教师 | 重新评价（以最新一次作答） |
| `POST /api/teacher/students/{sid}/regenerate` | 教师 | 重新生成问卷（**新版本**，旧问答完整保留） |
| `POST /api/teacher/students/{sid}/review` | 教师 | TA 复核：`{decision: confirmed/revised/published, ...}`，发布后学生端显示反馈 |
| `POST /api/teacher/students/{sid}/rotate-code` | 教师 | 轮换该生访问码（怀疑泄露时处置），旧码立即失效 |

## 安全加固（红蓝对抗审计后默认生效）

详见 [docs/security-audit-2026-09-13.md](docs/security-audit-2026-09-13.md)。默认配置即为安全配置，
以下环境变量供按需调整：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AA_TEACHER_TOKEN` | （自动随机） | 教师口令；未配置时生成随机值写入 tokens.json 并打印 |
| `AA_ACCESS_CODE_ENABLED` | `1` | 学生访问码开关。设 `0` 进入演示模式（仅凭学号，有冒充风险） |
| `AA_RESUBMIT_AFTER_FEEDBACK` | `0` | 评价出分后是否仍允许重交（`1`=开放重练模式） |
| `AA_MAX_ATTEMPTS_PER_VERSION` | `5` | 同一问卷版本的最大提交次数 |
| `AA_SUBMIT_PER_MINUTE` | `10` | 每学生每分钟提交上限（幂等重试不计） |
| `AA_OVERTIME_GRACE_SECONDS` | `600` | 超过问卷限时多久后直接拒收（之前仅标记） |
| `AA_MAX_BODY_BYTES` | `1048576` | 请求体大小上限（超出 413） |
| `AA_ALLOWED_HOSTS` | 本机+私网 | Host 头白名单（防 DNS rebinding），逗号分隔支持通配 |
| `AA_DOCS` | （关闭） | 设 `1` 开放 /docs、/openapi.json |

内置防护：认证失败按 IP 限速（8 次/10 分钟 → 429 封禁 10 分钟）、常量时间口令比较、
服务端限时计时（以首次打开问卷的服务端时间为准）、评价结论在复核发布前不向学生泄露、
文本作答提示词注入检测（强制转人工复核）、跨学生同文作答协同作弊检测、HTTP 安全响应头
（nosniff / DENY / CSP / no-store）。

## 测试学生（Mock 模式）

| 学号 | 原始错误 | 问卷侧重 |
|---|---|---|
| 20260001 | 完全正确 | 确认理解：为什么 i=1、复杂度、必须遍历的证明 |
| 20260002 | `max` 初始化为 0 | 负数、初始值假设、边界输入 |
| 20260003 | `i <= n` 越界 | 合法下标、循环终止条件、Undefined Behavior |
| 20260004 | 比较方向 `<` 写反 | 循环不变量、max/min 语义迁移 |
| 20260005 | 只比较最后一个元素 | 遍历完整性、为什么必须检查每个元素 |
| 20260006 | 缺少 `return` | 返回机制、局部变量与调用者的边界 |

## Mock Mode（18 案例 × 4 作答档位 × 5 题）

默认即 Mock：`MOCK_LLM` 未设置且无 `LLM_API_KEY` 时自动回退 `MockLLMProvider`。

- **诊断按代码内容匹配**（归一化代码指纹），与学号无关：同一份代码换学号 → 同一诊断；
  同一学号提交新代码 → 按新代码重新匹配。`data/mock_cases/C01–C18` 覆盖 18 种典型代码
  （正确实现、越界、哨兵值、下标错位、提前 return、排序副作用等），每案例 5 题（4 题诊断
  + 1 题**代码变异题**）与 4 组预置作答（mastered / partial / contradictory / insufficient，
  共 72 组，评价结果在测试中逐一断言）。
- **代码变异题（code_variant）**：题目假设"把某一行改成 XXX"，学生点击题目时左侧代码
  面板联动展示修改后的代码（改行高亮 + 原行删除线），用于训练"改动 → 行为"的推演能力。
  判分与选择题相同（exact-choice），`variant` 学生可见但答案永不下发。
- **评价是确定性规则判分**（`app/mock_grader.py`），判分依据显式标注：
  选择题 `exact-choice`（精确匹配）；text 题 `sample-match`（与预置样例变体精确匹配，
  支持同义改写）；未覆盖回答 → `uncovered`（待人工核验，**不用**字词重合冒充语义理解）；
  仅无样例数据的通用问卷用 `heuristic` 兜底并强制 `needs_ta_review`。
  反馈中英双语（`feedback` / `feedback_en`），计数一致。
- 未覆盖的代码 → 通用问卷，诊断注明"演示规则未覆盖该代码，待人工核验"。
- 代码质量与理解程度**两维正交**：代码写对但解释矛盾 → `code_quality=correct,
  understanding=limited`；代码有错但分析到位 → 反之。预置答案永不自动写入学生作答。

强制指定：`MOCK_LLM=true uv run uvicorn app.main:app`

## 双语 / 限时 / 预填（第二批迭代功能）

- **中英双语**：页面右上角一键切换中/EN（vue-i18n，偏好存 localStorage）。UI 文案与
  题目内容（`question_en` / `choices_en` / 诊断 / 反馈 / 知识点名）均双语；代码片段用等宽
  字体（`` `…` ``），`$…$` 若为代码表达式（含运算符/括号，或命中本题代码里的标识符）同样
  按等宽渲染，纯符号/变量才用 Times New Roman 衬线。答题页还会从本题代码自动提取标识符
  （排除 C 关键字），题面与选项里出现的 `arr` / `n` / `i <= n` 等无需后端标记也会按代码显示。
- **限时**：问卷携带 `time_limit_minutes`（Mock 案例默认 20 分钟）。答题页显示倒计时，
  归零自动提交；服务端记录 `time_expired` 与 `duration_seconds`，学生结果页与教师详情
  均标出超时提交。服务端以**首次打开问卷的时间**独立计时（防伪造客户端上报）：
超时在宽限内的提交被强制标记 `time_expired` 并以服务端计时为准，超过
`限时 + 2 分钟 + AA_OVERTIME_GRACE_SECONDS` 的提交直接 409 拒收。
- **重新作答预填**：重新进入答题页时自动载入同一问卷版本最近一次作答（本地草稿优先），
  可一键"清空重填"。
- **代码高亮**：代码面板使用 highlight.js 语法着色；桌面端左（代码）右（题目）两栏
  各自独立滚动，手机端代码置顶顺序阅读。

## 使用真实 LLM（OpenAI 兼容 API：GLM / Qwen / DeepSeek / 豆包 / 本地模型）

```bash
uv sync --extra llm   # 安装 openai 客户端

export LLM_API_KEY="sk-..."                                  # 必填
export LLM_BASE_URL="https://api.deepseek.com/v1"
export LLM_MODEL="deepseek-flash"                            # 未配置时的默认模型（推理型）
export AA_OUTPUT_LANG="en"                                   # 可选：学生可见文本输出语言（默认 zh）
uv run python -m app.generate_all --force
uv run uvicorn app.main:app --reload
```

也可以把以上变量写入项目根目录的 `.env.local`（已被 .gitignore 排除，**不要提交**）后
`set -a; source .env.local; set +a` 再启动。默认模型 `deepseek-flash` 是 DeepSeek
官方 API 的推理型模型（reasoning 与输出共享 max_tokens，`AA_MAX_TOKENS` 建议给到
`32768` 防止 JSON 被截断），端点 `https://api.deepseek.com/v1` 与 OpenAI
Chat Completions 协议兼容；也可换成任意 OpenAI 兼容端点（GLM / Qwen / 豆包 / 本地模型）。

模型输出经过 `JSON 提取 → 宽松修复 → Pydantic 校验 → 失败按类别处置`（LangChain 优化后）：
暂时性错误（网络/超时/限流/5xx）指数退避重试、空内容小步重试、结构坏时**定点修复坏题**
（不再整卷重试）；底层客户端为 langchain-openai `ChatOpenAI`，对端点做结构化输出能力
自动降级（`response_format=json_schema` → `json_mode` → 纯提示词）。`student_id` /
`assignment_id` 等权威字段由后端注入，不信任模型回显。已用真实 API 完成生成 + 评价
端到端冒烟（见交付说明）。

### LLM 相关环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AA_STRUCTURED_MODE` | `auto` | 结构化输出模式：`auto`（自动降级并学习）/ `json_schema` / `json_mode` / `legacy` |
| `AA_MAX_TOKENS` | `12000` | 单次生成的 max_tokens（出题提示要求超产 6~7 题，给思考留预算；`deepseek-flash` 推理模型建议 `32768`） |
| `AA_LLM_TIMEOUT` | `180` | 单次请求超时（秒） |
| `AA_TEMPERATURE` | `0.2` | 生成温度 |
| `AA_JOURNAL` | `1` | LLM 调用台账（`responses/llm_journal.jsonl`），设 0 关闭 |
| `AA_REUSE` | `1` | 内容寻址复用（同代码指纹直接克隆已验证问卷），设 0 关闭 |
| `AA_GEN_CONCURRENCY` | `4` | 批量生成并发上限 |
| `AA_MOCK_CHAOS` | （空） | Mock 故障注入（仅首次调用）：`empty` / `bad_json` / `truncate` |

## LangChain 优化（出题 agent 三批改造）

- **第一批 · 结构化输出与修复带**：`app/schemas.py` 草稿模型（不含服务器权威字段）+
  原生结构化输出（能力探测降级）+ 失败分类（`LLMTransientError`/`LLMEmptyError`）+
  定点修复（坏题带校验错误单独回修拼接）+ 超产 6~7 题择优保留 3~5 道（`finish_reason=length`
  截断闸门 + `AA_MAX_TOKENS` 预算）。
- **第二批 · 台账与质量闭环**：`app/journal.py` 每次 attempt 落 JSONL（原始输出 + 实际
  prompt + 校验结果，回放的前提）；`prompts/` 注册表 + `PROMPT_VERSION`（内容哈希）盖章到
  生成/评价记录；`python -m app.replay` 离线回放台账（修解析器零 API 成本回归）；
  `python -m app.eval_harness` 18 案例金标真实 LLM 评测（量化解析率/双语完备度/uncovered）
  并录制 `tests/cassettes/`，`pytest -m cassette` 无网络回放真实解析链路。
- **第三批 · 复用与异步**：`build_assignment_prefix`/`build_student_suffix` 前后缀拆分
  （学生身份移出可克隆载荷，稳定前缀吃 provider 前缀缓存）；`app/reuse.py` 内容寻址复用
  （`(作业, prompt版本, 代码指纹, 语言)` 命中即克隆，抄作业零 LLM 调用，single-flight 合并
  并发同指纹请求）；`generate_all` 并发批跑（`AA_GEN_CONCURRENCY`）；教师重新生成改
  **异步 202 + 任务轮询**（`generated/jobs/`，启动清扫中断任务），HTTP 不再挂 180s。

## 前端开发与构建

`frontend-vue/` 为源码（Vue 3.5 + vite 5.4 + Element Plus 2.9 + vue-i18n 9.14 +
highlight.js 11.12，版本全部锁定在 `package.json`）；`frontend/` 为构建产物
（已随项目分发，后端 `/static` 直接服务）。

```bash
cd frontend-vue
npm ci            # 按 package-lock.json 精确安装
npm run dev       # 开发服务器（/api 代理到 localhost:8000）
npm run build     # 构建到 dist/
# 发布：用 dist/ 的 index.html 与 assets/ 覆盖 frontend/ 同名内容
#（frontend/ 另含 legacy/ 与 favicon 等静态资源，不会被构建覆盖）
cp dist/index.html dist/favicon.svg ../frontend/ && rm -rf ../frontend/assets && cp -r dist/assets ../frontend/
```

旧版纯 HTML 页面保留在 `frontend/legacy/` 仅作参考。

## 命令行工具

```bash
uv run python -m app.generate_all          # 生成问卷（已存在则跳过；并发批跑 + 内容复用）
uv run python -m app.generate_all --force  # 覆盖重新生成（--no-reuse 忽略复用缓存）
uv run python -m app.evaluate_all          # 对所有已提交学生（重）跑评价
uv run python -m app.replay                # 离线回放 LLM 台账（修解析器零成本回归）
uv run python -m app.eval_harness          # 18 案例金标真实 LLM 评测 + 录制测试盒带
uv run pytest                              # 自动测试（108 个用例；-m cassette 跑盒带回归）
```

## 数据目录

```
data/assignment/      作业题目 + 参考答案 + 评分要点（1 个 JSON）
data/submissions/     每个学生的原始提交（{学号}.json）
data/mock_cases/      18 份 Mock 案例（代码 + 诊断 + 4 组预置作答与期望）
data/access/          tokens.json（教师口令 + 每生访问码）
generated/            Agent 生成的个性化问卷（{学号}.json 最新版）
generated/history/    问卷历史版本（{学号}_v{n}.json，重新生成时归档）
responses/            学生作答（{学号}.json，顶层最新 + attempts[] 全历史）
evaluations/          AI 评价（{学号}.json，逐 attempt 一条记录，含 pending/failed 状态）
reviews/              TA 复核记录（{学号}.json）
locks/                按学生文件锁（flock）
```

测试时可用环境变量 `AA_ROOT=/tmp/xxx` 把整个数据根目录指到别处（pytest 与浏览器
验收均这样隔离，项目内 `data/` 不被污染）。

## 设计决策（含边界情况处理）

- **重复提交与幂等**：允许重复提交，每次提交是一条新 `attempt`（绑定问卷 id+版本）。
  客户端携带 `submission_token`，网络重试/并发重复请求返回同一回执，不产生重复作答。
- **版本关联**：提交携带 `questionnaire_version`，教师重新生成后旧页面提交 → `409`，
  前端提示刷新加载新问卷；旧版本问答在 `history/` 与 `attempts[]` 中完整保留。
- **评价失败不丢数据**：答案先落盘再评价；评价失败保留作答并显式 `eval_failed`，
  学生端/教师端均可重试，绝不显示上一次的旧评价结果冒充本次。
- **启动恢复**：服务重启时把中断的 `pending` 评价标记为 `failed`（可重试）。
- **不存在的学号** → `404`；**问卷未生成** → `404`；学号非法格式 → 400，杜绝路径注入。
- **JSON 可靠性**：所有写入均为 `mkstemp 唯一临时文件 + os.replace` 原子写；每个学生
  写操作持有 `flock`；损坏 JSON 按"不存在"处理。
- **安全**：学生 API 只返回 `{id, type, question, choices}` 与代码本身；`expected_answer`、
  诊断、知识点结论仅存在于后端文件与教师 API。学生请求中的任何字段不参与判分。
- **TA 复核闭环**：评价带 `needs_ta_review` 或教师主动介入时，教师确认/修订结论并发布
  文字反馈；发布前学生端停留在"等待助教复核"，不显示内部结论。
- **访问码开关**：`AA_ACCESS_CODE_ENABLED`（**默认开启**）。设为 `0`/`false` 进入演示模式：
  学生登录页只有学号、教师详情页不显示访问码、学生端 API 仅凭学号访问（有冒充与窥探
  风险，仅建议本机演示）；`/api/meta` 返回当前状态，前端自适应。教师口令不受开关影响。
  怀疑访问码泄露时教师可调 `POST /api/teacher/students/{sid}/rotate-code` 立即轮换。

## 目录结构

```
app/
  main.py          FastAPI 路由（SPA 页面 + 学生 API + 教师 API + 复核）
  agent.py         诊断 Agent（服务端权威案例绑定）
  evaluator.py     评价 Agent（逐知识点证据组装）
  llm.py           LLMProvider / Mock / OpenAI 兼容封装 / JSON 提取与重试
  cases.py         Mock 案例库加载 + 代码指纹匹配
  mock_grader.py   确定性判分引擎（样例匹配 / uncovered / 启发式兜底）
  auth.py          最小鉴权（教师口令 + 学生访问码）
  models.py        Pydantic 模型（问卷/作答/评价/复核 + 防泄露的 public 视图）
  storage.py       JSON 原子读写 + 文件锁 + 版本历史 + 中断恢复
  evaluate_all.py  CLI：批量评价
frontend-vue/      Vue 3 源码（vite + Element Plus，版本锁定）
frontend/          构建产物（index.html + assets/，随项目分发）+ legacy/
data/              作业 + 6 份学生提交 + 18 份 Mock 案例 + 访问码
tests/             pytest 73 例（Agent / 判分 / 鉴权回归 / 闭环回归 / 双语与变异题 / API 端到端）
docs/
  evaluation-rules.md     评价规则与版本关联约定（实现对照）
  acceptance-shots/       浏览器验收截图（学生/教师双视角，12 张）
  delivery-2026-09-12.md  本期交付说明（修改清单 / 证据 / 未完成项）
```

## Future Work（有意不做）

真实 LLM 评价质量验收、Blackboard API、LTI、SSO/OAuth、反作弊、复杂用户系统、
知识图谱、IRT/BKT、长期学生画像、消息队列、向量数据库、微服务、K8s。
