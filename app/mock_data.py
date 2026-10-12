"""Mock 模式预定义数据：6 名模拟学生的诊断问卷 + 通用兜底问卷。

由 MockLLMProvider 使用，保证系统在没有真实 LLM API Key 时也能完整跑通
“诊断 → 个性化问卷 → 评价”闭环。
"""
from __future__ import annotations

# 说明：expected_answer 中选择题使用选项字母（single: "B"; multiple: "A,C"），
# target_issue 与 diagnosis.issues[].type 呼应。

MOCK_QUESTIONNAIRES: dict[str, dict] = {
    "20260001": {
        "diagnosis": {
            "summary": (
                "提交完全正确：最大值初始化、遍历范围 [1, n)、比较方向、返回值均符合要求，"
                "基本掌握该知识点。问卷目标是确认理解并适度加深，不再重复基础边界题。"
            ),
            "issues": [],
        },
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "question": "你的循环从 i = 1 开始而不是 i = 0。为什么这样做是安全的？",
                "choices": [
                    "arr[0] 已被用作最大值的初始值，跳过它只是少一次多余但无害的比较",
                    "i = 0 会导致数组越界",
                    "C 语言规定这种循环必须从 1 开始",
                    "从 1 开始能把比较次数减少一半",
                ],
                "target_issue": "understanding-check",
                "expected_answer": "A",
            },
            {
                "id": "q2",
                "type": "single_choice",
                "question": "该算法对长度为 n 的数组的时间复杂度是多少？",
                "choices": [
                    "O(1)",
                    "O(log n)",
                    "O(n)",
                    "O(n²)",
                ],
                "target_issue": "complexity",
                "expected_answer": "C",
            },
            {
                "id": "q3",
                "type": "single_choice",
                "question": "对于元素排列顺序未知（任意）的数组，以下哪个说法是正确的？",
                "choices": [
                    "存在一种算法，总是只需检查不到 n 个元素就能确定最大值",
                    "任何算法在最坏情况下都必须检查全部 n 个元素才能确定最大值",
                    "用二分查找可以在 O(log n) 内找到最大值",
                    "只需比较数组的第一个和最后一个元素",
                ],
                "target_issue": "algorithm-necessity",
                "expected_answer": "B",
            },
            {
                "id": "q4",
                "type": "text",
                "question": (
                    "题目约定 n >= 1。如果有人违反约定调用 find_max(arr, 0)，你的代码会发生什么？"
                    "为什么这个约定对函数的正确性是必要的？"
                ),
                "choices": None,
                "target_issue": "precondition",
                "expected_answer": (
                    "会执行 int max = arr[0]，但 n=0 意味着数组没有任何元素，arr[0] 是越界访问；"
                    "n>=1 是保证 arr[0] 存在、函数行为有定义的前置条件。"
                ),
            },
        ],
    },
    "20260002": {
        "diagnosis": {
            "summary": (
                "遍历范围与比较方向正确，但最大值初始化为常量 0，隐含了"
                "“数组中一定存在大于 0 的元素”这一未经验证的假设；"
                "当数组全为负数（如 [-5, -2, -10]）时返回 0，而 0 并不在数组中。"
            ),
            "issues": [
                {
                    "type": "initialization",
                    "description": (
                        "最大值初始值取常数 0 而非数组中的元素，对全负数数组必然给出错误结果"
                    ),
                    "evidence": "int max = 0;",
                    "severity": "high",
                },
                {
                    "type": "edge-case",
                    "description": "未考虑全部元素为负数的边界情况，返回值不是数组中的任何元素",
                    "evidence": "输入 [-5, -2, -10] 时返回 0",
                    "severity": "high",
                },
            ],
        },
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "question": "你的程序输入 [-5, -2, -10] 时会返回什么？",
                "choices": ["-2", "-10", "0", "-5"],
                "target_issue": "initialization",
                "expected_answer": "C",
            },
            {
                "id": "q2",
                "type": "single_choice",
                "question": "对上面这个返回结果，最准确的解释是什么？",
                "choices": [
                    "循环少执行了一次，导致漏掉了最大的元素",
                    "最大值的初始值取了一个不在数组中的常数 0，而所有元素都比它小，max 从未被更新过",
                    "比较运算符的方向写反了",
                    "数组下标越界，读到了别的内存",
                ],
                "target_issue": "initialization",
                "expected_answer": "B",
            },
            {
                "id": "q3",
                "type": "multiple_choice",
                "question": "以下哪些输入会让你的程序返回错误结果？（多选）",
                "choices": [
                    "[3, 1, 2]",
                    "[-4, -1, -6]",
                    "[5, -2, 9]",
                    "[-7, -3]",
                ],
                "target_issue": "edge-case",
                "expected_answer": "B,D",
            },
            {
                "id": "q4",
                "type": "text",
                "question": (
                    "在进入循环之前，你会选择什么值作为 max 的初始值？"
                    "请说明理由：为什么这个初始值能保证后续比较对所有输入（包括全负数）都是公平的？"
                ),
                "choices": None,
                "target_issue": "initialization",
                "expected_answer": (
                    "应选择 arr[0]（数组中的某个元素）。因为数组元素本身才是最大值的合法候选，"
                    "用它做初值不会引入任何“数组之外”的假设，对全负数数组同样成立；"
                    "而常数 0 是凭空引入的外部值。"
                ),
            },
        ],
    },
    "20260003": {
        "diagnosis": {
            "summary": (
                "遍历意图、初始化和比较方向都正确，但循环条件写成了 i <= n，"
                "最后一次迭代会访问 arr[n]，超出合法下标范围 [0, n-1]，属于越界读（未定义行为）。"
            ),
            "issues": [
                {
                    "type": "out-of-bounds",
                    "description": (
                        "循环条件 i <= n 导致访问 arr[n]，对长度 n 的数组越界，"
                        "在 C 中是未定义行为，可能返回垃圾值或崩溃"
                    ),
                    "evidence": "for (int i = 1; i <= n; i++)",
                    "severity": "high",
                },
            ],
        },
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "question": "长度为 n 的 C 数组 arr，合法的下标范围是？",
                "choices": ["1 到 n", "0 到 n-1", "0 到 n", "1 到 n-1"],
                "target_issue": "index-range",
                "expected_answer": "B",
            },
            {
                "id": "q2",
                "type": "single_choice",
                "question": "以下哪个循环恰好访问 arr[0] 到 arr[n-1]，不多也不少？",
                "choices": [
                    "for (i = 0; i <= n; i++)",
                    "for (i = 0; i < n; i++)",
                    "for (i = 1; i <= n; i++)",
                    "for (i = 0; i < n - 1; i++)",
                ],
                "target_issue": "loop-boundary",
                "expected_answer": "B",
            },
            {
                "id": "q3",
                "type": "single_choice",
                "question": "当程序读取 arr[n] 时，C 语言标准如何定义这个行为？",
                "choices": [
                    "返回一个随机值，但程序其余部分完全正常",
                    "未定义行为（Undefined Behavior），可能崩溃、可能读到垃圾值，任何结果都可能",
                    "编译器会在运行时自动拦截并报错",
                    "访问会自动回绕到 arr[0]",
                ],
                "target_issue": "undefined-behavior",
                "expected_answer": "B",
            },
            {
                "id": "q4",
                "type": "text",
                "question": (
                    "你的循环条件里 n 前面用的是 <=。请回答：对长度为 n 的数组，"
                    "最后一次合法访问的下标是多少？i 等于多少时循环就必须停止？"
                ),
                "choices": None,
                "target_issue": "loop-boundary",
                "expected_answer": (
                    "最后一次合法访问的下标是 n-1（即 arr[n-1]）；"
                    "当 i == n 时循环必须停止，因为 arr[n] 已经不属于这个数组。"
                ),
            },
        ],
    },
    "20260004": {
        "diagnosis": {
            "summary": (
                "初始化正确、遍历范围正确，但比较条件 arr[i] < max 与目标相反："
                "max 被不断更新为更小的值，函数实际返回的是数组的最小值而不是最大值。"
            ),
            "issues": [
                {
                    "type": "wrong-comparison",
                    "description": (
                        "条件 arr[i] < max 使 max 单调变小，函数语义变成求最小值；"
                        "遍历与初始化本身没有问题"
                    ),
                    "evidence": "if (arr[i] < max)",
                    "severity": "high",
                },
            ],
        },
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "question": "输入 [3, 9, 4, 1] 时，你的程序实际返回什么？",
                "choices": ["9", "4", "1", "3"],
                "target_issue": "wrong-comparison",
                "expected_answer": "C",
            },
            {
                "id": "q2",
                "type": "single_choice",
                "question": (
                    "求最大值的循环中，每轮迭代结束后应当保持的循环不变量（invariant）是什么？"
                ),
                "choices": [
                    "max 是目前已遍历元素中的最小值",
                    "max 是目前已遍历元素中的最大值",
                    "max 等于数组的最后一个元素",
                    "max 始终等于 arr[0]",
                ],
                "target_issue": "loop-invariant",
                "expected_answer": "B",
            },
            {
                "id": "q3",
                "type": "single_choice",
                "question": "为了维持上面的不变量，检查新元素 arr[i] 时应采用哪条更新规则？",
                "choices": [
                    "当 arr[i] < max 时，用 arr[i] 更新 max",
                    "当 arr[i] > max 时，用 arr[i] 更新 max",
                    "每个 arr[i] 都无条件更新 max",
                    "只有 arr[i] == max 时才更新",
                ],
                "target_issue": "wrong-comparison",
                "expected_answer": "B",
            },
            {
                "id": "q4",
                "type": "text",
                "question": (
                    "如果把任务改为求最小值，你会对“更新不变量”和“比较条件”"
                    "做怎样的概念上的调整？请用一句话概括。"
                ),
                "choices": None,
                "target_issue": "transfer-min",
                "expected_answer": (
                    "不变量改为“min 是已遍历元素中的最小值”，比较方向反过来："
                    "当 arr[i] < min 时用 arr[i] 更新 min。"
                ),
            },
        ],
    },
    "20260005": {
        "diagnosis": {
            "summary": (
                "没有使用循环，只比较了 arr[0] 和 arr[n-1] 两个元素，"
                "中间的所有元素全部被忽略，算法不完整；"
                "例如 [2, 10, 5, 3] 会错误地返回 3。"
            ),
            "issues": [
                {
                    "type": "incomplete-traversal",
                    "description": (
                        "仅比较首尾两个元素，未遍历中间元素，"
                        "当最大值不在首尾时结果错误"
                    ),
                    "evidence": "if (arr[n - 1] > max)",
                    "severity": "high",
                },
            ],
        },
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "question": "输入 [2, 10, 5, 3]（n = 4）时，你的程序返回什么？",
                "choices": ["10", "5", "3", "2"],
                "target_issue": "incomplete-traversal",
                "expected_answer": "C",
            },
            {
                "id": "q2",
                "type": "single_choice",
                "question": (
                    "在对数组内容没有任何先验信息的情况下，为什么寻找最大值"
                    "必须逐一检查每个元素？"
                ),
                "choices": [
                    "因为 C 语言语法强制要求这样做",
                    "因为任何没有被检查的元素都可能比当前最大值更大，跳过它就无法保证正确性",
                    "因为多比较能让程序运行得更快",
                    "因为不逐一检查会导致内存泄漏",
                ],
                "target_issue": "traversal-necessity",
                "expected_answer": "B",
            },
            {
                "id": "q3",
                "type": "single_choice",
                "question": "循环结构在这类算法中的核心作用是什么？",
                "choices": [
                    "让代码行数更少",
                    "对每一个候选元素重复执行相同的“比较—更新”规则，保证覆盖全部元素",
                    "把比较次数固定为 2 次",
                    "用于计算数组的长度",
                ],
                "target_issue": "traversal-role",
                "expected_answer": "B",
            },
            {
                "id": "q4",
                "type": "text",
                "question": (
                    "对于数组 [7, 2, 9, 4, 1]，请列出你的程序实际比较过的元素，"
                    "并解释真正的最大值为什么没有被发现。"
                ),
                "choices": None,
                "target_issue": "incomplete-traversal",
                "expected_answer": (
                    "只比较了 arr[0]=7 和 arr[4]=1；最大值 9 在 arr[2]，"
                    "从未参与比较，所以不可能被发现。"
                ),
            },
        ],
    },
    "20260006": {
        "diagnosis": {
            "summary": (
                "最大值的计算逻辑（初始化、遍历、比较）完全正确，"
                "但函数缺少 return 语句，计算出的 max 无法交还给调用者："
                "计算正确，返回机制缺失。不应将其判定为“不会寻找最大值”。"
            ),
            "issues": [
                {
                    "type": "missing-return",
                    "description": (
                        "返回类型为 int 的函数没有 return 语句，调用者拿不到计算结果；"
                        "在调用方使用返回值时这是未定义行为"
                    ),
                    "evidence": "函数体末尾缺少 return max;",
                    "severity": "high",
                },
            ],
        },
        "questions": [
            {
                "id": "q1",
                "type": "single_choice",
                "question": (
                    "C 语言中，返回类型为 int 的函数控制流到达函数末尾却没有执行任何 return，"
                    "且调用者使用了其返回值。标准如何定义这种情况？"
                ),
                "choices": [
                    "自动返回 0",
                    "自动返回函数内最后一个被赋值的变量",
                    "未定义行为，结果不可预测",
                    "编译阶段一定报错，根本无法生成可执行程序",
                ],
                "target_issue": "missing-return",
                "expected_answer": "C",
            },
            {
                "id": "q2",
                "type": "single_choice",
                "question": (
                    "变量 max 在函数结束时确实保存了正确结果，"
                    "为什么调用者的 printf(\"%d\", find_max(arr, n)) 却无法可靠地打印它？"
                ),
                "choices": [
                    "max 是函数的局部变量，只有通过 return 语句才能把它的值交还给调用者",
                    "printf 函数本身有缺陷",
                    "max 没有被初始化",
                    "数组在函数内被修改了",
                ],
                "target_issue": "return-mechanism",
                "expected_answer": "A",
            },
            {
                "id": "q3",
                "type": "single_choice",
                "question": (
                    "若希望调用者能写 int m = find_max(arr, n); 并得到正确结果，函数必须做什么？"
                ),
                "choices": [
                    "在结束前用 return 语句把结果返回给调用者",
                    "把 max 声明为全局变量，否则永远不行",
                    "把结果写入文件再让调用者读取",
                    "什么都不需要做，返回值会自动可用",
                ],
                "target_issue": "return-mechanism",
                "expected_answer": "A",
            },
            {
                "id": "q4",
                "type": "text",
                "question": (
                    "请区分：“在函数内计算出一个值”和“把值交还给调用者”"
                    "分别通过什么机制完成？你的代码完成了前者还是后者？"
                ),
                "choices": None,
                "target_issue": "missing-return",
                "expected_answer": (
                    "前者通过表达式求值和赋值（保存到局部变量 max）完成；"
                    "后者必须通过 return 语句。当前代码只完成了计算，没有把值返回。"
                ),
            },
        ],
    },
}

