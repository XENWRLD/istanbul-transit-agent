"""Work out what a per-line calibration would look like, without applying it.

The deviation investigation established that our pipeline reproduces the
feed's own end-to-end trip times exactly (M2 29.1, M4 38.4, M5 26.6, T1 67.8
minutes). So the gap against the external sources is not a computation error:
it is the 2020 feed diverging from how the lines run today, and the sign
differs per line -- M2 is optimistic by 3 minutes while T1 is pessimistic by
5. A single global correction would fix one and worsen the other.

That is what makes a per-line ratio the right shape. This script computes
each factor, says which are trustworthy, and shows the effect, so the
decision can be made before any of it reaches the query path.

    uv run python scripts/propose_calibration.py
"""

from __future__ import annotations

import csv
import json
import sys
import time

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.rail_queries import get_rail_travel_time, load_network

EXTERNAL = PROJECT_ROOT / "datasets" / "external_travel_times.csv"
OUT = PROJECT_ROOT / "data" / "calibration_proposal.json"

# A factor derived from a short journey is derived from rounding. F1's
# 2.0 vs 2.5 minutes is a 30 s difference over two stations: scaling the whole
# line by 1.25 on that evidence would be fitting to the external source's
# rounding, not to a real discrepancy.
MIN_STOPS_FOR_FACTOR = 5
MIN_MINUTES_FOR_FACTOR = 10.0

# Beyond this, a factor stops looking like drift and starts looking like the
# two numbers describing different journeys.
MAX_PLAUSIBLE_FACTOR = 1.5
MIN_PLAUSIBLE_FACTOR = 0.67


def main() -> int:
    if not EXTERNAL.exists():
        print(f"{EXTERNAL} yok.")
        return 1

    with EXTERNAL.open(encoding="utf-8") as handle:
        external = list(csv.DictReader(handle))

    network = load_network()
    rows: list[dict[str, object]] = []

    print("=" * 96)
    print("Dış kaynak karşılaştırması")
    print("=" * 96)
    print(
        f"{'Hat':5} {'Yolculuk':38} {'bizim':>7} {'dış':>6} {'fark':>7} "
        f"{'oran':>6} {'durak':>6} {'yöntem':>12}"
    )

    for entry in external:
        result = get_rail_travel_time(entry["from"], entry["to"], network=network)
        unverified = "DOĞRULANMADI" in (entry.get("notes") or "")
        external_minutes = float(entry["minutes"])

        if result.seconds is None:
            print(
                f"{entry['line']:5} {entry['from'][:17]:18}→{entry['to'][:17]:19} "
                f"{'—':>7} {external_minutes:>6.1f} {'—':>7} {'—':>6} {'—':>6} "
                f"{result.method:>12}"
            )
            continue

        ours = result.seconds / 60
        stops = sum(leg.stop_count for leg in result.legs)
        factor = external_minutes / ours if ours > 0 else None

        rows.append(
            {
                "line": entry["line"],
                "from": entry["from"],
                "to": entry["to"],
                "ours_minutes": round(ours, 2),
                "external_minutes": external_minutes,
                "delta_minutes": round(external_minutes - ours, 2),
                "factor": round(factor, 4) if factor else None,
                "stops": stops,
                "method": result.method,
                "estimated": result.estimated,
                "unverified": unverified,
                "source_url": entry["source_url"],
            }
        )

        print(
            f"{entry['line']:5} {entry['from'][:17]:18}→{entry['to'][:17]:19} "
            f"{ours:>7.2f} {external_minutes:>6.1f} {external_minutes - ours:>+7.2f} "
            f"{factor:>6.3f} {stops:>6} {result.method:>12}"
            + ("  [DOĞRULANMADI]" if unverified else "")
            + ("  [TAHMİN]" if result.estimated else "")
        )

    # ---------------- which factors are usable? ----------------
    print("\n" + "=" * 96)
    print("Hangi oranlar kalibrasyon için kullanılabilir?")
    print("=" * 96)
    print(f"{'Hat':5} {'oran':>7} {'durak':>6} {'dk':>6}  karar")

    usable: dict[str, dict[str, object]] = {}
    for row in rows:
        line = str(row["line"])
        reasons: list[str] = []
        if row["unverified"]:
            reasons.append("dış kaynak doğrulanmadı")
        if row["estimated"]:
            reasons.append("bizim sonucumuz tahmin; GTFS segmentini kalibre etmez")
        if int(row["stops"]) < MIN_STOPS_FOR_FACTOR:
            reasons.append(f"{row['stops']} durak < {MIN_STOPS_FOR_FACTOR}")
        if float(row["ours_minutes"]) < MIN_MINUTES_FOR_FACTOR:
            reasons.append(f"{row['ours_minutes']:.1f} dk < {MIN_MINUTES_FOR_FACTOR}")
        factor = row["factor"]
        if factor is not None and not MIN_PLAUSIBLE_FACTOR <= float(factor) <= MAX_PLAUSIBLE_FACTOR:
            reasons.append(f"oran {factor} makul aralık dışı")

        verdict = "KULLAN" if not reasons else "kullanma — " + "; ".join(reasons)
        print(
            f"{line:5} {factor if factor else '—':>7} {row['stops']:>6} "
            f"{row['ours_minutes']:>6.1f}  {verdict}"
        )
        if not reasons and factor is not None:
            usable[line] = {
                "factor": float(factor),
                "from": row["from"],
                "to": row["to"],
                "ours_minutes": row["ours_minutes"],
                "external_minutes": row["external_minutes"],
                "stops": row["stops"],
                "source_url": row["source_url"],
            }

    # ---------------- effect of applying it ----------------
    print("\n" + "=" * 96)
    print("Uygulanırsa etkisi (öneri — henüz uygulanmadı)")
    print("=" * 96)
    if not usable:
        print("  Kullanılabilir oran yok.")
    else:
        print(f"{'Hat':5} {'oran':>7} {'şimdi':>8} {'kalibreli':>10} {'dış':>6} {'kalan fark':>12}")
        for line, info in sorted(usable.items()):
            ours = float(info["ours_minutes"])  # type: ignore[arg-type]
            factor = float(info["factor"])  # type: ignore[arg-type]
            calibrated = ours * factor
            external_minutes = float(info["external_minutes"])  # type: ignore[arg-type]
            print(
                f"{line:5} {factor:>7.3f} {ours:>7.1f}dk {calibrated:>9.1f}dk "
                f"{external_minutes:>5.1f}dk {calibrated - external_minutes:>+11.2f}dk"
            )
        print(
            f"\n  Kalibre edilecek hat: {', '.join(sorted(usable))}  "
            f"({len(usable)}/{len(rows)} satır)"
        )
        print("  Diğer hatlar dokunulmaz; dış kaynak yoksa oran 1.0 kabul edilir.")

    OUT.write_text(
        json.dumps(
            {
                "proposed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "min_stops_for_factor": MIN_STOPS_FOR_FACTOR,
                "min_minutes_for_factor": MIN_MINUTES_FOR_FACTOR,
                "comparisons": rows,
                "usable_factors": usable,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
