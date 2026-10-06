"""Offline DDI monograph ingestion (S15, seam T3; plan.md §6, FR-14/NFR-05).

Public interface: :func:`build` converts ``.txt`` monographs found under a
source directory into a candidate dataset plus a validation report. No LLM,
no network, no fuzzy matching, no database: raw interacting names are kept
verbatim (concept resolution lands in S17) and every entry keeps its raw
text plus original line span.

Parser model (DDI design §§7–8): decode (BOM-tolerant, newline-only splits
so form feeds cannot shift spans), drop repeated page chrome while
remembering its line numbers, locate the actual ``Interactions`` section
(bare navigation headings carry no counts and are ignored), then run a
minimal section/state machine over ``Name (N)`` severity headings — found
even when glued to a neighbour line without a blank. Dense sections pack
several entries per blank-delimited block; a period-free line whose next
line echoes the name opens the next entry, otherwise blocks continue only
across chrome (page break), as subject-led restatements of the open pair,
or as repeated pair assertions. Condensed bullet sections start every
``•`` line as a new block: a bare ``• name`` header joins its content lines
(the bullet never leaks into the entry name), while an inline
``• n1, n2: shared description`` bullet splits into one entry per
comma-separated name — commas inside parentheses never split, and
slash-joined formulation variants stay one entry. The monograph subject
comes from the file stem — never a direction inference (S17 owns
resolution; raw text stays verbatim). A page break inside one entry leaves
chrome-only lines between its blocks, so a block separated from the
previous one by chrome continues the same entry; any other block boundary
starts a new entry. Declared ``(N)`` counts must equal parsed entry counts
or the document fails with an anomaly — counts are never repaired by
truncation or padding.
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
    "Serious Interactions": "serious",
    "Monitor Closely": "monitor_closely",
    "Significant - Monitor Closely": "monitor_closely",
    "Minor": "minor",
}

_ALL_CATEGORIES = ("contraindicated", "serious", "monitor_closely", "minor")

_CATEGORY_HEADING = re.compile(
    r"^(Contraindicated|Serious Interactions|Serious|Monitor Closely|"
    r"Significant - Monitor Closely|Minor) \((\d+)\)$"
)
_CITE_MARKER = re.compile(r"\[cite:[^\]]*\]")


def _without_cites(line: str) -> str:
    """Remove ``[cite: N]`` markers for structural matching only."""
    return _CITE_MARKER.sub("", line)


_PAGE_TIMESTAMP = re.compile(r"^\d{1,2}/\d{1,2}/\d{2},")
_PAGE_URL = re.compile(r"^https?://\S+(?:\s+\d+/\d+)?\s*$")
_PAGE_NUMBER = re.compile(r"^\d+/\d+\s*$")
_PAGE_TITLE = re.compile(r"dosing, indications, interactions, adverse effects, and more")
_SECTION_END = frozenset({"adverse effects", "warnings", "warnings & cautions"})


def _is_section_end(line: str) -> bool:
    """Whether a line closes the interaction section.

    Matches case-insensitively past markdown markers and citation
    suffixes (``## Adverse Effects``, ``ADVERSE EFFECTS [cite: N]``),
    so citation-style monographs stop before their adverse-effects text.
    """
    cleaned = _without_cites(line).strip().lstrip("#").strip().lower()
    return cleaned in _SECTION_END


def _is_interactions_heading(line: str) -> bool:
    """Whether a line opens the interaction section.

    Besides the standard ``Interactions`` banner, citation-style monographs
    use uppercase or markdown banners (``INTERACTIONS``, ``DRUG
    INTERACTIONS``, ``## Drug Interactions``) with ``[cite: N]`` markers
    glued on — the markers are ignored for section detection only;
    raw text stays verbatim.
    """
    cleaned = _without_cites(line).strip().lstrip("#").strip()
    return cleaned.lower() in ("interactions", "drug interactions")


def _match_category_heading(line: str) -> re.Match[str] | None:
    """Match a severity heading past markdown/citation decorations.

    Handles ``### Name (N)``, ``Name (N) [cite: N]``, and the
    ``Serious Interactions (N) - (...)`` summary-titled variant.
    """
    cleaned = _without_cites(line).strip().lstrip("#").strip()
    cleaned = re.sub(r"\s+-\s*\(.*\)$", "", cleaned).strip()
    return _CATEGORY_HEADING.match(cleaned)


_ENTITY_START = re.compile(
    r"^([A-Za-z][A-Za-z0-9'’\-/() ]*?)(?:,|\s+(?:decreases|increases|will)\b)"
)
# Words never found inside a drug name: when the comma-branch name part
# carries sentence verbs, the comma sits inside running prose
# ("decrease insulin sensitivity, particularly ..."), not at an entry
# boundary. Management fragments ("not clear, use caution.") match the same
# way: no interacting entity is named "not clear".
_PROSE_WORDS = re.compile(
    r"\b(?:and|or|both|either|may|will|is|are|was|were|decrease|decreases|increase|increases|not|clear|unclear|unknown|caution|with|deterioration|hypertension|levels|potential|results|synthesis|effects|dysfunction|inhibitors)\b",
    re.IGNORECASE,
)

# Clause markers never found inside a combination-product name
# ("belladonna and opium" stays a header): a period-free line carrying
# verbs or subordinate clauses is wrapped prose, not a name header.
_HEADER_PROSE = re.compile(
    r"\b(?:may|will|is|are|was|were|decrease|decreases|increase|increases|not|clear|unclear|unknown|caution|with|that|which)\b",
    re.IGNORECASE,
)


def _looks_like_header(first_line: str) -> bool:
    """Whether a period-free line can open an entry as its name header.

    A lone wrap fragment carrying running prose ("risk for rhabdomyolysis
    with drugs that increase ...") never opens an entry, even without a
    period — only a clean name line does.
    """
    text = first_line.strip()
    if "." in text:
        return False
    return _HEADER_PROSE.search(_strip_bullet(text)) is None


def _is_distinct_header(
    block: list[tuple[int, str]],
    next_block: list[tuple[int, str]] | None,
    entity: str | None,
    subject: str,
) -> bool:
    """Whether a block opens a header naming a different pair than open.

    A bare header confirmed by following content (echoed name in the same
    block, echoed name in the next block) always opens a new entry when it
    names a different pair — even when it textually overlaps the open one
    ("X, cell-cultured" after "X" are distinct listed pairs). A repeated
    bare restatement of the same pair still repeats-merges.
    """
    first = _strip_bullet(block[0][1].strip())
    if "." in first or not _looks_like_header(first):
        return False
    if len(block) > 1:
        if not _confirms_header(block, subject):
            return False
    elif next_block is None or not next_block[0][1].strip().lower().startswith(first.lower()):
        return False
    return entity is None or first.lower() != entity.lower()


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
    # Split on "\n" only: str.splitlines() also breaks on form feeds, which
    # would shift every later span away from the original file's line numbers.
    return [(number, line) for number, line in enumerate(text.split("\n"), 1)]


def _is_chrome(line: str) -> bool:
    # Page chrome takes several observed shapes: timestamp-titled headers
    # (sometimes form-feed prefixed), URL with the page marker glued on or
    # split across two lines, lone page markers, and standalone titles.
    clean = line.lstrip("\x0c")
    return bool(
        _PAGE_TIMESTAMP.match(clean)
        or _PAGE_URL.match(clean)
        or _PAGE_NUMBER.match(clean)
        or _PAGE_TITLE.search(clean)
    )


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

    start = next((i for i, (_, line) in enumerate(lines) if _is_interactions_heading(line)), None)
    if start is None:
        check = DocumentCheck(
            source_path=relative,
            checksum=checksum,
            passed=False,
            anomalies=(f"{relative}: interaction section not found",),
        )
        return (), check
    end = next(
        (i for i, (_, line) in enumerate(lines) if i > start and _is_section_end(line)),
        len(lines),
    )
    section = lines[start + 1 : end]

    expected: dict[str, int] = {}
    parsed: dict[str, int] = {key: 0 for key in _ALL_CATEGORIES}
    entries: list[CandidateEntry] = []
    anomalies: list[str] = []
    category: str | None = None
    # The monograph subject (file stem, e.g. acetaminophen): a block led by
    # this name is a continuation paragraph about the open pair seen from
    # the other direction — never a new entry about the drug itself.
    subject = Path(path.name).stem.strip().lower()
    current: list[tuple[int, str]] | None = None
    current_header: str | None = None
    current_start = 0

    blocks: list[list[tuple[int, str]]] = []
    for number, line in section:
        if not line.strip():
            continue
        previous = blocks[-1] if blocks else None
        # Every bullet line opens its own block: condensed sections pack one
        # entry (or one shared-description name list) per bullet line with no
        # blanks between them, so consecutive-line grouping alone would fuse
        # a whole category into a single entry. ``•`` and ``* `` bullets
        # share the mechanism (a ``*Note:`` prose line without the space
        # never splits). Following non-bullet lines still join the bullet's
        # block as wrapped content.
        if previous is not None and number == previous[-1][0] + 1 and not _is_bullet_line(line):
            previous.append((number, line))
        else:
            blocks.append([(number, line)])

    # Dense sections pack several entries in one blank-delimited block (a
    # period-free name line plus content lines, no blanks between entries):
    # split before a period-free line whose next line confirms it as a
    # header by echoing the name — either leading with it or mentioning it
    # (subject-led content names its pair drug). A bare last line strands
    # the same way when the next block confirms it, covering headers
    # orphaned by a blank/page break. Wrapped prose never echoes, so it
    # never splits.
    divided: list[list[tuple[int, str]]] = []
    for index, block in enumerate(blocks):
        start = 0
        for i in range(1, len(block)):
            candidate = block[i][1].strip()
            if "." in candidate:
                continue
            if i + 1 < len(block):
                following = block[i + 1][1].strip()
                # A wrapped content line cannot falsify the echo: extend
                # across the immediately wrapped continuation line while no
                # sentence ends, so a name wrapping mid-word still confirms
                # its header. Exactly one extra line: wider lookahead lets
                # short generic words ("Heartburn") echo coincidentally.
                if "." not in following and i + 2 < len(block):
                    following = following + " " + block[i + 2][1].strip()
            else:
                following = ""
                for later in blocks[index + 1 :]:
                    if later:
                        following = later[0][1].strip()
                        break
            if following and candidate.lower() in following.lower():
                divided.append(block[start:i])
                start = i
        divided.append(block[start:])

    def close_entry() -> None:
        if current:
            entry = _make_entry(relative, category or "", current_header, current_start, current)
            entries.append(entry)
            parsed[category or ""] = parsed.get(category or "", 0) + 1

    # A category heading shares its blank-delimited block with neighbour
    # lines when the source omits the blank (e.g. the nav summary glued to
    # "Contraindicated (0)"): split blocks at heading lines so the state
    # machine sees every declared heading. Lines before/after a heading stay
    # content under the then-current category.
    segments: list[tuple[str, list[tuple[int, str]]]] = []
    for block in divided:
        pending: list[tuple[int, str]] = []
        for item in block:
            if _match_category_heading(item[1]):
                if pending:
                    # Lines glued to the front of a heading are the tail of
                    # whatever came before, never a new entry.
                    segments.append(("tail", pending))
                    pending = []
                segments.append(("heading", [item]))
            else:
                pending.append(item)
        if pending:
            segments.append(("content", pending))

    previous_end = 0
    for index, (kind, block) in enumerate(segments):
        number, line = block[0]
        if kind == "tail":
            previous_end = block[-1][0]
            if category is None:
                continue
            first = block[0][1].strip()
            if "." in first and _entry_name(first) is None:
                # Pure prose tail glued to a heading: belongs to the open
                # entry, never a new one.
                if current is not None:
                    current.extend(block)
                continue
            # A header glued to a category heading is a genuine entry:
            # fall through to ordinary content handling below.
        heading = _match_category_heading(line) if kind == "heading" else None
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
        if _is_splittable_bullet(block):
            # Inline bullet: one entry per comma-separated name sharing the
            # bullet's text and span (paren-aware; slash-joined formulation
            # variants stay one entry).
            names = _bullet_names(block[0][1])
            if names:
                close_entry()
                current, current_header = None, None
                for name in names:
                    entries.append(_make_entry(relative, category, name, block[0][0], block))
                    parsed[category] = parsed.get(category, 0) + 1
                previous_end = block[-1][0]
                continue
        next_block = segments[index + 1][1] if index + 1 < len(segments) else None
        gap_has_chrome = any(previous_end < n < number for n in chrome_lines)
        bare_header = len(block) == 1 and _looks_like_header(line)
        continued = False
        if current is not None:
            entity = _current_entity(current, current_header)
            if _repeats_pair(block[0][1], entity, subject) and not _is_distinct_header(
                block, next_block, entity, subject
            ):
                # Repeated assertion about the same pair, possibly from the
                # other direction (e.g. clonidine; "gabapentin and alprazolam
                # both ..."): the monograph's own count keeps it in a single
                # entry, so the follow-on block extends it with its full
                # text preserved. A header naming a different pair never
                # merges, even when it textually overlaps the open one
                # ("X, cell-cultured" after "X" are distinct listed pairs).
                current.extend(block)
                continued = True
            elif _name_from_text(block[0][1]).lower() == subject and not _is_distinct_header(
                block, next_block, entity, subject
            ):
                # Subject-led continuation ("acetaminophen increases levels
                # of X ..."): the open pair from the other direction — unless
                # the block is a header naming a different pair (a swallowed
                # formulation header like "subject, long-acting injection"
                # opens its own entry).
                current.extend(block)
                continued = True
            elif gap_has_chrome and _continues_entry(current, current_header, block, subject):
                # Page break inside one entry: chrome-only lines between blocks.
                current.extend(block)
                continued = True
        if not continued:
            close_entry()
            first = block[0][1].strip()
            if len(block) > 1 and "." not in first and _confirms_header(block, subject):
                current, current_header = [ln for ln in block[1:]], _strip_bullet(first)
            elif bare_header:
                current, current_header = [], _strip_bullet(first)
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
    if not expected:
        # Section found but nothing countable: citation-style and uncounted
        # monographs carry no ``Name (N)`` headings, so no count can be
        # validated — entries are omitted by policy, never fabricated.
        anomalies.append(
            f"{relative}: uncounted interaction section (no Name (N) headings); entries omitted"
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


def _repeats_pair(first_line: str, entity: str | None, subject: str) -> bool:
    """Whether a block restates the open pair instead of starting a new entry.

    A repeated assertion names the open entity and leads with the monograph
    subject ("<subject> and <entity> ...") or with the entity itself
    ("<entity>,", "<entity> <verb>", "<entity> and ..."). A distinct longer
    name ("testosterone" vs "testosterone buccal system") continues with a
    plain word and stays a new entry.
    """
    if not entity:
        return False
    first = first_line.strip()
    low = first.lower()
    if entity.lower() not in low:
        return False
    if subject and low.startswith(subject):
        return True
    if not low.startswith(entity.lower()):
        return False
    if low == entity.lower():
        return True
    rest = first[len(entity) :]
    return rest.startswith((",", " and")) or bool(
        re.match(r"\s+(?:decreases|increases|will)\b", rest, re.IGNORECASE)
    )


def _confirms_header(block: list[tuple[int, str]], subject: str) -> bool:
    first = _strip_bullet(block[0][1].strip())
    second = block[1][1].strip()
    return second.lower().startswith(first.lower()) or second.lower().startswith(subject)


def _strip_bullet(text: str) -> str:
    """Remove one leading bullet marker (``•``, ``*``, ``**``), if present."""
    cleaned = text.strip()
    if cleaned.startswith("•"):
        return cleaned[1:].strip()
    if cleaned.startswith("*"):
        return cleaned.lstrip("* ").strip()
    return cleaned


def _is_bullet_line(line: str) -> bool:
    """Whether a line opens a bullet entry (``• ...`` or ``* ...``)."""
    return line.startswith("•") or line.startswith("* ")


def _is_splittable_bullet(block: list[tuple[int, str]]) -> bool:
    """Whether a bullet block splits into inline per-name entries.

    Single-line bullets split when they carry a description (``• n: ...``)
    or a bare name list (``• n1, n2, ...``); a lone ``• name`` header keeps
    the generic header/content path so it can join its content lines.
    Multi-line bullet blocks split only off an inline description line —
    a bare ``• name`` header with content lines stays one entry.
    """
    if not block or not _is_bullet_line(block[0][1]):
        return False
    if len(block) == 1:
        first = block[0][1]
        return ":" in first or "," in first
    return ":" in block[0][1]


def _split_names(head: str) -> list[str]:
    """Split a bullet name list on top-level commas only.

    Parenthesised qualifiers (``octreotide (Antidote)``, ``CYP1A2, CYP2D6``
    inside a category head) and bracketed citation markers never split;
    slash-joined formulation variants are not comma-separated and stay
    intact.
    """
    names: list[str] = []
    parens = 0
    brackets = 0
    current = ""
    for char in head:
        if char == "(":
            parens += 1
        elif char == ")":
            parens = max(0, parens - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        if char == "," and parens == 0 and brackets == 0:
            names.append(current.strip(" *."))
            current = ""
        else:
            current += char
    names.append(current.strip(" *."))
    return [name for name in names if name]


def _bullet_names(first_line: str) -> list[str]:
    """Per-name entries for one inline bullet line.

    Handles ``• n1, n2: shared description`` and markdown
    ``* **n:** desc`` forms, plus inverted mechanism bullets
    (``* **Mechanism:** drug1 [cite], drug2 [cite], ...``) where every
    listed item carries its own citation marker.
    """
    body = _strip_bullet(first_line).lstrip("*").strip()
    head, _, post = body.partition(":")
    if _is_inverted_bullet(post):
        names = [_without_cites(chunk).strip(" *.") for chunk in _split_names(post)]
        return [name for name in names if name]
    return _split_names(head)


def _is_inverted_bullet(post: str) -> bool:
    """Whether a bullet lists its names after the description head.

    True only when several citation markers are present and every
    top-level comma item ends with its own marker — ordinary descriptions
    with trailing cites never qualify.
    """
    if len(_CITE_MARKER.findall(post)) <= 1:
        return False
    chunks = _split_names(post)
    return bool(chunks) and all(chunk.rstrip().endswith("]") for chunk in chunks)


def _starts_new_entry(block: list[tuple[int, str]], subject: str = "") -> bool:
    first = block[0][1].strip()
    if "." not in first:
        if len(block) == 1:
            return _looks_like_header(first)
        return _confirms_header(block, subject)
    return _entry_name(first) is not None


def _entry_name(first_line: str) -> str | None:
    """Leading drug name of an entry-start line, else None for prose."""
    text = first_line.strip()
    match = _ENTITY_START.match(text)
    if match is None:
        return None
    name = match.group(1).strip()
    if _PROSE_WORDS.search(name):
        # Running prose ("results in small decreases ...", "not clear",
        # "levels of bupropion"), never an interacting entity.
        return None
    if not text[len(match.group(1)) :].startswith(","):
        return name
    if text.split(".", 1)[0].count(",") > 1:
        # Wrapped symptom list continuing an entry across a page break
        # ("depression, hypotension, profound sedation, coma, and/or
        # death.") rather than a "Subject, Pair." entry assertion, which
        # carries exactly one comma before its first period.
        return None
    return name


def _continues_entry(
    current: list[tuple[int, str]] | None,
    current_header: str | None,
    block: list[tuple[int, str]],
    subject: str,
) -> bool:
    if current is None:
        return False
    if current_header is not None and not current:
        # Orphan header awaiting its content across a page break.
        first = block[0][1].strip()
        return first.lower().startswith(current_header.lower()) or first.lower().startswith(subject)
    return not _starts_new_entry(block, subject)


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
    name = _entry_name(first_line)
    if name is not None:
        return name
    return first_line.strip()
