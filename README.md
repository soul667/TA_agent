# TA Agent：作业 ZIP → 个性化抽查题

这个仓库只做一件事：

1. **助教把作业 ZIP 拖给 Claude**（Blackboard 批量下载包，或任何按学号分的压缩包）
2. **Claude 出题**：读每个学生自己的代码，出 3–5 道只针对他本人代码的抽查题（单选 / 多选 / 简答，附 TA 参考答案）
3. **输入学号看题**：在生成的 `quiz.html` 里输入学号（可只输末几位或姓名），或者直接在对话里报学号

想知道哪些人可能用了 AI，再说一句「排查 AI 线索」，Claude 会另做一个页面 `review.html`：列出**能在代码里逐行核对的线索**
（自己写明用了 AI、与他人代码逐行相同、用了作业禁止的写法、程序与题目无关），每条带真实的代码行和一句中性的追问。
代码风格本身不能证明用了 AI，所以页面给的是「先问谁、问什么」，不是结论。

整个仓库就是一个 Claude 技能：[`.claude/skills/ta-quiz/`](.claude/skills/ta-quiz/SKILL.md)。没有服务器，不需要 API Key，
脚本只用 Python 3.8+ 标准库。

## 怎么用

**Claude Code**：在本仓库目录里打开 Claude Code，把 ZIP 拖进来（或给出路径），说「出题」。出完后说学号即可。
想在任何目录都能用，复制一份到个人技能目录：

```bash
cp -r .claude/skills/ta-quiz ~/.claude/skills/
```

**Claude 网页版 / 桌面版**：把 `.claude/skills/ta-quiz` 文件夹打成 zip，在设置的 Skills 里上传，之后在对话里拖入作业 ZIP 即可。

**先试一下**：用 [`examples/demo.zip`](examples/demo.zip)（7 名学生的 `find_max`，包含几种常见错误和一份没交代码的提交）。

## 产出

都在 `workspace/<压缩包名>/`（已加入 `.gitignore`）：

| 文件 | 说明 |
|---|---|
| `index.md` | 名单：学号、姓名、文件、行数，没交代码的学号 |
| `submissions/<学号>.md` | 该生最新一次提交的代码，带行号 |
| `blueprint.md` | 本次作业的出题口径（考点、超纲边界、常见错误） |
| `papers/<学号>.json` | 该生的题目和参考答案 |
| `quiz.html` | 查询页面：双击打开，首页是全班名单；输入学号看试卷和代码，可显示答案、打印 |
| `reviews/<学号>.json`、`families.json`、`similar.json` | AI 线索：每人的线索与追问、多人共有的写法、代码雷同对 |
| `review.html` | 线索排查页：按「需核实 / 建议追问」排序，可按线索类型筛选，点开看证据代码，可跳到该生的抽查题 |

想换题、改难度、出英文版，直接在对话里说，Claude 改完会重新生成页面。

## 打包与部署

```bash
python3 .claude/skills/ta-quiz/scripts/pack.py site workspace/<作业名>   # → dist/<作业名>-site.zip
python3 .claude/skills/ta-quiz/scripts/pack.py skill                     # → dist/ta-quiz-skill.zip
```

- **站点 zip**：`index.html`（入口）、`quiz.html`、`review.html`，纯静态，字体已嵌在页面里，不联网。解压后丢到任何静态主机
  （Netlify 拖拽、Cloudflare Pages、nginx）即可，本地双击 `index.html` 也能用。
- **技能 zip**：在 Claude 的「设置 → Skills」里上传，之后在对话里拖入作业 ZIP 即可。

站点里有学生的姓名、学号和代码。页面带 noindex、`robots.txt` 禁止收录，但这不是访问控制：请放在需要登录的地方
（Cloudflare Access、Netlify 密码保护、学校内网），不要放到公开的 GitHub Pages。

## 目录

```
.claude/skills/ta-quiz/
├── SKILL.md            出题流程和规则（Claude 照着做）
├── scripts/unpack.py   拆 ZIP → 每人一份带行号的代码（只读文本，不运行学生文件）
├── scripts/similar.py  找出代码雷同的学生对（只看全班罕见的共同行）
├── scripts/build.py    检查题目和线索的格式 → 生成 quiz.html、review.html
├── scripts/pack.py     打包：可部署的静态站点 zip、技能 zip
└── assets/
    ├── quiz.html、review.html   两个页面的模板
    ├── shared.css、shared.js    共用的代码组件（C 语法高亮、行号、被问到的行高亮、复制）和字体栈
    └── fonts/                   嵌进页面的字体子集（思源黑体 + JetBrains Mono，OFL 许可），离线也一致
examples/demo.zip       示例作业包
tests/test_skill.py     python3 -m unittest discover tests
```

页面是单个 HTML 文件，字体直接嵌在里面（约 2MB），在任何电脑、任何查看器里字形都一样，不依赖网络。
字体子集只含 ASCII 和 GB2312 一级汉字，姓名里的生僻字会退回系统的无衬线中文字体。

`workspace/` 里有学生的姓名、学号和代码，不要提交或上传到别处。
