"""Gradio demo arayüzü.

    uv run python -m istanbul_agent.app

Üç şey gösteriyor ve üçü de bilerek aynı ekranda:

1. **Cevap** — agent'ın metni.
2. **Çağrılan araçlar** — hangi araç, hangi parametreyle. Agent'ın doğru
   aracı seçip seçmediği cevaba bakarak anlaşılmıyor (karar 0006); bu
   tablo demonun asıl gösterdiği şey.
3. **Harita** — araç çıktılarındaki koordinatlar. Durak/istasyon
   sorularında cevabın gerçekten İstanbul'da bir yeri gösterdiği buradan
   görünüyor.

Kaynaksız cevap uyarısı ekranın en üstünde: araç çağrılmadan verilen bir
cevap doğru görünse bile kaynaksızdır (karar 0010).
"""

from __future__ import annotations

import json
import re
from typing import Any

from istanbul_agent.agent.graph import AgentRun, answer

__all__ = ["build_demo", "main"]

EXAMPLES = [
    "Kadıköy'den Taksim'e metroyla kaç dakika?",
    "Kadıköy iskelesine en yakın otobüs durakları hangileri?",
    "Metroya bisikletle binebilir miyim?",
    "500T hattı nerelerden geçiyor?",
    "Tam bilet şu anda kaç lira?",
    "Taksim'de şu an boş otopark var mı?",
]

# Araç çıktısı JSON metni olarak geliyor; koordinatlar bu alanlarda.
COORD_PAIR = re.compile(r'"lat"\s*:\s*(-?\d+\.\d+)\s*,\s*"lon"\s*:\s*(-?\d+\.\d+)', re.MULTILINE)
NAME_BEFORE = re.compile(r'"name"\s*:\s*"([^"]{1,60})"')

# İstanbul kaba sınır kutusu: araç çıktısındaki başka bir sayı çiftinin
# haritaya düşmesini engeller.
LAT_RANGE = (40.75, 41.70)
LON_RANGE = (27.80, 30.00)


def extract_points(run: AgentRun) -> list[dict[str, Any]]:
    """Araç çıktılarından koordinatları çıkar.

    Araç sonuçları MCP üzerinden JSON **metni** olarak dönüyor, bu yüzden
    yapı yeniden ayrıştırılmıyor; yalnızca `lat`/`lon` çiftleri ve varsa
    bir önceki `name` alanı okunuyor. Ayrıştırılamayan çıktı haritayı
    boş bırakır, hata vermez.
    """
    points: list[dict[str, Any]] = []
    for call in run.tool_calls:
        text = call.result or ""
        for match in COORD_PAIR.finditer(text):
            lat, lon = float(match.group(1)), float(match.group(2))
            if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
                continue
            names = NAME_BEFORE.findall(text[: match.start()])
            points.append(
                {
                    "ad": names[-1] if names else call.name,
                    "araç": call.name,
                    "enlem": lat,
                    "boylam": lon,
                }
            )
    return points


def build_map(points: list[dict[str, Any]]) -> Any:
    """Noktaları OpenStreetMap üzerinde göster; nokta yoksa `None`."""
    if not points:
        return None
    try:
        import plotly.graph_objects as go
    except ImportError:  # pragma: no cover — app ekstrası kurulu değil
        return None

    figure = go.Figure(
        go.Scattermap(
            lat=[point["enlem"] for point in points],
            lon=[point["boylam"] for point in points],
            mode="markers",
            marker={"size": 11},
            text=[f"{point['ad']} ({point['araç']})" for point in points],
            hoverinfo="text",
        )
    )
    figure.update_layout(
        map={
            "style": "open-street-map",
            "center": {
                "lat": sum(point["enlem"] for point in points) / len(points),
                "lon": sum(point["boylam"] for point in points) / len(points),
            },
            "zoom": 12 if len(points) > 1 else 14,
        },
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=360,
    )
    return figure


def format_calls(run: AgentRun) -> list[list[str]]:
    return [
        [str(index), call.name, json.dumps(call.arguments, ensure_ascii=False)]
        for index, call in enumerate(run.tool_calls, start=1)
    ]


def status_line(run: AgentRun) -> str:
    """Ekranın üstündeki durum satırı."""
    if run.error:
        return f"### ⚠ Hata\n`{run.error}`"
    parts = [
        f"**{run.provider}** ({run.model}) · {run.seconds:.1f} s · {len(run.tool_calls)} araç çağrısı"
    ]
    if run.nudged:
        parts.append("Model araç çağırmadığı için bir kez uyarıldı.")
    if run.unverified:
        return (
            "### ⚠ Bu cevap kaynaksız\n"
            "Hiçbir araç çağrılmadı, yani cevap veri kaynaklarına dayanmıyor. "
            "Doğrulanmamış bilgi olarak değerlendirin.\n\n" + " · ".join(parts)
        )
    return " · ".join(parts)


async def run_question(question: str, provider: str) -> tuple[str, str, list[list[str]], Any]:
    if not (question or "").strip():
        return ("Soru yazın.", "", [], None)
    run = await answer(question, provider or None)
    points = extract_points(run)
    return (
        run.answer or "(boş cevap)",
        status_line(run),
        format_calls(run),
        build_map(points),
    )


def build_demo() -> Any:
    import gradio as gr

    with gr.Blocks(title="İstanbul Ulaşım Agent'ı") as demo:
        gr.Markdown(
            "# İstanbul Ulaşım Agent'ı\n"
            "İBB Açık Veri Portalı verileri MCP araçları olarak sunuluyor; "
            "cevaplar yalnızca bu araçlardan gelir. Araç çağrılmadan verilen "
            "cevap **kaynaksız** olarak işaretlenir."
        )
        with gr.Row():
            question = gr.Textbox(
                label="Soru",
                placeholder="Kadıköy'den Taksim'e metroyla kaç dakika?",
                scale=4,
            )
            provider = gr.Dropdown(
                label="LLM",
                choices=["", "gemini", "ollama"],
                value="",
                info="boş: .env içindeki LLM_PROVIDER",
                scale=1,
            )
        ask = gr.Button("Sor", variant="primary")
        status = gr.Markdown()
        answer_box = gr.Markdown(label="Cevap")
        with gr.Row():
            calls = gr.Dataframe(
                headers=["#", "araç", "parametreler"],
                label="Çağrılan araçlar",
                wrap=True,
            )
        map_plot = gr.Plot(label="Araç çıktılarındaki konumlar")
        gr.Examples(examples=EXAMPLES, inputs=question)

        ask.click(
            fn=run_question,
            inputs=[question, provider],
            outputs=[answer_box, status, calls, map_plot],
        )
        question.submit(
            fn=run_question,
            inputs=[question, provider],
            outputs=[answer_box, status, calls, map_plot],
        )
    return demo


def main() -> None:
    # Qdrant yerel modu dizini tek sürece kilitliyor; arayüz
    # `knowledge_server`'ı alt süreç olarak başlattığı için ingest aynı anda
    # çalışmamalı (karar 0009).
    build_demo().launch()


if __name__ == "__main__":
    main()
