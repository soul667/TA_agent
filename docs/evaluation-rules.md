# 教学判断规则与数据关联约定（v1，2026-09-12）

本文件是 Vue / Mock 修订版（2026-09-12 审批）工作包 1 的交付物，约束本阶段（Mock 演示）
所有生成、评价、存储与页面展示行为。真实模型接入后的规则修订须另立版本。

## 1. 两个正交维度：代码质量 ≠ 理解证据

系统对每名学生的判断由两个**互相独立**的维度构成，任何页面与接口不得把二者混同：

| 维度 | 表达字段 | 判定依据 |
| --- | --- | --- |
| 代码质量 | `code_quality`: `correct` / `incorrect` / `unknown` | 学生**原始提交**与作业要求的对照（诊断阶段） |
| 理解证据 | `understanding`: `mastered` / `partial` / `limited` / `insufficient`（另加逐知识点状态，见 §2） | 学生对**自己代码**的诊断问题作答（评价阶段） |

由此必须能表达且区分（审批 §4）：

1. 代码正确且解释充分（`correct` + `mastered`）
2. 代码正确但解释矛盾（`correct` + `limited/insufficient`）
3. 代码有错但能准确分析原因（`incorrect` + `partial/mastered`）
4. 回答不足以判断（`insufficient` 或逐知识点 `uncovered`，进入待人工核验）

**禁止**的行为：因为代码正确就判"掌握"；因为代码有错就判"不理解"；把一个代码错误的
正确归因等同于整体掌握。

## 2. 逐知识点证据结构

每份评价由若干**知识点结论**（`knowledge_points[]`）组成，每个结论必须四要素齐全：

```json
{
  "point": "loop-boundary",            // 知识点标识，与问题 target_issue 对应
  "point_zh": "循环边界",
  "question_ids": ["q2", "q4"],        // 支撑该结论的题目
  "code_evidence": "for (int i = 1; i <= n; i++)",  // 对应的代码位置
  "student_evidence": "学生答 q4：最后一次合法访问是 arr[n-1]…",  // 学生说了什么
  "status": "mastered|partial|limited|insufficient|uncovered",
  "basis": "exact-choice|sample-match|heuristic|uncovered|manual",
  "note": "结论说明（可选）"
}
```

- `uncovered` = 证据不足/待人工核验：Mock 规则未覆盖该回答，或回答为空。出现任一
  `uncovered` 知识点时，`needs_ta_review` 必须为 `true`，反馈中必须明示"待人工核验"。
- `basis` 说明判断来源，必须随结论透传到教师界面：
  - `exact-choice`：选择题与参考答案精确匹配（确定性）；
  - `sample-match`：text 回答与预置作答样例（含声明的变体）显式匹配（确定性）；
  - `heuristic`：无预置样例时的字词重合启发式（**只能用于非案例数据兜底**，且必须
    标注启发式并触发人工复核；不得宣称具备通用语义理解）；
  - `uncovered`：未覆盖；`manual`：助教复核改判。
- 整体 `understanding` 由知识点状态聚合（见 §5），内部诊断分 `score`（0~10）仅辅助排序，
  学生界面以证据与反馈为主。
- 模型自报 `confidence` 不等于经验证的准确率，仅供教师参考。

## 3. 作答轮次与证据类型

| 轮次 | 含义 | 记录方式 |
| --- | --- | --- |
| 首次作答 | 学生在**未接受任何系统提示**下的回答 | `attempt.stage = "initial"`，是理解判断的主要依据 |
| 提示后作答 | 学生看到系统提示（如"检查初始值对候选结果的限制"）后的补充核验题回答 | `attempt.stage = "followup"`，单独记录，不与首次混算 |
| 证据不足 | 回答为空、无关或规则未覆盖 | 知识点 `uncovered`，不折算为"答错" |

本阶段 Mock 通过预置案例演示两类轮次分支；提示内容与提示前后表现分别落盘，不得合并覆盖。

## 4. 版本关联规则（四方同一约定）

问卷、原代码版本、作答批次、评价、复核之间按以下规则绑定，**后端、教师端、学生端
与测试必须使用同一套字段**：

