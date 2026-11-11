"""FastAPI uç noktası: agent'ı HTTP üzerinden sor.

    uv run uvicorn istanbul_agent.api:app --reload
    curl -X POST localhost:8000/ask -H "content-type: application/json" \
         -d '{"question": "Kadıköy''den Taksim''e metroyla kaç dakika?"}'

Cevap, CLI'nin bastığı şeyin aynısını taşır: metin, çağrılan araçlar ve
**`unverified`** bayrağı. Son alan önemli — araç çağrılmadan verilen cevap
kaynaksızdır ve bunu HTTP istemcisinin de görmesi gerekiyor (karar 0010).

`/health` üç şeyi söylüyor: yerel depo kurulu mu, metin indeksi kurulu mu,
hangi LLM sağlayıcısı yapılandırılmış. Ayağa kalkan bir servis "sağlıklı"
görünüp altında veri olmaması, bu projede en kötü sessiz hata olurdu.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from istanbul_agent.agent.graph import answer as _answer
from istanbul_agent.config import get_settings

app = FastAPI(
    title="İstanbul Ulaşım Agent'ı",
    description=(
        "İBB Açık Veri Portalı verilerini MCP araçları olarak sunan ve bunları bir "
        "LangGraph agent'ı ile birleştiren servis."
    ),
    version="0.1.0",
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, description="Soru, Türkçe")
    provider: Literal["gemini", "ollama"] | None = Field(
        None, description="LLM sağlayıcısı; verilmezse .env içindeki LLM_PROVIDER"
    )
    max_steps: int = Field(8, ge=1, le=20, description="Agent'ın en fazla adım sayısı")


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]


class AskResponse(BaseModel):
    question: str
    answer: str
    provider: str
    model: str
    seconds: float
    tool_calls: list[ToolCallOut]
    nudged: bool = Field(description="Araç çağrılmadığı için model bir kez uyarıldı mı")
    unverified: bool = Field(
        description=(
            "true ise cevap hiçbir araç çağrısına dayanmıyor, yani kaynaksız; "
            "istemci bunu kullanıcıya göstermeli"
        )
    )
    error: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    duckdb: bool = Field(description="data/transit.duckdb kurulu mu")
    qdrant: bool = Field(description="RAG indeksi kurulu mu")
    provider: str
    notes: list[str] = Field(default_factory=list)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Servis ayakta ama verisi kurulu mu?"""
    from istanbul_agent.rag.index import COLLECTION, QDRANT_PATH
    from istanbul_agent.store.db import database_path

    settings = get_settings()
    duckdb_ready = database_path().exists()
    notes: list[str] = []
    if not duckdb_ready:
        notes.append(
            "Yerel depo yok: uv run python -m istanbul_agent.store.ingest "
            "(ve ingest_rail, ingest_timetable)"
        )

    qdrant_ready = False
    if QDRANT_PATH.exists():
        # Yerel mod dizini tek sürece kilitliyor; sunucu açıksa burada
        # kilit hatası alınır ve bu "indeks yok" demek değildir.
        try:
            from contextlib import closing

            from istanbul_agent.rag.index import open_client

            with closing(open_client()) as client:
                qdrant_ready = bool(client.collection_exists(COLLECTION))
        except Exception as problem:
            notes.append(
                f"Metin indeksi açılamadı ({type(problem).__name__}); Qdrant yerel modu "
                "dizini tek sürece kilitliyor, knowledge_server açık olabilir."
            )
    else:
        notes.append("Metin indeksi yok: uv run python -m istanbul_agent.rag.ingest")

    return HealthResponse(
        ok=duckdb_ready,
        duckdb=duckdb_ready,
        qdrant=qdrant_ready,
        provider=settings.llm_provider,
        notes=notes,
    )


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Soruyu agent'a sor."""
    run = await _answer(request.question, request.provider, max_steps=request.max_steps)
    return AskResponse(
        question=run.question,
        answer=run.answer,
        provider=run.provider,
        model=run.model,
        seconds=round(run.seconds, 1),
        tool_calls=[
            ToolCallOut(name=call.name, arguments=call.arguments) for call in run.tool_calls
        ],
        nudged=run.nudged,
        unverified=run.unverified,
        error=run.error,
    )
