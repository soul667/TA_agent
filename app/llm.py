"""统一的 LLM Provider 接口。

- LLMProvider: 抽象基类，generate(messages, response_schema=None) -> str
- MockLLMProvider: 无 API Key 时的离线 Mock，按**代码内容**匹配 data/mock_cases 案例
- OpenAICompatProvider: OpenAI 兼容 API（基于 langchain-openai），支持结构化输出
  能力自动降级：json_schema -> json_mode -> legacy
- run_structured: LLM 输出 -> JSON 提取 -> Pydantic 校验 -> 按失败类别处置
  （暂时性错误退避重试 / 空内容小步重试 / 结构坏修复重试 / 定点修复钩子）

失败分类（LangChain 优化·第一批）：
- LLMTransientError: 网络/超时/限流/5xx，指数退避重试，不消耗结构重试次数
- LLMEmptyError: 返回空内容（常见于思考耗尽 max_tokens），单次重试
- 其余（含 JSON 无法提取、校验失败）：附加修复提示重试
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import cases, journal, mock_grader
from .cases import GENERIC_QUESTIONNAIRE

log = logging.getLogger("adaptive_assessment.llm")

T = TypeVar("T", bound=BaseModel)

_MAX_TRANSIENT_RETRIES = 2
_MAX_EMPTY_RETRIES = 1


class LLMError(Exception):
    pass


class LLMTransientError(LLMError):
    """暂时性错误（网络 / 超时 / 限流 / 5xx）：应退避重试而非立即放弃。"""


class LLMEmptyError(LLMError):
    """返回内容为空：通常是推理模型把 max_tokens 花在了思考上。"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# ---------------------------------------------------------------- JSON 提取

def extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取第一个平衡的 JSON 对象。

    依次尝试：直接解析 -> 去掉 markdown 代码块 -> 花括号配对扫描。
    失败返回 None（由上层触发重试），绝不抛异常。
    """
    if not text:
        return None
    stripped = text.strip()
    # 去掉 ```json ... ``` 代码块
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    # 花括号配对扫描（考虑字符串内的花括号与转义）
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        data = json.loads(stripped[start : i + 1])
                        if isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        start = -1  # 继续找下一个平衡块
    return None


# ---------------------------------------------------------------- Provider

class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, messages: list[dict], response_schema: dict | None = None) -> str:
        """根据 messages 生成文本。response_schema 是对输出结构的提示。"""
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    """离线 Mock：

    - 生成问卷：按**代码内容**匹配 data/mock_cases 案例（同学号换代码会得到不同诊断，
      同案例换学号诊断不变）；未匹配时代码走通用问卷并标注待人工核验；
    - 评价：解析 prompt 中的 GRADING_DATA_JSON / CASE_META_JSON，交给
      app.mock_grader 的确定性逐知识点引擎判分；
    - 混沌注入（AA_MOCK_CHAOS，仅首次调用生效）：empty=空内容 / bad_json=纯文本 /
      truncate=JSON 截断——离线测试重试、修复与降级路径。
    """

    name = "mock"

    def __init__(self) -> None:
        self._calls = 0

    def generate(self, messages: list[dict], response_schema: dict | None = None) -> str:
        self._calls += 1
        user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        kind = (response_schema or {}).get("kind", "")
        if kind == "evaluation" or "GRADING_DATA_JSON" in user:
            result = self._evaluate(user)
        else:
            result = self._diagnose(user)
        return self._apply_chaos(result)

    def _apply_chaos(self, result: str) -> str:
        if self._calls != 1:
            return result
        chaos = os.getenv("AA_MOCK_CHAOS", "")
        modes = {m.strip() for m in chaos.split(",") if m.strip()}
        if "empty" in modes:
            # 与真实 Provider 行为一致：空内容抛 LLMEmptyError（退避重试不消耗结构次数）
            raise LLMEmptyError("LLM 返回了空内容（可能 max_tokens 被思考过程耗尽），将重试")
        if "bad_json" in modes:
            return "抱歉，这个问题我需要再想一想，暂时无法给出结构化结果。"
        if "truncate" in modes:
            cut = max(1, int(len(result) * 0.6))
            return result[:cut]
        return result

    # ---- 生成问卷 ----
    def _diagnose(self, user: str) -> str:
        m = re.search(r"STUDENT_ID:\s*(\S+)", user)
        sid = m.group(1) if m else ""
        assignment_m = re.search(r"ASSIGNMENT_ID:\s*(\S+)", user)
        assignment_id = assignment_m.group(1) if assignment_m else ""

        code = self._extract_code(user)
        case = cases.match_case(code) if code else None
        if case is not None:
            payload = cases.case_to_questionnaire_payload(case, sid, assignment_id)
        elif not code:
            # 提示词没有代码（历史直连调用）：退回旧学号映射；仍无则通用问卷
            legacy_case_id = cases.LEGACY_SID_CASES.get(sid)
            legacy_case = cases.load_case(legacy_case_id) if legacy_case_id else None
            if legacy_case is not None:
                payload = cases.case_to_questionnaire_payload(
                    legacy_case, sid, assignment_id
                )
                payload["case_match"] = "legacy_sid"
            else:
                payload = self._generic_payload(sid, assignment_id)
        else:
            payload = self._generic_payload(sid, assignment_id)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _generic_payload(sid: str, assignment_id: str) -> dict:
        payload = {
            "student_id": sid,
            "assignment_id": assignment_id,
            "diagnosis": GENERIC_QUESTIONNAIRE["diagnosis"],
            "questions": GENERIC_QUESTIONNAIRE["questions"],
            "case_id": "",
            "case_match": "none",
        }
        return payload

    @staticmethod
    def _extract_code(user: str) -> str:
        m = re.search(r"该学生的提交如下：\s*(\{.*)$", user, re.DOTALL)
        if not m:
            return ""
        try:
            payload = json.loads(m.group(1))
            return str(payload.get("submission_code", "")) if isinstance(payload, dict) else ""
        except json.JSONDecodeError:
            return ""

    # ---- 评价 ----
    def _evaluate(self, user: str) -> str:
        items = self._parse_json_line(user, "GRADING_DATA_JSON")
        case_meta = self._parse_json_line(user, "CASE_META_JSON") or {}
        m = re.search(r"STUDENT_ID:\s*(\S+)", user)
        sid = m.group(1) if m else ""
        evaluation = mock_grader.grade(items if isinstance(items, list) else [], case_meta)
        evaluation["student_id"] = sid
        return json.dumps(evaluation, ensure_ascii=False)

    @staticmethod
    def _parse_json_line(user: str, tag: str) -> Optional[Any]:
        """按行提取 tag 后的单行 JSON。

        两段机器可读数据均由 json.dumps 单行序列化（换行已转义），
        不能用 DOTALL 贪婪匹配——样例文本里出现的 ] } 会截断或吞并匹配。
        """
        for line in user.splitlines():
            stripped = line.strip()
            if stripped.startswith(tag + ":"):
                try:
                    return json.loads(stripped[len(tag) + 1:].strip())
                except json.JSONDecodeError:
                    return None
        return None



_STRUCTURED_MODES = ("auto", "json_schema", "json_mode", "legacy")


class _UnsupportedFormatError(Exception):
    """端点拒绝了 response_format / 结构化输出（能力缺失，应降级重试）。"""


def _classify_exception(e: Exception) -> LLMError:
    """把底层异常归类为 LLMError 体系：暂时性 vs 永久性。"""
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    text = str(e).lower()
    transient = (
        name in ("APITimeoutError", "APIConnectionError", "RateLimitError",
                 "InternalServerError", "ConnectTimeout", "ReadTimeout")
        or status == 429
        or (isinstance(status, int) and status >= 500)
        or "timed out" in text
        or "rate limit" in text
        or "connection" in text
    )
    if transient:
        return LLMTransientError(f"LLM 暂时性错误（{name}）: {e}")
    return LLMError(f"LLM API 调用失败: {e}")


def _is_unsupported_format_error(e: Exception) -> bool:
    status = getattr(e, "status_code", None)
    if status not in (400, 404, 422):
        return False
    text = str(e).lower()
    return any(
        key in text
        for key in ("response_format", "json_schema", "json_object", "json mode", "structured")
    )


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 Chat API 封装（基于 langchain-openai，需 uv sync --extra llm）。

    结构化输出能力按梯子自动降级并缓存学习结果（AA_STRUCTURED_MODE 可强制固定）：
    json_schema（response_format 严格约束）-> json_mode（保证是 JSON）-> legacy（纯提示词）。
    max_tokens 通过 extra_body 原样透传，保持与 openai SDK 直连时相同的请求体。
    """

    def __init__(self, api_key: str, base_url: str | None = None, model: str = "gpt-4o-mini"):
        self.name = f"openai-compat:{model}"
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self._llm = None
        # 最近一次调用的结束原因（"length" = 被 max_tokens 截断）
        self.last_finish_reason: str | None = None
        mode = os.getenv("AA_STRUCTURED_MODE", "auto").strip().lower()
        self.structured_mode = mode if mode in _STRUCTURED_MODES else "auto"

    def _get_llm(self):
        if self._llm is None:
            try:
                from langchain_openai import ChatOpenAI
            except ImportError as e:
                raise LLMError("未安装 langchain-openai，请运行: uv sync --extra llm") from e
            self._llm = ChatOpenAI(
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=_env_float("AA_TEMPERATURE", 0.2),
                timeout=_env_float("AA_LLM_TIMEOUT", 180.0),
                # 重试策略统一由 run_structured 按失败类别管理，避免双层重试叠加
                max_retries=0,
                # 推理型模型（如 deepseek-flash）先输出思考内容，须给足预算
                extra_body={"max_tokens": _env_int("AA_MAX_TOKENS", 12000)},
            )
        return self._llm

    def _ladder(self, has_schema: bool) -> list[str]:
        if not has_schema or self.structured_mode == "legacy":
            return ["legacy"]
        if self.structured_mode == "json_schema":
            return ["json_schema", "legacy"]
        if self.structured_mode == "json_mode":
            return ["json_mode", "legacy"]
        return ["json_schema", "json_mode", "legacy"]

    def generate(self, messages: list[dict], response_schema: dict | None = None) -> str:
        hint = response_schema or {}
        schema = hint.get("json_schema")
        lc_messages = [
            (str(m.get("role", "user")), str(m.get("content", ""))) for m in messages
        ]
        last_exc: Exception | None = None
        for mode in self._ladder(schema is not None):
            try:
                content = self._invoke(lc_messages, schema, mode)
            except _UnsupportedFormatError as e:
                last_exc = e
                log.warning("结构化模式 %s 被端点拒绝，尝试降级: %s", mode, e)
                continue
            if mode != "json_schema" and self.structured_mode == "auto":
                # 学习端点能力：下次直接从可用的最高模式开始
                self.structured_mode = mode
            return content
        raise LLMError(f"LLM API 调用失败: {last_exc}")

    def _invoke(self, lc_messages: list[tuple], schema: dict | None, mode: str) -> str:
        llm = self._get_llm()
        kwargs: dict = {}
        if mode == "json_schema" and schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": str(schema.get("title") or "output"),
                    "schema": schema,
                },
            }
        elif mode == "json_mode":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            runnable = llm.bind(**kwargs) if kwargs else llm
            ai = runnable.invoke(lc_messages)
        except Exception as e:
            if _is_unsupported_format_error(e):
                raise _UnsupportedFormatError(str(e)) from e
            raise _classify_exception(e) from e
        content = ai.content
        if isinstance(content, list):  # 多分块内容兜底：拼接文本部分
            content = "".join(
                c if isinstance(c, str) else str(getattr(c, "text", "")) for c in content
            )
        self.last_finish_reason = (getattr(ai, "response_metadata", None) or {}).get(
            "finish_reason"
        )
        if not str(content).strip():
            raise LLMEmptyError("LLM 返回了空内容（可能 max_tokens 被思考过程耗尽），将重试")
        return str(content)


