"""Actually call the İETT SOAP methods and save what they return.

Knowing a method exists in the WSDL is not the same as it working, and
`get_vehicle_positions` in the tool table depends on GetFiloAracKonum_json
really answering with coordinates. Four of these WSDLs are malformed enough
that zeep refuses them, so the requests go out as hand-built SOAP 1.1
envelopes -- which also tells us whether the AuthHeader in the WSDL is
actually enforced, since we send none.

Writes:
    tests/fixtures/iett/<service>.<method>.json   parsed sample response
    tests/fixtures/iett/_call_results.json        the result table

    uv run python scripts/probe_soap_calls.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any
from xml.etree import ElementTree

import requests

from istanbul_agent.config import PROJECT_ROOT, get_settings

OUT = PROJECT_ROOT / "tests" / "fixtures" / "iett"
TEMPURI = "http://tempuri.org/"
FIXTURE_MAX_RECORDS = 5

# Real codes to exercise the parameterised methods.
HAT_KODU = "34"  # Metrobüs 34: busiest line, so a live position call should return rows
DURAK_KODU = "108571"  # harvested below from GetHat_json if possible

# (service slug, endpoint path, method, {param: value})
CALLS: list[tuple[str, str, str, dict[str, str]]] = [
    # --- Announcements: the live service alerts source ---
    ("duyurular", "/iett/UlasimDinamikVeri/Duyurular.asmx", "GetDuyurular_json", {}),
    # --- Fleet status: the vehicle position candidates ---
    ("sefer-gerceklesme", "/iett/FiloDurum/SeferGerceklesme.asmx", "GetFiloAracKonum_json", {}),
    (
        "sefer-gerceklesme",
        "/iett/FiloDurum/SeferGerceklesme.asmx",
        "GetHatOtoKonum_json",
        {"HatKodu": HAT_KODU},
    ),
    ("sefer-gerceklesme", "/iett/FiloDurum/SeferGerceklesme.asmx", "GetBozukSatih_json", {}),
    # --- Line / stop / route reference data ---
    (
        "hat-durak-guzergah",
        "/iett/UlasimAnaVeri/HatDurakGuzergah.asmx",
        "GetHat_json",
        {"HatKodu": HAT_KODU},
    ),
    ("hat-durak-guzergah", "/iett/UlasimAnaVeri/HatDurakGuzergah.asmx", "GetGaraj_json", {}),
    # --- Planned departure times ---
    (
        "planlanan-sefer-saati",
        "/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx",
        "GetPlanlananSeferSaati_json",
        {"HatKodu": HAT_KODU},
    ),
    # --- Stop detail / line service ---
    ("ibb", "/iett/ibb/ibb.asmx", "DurakDetay_GYY", {"hat_kodu": HAT_KODU}),
    ("ibb", "/iett/ibb/ibb.asmx", "HatServisi_GYY", {"hat_kodu": HAT_KODU}),
]

ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <{method} xmlns="{tempuri}">
{params}
    </{method}>
  </soap:Body>
</soap:Envelope>
"""

# Distinctive tokens only. Bare "x"/"y" would match SYON, TIP and friends, so
# the X/Y columns are matched through "koordinat" instead.
COORD_KEYS = ("boylam", "enlem", "latitude", "longitude", "koordinat", "_lat", "_lon")


def build_envelope(method: str, params: dict[str, str]) -> str:
    body = "\n".join(f"      <{key}>{value}</{key}>" for key, value in params.items())
    return ENVELOPE.format(method=method, tempuri=TEMPURI, params=body)


def extract_result(xml_text: str) -> tuple[Any, str]:
    """Pull the payload out of a SOAP response. Returns (payload, kind)."""
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8"))
    except ElementTree.ParseError as exc:
        return None, f"XML ayrıştırılamadı: {exc}"

    fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
    if fault is not None:
        message = "".join(fault.itertext()).strip()
        return None, f"SOAP Fault: {' '.join(message.split())[:220]}"

    # *_json methods wrap a JSON string in a <...Result> element.
    for element in root.iter():
        if element.tag.endswith("Result") and element.text:
            text = element.text.strip()
            if text.startswith(("[", "{")):
                try:
                    return json.loads(text), "json"
                except json.JSONDecodeError as exc:
                    return text[:600], f"Result JSON değil: {exc}"
            return text[:600], "metin"

    # *_XML methods inline a DataSet instead.
    rows = [
        {child.tag.split("}")[-1]: child.text for child in row}
        for row in root.iter()
        if len(row) and all(len(c) == 0 for c in row) and len(list(row)) > 1
    ]
    if rows:
        return rows, "inline-xml"
    return None, "sonuç elemanı bulunamadı"