```text
data/submissions/{sid}.json        原始提交（代码源，本阶段不修改学生文件）
generated/{sid}.json               最新问卷：questionnaire_id（UUID）、version（从 1 递增）、
                                   code_fingerprint（所依据代码的规范化指纹）、case_id
generated/history/{sid}_v{n}.json  历史版本完整快照（重新生成只增不改）
responses/{sid}.json               每次作答 = attempts[] 一条：attempt_id（UUID）、
                                   attempt_no、questionnaire_id、questionnaire_version、
                                   submission_token（幂等标识）、answers、submitted_at、
                                   stage、evaluation_status
evaluations/{sid}.json             评价记录 records[]：每条绑定 attempt_id +
                                   questionnaire_version + questionnaire_id；status:
                                   pending/ok/failed；result 内含逐知识点证据
reviews/{sid}.json                 复核记录 reviews[]：每条绑定 attempt_id + evaluation_id；
                                   decision: confirmed/revised；published 反馈发布状态
```

硬性约束：

1. **重新生成问卷 = 新版本**：`version` 递增、新 `questionnaire_id`，旧问卷写入 history
   完整保留；已有作答永远挂在原版本下，不得迁移到新问题编号上。
2. **旧版本提交拒绝**：客户端提交时携带所答问卷 `questionnaire_version`；与当前版本
   不一致返回 `409`，提示刷新后按新问卷作答。
3. **评价只针对某个 attempt**：`records[]` 只追加；读取"当前评价"时按**最新 attempt**
   过滤。新提交评价失败时，旧 attempt 的评价只作为明确标注的历史展示，不得当作当前
   结果（教师总览 likewise）。
4. **重试评价不新增作答**：评价失败重试复用同一 `attempt_id`，只追加评价记录。
5. **幂等提交**：同一 `submission_token` 的重复请求返回同一回执（相同 attempt_id、
   提交时间与评价状态），不新增 attempt、不重复评价。
6. **写入互斥**：同一学生的提交/评价/重生成/复核在进程内经每学生文件锁串行化；
   原子写使用**唯一**临时文件名后 `os.replace`。
7. **中断恢复**：启动时扫描评价记录，`pending` 视为中断，标记 `failed`（原因
   `interrupted`），可由教师或学生重试；学生答案本身已在提交时落盘，不因评价失败丢失。

## 5. 理解程度聚合规则（Mock 判分引擎实现基线）

设某次作答各题 verdict ∈ {`correct`(1.0), `partial`(0.5), `wrong`(0), `insufficient`(0),
`uncovered`(不计入分母但触发复核)}，`ratio = 得分和 / 计分题数`：

- 知识点状态 = 该点题目 verdict 的最低档（correct>partial>wrong；含 uncovered 则该点
  为 uncovered）。
- 整体：所有知识点 mastered → `mastered`；ratio ≥ 0.6 且无 wrong 主导 → `partial`；
  0.3 ≤ ratio < 0.6 → `limited`；其余 → `insufficient`；存在 uncovered 时封顶 `partial`
  且 `needs_ta_review = true`。
- `needs_ta_review` 触发条件（任一）：存在 uncovered；存在 heuristic 兜底；ratio < 0.6；
  助教未处理的上一次复核请求。

此规则同时是 `tests/test_mock_cases.py` 对 72 组预置作答的断言基线。

### 5.1 代码变异题（code_variant）与限时（2026-09-12 第二批增补）

- **code_variant 题**：题目携带 `variant = {line_no, replacement}`（"假设把第 N 行改成
  XXX"）。`replacement` 是假设内容、**学生可见**，不是答案；学生端点击该题时左侧代码
  面板联动展示修改后的代码（改行高亮 + 原行删除线幽灵行）。判分与 single_choice 相同
  （`exact-choice`），`code_evidence` 取原代码行。public 视图暴露 `variant` 但永不暴露
  `expected_answer`。
- **双语**：UI 文案 vue-i18n（zh/en，右上角切换）；内容级（题目/选项/诊断/反馈）由后端
  `*_en` 字段提供，前端缺失时回退中文。判分反馈同时输出 `feedback` 与 `feedback_en`，
  计数一致（`tests/test_iteration2.py` 断言）。样例匹配键（variants）不含渲染标记，
  与展示文本分离，避免包裹符破坏归一化匹配。
