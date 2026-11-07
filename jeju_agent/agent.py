"""에이전트 루프: Claude(Anthropic SDK) 와 도구 실행기를 잇는다.

- 모델 호출은 스트리밍으로 하고, 텍스트/사고 요약/도구 호출을 이벤트로 UI 에 흘려보낸다.
- 시스템 프롬프트의 고정 부분에 cache_control 을 달아 반복 호출 비용을 줄인다.
- 서버측 fallbacks("default") 로 안전 분류기 거절 시 대체 모델이 이어받게 한다(옵션).
- 테스트·오프라인 시연을 위해 ScriptedBackend(미리 정한 응답) 를 끼울 수 있다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from . import __version__
from .catalog import Catalog
from .config import PROMPT_DIR, TRACE_DIR, Settings, ensure_dirs
from .datahub import DataStore
from .session import AnalysisSession
from .tools import TOOLS, ToolExecutor
from .tools.executor import tool_input_summary

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def usage_summary(usage: dict | None) -> str:
    """캐시로 읽은·저장한 입력까지 합친 전체 입력 토큰과 출력 토큰을 한 줄로 보여준다."""
    usage = usage or {}
    inp = int(usage.get("input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    total_in = inp + cache_read + cache_write
    return f"입력 {total_in:,} (캐시 읽기 {cache_read:,} · 캐시 저장 {cache_write:,}) / 출력 {out:,}"


class AgentError(RuntimeError):
    """사용자에게 보여줄 한국어 메시지를 담은 에이전트 오류."""


@dataclass
class AgentEvent:
    type: str  # text | thinking | tool_start | tool_end | status | final | error
    data: dict = field(default_factory=dict)


@dataclass
class ModelResponse:
    content: list[dict]
    stop_reason: str
    usage: dict = field(default_factory=dict)
    model: str = ""


class ModelBackend(Protocol):
    def create(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
        on_text: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
    ) -> ModelResponse: ...


# ---------------------------------------------------------------------------- 백엔드
class ClaudeBackend:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def _kwargs(self, system: list[dict], messages: list[dict], tools: list[dict]) -> dict:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
            "output_config": {"effort": self.settings.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if self.settings.use_thinking:
            thinking: dict[str, str] = {"type": "adaptive"}
            if self.settings.thinking_display in ("summarized", "omitted"):
                thinking["display"] = self.settings.thinking_display
            kwargs["thinking"] = thinking
        return kwargs

    def create(self, *, system, messages, tools, on_text=None, on_thinking=None) -> ModelResponse:
        import anthropic

        kwargs = self._kwargs(system, messages, tools)
        try:
            if self.settings.use_fallbacks:
                cm = self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                cm = self.client.messages.stream(**kwargs)
            with cm as stream:
                for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", "")
                        if dtype == "text_delta" and on_text:
                            on_text(delta.text)
                        elif dtype == "thinking_delta" and on_thinking and getattr(delta, "thinking", ""):
                            on_thinking(delta.thinking)
                final = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AgentError("Claude API 키가 올바르지 않습니다. .env 의 ANTHROPIC_API_KEY 를 확인하세요.") from e
        except anthropic.PermissionDeniedError as e:
            raise AgentError(f"API 키 권한이 부족합니다: {e.message}") from e
        except anthropic.NotFoundError as e:
            raise AgentError(f"모델 '{self.settings.model}' 을(를) 찾을 수 없습니다. JEJU_AGENT_MODEL 을 확인하세요. ({e.message})") from e
        except anthropic.RateLimitError as e:
            raise AgentError("요청이 너무 많아 잠시 제한되었습니다. 1분 뒤 다시 시도하세요.") from e
        except anthropic.BadRequestError as e:
            raise AgentError(f"요청 형식 오류(모델이 지원하지 않는 옵션일 수 있음): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AgentError("인터넷 연결 또는 Claude API 접속에 실패했습니다. 네트워크를 확인하세요.") from e
        except anthropic.APIStatusError as e:
            raise AgentError(f"Claude API 오류 ({e.status_code}): {e.message}") from e

        content = []
        for block in final.content:
            if hasattr(block, "to_dict"):
                content.append(block.to_dict())
            else:  # pragma: no cover - 방어적 처리
                content.append(dict(block))
        usage = {}
        try:
            u = final.usage
            usage = {
                "input_tokens": getattr(u, "input_tokens", 0) or 0,
                "output_tokens": getattr(u, "output_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            }
        except Exception:  # noqa: BLE001
            usage = {}
        return ModelResponse(content=content, stop_reason=final.stop_reason or "end_turn", usage=usage, model=getattr(final, "model", self.settings.model))


class ScriptedBackend:
    """테스트/오프라인 시연용: 미리 정한 응답을 순서대로 돌려준다."""

    def __init__(self, responses: list[dict | ModelResponse], model: str = "scripted"):
        self.responses = list(responses)
        self.model = model
        self.calls: list[dict] = []

    def create(self, *, system, messages, tools, on_text=None, on_thinking=None) -> ModelResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.responses:
            resp = ModelResponse(content=[{"type": "text", "text": "(스크립트 응답이 더 없습니다)"}], stop_reason="end_turn", model=self.model)
        else:
            nxt = self.responses.pop(0)
            resp = nxt if isinstance(nxt, ModelResponse) else ModelResponse(content=nxt["content"], stop_reason=nxt.get("stop_reason", "end_turn"), usage=nxt.get("usage", {}), model=self.model)
        if on_text:
            for b in resp.content:
                if b.get("type") == "text":
                    on_text(b["text"])
        return resp


# ---------------------------------------------------------------------------- 에이전트
@dataclass
class TurnResult:
    text: str
    steps: list[dict]
    usage: dict
    stop_reason: str
    error: str = ""


class Agent:
    def __init__(
        self,
        settings: Settings | None = None,
        store: DataStore | None = None,
        catalog: Catalog | None = None,
        session: AnalysisSession | None = None,
        backend: ModelBackend | None = None,
    ):
        self.settings = settings or Settings()
        ensure_dirs()
        self.store = store or DataStore(settings=self.settings)
        self.catalog = catalog or Catalog()
        self.session = session or AnalysisSession()
        self.backend = backend or ClaudeBackend(self.settings)
        self.executor = ToolExecutor(self.session, self.store, self.catalog, self.settings, model_name=self.settings.model)
        self.messages: list[dict] = []
        self.turns: list[TurnResult] = []
        self.total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self._static_system = (PROMPT_DIR / "system.md").read_text(encoding="utf-8") + "\n\n" + self._registry_section()
        self._dynamic_system = self._runtime_section()

    # ------------------------------------------------------------------ 프롬프트
    def _registry_section(self) -> str:
        lines = ["## 등록 데이터셋 (load_dataset 으로 바로 사용 가능)", ""]
        for spec in self.store.registry:
            cols = ", ".join(f"{c.name}({c.dtype})" for c in spec.columns)
            lines.append(f"- `{spec.id}` — {spec.name} (No.{spec.catalog_no}, {spec.category}, {spec.access}, 기간 {spec.update})")
            lines.append(f"  - 컬럼: {cols}")
            if spec.date_params:
                a, b = (spec.date_params + [spec.date_params[0]])[:2]
                lines.append(
                    f"  - Open API 기간 변수: {a}, {b} (YYYYMMDD). 예: load_dataset params={{\"{a}\": \"20240101\", \"{b}\": \"20240131\"}}"
                )
            if spec.notes:
                lines.append(f"  - 주의: {spec.notes}")
        lines.append("")
        lines.append("이 밖의 데이터는 search_catalog 로 찾을 수 있으나, 사용자가 허브에서 내려받아 data/raw 에 넣어야 쓸 수 있다.")
        return "\n".join(lines)

    def _runtime_section(self) -> str:
        today = date.today()
        status = self.store.status()
        src_lines = []
        for s in status:
            label = {"api": "Open API", "file": "내려받은 파일", "sample": "합성 샘플", None: "사용 불가"}[s["preferred"]]
            src_lines.append(f"- {s['id']}: {label}")
        key_note = "제주데이터허브 프로젝트 키가 설정되어 있습니다." if self.settings.has_datahub_key else "제주데이터허브 프로젝트 키가 없어 Open API 수집은 불가하며, 파일 또는 합성 샘플로 동작합니다."
        return (
            f"## 실행 환경\n- 오늘 날짜: {today.isoformat()} ({'월화수목금토일'[today.weekday()]}요일)\n- {key_note}\n"
            "- 데이터셋별 현재 출처:\n" + "\n".join(src_lines)
        )

    def system_blocks(self) -> list[dict]:
        return [
            {"type": "text", "text": self._static_system, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": self._dynamic_system},
        ]

    # ------------------------------------------------------------------ 실행
    def run(self, user_text: str, on_event: Callable[[AgentEvent], None] | None = None) -> TurnResult:
        emit = on_event or (lambda e: None)
        self.messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
        steps: list[dict] = []
        turn_usage = {k: 0 for k in self.total_usage}
        text_parts: list[str] = []
        final_text = ""
        stop_reason = "end_turn"
        error = ""
        wrap_up_sent = False
        n_calls = 0

        try:
            while True:
                n_calls += 1
                emit(AgentEvent("status", {"message": "생각하는 중…" if n_calls == 1 else "결과를 해석하는 중…"}))
                text_parts = []
                resp = self.backend.create(
                    system=self.system_blocks(),
                    messages=self.messages,
                    tools=TOOLS,
                    on_text=lambda t: (text_parts.append(t), emit(AgentEvent("text", {"delta": t}))),
                    on_thinking=lambda t: emit(AgentEvent("thinking", {"delta": t})),
                )
                for k in turn_usage:
                    turn_usage[k] += int(resp.usage.get(k, 0) or 0)
                self.messages.append({"role": "assistant", "content": resp.content})
                stop_reason = resp.stop_reason
                text = "".join(b.get("text", "") for b in resp.content if b.get("type") == "text")
                tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]

                if stop_reason == "refusal":
                    error = "안전 정책에 따라 이 요청은 처리할 수 없습니다. 질문을 바꿔 다시 시도해 주세요."
                    final_text = text or error
                    break
                if stop_reason == "pause_turn":
                    continue
                if stop_reason == "max_tokens" and tool_uses:
                    error = "응답이 길어 중간에 잘렸습니다. 질문을 더 작게 나누어 주세요."
                    final_text = text or error
                    # 잘린 tool_use 에는 결과를 줄 수 없으므로 해당 assistant 턴을 제거한다.
                    self.messages.pop()
                    break
                if not tool_uses:
                    final_text = text
                    break

                results = []
                for tu in tool_uses:
                    name = tu.get("name", "")
                    args = tu.get("input") or {}
                    if not isinstance(args, dict):
                        args = {}
                    step_no = len(steps) + 1
                    emit(AgentEvent("tool_start", {"step": step_no, "name": name, "input": args, "summary": tool_input_summary(name, args)}))
                    started = datetime.now()
                    outcome = self.executor.execute(name, args)
                    elapsed = (datetime.now() - started).total_seconds()
                    step = {"step": step_no, "name": name, "input": args, "summary": tool_input_summary(name, args), "ok": not outcome.is_error, "result_text": outcome.text, "elapsed": round(elapsed, 2), "kind": outcome.payload.get("kind", "")}
                    steps.append(step)
                    emit(AgentEvent("tool_end", {**step, "payload": outcome.payload}))
                    block = {"type": "tool_result", "tool_use_id": tu.get("id"), "content": outcome.text}
                    if outcome.is_error:
                        block["is_error"] = True
                    results.append(block)
                content: list[dict] = results
                if len(steps) >= self.settings.max_steps and not wrap_up_sent:
                    wrap_up_sent = True
                    content = results + [{"type": "text", "text": "도구 호출 한도에 도달했습니다. 더 이상 도구를 부르지 말고 지금까지의 결과로 최종 답변을 작성하세요."}]
                elif wrap_up_sent:
                    final_text = text or "도구 호출 한도에 도달해 분석을 마쳤습니다. 지금까지의 결과를 참고해 주세요."
                    # 미응답 tool_use 를 남기지 않도록 결과까지는 넣어 둔다.
                    self.messages.append({"role": "user", "content": results})
                    break
                self.messages.append({"role": "user", "content": content})
        except AgentError as e:
            error = str(e)
            final_text = text_parts and "".join(text_parts) or ""
            emit(AgentEvent("error", {"message": error}))
            # 실패한 턴의 마지막 사용자 메시지가 도구 결과 없이 남지 않도록 정리한다.
            self._repair_history()

        for k in turn_usage:
            self.total_usage[k] += turn_usage[k]
        result = TurnResult(text=final_text, steps=steps, usage=turn_usage, stop_reason=stop_reason, error=error)
        self.turns.append(result)
        emit(AgentEvent("final", {"text": final_text, "error": error, "usage": turn_usage}))
        self._save_trace(user_text, result)
        return result

    def _repair_history(self) -> None:
        """마지막 assistant 턴이 tool_use 로 끝났는데 결과가 없으면 그 턴을 제거한다."""
        if self.messages and self.messages[-1]["role"] == "assistant":
            last = self.messages[-1]["content"]
            if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in last):
                self.messages.pop()

    # ------------------------------------------------------------------ 기록
    def _save_trace(self, user_text: str, result: TurnResult) -> Path | None:
        try:
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            path = TRACE_DIR / f"{self.session.id}_turn{len(self.turns):02d}.json"
            payload = {
                "app_version": __version__,
                "session_id": self.session.id,
                "turn": len(self.turns),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "model": self.settings.model,
                "effort": self.settings.effort,
                "user_text": user_text,
                "final_text": result.text,
                "error": result.error,
                "stop_reason": result.stop_reason,
                "usage": result.usage,
                "steps": [
                    {k: v for k, v in s.items() if k != "result_text"} | {"result_text": s["result_text"][:4000]}
                    for s in result.steps
                ],
                "datasets": self.session.citations(),
                "charts": [c.__dict__ for c in self.session.charts],
                "reports": [r.__dict__ for r in self.session.reports],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            return path
        except Exception:  # noqa: BLE001 - 기록 실패가 답변을 막지 않게
            return None

    def reset(self) -> None:
        self.session = AnalysisSession()
        self.executor = ToolExecutor(self.session, self.store, self.catalog, self.settings, model_name=self.settings.model)
        self.messages = []
        self.turns = []


def make_agent(settings: Settings | None = None, backend: ModelBackend | None = None) -> Agent:
    return Agent(settings=settings, backend=backend)
