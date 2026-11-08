"""LangGraph ReAct agent'ı, transit_server'a MCP üzerinden bağlı.

Araçlar agent'ın içinde tanımlı değil: `transit_server` ayrı bir süreç
olarak stdio üzerinden konuşuyor ve araç listesi oradan geliyor. Fazladan
bir katman gibi görünüyor ama bütün noktası bu — araçlar aynı şekilde
Claude Desktop'a veya başka bir MCP istemcisine de bağlanabiliyor, ve
agent'ın gördüğü şey ile dışarıya sunulan şey birebir aynı oluyor.

LLM yalnızca `istanbul_agent.llm.get_llm()` üzerinden kurulur (proje
kuralı); sağlayıcı `.env` içindeki `LLM_PROVIDER` ile ya da `provider`
parametresiyle seçilir.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from istanbul_agent.agent.prompt import SYSTEM_PROMPT
from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.llm import get_llm

__all__ = ["AgentRun", "ToolCall", "answer", "transit_agent"]

# The server is started as a module so it runs with the project's venv and
# its own imports, exactly as an external MCP client would start it.
SERVER_COMMAND = {
    "transit": {
        "command": sys.executable,
        "args": ["-m", "istanbul_agent.servers.transit_server"],
        "transport": "stdio",
        "cwd": str(PROJECT_ROOT),
    }
}


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


@asynccontextmanager
async def transit_agent(
    provider: str | None = None, *, max_steps: int = 8
) -> AsyncIterator[tuple[Any, str, str]]:
    """A ReAct agent wired to transit_server over stdio.

    Yields `(agent, provider, model)`. The MCP session lives as long as the
    context manager, so tools stay callable while the graph runs.
    """
    from istanbul_agent.config import get_settings

    llm = get_llm(provider)
    model_name = getattr(llm, "model", None) or getattr(llm, "model_name", "?")
    used = provider or get_settings().llm_provider

    client = MultiServerMCPClient(SERVER_COMMAND)
    async with client.session("transit") as session:
        from langchain_mcp_adapters.tools import load_mcp_tools

        tools = await load_mcp_tools(session)
        agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
        agent = agent.with_config({"recursion_limit": max_steps * 2})
        yield agent, used, str(model_name)


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


async def answer(question: str, provider: str | None = None, *, max_steps: int = 8) -> AgentRun:
    """Ask one question and return the answer with what it took to get it."""
    import time

    started = time.monotonic()
    async with transit_agent(provider, max_steps=max_steps) as (agent, used, model_name):
        try:
            state = await agent.ainvoke(
                {"messages": [SystemMessage(SYSTEM_PROMPT), HumanMessage(question)]}
            )
        except Exception as problem:
            return AgentRun(
                question=question,
                answer="",
                provider=used,
                model=model_name,
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
    return AgentRun(
        question=question,
        answer=final,
        provider=used,
        model=model_name,
        seconds=time.monotonic() - started,
        tool_calls=_collect(messages),
        messages=messages,
    )
