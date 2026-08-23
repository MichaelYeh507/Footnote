"""Local adjudication app for Phase 4/5 -- one blinded verdict at a time.

    cd backend
    .\\venv\\Scripts\\python.exe scripts/adjudicate_qa.py
    then open http://127.0.0.1:8767

    .\\venv\\Scripts\\python.exe scripts/adjudicate_qa.py --freeze
    when every item is judged, to digest-freeze the verdict file.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*. The
adjudicator sees the question, the gold span(s) and the answer text --
**nothing else**. Every record this app holds comes through
`evaluation/qa_adjudication.blind_queue`, which strips everything else in
the act of reading it; this file never touches the dropped fields, names
none of them, and tests enforce both. Items arrive in a seeded shuffled
order, one verdict per distinct (query, normalized answer), append-only
with last write winning before the freeze.

The rubric is displayed verbatim beside every item. The tie-break is part
of it: an item the rubric does not clearly decide is recorded *ambiguous*
and scored incorrect in the headline cells -- resolve doubt against the
pipeline, and let the ambiguous count be reported.

After the freeze, this app refuses new verdicts: a later change is a
disclosed edit against the frozen digest, made deliberately and by hand,
never a quiet click.

Binds 127.0.0.1 only. No authentication; a local single-user tool.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

import corpus_paths  # noqa: E402
from evaluation import qa_adjudication  # noqa: E402
from scripts import review_queries as review  # noqa: E402

VERDICTS_NAME = "qa-adjudications.jsonl"
FREEZE_NAME = "qa-adjudications-freeze.json"


def completed_answer_files() -> list[pathlib.Path]:
    """Answer files whose run finished, by the existence of its record file.

    Existence only -- that record is never opened here. This app needs the
    answers and the query set, and nothing else about how the run happened.
    """
    out = []
    for path in sorted(corpus_paths.qa_dir().glob("answers-*.jsonl")):
        run = path.stem.replace("answers-", "")
        if (path.parent / f"qa-provenance-{run}.json").exists():
            out.append(path)
    return out


def read_answer_lines(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_queue() -> list[dict]:
    files = completed_answer_files()
    if not files:
        raise FileNotFoundError(
            "no completed run to adjudicate. Run scripts/run_qa.py first; "
            "a still-running or crashed run is finished there, not judged "
            "here.")
    return qa_adjudication.blind_queue(read_answer_lines(files[-1]),
                                       review.read_queries())


def build_app(queue: list[dict], verdicts_path: pathlib.Path,
              freeze_path: pathlib.Path) -> FastAPI:
    app = FastAPI(title="Phase 4/5 adjudication")
    queue_keys = {item["key"] for item in queue}

    def state() -> dict:
        verdicts = qa_adjudication.read_verdicts(verdicts_path)
        todo = qa_adjudication.outstanding(queue, verdicts)
        return {
            "total": len(queue),
            "done": len(queue) - len(todo),
            "frozen": freeze_path.exists(),
            "rubric": [list(row) for row in qa_adjudication.RUBRIC],
            "next": todo[0] if todo else None,
        }

    @app.get("/api/state")
    def api_state():
        return JSONResponse(state())

    @app.post("/api/verdict")
    async def api_verdict(payload: dict):
        if freeze_path.exists():
            raise HTTPException(409, detail=(
                "the verdict file is frozen. A change now is a disclosed "
                "edit against the frozen digest, not a click."))
        record = qa_adjudication.verdict_record(
            payload.get("key"), payload.get("verdict"),
            payload.get("ambiguous", False), payload.get("note", ""))
        problems = qa_adjudication.validate_verdict(record, queue_keys)
        if problems:
            raise HTTPException(422, detail="; ".join(problems))
        with open(verdicts_path, "a", encoding="utf-8",
                  newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return JSONResponse(state())

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    return app


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Phase 4/5 adjudication</title>
<style>
  body { font: 16px/1.5 Georgia, serif; max-width: 52rem;
         margin: 2rem auto; padding: 0 1rem; color: #222; }
  .card { border: 1px solid #ccc; border-radius: 6px; padding: 1rem 1.25rem;
          margin: 1rem 0; }
  .label { font: 700 11px/1 Verdana, sans-serif; letter-spacing: .08em;
           text-transform: uppercase; color: #666; margin-bottom: .35rem; }
  .gold { background: #f6f3e8; }
  .answer { background: #eef3f6; font-size: 1.1rem; }
  .rubric { font-size: .85rem; color: #444; }
  .rubric b { color: #222; }
  button { font: 700 14px Verdana, sans-serif; padding: .6rem 1.2rem;
           border-radius: 6px; border: 1px solid #999; cursor: pointer;
           margin-right: .5rem; }
  #correct { background: #e4f2e4; }
  #incorrect { background: #f6e4e4; }
  label.amb { font: 14px Verdana, sans-serif; margin-left: .75rem; }
  #note { width: 100%; font: 14px Verdana, sans-serif; margin-top: .5rem; }
  #progress { font: 12px Verdana, sans-serif; color: #666; }
  #done { font-size: 1.3rem; }
</style>
<div id="progress"></div>
<div id="item" hidden>
  <div class="card"><div class="label">Question</div><div id="question"></div></div>
  <div class="card gold"><div class="label">Gold span(s)</div><div id="gold"></div></div>
  <div class="card answer"><div class="label">The answer under judgement</div><div id="answer"></div></div>
  <div class="card rubric"><div class="label">Rubric — correct iff all three hold; doubt resolves against the pipeline as ambiguous + incorrect</div><div id="rubric"></div></div>
  <div>
    <button id="correct">Correct (c)</button>
    <button id="incorrect">Incorrect (x)</button>
    <label class="amb"><input type="checkbox" id="ambiguous"> ambiguous (a)</label>
    <input id="note" placeholder="note (optional)">
  </div>
</div>
<div id="done" hidden>Every item is judged. Freeze the file with:
  <code>python scripts/adjudicate_qa.py --freeze</code></div>
<script>
let current = null;
function esc(s) { const d = document.createElement("div");
  d.textContent = s; return d.innerHTML; }
function render(s) {
  document.getElementById("progress").textContent =
    s.done + " / " + s.total + " judged" + (s.frozen ? " — FROZEN" : "");
  const item = document.getElementById("item");
  const done = document.getElementById("done");
  if (!s.next) { item.hidden = true; done.hidden = false; return; }
  current = s.next;
  item.hidden = false; done.hidden = true;
  document.getElementById("question").innerHTML = esc(current.question);
  document.getElementById("gold").innerHTML =
    current.gold_spans.map(g => "<p>“" + esc(g) + "”</p>").join("");
  document.getElementById("answer").innerHTML = esc(current.answer);
  document.getElementById("rubric").innerHTML =
    s.rubric.map(r => "<p><b>" + esc(r[0]) + ".</b> " + esc(r[1]) + "</p>").join("");
  document.getElementById("ambiguous").checked = false;
  document.getElementById("note").value = "";
}
async function load() { render(await (await fetch("/api/state")).json()); }
async function judge(verdict) {
  if (!current) return;
  const body = { key: current.key, verdict: verdict,
    ambiguous: document.getElementById("ambiguous").checked,
    note: document.getElementById("note").value };
  const r = await fetch("/api/verdict", { method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body) });
  if (r.ok) render(await r.json()); else alert(await r.text());
}
document.getElementById("correct").onclick = () => judge("correct");
document.getElementById("incorrect").onclick = () => judge("incorrect");
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" && e.target.id === "note") return;
  if (e.key === "c") judge("correct");
  if (e.key === "x") judge("incorrect");
  if (e.key === "a") { const box = document.getElementById("ambiguous");
    box.checked = !box.checked; }
});
load();
</script>
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true",
                        help="digest-freeze the verdict file once every "
                             "item is judged")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args(argv)

    try:
        queue = build_queue()
    except (FileNotFoundError, ValueError) as exc:
        print(f"REFUSING: {exc}")
        return 2

    out_dir = corpus_paths.qa_dir()
    verdicts_path = out_dir / VERDICTS_NAME
    freeze_path = out_dir / FREEZE_NAME

    if args.freeze:
        if freeze_path.exists():
            print(f"REFUSING: {freeze_path.name} already exists. The frozen "
                  f"digest is the reference later edits are disclosed "
                  f"against; it is not rewritten.")
            return 2
        try:
            record = qa_adjudication.freeze_verdicts(verdicts_path, queue)
        except (RuntimeError, FileNotFoundError) as exc:
            print(f"REFUSING to freeze: {exc}")
            return 2
        freeze_path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"frozen: {record['verdicts']} verdicts over "
              f"{record['queue']} items, {record['ambiguous']} ambiguous")
        print(f"        sha256 {record['file_sha256']}")
        print(f"        {freeze_path.name}")
        print("\nNext:  python scripts/score_qa.py")
        return 0

    verdicts = qa_adjudication.read_verdicts(verdicts_path)
    print(f"queue      : {len(queue)} items "
          f"({len(qa_adjudication.outstanding(queue, verdicts))} to judge)")
    print(f"verdicts   : {verdicts_path}")
    print(f"open http://127.0.0.1:{args.port}")
    uvicorn.run(build_app(queue, verdicts_path, freeze_path),
                host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
