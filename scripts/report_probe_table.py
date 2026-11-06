"""Render the probe results as a Markdown table.

Reads what the probe scripts wrote so the report and the decision note quote
measured numbers instead of retyped ones.

    uv run python scripts/report_probe_table.py
"""

from __future__ import annotations

import json
import sys

from istanbul_agent.config import PROJECT_ROOT

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def main() -> int:
    rest = load("_probe_results.json")
    soap_wsdl = load("iett_wsdl/_summary.json")
    soap_calls = load("iett/_call_results.json")

    print(f"REST probed_at: {rest['probed_at']}")
    print(f"SOAP probed_at: {soap_wsdl['probed_at']}\n")

    print(
        "| No | Grup | Uç nokta | Çalışıyor | HTTP | Auth | Format | ms | En yeni damga | Kayıt | Özet |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in sorted(rest["results"], key=lambda r: (r["group"], -int(r["works"]), r["no"])):
        works = "✅" if row["works"] else "❌"
        method = "" if row.get("method", "GET") == "GET" else " (POST)"
        stamp = row.get("newest_timestamp") or "—"
        records = row.get("records")
        summary = " ".join(str(row["summary"]).split())[:95]
        print(
            f"| {row['no']} | {row['group']} | `{row['slug']}`{method} | {works} | "
            f"{row['status']} | {row.get('auth', '—')} | {row.get('format', '—')} | "
            f"{row.get('elapsed_ms') or '—'} | {stamp} | {records if records is not None else '—'} | {summary} |"
        )

    print("\n\n## İETT SOAP — WSDL\n")
    print("| No | Grup | Servis | WSDL | Ayrıştırma | ms | Metot | AuthHeader |")
    print("|---|---|---|---|---|---|---|---|")
    for service in soap_wsdl["services"]:
        ok = "✅" if service["wsdl_ok"] else "❌"
        parsed = {"zeep": "zeep", "xml": "WSDL XML (zeep reddetti)"}.get(
            service.get("parsed_by") or "", "—"
        )
        headers = ", ".join(service.get("auth_headers") or []) or "—"
        print(
            f"| {service['no']} | {service['group']} | `{service['slug']}` | {ok} | "
            f"{parsed} | {service['elapsed_ms']} | {service['method_count']} | {headers} |"
        )

    print("\n\n## İETT SOAP — gerçek çağrılar\n")
    print("| Servis | Metot | Çalışıyor | ms | Kayıt | En yeni damga | Koordinat | Alanlar |")
    print("|---|---|---|---|---|---|---|---|")
    for row in soap_calls["results"]:
        works = "✅" if row.get("works") else "❌"
        coords = "✅" if row.get("has_coordinates") else "—"
        fields = ", ".join(row.get("fields", [])[:8])
        print(
            f"| `{row['service']}` | `{row['method']}` | {works} | {row.get('elapsed_ms', '—')} | "
            f"{row.get('records', '—')} | {row.get('newest_timestamp') or '—'} | {coords} | {fields} |"
        )

    rest_ok = sum(1 for r in rest["results"] if r["works"])
    soap_ok = sum(1 for r in soap_calls["results"] if r.get("works"))
    print(
        f"\n\nREST {rest_ok}/{len(rest['results'])} · "
        f"WSDL {sum(1 for s in soap_wsdl['services'] if s['wsdl_ok'])}/{len(soap_wsdl['services'])} · "
        f"SOAP çağrı {soap_ok}/{len(soap_calls['results'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
