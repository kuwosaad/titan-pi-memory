"""Occurrence dates, separate from the time a memory was saved.

Intervals use calendar dates with an inclusive start and exclusive end. Unknown
occurrence dates are retained by default when filtering, so legacy records and
undated facts do not silently disappear. No ingestion timestamp is used as an
occurrence date; relative dates require a caller-supplied source-scene time.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Optional


_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_MONTH = re.compile(r"\d{4}-\d{2}\Z")
_DATE_TOKEN = r"\d{4}-\d{2}(?:-\d{2})?"
_EMPTY = {"event_date": None, "event_start": None, "event_end": None}


def _period(value: Any, reference_time: Any = None) -> Optional[tuple[date, date]]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower() in {"today", "yesterday", "tomorrow"}:
        anchor = _reference_date(reference_time)
        if anchor is None:
            return None
        try:
            day = anchor + timedelta(days={"today": 0, "yesterday": -1, "tomorrow": 1}[value.lower()])
            return day, day + timedelta(days=1)
        except OverflowError:
            return None
    try:
        if _DAY.fullmatch(value):
            day = date.fromisoformat(value)
            return day, day + timedelta(days=1)
        if _MONTH.fullmatch(value):
            first = date.fromisoformat(value + "-01")
            next_month = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            return first, next_month
    except (ValueError, OverflowError):
        pass
    return None


def _reference_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        # Preserve the local calendar date represented by the source timestamp.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def normalize_event_time(event_date: Any = None, event_start: Any = None,
                         event_end: Any = None, *, source_time: Any = None) -> dict[str, Optional[str]]:
    """Normalize explicit occurrence metadata; malformed or conflicting values are unknown.

    ``event_date`` accepts a strict ISO day/month or today/yesterday/tomorrow
    anchored to ``source_time``. Explicit start/end must both be ISO days and
    end is already exclusive. A supplied date and interval must agree exactly.
    """
    has_date = event_date is not None and event_date != ""
    has_bounds = event_start is not None or event_end is not None
    period = _period(event_date, source_time) if has_date else None
    if has_date and period is None:
        return _EMPTY.copy()
    if has_bounds:
        if not isinstance(event_start, str) or not isinstance(event_end, str):
            return _EMPTY.copy()
        if not _DAY.fullmatch(event_start) or not _DAY.fullmatch(event_end):
            return _EMPTY.copy()
        try:
            bounds = date.fromisoformat(event_start), date.fromisoformat(event_end)
        except ValueError:
            return _EMPTY.copy()
        if bounds[0] >= bounds[1] or (period is not None and period != bounds):
            return _EMPTY.copy()
        period = bounds
    if period is None:
        return _EMPTY.copy()
    normalized_date = None
    if has_date:
        normalized_date = event_date.strip()
        if normalized_date.lower() in {"today", "yesterday", "tomorrow"}:
            normalized_date = period[0].isoformat()
    return {"event_date": normalized_date, "event_start": period[0].isoformat(),
            "event_end": period[1].isoformat()}


def parse_temporal_query(text: str, *, reference_time: Any = None) -> Optional[tuple[str, str]]:
    """Parse unambiguous ISO date-only queries or explicit bracketed dates.

    Brackets accept a single day/month or inclusive range (``to``, ``through``,
    ``..``). Date-only relative queries require a supplied reference time.
    Other prose is left untouched; in particular, a date in an exclusion,
    comparison, filename or declarative statement is not treated as a filter.
    """
    if not isinstance(text, str) or len(text) > 200 or "\n" in text:
        return None
    text = text.strip()
    token = rf"(?:{_DATE_TOKEN}|today|yesterday|tomorrow)"
    single = re.fullmatch(token, text, re.IGNORECASE)
    brackets = list(re.finditer(rf"\[\s*({token})(?:\s*(?:to|through|\.\.)\s*({token}))?\s*\]", text, re.IGNORECASE))
    bracket = brackets[0] if len(brackets) == 1 else None
    if bracket and (text.count("[") != 1 or text.count("]") != 1 or
                    re.search(r"\b(?:not|except|exclude|excluding|before|after|since|until)\b", text, re.IGNORECASE)):
        return None
    if single:
        first, last = text, text
    elif bracket:
        first, last = bracket.group(1), bracket.group(2) or bracket.group(1)
    else:
        return None
    start, end = _period(first, reference_time), _period(last, reference_time)
    if start is None or end is None or start[0] >= end[1]:
        return None
    return start[0].isoformat(), end[1].isoformat()


def filter_records_by_event_time(records: Iterable[Mapping[str, Any]],
                                 interval: Optional[tuple[str, str]], *,
                                 retain_unknown: bool = True) -> list[Mapping[str, Any]]:
    """Keep overlapping occurrence intervals, optionally retaining undated records.

    ``interval`` is the exclusive-end result of ``parse_temporal_query``. Memory
    fields may be top-level or in a metadata mapping. Invalid metadata is unknown;
    saved-at timestamps are never used as a substitute.
    """
    if interval is None:
        return list(records)
    if (not isinstance(interval, (tuple, list)) or len(interval) != 2
            or not all(isinstance(v, str) and _DAY.fullmatch(v) for v in interval)):
        raise ValueError("interval must contain two ISO dates")
    try:
        query_start, query_end = map(date.fromisoformat, interval)
    except ValueError as exc:
        raise ValueError("interval must contain valid ISO dates") from exc
    if query_start >= query_end:
        raise ValueError("interval start must precede end")
    result = []
    for record in records:
        metadata = record.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        def field(name: str) -> Any:
            return record[name] if name in record else metadata.get(name)
        normalized = normalize_event_time(field("event_date"), field("event_start"), field("event_end"))
        if normalized["event_start"] is None:
            if retain_unknown:
                result.append(record)
        elif date.fromisoformat(normalized["event_start"]) < query_end and date.fromisoformat(normalized["event_end"]) > query_start:
            result.append(record)
    return result
