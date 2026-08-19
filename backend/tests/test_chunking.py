"""Section-aware chunking: page tracking and Item-boundary detection.

Two jobs, and they fail in different ways.

**Page tracking** must not disturb the text. The published Phase 2 numbers were
computed over exactly what `extract_text_from_html` returns, so a page-aware
extractor that "improves" the text silently makes RESULTS.md unreproducible.
The equality test below is what stops that, and it is the most important test
in this file.

**Section detection** is prose-based, which is the class of detector this
project has already been burned by once (the retired `item_8_by_reference`
flag, wrong in both directions). So the rule is grounded in what the corpus
actually contains rather than in what a 10-K is supposed to look like:

  * 5 of 6 sampled filings open with a table of contents whose Item entries
    carry no title on their own line, because the TOC is a table and the title
    is a separate cell. The body headings carry theirs inline.
  * PGR has no table of contents at all, and instead carries cross-references
    of the form "Item 1A, Risk Factors - II. Insurance Risks" at line start.
    The comma is what separates those from headings.
  * Real headings run in canonical Item order across the whole document; a TOC
    spans 1-2% of it and a cross-reference points backwards.

Written before backend/services/chunking.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
from services.chunking import (  # noqa: E402
    canonical_rank, extract_text_with_pages, find_sections, page_of_line,
)
from services.html_parser import extract_text_from_html  # noqa: E402

# --------------------------------------------------------------- fixtures

PAGE_BREAK = '<hr style="page-break-after:always"/>'


def doc(*blocks: str) -> bytes:
    return ("<html><body>" + "".join(blocks) + "</body></html>").encode("utf-8")


def para(text: str) -> str:
    return f"<p>{text}</p>"


def toc_row(item: str) -> str:
    """A table-of-contents row: the item and its title are separate cells, so
    the item number lands on a line of its own."""
    return f"<tr><td>Item {item}</td><td>Some Title</td><td>4</td></tr>"


def heading(item: str, title: str) -> str:
    return f"<p><b>Item {item}. {title}</b></p>"


BODY_ITEMS = [
    ("1", "Business"), ("1A", "Risk Factors"), ("1B", "Unresolved Staff Comments"),
    ("2", "Properties"), ("3", "Legal Proceedings"), ("5", "Market for Common Equity"),
    ("7", "Management's Discussion and Analysis"), ("7A", "Quantitative Disclosures"),
    ("8", "Financial Statements and Supplementary Data"), ("9A", "Controls and Procedures"),
    ("15", "Exhibits"),
]


def realistic_filing() -> bytes:
    """A TOC block, then the body, with filler so the body dominates the span."""
    blocks = ["<table>"] + [toc_row(i) for i, _ in BODY_ITEMS] + ["</table>"]
    for item, title in BODY_ITEMS:
        blocks.append(heading(item, title))
        blocks.extend(para(f"{title} paragraph {n}.") for n in range(12))
    return doc(*blocks)


# ------------------------------------------------------- the equality guard

class TestPageExtractionDoesNotDisturbTheText:
    def test_text_is_identical_to_the_published_extractor(self):
        """THE guard. RESULTS.md was computed over extract_text_from_html's
        output; if the page-aware path returns anything else, the published
        numbers stop being reproducible from the code that claims to produce
        them."""
        raw = realistic_filing()
        assert extract_text_with_pages(raw).text == extract_text_from_html(raw)

    @pytest.mark.parametrize("blocks", [
        (para("plain"),),
        (para("with break"), PAGE_BREAK, para("after break")),
        ("<table><tr><td>Total assets</td><td>391,035</td></tr></table>",),
        (para("nbsp\xa0separated"), PAGE_BREAK, PAGE_BREAK, para("double break")),
    ])
    def test_equality_holds_across_shapes(self, blocks):
        raw = doc(*blocks)
        assert extract_text_with_pages(raw).text == extract_text_from_html(raw)


class TestPageNumbers:
    def test_a_document_with_no_breaks_is_one_page(self):
        result = extract_text_with_pages(doc(para("only")))
        assert result.page_count == 1

    def test_each_break_starts_a_new_page(self):
        result = extract_text_with_pages(
            doc(para("one"), PAGE_BREAK, para("two"), PAGE_BREAK, para("three")))
        assert result.page_count == 3

    def test_pages_are_one_indexed_and_advance_with_the_text(self):
        result = extract_text_with_pages(
            doc(para("alpha"), PAGE_BREAK, para("bravo")))
        lines = result.text.splitlines()
        alpha = next(i for i, l in enumerate(lines) if "alpha" in l)
        bravo = next(i for i, l in enumerate(lines) if "bravo" in l)
        assert page_of_line(result, alpha) == 1
        assert page_of_line(result, bravo) == 2

    def test_page_of_line_never_returns_zero_or_beyond_the_count(self):
        result = extract_text_with_pages(
            doc(para("a"), PAGE_BREAK, para("b")))
        for line in range(len(result.text.splitlines())):
            assert 1 <= page_of_line(result, line) <= result.page_count


# ------------------------------------------------------- section detection

class TestSectionDetection:
    def sections(self, raw=None):
        """The Item sections only.

        AMENDMENT 2 also returns an item-less section for the front matter and
        one for the post-signature tail. Both are asserted in their own classes
        below; filtering them here keeps each test in this class about the thing
        it was written for.
        """
        raw = raw if raw is not None else realistic_filing()
        return [s for s in find_sections(extract_text_with_pages(raw).text) if s.item]

    def test_finds_the_body_items(self):
        found = [s.item for s in self.sections()]
        assert found == [item for item, _ in BODY_ITEMS]

    def test_titles_are_captured(self):
        by_item = {s.item: s.title for s in self.sections()}
        assert by_item["1"] == "Business"
        assert "Financial Statements" in by_item["8"]

    def test_the_table_of_contents_is_not_mistaken_for_sections(self):
        """Each Item appears twice in a real 10-K -- once in the TOC and once as
        a heading. Returning both doubles the sections and puts the first chunk
        boundary inside the contents table."""
        sections = self.sections()
        assert len(sections) == len(BODY_ITEMS)
        first = sections[0]
        # The TOC rows sit in the first handful of lines; the body starts after.
        assert first.start_line > len(BODY_ITEMS)

    def test_cross_references_are_not_headings(self):
        """PGR's shape: 'Item 1A, Risk Factors - II. Insurance Risks' occurs
        line-initial, mid-document, pointing backwards."""
        blocks = []
        for item, title in BODY_ITEMS:
            blocks.append(heading(item, title))
            blocks.append(para("filler."))
            blocks.append(para(f"Item 1A, Risk Factors - see the discussion above."))
        sections = self.sections(doc(*blocks))
        assert [s.item for s in sections] == [item for item, _ in BODY_ITEMS]

    def test_a_cross_reference_does_not_move_its_own_sections_boundary(self):
        """The case the plain list above cannot catch, and the one that would
        actually corrupt a chunk.

        A cross-reference sitting between its own heading and the next ranks
        identically to the real heading and sits later, so the tie-break that
        defeats the table of contents would happily prefer it -- moving Item
        1A's boundary past the start of its own text. Only the comma rule
        separates them. PGR is full of exactly this shape.
        """
        blocks = []
        for item, title in BODY_ITEMS:
            blocks.append(heading(item, title))
            blocks.append(para(f"{title} paragraph 0."))
            # The pointer lands inside the section it names, after the heading.
            blocks.append(para(f"Item {item}, {title} - see the discussion above."))
            blocks.extend(para(f"{title} paragraph {n}.") for n in range(1, 6))
        sections = self.sections(doc(*blocks))

        assert [s.item for s in sections] == [item for item, _ in BODY_ITEMS]
        for section in sections:
            body = "\n".join(
                self.text_of(doc(*blocks)).splitlines()[section.start_line:section.end_line])
            assert f"{section.title} paragraph 0." in body, (
                f"Item {section.item} starts after its own first paragraph, so "
                f"the boundary landed on the cross-reference")

    def text_of(self, raw):
        return extract_text_with_pages(raw).text

    def test_sections_are_returned_in_canonical_order(self):
        ranks = [canonical_rank(s.item) for s in self.sections()]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)

    def test_sections_span_to_the_next_heading(self):
        sections = self.sections()
        for earlier, later in zip(sections, sections[1:]):
            assert earlier.end_line == later.start_line
        assert sections[0].end_line > sections[0].start_line

    def test_section_text_contains_its_own_body_and_not_the_next(self):
        raw = realistic_filing()
        result = extract_text_with_pages(raw)
        sections = find_sections(result.text)
        business = next(s for s in sections if s.item == "1")
        body = "\n".join(result.text.splitlines()[business.start_line:business.end_line])
        assert "Business paragraph 0." in body
        assert "Risk Factors paragraph 0." not in body

    def test_titles_on_the_following_line_are_captured(self):
        """CHTR, HON and PG print the heading as "Item 1." with "Business" on
        the next line, where AMCR and PGR put both on one. Measured on the
        corpus; a section whose title came back empty on a third of the filings
        would be a citation with nothing to name it."""
        blocks = []
        for item, title in BODY_ITEMS:
            blocks.append(f"<p><b>Item {item}.</b></p><p><b>{title}</b></p>")
            blocks.extend(para(f"{title} paragraph {n}.") for n in range(3))
        sections = self.sections(doc(*blocks))
        assert [s.item for s in sections] == [item for item, _ in BODY_ITEMS]
        by_item = {s.item: s.title for s in sections}
        assert by_item["1"] == "Business"
        assert by_item["8"] == "Financial Statements and Supplementary Data"
        assert all(s.title for s in sections), "a heading came back untitled"

    def test_a_title_separated_by_blank_or_punctuation_lines_is_still_found(self):
        """Two layouts measured in the corpus, both of which defeat a naive
        "look at the next line": DPZ and DVN put a blank line between heading
        and title, and CTSH puts the heading's own full stop on a line by
        itself. Together these accounted for all 40 untitled sections in the
        first working version."""
        blocks = []
        for index, (item, title) in enumerate(BODY_ITEMS):
            if index % 2:
                blocks.append(f"<p><b>Item {item}</b></p><p>.</p><p><b>{title}</b></p>")
            else:
                blocks.append(f"<p><b>Item {item}.</b></p><p></p><p><b>{title}</b></p>")
            blocks.extend(para(f"{title} paragraph {n}.") for n in range(3))
        sections = self.sections(doc(*blocks))
        assert [s.item for s in sections] == [item for item, _ in BODY_ITEMS]
        assert all(s.title for s in sections), (
            f"untitled: {[s.item for s in sections if not s.title]}")
        assert {s.title for s in sections} == {title for _, title in BODY_ITEMS}

    def test_combined_headings_cover_every_item_they_name(self):
        """DVN prints "Items 1 and 2. Business and Properties" and SO prints
        "Items 10, 11, 12, and 13"; both are ordinary 10-K layouts. The section
        is Item 1, and it also answers for Item 2 -- a caller asking "does this
        filing have Item 2" must not be told no.

        The letter suffix is the trap: without a lookahead, the optional [a-c]
        takes the "a" of "and" and the heading parses as Item 1A.
        """
        blocks = ["<p><b>Items 1 and 2. Business and Properties</b></p>",
                  para("Business paragraph 0.")]
        for item, title in BODY_ITEMS[1:]:
            blocks.append(heading(item, title))
            blocks.extend(para(f"{title} paragraph {n}.") for n in range(3))
        sections = self.sections(doc(*blocks))

        first = sections[0]
        assert first.item == "1", f"combined heading parsed as Item {first.item}"
        assert first.covers == ("1", "2")
        assert "Business and Properties" in first.title
        covered = {item for section in sections for item in section.covers}
        assert "2" in covered

    def test_a_combined_heading_still_admits_the_items_that_follow_it(self):
        """DVN's real order: Items 1 and 2, then 1A, 1B, 1C. Those rank inside
        the combined range, so ordering that compared against the *last* item
        covered would reject all three and lose Item 1A entirely."""
        blocks = ["<p><b>Items 1 and 2. Business and Properties</b></p>",
                  para("filler.")]
        for item, title in [("1A", "Risk Factors"), ("1B", "Unresolved Staff Comments"),
                            ("1C", "Cybersecurity"), ("3", "Legal Proceedings"),
                            ("7", "MD&A"), ("8", "Financial Statements")]:
            blocks.append(heading(item, title))
            blocks.extend(para(f"{title} paragraph {n}.") for n in range(3))
        found = [s.item for s in self.sections(doc(*blocks))]
        assert found == ["1", "1A", "1B", "1C", "3", "7", "8"]

    def test_an_unrecognizable_document_yields_no_sections(self):
        """Better to return nothing than to invent boundaries. A caller can
        fall back; a caller cannot detect a fabricated split."""
        assert find_sections("no headings here\njust prose\n") == []

    def test_a_lone_heading_is_not_enough(self):
        """One 'Item 7' in running prose is a reference, not a document
        structure. Requiring a run is what makes this robust."""
        text = "intro\n" * 50 + "Item 7. Management's Discussion\n" + "body\n" * 50
        assert find_sections(text) == []


class TestCanonicalRank:
    def test_letter_suffixes_sort_after_their_number(self):
        assert canonical_rank("1") < canonical_rank("1A") < canonical_rank("1B")
        assert canonical_rank("1C") < canonical_rank("2")

    def test_multi_digit_items_sort_numerically_not_lexically(self):
        """'10' must not sort between '1' and '2'."""
        assert canonical_rank("9C") < canonical_rank("10")
        assert canonical_rank("2") < canonical_rank("10")

    def test_an_unknown_item_is_rejected(self):
        with pytest.raises(KeyError):
            canonical_rank("42")


# ------------------------------------------------------------ real corpus

@pytest.mark.corpus
class TestAgainstTheRealCorpus:
    """Skips cleanly when the local filings are absent (RAG_FILINGS_DIR)."""

    @pytest.fixture(scope="class")
    def filings(self):
        directory = corpus_paths.filings_dir()
        paths = sorted(directory.glob("*.htm")) if directory.exists() else []
        if not paths:
            pytest.skip(f"no local filings at {directory}")
        return paths

    def test_every_filing_has_page_breaks(self, filings):
        for path in filings:
            result = extract_text_with_pages(path.read_bytes())
            assert result.page_count > 1, f"{path.name} has no page breaks"

    def test_every_filing_yields_the_major_sections(self, filings):
        """Item 1, 1A, 7 and 8 are the sections §4 names for chunking. A filing
        missing one means the detector broke on a layout, and the whole point
        of running this over all 44 is to find that before it matters."""
        missing = {}
        for path in filings:
            text = extract_text_with_pages(path.read_bytes()).text
            found = {s.item for s in find_sections(text)}
            absent = [m for m in ("1", "1A", "7", "8") if m not in found]
            if absent:
                missing[path.name] = absent
        # HON is a measured exception, disclosed rather than tuned away. Its
        # document order is not canonical: the MD&A running headers sit on
        # printed pages 17-25 and Risk Factors on page 41, so a run in Item
        # order cannot contain both. Its Item 1 and Item 7 text is retrievable
        # and page-cited, but labelled Item 1B. Asserting the exact set rather
        # than skipping HON means a regression elsewhere still fails, and a day
        # when HON parses cleanly fails too and gets this comment deleted.
        assert missing == {
            "HON_2024-12-31.htm": ["1", "7"],
            "HON_2025-12-31.htm": ["1", "7"],
        }, f"major sections not detected: {missing}"

    def test_sections_never_overlap_and_cover_in_order(self, filings):
        for path in filings:
            sections = find_sections(extract_text_with_pages(path.read_bytes()).text)
            for earlier, later in zip(sections, sections[1:]):
                assert earlier.end_line <= later.start_line, path.name
            # AMENDMENT 2 gives the front matter and the post-signature tail an
            # empty item, so canonical ordering is asserted over the Item
            # sections; that those two sit at the ends is asserted rather than
            # skipped, since an item-less section in the middle would mean a
            # boundary was lost.
            positions = [i for i, s in enumerate(sections) if s.item]
            assert positions == list(range(positions[0], positions[-1] + 1)), path.name
            items = [sections[i] for i in positions]
            for earlier, later in zip(items, items[1:]):
                assert canonical_rank(earlier.item) < canonical_rank(later.item), path.name


# ------------------------------------------- AMENDMENT 2, 2026-08-18
#
# Section detection was repaired after the store was built and measured. The
# defect: a contents table is a complete run of Items by construction, a body
# is not, so a chain of contents-table entries stitched to the two or three
# headings printed near the end was *longer* than any body chain and won on
# length before the later-occurrence tie-break was ever consulted. Measured
# over the corpus: SO put 91% of the filing under Item 13, DVN 82% under 9C,
# and HON -- which prints Item numbers only in a cross-reference index after
# its signature block -- delivered 0.7% of its text.
#
# The rules these tests fix are written up as AMENDMENT 2 in plan §4, dated
# before this file was edited and before any index or retrieval number existed.

SIGNATURE_BLOCK = (
    para("SIGNATURES")
    + para("Pursuant to the requirements of Section 13 or 15(d) of the "
           "Securities Exchange Act of 1934, the registrant has duly caused "
           "this report to be signed on its behalf by the undersigned.")
)

# The titles Form 10-K itself specifies, which is the point: the list is the
# form's, not a particular filer's.
TITLE_ITEMS = [
    ("1", "Business"), ("1A", "Risk Factors"),
    ("1B", "Unresolved Staff Comments"), ("2", "Properties"),
    ("3", "Legal Proceedings"), ("9A", "Controls and Procedures"),
]


def titled_heading(title: str) -> str:
    """A heading printed as the bare Form 10-K title -- HON's shape."""
    return f"<p><b>{title}</b></p>"


