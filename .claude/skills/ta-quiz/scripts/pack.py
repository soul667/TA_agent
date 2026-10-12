#!/usr/bin/env python3
"""打包成 zip：

  python3 pack.py site <ws> [--out 文件.zip]   可直接部署的静态站点：index.html、quiz.html、review.html
                                               解压后放到任何静态主机（Netlify、Cloudflare Pages、nginx、学校服务器）即可
  python3 pack.py skill [--out 文件.zip]       本技能自己的压缩包，在 Claude 的「设置 → Skills」里上传

站点里有学生的姓名、学号和代码：页面带 noindex、robots.txt 禁止收录，但这不是访问控制。
部署时请放在需要登录的地方（Cloudflare Access、Netlify 密码保护、学校内网），不要放到公开的 GitHub Pages。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import zipfile
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
NOINDEX = '<meta name="robots" content="noindex, nofollow">'

INDEX = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<style>
:root {{ --bg: #eceff4; --card: #fff; --ink: #17202b; --ink-2: #5a6878; --line: #dce2ea; --accent: #2448a3; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #0d1117; --card: #151b23; --ink: #e6edf3; --ink-2: #9aa7b6; --line: #28313c; --accent: #8fb0ff; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: var(--bg); color: var(--ink);
  font: 15px/1.7 "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif; }}
main {{ width: min(100%, 520px); }}
p.eyebrow {{ margin: 0 0 6px; font-size: 12px; letter-spacing: .12em; color: var(--ink-2); }}
h1 {{ margin: 0 0 22px; font-size: 26px; line-height: 1.3; }}
a.card {{ display: block; margin-top: 12px; padding: 16px 18px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; color: inherit; text-decoration: none; }}
a.card:hover {{ border-color: var(--accent); }}
a.card b {{ display: block; font-size: 17px; }}
a.card span {{ color: var(--ink-2); font-size: 13.5px; }}
</style>
</head>
<body>
<main>
<p class="eyebrow">课堂抽查</p>
<h1>{title}</h1>
<a class="card" href="quiz.html"><b>个性化抽查题</b><span>输入学号查看该生的题目、参考答案和代码</span></a>
{review}</main>
</body>
</html>
"""
REVIEW_CARD = '<a class="card" href="review.html"><b>线索排查</b><span>自述用 AI、代码雷同、超出作业范围的写法，每条带代码和追问</span></a>\n'

README = """部署说明
========

这个文件夹是纯静态页面，没有后端、不需要数据库，也不需要联网加载任何资源（字体已嵌在页面里）。

  index.html   入口，链接到下面两个页面
  quiz.html    个性化抽查题：输入学号查看
  review.html  线索排查页（如果有）

部署：把整个文件夹放到任何静态主机即可。
  - Netlify：把文件夹拖进 app.netlify.com/drop
  - Cloudflare Pages：创建项目 → Direct Upload
  - 自己的服务器：解压到 nginx / Apache 的网站目录
  - 本地：直接双击 index.html 也能用

隐私（重要）：
  页面里有学生的姓名、学号和代码。页面带了 noindex，robots.txt 也禁止收录，但这不是访问控制。
  请放在需要登录的地方（Cloudflare Access、Netlify 密码保护、学校内网）。
  不要放到公开的 GitHub Pages 或没有任何保护的公网地址。
"""
HEADERS = "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n  X-Content-Type-Options: nosniff\n"


def noindex(page: str) -> str:
    return page if NOINDEX in page else page.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n' + NOINDEX, 1)


def site(ws: Path, out: Path) -> list[str]:
    if not (ws / "quiz.html").exists():
        sys.exit(f"{ws} 里还没有 quiz.html，请先运行：python3 {SKILL / 'scripts/build.py'} {ws}")
    data = json.loads((ws / "students.json").read_text(encoding="utf-8"))
    title = html.escape(data.get("assignment") or data["archive"].rsplit(".", 1)[0])
    has_review = (ws / "review.html").exists()
    files = {"index.html": INDEX.format(title=title, review=REVIEW_CARD if has_review else ""),
             "quiz.html": noindex((ws / "quiz.html").read_text(encoding="utf-8")),
             "robots.txt": "User-agent: *\nDisallow: /\n", "_headers": HEADERS, "部署说明.txt": README}
    if has_review:
        files["review.html"] = noindex((ws / "review.html").read_text(encoding="utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return list(files)


def skill(out: Path) -> list[str]:
    names = []
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(SKILL.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                arc = f"{SKILL.name}/{path.relative_to(SKILL).as_posix()}"
                z.write(path, arc)
                names.append(arc)
    return names


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="what", required=True)
    a = sub.add_parser("site")
    a.add_argument("workspace", type=Path)
    a.add_argument("--out", type=Path)
    b = sub.add_parser("skill")
    b.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.what == "site":
        out = args.out or Path("dist") / f"{args.workspace.resolve().name}-site.zip"
        names = site(args.workspace, out)
    else:
        out = args.out or Path("dist") / f"{SKILL.name}-skill.zip"
        names = skill(out)
    print(f"{out}（{out.stat().st_size / 1024 / 1024:.1f} MB，{len(names)} 个文件）")


if __name__ == "__main__":
    main()
