"""Local review app for the 65-query retrieval set -- approve or reject each.

    cd backend
    .\\venv\\Scripts\\python.exe scripts/review_queries.py
    then open http://127.0.0.1:8766

The one judgement no check can make. `evaluation/query_set.py` enforces the
strata, the schema and the conceptual rule; `evaluation/retrieval_gold.py`
enforces that a span exists, is unique enough and is not boilerplate. None of
them can tell whether a gold span actually *answers* its query -- q013 passed
every one of those checks while answering a question about Sadara that had
been asked about EQUATE. That is what this tool is for.

Decisions are DATA and land beside the query set, outside the repo, under the
same rule that put the query set there: gold spans are verbatim filing text.

**Each decision records the sha256 of the query it was cast against**, so
`scripts/freeze_queries.py` can check that an approval still belongs to the
text on disk rather than merely existing. The first 65 decisions here were
written before that field did, which is why the freeze needs an attestation to
cover them and mechanical verification for everything after.

Binds 127.0.0.1 only, like scripts/label_server.py. Port 8766 rather than that
tool's 8765, so an orphaned labeling server cannot silently take the socket.
"""

import argparse
import html
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import corpus_paths  # noqa: E402
import services.chunk_store as chunk_store  # noqa: E402
from evaluation import query_freeze  # noqa: E402
from evaluation import retrieval_gold as gold  # noqa: E402

VERDICTS = ("approved", "rejected")


def queries_path() -> pathlib.Path:
    return corpus_paths.queries_dir() / "queries.jsonl"


def decisions_path() -> pathlib.Path:
    return corpus_paths.queries_dir() / "review-decisions.jsonl"


def context_path() -> pathlib.Path:
    return corpus_paths.queries_dir() / "review-context.json"


def read_queries(path: pathlib.Path | None = None) -> list[dict]:
    path = queries_path() if path is None else path
    if not path.exists():
        raise FileNotFoundError(
            f"no query set at {path}. Expected queries.jsonl beside the filings."
        )
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_decisions(path: pathlib.Path | None = None) -> dict:
    """query_id -> the latest decision for it. Last write wins.

    The file is append-only and collapsed here on read, rather than rewritten
    in place. A crash or a half-written line then costs at most the decision
    being made, never the ones already made -- and re-reviewing 65 gold spans
    by hand is the expensive thing this tool exists to avoid repeating.
    """
    path = decisions_path() if path is None else path
    if not path.exists():
        return {}
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            latest[record["query_id"]] = record
    return latest