def filler(title: str, count: int = 12) -> list[str]:
    return [para(f"{title} paragraph {n}.") for n in range(count)]


def spans(sections) -> list[int]:
    return [s.end_line - s.start_line for s in sections]


class TestABareTitleIsAHeading:
    def test_a_filing_that_prints_only_titles_is_still_sectioned(self):
        """HON marks its body with the title alone and prints `Item N` only in
        a trailing index. Before the repair it delivered 83 of 11,907 lines."""
        blocks = []
        for _item, title in TITLE_ITEMS:
            blocks.append(titled_heading(title))
            blocks.extend(filler(title))
        sections = find_sections(extract_text_with_pages(doc(*blocks)).text)
        assert [s.item for s in sections] == [i for i, _ in TITLE_ITEMS]

    def test_a_title_inside_a_sentence_is_not_a_heading(self):
        """`Properties` is a heading on its own line and a noun in a sentence,
        and only the first is a section boundary."""
        blocks = []
        for _item, title in TITLE_ITEMS:
            blocks.append(titled_heading(title))
            blocks.append(para(f"Our {title} are described below in detail."))
            blocks.extend(filler(title))
        sections = find_sections(extract_text_with_pages(doc(*blocks)).text)
        assert [s.item for s in sections] == [i for i, _ in TITLE_ITEMS]

    def test_numbered_headings_still_win_where_a_filing_prints_them(self):
        """A regression guard: the 38 filings that were already right must not
        be re-cut by the new candidate source."""
        text = extract_text_with_pages(realistic_filing()).text
        sections = [s for s in find_sections(text) if s.item]
        assert [s.item for s in sections] == [i for i, _ in BODY_ITEMS]


