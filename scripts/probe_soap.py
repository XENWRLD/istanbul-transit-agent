"""Faz 0 discovery: enumerate the İETT SOAP services and their methods.

The tool table has a `get_vehicle_positions` entry marked "existence to be
verified first", and this is the script that verifies it: it loads each WSDL,
writes out every operation with its full signature, and flags the operations
whose name or parameters suggest live vehicle coordinates.

Writes:
    tests/fixtures/iett_wsdl/<service>.methods.txt   one method list per service
    tests/fixtures/iett_wsdl/_summary.json           machine-readable summary

    uv run python scripts/probe_soap.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any
from xml.etree import ElementTree

import requests
from zeep import Client
from zeep import Settings as ZeepSettings
from zeep.exceptions import Error as ZeepError
from zeep.transports import Transport

from istanbul_agent.config import PROJECT_ROOT, get_settings

OUT = PROJECT_ROOT / "tests" / "fixtures" / "iett_wsdl"

# dataset no, group, slug, WSDL path
SERVICES: list[tuple[int, str, str, str]] = [
    (351, "A", "duyurular", "/iett/UlasimDinamikVeri/Duyurular.asmx?wsdl"),
    (350, "A", "planlanan-sefer-saati", "/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx?wsdl"),
    (349, "A", "sefer-gerceklesme", "/iett/FiloDurum/SeferGerceklesme.asmx?wsdl"),
    (172, "A", "hat-durak-guzergah", "/iett/UlasimAnaVeri/HatDurakGuzergah.asmx?wsdl"),
    (171, "A", "ibb", "/iett/ibb/ibb.asmx?wsdl"),
    (347, "A", "ibb360", "/iett/ibb/ibb360.asmx?wsdl"),
    (352, "A2", "arac-ozellik", "/iett/AracAnaVeri/AracOzellik.asmx?wsdl"),
]

# A method is a vehicle-position candidate if its name or signature mentions
# both "a vehicle" and "a position", or names a known live-location concept.
VEHICLE_WORDS = ("arac", "araç", "filo", "otobus", "otobüs", "vehicle", "bus")
POSITION_WORDS = (
    "konum",
    "koordinat",
    "lokasyon",
    "position",
    "location",
    "gps",
    "enlem",
    "boylam",
    "lat",
    "lon",
    "nokta",
)
STRONG_HINTS = ("aracknm", "aracKonum", "GetVehicle", "AnlikKonum", "CanliKonum")


def classify(name: str, signature: str) -> str:
    """Return a marker for methods that may expose live vehicle positions."""
    haystack = f"{name} {signature}".lower()
    if any(hint.lower() in name.lower() for hint in STRONG_HINTS):
        return "GÜÇLÜ ADAY"
    has_vehicle = any(word in haystack for word in VEHICLE_WORDS)
    has_position = any(word in haystack for word in POSITION_WORDS)
    if has_vehicle and has_position:
        return "ADAY"
    if has_position:
        return "konum geçiyor"
    return ""


def operations_from_xml(wsdl_xml: bytes) -> tuple[list[tuple[str, str]], list[str]]:
    """Extract operations straight from the WSDL document.

    Needed because several of these ASMX services declare a SOAP AuthHeader
    on every operation but never define the matching <message>, which makes
    zeep refuse the whole document. Reading the XML gets us the method list
    anyway, and tells us which auth headers the service expects.
    """
    root = ElementTree.fromstring(wsdl_xml)
    wsdl_ns = "{http://schemas.xmlsoap.org/wsdl/}"
    xsd_ns = "{http://www.w3.org/2001/XMLSchema}"

    # Element name -> its child field names, from the inline schema.
    element_fields: dict[str, list[str]] = {}
    for element in root.iter(f"{xsd_ns}element"):
        name = element.get("name")
        if not name:
            continue
        fields = [
            child.get("name", "?")
            for child in element.iter(f"{xsd_ns}element")
            if child is not element and child.get("name")
        ]
        if name not in element_fields:
            element_fields[name] = fields

    methods: dict[str, str] = {}
    for port_type in root.iter(f"{wsdl_ns}portType"):
        for operation in port_type.iter(f"{wsdl_ns}operation"):
            name = operation.get("name")
            if name:
                methods.setdefault(name, ", ".join(element_fields.get(name, [])))

    auth_headers = sorted(
        {
            header.get("part", "") or header.get("message", "").split(":")[-1]
            for header in root.iter("{http://schemas.xmlsoap.org/wsdl/soap/}header")
            if header.get("part") or header.get("message")
        }
    )
    return sorted(methods.items()), [h for h in auth_headers if h]


def operations(client: Client) -> list[tuple[str, str]]:
    """Every operation in the WSDL as (name, signature)."""
    found: list[tuple[str, str]] = []
    for service in client.wsdl.services.values():
        for port in service.ports.values():
            binding = port.binding
            for operation in binding._operations.values():
                try:
                    signature = str(operation.input.signature())
                except Exception:
                    signature = "(imza okunamadı)"
                found.append((operation.name, signature))
    # One method can appear under several bindings (SOAP 1.1 / 1.2 / HttpPost).
    unique: dict[str, str] = {}
    for name, signature in found:
        unique.setdefault(name, signature)
    return sorted(unique.items())


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)

    # zeep speaks through requests, not httpx.
    session = requests.Session()
    session.headers["User-Agent"] = "istanbul-agent/0.1 (Faz 0 discovery)"
    transport = Transport(
        session=session,
        timeout=settings.ibb_request_timeout,
        operation_timeout=settings.ibb_request_timeout,
    )
    zeep_settings = ZeepSettings(strict=False, xml_huge_tree=True)

    summary: list[dict[str, Any]] = []
    all_candidates: list[str] = []

    for dataset_no, group, slug, path in SERVICES:
        url = f"{base}{path}"
        print(f"\n=== [{group}] {dataset_no} {slug} ===")
        print(f"    {url}")

        started = time.perf_counter()
        parsed_by = "zeep"
        zeep_error: str | None = None
        auth_headers: list[str] = []
        methods: list[tuple[str, str]] = []

        try:
            client = Client(url, transport=transport, settings=zeep_settings)
            methods = operations(client)
        except (ZeepError, Exception) as exc:
            zeep_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            print(f"    zeep WSDL'i açamadı: {zeep_error[:140]}")
            # Fall back to reading the WSDL document ourselves.
            try:
                response = session.get(url, timeout=settings.ibb_request_timeout)
                response.raise_for_status()
                methods, auth_headers = operations_from_xml(response.content)
                parsed_by = "xml"
            except Exception as inner:
                elapsed = round((time.perf_counter() - started) * 1000)
                print(f"    WSDL HİÇ OKUNAMADI: {type(inner).__name__}: {str(inner)[:160]}")
                summary.append(
                    {
                        "no": dataset_no,
                        "group": group,
                        "slug": slug,
                        "url": url,
                        "wsdl_ok": False,
                        "parsed_by": None,
                        "elapsed_ms": elapsed,
                        "zeep_error": zeep_error,
                        "fetch_error": f"{type(inner).__name__}: {str(inner)[:300]}",
                        "auth_headers": [],
                        "method_count": 0,
                        "methods": [],
                        "vehicle_position_candidates": [],
                    }
                )
                continue

        elapsed = round((time.perf_counter() - started) * 1000)
        via = "zeep" if parsed_by == "zeep" else "WSDL XML (zeep reddetti)"
        print(f"    WSDL OK ({elapsed} ms) [{via}], {len(methods)} metot")
        if auth_headers:
            print(f"    SOAP auth header: {', '.join(auth_headers)}")

        lines = [
            f"# {slug}  (veri seti {dataset_no}, grup {group})",
            f"# WSDL: {url}",
            f"# Okunma: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
            f"# Ayrıştırma: {via}",
            f"# {len(methods)} metot",
        ]
        if zeep_error:
            lines.append(f"# zeep hatası: {zeep_error}")
        if auth_headers:
            lines.append(f"# SOAP auth header: {', '.join(auth_headers)}")
        lines.append("")
        candidates: list[dict[str, str]] = []
        for name, signature in methods:
            marker = classify(name, signature)
            lines.append(f"{name}({signature})")
            if marker:
                lines[-1] += f"    <-- {marker}"
                candidates.append({"method": name, "signature": signature, "marker": marker})
                print(f"      {marker:12} {name}({signature[:90]})")

        (OUT / f"{slug}.methods.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        summary.append(
            {
                "no": dataset_no,
                "group": group,
                "slug": slug,
                "url": url,
                "wsdl_ok": True,
                "parsed_by": parsed_by,
                "zeep_error": zeep_error,
                "auth_headers": auth_headers,
                "elapsed_ms": elapsed,
                "method_count": len(methods),
                "methods": [{"name": n, "signature": s} for n, s in methods],
                "vehicle_position_candidates": candidates,
            }
        )
        all_candidates += [f"{slug}.{c['method']} [{c['marker']}]" for c in candidates]

    (OUT / "_summary.json").write_text(
        json.dumps(
            {
                "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "api_base_url": base,
                "services": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    ok = sum(1 for s in summary if s["wsdl_ok"])
    total_methods = sum(s["method_count"] for s in summary)
    print(f"{ok}/{len(summary)} WSDL okundu, toplam {total_methods} metot")
    print("\nARAÇ KONUMU ADAYLARI:")
    print("\n".join(f"  {c}" for c in all_candidates) if all_candidates else "  YOK")
    print("\nMetot listeleri: tests/fixtures/iett_wsdl/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
