import json

import pytest
import requests

from clipper import llm

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


class Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


def chat(content):
    return Resp(200, {"choices": [{"message": {"content": content}}]})


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    monkeypatch.setattr(llm, "RETRY_WAITS", (0, 0))           # retry rounds without the real waits
    for d in (llm.BROKEN, llm.BAD, llm.STRIKES, llm.COOLING):
        d.clear()
    llm.CLOUD_DOWN["until"] = 0.0
    monkeypatch.setattr(llm.cfg, "load", lambda: {"model": "cloud:test-model", "cloud_url": "http://gw:20128", "cloud_key": "k"})
    monkeypatch.setattr(llm, "log", lambda m: None)


def test_cloud_model_answers_with_schema(monkeypatch):
    sent = []
    monkeypatch.setattr(llm.requests, "post", lambda url, json, headers, timeout: sent.append((url, json, headers)) or chat('{"title": "Hi"}'))
    assert llm.ask_json("p", SCHEMA) == {"title": "Hi"}
    url, body, headers = sent[0]
    assert url == "http://gw:20128/v1/chat/completions" and body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema" and headers["Authorization"] == "Bearer k"


def test_provider_without_schemas_gets_plain_json_request(monkeypatch):
    replies = iter([Resp(400, {}), chat('```json\n{"title": "Fenced"}\n```')])
    sent = []
    monkeypatch.setattr(llm.requests, "post", lambda url, json, headers, timeout: sent.append(json) or next(replies))
    assert llm.ask_json("p", SCHEMA) == {"title": "Fenced"}
    assert sent[1]["response_format"] == {"type": "json_object"} and "JSON schema" in sent[1]["messages"][0]["content"]


def test_cloud_failure_falls_back_to_local(monkeypatch):
    def post(url, json, headers=None, timeout=None):
        if "gw" in url:
            raise requests.ConnectionError("down")
        return Resp(200, {"message": {"content": '{"title": "Local"}'}})
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm, "installed_models", lambda: ["qwen3:8b"])
    assert llm.ask_json("p", SCHEMA) == {"title": "Local"}


def test_reply_missing_required_field_is_rejected_then_falls_back(monkeypatch):
    calls = []
    def post(url, json, headers=None, timeout=None):
        calls.append(url)
        return chat('{"other": 1}') if "gw" in url else Resp(200, {"message": {"content": '{"title": "Local"}'}})
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm, "installed_models", lambda: ["qwen3:8b"])
    assert llm.ask_json("p", SCHEMA) == {"title": "Local"}
    assert any("11434" in c for c in calls)


def test_busy_cloud_model_fails_over_to_another_working_cloud_model(monkeypatch):
    monkeypatch.setattr(llm, "working_models", lambda: ["test-model", "backup-model"])
    used = []
    def post(url, json, headers=None, timeout=None):
        used.append(json.get("model"))
        return Resp(503, {}) if json.get("model") == "test-model" else chat('{"title": "Backup"}')
    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.ask_json("p", SCHEMA) == {"title": "Backup"}
    assert used == ["test-model", "backup-model"]



def test_rate_limited_gateway_is_retried_before_falling_back(monkeypatch):
    monkeypatch.setattr(llm, "working_models", lambda: [])
    replies = iter([Resp(429, {}), chat('{"title": "Second round"}')])
    monkeypatch.setattr(llm.requests, "post", lambda url, json, headers=None, timeout=None: next(replies))
    assert llm.ask_json("p", SCHEMA) == {"title": "Second round"}
    assert llm.LAST["model"] == "cloud:test-model"



def test_out_of_quota_model_is_rested_so_later_calls_skip_straight_to_a_backup(monkeypatch):
    monkeypatch.setattr(llm, "working_models", lambda: ["HERMES"])
    calls = []
    def post(url, json, headers=None, timeout=None):
        calls.append(json["model"])
        return Resp(429, {}) if json["model"] == "test-model" else chat('{"title": "combo"}')
    monkeypatch.setattr(llm.requests, "post", post)
    for _ in range(3):
        assert llm.ask_json("p", SCHEMA) == {"title": "combo"}
    assert calls == ["test-model", "HERMES", "test-model", "HERMES", "HERMES"]   # 2 strikes, then skipped



def test_once_every_cloud_model_fails_later_calls_go_straight_to_local(monkeypatch):
    calls = []
    def post(url, json, headers=None, timeout=None):
        calls.append(url)
        if "gw" in url:
            raise requests.ConnectionError("down")
        return Resp(200, {"message": {"content": '{"title": "Local"}'}})
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm, "installed_models", lambda: ["qwen3:8b"])
    llm.ask_json("p", SCHEMA)
    n = len(calls)
    assert llm.ask_json("p", SCHEMA) == {"title": "Local"}
    assert len(calls) == n + 1 and "11434" in calls[-1]                     # no cloud attempts, no waiting
