# 交付说明：LangChain 优化出题 agent（三批）

日期：2026-09-12　分支：`master`

## 0. 背景与选型

以「用 LangChain 优化出题 agent」为题做了 5 认知框架 × 6 想法的发散式方案探索（30 条、
5 聚类），按加权分（新颖 0.35 + 可行 0.40 + 契合 0.25）收敛出三条主线并全部落地：

1. **结构化输出与修复带**（原生结构化输出 + 失败分类 + 定点修复 + 超产安全库存）
2. **台账与质量闭环**（原始响应台账 + 离线回放 + 18 案例金标评测 + 测试盒带）
3. **复用与异步**（内容寻址复用 + 前缀缓存 + 并发批跑 + 重新生成 202 化）

明确不做的陷阱（发散阶段识别）：多学生拼一次调用（毁个性化绑定，假节俭）、生成+判分
闭环 StateGraph（判分发生在学生作答后，伪闭环）、LangGraph checkpointer 后台化（需持久
DB，规模不需要）、PII 脱敏门、seed 复现证明（超前）。后置留档：LangGraph 两阶段
map-reduce 出题、best-of-N + judge、判分 uncovered 反馈回流、多厂商 `with_fallbacks`。

## 1. 第一批：结构化输出 + 失败分类 + 定点修复

- **`app/schemas.py`（新）**：`QuestionnaireDraft` / `EvaluationDraft` 只含模型产出字段，
  `questionnaire_id` / `version` / `code_fingerprint` / `case_id` / `generated_by` 等
  服务器权威字段不进发给模型的 JSON Schema；题目条数放宽 3~7（超产安全库存）。
- **`app/llm.py`**：`OpenAICompatProvider` 底层换 langchain-openai `ChatOpenAI`
  （`max_tokens` 经 `extra_body` 原样透传，保持与 openai SDK 直连一致的请求体）；
  结构化输出能力梯子 `json_schema → json_mode → legacy` 自动降级并学习（`AA_STRUCTURED_MODE`
  可固定）；异常归类为 `LLMTransientError`（网络/超时/限流/5xx → 指数退避重试 2 次）与
  `LLMEmptyError`（空内容 → 小步重试 1 次），均不消耗结构重试次数；每次 attempt 检查
  `finish_reason=="length"`，截断时在修复提示中附加精简要求。
- **`app/agent.py`**：逐题用权威 `Question` 校验，合格题直接装配（坏题不再连坐整卷）；
  有效题 <3 时走**定点修复**——只把坏题 + 校验错误发回给模型做独立小调用，修好后按原位
  拼回；`_pick_questions` 按 target_issue 多样性择优保留 ≤5 道。出题提示改超产 6~7 题、
  code_variant 规则补全、**双语字段改为硬性要求**（见 §5 评测发现）。
- **`app/evaluator.py`**：同样接入草稿 + 装配；Mock 路径与 75 个既有测试行为不变。

## 2. 第二批：台账 + 回放 + 金标评测

- **`app/journal.py`（新）**：`run_structured` 每次 attempt 追加一行 JSONL 到
  `{AA_ROOT}/responses/llm_journal.jsonl`——provider、prompt_version、attempt、实际
  发送的完整对话（convo）、**原始输出**（此前直接丢弃）、解析结果、错误、finish_reason、
  延迟。`AA_JOURNAL=0` 关闭；flock 串行化，台账失败绝不影响主流程。
- **`prompts/`（新）+ `app/prompts.py`（新）**：两段 SYSTEM_PROMPT 抽为
  `prompts/questionnaire.txt`、`prompts/evaluation.txt`，`PROMPT_VERSION = "pv-" +
  sha256[:12]`，盖章到 `Questionnaire.prompt_version` / `Evaluation.prompt_version`
  （provenance：生产历史上「prompt 变了」与「模型变了」可区分）。
- **`app/replay.py`（新）**：`python -m app.replay` 对台账离线重跑 `extract_json →
  两级校验`（严格 = 原始输出直过草稿 Schema；宽松 = pre_clean 后通过），输出解析率 /
  通过率 / 高频错误 / prompt_version 分布，`--json` 落机器可读报告。修解析器从此
  **零 API 成本**回归历史真实输出。
- **`app/eval_harness.py`（新）**：18 案例库升格金标准集，真实 LLM 跑出题腿（解析成功
  率、题数、双语完备率、variant 数、案例问题覆盖率）+ 判分腿（用案例 mastered 档案
  预置作答走 `evaluate()`，量化 knowledge_points uncovered 与 needs_ta_review——
  首次把交付文档遗留项「真实 LLM 评价质量未做系统评估」变成可测数字）；同时把每条
  原始输出固化为 `tests/cassettes/{case_id}.json`。
- **`tests/test_cassettes.py`（新）**：`pytest -m cassette` 无网络回放真实输出过完整
  「宽松解析 → 草稿校验 → 装配」链路；无盒带时整层自动跳过，默认 CI 不受影响。
  调用次数与录制不符会显式报错（流水线漂移信号）。

## 3. 第三批：复用 + 并发 + 异步 202

