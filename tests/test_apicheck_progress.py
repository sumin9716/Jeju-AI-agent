"""check-api 진행 표시·먼저 저장·Ctrl+C 중단 처리."""

import json
from dataclasses import replace

from jeju_agent.datahub import DataHubClient, load_registry
from jeju_agent.datahub import apicheck
from jeju_agent.datahub.apicheck import check_apis, deep_check, run_deep_checks, save_results


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, '{"message":"Forbidden"}'

    def json(self):
        return self._payload


class _Hub:
    """DENY 로 시작하는 api_id 는 403, 나머지는 글자 컬럼 6개짜리 행 3건 (거르면 1건)."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        if "/DENY" in url:
            return _Resp(403, {"message": "Forbidden"})
        row = {f"c{i}": f"v{i}" for i in range(6)}
        filtered = any(k.startswith("c") for k in (params or {}))
        rows = [row] if filtered else [row, dict(row, c0="w0"), dict(row, c0="x0")]
        size, page = int(params.get("limit", 100)), int(params.get("number", 1))
        return _Resp(200, {"totCnt": len(rows), "hasMore": False, "data": rows[(page - 1) * size: page * size]})


def _client(hub):
    return DataHubClient(project_key="KEY", session=hub, page_size=2, max_pages=1, sleep_between_pages=0)


def test_check_apis_reports_each_dataset_as_it_goes():
    reg = load_registry()
    specs = [
        replace(reg.get("oreum_locations"), api_id="OK"),
        replace(reg.get("museum_locations"), api_id="DENY1"),
        replace(reg.get("floating_population_foreign_long"), api_id=""),
    ]
    lines = []
    check_apis(_client(_Hub()), specs, rows=2, log=lines.append)
    assert lines[0].startswith("✔ No.544 오름 위치 정보 — 성공 (전체 3건")
    assert lines[1] == "✖ No.543 박물관 및 미술관 위치 정보 — 인증 실패(403)"
    assert lines[2] == "○ No.747 읍면동 단위 외국인 장기체류 유동인구 — api_id 미입력"


def test_deep_check_limits_filter_probes_and_logs_steps():
    hub = _Hub()
    spec = replace(load_registry().get("oreum_locations"), api_id="OK")
    steps = []
    out = deep_check(_client(hub), spec, {}, log=steps.append, max_filters=2)
    assert list(out["filters"]) == ["c0", "c1"] and out["filters"]["c0"]["works"] is True
    assert steps == ["페이지 넘김 확인 …", "쪽당 최대 건수 확인 …", "거르기 변수 2개 확인 …"]


def test_run_deep_checks_stops_cleanly_on_ctrl_c(monkeypatch, tmp_path):
    reg = load_registry()
    specs = [replace(reg.get(k), api_id="OK") for k in ("oreum_locations", "museum_locations", "souvenir_shops")]
    results = check_apis(_client(_Hub()), specs, rows=2)
    seen = []

    def fake_deep(client, spec, params, **kw):
        seen.append(spec.id)
        if len(seen) == 2:
            raise KeyboardInterrupt
        return {"errors": [], "paging": "정상"}

    monkeypatch.setattr(apicheck, "deep_check", fake_deep)
    msgs = []
    assert run_deep_checks(_client(_Hub()), results, specs, log=msgs.append, pause=0) is True
    assert results[0].deep == {"errors": [], "paging": "정상"} and results[1].deep == {} and results[2].deep == {}
    assert msgs[-1].startswith("⚠️ 정밀 점검을 중단했습니다")
    # 기본 결과를 먼저 저장한 파일에 정밀 결과를 덮어써 하나로 남긴다
    out = tmp_path / "checks"
    path = save_results(results, out)
    assert save_results(results, out, path=path) == path and len(list(out.iterdir())) == 1
    assert json.loads(path.read_text(encoding="utf-8"))[0]["deep"]["paging"] == "정상"
