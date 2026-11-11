"""HTTP uç noktası: sağlık kontrolü ve `/ask` sözleşmesi.

LLM çağrısı yok: `/ask` testleri agent'ı taklit ediyor. Sınanan şey
cevabın **taşıdığı alanlar** — özellikle `unverified`. Araç çağrılmadan
verilen cevabın kaynaksız olduğunu HTTP istemcisinin de görmesi gerekiyor
(karar 0010); bunu sessizce düşürmek, kaynak gösteren bir sistemde
kabul edilemez.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from istanbul_agent import api
from istanbul_agent.agent.graph import AgentRun, ToolCall


@pytest.fixture
def client():
    return TestClient(api.app)


def fake_run(**kwargs) -> AgentRun:
    base = {
        "question": "soru",
        "answer": "cevap",
        "provider": "ollama",
        "model": "qwen3.5:4b",
        "seconds": 1.23,
    }
    return AgentRun(**{**base, **kwargs})


class TestHealth:
    def test_health_reports_each_store_separately(self, client):
        payload = client.get("/health").json()
        assert set(payload) >= {"ok", "duckdb", "qdrant", "provider", "notes"}

    def test_health_says_what_to_run_when_a_store_is_missing(self, client, monkeypatch, tmp_path):
        """Ayakta olup verisi olmayan servis "sağlıklı" görünmemeli."""
        monkeypatch.setattr(api, "get_settings", api.get_settings)
        from istanbul_agent.store import db

        monkeypatch.setattr(db, "database_path", lambda: tmp_path / "yok.duckdb")
        payload = client.get("/health").json()
        assert payload["duckdb"] is False
        assert payload["ok"] is False
        assert any("ingest" in note for note in payload["notes"])


class TestAsk:
    def test_a_grounded_answer_carries_its_tool_calls(self, client, monkeypatch):
        async def fake_answer(question, provider=None, **kwargs) -> AgentRun:
            return fake_run(
                question=question,
                tool_calls=[ToolCall("get_rail_travel_time", {"from_station": "Kadıköy"})],
            )

        monkeypatch.setattr(api, "_answer", fake_answer)
        payload = client.post("/ask", json={"question": "Kadıköy'den Taksim'e?"}).json()
        assert payload["answer"] == "cevap"
        assert payload["tool_calls"][0]["name"] == "get_rail_travel_time"
        assert payload["tool_calls"][0]["arguments"] == {"from_station": "Kadıköy"}
        assert payload["unverified"] is False

    def test_an_ungrounded_answer_is_flagged(self, client, monkeypatch):
        async def fake_answer(question, provider=None, **kwargs) -> AgentRun:
            return fake_run(question=question, unverified=True, nudged=True)

        monkeypatch.setattr(api, "_answer", fake_answer)
        payload = client.post("/ask", json={"question": "soru"}).json()
        assert payload["unverified"] is True
        assert payload["nudged"] is True

    def test_an_error_is_reported_not_swallowed(self, client, monkeypatch):
        async def fake_answer(question, provider=None, **kwargs) -> AgentRun:
            return fake_run(question=question, answer="", error="McpError: Connection closed")

        monkeypatch.setattr(api, "_answer", fake_answer)
        payload = client.post("/ask", json={"question": "soru"}).json()
        assert "McpError" in payload["error"]

    def test_an_empty_question_is_rejected(self, client):
        assert client.post("/ask", json={"question": ""}).status_code == 422

    def test_an_unknown_provider_is_rejected(self, client):
        response = client.post("/ask", json={"question": "soru", "provider": "openai"})
        assert response.status_code == 422

    def test_max_steps_is_bounded(self, client):
        assert client.post("/ask", json={"question": "s", "max_steps": 99}).status_code == 422

    def test_the_provider_passes_through(self, client, monkeypatch):
        seen: dict[str, object] = {}

        async def fake_answer(question, provider=None, **kwargs) -> AgentRun:
            seen["provider"] = provider
            seen["max_steps"] = kwargs.get("max_steps")
            return fake_run(question=question, tool_calls=[ToolCall("geocode_place", {})])

        monkeypatch.setattr(api, "_answer", fake_answer)
        client.post("/ask", json={"question": "s", "provider": "gemini", "max_steps": 4})
        assert seen == {"provider": "gemini", "max_steps": 4}
