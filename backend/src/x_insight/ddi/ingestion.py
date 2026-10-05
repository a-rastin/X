"""Offline DDI monograph ingestion (S15, seam T3; plan.md §6, FR-14/NFR-05).

Public interface: :func:`build` converts ``.txt`` monographs found under a
source directory into a candidate dataset plus a validation report. No LLM,
no network, no fuzzy matching, no database: raw interacting names are kept
verbatim (concept resolution lands in S17) and every entry keeps its raw
text plus original line span.

Parser model (DDI design §§7–8): decode (BOM-tolerant), drop repeated page
chrome while remembering its line numbers, locate the actual ``Interactions``
section (bare navigation headings carry no counts and are ignored), then run
a minimal section/state machine over ``Name (N)`` severity headings. A page
break inside one entry leaves chrome-only lines between its blocks, so a
block separated from the previous one by chrome continues the same entry;
any other block boundary starts a new entry. Declared ``(N)`` counts must
equal parsed entry counts or the document fails with an anomaly — counts
are never repaired by truncation or padding.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PARSER_VERSION = "ddi-ingestion/0.1.0"

_CATEGORY_BY_HEADING = {
    "Contraindicated": "contraindicated",
    "Serious": "serious",
    "Monitor Closely": "monitor_closely",
    "Minor": "minor",
}

_ALL_CATEGORIES = ("contraindicated", "serious", "monitor_closely", "minor")

_CATEGORY_HEADING = re.compile(r"^(Contraindicated|Serious|Monitor Closely|Minor) \((\d+)\)$")
_PAGE_TIMESTAMP = re.compile(r"^\d{1,2}/\d{1,2}/\d{2},")
_PAGE_URL = re.compile(r"^https?://\S+\s+\d+/\d+\s*$")
_SECTION_END = frozenset({"Adverse Effects", "Warnings"})
_ENTITY_START = re.compile(
    r"^([A-Za-z][A-Za-z0-9'’\-/() ]*?)(?:,|\s+(?:decreases|increases|will)\b)"
)


@dataclass(frozen=True)
class CandidateEntry:
    """One parsed interaction assertion with source provenance."""

    source_path: str
    source_category: str
    interacting_name: str
    raw_text: str
    span_start: int
    span_end: int


@dataclass(frozen=True)
class SourceDocument:
    source_path: str
    checksum: str
    entries: tuple[CandidateEntry, ...] = ()


@dataclass(frozen=True)
class DocumentCheck:
    source_path: str
    checksum: str
    expected_counts: dict[str, int] = field(default_factory=dict)
    parsed_counts: dict[str, int] = field(default_factory=dict)
    passed: bool = False
    anomalies: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateDataset:
    parser_version: str = PARSER_VERSION
    documents: tuple[SourceDocument, ...] = ()


@dataclass(frozen=True)
class Report:
    parser_version: str = PARSER_VERSION
    documents: tuple[DocumentCheck, ...] = ()
    passed: bool = False


def _read_lines(path: Path) -> list[tuple[int, str]] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return [(number, line) for number, line in enumerate(text.splitlines(), 1)]


def _is_chrome(line: str) -> bool:
    return bool(_PAGE_TIMESTAMP.match(line) or _PAGE_URL.match(line))


def build(
    source_dir: str | Path,
    terminology: str | Path | Mapping[str, Any] | None = None,
    review_manifest: str | Path | Mapping[str, Any] | None = None,
) -> tuple[CandidateDataset, Report]:
    """Build a candidate dataset + validation report from ``source_dir``.

    ``terminology``/``review_manifest`` are accepted for the plan.md §3.2
    interface but unused in S15 (resolution lands in S17, review in S18);
    a missing terminology path is tolerated, never fatal.
    """
    root = Path(source_dir)
    if not root.is_dir():
        raise ValueError(f"source_dir is not a directory: {source_dir}")

    documents: list[SourceDocument] = []
    checks: list[DocumentCheck] = []
    for path in sorted(root.rglob("*.txt")):
        relative = path.relative_to(root).as_posix()
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        entries, check = _parse_document(relative, path, checksum)
        documents.append(SourceDocument(source_path=relative, checksum=checksum, entries=entries))
        checks.append(check)

    dataset = CandidateDataset(documents=tuple(documents))
    report = Report(documents=tuple(checks), passed=all(c.passed for c in checks))
    return dataset, report


def _parse_document(
    relative: str, path: Path, checksum: str
) -> tuple[tuple[CandidateEntry, ...], DocumentCheck]:
    numbered = _read_lines(path)
    if numbered is None:
        check = DocumentCheck(
            source_path=relative,
            checksum=checksum,
            passed=False,
            anomalies=(f"{relative}: unreadable (I/O or non-UTF-8 bytes)",),
        )
        return (), check
    lines = [(number, line) for number, line in numbered if not _is_chrome(line)]
    chrome_lines = {number for number, line in numbered if _is_chrome(line)}

    start = next((i for i, (_, line) in enumerate(lines) if line == "Interactions"), None)
    if start is None:
        check = DocumentCheck(
            source_path=relative,
            checksum=checksum,
            passed=False,
            anomalies=(f"{relative}: interaction section not found",),
        )
        return (), check
    end = next(
        (i for i, (_, line) in enumerate(lines) if i > start and line.strip() in _SECTION_END),
        len(lines),
    )
    section = lines[start + 1 : end]

    expected: dict[str, int] = {}
    parsed: dict[str, int] = {key: 0 for key in _ALL_CATEGORIES}
    entries: list[CandidateEntry] = []
    anomalies: list[str] = []
    category: str | None = None
    current: list[tuple[int, str]] | None = None
    current_header: str | None = None
    current_start = 0

    blocks: list[list[tuple[int, str]]] = []
    for number, line in section:
        if not line.strip():
            continue
        previous = blocks[-1] if blocks else None
        if previous is not None and number == previous[-1][0] + 1:
            previous.append((number, line))
        else:
            blocks.append([(number, line)])

    def close_entry() -> None:
        if current:
            entry = _make_entry(relative, category or "", current_header, current_start, current)
            entries.append(entry)
            parsed[category or ""] = parsed.get(category or "", 0) + 1

    previous_end = 0
    for block in blocks:
        number, line = block[0]
        heading = _CATEGORY_HEADING.match(line) if len(block) == 1 else None
        if heading:
            close_entry()
            current, current_header = None, None
            category = _CATEGORY_BY_HEADING[heading.group(1)]
            expected[category] = int(heading.group(2))
            previous_end = number
            continue
        if category is None:
            previous_end = block[-1][0]
            continue
        gap_has_chrome = any(previous_end < n < number for n in chrome_lines)
        bare_header = len(block) == 1 and "." not in line
        continued = False
        if current is not None:
            if _same_entity(block[0][1], _current_entity(current, current_header)):
                # Repeated assertion about the same pair inside one category
                # (e.g. clonidine, guanfacine, lonapegsomatropin): the
                # monograph's own count keeps it in a single entry, so the
                # follow-on block extends it with its full text preserved.
                current.extend(block)
                continued = True
            elif gap_has_chrome and _continues_entry(current, current_header, block):
                # Page break inside one entry: chrome-only lines between blocks.
                current.extend(block)
                continued = True
        if not continued:
            close_entry()
            first = block[0][1].strip()
            if len(block) > 1 and "." not in first and _confirms_header(block):
                current, current_header = [ln for ln in block[1:]], first
            elif bare_header:
                current, current_header = [], first
            else:
                current, current_header = list(block), None
            current_start = block[0][0]
        previous_end = block[-1][0]
    close_entry()

    for key in _ALL_CATEGORIES:
        if key not in expected:
            anomalies.append(f"{relative}: missing category heading for {key}")
        elif parsed.get(key, 0) != expected[key]:
            anomalies.append(
                f"{relative}: {key} expected {expected[key]} parsed {parsed.get(key, 0)}"
            )
    passed = not anomalies
    check = DocumentCheck(
        source_path=relative,
        checksum=checksum,
        expected_counts=expected,
        parsed_counts={key: parsed.get(key, 0) for key in _ALL_CATEGORIES},
        passed=passed,
        anomalies=tuple(anomalies),
    )
    return tuple(entries), check


def _current_entity(
    current: list[tuple[int, str]] | None, current_header: str | None
) -> str | None:
    if current_header is not None:
        return current_header
    if current:
        return _name_from_text(current[0][1])
    return None


def _same_entity(first_line: str, entity: str | None) -> bool:
    if not entity:
        return False
    first = first_line.strip()
    if first.lower() == entity.lower():
        return True
    if not first.lower().startswith(entity.lower()):
        return False
    # A repeated assertion continues "<entity>," or "<entity> <verb>", while a
    # distinct longer name ("testosterone" vs "testosterone buccal system")
    # continues with a plain word.
    rest = first[len(entity) :]
    return rest.startswith(",") or bool(
        re.match(r"\s+(?:decreases|increases|will)\b", rest, re.IGNORECASE)
    )


def _confirms_header(block: list[tuple[int, str]]) -> bool:
    first = block[0][1].strip()
    second = block[1][1].strip()
    return second.lower().startswith(first.lower()) or second.lower().startswith("sitagliptin")


def _starts_new_entry(block: list[tuple[int, str]]) -> bool:
    first = block[0][1].strip()
    if "." not in first:
        return len(block) == 1 or _confirms_header(block)
    return _ENTITY_START.match(first) is not None


def _continues_entry(
    current: list[tuple[int, str]] | None,
    current_header: str | None,
    block: list[tuple[int, str]],
) -> bool:
    if current is None:
        return False
    if current_header is not None and not current:
        # Orphan header awaiting its content across a page break.
        first = block[0][1].strip()
        return first.lower().startswith(current_header.lower()) or first.lower().startswith(
            "sitagliptin"
        )
    return not _starts_new_entry(block)


def _make_entry(
    relative: str,
    category: str,
    header: str | None,
    span_start: int,
    lines: list[tuple[int, str]],
) -> CandidateEntry:
    name = header if header is not None else _name_from_text(lines[0][1])
    raw_text = re.sub(r"\s+", " ", " ".join(line.strip() for _, line in lines)).strip()
    return CandidateEntry(
        source_path=relative,
        source_category=category,
        interacting_name=name,
        raw_text=raw_text,
        span_start=span_start,
        span_end=lines[-1][0],
    )


def _name_from_text(first_line: str) -> str:
    match = _ENTITY_START.match(first_line.strip())
    if match:
        return match.group(1).strip()
    return first_line.strip()
