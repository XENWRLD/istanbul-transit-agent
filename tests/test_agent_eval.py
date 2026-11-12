"""Senaryo değerlendirmesinin kendisi: ölçüm doğru şeyi mi ölçüyor?

Değerlendirme betiği `eval/` altında ve paket değil, bu yüzden dosya
yolundan yükleniyor. LLM çağrısı yok; sınanan şey karar mantığı:

- beklenen araçlar çağrılmadıysa senaryo geçmemeli,
- sıra bozulduysa geçmemeli,
- doğru aracı çağırıp sonucu yanlış aktaran cevap (tazelik uyarısı yok,
  hata payı yok) geçmemeli,
- **kaynaksız cevap** geçmemeli — araç beklenmeyen senaryolar dışında.

Son madde projenin asıl kuralı: araç çağırmadan verilen cevap, doğru
görünse bile kaynaksızdır.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from istanbul_agent.agent.graph import AgentRun, ToolCall
from istanbul_agent.config import PROJECT_ROOT

EVAL_PATH = PROJECT_ROOT / "eval" / "run_agent_eval.py"

# Üç MCP sunucusunun sunduğu araçların tamamı (Faz 2 ile 11, karar 0019 ile 12).
ALL_TOOLS = {
    "geocode_place",
    "find_nearby_stops",
    "get_lines_at_stop",
    "get_line_route",
    "get_rail_travel_time",
    "get_next_departures",
    "get_service_alerts",
    "get_station_accessibility",
    "search_knowledge",
    "find_parking",
    "get_traffic_status",
    "find_mobility_options",
}


def load_module():
    spec = importlib.util.spec_from_file_location("run_agent_eval", EVAL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_agent_eval"] = module
    spec.loader.exec_module(module)
    return module


harness = load_module()


def run(
    *,
    tools: list[str] | None = None,
    answer: str = "cevap",
    unverified: bool = False,
    error: str | None = None,
) -> AgentRun:
    calls = [ToolCall(name=name, arguments={}) for name in (tools or [])]
    return AgentRun(
        question="soru",
        answer=answer,
        provider="ollama",
        model="qwen3.5:4b",
        seconds=1.0,
        tool_calls=calls,
        error=error,
        unverified=unverified or not calls,
    )


def scenario(**kwargs) -> harness.Scenario:
    base = {"id": "t-01", "question": "soru"}
    return harness.Scenario(**{**base, **kwargs})


class TestSubsequence:
    def test_exact_order(self):
        assert harness.is_subsequence(["a", "b"], ["a", "b"]) is True

    def test_other_calls_in_between_are_allowed(self):
        assert harness.is_subsequence(["a", "b"], ["a", "x", "b"]) is True

    def test_wrong_order_fails(self):
        assert harness.is_subsequence(["a", "b"], ["b", "a"]) is False

    def test_missing_call_fails(self):
        assert harness.is_subsequence(["a", "b"], ["a"]) is False


class TestToolExpectations:
    def test_all_expected_tools_must_be_called(self):
        spec = scenario(expect_tools=["geocode_place", "find_nearby_stops"])
        assert harness.check(spec, run(tools=["geocode_place"]))["tools_ok"] is False
        assert (
            harness.check(spec, run(tools=["geocode_place", "find_nearby_stops"]))["tools_ok"]
            is True
        )

    def test_expect_any_accepts_one_of_them(self):
        spec = scenario(expect_tools=["geocode_place", "find_nearby_stops"], expect_any=True)
        assert harness.check(spec, run(tools=["find_nearby_stops"]))["tools_ok"] is True

    def test_a_forbidden_tool_fails_the_scenario(self):
        spec = scenario(expect_tools=["geocode_place"], forbid_tools=["get_line_route"])
        result = harness.check(spec, run(tools=["geocode_place", "get_line_route"]))
        assert result["forbidden_called"] == ["get_line_route"]
        assert result["passed"] is False

    def test_order_is_checked_when_given(self):
        spec = scenario(
            expect_tools=["find_nearby_stops", "get_lines_at_stop"],
            expect_order=["find_nearby_stops", "get_lines_at_stop"],
        )
        reversed_run = run(tools=["get_lines_at_stop", "find_nearby_stops"])
        assert harness.check(spec, reversed_run)["order_ok"] is False
        assert harness.check(spec, reversed_run)["passed"] is False

    def test_order_is_not_checked_when_absent(self):
        spec = scenario(expect_tools=["geocode_place"])
        assert harness.check(spec, run(tools=["geocode_place"]))["order_ok"] is None


class TestMentions:
    def test_a_missing_phrase_fails_the_scenario(self):
        """Doğru aracı çağırıp tazelik uyarısını yutmak da hatadır."""
        spec = scenario(expect_tools=["get_next_departures"], must_mention=["2020", "eski"])
        result = harness.check(spec, run(tools=["get_next_departures"], answer="Son sefer 00:00."))
        assert result["mention_ok"] is False
        assert result["passed"] is False

    def test_any_one_phrase_is_enough(self):
        spec = scenario(expect_tools=["get_next_departures"], must_mention=["2020", "eski"])
        result = harness.check(
            spec,
            run(tools=["get_next_departures"], answer="Saatler 2020 verisinden."),
        )
        assert result["mention_ok"] is True

    def test_matching_ignores_case(self):
        spec = scenario(expect_tools=["x"], must_mention=["YAKLAŞIK"])
        assert harness.check(spec, run(tools=["x"], answer="yaklaşık 50 dk"))["mention_ok"] is True


class TestUnverifiedAnswers:
    def test_an_answer_without_tools_fails_when_a_tool_was_expected(self):
        spec = scenario(expect_tools=["search_knowledge"])
        result = harness.check(spec, run(tools=[], answer="Bisiklet yasaktır."))
        assert result["unverified"] is True
        assert result["passed"] is False

    def test_no_tool_is_correct_when_the_tool_does_not_exist_yet(self):
        """Otopark, trafik, hava kalitesi: Faz 2'de olmayan araçlar."""
        spec = scenario(expect_tools=[], must_mention=["yok"])
        result = harness.check(spec, run(tools=[], answer="Bu bilgi elimdeki kaynaklarda yok."))
        assert result["passed"] is True

    def test_a_refusal_scenario_still_needs_its_phrase(self):
        spec = scenario(expect_tools=[], must_mention=["yok"])
        result = harness.check(spec, run(tools=[], answer="Taksim'de 3 otopark var."))
        assert result["passed"] is False


