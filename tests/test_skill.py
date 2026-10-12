"""python3 -m unittest discover tests"""
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / ".claude/skills/ta-quiz/scripts"


sys.dont_write_bytecode = True  # 不在技能目录里留下 __pycache__（技能会被整个打包上传）


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


unpack, build, similar = load("unpack"), load("build"), load("similar")


def make_zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, value in entries.items():
            z.writestr(name, value)
    return buf.getvalue()


def bb(sid, file="", stamp="2026-09-14-10-00-00"):
    prefix = f"PA1_{sid}_attempt_{stamp}"
    return prefix + (f"_{file}" if file else ".txt")


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_unpack(self, entries):
        archive = self.tmp / "hw.zip"
        archive.write_bytes(make_zip(entries))
        out = self.tmp / "ws"
        with contextlib.redirect_stdout(io.StringIO()):
            unpack.main([str(archive), "--out", str(out)])
        return out, json.loads((out / "students.json").read_text(encoding="utf-8"))["students"]


class UnpackTest(Case):
    def test_blackboard_nested_zip_encoding_and_missing_code(self):
        nested = make_zip({"src/part3.c": "int main(){return 0;}\n", "part3.exe": b"MZ binary",
                           "../escape.c": "int main(){return 9;}", "gbk.c": "// 中文\nint x;".encode("gb18030")})
        out, students = self.run_unpack({
            bb("12610001"): "Name: 张三 (12610001)\nAssignment: PA1 四则运算\nSubmission Field:\n"
                            "There is no student submission text data for this assignment.\nComments:\n",
            bb("12610001", "part2.c"): '#include <stdio.h>\r\nint main(){puts("Hello");}\r\n',
            bb("12610001", "project.zip"): nested,
            bb("12610002", "shot.png"): b"\x89PNG",
        })
        s = students["12610001"]
        self.assertEqual(s["name"], "张三")
        self.assertEqual([f["name"] for f in s["files"]],
                         ["part2.c", "project.zip/gbk.c", "project.zip/src/part3.c"])
        self.assertIn("中文", s["files"][1]["code"])
        self.assertNotIn("\r", s["files"][0]["code"])
        self.assertTrue(any("不安全" in n for n in s["notes"]))
        self.assertEqual(students["12610002"]["files"], [])
        md = (out / "submissions/12610001.md").read_text(encoding="utf-8")
        self.assertIn("1 | #include <stdio.h>", md)
        self.assertIn(s["fingerprint"], md)
        self.assertNotIn("MZ binary", md)
        self.assertIn("没有可读的代码", (out / "submissions/12610002.md").read_text(encoding="utf-8"))
        index = (out / "index.md").read_text(encoding="utf-8")
        self.assertIn("PA1 四则运算", index)
        self.assertIn("无可读代码：12610002", index)
        self.assertFalse((self.tmp / "escape.c").exists())

    def test_latest_attempt_and_inline_text(self):
        _, students = self.run_unpack({
            bb("12610001", "old.c", stamp="2026-09-12-09-00-00"): "int main(){return 8;}",
            bb("12610001"): "Submission Field:\n<pre>#include &lt;stdio.h&gt;\nint main(){return 0;}</pre>\nComments:\n",
            bb("12610002", "a.c"): '#include <stdio.h>\nint main(){puts("hi");}',
            bb("12610002"): 'Submission Field:\n<p>#include &lt;stdio.h&gt;</p><p>  int main(){puts("hi");}</p>\nComments:\n',
        })
        first = students["12610001"]
        self.assertEqual([f["name"] for f in first["files"]], ["文本框.txt"])
        self.assertTrue(first["files"][0]["code"].startswith("#include <stdio.h>"))
        self.assertTrue(any("只取最新" in n for n in first["notes"]))
        second = students["12610002"]
        self.assertEqual([f["name"] for f in second["files"]], ["a.c"])
        self.assertTrue(any("重复粘贴" in n for n in second["notes"]))

    def test_plain_zip_with_shared_root_folder_and_mac_junk(self):
        _, students = self.run_unpack({
            "PA1_20260914/12345678_张三/main.c": "int main(){}",
            "PA1_20260914/12345678_张三/notes.docx": b"PK",
            "PA1_20260914/李四-87654321.zip": make_zip({"hw/main.py": "print(1)\n"}),
            "__MACOSX/PA1_20260914/12345678_张三/._main.c": b"\x00\x05",
            "PA1_20260914/.DS_Store": b"\x00",
        })
        self.assertEqual(sorted(students), ["12345678", "87654321"])
        self.assertEqual([f["name"] for f in students["12345678"]["files"]], ["main.c"])
        self.assertEqual([f["name"] for f in students["87654321"]["files"]], ["李四-87654321.zip/hw/main.py"])

    def test_same_file_loose_and_inside_zip_is_read_once(self):
        inner = make_zip({"pa2/main.c": "int main(){return 0;}\n", "pa2/extra.c": "int x;\n"})
        _, students = self.run_unpack({
            bb("12610001", "main.c"): "int main(){return 0;}\r\n",
            bb("12610001", "pa2.zip"): inner,
        })
        files = students["12610001"]["files"]
        self.assertEqual([f["name"] for f in files], ["main.c", "pa2.zip/pa2/extra.c"])
        self.assertTrue(any("内容相同" in n for n in students["12610001"]["notes"]))

    def test_zip_without_student_ids_fails_clearly(self):
        archive = self.tmp / "x.zip"
        archive.write_bytes(make_zip({"main.c": "int main(){}"}))
        with self.assertRaises(SystemExit) as ctx:
            unpack.main([str(archive), "--out", str(self.tmp / "ws")])
        self.assertIn("没有识别到任何学号", str(ctx.exception.code))


