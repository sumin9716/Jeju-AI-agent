"""Open API 일괄 점검, 예시 키 판별, 토큰 요약 표시."""

from dataclasses import replace

from jeju_agent.agent import usage_summary
from jeju_agent.config import Settings, is_real_secret
from jeju_agent.datahub import DataHubClient, load_registry
from jeju_agent.datahub.apicheck import check_apis, format_report, save_results


class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, "x"

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "/BAD/" in url:
            return FakeResp(403, {})
        return FakeResp(200, {"totCnt": "57", "data": [{"baseDate": "20240101", "emd": "애월읍", "visitPop": 10, "newCol": "x" * 200}]})


def test_check_apis_statuses_and_report(tmp_path):
    reg = load_registry()
    base = reg.get("floating_population_emd")
    good = replace(base, api_id="GOOD", api_params={"startDate": "{{today}}"})
    bad = replace(base, id="bad_one", api_id="BAD")
    none = replace(reg.get("floating_population_foreign_long"), api_id="")
    sess = FakeSession()
    client = DataHubClient(project_key="KEY", session=sess, page_size=2, max_pages=1, sleep_between_pages=0)
    results = check_apis(client, [good, bad, none], extra_params={"endDate": "20240107"}, rows=2)
    assert [r.status for r in results] == ["ok", "error", "no_api_id"]
    ok = results[0]
    assert ok.total == 57 and ok.columns == ["baseDate", "emd", "visitPop", "newCol"]
    assert ok.unknown_columns == ["newCol"] and len(ok.sample[0]["newCol"]) == 80
    assert ok.params["endDate"] == "20240107" and ok.params["startDate"].isdigit()
    assert sess.calls[0][1]["limit"] == 2 and len(sess.calls) == 2
    assert "403" in results[1].error and "KEY" not in results[1].error
    text = format_report(results)
    assert "newCol" in text and "api_id 미입력" in text and "성공 1 · 실패 1 · api_id 미입력 1" in text
    path = save_results(results, tmp_path)
    assert path.exists() and "newCol" in path.read_text(encoding="utf-8")


def test_placeholder_keys_are_not_real(monkeypatch):
    assert not is_real_secret("") and not is_real_secret("sk-ant-...") and not is_real_secret("<your key>")
    assert is_real_secret("sk-ant-api03-abcDEF123")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-...")
    assert not Settings().has_anthropic_key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcDEF123")
    assert Settings().has_anthropic_key


def test_usage_summary_counts_cached_input():
    text = usage_summary({"input_tokens": 10, "cache_read_input_tokens": 32777, "cache_creation_input_tokens": 12268, "output_tokens": 2113})
    assert text.startswith("입력 45,055") and "캐시 읽기 32,777" and text.endswith("출력 2,113")
    assert usage_summary(None).startswith("입력 0")