class TestErrors:
    def test_an_errored_run_never_passes(self):
        spec = scenario(expect_tools=[])
        result = harness.check(spec, run(tools=[], error="McpError: Connection closed"))
        assert result["passed"] is False


@pytest.fixture(scope="module")
def scenarios():
    return harness.load_scenarios()


class TestScenarioFile:
    def test_the_file_has_at_least_forty_scenarios(self, scenarios):
        """PLAN.md Faz 4 "bitti" tanımı: en az 40 senaryo."""
        assert len(scenarios) >= 40

    def test_ids_are_unique(self, scenarios):
        ids = [item.id for item in scenarios]
        assert len(ids) == len(set(ids))

    def test_every_expected_tool_exists(self, scenarios):
        known = ALL_TOOLS
        for item in scenarios:
            for name in [*item.expect_tools, *item.expect_order, *item.forbid_tools]:
                assert name in known, f"{item.id}: bilinmeyen araç {name}"

    def test_the_set_covers_every_tool(self, scenarios):
        covered = {name for item in scenarios for name in item.expect_tools}
        assert covered == ALL_TOOLS

    def test_the_set_includes_multi_step_and_refusal_scenarios(self, scenarios):
        assert any(len(item.expect_tools) > 1 for item in scenarios)
        assert any(item.expects_no_tool for item in scenarios)

    def test_every_scenario_has_a_note(self, scenarios):
        for item in scenarios:
            assert item.note, item.id


class TestOptions:
    """Koşuyu hızlandıran seçenek doğruluğu değiştirmiyor; yardım bunu yazıyor."""

    def test_concurrency_defaults_to_serial(self):
        assert harness.build_parser().parse_args([]).concurrency == 1

    def test_concurrency_can_be_raised(self):
        assert harness.build_parser().parse_args(["--concurrency", "2"]).concurrency == 2

    def test_the_help_text_names_the_resource_ceiling(self):
        """Risk yanlış cevap değil, çöken model çalıştırıcısı (karar 0014)."""
        action = next(
            item for item in harness.build_parser()._actions if item.dest == "concurrency"
        )
        assert "doğruluğu" in (action.help or "")
        assert "VRAM" in (action.help or "")


class TestCacheKey:
    """Anahtar, cevabı değiştiren her girdiyi taşımak zorunda (karar 0016).

    Taşımadığı girdi değişince betik eski cevapları önbellekten okuyup
    yeni ölçüm diye raporlar — ölçümün kendisi yalan olur.
    """

    def test_the_tool_surface_changes_the_key(self):
        case = scenario()
        before = harness.cache_key(case, "ollama", "qwen3.5:4b", "aaaaaaaaaaaa")
        after = harness.cache_key(case, "ollama", "qwen3.5:4b", "bbbbbbbbbbbb")
        assert before != after

    def test_the_same_surface_gives_the_same_key(self):
        case = scenario()
        first = harness.cache_key(case, "ollama", "qwen3.5:4b", "aaaaaaaaaaaa")
        second = harness.cache_key(case, "ollama", "qwen3.5:4b", "aaaaaaaaaaaa")
        assert first == second

    def test_an_absent_surface_keeps_the_older_key(self):
        # Yığın açılmadan anahtar hesaplanabilsin diye `surface` isteğe
        # bağlı; verilmediğinde anahtar karar 0015'teki biçimde kalır.
        case = scenario()
        assert harness.cache_key(case, "ollama", "qwen3.5:4b") == harness.cache_key(
            case, "ollama", "qwen3.5:4b", ""
        )

    def test_the_scenario_the_provider_and_the_model_still_separate_keys(self):
        one = harness.cache_key(scenario(id="t-01"), "ollama", "qwen3.5:4b", "aaaaaaaaaaaa")
        two = harness.cache_key(scenario(id="t-02"), "ollama", "qwen3.5:4b", "aaaaaaaaaaaa")
        three = harness.cache_key(scenario(id="t-01"), "gemini", "qwen3.5:4b", "aaaaaaaaaaaa")
        four = harness.cache_key(scenario(id="t-01"), "ollama", "gemini-3.5-flash", "aaaaaaaaaaaa")
        assert len({one, two, three, four}) == 4

    def test_the_key_is_a_usable_file_name(self):
        key = harness.cache_key(scenario(), "ollama", "qwen3.5:4b", "aaaaaaaaaaaa")
        assert ":" not in key and "/" not in key
