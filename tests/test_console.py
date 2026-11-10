"""console.enable_unicode_output: yalnızca gereken akımı çevirir."""

from __future__ import annotations

import io
import sys

from istanbul_agent.console import PROBE, enable_unicode_output


def test_probe_characters_are_the_ones_reports_use():
    """Raporlardaki karakterler sondaj dizesinde olmalı."""
    assert "→" in PROBE
    assert "⚠" in PROBE


def test_switches_a_stream_that_cannot_encode(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1254")
    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        monkeypatch.setattr(sys, name, stream)

    assert enable_unicode_output() is True
    assert stream.encoding == "utf-8"
    stream.write(PROBE)  # eskiden UnicodeEncodeError


def test_leaves_a_utf8_stream_alone(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        monkeypatch.setattr(sys, name, stream)

    assert enable_unicode_output() is False
    assert stream.encoding == "utf-8"


def test_survives_a_stream_without_reconfigure(monkeypatch):
    """Yakalanmış akımlarda `reconfigure` olmayabilir; çökmemeli."""

    class Captured:
        encoding = "cp1254"

    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        monkeypatch.setattr(sys, name, Captured())

    assert enable_unicode_output() is False