def write_decision(query, verdict, note="", path=None) -> dict:
    """Append one decision, bound to the text it was cast against.

    Takes the **whole query record**, never a `query_id`. A verdict is a
    judgement about text, and the first 65 decisions in this log recorded only
    an id -- so when q009 and q030 were edited afterwards their stored
    `approved` went stale silently, and only a person remembering caught it.
    Passing the record makes that binding impossible to forget: the hash is
    computed here, over the same bytes `scripts/freeze_queries.py` hashes.

    Also refuses a verdict outside VERDICTS, because the verdict is what the
    freeze reads: a typo silently stored would leave a query neither approved
    nor rejected while looking decided.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    # Hash before reading any field, so that a caller passing a bare id gets
    # `query_sha256`'s explanation rather than "string indices must be
    # integers" -- the message has to name the mistake, since the whole point
    # is that this call shape is the guard.
    digest = query_freeze.query_sha256(query)
    path = decisions_path() if path is None else path
    record = {"query_id": query["query_id"], "verdict": verdict,
              "note": note.strip(),
              query_freeze.DECISION_HASH_FIELD: digest}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def summarize(queries: list[dict], decisions: dict) -> dict:
    verdicts = [d["verdict"] for d in decisions.values()]
    return {"approved": verdicts.count("approved"),
            "rejected": verdicts.count("rejected"),
            "total": len(queries)}


def build_context(queries: list[dict], records: list[dict]) -> dict:
    """Per-query gold-chunk counts and advisories, read from the store.

    Reads the store, never any retriever -- the same class of act as reading
    the filing, so opening this tool does not break the blind the query set
    was written under.
    """
    context = {}
    for query in queries:
        locations = [(g["accession"], g["span"]) for g in query["gold"]]
        context[query["query_id"]] = {
            "gold_chunks": len(gold.gold_chunk_ids_for(records, locations)) if locations else 0,
            "notes": gold.advisory_notes(locations, records) if locations else [],
        }
    return context


def load_context(queries: list[dict]) -> dict:
    """Cached `build_context`. It costs a full store scan per span."""
    path = context_path()
    if path.exists() and path.stat().st_mtime >= queries_path().stat().st_mtime:
        return json.loads(path.read_text(encoding="utf-8"))
    print("scanning the store for gold counts and advisories (once, ~2 min) ...")
    context = build_context(queries, chunk_store.read())
    path.write_text(json.dumps(context, indent=1), encoding="utf-8")
    return context


app = FastAPI(title="Query-set review")


class Decision(BaseModel):
    query_id: str
    verdict: str
    note: str = ""


@app.post("/decide")
def decide(decision: Decision):
    queries = read_queries()
    # Re-read rather than trusting a cached copy, and look the record up rather
    # than taking the id on trust: the hash written with the verdict has to be
    # the hash of the text on disk at the moment the button was pressed, or the
    # binding records something that was never reviewed.
    matched = [q for q in queries if q["query_id"] == decision.query_id]
    if not matched:
        raise HTTPException(404, f"no such query {decision.query_id!r}")
    try:
        record = write_decision(matched[0], decision.verdict, decision.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    payload = summarize(queries, read_decisions())
    payload["saved"] = record
    return JSONResponse(payload)


@app.get("/progress")
def progress():
    return JSONResponse(summarize(read_queries(), read_decisions()))


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(render(read_queries()))


def render(queries: list[dict]) -> str:
    """The page. All I/O happens here; the markup is built by render_cards."""
    return render_cards(queries, load_context(queries), read_decisions())


def render_cards(queries: list[dict], context: dict, decisions: dict) -> str:
    """Pure: queries plus their context and verdicts in, one page of HTML out.

    Split from `render` so the escaping can be tested without a loaded store.
    Gold spans are filing text and carry `&`, `<` and quote characters, so an
    unescaped span would corrupt the markup and could truncate the very text
    being reviewed -- a defect that would make this tool quietly lie.
    """
    esc = html.escape
    cards = []

    for query in queries:
        qid = query["query_id"]
        info = context.get(qid, {})
        prior = decisions.get(qid, {})
        stratum = query["stratum"]

        chips = ['<span class="chip s-' + esc(stratum) + '">' + esc(stratum) + "</span>"]
        golds = ""
        for location in query["gold"]:
            chips.append('<span class="chip">' + esc(location["ticker"])
                         + " &middot; item " + esc(location.get("item") or "-")
                         + "</span>")
            golds += ('<div class="gold"><div class="acc">'
                      + esc(location["accession"]) + '</div><div class="span">'
                      + esc(location["span"]) + "</div></div>")
        if not query["gold"]:
            golds = ('<div class="gold none"><b>No gold &mdash; unanswerable.</b><br>'
                     + esc(query.get("why_unanswerable", "")) + "</div>")
        if info.get("gold_chunks"):
            chips.append('<span class="chip">' + str(info["gold_chunks"])
                         + " gold chunk(s)</span>")

        notes = "".join('<div class="adv">' + esc(n) + "</div>"
                        for n in info.get("notes", []))
        verdict = prior.get("verdict", "")
        cards.append(
            '<section class="card ' + esc(verdict) + '" id="c-' + esc(qid) + '">'
            + "<header><b>" + esc(qid) + "</b>" + "".join(chips)
            + '<span class="verdict">' + esc(verdict) + "</span></header>"
            + '<p class="q">' + esc(query["query"]) + "</p>"
            + golds + notes
            + '<div class="row">'
            + "<button class=\"ok\" onclick=\"decide('" + esc(qid) + "','approved')\">Approve</button>"
            + "<button class=\"no\" onclick=\"decide('" + esc(qid) + "','rejected')\">Reject</button>"
            + '<input placeholder="note (optional)" value="' + esc(prior.get("note", "")) + '">'
            + "</div></section>")

    counts = summarize(queries, decisions)
    bar = (str(counts["approved"] + counts["rejected"]) + " of "
           + str(counts["total"]) + " decided")

    return (HEAD
            + '<header class="top"><b>Query-set review</b><span id="bar">' + bar
            + '</span><span class="hint">decisions save on click</span></header>'
            + "<main>" + "".join(cards) + "</main>" + SCRIPT)


HEAD = """<!doctype html><meta charset="utf-8"><title>Query-set review</title>
<style>
 body{font:15px/1.5 system-ui,-apple-system,sans-serif;margin:0;background:#f6f7f9;color:#111}
 header.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #d8dbe0;
   padding:12px 20px;display:flex;gap:16px;align-items:center;z-index:9}
 header.top .hint{margin-left:auto;font-size:13px;color:#666}
 main{max-width:900px;margin:0 auto;padding:20px}
 .card{background:#fff;border:1px solid #d8dbe0;border-left:5px solid #d8dbe0;
   border-radius:8px;padding:14px 16px;margin:0 0 14px}
 .card.approved{border-left-color:#1a7f37}
 .card.rejected{border-left-color:#c1121f}
 .card header{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
 .chip{font-size:12px;background:#eef0f3;border-radius:99px;padding:2px 9px;color:#444}
 .chip.s-conceptual{background:#e7f0ff;color:#14406e}
 .chip.s-unanswerable{background:#f4e7ff;color:#4c1d70}
 .verdict{margin-left:auto;font-size:12px;text-transform:uppercase;
   letter-spacing:.06em;color:#666}
 .q{font-size:17px;font-weight:600;margin:6px 0 10px}
 .gold{background:#fafbfc;border:1px solid #e5e7eb;border-radius:6px;
   padding:9px 11px;margin:6px 0}
 .gold.none{background:#faf5ff;border-color:#e3d4f5}
 .acc{font:12px ui-monospace,SFMono-Regular,monospace;color:#777;margin-bottom:4px}
 .span{font:13px/1.55 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap}
 .adv{background:#fff8e6;border:1px solid #f0dda0;border-radius:6px;
   padding:8px 10px;margin:6px 0;font-size:13px}
 .row{display:flex;gap:8px;margin-top:10px}
 button{border:1px solid #c7ccd3;background:#fff;border-radius:6px;
   padding:6px 14px;font:inherit;cursor:pointer}
 button.ok:hover{background:#e8f5ec;border-color:#1a7f37}
 button.no:hover{background:#fdeaec;border-color:#c1121f}
 input{flex:1;border:1px solid #c7ccd3;border-radius:6px;padding:6px 10px;font:inherit}
 #bar{font-variant-numeric:tabular-nums}
</style>
"""

SCRIPT = """<script>
async function decide(id, verdict) {
  const card = document.getElementById('c-' + id);
  const note = card.querySelector('input').value;
  const response = await fetch('/decide', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({query_id: id, verdict: verdict, note: note})
  });
  if (!response.ok) { alert('save failed: ' + await response.text()); return; }
  const data = await response.json();
  card.classList.remove('approved', 'rejected');
  card.classList.add(verdict);
  card.querySelector('.verdict').textContent = verdict;
  document.getElementById('bar').textContent =
    (data.approved + data.rejected) + ' of ' + data.total + ' decided ('
    + data.approved + ' approved, ' + data.rejected + ' rejected)';
}
</script>"""


def preflight() -> str | None:
    """Why this cannot run, or None. Checked BEFORE the socket is bound.

    Without RAG_FILINGS_DIR set, `queries_dir()` falls back to its
    repo-relative default and the query set is simply not there. Without this
    check uvicorn binds happily and every page load 500s, which reads to the
    user as "localhost isn't loading" -- a symptom that says nothing about the
    cause. Failing here names the variable and the path instead.
    """
    path = queries_path()
    if not path.exists():
        return (
            "no query set at " + str(path) + "\n\n"
            "If that path is inside the repo, RAG_FILINGS_DIR is not set: the\n"
            "query set is data and lives beside the filings, not in the repo.\n"
            "Set RAG_FILINGS_DIR (and RAG_CALIBRATION_DIR) to the data\n"
            "directory and run this again. SETUP.md records both.\n\n"
            "The example path is deliberately not spelled out here: committed\n"
            "source may not name a machine-local absolute path, which\n"
            "tests/test_no_machine_local_paths.py enforces -- and caught the\n"
            "first draft of this very message.\n"
        )
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review the 65-query set.")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    problem = preflight()
    if problem:
        print("cannot start: " + problem, file=sys.stderr)
        raise SystemExit(2)

    print("query set : " + str(queries_path()))
    print("decisions : " + str(decisions_path()))
    print("open      : http://127.0.0.1:" + str(args.port))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