def get_provider() -> LLMProvider:
    """根据环境变量选择 Provider：
    MOCK_LLM=true 或没有配置 API Key 时使用 Mock，保证 Demo 永远可运行。
    """
    if os.getenv("MOCK_LLM", "").strip().lower() in ("1", "true", "yes"):
        return MockLLMProvider()
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        log.warning("未配置 LLM_API_KEY，回退到 MockLLMProvider")
        return MockLLMProvider()
    return OpenAICompatProvider(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL") or None,
        # DeepSeek 官方网关的默认模型；可用 LLM_MODEL 覆盖
        model=os.getenv("LLM_MODEL", "deepseek-flash"),
    )


# ---------------------------------------------------------------- 结构化调用

_REPAIR_MESSAGE = (
    "你上面的输出不是符合要求的合法 JSON 对象。请重新输出："
    "只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字，"
    "并严格遵守给定的字段结构。"
)

_TRUNCATION_HINT = (
    "（提示：上次输出疑似被 max_tokens 截断。请更精简地输出，"
    "确保 JSON 完整闭合。）"
)


def run_structured(
    provider: LLMProvider,
    messages: list[dict],
    model_cls: Type[T],
    kind: str,
    pre_clean: Optional[Callable[[dict], dict]] = None,
    max_attempts: int = 3,
    draft_cls: Optional[Type[BaseModel]] = None,
    salvage: Optional[Callable[[dict, Exception], Optional[dict]]] = None,
    assemble: Optional[Callable[[BaseModel], T]] = None,
    prompt_version: str = "",
) -> T:
    """LLM 输出 -> JSON 提取 -> (清洗) -> 校验 -> 失败按类别处置。

    - draft_cls: 模型侧草稿 Schema（不含服务器权威字段）。提供时其 JSON Schema 会
      传给支持结构化输出的 Provider，并先校验草稿再由 assemble 装配为权威模型；
    - salvage(data, error) -> dict | None: 定点修复钩子。校验失败时优先尝试
      （如剔除/修复个别坏题），返回修复后的 dict 则重新校验装配，None 则走整卷重试；
    - assemble(draft) -> model_cls 实例。可抛 ValidationError/ValueError 表示装配失败；
    - prompt_version: 台账与 provenance 使用的 prompt 版本章（app.prompts.version）。

    每次 attempt（含 provider 报错）都会写入 llm 台账（app/journal.py）。
    """
    convo = list(messages)
    schema_hint: dict = {"kind": kind, "model": model_cls.__name__}
    if draft_cls is not None:
        schema_hint["json_schema"] = draft_cls.model_json_schema()

    def _journal(event: str, attempt_no: int, raw: str, outcome: str,
                 error: str, finish_reason: str | None, latency_ms: int,
                 salvaged: bool = False) -> None:
        try:
            journal.record({
                "event": event,
                "kind": kind,
                "provider": provider.name,
                "prompt_version": prompt_version,
                "attempt": attempt_no,
                "outcome": outcome,
                "salvaged": salvaged,
                "finish_reason": finish_reason,
                "latency_ms": latency_ms,
                "raw": raw,
                "error": error,
                "convo": convo,
            })
        except Exception:  # 台账失败绝不影响主流程
            log.warning("写入 LLM 台账失败", exc_info=True)

    def _finish(data: dict) -> T:
        if pre_clean:
            data = pre_clean(data)
        if draft_cls is not None:
            draft = draft_cls.model_validate(data)
            return assemble(draft) if assemble else model_cls.model_validate(draft.model_dump())
        return model_cls.model_validate(data)

    transient_retries = 0
    empty_retries = 0
    attempt = 0
    last_error = "unknown"
    while attempt < max_attempts:
        # ---- 生成（暂时性错误退避重试、空内容小步重试，均不消耗结构重试次数）----
        t0 = time.monotonic()
        try:
            raw = provider.generate(convo, response_schema=schema_hint)
        except LLMEmptyError as e:
            _journal("provider_error", attempt + 1, "", "LLMEmptyError",
                     str(e)[:300], None, int((time.monotonic() - t0) * 1000))
            if empty_retries >= _MAX_EMPTY_RETRIES:
                raise LLMError(f"LLM 连续返回空内容: {e}") from e
            empty_retries += 1
            log.warning("LLM 返回空内容，原样重试 (%d/%d)", empty_retries, _MAX_EMPTY_RETRIES)
            continue
        except LLMTransientError as e:
            _journal("provider_error", attempt + 1, "", "LLMTransientError",
                     str(e)[:300], None, int((time.monotonic() - t0) * 1000))
            if transient_retries >= _MAX_TRANSIENT_RETRIES:
                raise LLMError(f"LLM 暂时性错误重试耗尽: {e}") from e
            transient_retries += 1
            delay = 2.0 ** (transient_retries - 1)
            log.warning(
                "LLM 暂时性错误，%.1fs 后重试 (%d/%d): %s",
                delay, transient_retries, _MAX_TRANSIENT_RETRIES, e,
            )
            time.sleep(delay)
            continue
        except LLMError as e:
            _journal("provider_error", attempt + 1, "", type(e).__name__,
                     str(e)[:300], None, int((time.monotonic() - t0) * 1000))
            raise
        latency_ms = int((time.monotonic() - t0) * 1000)

        attempt += 1
        finish_reason = getattr(provider, "last_finish_reason", None)
        truncated = finish_reason == "length"
        instance: Optional[T] = None
        outcome = "invalid"
        salvaged = False
        data = extract_json(raw)
        if data is None:
            outcome = "parse_failed"
            last_error = "输出中无法提取到 JSON 对象" + ("（输出被截断）" if truncated else "")
        else:
            try:
                instance = _finish(data)
                outcome = "ok"
            except (ValidationError, ValueError) as e:
                last_error = str(e)[:400]
                if salvage is not None and isinstance(data, dict):
                    try:
                        fixed = salvage(data, e)
                    except Exception as repair_exc:
                        log.warning("定点修复钩子执行失败: %s", repair_exc)
                        fixed = None
                    if isinstance(fixed, dict):
                        try:
                            instance = _finish(fixed)
                            outcome = "ok"
                            salvaged = True
                            log.info("定点修复成功：%s", model_cls.__name__)
                        except (ValidationError, ValueError) as e2:
                            last_error = str(e2)[:400]
        if instance is not None:
            _journal("attempt", attempt, raw, outcome, "", finish_reason, latency_ms, salvaged)
            return instance
        log.warning(
            "structured call attempt %d/%d failed (%s: %s)",
            attempt, max_attempts, model_cls.__name__, last_error,
        )
        _journal("attempt", attempt, raw, outcome, last_error, finish_reason, latency_ms)
        repair = _REPAIR_MESSAGE + (_TRUNCATION_HINT if truncated else "")
        convo = convo + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": repair},
        ]
    raise LLMError(f"{model_cls.__name__} 结构化输出在 {max_attempts} 次尝试后仍失败: {last_error}")
