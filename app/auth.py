"""最小身份认证（docs/evaluation-rules.md §6）。

- 教师/助教：单一口令，来自环境变量 AA_TEACHER_TOKEN；未配置时**随机生成**并写入
  tokens.json（安全修复：不再有代码内的默认口令）。
- 学生：每学号一个专属访问码（data/access/tokens.json，教师可在详情页查出发放）。
  功能开关 AA_ACCESS_CODE_ENABLED：**默认开启**（安全默认）；显式设为 0/false/no/off
  时关闭（课堂演示场景：学生端仅凭学号访问，有冒充风险，责任自负）。
- 归属校验：学生访问码只对本人学号有效；教师口令可访问全部学生（最小系统约定）。
- 认证失败 401（未提供），归属不符 403；先认证后查询，避免向未认证方泄露学号是否存在。
- 防爆破（安全修复 R1）：按客户端 IP 统计认证失败次数，窗口内超阈值返回 429；
  所有口令比较使用 secrets.compare_digest（常量时间，防时序侧信道）。
- 凭据只接受 Authorization: Bearer 头（安全修复 R1：不再接受 URL query 参数，
  避免凭据进入访问日志 / 浏览器历史 / Referer）。
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from . import storage

ACCESS_CODE_ENV = "AA_ACCESS_CODE_ENABLED"

# ---- 防爆破限速（进程内滑动窗口；多 worker 部署时各进程独立计数）----
AUTH_FAIL_LIMIT = 8          # 窗口内允许的认证失败次数
AUTH_FAIL_WINDOW_SECONDS = 600.0   # 统计窗口
AUTH_LOCKOUT_SECONDS = 600.0       # 超限后的封禁时长

_failures: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def access_code_enabled() -> bool:
    """学生访问码功能开关：默认**开启**（安全默认）。显式 0/false/no/off 关闭（演示用）。"""
    return os.getenv(ACCESS_CODE_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _check_rate_limit(request: Request) -> None:
    """窗口内失败过多 → 429。成功认证会清零该 IP 的计数。"""
    ip = _client_ip(request)
    now = time.monotonic()
    with _lock:
        wins = _failures.get(ip)
        if wins is None:
            wins = _failures[ip] = deque()
        while wins and now - wins[0] > AUTH_FAIL_WINDOW_SECONDS:
            wins.popleft()
        # 安全修复 R8：过期清空后的空键及时摘除，避免长期运行下的无界增长
        if not wins:
            _failures.pop(ip, None)
        if len(wins) >= AUTH_FAIL_LIMIT:
            retry_after = int(AUTH_LOCKOUT_SECONDS - (now - wins[0])) + 1
            raise HTTPException(
                status_code=429,
                detail="认证失败次数过多，请稍后再试",
                headers={"Retry-After": str(max(retry_after, 1))},
            )


def _record_failure(request: Request) -> None:
    ip = _client_ip(request)
    with _lock:
        _failures[ip].append(time.monotonic())


def _record_success(request: Request) -> None:
    with _lock:
        _failures.pop(_client_ip(request), None)


def _token_from(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _tokens() -> dict:
    return storage.load_tokens(create_if_missing=True)


def require_teacher(request: Request) -> None:
    _check_rate_limit(request)
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="需要教师认证：请提供访问口令")
    expected = _tokens().get("teacher_token", "")
    if not expected or not secrets.compare_digest(token, expected):
        _record_failure(request)
        raise HTTPException(status_code=403, detail="教师口令不正确")
    _record_success(request)


def require_student_access(student_id: str, request: Request) -> None:
    """学生端接口：开关关闭时仅凭学号（演示模式，风险自负）；开启后需本人访问码或
    教师口令，他人的码一律 403。"""
    if not access_code_enabled():
        return
    _check_rate_limit(request)
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="需要认证：请输入学号对应的访问码")
    tokens = _tokens()
    teacher_token = tokens.get("teacher_token", "")
    if teacher_token and secrets.compare_digest(token, teacher_token):
        _record_success(request)
        return
    student_token = (tokens.get("students") or {}).get(student_id) or ""
    if student_token and secrets.compare_digest(token, student_token):
        _record_success(request)
        return
    _record_failure(request)
    raise HTTPException(status_code=403, detail="访问码不属于该学号，无法查看或提交")
