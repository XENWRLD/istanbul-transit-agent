"""İETT SOAP istemcisi: `zeep` mi, elle kurulan zarf mı?

PLAN.md'nin açık bıraktığı karar. Faz 0'da ölçülen: `zeep` dört WSDL'i
açamıyor (350, 349, 172, 171) çünkü `AuthHeader` bildirilmiş ama eşleşen
`<message>` tanımı yok; dokuz çağrının dokuzu elle kurulan SOAP 1.1
zarfıyla çalışıyor.

Burada ölçülen: **zeep'in açabildiği** bir servis (`duyurular`, 351) için
iki yöntem yan yana — gecikme, dönen kayıt sayısı ve kodun ne kadarının
tekrarlandığı. Bir de zeep'in açamadığı servis (`planlanan-sefer-saati`,
350) elle zarfla çağrılıyor, çünkü karar ancak iki servisin birlikte
çalışmasıyla verilebilir.

    uv run python scripts/probe_soap_client.py
    uv run python scripts/probe_soap_client.py --line 34

Çıktı `data/soap_client_probe.json`; tek seferlik ölçüm betiği.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.console import enable_unicode_output

OUT_PATH = PROJECT_ROOT / "data" / "soap_client_probe.json"

TEMPURI = "http://tempuri.org/"
SERVICES = {
    "duyurular": "/iett/UlasimDinamikVeri/Duyurular.asmx",
    "planlanan-sefer-saati": "/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx",
}


def endpoint(slug: str) -> str:
    return f"{get_settings().ibb_api_base_url.rstrip('/')}{SERVICES[slug]}"


def try_zeep(slug: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    """`zeep` ile çağır; WSDL açılamazsa hatayı raporla."""
    started = time.perf_counter()
    try:
        from zeep import Client

        client = Client(f"{endpoint(slug)}?wsdl")
        raw = getattr(client.service, method)(**params)
    except Exception as problem:
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - started, 3),
            "error": f"{type(problem).__name__}: {str(problem)[:160]}",
        }
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    return {
        "ok": True,
        "seconds": round(time.perf_counter() - started, 3),
        "chars": len(text),
        "records": _count(text),
    }


def try_envelope(slug: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    """Elle kurulan SOAP 1.1 zarfıyla çağır."""
    import httpx

    body = "".join(f"<{key}>{value}</{key}>" for key, value in params.items())
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
        ' xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        f'<{method} xmlns="{TEMPURI}">{body}</{method}>'
        "</soap:Body></soap:Envelope>"
    )
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=get_settings().ibb_request_timeout) as client:
            response = client.post(
                endpoint(slug),
                content=envelope.encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f'"{TEMPURI}{method}"',
                },
            )
            response.raise_for_status()
            text = response.text
    except Exception as problem:
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - started, 3),
            "error": f"{type(problem).__name__}: {str(problem)[:160]}",
        }
    return {
        "ok": True,
        "seconds": round(time.perf_counter() - started, 3),
        "chars": len(text),
        "records": _count(text),
    }


def _count(text: str) -> int | None:
    """Gövdedeki JSON listesinin uzunluğu (ölçülebilir bir karşılaştırma için)."""
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return len(payload) if isinstance(payload, list) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--line", default="34", help="GetPlanlananSeferSaati_json için hat kodu")
    args = parser.parse_args()

    cases = [
        ("duyurular", "GetDuyurular_json", {}),
        ("planlanan-sefer-saati", "GetPlanlananSeferSaati_json", {"HatKodu": args.line}),
    ]

    report: dict[str, Any] = {"cases": []}
    print("=== İETT SOAP: zeep vs elle zarf ===")
    for slug, method, params in cases:
        zeep_result = try_zeep(slug, method, params)
        envelope_result = try_envelope(slug, method, params)
        report["cases"].append(
            {
                "service": slug,
                "method": method,
                "params": params,
                "zeep": zeep_result,
                "envelope": envelope_result,
            }
        )
        print(f"\n  {slug} / {method}({params or ''})")
        for label, result in (("zeep", zeep_result), ("elle zarf", envelope_result)):
            if result["ok"]:
                print(
                    f"    {label:10} ✓ {result['seconds']:>6.2f} s  "
                    f"{result['chars']:>8} karakter  {result['records']} kayıt"
                )
            else:
                print(f"    {label:10} ✗ {result['seconds']:>6.2f} s  {result['error']}")

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  rapor: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