- **限时**：问卷携带 `time_limit_minutes`（0 = 不限时）。前端倒计时到 0 自动提交并携带
  `time_expired = true` 与 `duration_seconds`；服务端**记录**这两个字段供教师查看，但
  不做硬性拒绝（客户端时钟可被篡改，硬约束需服务端计时，列入未完成项）。
- **重新作答预填**：`GET /api/exam/{sid}/result` 的 `attempts[]` 返回本人历次 `answers`；
  学生重新进入答题页时，同一问卷版本下无本地草稿的题目用最近一次作答预填，可一键清空。

## 6. 身份与可见性约定

| 角色 | 凭据 | 可见 |
| --- | --- | --- |
| 学生 | 专属访问码（`data/access/tokens.json`，教师可查并发放）；**开关 `AA_ACCESS_CODE_ENABLED` 默认关闭**，关闭时仅凭学号访问（演示模式） | 自己的代码、问卷、作答回执、状态、**已发布**反馈 |
| 教师/助教 | 教师 token（环境变量 `AA_TEACHER_TOKEN`，默认仅演示值） | 全部内部字段：expected_answer、诊断、逐知识点证据、复核工具、学生访问码 |
| 未认证 | 无 | 任何 `/api/exam/*`、`/api/teacher/*` 均拒绝（401/403） |

- 学生凭据与归属校验：访问码只绑定本人学号；用 A 的码访问 B 一律 403。
- 学生结果接口（`GET /api/exam/{sid}/result`）只返回：作答回执（编号/时间/状态）、
  状态机（§7）、已发布反馈、下一步建议；**永不**返回参考答案、内部诊断与教师备注。

## 7. 学生可见状态机

```text
draft_saved（本地草稿，含问卷版本键）
  → submitted（答案已落盘，回执含 attempt_id）
  → evaluating → eval_failed（待重试，答案仍在）
               → awaiting_review（评价完成但需助教复核，反馈未发布）
               → feedback_ready（反馈已生成并可见）
```

- 评价成功且 `needs_ta_review=false` 时直接 `feedback_ready`（评价反馈即公开反馈）；
  否则等待助教在复核闭环中点"发布反馈"。
- 反馈内容约束：具体、引用题目/代码位置、不含完整修正代码。

## 8. 助教复核闭环（最小集）

`POST /api/teacher/students/{sid}/review`：确认（confirmed）或修订（revised，可改判
理解档位并必填理由）→ 填写给学生的一句反馈 → 发布（published）→ 学生端状态变更为
`feedback_ready`。复核记录含复核人、时间、理由、发布状态，永久保留。

## 9. Mock 案例匹配规则（P1 修复约定）

- 诊断按**代码内容**匹配案例（规范化后比对，见 `app/cases.py`），与学号解耦：
  同一案例换学号诊断不变；相同学号换代码按新代码匹配。
- 未匹配到案例的代码：生成通用问卷，`case_id = ""`、`demo_notice = "演示规则未覆盖，
  待人工核验"`，评价走 heuristic 兜底并强制复核。
- 学生请求中的任何字段（包括传入的"期望分数/答案类型"）不参与、不影响判分；
  判分输入只有服务端保存的问卷与作答。
- 预置作答样例存于 `data/mock_cases/`，仅供演示与测试，绝不自动写入学生提交记录。

## 10. 本文档与实现的对应

| 约定 | 实现位置 |
| --- | --- |
| 数据模型与版本字段 | `app/models.py` |
| 锁、原子写、历史、恢复 | `app/storage.py`、`app/main.py`（lifespan） |
| 认证与可见性 | `app/auth.py`、各路由 |
| 案例匹配 | `app/cases.py` |
| 判分引擎与 basis | `app/mock_grader.py` |
| 聚合规则 | `app/mock_grader.py`（真实 LLM 亦须输出同构 knowledge_points） |
| 回归断言 | `tests/`（39 项保留 + 新增用例） |
