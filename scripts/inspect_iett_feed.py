"""İETT GTFS beslemesinin (veri seti 75) gerçekte ne döndüğünü ölç.

Mevcut okuyucu `store/gtfs.py` 2020 tarihli "Public Transport" beslemesine
göre yazıldı: cp1254, virgül ayırıcı, dolmuş `calendar` penceresi. İETT
beslemesi ilk bakışta bunların üçünde de farklı görünüyor. Kod yazmadan
önce dosya dosya ölçülen şeyler:

- kodlama (BOM var mı, cp1254 çözülüyor mu, çifte kodlama izi var mı),
- ayırıcı ve kolon listesi, satır sayısı,
- `stops.csv` koordinat biçimi ve onarıldığında İstanbul sınırlarına düşüp
  düşmediği,
- `calendar.csv` tarih penceresi bugünü kapsıyor mu (karar 0002'nin İETT'de
  de geçerli olup olmadığı buna bağlı),
- `stop_times.txt` satır sayısı ve saatin kaç satırda gerçekten dolu olduğu.

Çıktı `data/iett_feed_probe.json`; tek seferlik keşif betiği, ürün kodu değil.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

FEED_DIR = PROJECT_ROOT / "data" / "gtfs" / "iett"
OUT_PATH = PROJECT_ROOT / "data" / "iett_feed_probe.json"

# İstanbul kaba sınır kutusu: onarılan koordinatın buraya düşmesi beklenir.
LAT_RANGE = (40.75, 41.70)
LON_RANGE = (27.80, 30.00)

# Çifte kodlama izi: UTF-8 baytları latin1 sanılıp yeniden kodlanmış metin.
MOJIBAKE_MARKS = ("Ã", "Ä", "Å", "Â")


def describe_bytes(raw: bytes) -> dict[str, Any]:
    """Kodlama ve ayırıcıyı bayt düzeyinde tespit et."""
    info: dict[str, Any] = {"bom": raw.startswith(b"\xef\xbb\xbf")}
    try:
        raw.decode("utf-8")
        info["utf8"] = "ok"
    except UnicodeDecodeError as exc:
        info["utf8"] = f"hata: {exc.reason} @ {exc.start}"
    try:
        raw.decode("cp1254")
        info["cp1254"] = "ok"
    except UnicodeDecodeError as exc:
        info["cp1254"] = f"hata: {exc.reason} @ {exc.start}"
    header = raw.decode("utf-8", errors="replace").splitlines()[0] if raw else ""
    info["delimiter"] = ";" if header.count(";") > header.count(",") else ","
    return info


def mojibake_count(rows: list[dict[str, str]]) -> dict[str, int]:
    """Hangi kolonda kaç satır çifte kodlama izi taşıyor?"""
    counts: Counter[str] = Counter()
    for row in rows:
        for key, value in row.items():
            if value and any(mark in value for mark in MOJIBAKE_MARKS):
                counts[key or "?"] += 1
    return dict(counts)


def repair_coordinate(value: str) -> float | None:
    """Noktalarla parçalanmış koordinatı onar.

    `410.191.700.005.564` -> 41.0191700005564. Basamaklar doğru, yalnızca
    ondalık ayırıcı kaybolmuş ve binlik nokta serpilmiş. İlk iki basamaktan
    sonra ondalık koyma varsayımı bu betikte **doğrulanıyor**; koda ancak
    sınır kutusu testini geçerse girecek.
    """
    text = (value or "").strip()
    if not text:
        return None
    if text.count(".") == 1:
        try:
            number = float(text)
        except ValueError:
            number = None
        if number is not None and -180.0 <= number <= 180.0:
            return number
    digits = text.replace(".", "").replace(",", "")
    if not digits.isdigit() or len(digits) < 3:
        return None
    return float(f"{digits[:2]}.{digits[2:]}")


def read_csv(path: Path, delimiter: str, encoding: str) -> list[dict[str, str]]:
    with path.open(encoding=encoding, newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def probe_tables() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name in ("agency.csv", "calendar.csv", "routes.csv", "stops.csv", "trips.csv"):
        path = FEED_DIR / name
        raw = path.read_bytes()
        info = describe_bytes(raw[:8192])
        encoding = "utf-8-sig" if info["utf8"] == "ok" else "cp1254"
        rows = read_csv(path, info["delimiter"], encoding)
        info.update(
            {
                "size_mb": round(path.stat().st_size / 1e6, 2),
                "encoding_used": encoding,
                "rows": len(rows),
                "columns": list(rows[0]) if rows else [],
                "mojibake_rows_by_column": mojibake_count(rows),
                "sample": rows[0] if rows else None,
            }
        )
        report[name] = info
    return report


def probe_coordinates(delimiter: str, encoding: str) -> dict[str, Any]:
    rows = read_csv(FEED_DIR / "stops.csv", delimiter, encoding)
    plain = repaired = inside = unusable = 0
    examples: list[dict[str, Any]] = []
    for row in rows:
        raw_lat, raw_lon = row.get("stop_lat", ""), row.get("stop_lon", "")
        direct = raw_lat.count(".") == 1 and raw_lon.count(".") == 1
        lat, lon = repair_coordinate(raw_lat), repair_coordinate(raw_lon)
        if lat is None or lon is None:
            unusable += 1
            if len(examples) < 5:
                examples.append({"stop_id": row.get("stop_id"), "lat": raw_lat, "lon": raw_lon})
            continue
        if direct:
            plain += 1
        else:
            repaired += 1
        if LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]:
            inside += 1
    ids = [row.get("stop_id", "") for row in rows]
    codes = [row.get("stop_code", "") for row in rows]
    return {
        "stops": len(rows),
        "already_plain": plain,
        "needed_repair": repaired,
        "unusable": unusable,
        "inside_istanbul_after_repair": inside,
        "unusable_examples": examples,
        "duplicate_stop_ids": len(ids) - len(set(ids)),
        "duplicate_stop_codes": len(codes) - len(set(codes)),
        "empty_stop_names": sum(1 for row in rows if not (row.get("stop_name") or "").strip()),
    }


def probe_calendar(delimiter: str, encoding: str) -> dict[str, Any]:
    rows = read_csv(FEED_DIR / "calendar.csv", delimiter, encoding)
    today = date.today().strftime("%Y%m%d")
    services = []
    for row in rows:
        start = row.get("start_date") or ""
        end = row.get("end_date") or ""
        services.append(
            {
                "service_id": row.get("service_id"),
                "service_name": row.get("service_name"),
                "start_date": start,
                "end_date": end,
                "covers_today": bool(start and end and start <= today <= end),
            }
        )
    return {
        "services": services,
        "today": today,
        "calendar_dates_present": (FEED_DIR / "calendar_dates.csv").exists(),
    }


def probe_stop_times(limit: int | None) -> dict[str, Any]:
    """stop_times.txt'yi zip içinden akıtarak say."""
    path = FEED_DIR / "stop_times.zip"
    total = with_time = timepoints = 0
    trips: set[str] = set()
    stops: set[str] = set()
    out_of_order = 0
    previous: tuple[str, int] | None = None
    with zipfile.ZipFile(path) as archive:
        member = archive.infolist()[0]
        with archive.open(member) as binary:
            stream = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(stream):
                total += 1
                trips.add(row["trip_id"])
                stops.add(row["stop_id"])
                if row.get("timepoint") == "1":
                    timepoints += 1
                departure = (row.get("departure_time") or "").strip()
                if departure:
                    with_time += 1
                    seconds = (
                        int(departure.split(":")[0]) * 3600 + int(departure.split(":")[1]) * 60
                    )
                    if previous and previous[0] == row["trip_id"] and seconds < previous[1]:
                        out_of_order += 1
                    previous = (row["trip_id"], seconds)
                if limit and total >= limit:
                    break
    return {
        "member": member.filename,
        "uncompressed_mb": round(member.file_size / 1e6, 1),
        "rows_read": total,
        "truncated": bool(limit and total >= limit),
        "rows_with_departure_time": with_time,
        "timepoint_rows": timepoints,
        "distinct_trips": len(trips),
        "distinct_stops": len(stops),
        "decreasing_times_within_trip": out_of_order,
    }