class TestTheContentsTableCannotWin:
    def contents_table_filing(self) -> bytes:
        """SO's shape: a complete contents table, a body that prints fewer
        headings than the table does, and a heading near the end.

        Under the superseded rule the chain was the table plus that trailing
        heading -- longer than the body chain, so it won -- and the table's
        last entry swallowed everything between.
        """
        table = ["<table>"] + [toc_row(i) for i in
                               ("1", "1A", "1B", "2", "3", "5", "7", "7A", "8", "9A")
                               ] + ["</table>"]
        body = []
        for item, title in (("1", "Business"), ("1A", "Risk Factors"),
                            ("2", "Properties"), ("3", "Legal Proceedings"),
                            ("7", "Discussion"), ("8", "Financial Statements")):
            body.append(heading(item, title))
            body.extend(filler(title, 20))
        tail = [heading("15", "Exhibits")] + filler("Exhibits")
        return doc(*(table + body + tail))

    def test_the_body_is_chosen_over_the_longer_contents_table(self):
        sections = [s for s in find_sections(
            extract_text_with_pages(self.contents_table_filing()).text) if s.item]
        assert [s.item for s in sections] == ["1", "1A", "2", "3", "7", "8", "15"]

    def test_no_section_swallows_the_document(self):
        text = extract_text_with_pages(self.contents_table_filing()).text
        sections = find_sections(text)
        assert max(spans(sections)) < len(text.splitlines()) / 2


