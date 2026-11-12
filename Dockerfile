# İstanbul Ulaşım Agent'ı — API ve demo arayüzü için imaj.
#
# İmaj **veriyi içermez**: yerel depo (DuckDB), metin indeksi (Qdrant) ve
# gömme modeli toplamda birkaç GB ve hepsi indirme/ingest adımlarının
# ürünü. `data/` ve model önbelleği birim olarak bağlanır; böylece imaj
# küçük kalır ve veri yenilendiğinde imajı yeniden kurmak gerekmez.
#
#   docker build -t istanbul-agent .
#
#   # Veriyi bir kez kur (konteyner içinde, kalıcı birime yazar):
#   docker run --rm -v "$PWD/data:/app/data" istanbul-agent \
#     sh -c "python scripts/download_gtfs.py --feed iett \
#            && python scripts/download_stop_geojson.py \
#            && python scripts/download_rail_reference.py \
#            && python scripts/download_rail_geojson.py \
#            && python scripts/download_knowledge.py \
#            && python -m istanbul_agent.store.ingest_rail \
#            && python -m istanbul_agent.store.ingest_timetable \
#            && python -m istanbul_agent.store.ingest \
#            && python -m istanbul_agent.rag.ingest"
#
#   # API:
#   docker run --rm -p 8000:8000 --env-file .env \
#     -v "$PWD/data:/app/data" -v huggingface:/root/.cache/huggingface \
#     istanbul-agent
#
#   # Demo arayüzü:
#   docker run --rm -p 7860:7860 --env-file .env \
#     -v "$PWD/data:/app/data" -v huggingface:/root/.cache/huggingface \
#     istanbul-agent python -m istanbul_agent.app
#
# Yerel LLM (Ollama) konteynerin dışında çalışır; `OLLAMA_BASE_URL` konteyner
# içinden erişilebilir bir adres olmalı (Docker Desktop'ta
# `http://host.docker.internal:11434`). `--env-file .env` ile gelen
# `OLLAMA_BASE_URL` konteyner içinde **konteynerin kendisini** gösterir, bu
# yüzden `-e` ile ezilmeli; `OLLAMA_HOST` ayarlıysa port da varsayılan
# olmayabilir (ölçüm makinesinde 11435). Karar 0016.
#
# **Windows:** yukarıdaki `$PWD/data` biçimi Git Bash'te yol dönüşümüne
# uğruyor ve sessizce boş bir dizin bağlıyor — konteyner kalkıyor, `/health`
# "veri yok" diyor ve sebep görünmüyor. PowerShell'den tam yol kullan:
#
#   docker run -d -p 8000:8000 --env-file .env `
#     -e LLM_PROVIDER=ollama -e OLLAMA_BASE_URL=http://host.docker.internal:11435 `
#     -v "C:\Users\<kullanici>\...\IBB PROJECT\data:/app/data" `
#     -v huggingface:/root/.cache/huggingface istanbul-agent

FROM python:3.12-slim AS base

# uv: bağımlılıklar kilit dosyasından kurulur, böylece imaj ile geliştirme
# ortamı aynı sürümleri alır.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Gradio ve uvicorn konteyner dışından erişilebilir olsun.
    GRADIO_SERVER_NAME=0.0.0.0 \
    HF_HUB_DISABLE_PROGRESS_BARS=1

WORKDIR /app

# Önce yalnızca kilit ve proje tanımı: kaynak değiştiğinde bağımlılık
# katmanı yeniden kurulmasın.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project \
      --extra store --extra rag --extra agent --extra app

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY datasets/ ./datasets/
COPY eval/ ./eval/
RUN uv sync --locked --extra store --extra rag --extra agent --extra app

# `data/` birim olarak bağlanır; boş dizin, bağlanmazsa servis de çalışır
# ve /health "veri yok" der (api.py).
RUN mkdir -p /app/data

EXPOSE 8000 7860

# Kurulu veri olmadan da ayağa kalkar; /health eksikleri söyler.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "istanbul_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