# 兜底：未知学号（Mock 模式没有预定义诊断）时使用的通用问卷
GENERIC_QUESTIONNAIRE = {
    "diagnosis": {
        "summary": (
            "Mock 模式下该学号没有预定义诊断（不在 6 名模拟学生之列），"
            "生成通用诊断问卷，覆盖本题最常见的三类错误。"
        ),
        "issues": [
            {
                "type": "unknown",
                "description": "未做针对性分析，使用通用问卷",
                "evidence": "",
                "severity": "low",
            }
        ],
    },
    "questions": [
        {
            "id": "q1",
            "type": "single_choice",
            "question": "长度为 n 的 C 数组 arr，合法的下标范围是？",
            "choices": ["1 到 n", "0 到 n-1", "0 到 n", "1 到 n-1"],
            "target_issue": "index-range",
            "expected_answer": "B",
        },
        {
            "id": "q2",
            "type": "single_choice",
            "question": "以下哪个更适合作为“求最大值”循环中 max 的初始值？",
            "choices": [
                "常数 0",
                "arr[0]（数组中的第一个元素）",
                "一个足够大的常数，比如 1000000",
                "不初始化，直接开始比较",
            ],
            "target_issue": "initialization",
            "expected_answer": "B",
        },
        {
            "id": "q3",
            "type": "single_choice",
            "question": (
                "若希望调用者能写 int m = find_max(arr, n); 并得到正确结果，函数必须做什么？"
            ),
            "choices": [
                "在结束前用 return 语句把结果返回给调用者",
                "把 max 声明为全局变量，否则永远不行",
                "把结果写入文件再让调用者读取",
                "什么都不需要做，返回值会自动可用",
            ],
            "target_issue": "return-mechanism",
            "expected_answer": "A",
        },
    ],
}