class TestTheSignatureBlock:
    def signed_filing(self, *tail: str) -> bytes:
        blocks = []
        for item, title in BODY_ITEMS:
            blocks.append(heading(item, title))
            blocks.extend(filler(title))
        return doc(*(blocks + [SIGNATURE_BLOCK] + list(tail)))

    def test_the_last_item_section_ends_at_the_signature_block(self):
        text = extract_text_with_pages(self.signed_filing()).text
        lines = text.splitlines()
        signature = next(i for i, line in enumerate(lines)
                         if line.strip().upper() == "SIGNATURES")
        items = [s for s in find_sections(text) if s.item]
        assert items[-1].end_line == signature

    def test_the_tail_is_a_section_with_no_item(self):
        """For CHTR, CTSH, DGX, QCOM and VICI the post-signature tail is the
        financial statements -- the auditor's report is inside it -- so it has
        to be chunked. Labelling it `Item 8` was declined as an inference; an
        empty label costs Item filtering, a wrong one costs the citation."""
        statements = [para("Report of Independent Registered Public Accounting Firm")]
        statements.extend(filler("Consolidated balance sheet", 30))
        text = extract_text_with_pages(self.signed_filing(*statements)).text
        sections = find_sections(text)
        assert sections[-1].item == ""
        body = "\n".join(text.splitlines()[sections[-1].start_line:sections[-1].end_line])
        assert "Report of Independent Registered" in body

    def test_item_headings_after_the_signature_block_are_not_sections(self):
        """HON's cross-reference index sits after its signatures, and every one
        of its 23 `Item N` lines is in it."""
        index = ["<table>"] + [toc_row(i) for i, _ in BODY_ITEMS] + ["</table>"]
        sections = find_sections(
            extract_text_with_pages(self.signed_filing(*index)).text)
        assert [s.item for s in sections if s.item] == [i for i, _ in BODY_ITEMS]
        assert sections[-1].item == ""

    def test_a_filing_with_no_signature_block_grows_no_tail(self):
        """The front matter still gets its own item-less section -- the two
        rules are separate, and only the tail one depends on the signatures."""
        sections = find_sections(extract_text_with_pages(realistic_filing()).text)
        assert sections[-1].item != ""
        assert sections[0].item == "" and sections[0].start_line == 0