def has_coordinates(payload: Any) -> bool:
    """Does the payload carry anything that looks like a position?"""
    sample: Any = payload
    if isinstance(payload, list) and payload:
        sample = payload[0]
    if not isinstance(sample, dict):
        return False
    lowered = {key.lower() for key in sample}
    return any(any(needle in key for needle in COORD_KEYS) for key in lowered)


def newest_timestamp(payload: Any) -> str | None:
    pattern = re.compile(r"(20\d{2})[-/.](\d{2})[-/.](\d{2})(?:[T ](\d{2}):(\d{2}))?")
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            match = pattern.search(node)
            if match:
                found.append(
                    f"{match.group(1)}-{match.group(2)}-{match.group(3)} "
                    f"{match.group(4) or '00'}:{match.group(5) or '00'}"
                )

    walk(payload)
    return max(found) if found else None


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {
            "Content-Type": "text/xml; charset=utf-8",
            "User-Agent": "istanbul-agent/0.1 (Faz 0 discovery)",
        }
    )

    rows: list[dict[str, Any]] = []
    for slug, path, method, params in CALLS:
        url = f"{base}{path}"
        envelope = build_envelope(method, params)
        print(f"\n=== {slug}.{method}({', '.join(f'{k}={v}' for k, v in params.items())}) ===")

        started = time.perf_counter()
        try:
            response = session.post(
                url,
                data=envelope.encode("utf-8"),
                headers={"SOAPAction": f'"{TEMPURI}{method}"'},
                timeout=settings.ibb_request_timeout,
            )
        except Exception as exc:
            print(f"    BAĞLANTI HATASI: {type(exc).__name__}: {exc}")
            rows.append(
                {
                    "service": slug,
                    "method": method,
                    "params": params,
                    "works": False,
                    "status": "BAĞLANTI HATASI",
                    "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
            )
            continue

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        print(f"    HTTP {response.status_code}  {elapsed_ms} ms  {len(response.content)} bayt")

        payload, kind = extract_result(response.text)
        row: dict[str, Any] = {
            "service": slug,
            "method": method,
            "params": params,
            "url": url,
            "http_status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "bytes": len(response.content),
            "result_kind": kind,
            "auth_required": response.status_code in (401, 403),
        }

        if payload is None:
            row.update(works=False, detail=kind)
            print(f"    BAŞARISIZ: {kind}")
            rows.append(row)
            continue

        count = len(payload) if isinstance(payload, list) else 1
        fields = (
            list(payload[0])
            if isinstance(payload, list) and payload and isinstance(payload[0], dict)
            else (list(payload) if isinstance(payload, dict) else [])
        )
        stamp = newest_timestamp(payload)
        coords = has_coordinates(payload)

        row.update(
            works=True,
            records=count,
            fields=fields,
            newest_timestamp=stamp,
            has_coordinates=coords,
        )
        print(f"    OK  {count} kayıt  format={kind}  en yeni damga={stamp or '-'}")
        print(f"        alanlar: {', '.join(fields[:12])}")
        if coords:
            print("        *** KOORDİNAT İÇERİYOR ***")
        if isinstance(payload, list) and payload:
            print(f"        örnek: {json.dumps(payload[0], ensure_ascii=False)[:240]}")

        trimmed = isinstance(payload, list) and len(payload) > FIXTURE_MAX_RECORDS
        sample = payload[:FIXTURE_MAX_RECORDS] if trimmed else payload
        target = OUT / f"{slug}.{method}.json"
        target.write_text(
            json.dumps(
                {
                    "_fixture_meta": {
                        "source_url": url,
                        "soap_action": f"{TEMPURI}{method}",
                        "request_params": params,
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "total_records": count,
                        "trimmed_to": FIXTURE_MAX_RECORDS if trimmed else None,
                        "auth_header_sent": False,
                    },
                    "response": sample,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        row["fixture"] = str(target.relative_to(PROJECT_ROOT)).replace("\\", "/")
        rows.append(row)
        time.sleep(0.6)

    (OUT / "_call_results.json").write_text(
        json.dumps(
            {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    working = sum(1 for row in rows if row.get("works"))
    print("\n" + "=" * 70)
    print(f"{working}/{len(rows)} SOAP metodu veri döndürdü (AuthHeader GÖNDERİLMEDİ)")
    positional = [f"{r['service']}.{r['method']}" for r in rows if r.get("has_coordinates")]
    print("\nKOORDİNAT DÖNEN METOTLAR:")
    print("\n".join(f"  {p}" for p in positional) if positional else "  YOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
