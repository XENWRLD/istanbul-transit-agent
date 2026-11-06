"""Sanity check GetHatOtoKonum_json: how many live buses does it report per line?

The first call returned only 2 vehicles for line 34 (Metrobüs, the busiest in
the city), which is low enough to suspect the method reports something
narrower than "every bus on the line". Compare several lines, and compare the
total against GetFiloAracKonum_json, before the tool table relies on either.

    uv run python scripts/probe_hat_konum.py
"""

from __future__ import annotations

import json
import sys
import time
from xml.etree import ElementTree

import requests

from istanbul_agent.config import get_settings

TEMPURI = "http://tempuri.org/"
FLEET_URL = "/iett/FiloDurum/SeferGerceklesme.asmx"

LINES = ["34", "34AS", "500T", "15F", "11", "76D", "E-10", "145M", "99A", "14"]

ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    '<soap:Body><{method} xmlns="{ns}">{params}</{method}></soap:Body></soap:Envelope>'
)


def call(session: requests.Session, base: str, method: str, params: str, timeout: float):
    body = ENVELOPE.format(method=method, ns=TEMPURI, params=params)
    response = session.post(
        f"{base}{FLEET_URL}",
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{TEMPURI}{method}"',
        },
        timeout=timeout,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    for element in root.iter():
        if element.tag.endswith("Result") and element.text:
            text = element.text.strip()
            if text.startswith(("[", "{")):
                return json.loads(text)
    return None


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    session = requests.Session()

    print("=== GetFiloAracKonum_json (tüm filo) ===")
    fleet = call(session, base, "GetFiloAracKonum_json", "", settings.ibb_request_timeout)
    print(f"  {len(fleet)} araç")
    operators = {}
    for vehicle in fleet:
        operators[vehicle.get("Operator")] = operators.get(vehicle.get("Operator"), 0) + 1
    for name, count in sorted(operators.items(), key=lambda kv: -kv[1]):
        print(f"    {count:5}  {name}")
    moving = sum(1 for v in fleet if (v.get("Hiz") or "0") not in ("0", "", None))
    print(f"  hızı sıfırdan büyük: {moving}")
    print(f"  örnek Saat değerleri: {[v.get('Saat') for v in fleet[:5]]}")

    print("\n=== GetHatOtoKonum_json (hat bazlı) ===")
    for line in LINES:
        try:
            rows = call(
                session,
                base,
                "GetHatOtoKonum_json",
                f"<HatKodu>{line}</HatKodu>",
                settings.ibb_request_timeout,
            )
        except Exception as exc:
            print(f"  {line:6} HATA {type(exc).__name__}")
            continue
        if rows is None:
            print(f"  {line:6} sonuç yok")
            continue
        stamps = sorted({r.get("son_konum_zamani", "")[:16] for r in rows})
        name = rows[0].get("hatad") if rows else "-"
        print(f"  {line:6} {len(rows):3} araç  {str(name)[:34]:34} damgalar: {stamps[:3]}")
        time.sleep(0.4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
