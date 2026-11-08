"""Metro İstanbul REST client, only what the timetable tool needs.

Two things about this API, both measured in Faz 0 (decision 0001):

- It answers **HTTP 200 even when it failed**. The body's `Success` field is
  the only reliable signal, so every response goes through `unwrap`.
- `GetAnnouncements/TR` is a plain GET and returns HTML in `Content`.

Announcements matter here because a schedule change is announced, not
reflected in the 2020 GTFS feed: if Metro İstanbul says it is running M7 on
an adjusted timetable, the headway this tool computed is wrong and the
answer has to say so.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

import httpx

from istanbul_agent.config import get_settings

__all__ = [
    "SCHEDULE_KEYWORDS",
    "Announcement",
    "fetch_announcements",
    "parse_announcements",
    "schedule_alerts",
    "unwrap",
]

ANNOUNCEMENTS_PATH = "/MetroIstanbul/api/MetroMobile/V2/GetAnnouncements/TR"

# Words that mark an announcement as being about the timetable rather than
# about a lift, a campaign or an event. Chosen from the live announcement
# titles in tests/fixtures/metro/metro-announcements-tr.json.
SCHEDULE_KEYWORDS = (
    "sefer",
    "tarife",
    "çalışma saat",
    "ilk sefer",
    "son sefer",
    "servis saat",
    "düzenleme",
    "ek sefer",
    "seferler",
)

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
# A line code as a standalone token: `M7`, `T1`, `F4`, `M1A`, `TF2`.
_LINE_TOKEN = re.compile(r"\b(?:TF|[MTF])\d+[AB]?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Announcement:
    """One announcement, with its markup stripped."""

    id: int
    title: str
    text: str
    lines: frozenset[str]

    @property
    def is_schedule_related(self) -> bool:
        haystack = f"{self.title} {self.text}".casefold()
        return any(keyword in haystack for keyword in SCHEDULE_KEYWORDS)

    def summary(self, limit: int = 240) -> str:
        text = self.text if self.text else self.title
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def unwrap(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The `Data` list of a Metro İstanbul response, or an error.

    Raises on `Success: false` because the status code does not report it.
    """
    if not payload.get("Success"):
        raise RuntimeError(f"Metro İstanbul API başarısız: {payload.get('Error')!r}")
    data = payload.get("Data")
    return data if isinstance(data, list) else []


def _plain_text(markup: str | None) -> str:
    """Announcement bodies are HTML; this is the readable text in them."""
    if not markup:
        return ""
    text = _TAGS.sub(" ", markup)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("•", " ")
    text = _SPACES.sub(" ", text)
    return " ".join(part.strip() for part in text.split("\n") if part.strip()).strip()


def parse_announcements(payload: dict[str, Any]) -> list[Announcement]:
    """Turn a `GetAnnouncements` response into announcements.

    The payload may be either the raw response or a saved fixture, which
    wraps the response under `response`.
    """
    body = payload.get("response", payload)
    announcements: list[Announcement] = []
    for row in unwrap(body):
        title = _plain_text(row.get("Title"))
        text = _plain_text(row.get("Content"))
        lines = frozenset(
            match.group(0).upper() for match in _LINE_TOKEN.finditer(f"{title} {text}")
        )
        announcements.append(
            Announcement(
                id=int(row.get("Id") or 0),
                title=title,
                text=text,
                lines=lines,
            )
        )
    return announcements


def schedule_alerts(announcements: list[Announcement], line: str) -> list[Announcement]:
    """Announcements about `line`'s timetable.

    An announcement has to be about the timetable -- a lift breakdown on M2
    is no reason to doubt M2's headway -- and it has to concern the line.
    An announcement naming no line at all concerns every line: "Metrolarda
    Kış Tarifesine Geçiliyor" changes first and last trains across the
    network without mentioning a single code.
    """
    wanted = line.strip().upper()
    return [
        announcement
        for announcement in announcements
        if announcement.is_schedule_related
        and (not announcement.lines or wanted in announcement.lines)
    ]


def fetch_announcements(*, timeout: float = 10.0) -> list[Announcement]:
    """Live announcements. Never called from unit tests; fixtures are used."""
    settings = get_settings()
    url = f"{settings.ibb_api_base_url.rstrip('/')}{ANNOUNCEMENTS_PATH}"
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        return parse_announcements(response.json())
