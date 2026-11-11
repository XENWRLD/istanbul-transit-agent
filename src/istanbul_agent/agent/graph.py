"""LangGraph ReAct agent'ı, MCP sunucularına stdio üzerinden bağlı.

Araçlar agent'ın içinde tanımlı değil: her sunucu ayrı bir süreç olarak
stdio üzerinden konuşuyor ve araç listesi oradan geliyor. Fazladan bir
katman gibi görünüyor ama bütün noktası bu — araçlar aynı şekilde Claude
Desktop'a veya başka bir MCP istemcisine de bağlanabiliyor, ve agent'ın
gördüğü şey ile dışarıya sunulan şey birebir aynı oluyor.

**Oturumlar agent çalıştığı sürece açık tutuluyor.** `get_tools()` her araç
çağrısında yeni bir oturum açıyor; `knowledge_server` için bu, BGE-M3'ü her
aramada yeniden yüklemek demekti. Bunun yerine her sunucu için bir oturum
`AsyncExitStack` ile açılıyor ve araç listeleri birleştiriliyor: sunucu
süreci bir kez kalkıyor, model bir kez yükleniyor.

Üç sunucu bağlı: `transit` (8 araç), `knowledge` (1 araç) ve `city`
(2 araç) — toplam 11 araç. `city` Faz 2'de eklendi (karar 0012).

LLM yalnızca `istanbul_agent.llm.get_llm()` üzerinden kurulur (proje
kuralı); sağlayıcı `.env` içindeki `LLM_PROVIDER` ile ya da `provider`
parametresiyle seçilir.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from istanbul_agent.agent.prompt import SYSTEM_PROMPT
from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.llm import get_llm

__all__ = [
    "DEFAULT_SERVERS",
    "NUDGE",
    "AgentRun",
    "ToolCall",
    "answer",
    "istanbul_agent",
    "transit_agent",
]


def _stdio(module: str) -> dict[str, Any]:
    """Sunucuyu modül olarak başlat: projenin venv'i ve importları geçerli olsun."""
    return {
        "command": sys.executable,
        "args": ["-m", module],
        "transport": "stdio",
        "cwd": str(PROJECT_ROOT),
    }


SERVER_COMMANDS: dict[str, dict[str, Any]] = {
    "transit": _stdio("istanbul_agent.servers.transit_server"),
    "knowledge": _stdio("istanbul_agent.servers.knowledge_server"),
    "city": _stdio("istanbul_agent.servers.city_server"),
}

DEFAULT_SERVERS: tuple[str, ...] = ("transit", "knowledge", "city")


@dataclass
class ToolCall:
    """One tool call the model made, with what came back."""

    name: str
    arguments: dict[str, Any]
    result: str = ""


@dataclass
class AgentRun:
    """What one question produced: the answer, the calls, the time taken."""

    question: str
    answer: str
    provider: str
    model: str
    seconds: float
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    messages: list[BaseMessage] = field(default_factory=list)
    nudged: bool = False
    unverified: bool = False

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]


@asynccontextmanager
async def istanbul_agent(
    provider: str | None = None,
    *,
    max_steps: int = 8,
    servers: Sequence[str] = DEFAULT_SERVERS,
) -> AsyncIterator[tuple[Any, str, str]]:
    """A ReAct agent wired to the MCP servers over stdio.

    Yields `(agent, provider, model)`. Every MCP session lives as long as the
    context manager, so tools stay callable while the graph runs and each
    server process starts only once.
    """
    from contextlib import AsyncExitStack

    from langchain_mcp_adapters.tools import load_mcp_tools

    from istanbul_agent.config import get_settings

    unknown = [name for name in servers if name not in SERVER_COMMANDS]
    if unknown:
        known = ", ".join(sorted(SERVER_COMMANDS))
        raise KeyError(f"bilinmeyen MCP sunucusu: {unknown} (bilinenler: {known})")

    llm = get_llm(provider)
    model_name = getattr(llm, "model", None) or getattr(llm, "model_name", "?")
    used = provider or get_settings().llm_provider

    client = MultiServerMCPClient({name: SERVER_COMMANDS[name] for name in servers})
    async with AsyncExitStack() as stack:
        tools: list[Any] = []
        for name in servers:
            session = await stack.enter_async_context(client.session(name))
            tools.extend(await load_mcp_tools(session))
        agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
        agent = agent.with_config({"recursion_limit": max_steps * 2})
        yield agent, used, str(model_name)


# Faz 1'den kalan ad; tek sunucuya bağlanmak isteyen çağrılar için.
@asynccontextmanager
async def transit_agent(
    provider: str | None = None, *, max_steps: int = 8
) -> AsyncIterator[tuple[Any, str, str]]:
    """Yalnızca `transit_server`'a bağlı agent (geriye dönük ad)."""
    async with istanbul_agent(provider, max_steps=max_steps, servers=("transit",)) as bundle:
        yield bundle


