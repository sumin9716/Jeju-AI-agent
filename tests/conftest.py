import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_outputs(tmp_path, monkeypatch):
    """테스트가 만드는 보고서·차트·실행 기록·점검 결과를 실제 outputs/ 가 아닌 임시 폴더에 쓰게 한다."""
    import jeju_agent.agent as agent_mod
    import jeju_agent.config as config
    import jeju_agent.tools.executor as executor_mod

    out = tmp_path / "outputs"
    dirs = {name: out / name for name in ("reports", "charts", "traces", "checks")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "REPORT_DIR", dirs["reports"])
    monkeypatch.setattr(config, "CHART_DIR", dirs["charts"])
    monkeypatch.setattr(config, "TRACE_DIR", dirs["traces"])
    monkeypatch.setattr(agent_mod, "TRACE_DIR", dirs["traces"])
    monkeypatch.setattr(executor_mod, "CHART_DIR", dirs["charts"])
    monkeypatch.setattr(executor_mod, "REPORT_DIR", dirs["reports"])
    yield out
