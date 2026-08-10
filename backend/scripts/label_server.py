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
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from evaluation.label_view import (  # noqa: E402
    FIELD_GUIDANCE, highlight_all, sanitize_filing_html,
)
from evaluation.labeling import (  # noqa: E402
    ANSWER_KINDS, build_queue, completed_keys, label_record, validate_label,
)

BACKEND = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = BACKEND / "corpus" / "manifest.json"
FILINGS = BACKEND / "corpus" / "filings"
LABELS = BACKEND / "corpus" / "labels.jsonl"

app = FastAPI(title="SEC extraction labeling")

_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
_queue = build_queue(_manifest)
_by_accession = {f["accession"]: f for f in _manifest["filings"]}
_html_cache: dict[str, str] = {}


def _done() -> set[tuple[str, str]]:
    if not LABELS.exists():
        return set()
    return completed_keys(LABELS.read_text(encoding="utf-8").splitlines())


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
        document = FILINGS / f"{filing['ticker']}_{filing['period']}.htm"
        if not document.exists():
            raise HTTPException(status_code=404, detail=f"{document.name} not fetched")
        marked, _counts = highlight_all(sanitize_filing_html(document.read_bytes()))
        _html_cache.clear()          # one filing in memory at a time; 10-Ks are large
        _html_cache[accession] = marked
    return HTMLResponse(_html_cache[accession])


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
    return JSONResponse({"ok": True})


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
#err { color:#fca5a5; font-size:13px; min-height:18px; margin-top:6px; }
kbd { background:#232936; border:1px solid #3a4152; border-radius:3px;
      padding:0 4px; font-size:11px; }
</style></head><body>
<div id="doc">loading filing…</div>
<div id="side">
  <div class="pad">
    <h2 id="co">…</h2>
    <div class="muted" id="meta"></div>
    <div class="field" id="fld"></div>
    <div class="muted" id="prog"></div>
  </div>
  <div class="pad"><div id="units" style="display:none"></div></div>
  <div class="pad"><div class="guide" id="guide"></div></div>
  <div class="pad">
    <div class="muted">Highlights <span id="hits"></span>
      <button onclick="jump(-1)">◀</button><button onclick="jump(1)">▶</button>
      &nbsp;<kbd>n</kbd>/<kbd>p</kbd></div>
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

async function load(){
  const s=await (await fetch('/api/queue')).json();
  if(!s.item){ document.getElementById('side').innerHTML='<div class="pad"><h2>All '+s.total+' labeled.</h2></div>'; return; }
  item=s.item;
  co.textContent=item.name; meta.textContent=item.ticker+' · FY '+item.period+' · '+item.accession;
  fld.textContent=item.field;
  prog.textContent=item.index+' of '+s.total+'  ·  '+s.remaining+' remaining';
  guide.innerHTML=item.guidance.replace(/TRAP:/g,'<b>TRAP:</b>').replace(/MILLIONS/g,'<b>MILLIONS</b>');

  // Only refetch when the filing changes. Within a filing the document is
  // already in the DOM with every field marked, so switching fields is a
  // class toggle rather than a 3s reparse.
  if(loaded!==item.accession){
    doc.innerHTML='<p style="padding:40px;font:16px system-ui">loading filing…</p>';
    doc.innerHTML=await (await fetch('/api/filing/'+item.accession)).text();
    loaded=item.accession; buildIndex(); unitsBanner();
  }
  lightField(item.field);
  setKind('value'); anchor=''; section='';
  document.getElementById('anchor').textContent='nothing selected';
  document.getElementById('section').textContent='';
  value.value=''; searched.value=''; note.value=''; amb.checked=false; err.textContent='';
  preview.textContent='';
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
function lightField(field){
  for(const m of doc.querySelectorAll('mark.hit.live')) m.classList.remove('live','on');
  hits=[...doc.querySelectorAll('mark.hit')].filter(
    m=>(m.dataset.fields||'').split(' ').includes(field));
  for(const m of hits) m.classList.add('live');
  at=-1;
  document.getElementById('hits').textContent=hits.length?('0 / '+hits.length):'none — use Ctrl+F';
  if(hits.length) jump(1); else doc.scrollTop=0;
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
  load();
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
    args = parser.parse_args()
    print(f"labeling app: http://127.0.0.1:{args.port}", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
