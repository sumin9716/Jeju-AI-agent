"""실행 설정: 환경변수(.env)와 경로를 한 곳에서 관리한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
CATALOG_FILE = DATA_DIR / "catalog" / "jejudatahub_datalist_20261105.csv"
REGISTRY_FILE = Path(__file__).resolve().parent / "datahub" / "registry.yaml"
SAMPLES_DIR = DATA_DIR / "samples"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = ROOT_DIR / "outputs"
REPORT_DIR = OUTPUT_DIR / "reports"
CHART_DIR = OUTPUT_DIR / "charts"
TRACE_DIR = OUTPUT_DIR / "traces"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# .env 는 프로젝트 루트 기준으로 읽는다 (없으면 조용히 통과).
load_dotenv(ROOT_DIR / ".env")

VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def is_real_secret(value: str | None) -> bool:
    """빈 값이나 'sk-ant-...' 같은 예시값은 키로 인정하지 않는다."""
    v = (value or "").strip()
    if not v:
        return False
    lowered = v.lower()
    return not ("..." in v or "…" in v or lowered.startswith("<") or lowered in {"sk-ant-", "your-api-key", "changeme"})


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    """에이전트 실행 설정. 환경변수에서 읽되 코드로도 덮어쓸 수 있다."""

    model: str = field(default_factory=lambda: os.getenv("JEJU_AGENT_MODEL", "claude-opus-5-5").strip())
    effort: str = field(default_factory=lambda: os.getenv("JEJU_AGENT_EFFORT", "medium").strip().lower())
    max_tokens: int = field(default_factory=lambda: _env_int("JEJU_AGENT_MAX_TOKENS", 16000))
    max_steps: int = field(default_factory=lambda: _env_int("JEJU_AGENT_MAX_STEPS", 25))
    sandbox_timeout: int = field(default_factory=lambda: _env_int("JEJU_AGENT_SANDBOX_TIMEOUT", 90))
    fallbacks: str = field(default_factory=lambda: os.getenv("JEJU_AGENT_FALLBACKS", "default").strip().lower())
    thinking: str = field(default_factory=lambda: os.getenv("JEJU_AGENT_THINKING", "adaptive").strip().lower())
    thinking_display: str = field(
        default_factory=lambda: os.getenv("JEJU_AGENT_THINKING_DISPLAY", "summarized").strip().lower()
    )
    datahub_project_key: str = field(default_factory=lambda: os.getenv("JEJUDATAHUB_PROJECT_KEY", "").strip())
    datahub_base_url: str = field(
        default_factory=lambda: os.getenv("JEJUDATAHUB_BASE_URL", "https://open.jejudatahub.net/api/proxy").strip()
    )

    def __post_init__(self) -> None:
        if self.effort not in VALID_EFFORTS:
            self.effort = "medium"
        if self.max_steps < 1:
            self.max_steps = 1

    @property
    def has_anthropic_key(self) -> bool:
        return is_real_secret(os.getenv("ANTHROPIC_API_KEY")) or is_real_secret(os.getenv("ANTHROPIC_AUTH_TOKEN"))

    @property
    def has_datahub_key(self) -> bool:
        return is_real_secret(self.datahub_project_key)

    @property
    def use_thinking(self) -> bool:
        return self.thinking not in ("", "off", "false", "0", "none", "disabled")

    @property
    def use_fallbacks(self) -> bool:
        return self.fallbacks not in ("", "off", "false", "0", "none")


def ensure_dirs() -> None:
    for d in (SAMPLES_DIR, RAW_DIR, CACHE_DIR, REPORT_DIR, CHART_DIR, TRACE_DIR):
        d.mkdir(parents=True, exist_ok=True)
