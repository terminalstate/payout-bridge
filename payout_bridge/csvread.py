"""CSV in, rows out, with line numbers kept for evidence."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from pathlib import Path


class InputError(ValueError):
    pass


def norm_header(name: str) -> str:
    """'Gross Credit (GC)' → 'gross credit (gc)'; 'payment_metadata[order_id]' stays itself, lower-cased."""
    return re.sub(r"\s+", " ", (name or "").replace("\ufeff", "").strip()).lower()


def read_rows(path: str | Path) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """→ (normalized headers, [(line number, {header: value})]). Blank lines are skipped."""
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
    if "\x00" in text:
        raise InputError(f"{path}: contains NUL bytes; save it as UTF-8 CSV")
    sample = text[:20000]
    first = next((ln for ln in sample.splitlines() if ln.strip()), "")
    counts = {d: first.count(d) for d in (",", ";", "\t")}
    delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows: list[tuple[int, dict[str, str]]] = []
    headers: list[str] = []
    try:
        for row in reader:
            line = reader.line_num
            if not headers:
                if not any(cell.strip() for cell in row):
                    continue
                headers = [norm_header(h) for h in row]
                continue
            if not any(cell.strip() for cell in row):
                continue
            rows.append((line, {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(headers) if h}))
    except csv.Error as e:
        raise InputError(f"{path}:{reader.line_num}: {e}") from e
    if not headers:
        raise InputError(f"{path}: empty file")
    return headers, rows


def pick(headers: list[str], *candidates: str) -> str | None:
    """The first candidate column present in headers (candidates are normalized)."""
    present = set(headers)
    for c in candidates:
        n = norm_header(c)
        if n in present:
            return n
    return None


def iter_values(rows: list[tuple[int, dict[str, str]]], column: str | None) -> Iterator[str]:
    for _, row in rows:
        yield row.get(column, "") if column else ""
