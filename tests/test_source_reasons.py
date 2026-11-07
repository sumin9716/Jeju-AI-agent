"""Open API 를 못 쓰는 이유 안내(에이전트용)와 check-api 도움말(사용자용)."""

from dataclasses import replace

import pytest

from jeju_agent.catalog import Catalog
from jeju_agent.config import Settings
from jeju_agent.datahub import DataHubClient, DataHubError, DataStore, load_registry
from jeju_agent.datahub.apicheck import check_apis, format_report
from jeju_agent.datahub.registry import Registry
from jeju_agent.session import AnalysisSession
from jeju_agent.tools.executor import ToolExecutor


def _store(key: str = "", **overrides) -> DataStore:
    """레지스트리 일부 항목만 바꾼 저장소. overrides: {dataset_id: {필드: 값}}"""
    specs = [replace(s, **overrides.get(s.id, {})) for s in load_registry()]
    return DataStore(registry=Registry(specs), client=DataHubClient(project_key=key, sleep_between_pages=0))


def test_auto_mode_says_why_api_was_skipped_when_api_id_missing():
    store = _store(key="KEY", visitors_by_purpose={"api_id": ""})
    spec = store.registry.get("visitors_by_purpose")
    assert store.api_unavailable_reason(spec) == "no_api_id"
    ds = store.load("visitors_by_purpose")
    assert ds.is_synthetic
    assert "API 고유 ID 미등록" in ds.notes[0] and "source='api'" in ds.notes[0] and "data/raw" in ds.notes[0]
    # 직접 API 를 고르면 다시 시도해도 소용없다는 설정 오류로 끝난다.
    with pytest.raises(DataHubError) as e:
        store.load("visitors_by_purpose", prefer="api")
    assert e.value.kind == "config" and "API 고유 ID 미등록" in str(e.value)


def test_missing_key_and_file_only_reasons():
    store = _store(key="")
    emd = store.registry.get("floating_population_emd")
    assert emd.api_id and store.api_unavailable_reason(emd) == "no_key"
    ds = store.load("floating_population_emd")
    assert ds.is_synthetic and "프로젝트 키 없음" in ds.notes[0]
    restaurants = store.registry.get("restaurants")
    assert store.api_unavailable_reason(restaurants) == "file_only"
    assert "파일로만" in store.load("restaurants").notes[0]
    status = {s["id"]: s for s in store.status()}
    assert status["floating_population_emd"]["api_unavailable"] == "no_key"


def test_ready_api_has_no_reason_and_labels_show_reason():
    store = _store(key="KEY", visitors_by_purpose={"api_id": ""})
    assert store.api_unavailable_reason(store.registry.get("floating_population_emd")) is None
    ex = ToolExecutor(AnalysisSession(), store, Catalog(), Settings())
    text = ex.list_datasets().text
    assert "합성 샘플만 있음 (API 고유 ID 미등록)" in text
    assert "Open API 준비됨 (" not in text  # 준비된 API 에는 이유를 붙이지 않는다


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, '{"message":"Forbidden"}'

    def json(self):
        return self._payload


class _Hub:
    """api_id 가 DENY 로 시작하면 403, EMPTY 면 0건, 나머지는 1건을 돌려준다."""

    def get(self, url, params=None, timeout=None):
        if "/DENY" in url:
            return _Resp(403, {"message": "Forbidden"})
        if "/EMPTY/" in url:
            return _Resp(200, {"totCnt": 0, "hasMore": False, "data": []})
        return _Resp(200, {"totCnt": 1, "hasMore": False, "data": [{"placeName": "물영아리오름"}]})


def _client():
    return DataHubClient(project_key="KEY", session=_Hub(), page_size=2, max_pages=1, sleep_between_pages=0)


def test_403_with_other_success_points_to_adding_api_to_project():
    reg = load_registry()
    ok = replace(reg.get("oreum_locations"), api_id="OK")
    denied = replace(reg.get("museum_locations"), api_id="DENY1")
    empty = replace(reg.get("floating_population_emd"), api_id="EMPTY")
    results = check_apis(_client(), [ok, denied, empty], rows=2)
    assert [r.status for r in results] == ["ok", "error", "empty"]
    assert results[1].error_kind == "auth" and "프로젝트에 담겨 있지 않은" in results[1].hint
    assert "--deep" in results[2].hint
    text = format_report(results)
    assert "- 도움말: 같은 프로젝트 키로 다른 API 는 성공했습니다" in text
    assert "할 일:" in text and "허브 프로젝트에 추가할 API: No.543" in text
    assert "최근 날짜 확인(--deep): No.753" in text
    assert "KEY" not in results[1].error


def test_403_everywhere_points_to_project_key():
    reg = load_registry()
    specs = [replace(reg.get("oreum_locations"), api_id="DENY1"), replace(reg.get("museum_locations"), api_id="DENY2")]
    results = check_apis(_client(), specs, rows=2)
    assert all(r.error_kind == "auth" for r in results)
    assert all("JEJUDATAHUB_PROJECT_KEY" in r.hint for r in results)
    assert "- .env 의 JEJUDATAHUB_PROJECT_KEY 가 맞는지 확인" in format_report(results)