# Araç çağırmayan modele verilen tek seferlik uyarı. Prompt'la çözülemedi:
# yerel model (qwen3.5:4b) "metroya bisikletle binebilir miyim" sorusunda
# araç çağırmadan bisiklet dolabı ve izin belgesi uydurdu, prompt'a kaynak
# gösterme kuralı eklendiğinde de **kaynağı** uydurdu ("Metro İstanbul
# SSS'ine göre"). Bu yüzden koruma grafikte: araç çağrılmadıysa bir kez
# uyarılıyor, ikinci turda da çağırmazsa cevap `unverified` işaretleniyor.
# Soru uyarının içinde **yeniden yazılıyor**: ilk denemede uyarı tek başına
# gönderildi ve yerel model soruyu kaybedip "hangi soruyu soruyorsunuz?"
# diye cevap verdi.
NUDGE = (
    "Bu soruyu araç çağırmadan cevapladın: {question!r}\n\n"
    "İstanbul ulaşımı hakkındaki hiçbir olguyu kendi belleğinden "
    "söyleyemezsin: saat, süre, fiyat, kural, durak, hat ve konum bilgisi "
    "yalnızca araçlardan gelir. Şimdi bu soru için uygun aracı çağır ve "
    "cevabı aracın döndürdüğü veriye dayandır. Araçlardan hiçbiri bu soruyu "
    "cevaplayamıyorsa 'bu bilgi elimdeki kaynaklarda yok' de ve kaynak "
    "cümlesi yazma."
)


def _text(content: Any) -> str:
    """The readable text of a message.

    Gemini answers with a list of content blocks (text plus an opaque
    signature), so taking `content` as a string would print the blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            if isinstance(block, dict)
            else block
            if isinstance(block, str)
            else ""
            for block in content
        ]
        return chr(10).join(part for part in parts if part).strip()
    return str(content or "")


def _collect(messages: list[BaseMessage]) -> list[ToolCall]:
    """Pair each tool call with the message that answered it."""
    calls: dict[str, ToolCall] = {}
    order: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                identifier = call.get("id") or f"{call['name']}-{len(order)}"
                calls[identifier] = ToolCall(
                    name=call["name"], arguments=dict(call.get("args") or {})
                )
                order.append(identifier)
        elif isinstance(message, ToolMessage):
            existing = calls.get(str(message.tool_call_id))
            if existing is not None:
                existing.result = str(message.content)
    return [calls[identifier] for identifier in order]


async def ask(
    agent: Any,
    question: str,
    *,
    provider: str = "?",
    model: str = "?",
    nudge: bool = True,
) -> AgentRun:
    """Ask one question on an **already open** agent.

    `answer()` bunu her soru için yeni bir MCP yığını açarak çağırıyor;
    aynı yığına çok soru soracak çağıranlar (değerlendirme betiği) bu
    fonksiyonu doğrudan kullanıyor. Ölçüm: yığını açmak 15.0 s sürüyor
    (üç sunucu; `knowledge_server` gömme modelini açılışta yüklemek
    zorunda, karar 0010) ve senaryo başına 46.8 s'lik medyanın üçte biri
    buydu. Modele giden girdi aynı kaldığı için bunu bir kez açmak
    doğruluğu değiştirmiyor, yalnızca süreyi (karar 0014).

    `nudge` açıkken araç çağırmayan bir cevap bir kez geri çevrilir (bkz.
    `NUDGE`). İkinci turda da araç çağrılmazsa sonuç `unverified=True`
    döner: kaynaksız bir cevabı sessizce doğru gibi sunmak, kaynak
    gösteren bir sistemde kabul edilemez.
    """
    import time

    started = time.monotonic()
    history: list[BaseMessage] = [SystemMessage(SYSTEM_PROMPT), HumanMessage(question)]
    nudged = False
    try:
        state = await agent.ainvoke({"messages": history})
        if nudge and not _collect(list(state["messages"])):
            # Araç çağrılmadı: bir kez uyar ve yeniden sor.
            nudged = True
            state = await agent.ainvoke(
                {
                    "messages": [
                        *list(state["messages"]),
                        HumanMessage(NUDGE.format(question=question)),
                    ]
                }
            )
    except Exception as problem:
        return AgentRun(
            question=question,
            answer="",
            provider=provider,
            model=model,
            seconds=time.monotonic() - started,
            error=f"{type(problem).__name__}: {problem}",
        )

    messages = list(state["messages"])
    final = next(
        (
            _text(message.content)
            for message in reversed(messages)
            if isinstance(message, AIMessage) and _text(message.content)
        ),
        "",
    )
    calls = _collect(messages)
    return AgentRun(
        question=question,
        answer=final,
        provider=provider,
        model=model,
        seconds=time.monotonic() - started,
        tool_calls=calls,
        messages=messages,
        nudged=nudged,
        unverified=not calls,
    )


async def answer(
    question: str,
    provider: str | None = None,
    *,
    max_steps: int = 8,
    servers: Sequence[str] = DEFAULT_SERVERS,
    nudge: bool = True,
) -> AgentRun:
    """Ask one question, opening and closing its own MCP stack.

    Tek soruluk yol: CLI, FastAPI ve Gradio bunu kullanıyor. Çok soru
    soracaksan `istanbul_agent()` ile yığını bir kez aç ve `ask()` çağır.
    """
    async with istanbul_agent(provider, max_steps=max_steps, servers=servers) as (
        agent,
        used,
        model_name,
    ):
        return await ask(agent, question, provider=used, model=str(model_name), nudge=nudge)
