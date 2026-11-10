"""İETT beslemesinde tarife gerçekte nerede, ve bozuk metin onarılabiliyor mu?

`inspect_iett_feed.py` iki şeyi işaret etti, ikisi de araç tasarımını
doğrudan değiştirir:

1. `stop_times.txt`'nin yalnızca %4.4'ünde kalkış saati var ve `timepoint=1`
   satır sayısı sefer sayısına **birebir eşit** (135 625). Bu doğruysa
   besleme sefer başına tek saat veriyor; ara duraklara saat veremeyiz.
   Burada ölçülen: sefer başına kaç saat var, o saat hangi
   `stop_sequence`'ta ve saatsiz duraklar için sıra bilgisi sağlam mı.
2. `routes.csv`'de 9067 satırda çifte kodlama izi var (`KADIKÃ–Y`). Onarım
   `latin1`'e kodlayıp `utf-8` çözmek; burada ölçülen: kaç satır onarılıyor,
   kaç satır onarımdan sonra bozuk kalıyor, onarım düzgün satırları bozuyor mu.

Çıktı `data/iett_times_probe.json`; tek seferlik keşif betiği.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from collections import Counter
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

FEED_DIR = PROJECT_ROOT / "data" / "gtfs" / "iett"
OUT_PATH = PROJECT_ROOT / "data" / "iett_times_probe.json"

# `Â` bilerek listede değil: "güzergâh" gerçek Türkçe ve onu bozuk sayan
# tespit, onarılmış dizeleri "hâlâ bozuk" diye raporluyordu.
MOJIBAKE_MARKS = ("Ã", "Ä", "Å")


def looks_mojibake(text: str) -> bool:
    return any(mark in text for mark in MOJIBAKE_MARKS)


# Çifte kodlamanın hangi kod sayfasından geldiği ölçümle belirlendi ve tek
# bir kod sayfası yetmiyor: `KADIKÃ–Y`in `–`si cp1252'de 0x96, `ESATPAÅA`nın
# ``si ise latin1'de 0x9E. Aynı dizede ikisi birden görülüyor, bu yüzden
# onarım karakter bazında yapılır: her karakter için cp1252, olmazsa latin1.
MISREAD_AS = ("cp1252", "latin1")


def _original_bytes(text: str) -> bytes | None:
    """Yanlış okunmuş metni, okunduğu baytlara geri çevir.

    Bir karakter hiçbir kod sayfasına sığmıyorsa `None` döner — o dize
    gerçek Türkçe karakter taşıyor, yani zaten bozuk değil.
    """
    out = bytearray()
    for char in text:
        for encoding in MISREAD_AS:
            try:
                out += char.encode(encoding)
                break
            except UnicodeEncodeError:
                continue
        else:
            return None
    return bytes(out)


def repair_mojibake(text: str) -> tuple[str, str | None]:
    """UTF-8 baytları yanlış kod sayfasıyla okunmuş metni geri çevir.

    Onarılan metni ve yöntemi döner; onarılamazsa metin olduğu gibi ve
    `None`. Yarım onarım, bozuk bırakmaktan daha kötüdür: hangi satırın
    gerçekten düzeldiği anlaşılmaz.
    """
    raw = _original_bytes(text)
    if raw is None:
        return text, None
    try:
        return raw.decode("utf-8"), "karakter bazlı"
    except UnicodeDecodeError:
        return text, None


def probe_mojibake() -> dict[str, Any]:
    path = FEED_DIR / "routes.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))

    columns = ("route_short_name", "route_long_name", "route_desc", "route_code")
    broken = fixed = still_broken = unchanged_but_different = 0
    by_encoding: Counter[str] = Counter()
    examples: list[dict[str, str]] = []
    for row in rows:
        for column in columns:
            value = row.get(column) or ""
            if not value:
                continue
            repaired, used = repair_mojibake(value)
            if used:
                by_encoding[used] += 1
            if looks_mojibake(value):
                broken += 1
                if looks_mojibake(repaired) or repaired == value:
                    still_broken += 1
                    if len(examples) < 5:
                        examples.append({"column": column, "before": value, "after": repaired})
                else:
                    fixed += 1
                    if len([e for e in examples if e.get("kind") == "ok"]) < 3:
                        examples.append(
                            {"kind": "ok", "column": column, "before": value, "after": repaired}
                        )
            elif repaired != value:
                # Düzgün görünen satırı onarım değiştirdiyse onarım seçici olmalı.
                unchanged_but_different += 1
                if len([e for e in examples if e.get("kind") == "risk"]) < 3:
                    examples.append(
                        {"kind": "risk", "column": column, "before": value, "after": repaired}
                    )
    return {
        "rows": len(rows),
        "broken_values": broken,
        "repaired": fixed,
        "still_broken_after_repair": still_broken,
        "clean_values_changed_by_repair": unchanged_but_different,
        "repaired_by_encoding": dict(by_encoding),
        "examples": examples,
    }


def probe_trip_times() -> dict[str, Any]:
    """Sefer başına kaç saat var ve hangi sırada?"""
    times_per_trip: Counter[int] = Counter()
    sequence_of_time: Counter[str] = Counter()
    first_sequences: Counter[str] = Counter()
    current_trip: str | None = None
    current_times = 0
    sequences: list[int] = []
    gaps = 0
    trips = 0
    examples: list[dict[str, Any]] = []

    def close(trip: str | None, count: int, seqs: list[int]) -> None:
        nonlocal gaps
        if trip is None:
            return
        times_per_trip[count] += 1
        if seqs:
            first_sequences[str(min(seqs))] += 1
            if seqs != list(range(min(seqs), min(seqs) + len(seqs))):
                gaps += 1

    with zipfile.ZipFile(FEED_DIR / "stop_times.zip") as archive:
        member = archive.infolist()[0]
        with archive.open(member) as binary:
            stream = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            for row in csv.DictReader(stream):
                trip = row["trip_id"]
                if trip != current_trip:
                    close(current_trip, current_times, sequences)
                    if current_trip is not None:
                        trips += 1
                    current_trip, current_times, sequences = trip, 0, []
                sequences.append(int(row["stop_sequence"]))
                if (row.get("departure_time") or "").strip():
                    current_times += 1
                    sequence_of_time[row["stop_sequence"]] += 1
                    if len(examples) < 5 and row["stop_sequence"] != "1":
                        examples.append(dict(row))
    close(current_trip, current_times, sequences)
    trips += 1

    return {
        "trips": trips,
        "times_per_trip": {str(k): v for k, v in sorted(times_per_trip.items())},
        "stop_sequence_of_timed_rows": dict(sequence_of_time.most_common(10)),
        "first_stop_sequence_per_trip": dict(first_sequences.most_common(5)),
        "trips_with_sequence_gaps": gaps,
        "timed_rows_not_at_sequence_1": examples,
    }


def main() -> int:
    if not (FEED_DIR / "stop_times.zip").exists():
        print(f"besleme yok: {FEED_DIR}")
        return 1

    report: dict[str, Any] = {"mojibake": probe_mojibake(), "trip_times": probe_trip_times()}
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    moji = report["mojibake"]
    print("=== routes.csv çifte kodlama ===")
    print(f"  {moji['rows']} satır, bozuk değer {moji['broken_values']}")
    print(
        f"  onarıldı {moji['repaired']}, onarımdan sonra hâlâ bozuk "
        f"{moji['still_broken_after_repair']}, düzgün değeri bozan onarım "
        f"{moji['clean_values_changed_by_repair']}"
    )
    print(f"  işe yarayan kod sayfası: {moji['repaired_by_encoding']}")
    for example in moji["examples"][:6]:
        kind = example.get("kind", "bozuk")
        print(f"    [{kind}] {example['column']}: {example['before']!r} -> {example['after']!r}")

    times = report["trip_times"]
    print("\n=== stop_times: sefer başına saat ===")
    print(f"  {times['trips']} sefer")
    for count, how_many in times["times_per_trip"].items():
        print(f"    {count} saatli sefer: {how_many}")
    print(f"  saatli satırın stop_sequence dağılımı: {times['stop_sequence_of_timed_rows']}")
    print(f"  sefer başına ilk stop_sequence: {times['first_stop_sequence_per_trip']}")
    print(f"  sırası boşluklu sefer: {times['trips_with_sequence_gaps']}")
    if times["timed_rows_not_at_sequence_1"]:
        print("  1. sıra dışında saat taşıyan örnek satırlar:")
        for row in times["timed_rows_not_at_sequence_1"]:
            print(f"    {row}")

    print(f"\n  rapor: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
