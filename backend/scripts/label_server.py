"""Local labeling app -- renders the filing, records one label at a time.

    cd backend
    .\\venv\\Scripts\\python.exe scripts/label_server.py
    then open http://127.0.0.1:8765

Replaces the terminal flow for the same 351 instances and writes the same
corpus/labels.jsonl in the same pre-registered shape. Both tools can be used
interchangeably; they share evaluation/labeling.py, so the protocol rules are
enforced identically.

This is a separate application from backend/main.py on purpose. The API app
imports the extractor and the database client; this one must be structurally
incapable of reading model output, so it shares no import path with it. Tests
check the import graph, check that no string here names that file, and
instrument open() across a full request cycle.

Binds 127.0.0.1 only. No authentication, because it is a local single-user
tool and adding a login would be security theatre -- but do not expose the
port.
"""

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

import corpus_paths  # noqa: E402

from evaluation.field_audit import (  # noqa: E402
    FIELD_CONCEPTS, audit_hint, audit_verdict,
)
from evaluation.label_view import (  # noqa: E402
    FIELD_GUIDANCE, highlight_all, sanitize_filing_html,
)
from evaluation.labeling import (  # noqa: E402
    ANSWER_KINDS, QUEUE_FIELDS, build_queue, completed_keys, label_record,
    prior_hint, validate_label,
)
from evaluation.xbrl import facts_named, parse_facts, period_label  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = BACKEND / "corpus" / "manifest.json"
FILINGS = corpus_paths.filings_dir()
LABELS = BACKEND / "corpus" / "labels.jsonl"

app = FastAPI(title="SEC extraction labeling")

_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
_queue = build_queue(_manifest)
_by_accession = {f["accession"]: f for f in _manifest["filings"]}
_html_cache: dict[str, str] = {}
# accession -> field -> the marked strings, in document order. Same order the
# client's `hits` array ends up in, because both derive from document order.
_marks_cache: dict[str, dict[str, list[str]]] = {}
# Parsed XBRL facts, one filing at a time. Populated on the first audit for a
# filing rather than during rendering, so loading a document stays fast.
_facts_cache: dict[str, list[dict]] = {}

_MARK = re.compile(r'<mark[^>]*data-fields="([^"]*)"[^>]*>(.*?)</mark>', re.S)


_TAGS = re.compile(r"<[^>]+>")
CONTEXT_RADIUS = 180


