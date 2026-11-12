"""Agent grafiği: sunucu kaydı, uyarı turu, kaynaksız cevap işareti.

LLM çağrısı yok; burada sınanan şey modelin cevabı değil **grafiğin
davranışı**: hangi sunucular bağlı, araç çağrılmadığında ne oluyor, araç
çağrıları mesaj akışından nasıl toplanıyor.

Uyarı turu ve `unverified` işareti ölçümle geldi (karar 0010): yerel model
"metroya bisikletle binebilir miyim" sorusunda araç çağırmadan cevap
uydurdu, prompt'a kaynak gösterme kuralı eklendiğinde de kaynağı uydurdu.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from istanbul_agent.agent.graph import (
    DEFAULT_SERVERS,
    NUDGE,
    SERVER_COMMANDS,
    AgentRun,
    ToolCall,
    _collect,
    _text,
    ask,
    tool_surface_digest,
)


class TestServerRegistry:
    def test_every_server_is_registered(self):
        assert set(DEFAULT_SERVERS) == {"transit", "knowledge", "city"}
        for name in DEFAULT_SERVERS:
            assert name in SERVER_COMMANDS

    def test_servers_start_as_modules_with_the_project_venv(self):
        """Sunucu dışarıdan bir MCP istemcisinin başlatacağı şekilde kalkar."""
        for name in DEFAULT_SERVERS:
            command = SERVER_COMMANDS[name]
            assert command["args"][0] == "-m"
            assert command["args"][1].startswith("istanbul_agent.servers.")
            assert command["transport"] == "stdio"

    def test_the_city_server_is_wired(self):
        """Faz 2'de eklendi (karar 0012): otopark ve trafik araçları."""
        assert SERVER_COMMANDS["city"]["args"][1].endswith("city_server")


class TestNudge:
    def test_the_nudge_restates_the_question(self):
        """İlk sürüm soruyu tekrarlamıyordu ve model 'hangi soru?' diye sordu."""
        text = NUDGE.format(question="Metroya bisikletle binebilir miyim?")
        assert "Metroya bisikletle binebilir miyim?" in text

    def test_the_nudge_forbids_inventing_a_citation(self):
        text = NUDGE.format(question="x")
        assert "kaynak" in text.casefold()

    def test_the_nudge_names_what_cannot_be_recalled(self):
        text = NUDGE.format(question="x").casefold()
        for term in ("saat", "fiyat", "kural", "durak"):
            assert term in text


class TestAgentRun:
    def test_tool_names_lists_the_calls_in_order(self):
        run = AgentRun(
            question="s",
            answer="c",
            provider="ollama",
            model="m",
            seconds=1.0,
            tool_calls=[ToolCall("geocode_place", {}), ToolCall("find_nearby_stops", {})],
        )
        assert run.tool_names == ["geocode_place", "find_nearby_stops"]

    def test_an_answer_without_tools_defaults_to_verified_only_when_set(self):
        """`unverified` yalnızca `answer()` tarafından hesaplanır."""
        run = AgentRun(question="s", answer="c", provider="ollama", model="m", seconds=1.0)
        assert run.unverified is False
        assert run.nudged is False


class TestCollect:
    def test_tool_calls_are_paired_with_their_results(self):
        messages = [
            HumanMessage("soru"),
            AIMessage(
                content="",
                tool_calls=[{"name": "geocode_place", "args": {"query": "Kadıköy"}, "id": "a"}],
            ),
            ToolMessage(content="{}", tool_call_id="a"),
            AIMessage(content="cevap"),
        ]
        calls = _collect(messages)
        assert [call.name for call in calls] == ["geocode_place"]
        assert calls[0].arguments == {"query": "Kadıköy"}
        assert calls[0].result == "{}"

    def test_calls_keep_their_order(self):
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"name": "find_nearby_stops", "args": {}, "id": "1"}],
            ),
            ToolMessage(content="x", tool_call_id="1"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_lines_at_stop", "args": {}, "id": "2"}],
            ),
            ToolMessage(content="y", tool_call_id="2"),
        ]
        assert [call.name for call in _collect(messages)] == [
            "find_nearby_stops",
            "get_lines_at_stop",
        ]

    def test_no_tool_calls_is_an_empty_list(self):
        assert _collect([HumanMessage("soru"), AIMessage(content="cevap")]) == []


class TestText:
    def test_a_plain_string_passes_through(self):
        assert _text("merhaba") == "merhaba"

    def test_gemini_content_blocks_are_joined(self):
        """Gemini cevabı metin bloğu listesi döndürüyor."""
        blocks = [{"text": "bir"}, {"signature": "opaque"}, {"text": "iki"}]
        assert _text(blocks) == "bir\niki"

    def test_empty_content(self):
        assert _text(None) == ""
        assert _text([]) == ""