GOOD = {
    "summary": "正确",
    "questions": [
        {"type": "single", "question": "第 1 行？", "choices": ["a", "b", "c"], "answer": "b", "lines": [1]},
        {"type": "multi", "question": "哪些？", "choices": ["a", "b", "c", "d"], "answer": "C, A",
         "lines": ["main.c:1-2"]},
        {"type": "text", "question": "为什么 `x` 这样写？</script><script>alert(1)</script>", "answer": "要点"},
    ],
}


class BuildTest(Case):
    def setUp(self):
        super().setUp()
        self.ws, students = self.run_unpack({bb("12610001", "main.c"): "int x;\nint main(){return x;}\n",
                                             bb("12610002", "main.c"): "int main(){}\n"})
        self.fp = students["12610001"]["fingerprint"]
        (self.ws / "papers").mkdir()

    def write(self, sid, paper):
        (self.ws / "papers" / f"{sid}.json").write_text(json.dumps(paper, ensure_ascii=False), encoding="utf-8")

    def page_data(self):
        html = (self.ws / "quiz.html").read_text(encoding="utf-8")
        raw = html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
        return json.loads(raw)

    def test_valid_paper_builds_page_with_normalized_answers(self):
        self.write("12610001", {**GOOD, "fingerprint": self.fp})
        report, ok = build.build(self.ws)
        self.assertTrue(ok, report)
        self.assertIn("已出题 1 / 2", report)
        self.assertIn("12610002", report)
        paper = self.page_data()["papers"]["12610001"]
        self.assertEqual(paper["questions"][0]["answer"], "B")
        self.assertEqual(paper["questions"][1]["answer"], "A,C")
        self.assertEqual(paper["questions"][0]["lines"], ["main.c:1"])
        self.assertFalse(paper["stale"])

    def test_student_text_cannot_break_out_of_data_block(self):
        self.write("12610001", {**GOOD, "fingerprint": self.fp})
        build.build(self.ws)
        html = (self.ws / "quiz.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("alert(1)", self.page_data()["papers"]["12610001"]["questions"][2]["question"])

    def test_format_errors_are_reported_and_paper_left_out(self):
        bad = json.loads(json.dumps(GOOD))
        bad["questions"][0]["answer"] = "E"
        bad["questions"][1]["answer"] = "A"
        bad["questions"][1]["lines"] = ["main.c:9"]
        bad["questions"][2]["type"] = "single"
        bad["questions"][2]["choices"] = ["x", "y", "z"]
        bad["questions"][2]["answer"] = "A"
        self.write("12610001", bad)
        self.write("99999999", GOOD)
        (self.ws / "papers/12610002.json").write_text("{oops", encoding="utf-8")
        report, ok = build.build(self.ws)
        self.assertFalse(ok)
        for expected in ("选项字母", "至少两个正确选项", "main.c:9", "至少要有 1 道简答题",
                         "99999999：名单里没有", "12610002：JSON 解析失败"):
            self.assertIn(expected, report)
        self.assertEqual(self.page_data()["papers"], {})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.main([str(self.ws)]), 1)

    def test_too_few_questions_and_stale_fingerprint(self):
        self.write("12610001", {**GOOD, "questions": GOOD["questions"][:2]})
        self.assertIn("要 3–5 道题", build.build(self.ws)[0])
        self.write("12610001", {**GOOD, "fingerprint": "old"})
        report, ok = build.build(self.ws)
        self.assertTrue(ok)
        self.assertIn("可能过期：12610001", report)
        self.assertTrue(self.page_data()["papers"]["12610001"]["stale"])


