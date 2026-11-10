"""Add a measured status column to the A and A2 tables in the inventory.

Reads the probe results rather than taking dictated statuses, so the inventory
and tests/fixtures/ cannot drift apart. Re-running it is safe: a table that
already carries the column is rewritten, not doubled.

    uv run python scripts/mark_inventory.py
"""

from __future__ import annotations

import json
import re
import sys

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

INVENTORY = PROJECT_ROOT / "datasets" / "data-inventory.md"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"

# Datasets deferred by decision 0001 rather than by a failed probe.
DEFERRED = {
    501: "⏸️ ertelendi — uç nokta kalkmış (nginx 404), yol çalışmaları aracı kapsamdan çıktı (K3)",
    169: "⏸️ ertelendi — HTTP 200 ama dataList boş; servis portal notuna göre kapalı (K4)",
    170: "⏸️ ertelendi — 400, zorunlu guid yalnızca boş olan 169'dan alınabilir (K4)",
}

# Rows whose problem is a classification error, not a broken service.
RECLASSIFIED = {
    384: "ℹ️ JSON uç noktası değil — developer portal sayfası; geocoding kararı Faz 1'e kaldı",
}


def status_for(dataset_no: int, row: dict | None) -> str:
    """One-line verdict for an inventory row."""
    if dataset_no in DEFERRED:
        return DEFERRED[dataset_no]
    if dataset_no in RECLASSIFIED:
        return RECLASSIFIED[dataset_no]
    if row is None:
        return "—"

    if row.get("works"):
        extra = []
        if row.get("method") == "POST":
            body = json.dumps(row.get("body"), ensure_ascii=False)
            extra.append(f"POST {body}")
        if row.get("records") is not None:
            extra.append(f"{row['records']} kayıt")
        if row.get("newest_timestamp"):
            extra.append(f"en yeni {row['newest_timestamp']}")
        detail = "; ".join(extra)
        return f"✅ çalışıyor ({detail})" if detail else "✅ çalışıyor"

    summary = " ".join(str(row.get("summary", "")).split())
    if "Success=false" in summary:
        message = summary.split("Success=false:", 1)[-1].strip()
        return f"❌ sunucu hatası — HTTP 200 + Success=false: {message[:90]}"
    return f"❌ çalışmıyor — HTTP {row.get('status')}: {summary[:90]}"


def soap_status(dataset_no: int, service: dict, calls: list[dict]) -> str:
    """Verdict for a SOAP service: WSDL readability plus a verified call."""
    parts = [f"✅ WSDL okundu, {service['method_count']} metot"]
    if service.get("parsed_by") == "xml":
        parts.append("zeep reddetti, WSDL XML ile okundu")
    if service.get("auth_headers"):
        parts.append("AuthHeader bildirilmiş ama zorunlu değil")
    verified = [c for c in calls if c.get("works")]
    if verified:
        names = ", ".join(f"{c['method']}" for c in verified[:3])
        parts.append(f"doğrulanan çağrı: {names}")
    return " — ".join(parts)


def rewrite_table(text: str, heading: str, statuses: dict[int, str]) -> str:
    """Append (or refresh) a Durum column in the table under `heading`."""
    start = text.index(heading)
    end = text.index("\n## ", start + len(heading))
    block = text[start:end]
    out_lines: list[str] = []

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            out_lines.append(line)
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        # Drop a Durum column from a previous run before adding the new one.
        if (cells and cells[-1].startswith(("✅", "❌", "⏸️", "ℹ️"))) or cells[-1] == "Durum":
            cells = cells[:-1]
        elif set(cells[-1]) <= {"-", ":"} and len(cells) > 5:
            cells = cells[:-1]

        if cells[0] == "No":
            cells.append("Durum")
        elif set(cells[0]) <= {"-", ":"}:
            cells.append("---")
        else:
            match = re.match(r"^(\d+)$", cells[0])
            cells.append(statuses.get(int(match.group(1)), "—") if match else "—")
        out_lines.append("| " + " | ".join(cells) + " |")

    return text[:start] + "\n".join(out_lines) + text[end:]


def main() -> int:
    rest = json.loads((FIXTURES / "_probe_results.json").read_text(encoding="utf-8"))
    wsdl = json.loads((FIXTURES / "iett_wsdl" / "_summary.json").read_text(encoding="utf-8"))
    soap_calls = json.loads((FIXTURES / "iett" / "_call_results.json").read_text(encoding="utf-8"))

    by_no = {row["no"]: row for row in rest["results"]}
    statuses: dict[int, str] = {}

    for dataset_no in {*by_no, *DEFERRED, *RECLASSIFIED}:
        statuses[dataset_no] = status_for(dataset_no, by_no.get(dataset_no))

    for service in wsdl["services"]:
        calls = [c for c in soap_calls["results"] if c["service"] == service["slug"]]
        statuses[service["no"]] = soap_status(service["no"], service, calls)

    text = INVENTORY.read_text(encoding="utf-8")
    text = rewrite_table(text, "## A — Canlı API → MCP aracı (öncelikli)", statuses)
    text = rewrite_table(text, "## A2 — Canlı API → ikincil / destekleyici", statuses)

    # Replace the "not tested yet" warning with what was actually measured.
    old_warning = (
        "> Uç noktalar henüz canlı test edilmedi. Faz 0'da her A işaretli uç noktaya "
        "gerçek istek atıp yanıtı `tests/fixtures/` altına kaydet."
    )
    new_warning = (
        "> **Uç nokta testi yapıldı: 6 Kasım 2026, 17:20–17:23 (TRT).** A ve A2 grubundaki\n"
        "> 39 veri setinin tamamına gerçek istek atıldı: REST 25/32 çalışıyor, İETT SOAP\n"
        "> 7/7 WSDL okundu (28 metot), 9/9 doğrulanan çağrı. Hiçbiri kimlik doğrulama\n"
        "> istemedi. Örnek yanıtlar `tests/fixtures/` altında, bulgular ve kararlar\n"
        "> `documents/decisions/0001-veri-kaynaklari.md` içinde. Aşağıdaki Durum kolonu\n"
        "> ölçüm sonuçlarından üretilir (`scripts/mark_inventory.py`)."
    )
    if old_warning in text:
        text = text.replace(old_warning, new_warning)

    INVENTORY.write_text(text, encoding="utf-8")
    marked = sum(1 for v in statuses.values() if v != "—")
    print(f"{marked} satır işaretlendi -> {INVENTORY.relative_to(PROJECT_ROOT)}")
    for dataset_no, status in sorted(statuses.items()):
        print(f"  {dataset_no:>4}  {status[:110]}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
