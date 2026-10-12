"""FastAPI 应用：学生端问卷 API + 教师端查看/复核 API + 静态页面路由。

本版（Vue / Mock 修订版）关键行为（docs/evaluation-rules.md）：
- 所有 /api/* 均需认证：教师口令或学生本人访问码，归属不符 403；
- 提交幂等（submission_token）、旧问卷版本提交 409；
- 评价状态与提交状态分离保存：评价失败不丢答案、不显示旧结论；
- 重新生成问卷 = 新版本，历史版本与历次作答完整保留；
- 学生可查询作答回执 / 状态 / 已发布反馈；教师可复核并发布反馈；
- 问卷再生成走异步 202 + 任务轮询（第三批），HTTP 请求不再挂住 LLM 调用；
- 启动时恢复中断的评价记录与生成任务（pending → failed[interrupted]，可重试）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, cases, reuse, storage
from .agent import generate_questionnaire
from .auth import access_code_enabled
from .evaluator import evaluate
from .llm import get_provider
from .source import paper_source_fingerprint as _source_fingerprint
from .grading import router as grading_router
from .answers import answer_receipt, current_paper_answer
from .models import (
    UNDERSTANDING_ZH,
    AttemptData,
    EvaluationRecord,
    GenerationJob,
    ResponseFile,
    ReviewRecord,
    now_utc,
)

log = logging.getLogger("adaptive_assessment")

FRONTEND_DIR = Path(os.getenv("AA_FRONTEND_DIR", str(storage.DEFAULT_ROOT / "frontend")))
SID_PATTERN = re.compile(r"^\d{4,20}$")

# ---- 提交防滥用（安全修复 R2：刷分 / DoS / 存储与 LLM 成本放大）----
MAX_ATTEMPTS_PER_VERSION = int(os.getenv("AA_MAX_ATTEMPTS_PER_VERSION", "5"))
SUBMIT_PER_MINUTE = int(os.getenv("AA_SUBMIT_PER_MINUTE", "10"))
OVERTIME_GRACE_SECONDS = int(os.getenv("AA_OVERTIME_GRACE_SECONDS", "600"))
MAX_TEXT_ANSWER_CHARS = 8000       # 单题文本答案上限
MAX_ANSWERS_TOTAL_CHARS = 32_000   # 全卷答案总量上限
TIME_LIMIT_GRACE_SECONDS = 120     # 服务端判定超时的固定宽限（网络/自动提交延迟）


def _resubmit_after_feedback_allowed() -> bool:
    """评价出分后是否仍允许重交。默认**不允许**（防“看反馈改答案”刷分）；
    AA_RESUBMIT_AFTER_FEEDBACK=1 恢复旧的开放闭环（课堂演示 / 教学重练场景）。"""
    return os.getenv("AA_RESUBMIT_AFTER_FEEDBACK", "0").strip().lower() in ("1", "true", "yes")

_submit_times: dict[str, deque] = defaultdict(deque)
_submit_times_lock = threading.Lock()


def _check_submit_rate(student_id: str) -> None:
    """每学生提交频率限制（幂等重试不计入；评价重试接口不走此处）。"""
    now = time.monotonic()
    with _submit_times_lock:
        wins = _submit_times[student_id]
        while wins and now - wins[0] > 60.0:
            wins.popleft()
        if len(wins) >= SUBMIT_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail="提交过于频繁，请稍后再试",
                headers={"Retry-After": "10"},
            )
        wins.append(now)


def _check_answer_size(answers: dict) -> None:
    total = 0
    for value in answers.values():
        n = len(str(value))
        if n > MAX_TEXT_ANSWER_CHARS:
            raise HTTPException(
                status_code=422, detail=f"文本答案过长（单题上限 {MAX_TEXT_ANSWER_CHARS} 字符）"
            )
        total += n
    if total > MAX_ANSWERS_TOTAL_CHARS:
        raise HTTPException(
            status_code=422, detail=f"答案总量过大（全卷上限 {MAX_ANSWERS_TOTAL_CHARS} 字符）"
        )


def _normalize_text_answer(value: str) -> str:
    """协同作弊比对用的规范化：去空白、转小写（标点保留——完全一致的标点更可疑）。"""
    return "".join(str(value).split()).lower()


def _detect_collusion(student_id: str, answers: dict) -> list[str]:
    """安全修复 R7：文本作答（≥20 个有效字符）与其他学生历史作答完全一致 → 疑似协同作弊。

    只做精确匹配（规范化后），命中即标记转人工复核，不自动扣分。个性化问卷下
    长文本逐字一致而各自独立作答的概率极低；命中相同代码指纹（复用克隆问卷）的
    学生互抄是本检测的主要目标。
    """
    mine = {
        _normalize_text_answer(v)
        for v in answers.values()
        if isinstance(v, str) and len(_normalize_text_answer(v)) >= 20
    }
    if not mine:
        return []
    matched = []
    for submission in storage.list_submissions():
        other = submission.student_id
        if other == student_id:
            continue
        response = storage.load_response(other)
        if response is None:
            continue
        for attempt in response.attempts:
            for value in attempt.answers.values():
                if not isinstance(value, str):
                    continue
                if _normalize_text_answer(value) in mine:
                    if other not in matched:
                        matched.append(other)
    return matched


def _server_time_check(student_id: str, questionnaire) -> tuple[bool, int | None]:
    """服务端限时（安全修复 R2）：以首次打开问卷的时间为准，客户端上报不可信。

    返回 (time_expired 服务端判定, elapsed_seconds)。无会话记录（如直接 POST 的
    测试 / 老数据）不判超时。超过 限时+宽限+硬宽限 的提交直接 409。
    """
    limit = questionnaire.time_limit_minutes
    if limit <= 0:
        return False, None
    session = storage.load_exam_session(student_id)
    if not session or session.get("questionnaire_id") != questionnaire.questionnaire_id:
        return False, None
    opened = session.get("opened_at")
    if not opened:
        return False, None
    try:
        opened_dt = datetime.fromisoformat(opened)
    except ValueError:
        return False, None
    elapsed = int((now_utc() - opened_dt).total_seconds())
    expired = elapsed > limit * 60 + TIME_LIMIT_GRACE_SECONDS
    if elapsed > limit * 60 + TIME_LIMIT_GRACE_SECONDS + OVERTIME_GRACE_SECONDS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"问卷限时 {limit} 分钟，提交已超出宽限期，不再受理；如确有网络故障请联系助教"
            ),
        )
    return expired, elapsed


@asynccontextmanager
async def lifespan(app: FastAPI):
    recovered = storage.recover_interrupted_evaluations()
    if recovered:
        log.warning("启动恢复：%d 条中断的评价记录已标记为 failed(interrupted)", recovered)
    jobs = storage.recover_interrupted_generation_jobs()
    if jobs:
        log.warning("启动恢复：%d 个中断的问卷生成任务已标记为 failed", jobs)
    yield


# 安全修复 R1：API 文档端点（/docs /redoc /openapi.json）默认关闭，显式 AA_DOCS=1 开启，
# 避免向未认证方暴露完整接口清单（攻击面侦察成本降为零）
_docs_enabled = os.getenv("AA_DOCS", "").strip().lower() in ("1", "true", "yes")

app = FastAPI(
    title="个性化诊断问卷系统",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.include_router(grading_router)


# ---------------- helpers ----------------

def _check_sid(student_id: str) -> None:
    if not SID_PATTERN.match(student_id):
        raise HTTPException(status_code=400, detail="学号格式不正确（应为 4~20 位数字）")


def _get_submission_or_404(student_id: str):
    submission = storage.load_submission(student_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="学号不存在，请检查输入")
    return submission


def _get_questionnaire_or_404(student_id: str):
    questionnaire = storage.load_questionnaire(student_id)
    if questionnaire is None:
        raise HTTPException(status_code=404, detail="问卷尚未生成，请稍后再试或联系助教")
    return questionnaire


def _get_response_or_404(student_id: str):
    response = storage.load_response(student_id)
    if response is None or not response.attempts:
        raise HTTPException(status_code=404, detail="该学生尚未提交问卷")
    return response


def _questionnaire_for_attempt(student_id: str, attempt: AttemptData):
    """评价必须使用作答所属版本的问卷（重生成后旧作答不迁移，P0）。"""
    return (
        storage.load_questionnaire(student_id, attempt.questionnaire_version)
        or _get_questionnaire_or_404(student_id)
    )


def _run_evaluation(student_id: str, attempt: AttemptData) -> EvaluationRecord:
    """对某个 attempt 运行（或重试）评价：只追加评价记录，绝不新增作答。

    调用方需持有该学生的文件锁。答案已在提交时落盘，评价失败不回滚作答。
    """
    submission = _get_submission_or_404(student_id)
    questionnaire = _questionnaire_for_attempt(student_id, attempt)
    record = EvaluationRecord(
        student_id=student_id,
        attempt_id=attempt.attempt_id,
        attempt_no=attempt.attempt_no,
        questionnaire_id=questionnaire.questionnaire_id,
        questionnaire_version=questionnaire.version,
        status="pending",
        started_at=now_utc(),
    )
    storage.append_evaluation_record(record)
    try:
        assignment = storage.load_assignment()
        provider = get_provider()
        response_view = ResponseFile(
            student_id=student_id,
            submitted_at=attempt.submitted_at,
            answers=attempt.answers,
            attempt_count=attempt.attempt_no,
        )
        evaluation = evaluate(provider, assignment, submission, questionnaire, response_view)
        evaluation.attempt_id = attempt.attempt_id
        evaluation.based_on_attempt = attempt.attempt_no
        # 防作弊（安全修复 R7）：疑似协同作弊的作答一律转人工复核
        if getattr(attempt, "collusion_suspect", False):
            evaluation.needs_ta_review = True
            evaluation.basis = (evaluation.basis + "+collusion-suspect").strip("+")
        record.status = "ok"
        record.result = evaluation
    except Exception as e:  # noqa: BLE001 —— 评价失败必须落盘为 failed，不得影响已存答案
        log.error("evaluation failed for %s attempt %s: %s", student_id, attempt.attempt_id, e)
        record.status = "failed"
        record.error = str(e)[:500]
    record.finished_at = now_utc()
    storage.update_evaluation_record(record)
    attempt.evaluation_status = record.status
    return record


def _write_back_eval_status(student_id: str, attempt_id: str, status: str) -> None:
    """把评价状态回写到磁盘作答。锁纪律由调用方负责：同步调用方已在
    student_lock 内直接执行；异步线程包装器自行加锁，避免嵌套 flock 死锁。"""
    response = storage.load_response(student_id)
    if response is None:
        return
    stored = storage.find_attempt(response, attempt_id)
    if stored is not None:
        stored.evaluation_status = status
        storage.save_response(response)


# 在飞的异步评价线程：conftest 在还原 AA_ROOT 前 join，防止迟到写穿隔离根
_eval_workers: set = set()


def _spawn_evaluation(student_id: str, attempt: AttemptData) -> None:
    """提交后异步评价：线程内完成评价与状态回写，请求路径零等待。"""

    def _target():
        try:
            record = _run_evaluation(student_id, attempt)
            status = record.status
        except Exception:  # noqa: BLE001 —— 线程内兜底：不留悬挂 pending
            log.exception("async evaluation crashed for %s", student_id)
            status = "failed"
        finally:
            # 安全修复 R2：线程集合只增不减会缓慢泄漏，结束即摘除
            _eval_workers.discard(threading.current_thread())
        with storage.student_lock(student_id):
            _write_back_eval_status(student_id, attempt.attempt_id, status)

    t = threading.Thread(target=_target, daemon=True, name=f"eval-{student_id}")
    _eval_workers.add(t)
    t.start()


def _receipt(response: ResponseFile, attempt: AttemptData) -> dict:
    return {
        "status": "submitted",
        "student_id": response.student_id,
        "attempt": attempt.attempt_no,
        "attempt_id": attempt.attempt_id,
        "submitted_at": attempt.submitted_at.isoformat(),
        "questionnaire_id": attempt.questionnaire_id,
        "questionnaire_version": attempt.questionnaire_version,
        "evaluation_status": attempt.evaluation_status,
    }


# ---------------- 页面路由（Vue 构建产物，SPA 壳） ----------------

SPA_SHELL_PATHS = ("/", "/exam", "/teacher", "/student", "/student/exam", "/student/result")


def _spa_shell() -> FileResponse:
    # SPA 壳永远回源校验：构建换版后浏览器立刻拿到新哈希资源，不再吃旧缓存
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})

@app.get("/", include_in_schema=False)
def index_page():
    return _spa_shell()


@app.get("/exam", include_in_schema=False)
def exam_page():
    return _spa_shell()


@app.get("/teacher", include_in_schema=False)
@app.get("/teacher/manage", include_in_schema=False)
@app.get("/ta", include_in_schema=False)
def teacher_page():
    return _spa_shell()


@app.get("/ta/{student_id}", include_in_schema=False)
def grading_page(student_id: str):
    return _spa_shell()


@app.get("/student", include_in_schema=False)
def student_page():
    return _spa_shell()


@app.get("/student/exam", include_in_schema=False)
def student_exam_page():
    return _spa_shell()


@app.get("/student/result", include_in_schema=False)
def student_result_page():
    return _spa_shell()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FRONTEND_DIR / "favicon.svg")


# ---------------- 元信息 ----------------

@app.get("/api/meta")
def meta():
    assignment = storage.load_assignment()
    provider = get_provider()
    return {
        "assignment_id": assignment.id,
        "demo_mode": provider.name == "mock",
        "provider": provider.name,
        "course": assignment.course,
        "course_en": assignment.course_en,
        "title": assignment.title,
        "title_en": assignment.title_en,
        "access_code_enabled": access_code_enabled(),
    }


# ---------------- 扫码答题二维码（教师面板） ----------------

def _primary_lan_ip() -> str:
    """UDP connect 只参与选路由、不实际发包：返回本机对外网卡 IPv4；失败回空串。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