class ReviewTest(Case):
    """AI 线索排查：reviews/、families.json、similar.py 与 review.html。"""

    def setUp(self):
        super().setUp()
        self.ws, students = self.run_unpack({
            bb("12610001", "main.c"): "int main(){\n  return 0;\n}\n",
            bb("12610002", "main.c"): "int main(){\n  return 1;\n}\n",
            bb("12610003", "shot.png"): b"\x89PNG",
        })
        self.fp = students["12610001"]["fingerprint"]
        (self.ws / "reviews").mkdir()

    def write(self, sid, review):
        (self.ws / "reviews" / f"{sid}.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    def page(self):
        html = (self.ws / "review.html").read_text(encoding="utf-8")
        raw = html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
        return json.loads(raw)

    def test_valid_review_builds_page_with_families_and_similar(self):
        self.write("12610001", {"fingerprint": self.fp, "priority": "verify", "summary": "自述用了 AI",
                                "signals": [{"kind": "disclosed", "ref": "main.c:2", "observation": "注释写明用了 AI", "ask": "能解释吗？"}]})
        self.write("12610002", {"fingerprint": "old", "priority": "normal", "summary": "没有线索", "signals": []})
        (self.ws / "families.json").write_text(json.dumps([{"title": "同一写法", "strength": "weak", "note": "说明", "members": ["12610001"]}]))
        (self.ws / "similar.json").write_text("[]")
        report, ok = build.build(self.ws)
        self.assertTrue(ok, report)
        self.assertIn("需核实 1", report)
        page = self.page()
        self.assertEqual(page["reviews"]["12610001"]["signals"][0]["ref"], "main.c:2")
        self.assertTrue(page["reviews"]["12610002"]["stale"])
        self.assertEqual(len(page["families"]), 1)
        self.assertEqual(page["students"]["12610003"]["files"], [])

    def test_review_format_errors_are_reported(self):
        self.write("12610001", {"priority": "verify", "summary": "x", "signals": []})
        self.write("12610002", {"priority": "maybe", "summary": "x", "signals": []})
        report, ok = build.build(self.ws)
        self.assertFalse(ok)
        self.assertIn("提高追问优先级必须给出具体的代码线索", report)
        self.assertIn("priority 只能是", report)
        self.write("12610001", {"priority": "follow_up", "summary": "x", "signals": [
            {"kind": "guess", "ref": "main.c:99", "observation": "", "ask": "?"}]})
        self.write("12610002", {"priority": "normal", "summary": "ok"})
        report, ok = build.build(self.ws)
        for expected in ("kind 只能是", "行号引用无效", "observation 为空"):
            self.assertIn(expected, report)

    def test_families_must_name_real_students(self):
        self.write("12610001", {"fingerprint": self.fp, "priority": "normal", "summary": "ok"})
        (self.ws / "families.json").write_text(json.dumps([
            {"title": "t", "strength": "huge", "note": "n", "members": ["12610001", "99999999"]}]))
        report, ok = build.build(self.ws)
        self.assertFalse(ok)
        self.assertIn("strength 只能是", report)
        self.assertIn("99999999", report)

    def test_similar_uses_rare_shared_lines_not_formatting(self):
        common = "int main() {\n  printf(\"hi\\n\");\n  return 0;\n}\n"
        quirk = "  printf(\"The space occupied by long is %d bytes\\n\", -((int)&b - (int)&c));\n"
        def student(extra):
            code = "#include <stdio.h>\nlong b; int c;\n" + common.replace("  return 0;", extra + "  return 0;")
            return {"files": [{"name": "calc_long.c", "code": code}]}
        students = {sid: student(quirk if sid in ("1", "2") else "  printf(\"x%d\\n\", %s);\n" % (i, i))
                    for i, sid in enumerate("123456")}
        students["2"]["files"][0]["code"] = students["2"]["files"][0]["code"].replace("  printf", "      printf")  # 只改缩进
        students["1"]["files"][0]["code"] += "// extra line one\n// extra line two\n// extra line three\n"
        students["2"]["files"][0]["code"] += "// extra line one\n// extra line two\n// extra line three\n"
        pairs = similar.compare(students, min_shared=3, rare=2)
        self.assertEqual([(p["a"], p["b"]) for p in pairs], [("1", "2")])
        self.assertGreaterEqual(pairs[0]["shared_count"], 3)
        # 全班都写过的行不算证据
        self.assertEqual(similar.compare(students, min_shared=3, rare=1), [])


class PageTest(Case):
    """页面生成：字体和共享代码组件被嵌入，学生代码里的占位符字样不会破坏页面。"""

    def test_fonts_and_shared_code_are_embedded_and_data_is_safe(self):
        code = '/*__FONTS__*/\nchar *s = "__DATA__ </script> //__SHARED_JS__";\nint main(){}\n'
        ws, students = self.run_unpack({bb("12610001", "main.c"): code})
        (ws / "papers").mkdir()
        paper = {**GOOD, "fingerprint": students["12610001"]["fingerprint"]}
        (ws / "papers/12610001.json").write_text(json.dumps(paper, ensure_ascii=False), encoding="utf-8")
        (ws / "reviews").mkdir()
        report, ok = build.build(ws)
        self.assertTrue(ok, report)
        for name in ("quiz.html", "review.html"):
            html = (ws / name).read_text(encoding="utf-8")
            template, rest = html.split('<script id="data" type="application/json">')
            data, scripts = rest.split("</script>", 1)
            for marker in ("/*__SHARED_CSS__*/", "//__SHARED_JS__", "/*__FONTS__*/", "__DATA__"):
                self.assertNotIn(marker, template + scripts, f"{name} 里还有未替换的占位符 {marker}")
            self.assertEqual(html.count("@font-face"), 5, name)
            self.assertIn('font-family:"TA Sans"', html)
            self.assertIn("function codeBlock", html)
            self.assertIn("function highlightC", html)
            self.assertEqual(json.loads(data)["students"]["12610001"]["files"][0]["code"], code.replace("\r\n", "\n"))
            self.assertNotIn("</script> //__SHARED_JS__", html)   # 学生代码里的 </script> 被转义，不会提前结束数据块
            self.assertIn("__DATA__", data)                      # 学生代码里的占位符字样原样保留在数据里

    def test_all_font_files_exist(self):
        assets = SCRIPTS.parent / "assets"
        for stem in build.FONTS:
            self.assertGreater((assets / "fonts" / f"{stem}.woff2").stat().st_size, 1000, stem)
        self.assertTrue((assets / "fonts" / "LICENSE-OFL.txt").exists())


if __name__ == "__main__":
    unittest.main()