class TestTitleFragmentsAndRunningHeaders:
    """The two rules the corpus caught and the synthetic tests first did not.

    Both were found by perturbation: removing either left every fast test
    green while the corpus tests failed, which makes the fast suite a poor
    guard for exactly the rules most likely to be "simplified" later.
    """

    def body(self, *extra: str) -> bytes:
        blocks = []
        for item, title in BODY_ITEMS:
            blocks.append(heading(item, title))
            blocks.extend(filler(title))
            blocks.extend(extra)
        return doc(*blocks)

    def test_a_short_title_prefix_is_not_a_heading(self):
        """`Changes in` opens Item 9's title and opens ordinary sentences too.

        Admitting two-word prefixes made it an Item 9 heading inside HON's
        financial statements, where it took 4,609 lines -- the statements
        themselves -- out of Item 8 and filed them under "Changes in".
        """
        sections = [s for s in find_sections(
            extract_text_with_pages(self.body(para("Changes in"))).text) if s.item]
        assert [s.item for s in sections] == [item for item, _ in BODY_ITEMS]

    def test_a_section_starts_at_the_first_of_its_running_headers(self):
        """HON repeats each section's title as a page header -- "FINANCIAL
        STATEMENTS AND SUPPLEMENTARY DATA" on some eighty consecutive pages.
        Preferring later occurrences started Item 8 at printed page 122 instead
        of 57, putting sixty-five pages of statements under the previous Item.
        """
        blocks = []
        for _item, title in TITLE_ITEMS[:5]:
            blocks.append(titled_heading(title))
            blocks.extend(filler(title))
        # The section, printed with its title repeated at the top of each page.
        for page in range(6):
            blocks.append(titled_heading("Financial Statements and Supplementary Data"))
            blocks.extend(filler(f"statements page {page}", 8))
        blocks += [titled_heading("Controls and Procedures")] + filler("Controls")
        text = extract_text_with_pages(doc(*blocks)).text
        sections = {s.item: s for s in find_sections(text) if s.item}
        assert "8" in sections
        lines = text.splitlines()
        repeats = [i for i, line in enumerate(lines)
                   if line.strip() == "Financial Statements and Supplementary Data"]
        assert len(repeats) == 6, "fixture should repeat the header"
        assert sections["8"].start_line == repeats[0]


