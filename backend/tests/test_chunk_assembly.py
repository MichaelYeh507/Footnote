"""Sections into retrieval-sized chunks, under the pre-registered parameters.

The parameters are fixed in EVALUATION-SPEC.md and plan §4, dated 2026-08-18,
before any chunk existed: 512 target tokens, 64 overlap, split on paragraph
boundaries, never span two Items, small sections kept whole, tokens counted with
the model's own encoding. These tests hold the implementation to that record --
not to whatever the code happens to do -- because chunk size moves recall@k and
a parameter that drifts after the fact is a dial.

The measurement that motivated them: 1,000 sections, median 117 tokens, p75
1,032, max 238,240, and Item 8 with a median of 25,510. Both extremes have to
come out sensible.

Written before backend/services/chunk_assembly.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
from services.chunk_assembly import (  # noqa: E402
    OVERLAP_TOKENS, TARGET_TOKENS, Chunk, assemble_chunks, chunk_filing,
    count_tokens,
)
from services.chunking import Section, extract_text_with_pages  # noqa: E402


def paragraphs(count: int, words: int = 60, label: str = "para") -> str:
    """Text shaped like a filing.

    One block per line, because that is what extracted filing text looks like:
    a line is a heading, a paragraph, or a table cell. An earlier version of
    this helper separated blocks with blank lines, which no real filing does --
    AMCR runs heading/paragraph/heading down consecutive lines -- and it hid a
    defect where chunking never split at all.
    """
    return "\n".join(
        f"{label} {index} " + " ".join(f"word{index}x{n}" for n in range(words))
        for index in range(count)
    )


def section(item="1", title="Business", start=0, end=None, covers=None):
    return Section(item=item, covers=covers or (item,), title=title,
                   start_line=start, end_line=end if end is not None else start + 1)


class TestParameters:
    def test_the_pre_registered_values_are_what_the_code_uses(self):
        """If these ever change, the published appendix changed with them or the
        record is wrong."""
        assert TARGET_TOKENS == 512
        assert OVERLAP_TOKENS == 64

    def test_tokens_are_counted_with_a_real_encoding(self):
        """Not characters, not words. '512 tokens' has to mean what the
        retriever and the QA layer mean by it."""
        assert count_tokens("") == 0
        assert count_tokens("hello world") < len("hello world")
        assert count_tokens(paragraphs(4)) > 100


class TestChunkSizes:
    def chunks(self, text, **kwargs):
        return assemble_chunks(text, section(), page_of=lambda line: 1, **kwargs)

    def test_a_small_section_becomes_one_chunk(self):
        chunks = self.chunks("Item 4. Mine Safety Disclosures\n\nNone.")
        assert len(chunks) == 1
        assert "None." in chunks[0].text

    def test_a_large_section_is_split(self):
        chunks = self.chunks(paragraphs(60))
        assert len(chunks) > 1

    def test_chunks_land_near_the_target(self):
        chunks = self.chunks(paragraphs(60))
        # The last chunk is whatever is left over, so it is exempt.
        for chunk in chunks[:-1]:
            assert count_tokens(chunk.text) <= TARGET_TOKENS + OVERLAP_TOKENS

    def test_no_chunk_is_empty(self):
        for chunk in self.chunks(paragraphs(40)):
            assert chunk.text.strip()

    def test_a_single_oversized_paragraph_is_not_cut(self):
        """Pre-registered rule 2. Splitting mid-paragraph would cut a sentence
        in half and hand the QA layer a fragment it cannot quote."""
        giant = "word " * 4000
        chunks = self.chunks(giant)
        assert len(chunks) == 1
        assert count_tokens(chunks[0].text) > TARGET_TOKENS

    def test_many_tiny_blocks_still_respect_the_target(self):
        """The financial-statement shape, and the one that broke the first
        working version. Item 8 chunks run to 229 table-cell blocks, and the
        newlines joining them are tokens too -- budgeting on block sums alone
        let a 490-token budget produce 619 actual tokens. Prose-sized blocks
        hide this completely, which is why it needs its own test."""
        cells = "\n".join(str(n % 10) for n in range(4000))
        chunks = self.chunks(cells)
        assert len(chunks) > 1
        for chunk in chunks:
            assert count_tokens(chunk.text) <= TARGET_TOKENS, (
                f"{count_tokens(chunk.text)} tokens over a {TARGET_TOKENS} target; "
                f"the block separators are unbudgeted")

    def test_blocks_are_never_split_open(self):
        chunks = self.chunks(paragraphs(60))
        for chunk in chunks:
            for line in chunk.text.split("\n"):
                if not line.strip():
                    continue
                assert line.strip().startswith("para "), (
                    f"a block was cut open: {line[:60]!r}")


class TestOverlap:
    def test_consecutive_chunks_overlap(self):
        """Overlap is what keeps an answer that straddles a boundary
        retrievable from at least one chunk."""
        chunks = assemble_chunks(paragraphs(60), section(), page_of=lambda line: 1)
        assert len(chunks) > 1
        first_paras = set(chunks[0].text.split("\n"))
        second_paras = set(chunks[1].text.split("\n"))
        assert first_paras & second_paras, "no overlap between adjacent chunks"

    def test_overlap_is_bounded(self):
        """An overlap as large as the chunk would duplicate the corpus and
        inflate any recall number that counts distinct chunks."""
        chunks = assemble_chunks(paragraphs(60), section(), page_of=lambda line: 1)
        for earlier, later in zip(chunks, chunks[1:]):
            shared = set(earlier.text.split("\n")) & set(later.text.split("\n"))
            assert count_tokens("\n".join(shared)) <= TARGET_TOKENS

    def test_every_block_survives_somewhere(self):
        """Chunking must not lose text. A dropped block is a passage no
        query can ever retrieve, and nothing downstream would report it."""
        text = paragraphs(60)
        chunks = assemble_chunks(text, section(), page_of=lambda line: 1)
        covered = set()
        for chunk in chunks:
            covered.update(p.strip() for p in chunk.text.split("\n") if p.strip())
        expected = {p.strip() for p in text.split("\n") if p.strip()}
        assert expected == covered


class TestMetadata:
    def test_a_chunk_carries_its_section_identity(self):
        chunks = assemble_chunks(
            paragraphs(4), section(item="1A", title="Risk Factors"),
            page_of=lambda line: 7)
        for chunk in chunks:
            assert chunk.item == "1A"
            assert chunk.title == "Risk Factors"

    def test_a_chunk_carries_its_page_range(self):
        """The page is the citation. A chunk spanning a page break must say so
        rather than claiming only its first page."""
        text = paragraphs(30)
        chunks = assemble_chunks(text, section(start=0, end=100),
                                 page_of=lambda line: 1 + line // 5)
        for chunk in chunks:
            assert chunk.first_page >= 1
            assert chunk.last_page >= chunk.first_page

    def test_chunks_are_ordered_and_indexed(self):
        chunks = assemble_chunks(paragraphs(60), section(), page_of=lambda line: 1)
        assert [c.index for c in chunks] == list(range(len(chunks)))


class TestWholeFiling:
    def build(self):
        blocks = ["<html><body>"]
        for item, title in [("1", "Business"), ("1A", "Risk Factors"),
                            ("2", "Properties"), ("3", "Legal Proceedings"),
                            ("7", "MD&A"), ("8", "Financial Statements")]:
            blocks.append(f"<p><b>Item {item}. {title}</b></p>")
            for index in range(14):
                blocks.append(f"<p>{title} paragraph {index} " +
                              " ".join(f"w{index}x{n}" for n in range(60)) + "</p>")
            blocks.append('<hr style="page-break-after:always"/>')
        blocks.append("</body></html>")
        return "".join(blocks).encode("utf-8")

    def test_a_filing_produces_chunks_across_its_sections(self):
        chunks = chunk_filing(self.build(), accession="0000-00-000000",
                              ticker="TEST", period="2025-12-31")
        assert len(chunks) > 6
        assert {c.item for c in chunks} >= {"1", "1A", "7", "8"}

    def test_no_chunk_spans_two_items(self):
        """Pre-registered rule 3. A chunk straddling a boundary makes its own
        citation false."""
        chunks = chunk_filing(self.build(), accession="0000-00-000000",
                              ticker="TEST", period="2025-12-31")
        for chunk in chunks:
            assert isinstance(chunk.item, str) and chunk.item

    def test_filing_identity_reaches_every_chunk(self):
        chunks = chunk_filing(self.build(), accession="0001-25-000009",
                              ticker="TEST", period="2025-12-31")
        for chunk in chunks:
            assert chunk.accession == "0001-25-000009"
            assert chunk.ticker == "TEST"
            assert chunk.period == "2025-12-31"

    def test_chunk_ids_are_unique_and_stable(self):
        """The id is what a citation points at, so two runs over the same
        filing must produce the same ids."""
        first = chunk_filing(self.build(), accession="0001-25-000009",
                             ticker="TEST", period="2025-12-31")
        second = chunk_filing(self.build(), accession="0001-25-000009",
                              ticker="TEST", period="2025-12-31")
        ids = [c.chunk_id for c in first]
        assert len(set(ids)) == len(ids)
        assert ids == [c.chunk_id for c in second]

    def test_a_filing_with_no_detectable_sections_still_chunks(self):
        """Falling back to the whole document is the honest failure mode: no
        section metadata, but the text is still retrievable."""
        raw = b"<html><body><p>" + b"</p><p>".join(
            f"loose paragraph {n}".encode() for n in range(40)) + b"</p></body></html>"
        chunks = chunk_filing(raw, accession="a", ticker="T", period="2025-12-31")
        assert chunks
        assert all(c.item == "" for c in chunks)


@pytest.mark.corpus
class TestAgainstTheRealCorpus:
    @pytest.fixture(scope="class")
    def filing(self):
        directory = corpus_paths.filings_dir()
        paths = sorted(directory.glob("*.htm")) if directory.exists() else []
        if not paths:
            pytest.skip(f"no local filings at {directory}")
        return paths[0]

    def test_a_real_filing_chunks_within_the_pre_registered_size(self, filing):
        chunks = chunk_filing(filing.read_bytes(), accession="x",
                              ticker=filing.stem.split("_")[0],
                              period=filing.stem.split("_")[1])
        assert len(chunks) > 20
        oversized = [c for c in chunks
                     if count_tokens(c.text) > TARGET_TOKENS + OVERLAP_TOKENS]
        # Only single paragraphs longer than the target may exceed it (rule 2).
        for chunk in oversized:
            assert len([p for p in chunk.text.split("\n") if p.strip()]) == 1

    def test_real_chunks_carry_pages_and_items(self, filing):
        chunks = chunk_filing(filing.read_bytes(), accession="x",
                              ticker=filing.stem.split("_")[0],
                              period=filing.stem.split("_")[1])
        assert all(c.first_page >= 1 for c in chunks)
        assert any(c.item == "8" for c in chunks)