@app.get("/api/qr/exam")
def exam_entry_qr(request: Request):
    """教师面板展示的答题入口二维码：手机扫码直达 /student 登录页。

    二维码目标必须用本机局域网 IP——教师常以 localhost 打开面板，
    回环地址在学生手机上不可达；端口跟随当前请求的 Host。
    """
    auth.require_teacher(request)
    import qrcode
    import qrcode.image.svg

    host = (request.headers.get("host") or "127.0.0.1:8000").strip()
    if host.startswith("["):                                  # [::1]:8000
        port = host.split("]:", 1)[1] if "]:" in host else "80"
        host_no_port = host.split("]", 1)[0].lstrip("[")
    elif ":" in host:
        host_no_port, port = host.split(":", 1)
    else:
        host_no_port, port = host, "80"
    lan_ip = _primary_lan_ip() or host_no_port
    url = f"http://{lan_ip}:{port}/student"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=12, border=3)  # 静区≥3 模块，保证手机相机容错
    svg = img.to_string().decode("utf-8")
    return {"url": url, "lan_ip": lan_ip, "port": port, "svg": svg}


MAX_REQUEST_BODY_BYTES = int(os.getenv("AA_MAX_BODY_BYTES", str(1024 * 1024)))  # 1MB


class RequestBodyLimitMiddleware:
    """安全修复 R5：请求体大小上限（含 chunked 传输），防认证用户内存 DoS。

    纯 ASGI 实现：带 1MB 上限完整收包后向下游重放；超限直接回 413。
    （不用 BaseHTTPMiddleware：其 call_next 捕获原始 receive，body 重注入不生效。）
    """

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        content_length = headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > self.max_bytes:
            await self._reject(send)
            return
        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                messages.append(message)
                if total > self.max_bytes:
                    await self._reject(send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return
            else:
                messages.append(message)
        replay_iter = iter(messages)

        async def replay():
            return next(replay_iter, {"type": "http.request", "body": b"", "more_body": False})

        await self.app(scope, replay, send)

    async def _reject(self, send) -> None:
        body = json.dumps({"detail": "请求体过大"}, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


app.add_middleware(RequestBodyLimitMiddleware)

# 安全修复 R6：Host 头白名单（防 DNS rebinding——恶意网页把域名解析到 127.0.0.1
# 后同源读取本地 API）。默认放行本机与私网地址；对外/局域网部署用 AA_ALLOWED_HOSTS
# 显式指定（逗号分隔，支持通配，"*" 全放行）。
_DEFAULT_HOSTS = (
    "localhost,127.0.0.1,[::1],::1,0.0.0.0,"
    "192.168.*,10.*,172.16.*,172.17.*,172.18.*,172.19.*,172.20.*,"
    "172.21.*,172.22.*,172.23.*,172.24.*,172.25.*,172.26.*,172.27.*,"
    "172.28.*,172.29.*,172.30.*,172.31.*"
)
_raw_hosts = os.getenv("AA_ALLOWED_HOSTS", "").strip()
_ALLOWED_HOSTS = [
    h.strip()
    for h in (_raw_hosts.split(",") if _raw_hosts else _DEFAULT_HOSTS.split(","))
    if h.strip()
] or ["*"]


def _host_allowed(host_header: str) -> bool:
    """支持 '*'、精确名与前缀通配（如 192.168.*）。端口与 IPv6 括号先归一化。"""
    host = (host_header or "").strip().lower()
    if host.startswith("["):                     # [::1]:8000
        host = host.split("]")[0].lstrip("[")
    else:                                        # 127.0.0.1:8000
        host = host.split(":")[0]
    for pattern in _ALLOWED_HOSTS:
        if pattern == "*" or host == pattern:
            return True
        if pattern.endswith("*") and host.startswith(pattern[:-1]):
            return True
    return False


class HostAllowListMiddleware:
    """安全修复 R6：Host 头白名单（防 DNS rebinding 同源读取本地 API）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            host = ""
            for key, value in scope.get("headers", []):
                if key == b"host":
                    host = value.decode("latin-1")
                    break
            if not _host_allowed(host):
                body = json.dumps(
                    {"detail": "Host 不在白名单：如属正常访问，请配置 AA_ALLOWED_HOSTS"},
                    ensure_ascii=False,
                ).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


app.add_middleware(HostAllowListMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """安全修复 R3/R5：基础 HTTP 安全响应头 + API 响应禁止缓存。

    - X-Content-Type-Options: nosniff —— 防 MIME 嗅探；
    - X-Frame-Options: DENY —— 防教师/学生页面被 iframe 嵌套（点击劫持）；
    - Referrer-Policy: no-referrer —— 凭据/路径不随外链外泄；
    - CSP：脚本与连接仅限同源（Vite 产物无内联脚本；样式放开 inline 供组件库使用）。
      script-src 保留 'unsafe-eval'：vue-i18n 的运行时消息编译器用 new Function 生成
      翻译函数（intlify 已知约束），严格 CSP 会让所有用到 t() 的组件渲染成空白。
      长期方案是 @intlify/unplugin-vue-i18n 构建期预编译消息后再收紧。
    - Cache-Control: no-store（仅 /api/*）—— 问卷/反馈不经浏览器或共享缓存落盘。
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ---------------- 学生端 API ----------------

@app.get("/api/exam/{student_id}")
def get_exam(student_id: str, request: Request):
    """返回面向学生的问卷 + 本人原始代码（不含 diagnosis / expected_answer / target_issue）。"""
    auth.require_student_access(student_id, request)
    _check_sid(student_id)
    submission = _get_submission_or_404(student_id)
    questionnaire = _get_questionnaire_or_404(student_id)
    assignment = storage.load_assignment()
    payload = questionnaire.public()
    payload["course"] = assignment.course
    payload["course_en"] = assignment.course_en
    payload["title"] = assignment.title
    payload["title_en"] = assignment.title_en
    # 题面与要求：供答题页代码栏「查看题目」弹层（不含 reference_answer / rubric）
    payload["assignment_prompt"] = assignment.prompt
    payload["assignment_requirements"] = assignment.requirements
    payload["code"] = submission.code
    payload["language"] = submission.language
    payload["submitted_at"] = submission.submitted_at
    payload["demo_mode"] = questionnaire.generated_by == "mock"
    payload["time_limit_minutes"] = questionnaire.time_limit_minutes
    if not questionnaire.case_id:
        payload["demo_notice"] = "演示规则未覆盖该代码：诊断与评价均为通用内容，待人工核验。"
        payload["demo_notice_en"] = (
            "This code is outside the demo rules: diagnosis and evaluation are generic "
            "and await manual review."
        )
    existing = storage.load_response(student_id)
    payload["attempt_count"] = existing.attempt_count if existing else 0
    payload["has_submitted"] = bool(existing and existing.attempts)
    # 服务端计时起点：首次打开该版本问卷时落盘，刷新页面不重置（安全修复 R2）
    session = storage.load_exam_session(student_id)
    if not session or session.get("questionnaire_id") != questionnaire.questionnaire_id:
        storage.save_exam_session(student_id, {
            "student_id": student_id,
            "questionnaire_id": questionnaire.questionnaire_id,
            "version": questionnaire.version,
            "opened_at": now_utc().isoformat(),
        })
    return payload


class SubmitBody(BaseModel):
    answers: dict
    questionnaire_version: int | None = None   # 客户端所答问卷版本；与当前不符 → 409
    submission_token: str = ""                 # 提交请求唯一标识；重复请求返回同一回执
    time_expired: bool = False                 # 客户端倒计时归零后的自动提交标记
    duration_seconds: int | None = None        # 客户端作答用时（秒，仅记录）


@app.post("/api/exam/{student_id}/submit")
def submit_exam(student_id: str, body: SubmitBody, request: Request):
    auth.require_student_access(student_id, request)
    _check_sid(student_id)
    submission = _get_submission_or_404(student_id)
    questionnaire = _get_questionnaire_or_404(student_id)

    try:
        answers = questionnaire.normalize_answers(body.answers)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"答案格式有误: {e}")
    _check_answer_size(answers)

    with storage.student_lock(student_id):
        # P0：旧版本问卷的提交拒绝，防止旧答案挂到新问题下
        if (
            body.questionnaire_version is not None
            and body.questionnaire_version != questionnaire.version
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"问卷已更新到第 {questionnaire.version} 版（你提交的是第 "
                    f"{body.questionnaire_version} 版），请刷新页面按最新问卷重新作答"
                ),
            )

        existing = storage.load_response(student_id)

        # 幂等：同一 submission_token 的重试返回同一回执，不新增作答、不重复评价
        if body.submission_token and existing is not None:
            hit = storage.find_attempt_by_token(existing, body.submission_token)
            if hit is not None:
                return _receipt(existing, hit)

        # 防作弊（安全修复 R2）：
        # 1) 本问卷版本已有评价完成的作答 → 锁死，防止“看反馈 → 改答案 → 重交”刷分；
        # 2) 每版本提交次数上限，防止洪水式重交（每次提交都会触发一次 LLM 评价）。
        if existing is not None:
            same_version = [
                a for a in existing.attempts if a.questionnaire_version == questionnaire.version
            ]
            if not _resubmit_after_feedback_allowed() and any(
                a.evaluation_status == "ok" for a in same_version
            ):
                raise HTTPException(
                    status_code=409,
                    detail="本问卷已生成评价反馈，不能再修改作答；如需重新作答请联系助教重新生成问卷",
                )
            if len(same_version) >= MAX_ATTEMPTS_PER_VERSION:
                raise HTTPException(
                    status_code=409,
                    detail=f"该问卷提交次数已达上限（{MAX_ATTEMPTS_PER_VERSION} 次），请联系助教重新生成问卷",
                )
        _check_submit_rate(student_id)

        # 服务端限时：客户端上报的 time_expired / duration_seconds 不可信，超时以
        # 首次打开问卷的服务端时间为准（安全修复 R2）
        server_expired, server_elapsed = _server_time_check(student_id, questionnaire)

        # 防作弊（安全修复 R7）：与其他学生文本作答完全一致 → 标记疑似协同作弊
        collusion_with = _detect_collusion(student_id, answers)

        submitted_at = now_utc()
        duration = body.duration_seconds
        if server_expired:
            time_expired = True
            duration = server_elapsed if server_elapsed is not None else duration
        else:
            time_expired = bool(body.time_expired)
        attempt_data = AttemptData(
            submitted_at=submitted_at,
            answers=answers,
            attempt_no=(existing.attempt_count + 1) if existing else 1,
            questionnaire_id=questionnaire.questionnaire_id,
            questionnaire_version=questionnaire.version,
            submission_token=body.submission_token,
            stage="initial",
            evaluation_status="pending",
            time_expired=time_expired,
            duration_seconds=max(0, int(duration)) if isinstance(duration, (int, float)) else None,
            collusion_suspect=bool(collusion_with),
            collusion_with=collusion_with,
        )
        if existing:
            existing.answers = answers
            existing.submitted_at = submitted_at
            existing.attempt_count += 1
            existing.attempts.append(attempt_data)
            existing.questionnaire_id = questionnaire.questionnaire_id
            existing.questionnaire_version = questionnaire.version
            existing.attempt_id = attempt_data.attempt_id
            response = existing
        else:
            response = ResponseFile(
                student_id=student_id,
                submitted_at=submitted_at,
                answers=answers,
                attempt_count=1,
                attempts=[attempt_data],
                questionnaire_id=questionnaire.questionnaire_id,
                questionnaire_version=questionnaire.version,
                attempt_id=attempt_data.attempt_id,
            )
        storage.save_response(response)  # 答案先落盘：评价失败不丢

        # 评价异步化：提交立即返回回执（evaluation_status=pending），AI 评价在
        # 后台线程执行（真实 LLM 需 30~90s，不占用请求）；学生端结果页轮询进度，
        # 评价失败落盘为 failed，学生可在结果页重试。
        _spawn_evaluation(student_id, attempt_data)
        return _receipt(response, attempt_data)


@app.get("/api/exam/{student_id}/result")
def student_result(student_id: str, request: Request, attempt_id: str | None = None):
    """学生结果查询：只返回回执、状态、已发布反馈与下一步建议（docs §6/§7）。"""
    auth.require_student_access(student_id, request)
    _check_sid(student_id)
    _get_submission_or_404(student_id)
    response = storage.load_response(student_id)
    if response is None or not response.attempts:
        return {
            "student_id": student_id,
            "state": "none",
            "attempts": [],
            "feedback": None,
            "next_steps": [],
            "next_steps_en": [],
        }

    attempt = None
    if attempt_id:
        attempt = storage.find_attempt(response, attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="作答编号不存在")
    if attempt is None:
        attempt = storage.latest_attempt(response)

    eval_file = storage.load_evaluation_file(student_id)
    record = eval_file.current(attempt.attempt_id)
    reviews = storage.load_review_file(student_id)
    review = reviews.latest_for(attempt.attempt_id)

    feedback = None
    state = "evaluating"
    if attempt.evaluation_status == "pending":
        state = "evaluating"
    elif attempt.evaluation_status == "awaiting_review":
        state = "awaiting_review"
    elif attempt.evaluation_status == "failed":
        state = "eval_failed"
    elif record is not None and record.status == "ok" and record.result is not None:
        if review is not None and review.published:
            state = "feedback_ready"
            feedback = {
                "source": "review",
                "text": review.feedback_to_student,
                "decision": review.decision,
                "published_at": review.created_at.isoformat(),
            }
        elif record.result.needs_ta_review:
            state = "awaiting_review"
        else:
            state = "feedback_ready"
            feedback = {"source": "evaluation", "text": record.result.feedback,
                        "decision": None, "published_at": None}
    else:
        state = "evaluating"

    next_steps = []
    next_steps_en = []
    if state == "eval_failed":
        next_steps.append("评价未完成，可点击“重新评价”重试；你的答案已保存，不会丢失。")
        next_steps_en.append(
            "Evaluation did not finish — click Retry. Your answers are saved and will not be lost."
        )
    if state == "awaiting_review":
        next_steps.append("课堂抽查答案已保存，等待 TA 人工评分。" if attempt.submission_source == "classroom"
                          else "系统已生成初步判断，等待助教复核后反馈可见。")
        next_steps_en.append(
            "Your classroom answers are saved and awaiting TA grading." if attempt.submission_source == "classroom"
            else "An initial result is ready and is awaiting TA review before feedback is released."
        )
    # 安全修复 R2：remaining_issues 属于评价结论，仅在反馈已发布（feedback_ready）时
    # 下发；awaiting_review 阶段提前泄露会让学生针对性重交刷分
    if state == "feedback_ready" and record is not None and record.status == "ok" \
            and record.result is not None:
        for issue, issue_en in zip(
            record.result.remaining_issues,
            record.result.remaining_issues_en or [""] * len(record.result.remaining_issues),
        ):
            next_steps.append(f"建议复习：{issue}")
            next_steps_en.append(f"Review suggested: {issue_en or issue}")

    feedback_payload = None
    if feedback is not None:
        text_en = ""
        if feedback["source"] == "evaluation" and record is not None and record.result is not None:
            text_en = record.result.feedback_en or ""
        feedback_payload = {**feedback, "text_en": text_en}

    return {
        "student_id": student_id,
        "state": state,
        "latest_attempt": {
            "attempt_id": attempt.attempt_id,
            "attempt_no": attempt.attempt_no,
            "submitted_at": attempt.submitted_at.isoformat(),
            "questionnaire_version": attempt.questionnaire_version,
            "evaluation_status": attempt.evaluation_status,
            "time_expired": attempt.time_expired,
        },
        "attempts": [
            {
                "attempt_id": a.attempt_id,
                "attempt_no": a.attempt_no,
                "submitted_at": a.submitted_at.isoformat(),
                "questionnaire_version": a.questionnaire_version,
                "evaluation_status": a.evaluation_status,
                "time_expired": a.time_expired,
                # 本人作答数据：用于“重新作答时载入上次答案”
                "answers": a.answers,
            }
            for a in response.attempts
        ],
        "feedback": feedback_payload,
        "next_steps": next_steps,
        "next_steps_en": next_steps_en,
    }


@app.post("/api/exam/{student_id}/evaluate")
def student_retry_evaluate(student_id: str, request: Request):
    """学生触发的评价重试：复用最新作答，不新增 attempt（docs §4.4）。

    安全修复 R3：仅在最新作答评价**失败**时允许重试——评价成功后重跑没有意义，
    只会被用来无限触发 LLM 调用（成本放大 / 线程池占用）。需要刷新结果走教师端。
    """
    auth.require_student_access(student_id, request)
    _check_sid(student_id)
    with storage.student_lock(student_id):
        response = _get_response_or_404(student_id)
        attempt = storage.latest_attempt(response)
        if attempt.evaluation_status not in ("failed",):
            raise HTTPException(
                status_code=409,
                detail=(
                    "最新作答的评价不在失败状态，无需重试；如需重新评价请联系助教"
                    if attempt.evaluation_status == "ok"
                    else "评价正在进行中，请稍后在结果页查看"
                ),
            )
        record = _run_evaluation(student_id, attempt)
        _write_back_eval_status(student_id, attempt.attempt_id, record.status)
        receipt = _receipt(response, attempt)
        receipt["evaluation_status"] = record.status
        return receipt


# ---------------- 教师端 API ----------------

@app.get("/api/teacher/overview")
def teacher_overview(request: Request):
    auth.require_teacher(request)
    rows = []
    for submission in storage.list_submissions():
        sid = submission.student_id
        questionnaire = storage.load_questionnaire(sid)
        response = storage.load_response(sid)
        evaluation = None
        eval_status = None
        latest_attempt_id = None
        latest_attempt = None
        review = None
        if questionnaire is None:
            exam_status = "not_generated"
        elif response is None or not response.attempts:
            exam_status = "waiting"
        else:
            exam_status = "done"
            latest_attempt = storage.latest_attempt(response)
            latest_attempt_id = latest_attempt.attempt_id
            eval_status = latest_attempt.evaluation_status
            eval_file = storage.load_evaluation_file(sid)
            record = eval_file.current(latest_attempt.attempt_id)
            if record is not None and record.status == "ok":
                evaluation = record.result
            reviews = storage.load_review_file(sid)
            review = reviews.latest_for(latest_attempt.attempt_id)
        review_status = "none"
        if evaluation is not None and evaluation.needs_ta_review:
            review_status = "awaiting"
        if review is not None:
            review_status = "reviewed"
        rows.append({
            "student_id": sid,
            "case_id": questionnaire.case_id if questionnaire else "",
            "original_issues": [i.type for i in questionnaire.diagnosis.issues] if questionnaire else [],
            "exam_status": exam_status,
            "attempt_count": response.attempt_count if response else 0,
            "latest_attempt_id": latest_attempt_id,
            "evaluation_status": eval_status,
            "understanding": evaluation.understanding.value if evaluation else None,
            "understanding_zh": UNDERSTANDING_ZH[evaluation.understanding.value] if evaluation else None,
            "score": evaluation.score if evaluation else None,
            "max_score": evaluation.max_score if evaluation else None,
            "needs_ta_review": evaluation.needs_ta_review if evaluation else False,
            "collusion_suspect": latest_attempt.collusion_suspect if latest_attempt else False,
            "review_status": review_status,
        })
    return {"rows": rows}


@app.get("/api/teacher/students/{student_id}")
def teacher_detail(student_id: str, request: Request, version: int | None = None):
    auth.require_teacher(request)
    _check_sid(student_id)
    submission = _get_submission_or_404(student_id)
    questionnaire = (
        storage.load_questionnaire(student_id, version)
        if version is not None
        else storage.load_questionnaire(student_id)
    )
    if questionnaire is None:
        raise HTTPException(status_code=404, detail="该学生的问卷尚未生成")
    response = storage.load_response(student_id)
    eval_file = storage.load_evaluation_file(student_id)
    reviews = storage.load_review_file(student_id)

    latest_attempt = storage.latest_attempt(response) if response else None
    current_record = eval_file.current(latest_attempt.attempt_id) if latest_attempt else None
    current_ok = (
        current_record.result
        if current_record is not None and current_record.status == "ok"
        else None
    )

    case = cases.load_case(questionnaire.case_id) if questionnaire.case_id else None
    tokens = storage.load_tokens(create_if_missing=True)

    versions = sorted({
        q.version for q in [storage.load_questionnaire(student_id, v) for v in _known_versions(student_id)] if q
    })

    return {
        "student_id": student_id,
        "submission": submission.model_dump(mode="json"),
        "questionnaire": questionnaire.model_dump(mode="json"),
        "questionnaire_versions": versions,
        "response": response.model_dump(mode="json") if response else None,
        # 兼容旧读取方：evaluation = 最新作答的最新成功评价（评价失败时为 None，不显示旧结论）
        "evaluation": current_ok.model_dump(mode="json") if current_ok else None,
        "evaluation_records": [r.model_dump(mode="json") for r in eval_file.records],
        "reviews": [r.model_dump(mode="json") for r in reviews.reviews],
        "student_access_code": (tokens.get("students") or {}).get(student_id, ""),
        "case": (
            {
                "case_id": case.case_id,
                "title": case.title,
                "title_en": case.title_en,
                "code_quality": case.code_quality,
                "behavior_notes": case.behavior_notes,
                "behavior_notes_en": case.behavior_notes_en,
                "time_limit_minutes": case.time_limit_minutes,
                "verification_points": case.verification_points,
                "review_status": case.review_status,
            }
            if case else None
        ),
    }


def _known_versions(student_id: str) -> list[int]:
    """历史目录里出现过的全部版本号。"""
    versions = []
    folder = storage.generated_history_dir()
    if folder.exists():
        for path in folder.glob(f"{student_id}_v*.json"):
            m = re.search(r"_v(\d+)$", path.stem)
            if m:
                versions.append(int(m.group(1)))
    return versions or [1]


@app.post("/api/teacher/students/{student_id}/evaluate")
def teacher_evaluate(student_id: str, request: Request):
    """重新运行评价（提交后评价失败、或想刷新结果）。不新增学生作答。"""
    auth.require_teacher(request)
    _check_sid(student_id)
    with storage.student_lock(student_id):
        response = _get_response_or_404(student_id)
        attempt = storage.latest_attempt(response)
        record = _run_evaluation(student_id, attempt)
        _write_back_eval_status(student_id, attempt.attempt_id, record.status)
        if record.status != "ok" or record.result is None:
            raise HTTPException(status_code=502, detail=f"评价失败: {record.error}")
        return record.result.model_dump(mode="json")


def _paper_source(student_id: str):
    submission = _get_submission_or_404(student_id)
    assignment = storage.load_assignment()
    if submission.student_id != student_id or submission.assignment_id != assignment.id:
        raise HTTPException(status_code=409, detail="该学生的作答与当前作业不匹配，请检查作答记录")
    if not submission.code.strip():
        detail = "该学生的提交记录存在，但没有可读取的 C 源代码，请核对原始提交后补充代码" if submission.source_archive_sha256 else "该学生尚无有效作答，无法生成个性化答卷"
        raise HTTPException(status_code=422, detail=detail)
    return submission, assignment


def _paper_payload(submission, assignment, questionnaire) -> dict:
    return {
        "status": "ready",
        "student_id": submission.student_id,
        "latest_answer": current_paper_answer(submission.student_id, questionnaire),
        "paper": {
            **questionnaire.public(),
            "course": assignment.course,
            "course_en": assignment.course_en,
            "title": assignment.title,
            "title_en": assignment.title_en,
            "code": submission.code,
            "language": submission.language,
            "submitted_at": submission.submitted_at,
            "generated_at": questionnaire.generated_at.isoformat(),
            "time_limit_minutes": questionnaire.time_limit_minutes,
            "source_files": [{"filename": f.filename, "line_start": f.line_start,
                              "line_count": len(f.code.splitlines())} for f in submission.source_files],
        },
        # 与可打印答卷分开，前端仅在教师主动展开时渲染。
        "teacher_reference": {
            "import_notes": submission.import_notes,
            "diagnosis": questionnaire.diagnosis.model_dump(mode="json"),
            "answers": [
                {"id": q.id, "answer": q.expected_answer} for q in questionnaire.questions
            ],
        },
    }


class ClassroomAnswerBody(BaseModel):
    answers: dict
    questionnaire_id: str = Field(min_length=1, max_length=100)
    questionnaire_version: int = Field(ge=1)
    submission_token: str = Field(min_length=16, max_length=100)


@app.post("/api/teacher/students/{student_id}/paper/answers")
def submit_classroom_answers(student_id: str, body: ClassroomAnswerBody, request: Request):
    """TA 开启的现场作答：保存到正式作答记录，交给 TA 评分，不启动 AI 评价。"""
    auth.require_teacher(request)
    _check_sid(student_id)
    with storage.student_lock(student_id):
        submission, assignment = _paper_source(student_id)
        existing = storage.load_response(student_id)
        hit = storage.find_attempt_by_token(existing, body.submission_token) if existing else None
        if hit:
            if (hit.questionnaire_id != body.questionnaire_id
                    or hit.questionnaire_version != body.questionnaire_version):
                raise HTTPException(409, "提交编号已用于另一份答卷，请重新载入")
            old_q = storage.load_questionnaire(student_id, hit.questionnaire_version)
            if not old_q or old_q.questionnaire_id != hit.questionnaire_id:
                raise HTTPException(409, "原答卷快照不可用，请联系 TA 核对记录")
            try:
                same_answers = old_q.normalize_answers(body.answers) == hit.answers
            except ValueError:
                same_answers = False
            if not same_answers:
                raise HTTPException(409, "提交编号已用于不同答案，请重新载入后作答")
            return answer_receipt(student_id, hit)
        q = storage.load_questionnaire(student_id)
        if (not q or q.questionnaire_id != body.questionnaire_id or q.version != body.questionnaire_version
                or q.source_fingerprint != _source_fingerprint(submission, assignment)):
            raise HTTPException(409, "答卷或原始提交已经更新，请重新载入答卷后作答；当前草稿仍保留")
        for question in q.questions:
            value = body.answers.get(question.id, []) if question.type.value == "multiple_choice" else body.answers.get(question.id, "")
            if question.type.value == "multiple_choice":
                valid_type = isinstance(value, list) and all(isinstance(v, str) for v in value)
            else:
                valid_type = isinstance(value, str)
            if not valid_type:
                raise HTTPException(422, "答案格式有误，请按题型填写")
        try:
            answers = q.normalize_answers(body.answers)
        except ValueError as e:
            raise HTTPException(422, f"答案格式有误：{e}")
        _check_answer_size(answers)
        if not any(answers.values()):
            raise HTTPException(422, "请至少作答一道题再提交")
        _check_submit_rate(student_id)
        submitted_at = now_utc()
        attempts = list(existing.attempts) if existing else []
        attempt = AttemptData(submitted_at=submitted_at, answers=answers,
            attempt_no=(existing.attempt_count + 1) if existing else 1,
            questionnaire_id=q.questionnaire_id, questionnaire_version=q.version,
            submission_token=body.submission_token, submission_source="classroom",
            source_code_snapshot=submission.code, evaluation_status="awaiting_review")
        attempts.append(attempt)
        storage.save_response(ResponseFile(student_id=student_id, submitted_at=submitted_at,
            answers=answers, attempt_count=attempt.attempt_no, attempts=attempts,
            questionnaire_id=q.questionnaire_id, questionnaire_version=q.version,
            attempt_id=attempt.attempt_id))
        return answer_receipt(student_id, attempt)


@app.post("/api/teacher/students/{student_id}/paper")
def prepare_spot_check(student_id: str, request: Request):
    """输入学号即获取答卷；没有匹配当前作答的答卷时，自动按需生成。"""
    auth.require_teacher(request)
    _check_sid(student_id)
    with storage.student_lock(student_id):
        submission, assignment = _paper_source(student_id)
        job = storage.load_generation_job(student_id)
        if job and job.status == "pending":
            return JSONResponse(status_code=202, content=_job_payload(job))
        questionnaire = storage.load_questionnaire(student_id)
        if (questionnaire and questionnaire.assignment_id == assignment.id
                and questionnaire.source_fingerprint == _source_fingerprint(submission, assignment)):
            return _paper_payload(submission, assignment, questionnaire)
        job = GenerationJob(student_id=student_id)
        storage.save_generation_job(job)
    threading.Thread(target=_run_generation, args=(student_id,), daemon=True).start()
    return JSONResponse(status_code=202, content=_job_payload(job))


@app.get("/api/teacher/students/{student_id}/paper")
def get_spot_check(student_id: str, request: Request):
    """只查询生成进度及答卷，不启动学生答题计时，不触发评价。"""
    auth.require_teacher(request)
    _check_sid(student_id)
    with storage.student_lock(student_id):
        submission, assignment = _paper_source(student_id)
        job = storage.load_generation_job(student_id)
        if job and job.status == "pending":
            return _job_payload(job)
        questionnaire = storage.load_questionnaire(student_id)
        if (questionnaire and questionnaire.assignment_id == assignment.id
                and questionnaire.source_fingerprint == _source_fingerprint(submission, assignment)):
            return _paper_payload(submission, assignment, questionnaire)
        if job and job.status == "failed":
            return _job_payload(job)
        raise HTTPException(status_code=409, detail="作答已更新或答卷尚未生成，请点击生成答卷")


@app.post("/api/teacher/students/{student_id}/regenerate")
def teacher_regenerate(student_id: str, request: Request):
    """重新生成诊断问卷（异步 202）：立即返回任务，教师端轮询状态端点。

    - 任务在后台线程执行，HTTP 请求不再挂住最长 180s 的 LLM 调用（第三批）；
    - 已有 pending 任务时幂等返回同一任务，不重复触发；
    - 生成结果写入新版本（history 保留），旧问卷与历次作答完整保留（P0）。
    """
    auth.require_teacher(request)
    _check_sid(student_id)
    _get_submission_or_404(student_id)
    with storage.student_lock(student_id):
        existing = storage.load_generation_job(student_id)
        if existing is not None and existing.status == "pending":
            return JSONResponse(status_code=202, content=_job_payload(existing))
        job = GenerationJob(student_id=student_id)
        storage.save_generation_job(job)
    threading.Thread(target=_run_generation, args=(student_id,), daemon=True).start()
    return JSONResponse(status_code=202, content=_job_payload(job))


@app.post("/api/teacher/students/{student_id}/rotate-code")
def teacher_rotate_access_code(student_id: str, request: Request):
    """安全修复 R8：轮换学生访问码（怀疑泄露/转发他人时教师自助处置）。

    旧码立即失效（下一请求起 403）；新码持久化并在响应中返回（仅教师可见）。
    """
    auth.require_teacher(request)
    _check_sid(student_id)
    _get_submission_or_404(student_id)
    tokens = storage.load_tokens(create_if_missing=True)
    students = tokens.setdefault("students", {})
    new_code = "s-" + secrets.token_hex(8)
    students[student_id] = new_code
    from .storage import save_json_atomic, access_dir
    save_json_atomic(access_dir() / "tokens.json", tokens)
    return {"student_id": student_id, "access_code": new_code}


@app.get("/api/teacher/students/{student_id}/regenerate/status")
def teacher_regenerate_status(student_id: str, request: Request):
    """轮询问卷再生成任务状态（pending / ok / failed）。"""
    auth.require_teacher(request)
    _check_sid(student_id)
    job = storage.load_generation_job(student_id)
    if job is None:
        raise HTTPException(status_code=404, detail="该学生没有生成任务")
    return _job_payload(job)


def _job_payload(job: GenerationJob) -> dict:
    return {
        "job_id": job.job_id,
        "student_id": job.student_id,
        "status": job.status,
        "version": job.version,
        "error": job.error,
        "reused_from": job.reused_from,
        "started_at": job.started_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _run_generation(student_id: str) -> None:
    """后台执行问卷再生成：成功写入新版本，失败落盘 failed（供教师端重试）。"""
    job = storage.load_generation_job(student_id)
    if job is None or job.status != "pending":
        return
    try:
        submission, assignment = _paper_source(student_id)
        fingerprint = _source_fingerprint(submission, assignment)
        provider = get_provider()
        if assignment.requires_real_model and provider.name == "mock":
            raise ValueError("当前作业需使用真实模型按学生代码出题，请检查模型配置；不会使用其他作业的演示题代替")
        # 教师显式重生成要求新鲜内容：不复用缓存，但生成结果写入复用索引
        questionnaire, _reused = reuse.get_or_generate(
            assignment, submission,
            lambda: generate_questionnaire(provider, assignment, submission),
            allow_reuse=False,
        )
        with storage.student_lock(student_id):
            current_submission, current_assignment = _paper_source(student_id)
            if _source_fingerprint(current_submission, current_assignment) != fingerprint:
                raise ValueError("生成期间学生作答或作业发生变化，请重新生成答卷")
            questionnaire.source_fingerprint = fingerprint
            questionnaire.version = storage.next_questionnaire_version(student_id)
            storage.save_questionnaire(questionnaire)
        job.status = "ok"
        job.version = questionnaire.version
        job.reused_from = questionnaire.reused_from
    except Exception as e:  # noqa: BLE001 —— 失败必须落盘，教师端才能看到原因
        log.error("questionnaire generation failed for %s: %s", student_id, e)
        job.status = "failed"
        job.error = str(e)[:500]
    job.finished_at = now_utc()
    storage.save_generation_job(job)


class ReviewBody(BaseModel):
    attempt_id: str | None = None      # 默认最新作答
    decision: str = ""                 # confirmed / revised（默认空以便认证先于校验）
    revised_understanding: str | None = None
    comment: str = ""                  # 复核理由（revised 必填）
    feedback_to_student: str = ""      # 发布给学生的反馈
    published: bool = True


@app.post("/api/teacher/students/{student_id}/review")
def teacher_review(student_id: str, request: Request, body: ReviewBody):
    """助教复核闭环：确认/修订 → 反馈发布 → 学生端 feedback_ready（docs §8）。"""
    auth.require_teacher(request)
    _check_sid(student_id)
    _get_submission_or_404(student_id)
    if body.decision not in ("confirmed", "revised"):
        raise HTTPException(status_code=422, detail="decision 必须是 confirmed 或 revised")
    if body.decision == "revised":
        if body.revised_understanding not in UNDERSTANDING_ZH:
            raise HTTPException(status_code=422, detail="revised 时必须给出修订后的理解档位")
        if not body.comment.strip():
            raise HTTPException(status_code=422, detail="修订结论必须填写复核理由")
    if body.published and not body.feedback_to_student.strip():
        raise HTTPException(status_code=422, detail="发布反馈时必须填写给学生的反馈")

    with storage.student_lock(student_id):
        response = _get_response_or_404(student_id)
        attempt = (
            storage.find_attempt(response, body.attempt_id)
            if body.attempt_id
            else storage.latest_attempt(response)
        )
        if attempt is None:
            raise HTTPException(status_code=404, detail="作答编号不存在")
        eval_file = storage.load_evaluation_file(student_id)
        record = eval_file.current(attempt.attempt_id)
        review = ReviewRecord(
            student_id=student_id,
            attempt_id=attempt.attempt_id,
            evaluation_id=record.evaluation_id if record else "",
            reviewer="ta",
            decision=body.decision,
            revised_understanding=(
                body.revised_understanding if body.decision == "revised" else None
            ),
            comment=body.comment.strip(),
            feedback_to_student=body.feedback_to_student.strip(),
            published=body.published,
        )
        review_file = storage.load_review_file(student_id)
        review_file.reviews.append(review)
        storage.save_review_file(review_file)
        return review.model_dump(mode="json")
