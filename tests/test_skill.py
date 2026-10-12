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


unpack, build = load("unpack"), load("build")


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


if __name__ == "__main__":
    unittest.main()
