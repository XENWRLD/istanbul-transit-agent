"""Design the interchange rule from the data rather than from names.

The audit showed name matching inventing 26 interchanges, including M5 and
Marmaray2 "Altınşehir" 33 km apart, while missing the real M4-Marmaray
interchange because one side writes `Ayrılık Çeşmesi` and the other
`Ayrılıkçeşme` -- two records 7 m apart.

Both failures have the same cause: the name was treated as evidence of
place. An interchange is a physical fact, so measure it as one. This script
checks what a purely distance-based rule would produce, and which of the
over-radius pairs dataset 313 explicitly backs -- İBB naming a pair is an
authority saying the interchange exists, not a guess from spelling.

    uv run python scripts/inspect_transfer_rule.py
"""

from __future__ import annotations

import sys
from itertools import combinations

from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.rail_queries import load_network
from istanbul_agent.store.rail_reference import _line_family, load_rail_reference
from istanbul_agent.store.station_names import collapsed

RADIUS_M = 400.0
# How far a pair dataset 313 names may be before even that is doubted.
SANCTIONED_RADIUS_M = 1000.0


def main() -> int:
    network = load_network()
    reference = load_rail_reference()

    # Pairs dataset 313 names, as line-family pairs.
    sanctioned: set[frozenset[str]] = set()
    for transfer in reference.rail_transfers():
        if transfer.from_line and transfer.to_line:
            sanctioned.add(
                frozenset({_line_family(transfer.from_line), _line_family(transfer.to_line)})
            )

    positioned = [s for s in network.stations if s.has_position]
    print(f"{len(positioned)} konumlu istasyon kaydı, {len(sanctioned)} 313 hat çifti\n")

    # Every cross-line station pair within the sanctioned radius.
    candidates: list[tuple[float, object, object]] = []
    for left, right in combinations(positioned, 2):
        if left.line == right.line:
            continue
        distance = haversine_m(left.lat, left.lon, right.lat, right.lon)
        if distance <= SANCTIONED_RADIUS_M:
            candidates.append((distance, left, right))
    candidates.sort(key=lambda row: row[0])

    inside = [row for row in candidates if row[0] <= RADIUS_M]
    outside = [row for row in candidates if row[0] > RADIUS_M]

    print(f"=== Mesafe ≤ {RADIUS_M:.0f} m olan hatlar-arası çiftler: {len(inside)} ===")
    print(f"{'A hattı':10} {'A istasyonu':24} {'B hattı':10} {'B istasyonu':24} {'m':>6}  ad")
    for distance, left, right in inside:
        same = collapsed(left.name) == collapsed(right.name)
        print(
            f"{left.line:10} {left.name[:23]:24} {right.line:10} {right.name[:23]:24} "
            f"{distance:>6.0f}  {'aynı' if same else 'FARKLI'}"
        )

    different_names = [(d, a, b) for d, a, b in inside if collapsed(a.name) != collapsed(b.name)]
    print(
        f"\n  Bunların {len(different_names)}'i farklı adlı — ad eşleştirmesi bunları kaçırıyordu:"
    )
    for distance, left, right in different_names:
        print(
            f"    {left.line:10} {left.name:26} ↔ {right.line:10} {right.name:26} {distance:>5.0f}m"
        )

    print(f"\n\n=== {RADIUS_M:.0f}-{SANCTIONED_RADIUS_M:.0f} m arası ({len(outside)}) ===")
    print("313 bu hat çiftini adlandırıyorsa aktarma gerçek olabilir.\n")
    print(f"{'A hattı':10} {'A istasyonu':24} {'B hattı':10} {'B istasyonu':24} {'m':>6}  313")
    for distance, left, right in outside:
        backed = frozenset({_line_family(left.line), _line_family(right.line)}) in sanctioned
        print(
            f"{left.line:10} {left.name[:23]:24} {right.line:10} {right.name[:23]:24} "
            f"{distance:>6.0f}  {'EVET' if backed else 'hayır'}"
        )

    # ---- what the current (broken) rule claims that distance refuses ----
    print("\n\n=== Mevcut kuralın ürettiği ama mesafenin reddettiği ===")
    lines = sorted({s.line for s in network.stations})
    refused = 0
    for from_line, to_line in combinations(lines, 2):
        for change_from, change_to in network.transfer_options(from_line, to_line):
            if not (change_from.has_position and change_to.has_position):
                continue
            distance = haversine_m(change_from.lat, change_from.lon, change_to.lat, change_to.lon)
            if distance > SANCTIONED_RADIUS_M:
                refused += 1
                print(
                    f"  {from_line:10} {change_from.name[:23]:24} {to_line:10} "
                    f"{change_to.name[:23]:24} {distance:>8.0f}m"
                )
    print(f"\n  {refused} iddia {SANCTIONED_RADIUS_M:.0f} m'yi de aşıyor; hepsi ad eşleşmesinden.")

    # ---- does the real M4 interchange appear under the new rule? ----
    print("\n=== Ayrılık Çeşmesi yeni kuralda çıkıyor mu? ===")
    hits = [
        (d, a, b)
        for d, a, b in inside
        if "ayrilik" in collapsed(a.name) or "ayrilik" in collapsed(b.name)
    ]
    for distance, left, right in hits:
        print(
            f"  {left.line:10} {left.name:24} ↔ {right.line:10} {right.name:24} {distance:>5.0f}m"
        )
    print(f"  → {'EVET' if hits else 'hayır'}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
