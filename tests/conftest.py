"""Test oturumu geneli ayarlar.

Tek işi konsolu rapor karakterlerini basabilecek hale getirmek: ölçüm
tablosu yazdıran testler (`test_external_travel_times`, `test_interchanges`)
cp1254 stdout'ta `UnicodeEncodeError` ile düşüyordu.
"""

from __future__ import annotations

from istanbul_agent.console import enable_unicode_output

enable_unicode_output()
