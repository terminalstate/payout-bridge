"""Timestamps and dates from exports, made timezone-aware.

Exports disagree on time zones: one writes UTC with a 'Z', one writes the account's local time with a
four-letter zone in the next column, a ledger writes naive local time. Every timestamp is turned into
an aware datetime at the edge; a naive one takes the zone its source was declared to use.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9 is not supported
    raise

UTC = timezone.utc

# Abbreviations seen in the TimeZone column of settlement reports. Fixed offsets: an abbreviation
# names an offset, not a place, so daylight saving is already in it (CET vs CEST).
_ABBREVIATIONS = {
    "UTC": 0,
    "GMT": 0,
    "Z": 0,
    "WET": 0,
    "WEST": 1,
    "CET": 1,
    "CEST": 2,
    "EET": 2,
    "EEST": 3,
    "MSK": 3,
    "ICT": 7,
    "WIB": 7,
    "SGT": 8,
    "HKT": 8,
    "AWST": 8,
    "PHT": 8,
    "JST": 9,
    "KST": 9,
    "AEST": 10,
    "AEDT": 11,
    "NZST": 12,
    "NZDT": 13,
    "HST": -10,
    "AKST": -9,
    "AKDT": -8,
    "PST": -8,
    "PDT": -7,
    "MST": -7,
    "MDT": -6,
    "CDT": -5,
    "EST": -5,
    "EDT": -4,
    "BRT": -3,
    "ART": -3,
}
# "BST" (British or Bangladesh), "IST" (India, Ireland, Israel), "CST" (US Central, China), "AST" and others are
# ambiguous: pass the zone explicitly instead.


class TimeError(ValueError):
    pass


def zone(name: str | None) -> tzinfo:
    """'UTC', 'Europe/Amsterdam', 'CET', '+09:00' → tzinfo."""
    if name is None or name == "":
        return UTC
    n = name.strip()
    m = re.fullmatch(r"(?:UTC|GMT)?([+-])(\d{1,2}):?(\d{2})?", n)
    if m:
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return timezone(-delta if m.group(1) == "-" else delta)
    if n.upper() in _ABBREVIATIONS:
        hours = _ABBREVIATIONS[n.upper()]
        return timezone(timedelta(minutes=int(hours * 60)), n.upper()) if hours else UTC
    try:
        return ZoneInfo(n)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise TimeError(f"unknown time zone {name!r}: use an IANA name like 'Europe/Amsterdam' or an offset like '+01:00'") from e


_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def parse_datetime(text: str | None, default_tz: tzinfo = UTC) -> datetime | None:
    """ISO-ish text or a Unix timestamp → aware datetime in UTC. Empty → None."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{9,11}(\.\d+)?", s):
        return datetime.fromtimestamp(float(s), UTC)
    s = s.replace("T", " ")
    tz: tzinfo | None = None
    if s.endswith("Z"):
        s, tz = s[:-1].rstrip(), UTC
    else:
        # an offset after the time of day: +09:00, +0900, +09 (PostgreSQL writes the last one)
        m = re.search(r"(?<=\d:\d\d)(?:[.,]\d+)?\s?([+-])(\d{2})(?::?(\d{2}))?$", s)
        if m:
            if int(m.group(2)) > 14 or int(m.group(3) or 0) > 59:
                raise TimeError(f"not a time zone offset in {text!r}")
            delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
            tz = timezone(-delta if m.group(1) == "-" else delta)
            s = s[: m.start(1)].rstrip()
    # fractions of a second beyond microseconds are dropped
    s = re.sub(r"([.,])(\d{6})\d+$", r".\2", s)
    s = re.sub(r",(\d{1,6})$", r".\1", s)
    for fmt in _FORMATS:
        try:
            naive = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        raise TimeError(f"not a date or time: {text!r}")
    return naive.replace(tzinfo=tz or default_tz).astimezone(UTC)


def local_date(moment: datetime, tz: tzinfo) -> date:
    return moment.astimezone(tz).date()


def parse_date(text: str | None, default_tz: tzinfo = UTC) -> date | None:
    """A date column: the date as written ('2026-09-14 00:00:00+09:00' → 2026-09-14). A Unix timestamp
    is read as a date in default_tz."""
    if text is None or str(text).strip() == "":
        return None
    s = str(text).strip()
    m = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})(?:$|[ T])", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as e:
            raise TimeError(f"not a date: {text!r}") from e
    dt = parse_datetime(s, default_tz)
    return local_date(dt, default_tz)