class TestAsk:
    """`ask()` açık bir agent'la çalışır; yığını açmak çağıranın işi.

    Yığını senaryo başına açmak 15.0 s'ye mal oluyordu (üç sunucu,
    `knowledge_server` BGE-M3'ü açılışta yükler) ve 54 senaryoda bunun
    ~13 dakikası sadece yeniden yüklemeydi. `ask()` modele giden girdiyi
    değiştirmiyor, yalnızca kimin yığın açtığını (karar 0014).
    """

    class FakeAgent:
        """`ainvoke` çağrılarını sayan ve hazır durum döndüren agent."""

        def __init__(self, *states: dict) -> None:
            self.states = list(states)
            self.calls: list[list] = []

        async def ainvoke(self, payload: dict) -> dict:
            self.calls.append(list(payload["messages"]))
            return self.states[min(len(self.calls) - 1, len(self.states) - 1)]

    def with_tool(self) -> dict:
        return {
            "messages": [
                HumanMessage("soru"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "geocode_place", "args": {}, "id": "a"}],
                ),
                ToolMessage(content="{}", tool_call_id="a"),
                AIMessage(content="Kadıköy bulundu."),
            ]
        }

    def without_tool(self) -> dict:
        return {"messages": [HumanMessage("soru"), AIMessage(content="Bence 20 dakika.")]}

    async def test_it_uses_the_agent_it_is_given(self):
        agent = self.FakeAgent(self.with_tool())
        run = await ask(agent, "Kadıköy nerede?", provider="ollama", model="m")
        assert len(agent.calls) == 1, "yığın yeniden açılmaz, tek tur koşar"
        assert run.answer == "Kadıköy bulundu."
        assert run.tool_names == ["geocode_place"]
        assert run.unverified is False

    async def test_the_provider_and_model_are_carried_through(self):
        agent = self.FakeAgent(self.with_tool())
        run = await ask(agent, "s", provider="gemini", model="gemini-3.5-flash")
        assert (run.provider, run.model) == ("gemini", "gemini-3.5-flash")

    async def test_an_answer_without_a_tool_is_nudged_once(self):
        agent = self.FakeAgent(self.without_tool(), self.with_tool())
        run = await ask(agent, "Metroya bisikletle binebilir miyim?", provider="ollama", model="m")
        assert len(agent.calls) == 2
        assert run.nudged is True
        assert run.unverified is False
        assert any(
            "Metroya bisikletle binebilir miyim?" in getattr(message, "content", "")
            for message in agent.calls[1]
        ), "uyarı soruyu yeniden yazar"

    async def test_two_toolless_turns_mark_the_answer_unverified(self):
        agent = self.FakeAgent(self.without_tool())
        run = await ask(agent, "s", provider="ollama", model="m")
        assert len(agent.calls) == 2
        assert (run.nudged, run.unverified) == (True, True)

    async def test_the_nudge_can_be_switched_off(self):
        agent = self.FakeAgent(self.without_tool())
        run = await ask(agent, "s", provider="ollama", model="m", nudge=False)
        assert len(agent.calls) == 1
        assert (run.nudged, run.unverified) == (False, True)

    async def test_a_failing_agent_becomes_an_errored_run(self):
        class Broken:
            async def ainvoke(self, payload: dict) -> dict:
                raise RuntimeError("model runner has unexpectedly stopped")

        run = await ask(Broken(), "s", provider="ollama", model="m")
        assert run.error and "model runner" in run.error
        assert run.answer == ""


class FakeTool:
    """`load_mcp_tools`'un döndürdüğü aracın özeti ilgilendiren üç alanı."""

    def __init__(self, name: str, description: str, schema: dict | None = None) -> None:
        self.name = name
        self.description = description
        self.args_schema = schema if schema is not None else {"type": "object"}


def surface(*tools: FakeTool) -> str:
    return tool_surface_digest(list(tools))


class TestToolSurfaceDigest:
    """Özet, modelin gördüğü yüzeyin değiştiğini yakalamak zorunda.

    Değerlendirme önbelleği buna bakıyor (karar 0016): yakalamadığı her
    değişiklik, ölçümün sessizce eski cevapları raporlaması demek.
    """

    def test_the_same_surface_gives_the_same_digest(self):
        first = surface(FakeTool("geocode_place", "Yer bulur."))
        second = surface(FakeTool("geocode_place", "Yer bulur."))
        assert first == second

    def test_a_changed_description_changes_the_digest(self):
        # Proje kuralı: araç açıklamasını değiştirmek davranış değişikliğidir.
        before = surface(FakeTool("geocode_place", "Yer bulur."))
        after = surface(FakeTool("geocode_place", "Yer bulur. Metro için kullanma."))
        assert before != after

    def test_a_changed_schema_changes_the_digest(self):
        before = surface(FakeTool("find_nearby_stops", "Durak arar.", {"type": "object"}))
        after = surface(
            FakeTool(
                "find_nearby_stops",
                "Durak arar.",
                {"type": "object", "properties": {"mode": {"type": "string"}}},
            )
        )
        assert before != after

    def test_a_changed_order_changes_the_digest(self):
        # Araç sırası da modele giden girdinin parçası.
        one = FakeTool("geocode_place", "Yer bulur.")
        two = FakeTool("find_parking", "Otopark bulur.")
        assert surface(one, two) != surface(two, one)

    def test_dropping_a_tool_changes_the_digest(self):
        one = FakeTool("geocode_place", "Yer bulur.")
        two = FakeTool("find_parking", "Otopark bulur.")
        assert surface(one, two) != surface(one)

    def test_a_pydantic_schema_is_rendered_too(self):
        # MCP araçları şemayı sözlük olarak veriyor, ama LangChain aracı
        # Pydantic modeli de verebilir; ikisi de özete girmeli.
        from pydantic import BaseModel

        class Args(BaseModel):
            query: str

        class Other(BaseModel):
            query: int

        with_model = tool_surface_digest([FakeTool("geocode_place", "Yer bulur.", Args)])
        with_other = tool_surface_digest([FakeTool("geocode_place", "Yer bulur.", Other)])
        assert with_model != with_other

    def test_the_digest_is_short_and_hex(self):
        value = surface(FakeTool("geocode_place", "Yer bulur."))
        assert len(value) == 12
        assert all(char in "0123456789abcdef" for char in value)