- **前后缀拆分（`app/agent.py`）**：`build_assignment_prefix`（作业上下文，全员字节
  相同，稳定前缀吃 provider 前缀缓存）+ `build_student_suffix`（每生提交）；**学生身份
  移出可克隆载荷**（只在消息头），问卷成为 `(作业, prompt版本, 代码指纹, 输出语言)` 的
  纯函数——这是复用安全的前提。
- **`app/reuse.py`（新）**：内容寻址复用。索引 `generated/reuse-index.json` 直接存
  问卷快照（免疫来源学生后续重生成）；命中即克隆（`reused_from` 盖来源章，学生端
  `public()` 不含该字段无泄露，`questionnaire_id`/`version` 服务端重置）；同指纹并发
  single-flight 合并为一次真实调用。`AA_REUSE=0` 关闭；教师显式重生成走
  `allow_reuse=False`（要新鲜内容）但结果仍写入索引。真实冒烟确认抄作业场景
  **0 次 LLM 调用**。
- **`app/generate_all.py`**：顺序循环改 `ThreadPoolExecutor` 并发（`AA_GEN_CONCURRENCY=4`，
  `--workers` 可覆盖），失败清单显式输出（合规报告），`--no-reuse` 可强制全真实调用。
- **`app/main.py`**：教师重新生成改**异步 202 + 任务轮询**：`POST .../regenerate` 立即
  返回 `GenerationJob`（pending），后台线程执行，`GET .../regenerate/status` 轮询；
  已有 pending 任务时幂等返回同一 job；启动清扫中断任务（`recover_interrupted_generation_jobs`）。
  HTTP 请求不再挂最长 180s 的 LLM 调用。
- **前端**：`TeacherPanel.vue` 重新生成改为「202 提示 → 轮询状态 → 成功/失败/超时
  toast」；`api.js` 增加 `teacherRegenerateStatus`；i18n 增补中英文案；已重新构建并
  同步 `frontend/`。

## 4. 测试与验收

- **pytest 108 通过**（原 75 全绿 + 新增 33：失败分类退避 / 定点修复 / 超产裁剪 /
  能力降级学习 / Mock 混沌注入 / 台账与回放 / provenance / 前后缀拆分 / 复用克隆与
  单飞 / 并发批跑 / 异步任务幂等与恢复）。另有 2 个 cassette 参数化用例随盒带数量增长。
- **真实 LLM 冒烟**（ark-code-latest，`/tmp` 独立根）：`generate_all --force` 6/6 成功、
  全部首试解析成功（attempt=1/3，单次 36~81s，超产裁剪后每人 5 题）；prompt_version
  盖章、台账（原始输出 + convo + 延迟）完整落盘。
- **金标评测**：18 案例 × 出题 + 判分两腿全量跑完（结果见 §5），盒带 18 份固化入库。
- **pytest -m cassette 盒带回归**：36 个参数化用例（18 案例 × 出题/判分两腿）在无网络
  环境重放真实输出通过。
- **浏览器验收**（Mock 模式，独立根）：登录 → 总览 → 学生详情 → 重新生成 → 确认框 →
  「已开始生成…」→ 轮询 → 「Generated v2」；服务端 version=2、history 归档完整、
  任务记录 ok、复用索引 6 条。

## 5. 金标评测发现（首次量化真实 LLM 质量）

全量 18 案例 × 出题 + 判分两腿（ark-code-latest，报告 `docs/eval-report-2026-09-13.md`）：

- **出题腿 18/18 解析成功**，平均 5 题/卷，code_variant 共 28 道；全程仅 1 次首试
  解析失败，由既有重试链路自动恢复。
- **双语完备率 0% → 100%**：评测首跑（prompt 双语为可选项）实测 `question_en` 完备率
  为 0%（EN 界面回退中文的根因）；把 prompt 双语改为硬性要求（prompt_version 更新为
  `pv-16bbbe1c4e22`）后全量重跑，题干/选项英文完备率 **18/18 = 100%**。
- **判分腿 18/18 成功**；mastered 档案（标准答案）作答下 uncovered 22/91（24%）、
  needs_ta_review 18/18、understanding 分布 limited 11 / partial 7——评价器明显偏保守，
  这是判分 prompt 调优的量化基线（调优后用同一 harness 复测即可对比）。
- **target_issue 与案例错误本体词汇 0 命中**（模型自由命名如 array-bounds vs 案例
  本体 out-of-bounds）：印证了方案探索阶段「课程错误本体」方向的价值——约束
  target_issue 词表或建立映射是下一步提高个性化一致性的抓手。
- Ark coding 网关实测：`response_format=json_schema` 未被拒绝（未触发降级），
  `finish_reason` 正常回传（截断闸门有效），响应含 `cached_tokens` 可验证前缀缓存
  命中率（前缀拆分已就位）。

## 6. 已知限制与后续建议

- 判分 prompt 的 uncovered 偏保守（见 §5），可基于台账 + 金标集迭代 evaluator prompt
  并用 `eval_harness` 做 A/B（prompt_version 已可区分）。
- 结构化输出当前是「未拒绝」而非「强制生效」的实证；如需硬保证可把
  `AA_STRUCTURED_MODE=json_schema` 固定并监控 400 告警。
- 限时仍是客户端软约束（服务端只记录），未在本期范围。
- `app/mock_data.py`（504 行死代码）建议后续删除。
