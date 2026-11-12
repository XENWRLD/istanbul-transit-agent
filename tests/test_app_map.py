"""Gradio demosunun haritası: araç çıktısındaki koordinatları gerçekten buluyor mu?

Harita README'de özellik olarak sayılıyor, ama araç sonucu metni Python
repr'ı olarak saklandığı sürece hiç nokta bulamıyordu (karar 0016/4).
Buradaki testler düzeltmenin kendisini değil **sonucunu** tutuyor: gerçek
araç çıktısı biçiminden nokta çıkıyor mu, ve İstanbul dışındaki sayı
çiftleri haritaya düşmüyor mu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from istanbul_agent.agent.graph import ToolCall
from istanbul_agent.app import extract_points

NL = chr(10)


@dataclass
class FakeRun:
    """`extract_points` yalnızca `tool_calls` okuyor."""

    tool_calls: list[Any] = field(default_factory=list)


def result_text(name: str, lat: float, lon: float) -> str:
    """`find_mobility_options` ve `find_nearby_stops` çıktısının biçimi."""
    return (
        "{"
        + NL
        + '  "ok": true,'
        + NL
        + '  "points": ['
        + NL
        + "    {"
        + NL
        + f'      "name": "{name}",'
        + NL
        + f'      "lat": {lat},'
        + NL
        + f'      "lon": {lon},'
        + NL
        + '      "distance_m": 31.8'
        + NL
        + "    }"
        + NL
        + "  ]"
        + NL
        + "}"
    )


def call(text: str, name: str = "find_mobility_options") -> ToolCall:
    return ToolCall(name=name, arguments={}, result=text)


class TestExtractPoints:
    def test_a_real_tool_output_yields_a_point(self):
        run = FakeRun([call(result_text("Kadıköy Rıhtım - 3", 40.990788, 29.021666))])
        points = extract_points(run)
        assert len(points) == 1
        assert points[0]["ad"] == "Kadıköy Rıhtım - 3"
        assert points[0]["enlem"] == 40.990788

    def test_the_tool_name_is_carried_so_the_map_can_label_it(self):
        run = FakeRun([call(result_text("Bostancı İDO", 40.952302, 29.092913))])
        assert extract_points(run)[0]["araç"] == "find_mobility_options"

    def test_coordinates_outside_istanbul_are_dropped(self):
        # Araç çıktısındaki başka bir sayı çifti haritaya düşmemeli.
        run = FakeRun([call(result_text("Paris", 48.8566, 2.3522))])
        assert extract_points(run) == []

    def test_several_calls_contribute_their_own_points(self):
        run = FakeRun(
            [
                call(result_text("Kadıköy Rıhtım - 3", 40.990788, 29.021666)),
                call(result_text("Bostancı İDO", 40.952302, 29.092913), name="find_nearby_stops"),
            ]
        )
        points = extract_points(run)
        assert {point["araç"] for point in points} == {
            "find_mobility_options",
            "find_nearby_stops",
        }

    def test_a_python_repr_result_finds_nothing(self):
        # Düzeltmeden önceki hâl: sonuç repr olarak saklanınca koordinat
        # deseni hiç eşleşmiyordu. Test bu farkı sabitliyor.
        payload = result_text("Kadıköy Rıhtım - 3", 40.990788, 29.021666)
        run = FakeRun([call(str([{"type": "text", "text": payload}]))])
        assert extract_points(run) == []

    def test_an_unparseable_result_leaves_the_map_empty_without_failing(self):
        run = FakeRun([call("araç çağrılamadı")])
        assert extract_points(run) == []
