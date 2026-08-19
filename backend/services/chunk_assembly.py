"""Sections into retrieval-sized chunks, under pre-registered parameters.

`services/chunking.py` finds where the Items are; this turns those sections into
passages a retriever can rank and a QA layer can cite. It is a separate module
because the two answer different questions and fail differently: section
detection is a claim about the document, chunk assembly is a set of parameters
that move recall@k.

Those parameters are **pre-registered** -- fixed and dated 2026-08-18 in
`EVALUATION-SPEC.md` and plan §4, before any chunk, index, or retrieval number
existed. The reason is the one §5 gives for the matching spec: a chunk size
chosen after seeing recall@k is a dial, and nothing in a published number would
reveal that it had been turned. Do not change a constant here to improve a
result; changing one after results exist invalidates the result.

The sizes exist because sections are not passages, and the corpus is emphatic
about it: 1,000 sections, median 117 tokens, p75 1,032, p95 22,154, max 238,240,
with Item 8 alone at a median of 25,510. Both ends have to come out sensible --
"Item 4. Mine Safety Disclosures / None." is one chunk, and Item 8 is many.
"""

import hashlib
from dataclasses import dataclass
from typing import Callable

import tiktoken

from services.chunking import find_sections, extract_text_with_pages, page_of_line

# Pre-registered 2026-08-18. See the module docstring before touching either.
TARGET_TOKENS = 512
OVERLAP_TOKENS = 64

# The encoding the extraction model uses. Counting tokens with anything else --
# characters, words, a different encoding -- would make "512 tokens" mean
# something other than what the retriever and the QA layer mean by it.
_ENCODING = "o200k_base"

# Cost of the newline that joins two blocks inside one chunk.
_JOIN_TOKENS = 1

_encoder = None


def _encoding():
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding(_ENCODING)
    return _encoder


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_encoding().encode(text))


@dataclass(frozen=True)
class Chunk:
    """One retrieval unit, and everything a citation needs to name it."""

    chunk_id: str
    accession: str
    ticker: str
    period: str
    item: str
    title: str
    index: int
    first_page: int
    last_page: int
    text: str


def _blocks(text: str) -> list[tuple[int, str]]:
    """(line offset within the section, block text), blank lines dropped.

    The atomic unit is a **line**, and that is a measurement rather than a
    preference. In extracted filing text one line is one block element: a
    heading, a paragraph, or a table cell. Splitting on blank lines was tried
    first and is wrong -- AMCR's Item 1 runs heading, paragraph, heading down
    consecutive lines with no blank between them, so a blank-line rule returned
    whole sections as single blocks and chunking silently stopped happening.
    The corpus is emphatic about the shape: the median non-blank line is 3
    characters, because most lines are table cells, while prose lines reach
    2,198.

    The line offset is kept so a chunk can report the pages its text actually
    falls on rather than the page its section started on.
    """
    return [(offset, line.strip())
            for offset, line in enumerate(text.splitlines())
            if line.strip()]


def assemble_chunks(
    text: str,
    section,
    page_of: Callable[[int], int],
    accession: str = "",
    ticker: str = "",
    period: str = "",
    start_index: int = 0,
) -> list[Chunk]:
    """Split one section's text into chunks.

    Whole blocks only (rule 2): a block longer than the target becomes its own
    oversized chunk rather than being cut, because handing the QA layer half a
    sentence produces a citation it cannot quote. A block is one line of
    extracted text -- see _blocks for why that is the unit.
    """
    blocks = _blocks(text)
    if not blocks:
        return []

    sized = [(offset, block, count_tokens(block)) for offset, block in blocks]

    # Fill a group to the target, emit it, then step back over the trailing
    # blocks that form the overlap and continue from there.
    #
    # Two invariants make this readable, and both are tested. A group never
    # exceeds the target unless a single block does on its own (rule 2 forbids
    # cutting one). And the step-back always moves at least one block whenever
    # the group holds more than one, so overlap is never silently zero -- which
    # it was in the first version, because a prose block runs 80-plus tokens
    # against a 64-token budget and no whole block ever fit. Blocks are atomic,
    # so overlap is quantised to them; the budget decides how many additional
    # blocks come back, not whether any do. Clarified and dated 2026-08-18 in
    # the pre-registration, before any retrieval number existed.
    groups: list[list[tuple[int, str, int]]] = []
    position = 0
    while position < len(sized):
        group: list[tuple[int, str, int]] = []
        total = 0
        while position < len(sized):
            # The newline that joins this block to the previous one is a token
            # too. Budgeting on block sums alone understates a chunk built from
            # many small blocks: Item 8 chunks run to 229 table-cell blocks, so
            # the separators alone were pushing a 490-token budget to 619 actual.
            tokens = sized[position][2] + (_JOIN_TOKENS if group else 0)
            if group and total + tokens > TARGET_TOKENS:
                break
            group.append(sized[position])
            total += tokens
            position += 1
        groups.append(group)

        if position >= len(sized):
            break

        step_back = 0
        carried_tokens = 0
        while step_back < len(group) - 1:
            tokens = group[-(step_back + 1)][2]
            if step_back and carried_tokens + tokens > OVERLAP_TOKENS:
                break
            carried_tokens += tokens
            step_back += 1
            if carried_tokens >= OVERLAP_TOKENS:
                break
        position -= step_back

    chunks = []
    for offset_index, group in enumerate(groups):
        body = "\n".join(block for _offset, block, _tokens in group)
        first_line = section.start_line + group[0][0]
        last_line = section.start_line + group[-1][0]
        identity = f"{accession}|{section.item}|{start_index + offset_index}"
        chunks.append(Chunk(
            chunk_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
            accession=accession,
            ticker=ticker,
            period=period,
            item=section.item,
            title=section.title,
            index=start_index + offset_index,
            first_page=page_of(first_line),
            last_page=max(page_of(first_line), page_of(last_line)),
            text=body,
        ))
    return chunks


def chunk_filing(file_bytes: bytes, accession: str, ticker: str,
                 period: str) -> list[Chunk]:
    """Every chunk in one filing, in document order.

    A chunk never spans two Items (rule 3): sections are assembled
    independently, so a boundary is a boundary. When no sections are detected the
    whole document is chunked with an empty item -- the honest failure mode,
    since text with no section label is still retrievable while a fabricated
    label would make its citation false.
    """
    parsed = extract_text_with_pages(file_bytes)
    lines = parsed.text.splitlines()
    sections = find_sections(parsed.text)

    def page_of(line: int) -> int:
        return page_of_line(parsed, min(max(line, 0), max(len(lines) - 1, 0)))

    if not sections:
        whole = _WholeDocument(len(lines))
        return assemble_chunks(parsed.text, whole, page_of, accession, ticker, period)

    chunks: list[Chunk] = []
    for section in sections:
        body = "\n".join(lines[section.start_line:section.end_line])
        chunks.extend(assemble_chunks(
            body, section, page_of, accession, ticker, period,
            start_index=len(chunks),
        ))
    return chunks


@dataclass(frozen=True)
class _WholeDocument:
    """Stands in for a section when none were detected."""

    end_line: int
    item: str = ""
    title: str = ""
    start_line: int = 0
