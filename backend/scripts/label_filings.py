"""Hand-label the eval corpus, one (filing, field) at a time.

    python scripts/label_filings.py

Walks 351 instances in queue order, shows candidate passages from the filing,
and records one JSONL line per label to corpus/labels.jsonl. Resumable per
field -- quit any time, run it again, it picks up at the next unlabeled
instance.

This tool cannot read model output. It does not import the extraction run or
the OpenAI client, no string in it names the predictions file, and a test fails
if a labeling session opens that path. That is the property the entire
measurement rests on: labels produced by someone who has seen the model's
answers score better and are indistinguishable afterward from real ground truth.

Protocol (HYBRID-RETRIEVAL-SEC-PLAN.md §5, pre-registered):
  * `value` and `stated_none` require a locator anchor -- pick a candidate or
    search, so the label points at text in the filing.
  * `not_addressed` requires the search terms tried, so the negative is the
    result of looking rather than of nothing coming to mind.

The console is cp1252 on this machine and 10-Ks contain characters it cannot
encode, so passages are transliterated before printing.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpus_paths  # noqa: E402

from evaluation.labeling import (  # noqa: E402
    build_queue, candidate_passages, completed_keys, label_record, validate_label,
)
from services.html_parser import extract_text_from_html  # noqa: E402

HELP = """
  <n>          use candidate n as the locator anchor
  /text        search the filing for text
  v <value>    record a value      (needs an anchor)
  z            stated as none/zero (needs an anchor)
  x a, b, c    not addressed; list the terms you searched
  a <note>     mark ambiguous, with a note
  s <reason>   skip this instance
  ?            show this help
  q            save and quit
"""


def show(text: str) -> None:
    """Print through the console encoding rather than crashing on it."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, "replace").decode(encoding))


def render_candidates(hits: list[dict]) -> None:
    if not hits:
        show("    (no candidate passages -- search with /text, or this may be "
             "genuinely absent)")
        return
    for i, hit in enumerate(hits, start=1):
        snippet = " ".join(hit["snippet"].split())
        show(f"  {i:>2}) @{hit['offset']:,}  [{hit['matched']}]")
        show(f"      {snippet[:300]}")


def label_one(item: dict, text: str, position: str) -> dict | None:
    """Run the prompt loop for one instance. Returns a record, or None to quit."""
    hits = candidate_passages(text, item["field"])
    show(f"\n{'=' * 78}")
    show(f"{position}  {item['ticker']}  {item['period']}  ->  {item['field']}")
    show(f"{'=' * 78}")
    render_candidates(hits)

    locator: dict = {}
    ambiguous, note = False, ""

    while True:
        anchored = f"anchor={locator.get('anchor')!r}" if locator.get("anchor") else "no anchor"
        try:
            raw = input(f"  [{item['field']} | {anchored}] > ").strip()
        except EOFError:
            return None
        if not raw:
            continue

        command, _, rest = raw.partition(" ")
        rest = rest.strip()

        if command == "q":
            return None
        if command == "?":
            show(HELP)
            continue
        if command.isdigit():
            index = int(command) - 1
            if not 0 <= index < len(hits):
                show(f"    no candidate {command}")
                continue
            hit = hits[index]
            locator = {"section": f"offset {hit['offset']}", "anchor": hit["matched"]}
            show(f"    anchor set: {hit['matched']!r}")
            continue
        if command.startswith("/"):
            query = raw[1:].strip()
            hits = [h for h in candidate_passages(text, item["field"], limit=99)
                    if query.lower() in h["snippet"].lower()] or _free_search(text, query)
            render_candidates(hits)
            continue
        if command == "a":
            ambiguous, note = True, rest
            show(f"    marked ambiguous: {rest!r}")
            continue

        try:
            if command == "v":
                if not rest:
                    show("    v needs a value")
                    continue
                record = label_record(item, "value", value=_coerce(rest),
                                      locator=locator, ambiguous=ambiguous, note=note)
            elif command == "z":
                record = label_record(item, "stated_none", locator=locator,
                                      ambiguous=ambiguous, note=note)
            elif command == "x":
                terms = [t.strip() for t in rest.split(",") if t.strip()]
                record = label_record(item, "not_addressed",
                                      locator={"section": "", "anchor": "",
                                               "searched": terms},
                                      ambiguous=ambiguous, note=note)
            elif command == "s":
                return label_record(item, "value", locator=locator, note=rest,
                                    status="skipped")
            else:
                show(f"    unknown command {command!r} -- ? for help")
                continue
            return validate_label(record)
        except ValueError as exc:
            show(f"    REJECTED: {exc}")


def _free_search(text: str, query: str) -> list[dict]:
    """Literal search, for when the field patterns miss the real passage."""
    hits, start = [], 0
    lowered, needle = text.lower(), query.lower()
    while len(hits) < 8:
        found = lowered.find(needle, start)
        if found == -1:
            break
        hits.append({"offset": found, "matched": query,
                     "snippet": text[max(0, found - 80):found + 320]})
        start = found + len(needle)
    return hits


def _coerce(raw: str):
    """Numbers as numbers, everything else as the string it was typed as."""
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned) if "." in cleaned else int(cleaned)
    except ValueError:
        return raw.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path,
                        default=pathlib.Path("corpus/manifest.json"))
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=corpus_paths.filings_dir())
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path("corpus/labels.jsonl"))
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    queue = build_queue(manifest)

    done = set()
    if args.out.exists():
        done = completed_keys(args.out.read_text(encoding="utf-8").splitlines())

    pending = [i for i in queue if (i["accession"], i["field"]) not in done]
    show(f"{len(queue)} instances, {len(done)} labeled, {len(pending)} remaining")
    show("? for help at any prompt, q to save and quit")

    text_cache: dict[str, str] = {}
    written = 0

    for item in pending:
        if item["accession"] not in text_cache:
            document = args.filings_dir / f"{item['ticker']}_{item['period']}.htm"
            if not document.exists():
                show(f"  !! {document} missing; skipping filing")
                continue
            text_cache.clear()   # one filing at a time; 10-Ks are large
            text_cache[item["accession"]] = extract_text_from_html(
                document.read_bytes())

        position = f"[{len(done) + written + 1:>3}/{len(queue)}]"
        record = label_one(item, text_cache[item["accession"]], position)
        if record is None:
            break

        with args.out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        written += 1

    show(f"\n{written} labeled this session, "
         f"{len(done) + written}/{len(queue)} total -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