@pytest.mark.corpus
class TestTheRepairAgainstTheWholeCorpus:
    """The acceptance criteria fixed in AMENDMENT 2 before the repair was
    written. They are permanent tests rather than a one-off measurement because
    the defect they describe was invisible to every test that came before."""

    @pytest.fixture(scope="class")
    def filings(self):
        directory = corpus_paths.filings_dir()
        paths = sorted(directory.glob("*.htm")) if directory.exists() else []
        if not paths:
            pytest.skip("no local filings at " + str(directory))
        return paths

    def test_no_section_but_item_8_holds_more_than_half_of_its_filing(self, filings):
        """The swallow signature, and the criterion AMENDMENT 2 had to correct.

        It was first written as "no section holds more than half", which is not
        what a 10-K looks like: measured after the repair, Item 8 exceeds half
        in 32 of 44 filings and the item-less tail in 10, both because the
        financial statements really are most of the document. What must never
        recur is a *Part III* item holding the bulk -- SO put 91% under Item 13
        and DVN 82% under Item 9C, and each of those chunks cited a section its
        text was not in.
        """
        offenders = []
        for path in filings:
            text = extract_text_with_pages(path.read_bytes()).text
            total = len(text.splitlines())
            for section in find_sections(text):
                if not section.item or section.item == "8":
                    continue
                if section.end_line - section.start_line > total / 2:
                    offenders.append(f"{path.stem} Item {section.item}")
        assert offenders == []

    def test_every_filing_keeps_at_least_ninety_percent_of_its_text(self, filings):
        thin = []
        for path in filings:
            text = extract_text_with_pages(path.read_bytes()).text
            lines = text.splitlines()
            total = len([l for l in lines if l.strip()])
            covered = set()
            for section in find_sections(text):
                covered.update(range(section.start_line, section.end_line))
            kept = len([i for i in covered if i < len(lines) and lines[i].strip()])
            if total and kept / total < 0.90:
                thin.append(f"{path.stem} {kept}/{total}")
        assert thin == []