def probe_routes(delimiter: str, encoding: str) -> dict[str, Any]:
    rows = read_csv(FEED_DIR / "routes.csv", delimiter, encoding)
    types = Counter(row.get("route_type", "") for row in rows)
    shorts = [row.get("route_short_name", "") for row in rows]
    return {
        "routes": len(rows),
        "route_types": dict(types),
        "distinct_short_names": len(set(shorts)),
        "duplicate_short_names": len(shorts) - len(set(shorts)),
        "sample_long_names": [row.get("route_long_name", "") for row in rows[:3]],
    }


def probe_trips(delimiter: str, encoding: str) -> dict[str, Any]:
    rows = read_csv(FEED_DIR / "trips.csv", delimiter, encoding)
    services = Counter(row.get("service_id", "") for row in rows)
    directions = Counter(row.get("direction_id", "") for row in rows)
    return {
        "trips": len(rows),
        "by_service_id": dict(services),
        "by_direction_id": dict(directions),
        "distinct_routes": len({row.get("route_id", "") for row in rows}),
    }


def main() -> int:
    if not FEED_DIR.exists():
        print(f"besleme yok: {FEED_DIR} — scripts/download_gtfs.py --feed iett")
        return 1

    report: dict[str, Any] = {"feed_dir": str(FEED_DIR)}
    report["tables"] = probe_tables()
    stops_info = report["tables"]["stops.csv"]
    report["coordinates"] = probe_coordinates(stops_info["delimiter"], stops_info["encoding_used"])
    calendar_info = report["tables"]["calendar.csv"]
    report["calendar"] = probe_calendar(calendar_info["delimiter"], calendar_info["encoding_used"])
    routes_info = report["tables"]["routes.csv"]
    report["routes"] = probe_routes(routes_info["delimiter"], routes_info["encoding_used"])
    trips_info = report["tables"]["trips.csv"]
    report["trips"] = probe_trips(trips_info["delimiter"], trips_info["encoding_used"])
    limit = None if "--full" in sys.argv else 2_000_000
    report["stop_times"] = probe_stop_times(limit)

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== İETT GTFS beslemesi ===")
    for name, info in report["tables"].items():
        print(
            f"  {name:16} {info['size_mb']:>6.2f} MB  {info['rows']:>7} satır  "
            f"ayırıcı '{info['delimiter']}'  BOM {'var' if info['bom'] else 'yok'}  "
            f"utf8 {info['utf8'][:14]}  cp1254 {info['cp1254'][:14]}"
        )
        if info["mojibake_rows_by_column"]:
            print(f"    çifte kodlama izi: {info['mojibake_rows_by_column']}")

    coords = report["coordinates"]
    print(
        f"\n  koordinat: {coords['stops']} durak — düzgün {coords['already_plain']}, "
        f"onarıldı {coords['needed_repair']}, kullanılamaz {coords['unusable']}; "
        f"onarım sonrası İstanbul içinde {coords['inside_istanbul_after_repair']}"
    )
    print(
        f"  durak kimliği tekrarı {coords['duplicate_stop_ids']}, "
        f"durak kodu tekrarı {coords['duplicate_stop_codes']}, "
        f"boş ad {coords['empty_stop_names']}"
    )
    if coords["unusable_examples"]:
        print(f"  kullanılamayan örnekler: {coords['unusable_examples']}")

    print(f"\n  calendar (bugün {report['calendar']['today']}):")
    for service in report["calendar"]["services"]:
        print(
            f"    {service['service_id']!s:>3} {service['service_name'] or '':10} "
            f"{service['start_date']}-{service['end_date']}  "
            f"bugünü kapsıyor: {'EVET' if service['covers_today'] else 'HAYIR'}"
        )
    print(
        f"    calendar_dates.csv: {'var' if report['calendar']['calendar_dates_present'] else 'yok'}"
    )

    routes = report["routes"]
    print(
        f"\n  hat: {routes['routes']} satır, {routes['distinct_short_names']} ayrı kod, "
        f"tip dağılımı {routes['route_types']}"
    )
    print(f"    örnek ad: {routes['sample_long_names']}")
    trips = report["trips"]
    print(
        f"  sefer: {trips['trips']} satır, {trips['distinct_routes']} hat, "
        f"servis {trips['by_service_id']}, yön {trips['by_direction_id']}"
    )

    times = report["stop_times"]
    share = (
        100 * times["rows_with_departure_time"] / times["rows_read"] if times["rows_read"] else 0
    )
    print(
        f"\n  stop_times: {times['member']} {times['uncompressed_mb']} MB, "
        f"{times['rows_read']} satır okundu{' (KESİLDİ)' if times['truncated'] else ''}, "
        f"{times['distinct_trips']} sefer, {times['distinct_stops']} durak"
    )
    print(
        f"    kalkış saati dolu: {times['rows_with_departure_time']} (%{share:.1f}), "
        f"timepoint=1: {times['timepoint_rows']}, "
        f"sefer içinde geriye giden saat: {times['decreasing_times_within_trip']}"
    )
    print(f"\n  rapor: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