def _marks_by_field(marked_html: str) -> dict[str, list[tuple[str, str]]]:
    """Each field's marks in document order, with surrounding text.

    A regex over serialized HTML is safe here and nowhere else in this file:
    `highlight_all` sets `mark.string`, so a mark holds one text node and never
    nested markup. Re-parsing a 3 MB filing with BeautifulSoup to recover what
    the marker already knew would cost another 1.4s per filing.

    The context window is what makes the prior-year hint work on the fields
    that matter. Thirteen marks in one filing read `chief executive officer`
    and the mark text alone cannot separate them; the surrounding text can,
    because only one of them sits beside the officer's name.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for match in _MARK.finditer(marked_html):
        window = marked_html[max(0, match.start() - CONTEXT_RADIUS):
                             match.end() + CONTEXT_RADIUS]
        context = re.sub(r"\s+", " ", _TAGS.sub(" ", window)).strip()
        for field in match.group(1).split():
            out.setdefault(field, []).append((match.group(2), context))
    return out


def _document_for(filing: dict) -> pathlib.Path:
    return FILINGS / f"{filing['ticker']}_{filing['period']}.htm"


def _done() -> set[tuple[str, str]]:
    if not LABELS.exists():
        return set()
    return completed_keys(LABELS.read_text(encoding="utf-8").splitlines())


def _labels() -> list[dict]:
    if not LABELS.exists():
        return []
    return [json.loads(line) for line
            in LABELS.read_text(encoding="utf-8").splitlines() if line.strip()]


@app.get("/api/queue")
def queue_state():
    done = _done()
    pending = [i for i in _queue if (i["accession"], i["field"]) not in done]
    item = dict(pending[0]) if pending else None
    if item:
        filing = _by_accession[item["accession"]]
        item["name"] = filing["name"]
        item["guidance"] = FIELD_GUIDANCE.get(item["field"], "")
        item["index"] = len(_queue) - len(pending) + 1
        # The path on disk, so the filing can be opened in an editor or a
        # browser tab outside this app. Sent as a string rather than linked:
        # a page served over http cannot navigate to file:// -- Chrome blocks
        # it silently, with no error anywhere -- so the UI offers copy plus a
        # same-origin tab instead of a link that looks live and does nothing.
        item["document_path"] = str(_document_for(filing))
    return {"total": len(_queue), "labeled": len(done),
            "remaining": len(pending), "item": item,
            "answer_kinds": list(ANSWER_KINDS)}


@app.get("/api/filing/{accession}", response_class=HTMLResponse)
def filing_html(accession: str, field: str = Query("")):
    # Resolved through the manifest, never built from the request path, so a
    # traversal attempt cannot reach a file outside the corpus.
    filing = _by_accession.get(accession)
    if filing is None:
        raise HTTPException(status_code=404, detail="unknown accession")

    # Keyed by accession alone, with every field marked in the one pass. The
    # client lights the current field's marks, so moving between the nine
    # fields of a filing costs nothing and only a new filing pays the parse.
    if accession not in _html_cache:
        document = _document_for(filing)
        if not document.exists():
            raise HTTPException(status_code=404, detail=f"{document.name} not fetched")
        # The ticker and the registrant name come from the manifest, so they
        # can be marked exactly rather than approximated by a pattern. Both
        # were being anchored by hand on every filing.
        marked, _counts = highlight_all(
            sanitize_filing_html(document.read_bytes()),
            literals={"ticker": [filing["ticker"]],
                      "company_name": [filing["name"]]})
        _html_cache.clear()          # one filing in memory at a time; 10-Ks are large
        _marks_cache.clear()
        _html_cache[accession] = marked
        _marks_cache[accession] = _marks_by_field(marked)
    return HTMLResponse(_html_cache[accession])


@app.get("/api/prior-hint")
def prior_year_hint(accession: str, field: str):
    """Where the other fiscal year's evidence sat, as an index into this
    year's highlights.

    An issuer's two 10-Ks are near-duplicates, so the second year's hunt is
    wasted motion -- but the second year's *read* is not. §2 sized the corpus
    at 22 issuers x 2 years rather than 4 x 10 exactly because consecutive
    filings correlate, and protocol rule 3 names the carry-over risk. Five of
    the nine fields change value every year.

    So this moves the cursor and nothing else. The response is three integers
    and a date string; last year's anchor and value never leave the server.
    """
    filing = _by_accession.get(accession)
    if filing is None:
        raise HTTPException(status_code=404, detail="unknown accession")
    entries = _marks_cache.get(accession, {}).get(field, [])
    if not entries:
        return JSONResponse({})

    prior = None
    for record in _labels():
        if (record.get("ticker") == filing["ticker"]
                and record.get("field") == field
                and record.get("period") != filing["period"]):
            prior = record          # last write wins; there is at most one
    hint = prior_hint(prior, [text for text, _ in entries],
                      [context for _, context in entries])
    return JSONResponse(hint or {})


@app.post("/api/label")
async def save_label(payload: dict):
    required = ("accession", "ticker", "period", "field", "answer_kind")
    missing = [k for k in required if k not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"missing: {missing}")

    record = label_record(
        {k: payload[k] for k in ("accession", "ticker", "period", "field")},
        answer_kind=payload["answer_kind"],
        value=payload.get("value"),
        locator=payload.get("locator") or {},
        ambiguous=bool(payload.get("ambiguous")),
        note=payload.get("note", ""),
        status=payload.get("status", "labeled"),
    )
    try:
        validate_label(record)
    except ValueError as exc:
        # Rejected before anything is written. The browser checks the same
        # rules for immediate feedback, but a client-side check the server does
        # not repeat is one that can be bypassed.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    LABELS.parent.mkdir(parents=True, exist_ok=True)
    with LABELS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    # Audited only AFTER the label is written, never before. Running it earlier
    # and showing the result would make this a lookup rather than a reading
    # task; running it after turns a defect into a prompt to go back, while the
    # corrected value still has to come from the filing. The payload carries a
    # verdict and a value-free sentence -- see AUDIT_HINTS.
    return JSONResponse({"ok": True, "audit": _audit_of(record)})


# Verdicts where the filing's own tags actually confirmed something. The rest
# are "no opinion": ceo_name has no concept, and a figure stated only in prose
# carries no tag at all.
_CONFIRMED = {"OK", "OK-SUM", "ABSENT-OK"}


def _audit_of(record: dict) -> dict:
    """Verdict for one saved label. Carries a status and never a figure."""
    filing = _by_accession.get(record["accession"])
    spec = FIELD_CONCEPTS.get(record["field"], {})
    if filing is None or not spec.get("concepts"):
        return {"status": "unchecked"}
    try:
        facts = facts_named(_facts_for(record["accession"]),
                            spec.get("concepts", ()))
        code, _detail = audit_verdict(record, facts, filing["period"],
                                      record["field"])
    except Exception:                                    # noqa: BLE001
        # A parsing failure must never cost a label that was already written.
        return {"status": "unchecked"}

    hint = audit_hint(code)
    if hint:
        return {"status": "warn", "code": code, "hint": hint}
    return {"status": "checked" if code in _CONFIRMED else "unchecked",
            "code": code}


def _facts_for(accession: str) -> list[dict]:
    """Tagged facts for one filing, parsed once and kept beside its HTML."""
    if accession not in _facts_cache:
        filing = _by_accession[accession]
        document = _document_for(filing)
        if not document.exists():
            return []
        _facts_cache.clear()
        _facts_cache[accession] = parse_facts(
            document.read_bytes().decode("utf-8", "replace"))
    return _facts_cache[accession]


@app.get("/api/facts")
def filer_facts(accession: str, field: str):
    """The filer's own tagged facts for one field -- xbrl_facts.py, in-app.

    Added 2026-08-18 at the owner's request, as the sanctioned alternative to
    model help: plan §5 records rejecting LLM assistance for labeling and
    replacing it with the registrant's own tags, because reading the filer's
    tags is reading the filing. Same contract as the CLI finder: every fact
    for the field's concepts with its resolved period and dimensions, and
    never a single crowned answer -- a comparative column and a
    subsequent-event declaration are tagged the same way, so picking the
    period under label stays the labeler's call.
    """
    filing = _by_accession.get(accession)
    if filing is None:
        raise HTTPException(status_code=404, detail="unknown accession")
    spec = FIELD_CONCEPTS.get(field)
    if spec is None:
        raise HTTPException(status_code=400, detail="unknown field")
    if not spec.get("concepts"):
        return JSONResponse(
            {"facts": [], "reason": "no XBRL concept for this field"})
    facts = facts_named(_facts_for(accession), spec["concepts"])
    return JSONResponse({
        "facts": [{"text": f["text"], "period": period_label(f),
                   "dims": f["dims"], "unit": f["unit"], "name": f["name"]}
                  for f in facts],
        "caveat": ("Every tag the filer wrote for this field, comparatives "
                   "and subsequent events included. Picking the period under "
                   "label is the labeler's call, and the anchor still comes "
                   "from text selected in the filing."),
    })


@app.post("/api/undo")
def undo_last():
    """Drop the most recent label so it can be redone.

    Labeling 351 instances without a way back would mean the only remedy for a
    misread is hand-editing a JSONL file. Rewrites the whole file rather than
    truncating, so a partially written final line cannot survive.
    """
    if not LABELS.exists():
        raise HTTPException(status_code=404, detail="no labels yet")
    lines = [ln for ln in LABELS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise HTTPException(status_code=404, detail="no labels yet")
    removed = json.loads(lines[-1])
    LABELS.write_text("".join(ln + "\n" for ln in lines[:-1]), encoding="utf-8")
    return JSONResponse({"ok": True, "removed": {"field": removed.get("field"),
                                                 "ticker": removed.get("ticker")}})


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Labeling</title><style>
* { box-sizing: border-box; }
body { margin:0; font:14px/1.5 system-ui,Segoe UI,sans-serif; height:100vh;
       display:flex; background:#0f1115; color:#e6e6e6; }
#doc { flex:1 1 62%; overflow:auto; background:#fff; color:#111; padding:24px; }
#doc table { border-collapse:collapse; }
#doc td,#doc th { padding:2px 6px; }
/* Every field's candidates are marked once per filing; only the current
   field's are lit, so switching fields is instant instead of a reparse. */
mark.hit { background:none; color:inherit; padding:0; }
mark.hit.live { background:#ffe066; padding:0 2px; border-radius:2px; }
mark.hit.live.on { background:#ff9f1a; outline:2px solid #ff6b00; }
#side { flex:1 1 38%; max-width:520px; display:flex; flex-direction:column;
        border-left:1px solid #2a2f3a; overflow:auto; }
.pad { padding:14px 16px; border-bottom:1px solid #2a2f3a; }
h2 { margin:0 0 4px; font-size:19px; }
.muted { color:#8b93a7; font-size:12px; }
.field { font-size:22px; font-weight:600; color:#7cc4ff; margin:2px 0 6px; }
.guide { background:#1a1f2b; border-left:3px solid #7cc4ff; padding:10px 12px;
         font-size:13px; border-radius:0 4px 4px 0; }
.guide b { color:#ffb86b; }
.warn { background:#3a2410; border-left:3px solid #ffb020; padding:10px 12px;
        font-size:13px; border-radius:0 4px 4px 0; color:#ffd9a0; }
.warn b { color:#fff2d6; }
button { font:inherit; padding:8px 12px; border-radius:6px; cursor:pointer;
         border:1px solid #3a4152; background:#232936; color:#e6e6e6; }
button:hover { border-color:#7cc4ff; }
button.sel { background:#1d4ed8; border-color:#60a5fa; color:#fff; }
button.go { background:#15803d; border-color:#22c55e; color:#fff; font-weight:600;
            width:100%; padding:11px; font-size:15px; }
input[type=text] { width:100%; padding:8px; border-radius:6px; font:inherit;
                   border:1px solid #3a4152; background:#161a22; color:#e6e6e6; }
label.row { display:flex; gap:8px; align-items:center; margin-top:8px; font-size:13px; }
.kinds { display:flex; gap:6px; margin:8px 0; }
.kinds button { flex:1; font-size:13px; }
#anchor { font-family:ui-monospace,Consolas,monospace; font-size:12px;
          background:#161a22; border:1px dashed #3a4152; border-radius:6px;
          padding:8px; min-height:34px; word-break:break-word; }
#audit { display:none; padding:12px 14px; margin:0; font-size:13px; }
#audit.warn { background:#3a1414; border-left:4px solid #f87171; color:#fecaca; }
#audit.pass { background:#11201a; border-left:4px solid #2f7d5c; color:#8fcbb0;
              padding:7px 14px; font-size:12px; }
#factsbox summary { cursor:pointer; }
#facts { margin-top:8px; }
#facts table { border-collapse:collapse; font-family:ui-monospace,Consolas,monospace;
               font-size:12px; width:100%; }
#facts td { padding:2px 8px 2px 0; border-bottom:1px solid #232936;
            vertical-align:top; }
#facts td.ft { text-align:right; color:#e6e6e6; white-space:nowrap; }
#facts td.fd { color:#ffb020; font-size:11px; }
#facts td.fp, #facts td.fu { color:#8b93a7; }
#audit b { color:#fff1f2; display:block; font-size:14px; margin-bottom:3px; }
#audit .why { color:#fda4af; }
.prior { margin-top:8px; background:#12241c; border-left:3px solid #34d399;
         padding:8px 10px; font-size:12px; color:#a7f3d0; border-radius:0 4px 4px 0; }
.prior b { color:#ecfdf5; }
#path { font-family:ui-monospace,Consolas,monospace; font-size:11px;
        background:#161a22; border:1px solid #3a4152; border-radius:6px;
        padding:7px 8px; word-break:break-all; cursor:pointer; color:#9fd0ff; }
#path:hover { border-color:#7cc4ff; }
#err { color:#fca5a5; font-size:13px; min-height:18px; margin-top:6px; }
kbd { background:#232936; border:1px solid #3a4152; border-radius:3px;
      padding:0 4px; font-size:11px; }
</style></head><body>
<div id="doc">loading filing…</div>
<div id="side">
  <div id="audit"></div>
  <div class="pad">
    <h2 id="co">…</h2>
    <div class="muted" id="meta"></div>
    <div class="field" id="fld"></div>
    <div class="muted" id="prog"></div>
  </div>
  <div class="pad"><div id="units" style="display:none"></div></div>
  <div class="pad"><div class="guide" id="guide"></div></div>
  <div class="pad"><details id="factsbox" ontoggle="if(this.open)loadFacts()">
    <summary class="muted">Filer's tagged facts — a finder, not an answer</summary>
    <div id="facts">…</div>
  </details></div>
  <div class="pad">
    <div class="muted">Highlights <span id="hits"></span>
      <button onclick="jump(-1)">◀</button><button onclick="jump(1)">▶</button>
      &nbsp;<kbd>n</kbd>/<kbd>p</kbd></div>
    <div id="prior" class="prior" style="display:none"></div>
  </div>
  <div class="pad">
    <div class="muted">This filing on disk</div>
    <div id="path" title="click to copy"></div>
    <div style="display:flex;gap:6px;margin-top:6px">
      <button style="flex:1" onclick="copyPath()">Copy path</button>
      <button style="flex:1" onclick="openTab()">Open in tab ↗</button>
    </div>
    <div class="muted" style="margin-top:6px">The tab is the sanitized filing on
      this server, so browser <kbd>Ctrl+F</kbd> works over the whole document.</div>
  </div>
  <div class="pad">
    <div class="muted">Answer</div>
    <div class="kinds">
      <button id="k-value" onclick="setKind('value')">Value <kbd>1</kbd></button>
      <button id="k-stated_none" onclick="setKind('stated_none')">Stated none <kbd>2</kbd></button>
      <button id="k-not_addressed" onclick="setKind('not_addressed')">Not addressed <kbd>3</kbd></button>
    </div>
    <div id="f-value">
      <input type="text" id="value" placeholder="type the figure exactly as printed">
      <div id="scalebox" style="margin-top:8px;display:none">
        <div class="muted">The table this came from is in:</div>
        <div class="kinds" style="margin:5px 0 0">
          <button id="s-millions"  onclick="setScale('millions')">millions</button>
          <button id="s-thousands" onclick="setScale('thousands')">thousands</button>
          <button id="s-billions"  onclick="setScale('billions')">billions</button>
        </div>
      </div>
      <div class="muted" id="preview" style="margin-top:6px"></div>
    </div>
    <div id="f-searched" style="display:none">
      <input type="text" id="searched" placeholder="terms you searched, comma separated">
    </div>
    <div class="muted" style="margin-top:10px">Anchor — select text in the filing</div>
    <div id="anchor">nothing selected</div>
    <div class="muted" style="margin-top:6px">Section (auto)</div>
    <div id="section" style="font-size:12px;color:#8b93a7"></div>
    <label class="row"><input type="checkbox" id="amb"> ambiguous</label>
    <input type="text" id="note" placeholder="note (optional)" style="margin-top:8px">
    <div id="err"></div>
  </div>
  <div class="pad"><button class="go" onclick="save()">Save &amp; next <kbd>Ctrl+Enter</kbd></button>
    <button style="width:100%;margin-top:8px" onclick="undo()">↶ Undo last label</button></div>
</div>
<script>
let item=null, kind='value', anchor='', section='', hits=[], at=-1, loaded=null;
let textNodes=[], nodeIndex=new Map();
let factsFor=null;   // "accession|field" the panel currently shows

// The in-app xbrl_facts finder. Fetched only when the labeler opens the
// panel, rendered with textContent throughout -- fact text comes from the
// filing and must not become markup.
async function loadFacts(){
  if(!item) return;
  const key=item.accession+'|'+item.field;
  if(factsFor===key) return;
  factsFor=key;
  const box=document.getElementById('facts');
  box.textContent='loading…';
  const r=await fetch('/api/facts?accession='+encodeURIComponent(item.accession)
                      +'&field='+encodeURIComponent(item.field));
  if(!r.ok){ box.textContent='unavailable ('+r.status+')'; factsFor=null; return; }
  const b=await r.json();
  if(!b.facts || !b.facts.length){
    box.textContent=b.reason || 'nothing tagged for this field'; return;
  }
  box.textContent='';
  const table=document.createElement('table');
  for(const f of b.facts){
    const tr=document.createElement('tr');
    for(const [cls,val] of [['ft',f.text],['fp',f.period],
                            ['fd',(f.dims||[]).join(', ')],['fu',f.unit||'']]){
      const td=document.createElement('td');
      td.className=cls; td.textContent=val; tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  box.appendChild(table);
  const caveat=document.createElement('div');
  caveat.className='muted'; caveat.style.marginTop='6px';
  caveat.textContent=b.caveat||'';
  box.appendChild(caveat);
}

async function load(){
  const s=await (await fetch('/api/queue')).json();
  if(!s.item){ document.getElementById('side').innerHTML='<div class="pad"><h2>All '+s.total+' labeled.</h2></div>'; return; }
  item=s.item;
  co.textContent=item.name; meta.textContent=item.ticker+' · FY '+item.period+' · '+item.accession;
  fld.textContent=item.field;
  prog.textContent=item.index+' of '+s.total+'  ·  '+s.remaining+' remaining';
  guide.innerHTML=item.guidance.replace(/TRAP:/g,'<b>TRAP:</b>').replace(/MILLIONS/g,'<b>MILLIONS</b>');
  document.getElementById('path').textContent=item.document_path||'';
  document.getElementById('path').onclick=copyPath;

  // Only refetch when the filing changes. Within a filing the document is
  // already in the DOM with every field marked, so switching fields is a
  // class toggle rather than a 3s reparse.
  if(loaded!==item.accession){
    doc.innerHTML='<p style="padding:40px;font:16px system-ui">loading filing…</p>';
    doc.innerHTML=await (await fetch('/api/filing/'+item.accession)).text();
    loaded=item.accession; buildIndex(); unitsBanner();
  }
  lightField(item.field);
  priorJump();
  setKind('value'); anchor=''; section='';
  document.getElementById('anchor').textContent='nothing selected';
  document.getElementById('section').textContent='';
  value.value=''; searched.value=''; note.value=''; amb.checked=false; err.textContent='';
  preview.textContent='';
  // New instance: fold the finder away so the default motion stays reading
  // the filing; reopening refetches for the new accession+field.
  factsFor=null; document.getElementById('factsbox').open=false;
  document.getElementById('facts').textContent='…';
  // Only the three millions-denominated fields get a scale selector. Offering
  // it for per-share amounts or headcount would invite scaling something that
  // must never be scaled.
  document.getElementById('scalebox').style.display = isMonetary() ? '' : 'none';
  setScale('millions');
  // Focus the value box. Without this, typing a value goes to the global
  // shortcut handler instead of the field -- which silently produced empty
  // labels rather than any visible error.
  value.focus();
}
async function undo(){
  const r=await fetch('/api/undo',{method:'POST'});
  if(r.ok) load();
}
// A file:// link from an http:// page is blocked by Chrome with no error, so
// the path is copyable text and the tab is served from here instead.
function copyPath(){
  if(!item) return;
  navigator.clipboard.writeText(item.document_path).then(()=>{
    const el=document.getElementById('path'), was=el.textContent;
    el.textContent='copied'; setTimeout(()=>{el.textContent=was;},700);
  });
}
function openTab(){
  if(item) window.open('/api/filing/'+item.accession+'?field='+item.field,'_blank');
}
function lightField(field){
  for(const m of doc.querySelectorAll('mark.hit.live')) m.classList.remove('live','on');
  hits=[...doc.querySelectorAll('mark.hit')].filter(
    m=>(m.dataset.fields||'').split(' ').includes(field));
  for(const m of hits) m.classList.add('live');
  at=-1;
  document.getElementById('hits').textContent=hits.length?('0 / '+hits.length):'none — use Ctrl+F';
  if(hits.length) jump(1); else doc.scrollTop=0;
}
// Start the cursor where the other fiscal year's evidence was found. The
// server sends an index and nothing else -- no anchor, no value -- so this
// moves the cursor and cannot show last year's answer.
async function priorJump(){
  const box=document.getElementById('prior');
  box.style.display='none'; box.textContent='';
  if(!item || !hits.length) return;
  let h={};
  try{
    h=await (await fetch('/api/prior-hint?accession='+encodeURIComponent(item.accession)
                         +'&field='+encodeURIComponent(item.field))).json();
  }catch(e){ return; }
  if(typeof h.index!=='number' || h.index<0 || h.index>=hits.length) return;
  if(at>=0) hits[at].classList.remove('on');
  at=h.index; hits[at].classList.add('on'); hits[at].scrollIntoView({block:'center'});
  document.getElementById('hits').textContent=(at+1)+' / '+hits.length;
  box.style.display='';
  box.innerHTML='Started at highlight <b>'+(h.index+1)+'</b> — where FY '+h.period
    +' was anchored. <b>Read this year’s figure.</b> Five of the nine fields '
    +'change every year, and the anchor must come from this document.';
}
function jump(d){
  if(!hits.length) return;
  if(at>=0) hits[at].classList.remove('on');
  at=(at+d+hits.length)%hits.length;
  hits[at].classList.add('on'); hits[at].scrollIntoView({block:'center'});
  document.getElementById('hits').textContent=(at+1)+' / '+hits.length;
}
function setKind(k){
  kind=k;
  for(const x of ANSWER) document.getElementById('k-'+x).classList.toggle('sel',x===k);
  document.getElementById('f-value').style.display = k==='value'?'':'none';
  document.getElementById('f-searched').style.display = k==='not_addressed'?'':'none';
}
const ANSWER=['value','stated_none','not_addressed'];
// A units slip is a silent 1000x error the matcher cannot tell from a misread.
// The box always holds the figure exactly as printed in the filing; the scale
// is declared separately and the app does the conversion.
//
// Declaring the scale rather than pressing a divide button matters: dividing
// mutates the box, so pressing it twice is a silent 1,000,000x error, and
// afterwards nothing records whether the conversion happened at all. A
// declaration is idempotent and re-selectable.
const MONETARY=['total_assets','revenue_most_recent_fy','goodwill_impairment'];
const SCALES={millions:1, thousands:0.001, billions:1000};
let unitScale='millions';

function parsed(){
  const raw=value.value.trim().replace(/[$,]/g,'');
  return raw!=='' && !isNaN(Number(raw)) ? Number(raw) : null;
}
function isMonetary(){ return item && MONETARY.includes(item.field); }
function storedValue(){
  const n=parsed();
  if(n===null) return null;
  if(!isMonetary()) return n;
  return Number((n*SCALES[unitScale]).toPrecision(15));
}
function setScale(s){
  unitScale=s;
  for(const k of Object.keys(SCALES))
    document.getElementById('s-'+k).classList.toggle('sel',k===s);
  showPreview();
}
function showPreview(){
  const n=parsed();
  if(n===null){ preview.textContent = value.value.trim()? 'stores as text':''; return; }
  const v=storedValue();
  let msg='stores: '+v.toLocaleString(undefined,{maximumFractionDigits:6});
  if(isMonetary()){
    msg+=' million';
    // Rendering as billions makes a units slip obvious at a glance:
    // "$5869.26bn" is absurd in a way "5,869,259" is not.
    msg+='  (= $'+(v/1000).toFixed(2)+'bn)';
  }
  preview.textContent=msg;
}
value.addEventListener('input',showPreview);
// Nearest preceding "Note N -" / "Item N." heading, for locator.section.
// The anchor stays on the evidence: a section title is identical across an
// issuer's two fiscal years, so anchoring on it would destroy the only signal
// that the second year was actually re-read.
const SECTION_RX=/(Note\s+\d+\s*[-–—:]?\s*[A-Za-z][^\n]{0,58}|Item\s+\d+[A-Z]?\.\s*[A-Za-z][^\n]{0,58})/;
// 12 of 39 corpus filings report in thousands. Getting this wrong is a silent
// 1000x error -- it does not look wrong in the record and the matcher just
// scores it as a miss. Counting captions is a heads-up, never an authority:
// a single filing can use different units in different tables, so the caption
// above the table you are reading is what governs.
function unitsBanner(){
  const t=(doc.textContent||'');
  const th=(t.match(/in thousands/gi)||[]).length;
  const mi=(t.match(/in millions/gi)||[]).length;
  const el=document.getElementById('units');
  if(th>mi&&th>0){
    el.style.display=''; el.className='warn';
    el.innerHTML='⚠ This filing mostly says <b>"in thousands"</b> ('+th+' vs '+mi+
      ' "in millions"). Monetary fields want MILLIONS — <b>divide by 1,000</b>. '+
      'Still check the caption above each table.';
  } else if(mi>0){
    el.style.display=''; el.className='muted';
    el.textContent='Captions: "in millions" ×'+mi+', "in thousands" ×'+th+
      '. Check the caption above each table.';
  } else {
    el.style.display=''; el.className='warn';
    el.innerHTML='⚠ No "in millions"/"in thousands" caption found. '+
      '<b>Read the table caption carefully</b> before entering a monetary value.';
  }
}
function buildIndex(){
  textNodes=[]; nodeIndex=new Map();
  const w=document.createTreeWalker(doc,NodeFilter.SHOW_TEXT);
  let n; while(n=w.nextNode()){ nodeIndex.set(n,textNodes.length); textNodes.push(n); }
}
function sectionFor(node){
  while(node && !nodeIndex.has(node)) node=node.parentNode&&node.firstChild===node?null:node.parentNode;
  let i=nodeIndex.has(node)?nodeIndex.get(node):-1;
  if(i<0) return '';
  for(let k=i;k>=0&&k>i-6000;k--){
    const raw=(textNodes[k].textContent||'').trim();
    // Headings only. Without these two conditions an inline cross-reference --
    // "see Note 2 to our consolidated financial statements included elsewhere"
    // -- is picked up as the section, which is worse than no section at all
    // because it looks authoritative. A heading starts its own node and is short.
    if(raw.length>110) continue;
    const m=raw.match(SECTION_RX);
    if(m && m.index===0) return m[0].replace(/\s+/g,' ').trim().slice(0,80);
  }
  return '';
}
doc.addEventListener('mouseup',()=>{
  const sel=window.getSelection();
  const t=(sel.toString()||'').trim();
  if(!t) return;
  anchor=t.slice(0,80);
  section=sectionFor(sel.anchorNode)||'';
  document.getElementById('anchor').textContent=anchor;
  document.getElementById('section').textContent=section||'(no Note/Item heading found above)';
});
async function save(){
  err.textContent='';
  const body={accession:item.accession,ticker:item.ticker,period:item.period,
    field:item.field,answer_kind:kind,ambiguous:amb.checked,note:note.value,
    locator:{section:section,anchor:anchor,
             searched:searched.value.split(',').map(s=>s.trim()).filter(Boolean)}};
  if(kind==='value'){
    const v=storedValue();
    body.value = v!==null ? v : value.value.trim();
  }
  const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify(body)});
  if(!r.ok){ err.textContent=(await r.json()).detail; return; }
  const saved=await r.json();
  const just={ticker:item.ticker,period:item.period,field:item.field};
  await load();
  showAudit(saved.audit, just);
}
// Shown only after the label is written, and only as a verdict. The server
// sends no figure, so this can say "go back and look" and cannot say "the
// answer is X" -- the corrected value still has to come from the filing.
function showAudit(audit, just){
  const box=document.getElementById('audit');
  box.style.display=''; box.className='';
  const where=just.ticker+' FY'+just.period+' · '+just.field;
  if(audit && audit.hint){
    box.className='warn';
    box.innerHTML='<b>⚠ '+audit.code+' — '+where+'</b><span class="why">'+audit.hint
      +'. Press <kbd>↶ Undo last label</kbd> to redo it.</span>';
    return;
  }
  // Shown even when nothing is wrong, so a working check and a broken one do
  // not look identical. It reveals no more than the banner's absence already
  // did -- the same single bit -- and it is what tells the labeler the check
  // ran at all. `not checked` is its own state: ceo_name has no XBRL concept
  // and many figures are stated only in prose.
  box.className='pass';
  box.textContent = (audit && audit.status==='checked')
    ? '✓ ' + where + ' — nothing in the filing’s tagged facts contradicts it'
    : '· ' + where + ' — no tagged fact to check against';
}
addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT' && e.key!=='Enter') return;
  if(e.ctrlKey&&e.key==='Enter') return save();
  if(e.target.tagName==='INPUT') return;
  if(e.key==='n') jump(1); else if(e.key==='p') jump(-1);
  else if(e.key==='1') setKind('value'); else if(e.key==='2') setKind('stated_none');
  else if(e.key==='3') setKind('not_addressed');
});
load();
</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skip-field", action="append", default=[],
                        choices=sorted(QUEUE_FIELDS), metavar="FIELD",
                        help="omit a field from the queue; repeatable. Defers "
                             "it rather than dropping it -- the instance stays "
                             "unlabeled and the denominator is unchanged")
    parser.add_argument("--only-field", action="append", default=[],
                        choices=sorted(QUEUE_FIELDS), metavar="FIELD",
                        help="serve only these fields, for a focused pass over "
                             "one field across every filing")
    args = parser.parse_args()

    # Filters the QUEUE, never the corpus. A deferred instance stays unlabeled
    # and the denominator is untouched -- 351 either way. Both fiscal years of
    # an issuer remain consecutive within whatever fields are served, so
    # protocol rule 3's intent survives; note that a single-field pass puts an
    # issuer's two years directly adjacent, which raises the carry-over risk
    # rule 3 names. The CARRY-OVER check in verify_labels.py is the answer to
    # that, and it is worth running after such a pass.
    global _queue
    if args.only_field:
        _queue = [i for i in _queue if i["field"] in set(args.only_field)]
    if args.skip_field:
        _queue = [i for i in _queue if i["field"] not in set(args.skip_field)]
    if args.only_field or args.skip_field:
        served = sorted({i["field"] for i in _queue})
        print(f"queue filtered to {len(_queue)} instances across "
              f"{len(served)} field(s): {', '.join(served)}", file=sys.stderr)

    print(f"labeling app: http://127.0.0.1:{args.port}", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
