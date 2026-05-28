#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kg_to_html.py
=============
Convert a Neo4j Aura "query table" CSV export into a single, fully self-contained,
offline, interactive HTML knowledge-graph viewer.

The exported CSV is expected to have the columns:
    source_id, source_labels, source_properties,
    relation,
    target_id, target_labels, target_properties

Node properties are stored in Neo4j Browser's Cypher-map display format
(unquoted keys/values, multi-line), e.g.

    {
      network_level: 1,
      knowledge_domain: 系统原理,
      importance: 1.0,
      name: Propulsion system,
      description: A propulsion system is ...,
      subsystem: General,
      id: 1,
      physics_domain: GeneralPhysics
    }

Design goals (per request):
  * Offline           -> everything embedded in ONE .html file, zero CDN / network use.
  * Queryable/filter  -> full-text search + faceted filters + relation-type filters + table view.
  * Readable          -> clean dark "engineering instrument" theme, detail panel, legend.
  * Low render load   -> layout is computed ONCE here in Python (no live physics in the
                         browser); the page draws fixed coordinates on a <canvas> and only
                         redraws on interaction (no animation loop).

Usage:
    python kg_to_html.py INPUT.csv [-o OUTPUT.html] [--title "My Graph"]
"""

import argparse
import csv
import html
import json
import re
import sys
from collections import defaultdict

# ----------------------------------------------------------------------------- #
#  CSV / property parsing
# ----------------------------------------------------------------------------- #

# Increase the field-size limit; Neo4j property blobs can be large.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# Keys that look numeric -> we try to coerce them so filters/sorting behave sensibly.
_NUMERIC_KEYS = {"network_level", "importance", "id"}


def parse_cypher_map(blob):
    """Parse Neo4j Browser's map-display format into a dict.

    This is *not* JSON: keys are unquoted, string values are unquoted and may
    themselves contain commas, colons and Chinese text. We rely on the fact
    that the exporter prints one "key: value" pair per line, separated by
    ",\\n". That makes a line-oriented split safe.
    """
    if blob is None:
        return None
    s = blob.strip()
    if not s or s.lower() == "null":
        return None
    if s.startswith("{"):
        s = s[1:]
    if s.endswith("}"):
        s = s[:-1]

    out = {}
    # Each property sits on its own logical line: "  key: value".
    # Split on ",\n" first (the exporter's pair separator), then fall back to
    # plain newlines so a single-line export still works.
    pieces = re.split(r",\s*\n", s)
    if len(pieces) == 1:
        pieces = s.split("\n")

    key_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", re.S)
    for piece in pieces:
        if not piece.strip():
            continue
        m = key_re.match(piece)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).strip().rstrip(",").strip()
        if key in _NUMERIC_KEYS:
            num = _to_number(val)
            if num is not None:
                val = num
        out[key] = val
    return out


def _to_number(v):
    try:
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        if re.fullmatch(r"-?\d*\.\d+", v):
            return float(v)
    except (TypeError, ValueError):
        pass
    return None


def clean_label(raw):
    """'[Component]' -> 'Component'; 'null' -> None."""
    if not raw or raw.lower() == "null":
        return None
    return raw.strip().strip("[]").strip()


# ----------------------------------------------------------------------------- #
#  Load graph from CSV
# ----------------------------------------------------------------------------- #

def load_graph(csv_path):
    nodes = {}          # id(str) -> dict(properties + _label)
    edges = []          # list of dicts: source, target, relation
    edge_seen = set()   # dedupe identical (s, r, t) triples

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"source_id", "source_labels", "source_properties",
                    "relation", "target_id", "target_labels", "target_properties"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                "ERROR: CSV is missing expected columns: %s\nFound: %s"
                % (", ".join(sorted(missing)), reader.fieldnames)
            )

        for row in reader:
            _ingest_node(nodes, row["source_id"], row["source_labels"],
                         row["source_properties"])
            _ingest_node(nodes, row["target_id"], row["target_labels"],
                         row["target_properties"])

            rel = (row["relation"] or "").strip()
            s, t = (row["source_id"] or "").strip(), (row["target_id"] or "").strip()
            if rel and rel.lower() != "null" and s and t and t.lower() != "null":
                key = (s, rel, t)
                if key not in edge_seen:
                    edge_seen.add(key)
                    edges.append({"s": s, "t": t, "r": rel})

    return nodes, edges


def _ingest_node(nodes, nid, labels, props):
    if not nid or nid.strip().lower() == "null":
        return
    nid = nid.strip()
    label = clean_label(labels)
    parsed = parse_cypher_map(props) or {}
    if nid not in nodes:
        nodes[nid] = {"_id": nid, "_label": label or "Unknown", **parsed}
    else:
        # Merge: fill any missing properties from a later occurrence.
        existing = nodes[nid]
        if existing.get("_label") in (None, "Unknown") and label:
            existing["_label"] = label
        for k, v in parsed.items():
            existing.setdefault(k, v)


# ----------------------------------------------------------------------------- #
#  Layout (computed ONCE, here, so the browser never runs physics)
# ----------------------------------------------------------------------------- #

def compute_layout(nodes, edges, seed=7):
    """Return {node_id: (x, y)} normalized to roughly [0, 1000] in both axes.

    Uses networkx spring layout when available; otherwise falls back to a
    deterministic circular layout so the script still works without networkx.
    """
    ids = list(nodes.keys())
    try:
        import networkx as nx
        import numpy as np

        G = nx.Graph()
        G.add_nodes_from(ids)
        for e in edges:
            if e["s"] in nodes and e["t"] in nodes:
                G.add_edge(e["s"], e["t"])

        # k controls node spacing; scale with graph size for readability.
        k = 1.3 / (len(ids) ** 0.5) if ids else None
        pos = nx.spring_layout(G, k=k, iterations=220, seed=seed, dim=2)

        xs = np.array([pos[i][0] for i in ids])
        ys = np.array([pos[i][1] for i in ids])
        return _normalize(ids, xs, ys)
    except Exception as exc:  # pragma: no cover - fallback path
        sys.stderr.write("networkx layout unavailable (%s); using circular layout\n" % exc)
        import math
        n = max(1, len(ids))
        out = {}
        for i, nid in enumerate(ids):
            ang = 2 * math.pi * i / n
            out[nid] = (500 + 450 * math.cos(ang), 500 + 450 * math.sin(ang))
        return out


def _normalize(ids, xs, ys, span=1000.0, pad=60.0):
    import numpy as np
    def scale(arr):
        lo, hi = float(arr.min()), float(arr.max())
        if hi - lo < 1e-9:
            return np.full_like(arr, span / 2.0)
        return pad + (arr - lo) / (hi - lo) * (span - 2 * pad)
    X, Y = scale(xs), scale(ys)
    return {nid: (round(float(X[i]), 2), round(float(Y[i]), 2)) for i, nid in enumerate(ids)}


# ----------------------------------------------------------------------------- #
#  Build the compact data payload embedded into the HTML
# ----------------------------------------------------------------------------- #

def build_payload(nodes, edges, pos, title):
    labels = sorted({n["_label"] for n in nodes.values()})
    rel_types = sorted({e["r"] for e in edges})

    # Degree for sizing.
    deg = defaultdict(int)
    for e in edges:
        deg[e["s"]] += 1
        deg[e["t"]] += 1

    # Stable id -> index mapping keeps the JSON small.
    id_list = list(nodes.keys())
    idx = {nid: i for i, nid in enumerate(id_list)}

    facet_keys = ["_label", "subsystem", "physics_domain", "network_level",
                  "knowledge_domain", "importance"]
    facets = {}
    for key in facet_keys:
        vals = {}
        for n in nodes.values():
            v = n.get(key)
            if v is None or v == "":
                continue
            vals[str(v)] = vals.get(str(v), 0) + 1
        if vals:
            facets[key] = vals

    out_nodes = []
    for nid in id_list:
        n = nodes[nid]
        x, y = pos.get(nid, (500.0, 500.0))
        out_nodes.append({
            "i": idx[nid],
            "rid": nid,
            "x": x, "y": y,
            "lab": n.get("_label", "Unknown"),
            "name": str(n.get("name", nid)),
            "deg": deg.get(nid, 0),
            "p": {k: v for k, v in n.items() if not k.startswith("_")},
        })

    out_edges = [{"s": idx[e["s"]], "t": idx[e["t"]], "r": e["r"]}
                 for e in edges if e["s"] in idx and e["t"] in idx]

    return {
        "title": title,
        "labels": labels,
        "relTypes": rel_types,
        "facets": facets,
        "nodes": out_nodes,
        "edges": out_edges,
        "stats": {"nodes": len(out_nodes), "edges": len(out_edges),
                  "relTypes": len(rel_types), "nodeTypes": len(labels)},
    }


# ----------------------------------------------------------------------------- #
#  HTML generation
# ----------------------------------------------------------------------------- #

def render_html(payload):
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Prevent a stray "</script>" inside any string value from terminating the
    # inline <script> block; also keep U+2028/U+2029 out of JS source.
    data_json = (data_json.replace("</", "<\\/")
                          .replace("\u2028", "\\u2028")
                          .replace("\u2029", "\\u2029"))
    page_title = html.escape(payload["title"])
    return HTML_TEMPLATE.replace("/*__TITLE__*/", page_title) \
                        .replace("/*__DATA__*/", data_json)


# The big self-contained template lives in a separate module-level string so the
# logic above stays readable.
# --- embedded HTML template (was html_template.py) ---
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>/*__TITLE__*/</title>
<style>
  :root{
    --bg:#0d1117; --panel:#141b24; --panel2:#0f151d; --line:#23303d;
    --ink:#e8eef4; --muted:#8aa0b3; --faint:#5a6b7b;
    --accent:#46b3ff; --accent2:#ffb84d; --grid:#16202b;
    --shadow:0 8px 30px rgba(0,0,0,.45);
    --mono:ui-monospace,"SFMono-Regular","Cascadia Code",Menlo,Consolas,"Liberation Mono",monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",
           "PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font-family:var(--sans);
            font-size:13px;overflow:hidden}
  button,input,select{font-family:inherit}
  /* ---- layout shell ---- */
  #app{display:grid;grid-template-rows:auto 1fr;height:100vh}
  header{display:flex;align-items:center;gap:18px;padding:10px 16px;background:var(--panel);
         border-bottom:1px solid var(--line);z-index:5}
  header .brand{font-family:var(--mono);font-weight:600;letter-spacing:.3px;font-size:14px;
                display:flex;align-items:center;gap:9px;white-space:nowrap}
  header .dot{width:9px;height:9px;border-radius:50%;background:var(--accent);
              box-shadow:0 0 10px var(--accent)}
  header .stats{display:flex;gap:14px;color:var(--muted);font-family:var(--mono);font-size:11.5px}
  header .stats b{color:var(--ink);font-weight:600}
  header .spacer{flex:1}
  .search{position:relative}
  .search input{width:280px;max-width:42vw;background:var(--panel2);border:1px solid var(--line);
    color:var(--ink);padding:7px 30px 7px 11px;border-radius:8px;outline:none;font-size:12.5px}
  .search input:focus{border-color:var(--accent)}
  .search .x{position:absolute;right:8px;top:50%;transform:translateY(-50%);cursor:pointer;
    color:var(--faint);font-size:14px;display:none}
  .toggle{display:flex;background:var(--panel2);border:1px solid var(--line);border-radius:8px;overflow:hidden}
  .toggle button{background:transparent;border:0;color:var(--muted);padding:7px 13px;cursor:pointer;
    font-size:12px;font-family:var(--mono)}
  .toggle button.on{background:var(--accent);color:#04121f;font-weight:600}

  main{display:grid;grid-template-columns:264px 1fr;min-height:0}
  /* ---- sidebar ---- */
  aside{background:var(--panel);border-right:1px solid var(--line);overflow-y:auto;padding:12px}
  aside::-webkit-scrollbar,.detail::-webkit-scrollbar,.tablewrap::-webkit-scrollbar{width:9px}
  aside::-webkit-scrollbar-thumb,.detail::-webkit-scrollbar-thumb,.tablewrap::-webkit-scrollbar-thumb
    {background:#26343f;border-radius:6px}
  .facet{border-bottom:1px solid var(--line);padding:8px 0}
  .facet h4{margin:0;font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);
    cursor:pointer;display:flex;align-items:center;gap:6px;user-select:none}
  .facet h4 .car{transition:transform .15s;color:var(--faint)}
  .facet.closed h4 .car{transform:rotate(-90deg)}
  .facet.closed .body{display:none}
  .facet .body{margin-top:8px;display:flex;flex-direction:column;gap:3px;max-height:230px;overflow:auto}
  .opt{display:flex;align-items:center;gap:8px;cursor:pointer;padding:3px 5px;border-radius:6px;
    font-size:12px;color:var(--ink)}
  .opt:hover{background:var(--panel2)}
  .opt input{accent-color:var(--accent);margin:0}
  .opt .sw{width:10px;height:10px;border-radius:3px;flex:none}
  .opt .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .opt .ct{color:var(--faint);font-family:var(--mono);font-size:10.5px}
  .facet .mini{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--ink);
    padding:5px 8px;border-radius:6px;outline:none;font-size:11.5px;margin-bottom:6px}
  .toolbar{display:flex;gap:8px;margin-bottom:10px}
  .toolbar button{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--muted);
    padding:7px;border-radius:7px;cursor:pointer;font-size:11.5px;font-family:var(--mono)}
  .toolbar button:hover{border-color:var(--accent);color:var(--ink)}
  .chk{display:flex;align-items:center;gap:8px;font-size:12px;padding:4px 5px;cursor:pointer;color:var(--ink)}
  .chk input{accent-color:var(--accent)}

  /* ---- stage ---- */
  .stage{position:relative;min-width:0;min-height:0;background:
     radial-gradient(circle at 50% 40%, #111a24 0%, var(--bg) 75%)}
  canvas{display:block;width:100%;height:100%;cursor:grab}
  canvas.drag{cursor:grabbing}
  .hud{position:absolute;left:12px;bottom:12px;display:flex;gap:8px;align-items:center;
    background:rgba(15,21,29,.82);border:1px solid var(--line);border-radius:9px;padding:6px;backdrop-filter:blur(4px)}
  .hud button{width:30px;height:30px;border-radius:7px;border:1px solid var(--line);background:var(--panel2);
    color:var(--ink);cursor:pointer;font-size:15px}
  .hud button:hover{border-color:var(--accent)}
  .hud .z{font-family:var(--mono);color:var(--muted);font-size:11px;min-width:46px;text-align:center}
  .tip{position:absolute;pointer-events:none;background:#04121f;border:1px solid var(--accent);
    color:var(--ink);padding:5px 9px;border-radius:7px;font-size:12px;display:none;max-width:280px;
    box-shadow:var(--shadow);z-index:9;font-family:var(--mono)}
  .legend{position:absolute;right:12px;top:12px;background:rgba(15,21,29,.82);border:1px solid var(--line);
    border-radius:9px;padding:9px 11px;backdrop-filter:blur(4px);font-size:11.5px}
  .legend .row{display:flex;align-items:center;gap:7px;margin:3px 0;color:var(--muted)}
  .legend .sw{width:11px;height:11px;border-radius:3px}
  .empty{position:absolute;inset:0;display:none;align-items:center;justify-content:center;
    color:var(--faint);font-family:var(--mono);font-size:13px}

  /* ---- detail panel ---- */
  .detail{position:absolute;right:0;top:0;height:100%;width:340px;background:var(--panel);
    border-left:1px solid var(--line);transform:translateX(105%);transition:transform .22s ease;
    overflow-y:auto;z-index:8;box-shadow:var(--shadow)}
  .detail.open{transform:none}
  .detail .head{padding:14px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}
  .detail .badge{display:inline-block;font-family:var(--mono);font-size:10.5px;padding:2px 8px;border-radius:20px;
    color:#04121f;font-weight:600;margin-bottom:8px}
  .detail h3{margin:0;font-size:16px;line-height:1.3}
  .detail .close{position:absolute;right:12px;top:12px;cursor:pointer;color:var(--faint);font-size:18px;
    background:none;border:0}
  .detail .sect{padding:12px 16px;border-bottom:1px solid var(--line)}
  .detail .sect h5{margin:0 0 8px;font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
  .kv{display:grid;grid-template-columns:96px 1fr;gap:5px 10px;font-size:12px}
  .kv .k{color:var(--faint);font-family:var(--mono)}
  .kv .v{color:var(--ink);word-break:break-word}
  .desc{font-size:12.5px;line-height:1.55;color:#cdd9e3}
  .rel{display:flex;align-items:center;gap:8px;padding:5px 7px;border-radius:7px;cursor:pointer;font-size:12px}
  .rel:hover{background:var(--panel2)}
  .rel .arr{font-family:var(--mono);font-size:10px;color:var(--accent2);white-space:nowrap}
  .rel .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink)}
  .rel .sw{width:9px;height:9px;border-radius:50%;flex:none}

  /* ---- table view ---- */
  .tablewrap{display:none;height:100%;overflow:auto;background:var(--bg)}
  table{border-collapse:collapse;width:100%;font-size:12px}
  thead th{position:sticky;top:0;background:var(--panel);color:var(--muted);text-align:left;
    padding:9px 12px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap;
    font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:.4px}
  thead th:hover{color:var(--ink)}
  thead th .ar{color:var(--accent);font-size:10px}
  tbody td{padding:8px 12px;border-bottom:1px solid var(--grid);color:var(--ink);vertical-align:top}
  tbody tr{cursor:pointer}
  tbody tr:hover{background:var(--panel2)}
  td .pill{font-family:var(--mono);font-size:10.5px;padding:2px 7px;border-radius:20px;color:#04121f;font-weight:600}
  td.num{font-family:var(--mono);color:var(--muted)}
  .tdesc{color:var(--muted);max-width:520px}
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="brand"><span class="dot"></span><span>/*__TITLE__*/</span></div>
    <div class="stats" id="stats"></div>
    <div class="spacer"></div>
    <div class="search">
      <input id="q" type="text" placeholder="搜索名称 / 描述 / 知识域…" autocomplete="off">
      <span class="x" id="qx">&times;</span>
    </div>
    <div class="toggle">
      <button id="vGraph" class="on">图谱</button>
      <button id="vTable">表格</button>
    </div>
  </header>

  <main>
    <aside id="side"></aside>

    <div class="stage" id="stage">
      <canvas id="cv"></canvas>
      <div class="legend" id="legend"></div>
      <div class="hud">
        <button id="zin" title="放大">+</button>
        <button id="zout" title="缩小">&minus;</button>
        <span class="z" id="zlabel">100%</span>
        <button id="fit" title="适配视图">⤢</button>
      </div>
      <div class="tip" id="tip"></div>
      <div class="empty" id="empty">没有匹配当前筛选条件的节点</div>

      <div class="detail" id="detail">
        <div class="head">
          <button class="close" id="dclose">&times;</button>
          <span class="badge" id="dbadge"></span>
          <h3 id="dname"></h3>
        </div>
        <div id="ddesc" class="sect" style="display:none">
          <h5>描述</h5><div class="desc" id="ddesctext"></div>
        </div>
        <div class="sect"><h5>属性</h5><div class="kv" id="dprops"></div></div>
        <div class="sect"><h5 id="douth">关系 · 出 →</h5><div id="dout"></div></div>
        <div class="sect"><h5 id="dinh">关系 · 入 ←</h5><div id="din"></div></div>
      </div>

      <div class="tablewrap" id="tablewrap"></div>
    </div>
  </main>
</div>

<script id="payload" type="application/json">/*__DATA__*/</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("payload").textContent);
const N = DATA.nodes, E = DATA.edges;

/* ---------- palette per node type ---------- */
const PALETTE = ["#46b3ff","#ffb84d","#7ee787","#ff7b9c","#c792ea",
                 "#56d4c2","#f6c177","#9aa7ff","#ff9e64","#a0e8a0"];
const COLOR = {};
DATA.labels.forEach((l,i)=>COLOR[l]=PALETTE[i%PALETTE.length]);
const colorOf = n => COLOR[n.lab] || "#9aa7ff";

/* ---------- adjacency ---------- */
const outAdj = N.map(()=>[]), inAdj = N.map(()=>[]), nbr = N.map(()=>new Set());
E.forEach((e,ei)=>{
  outAdj[e.s].push(ei); inAdj[e.t].push(ei);
  nbr[e.s].add(e.t); nbr[e.t].add(e.s);
});

/* ---------- state ---------- */
const FACET_LABELS = {_label:"节点类型",subsystem:"子系统",physics_domain:"物理域",
  network_level:"网络层级",knowledge_domain:"知识域",importance:"重要度"};
const FACET_ORDER = ["_label","subsystem","physics_domain","network_level","importance","knowledge_domain"];
const facetSel = {};                                   // key -> Set(enabled values) ; empty = all
FACET_ORDER.forEach(k=>{ if(DATA.facets[k]) facetSel[k]=new Set(); });
const relSel = new Set();                              // enabled relation types ; empty = all
let query = "";
let selected = -1, hover = -1;

/* node sizing (by degree) */
const maxDeg = Math.max(1, ...N.map(n=>n.deg));
const radius = n => 4 + 7*Math.sqrt(n.deg/maxDeg);

/* ---------- visibility ---------- */
let visNode = new Uint8Array(N.length);
let visEdge = new Uint8Array(E.length);
let matchNode = new Uint8Array(N.length);  // matches text query
let visibleCount = 0;

function passFacets(n){
  for(const k in facetSel){
    const sel = facetSel[k];
    if(sel.size===0) continue;
    const v = k==="_label" ? n.lab : n.p[k];
    if(!sel.has(String(v))) return false;
  }
  return true;
}
function matchesQuery(n){
  if(!query) return true;
  const hay = (n.name+" "+(n.p.knowledge_domain||"")+" "+(n.p.description||"")
              +" "+(n.p.subsystem||"")+" "+(n.p.physics_domain||"")).toLowerCase();
  return hay.indexOf(query)>=0;
}
function recompute(){
  visibleCount=0;
  for(let i=0;i<N.length;i++){
    const n=N[i];
    matchNode[i]= matchesQuery(n)?1:0;
    visNode[i]= (passFacets(n) && matchNode[i])?1:0;
    if(visNode[i]) visibleCount++;
  }
  for(let i=0;i<E.length;i++){
    const e=E[i];
    const relOk = relSel.size===0 || relSel.has(e.r);
    visEdge[i]= (relOk && visNode[e.s] && visNode[e.t])?1:0;
  }
  document.getElementById("empty").style.display = visibleCount? "none":"flex";
}

/* ============================================================= CANVAS */
const cv=document.getElementById("cv"), ctx=cv.getContext("2d"), stage=document.getElementById("stage");
let DPR=Math.min(window.devicePixelRatio||1,2);
let view={x:0,y:0,s:1};      // pan + scale (world->screen)
let W=0,H=0;

function resize(){
  const r=stage.getBoundingClientRect();
  W=r.width; H=r.height;
  cv.width=W*DPR; cv.height=H*DPR;
  ctx.setTransform(DPR,0,0,DPR,0,0);
  draw();
}
function worldToScreen(x,y){ return [x*view.s+view.x, y*view.s+view.y]; }
function screenToWorld(px,py){ return [(px-view.x)/view.s, (py-view.y)/view.s]; }

function fit(){
  let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9,any=false;
  for(let i=0;i<N.length;i++){ if(!visNode[i])continue; any=true;
    const n=N[i]; if(n.x<minX)minX=n.x; if(n.y<minY)minY=n.y;
    if(n.x>maxX)maxX=n.x; if(n.y>maxY)maxY=n.y; }
  if(!any){ minX=0;minY=0;maxX=1000;maxY=1000; }
  const pad=70, w=Math.max(1,maxX-minX), h=Math.max(1,maxY-minY);
  const s=Math.min((W-2*pad)/w,(H-2*pad)/h);
  view.s=Math.max(.05,Math.min(s,4));
  view.x=(W-(minX+maxX)*view.s)/2;
  view.y=(H-(minY+maxY)*view.s)/2;
  draw();
}

function isHi(i){            // is node "highlighted" (focus context)?
  if(selected>=0) return i===selected || nbr[selected].has(i);
  if(hover>=0)    return i===hover    || nbr[hover].has(i);
  return false;
}
function anyFocus(){ return selected>=0 || hover>=0; }
function focusNode(){ return selected>=0?selected:hover; }

function draw(){
  ctx.clearRect(0,0,W,H);
  const focus=anyFocus(), fn=focusNode();

  /* edges */
  ctx.lineWidth=1;
  for(let i=0;i<E.length;i++){
    if(!visEdge[i]) continue;
    const e=E[i], a=N[e.s], b=N[e.t];
    const [ax,ay]=worldToScreen(a.x,a.y), [bx,by]=worldToScreen(b.x,b.y);
    let hot=false;
    if(focus){ hot=(e.s===fn||e.t===fn); }
    if(focus && !hot){ ctx.globalAlpha=.05; ctx.strokeStyle="#3a4a59"; }
    else if(hot){ ctx.globalAlpha=.85; ctx.strokeStyle="#ffb84d"; }
    else { ctx.globalAlpha= query && (!matchNode[e.s]||!matchNode[e.t]) ? .12 : .22;
           ctx.strokeStyle="#46627c"; }
    ctx.beginPath(); ctx.moveTo(ax,ay); ctx.lineTo(bx,by); ctx.stroke();
    if(hot && view.s>0.18) drawArrow(ax,ay,bx,by,radius(b)*view.s);
  }
  ctx.globalAlpha=1;

  /* nodes */
  const labelOn=document.getElementById("optLabels").checked;
  for(let i=0;i<N.length;i++){
    if(!visNode[i]) continue;
    const n=N[i], [x,y]=worldToScreen(n.x,n.y), r=Math.max(2,radius(n)*view.s);
    let alpha=1;
    if(focus && !isHi(i)) alpha=.14;
    else if(query && !matchNode[i]) alpha=.2;
    ctx.globalAlpha=alpha;
    ctx.beginPath(); ctx.arc(x,y,r,0,6.2832);
    ctx.fillStyle=colorOf(n); ctx.fill();
    if(i===selected){ ctx.lineWidth=2.5; ctx.strokeStyle="#fff"; ctx.stroke(); }
    else if(i===hover){ ctx.lineWidth=2; ctx.strokeStyle="#ffb84d"; ctx.stroke(); }
    ctx.globalAlpha=1;
  }

  /* labels (drawn after, to sit on top) — kept sparse for low render load */
  if(labelOn || focus){
    ctx.font="11px "+getComputedStyle(document.body).getPropertyValue("--mono");
    ctx.textBaseline="middle";
    for(let i=0;i<N.length;i++){
      if(!visNode[i]) continue;
      const n=N[i], r=Math.max(2,radius(n)*view.s);
      const show = (i===selected)||(i===hover)||(focus&&isHi(i))
                 || (labelOn && (view.s>0.55 || r>7));
      if(!show) continue;
      if(focus && !isHi(i)) continue;
      const [x,y]=worldToScreen(n.x,n.y);
      const t=n.name.length>26?n.name.slice(0,25)+"…":n.name;
      ctx.lineWidth=3; ctx.strokeStyle="rgba(8,12,17,.92)";
      ctx.strokeText(t,x+r+4,y);
      ctx.fillStyle=(i===selected||i===hover)?"#fff":"#c6d3df";
      ctx.fillText(t,x+r+4,y);
    }
  }
  document.getElementById("zlabel").textContent=Math.round(view.s*100)+"%";
}
function drawArrow(ax,ay,bx,by,rb){
  const dx=bx-ax,dy=by-ay,L=Math.hypot(dx,dy)||1,ux=dx/L,uy=dy/L;
  const ex=bx-ux*(rb+2),ey=by-uy*(rb+2),sz=6;
  ctx.fillStyle="#ffb84d";
  ctx.beginPath();
  ctx.moveTo(ex,ey);
  ctx.lineTo(ex-ux*sz-uy*sz*.55, ey-uy*sz+ux*sz*.55);
  ctx.lineTo(ex-ux*sz+uy*sz*.55, ey-uy*sz-ux*sz*.55);
  ctx.closePath(); ctx.fill();
}

/* ---------- hit testing ---------- */
function pick(px,py){
  let best=-1,bd=1e9;
  for(let i=0;i<N.length;i++){
    if(!visNode[i]) continue;
    const n=N[i],[x,y]=worldToScreen(n.x,n.y);
    const r=Math.max(4,radius(n)*view.s)+3;
    const d=(x-px)*(x-px)+(y-py)*(y-py);
    if(d<=r*r && d<bd){ bd=d; best=i; }
  }
  return best;
}

/* ---------- interaction ---------- */
let dragging=false,moved=false,lastX=0,lastY=0;
cv.addEventListener("mousedown",e=>{dragging=true;moved=false;lastX=e.clientX;lastY=e.clientY;cv.classList.add("drag");});
window.addEventListener("mouseup",()=>{dragging=false;cv.classList.remove("drag");});
cv.addEventListener("mousemove",e=>{
  const r=cv.getBoundingClientRect(), px=e.clientX-r.left, py=e.clientY-r.top;
  if(dragging){
    const dx=e.clientX-lastX,dy=e.clientY-lastY;
    if(Math.abs(dx)+Math.abs(dy)>2) moved=true;
    view.x+=dx; view.y+=dy; lastX=e.clientX; lastY=e.clientY; draw(); hideTip(); return;
  }
  const h=pick(px,py);
  if(h!==hover){ hover=h; draw(); }
  if(h>=0){ showTip(e.clientX,e.clientY,N[h]); } else hideTip();
});
cv.addEventListener("mouseleave",()=>{ if(hover>=0){hover=-1;draw();} hideTip(); });
cv.addEventListener("click",e=>{
  if(moved) return;
  const r=cv.getBoundingClientRect();
  const h=pick(e.clientX-r.left,e.clientY-r.top);
  if(h>=0) select(h); else deselect();
});
cv.addEventListener("wheel",e=>{
  e.preventDefault();
  const r=cv.getBoundingClientRect(), px=e.clientX-r.left, py=e.clientY-r.top;
  const [wx,wy]=screenToWorld(px,py);
  const f=Math.exp(-e.deltaY*0.0012);
  view.s=Math.max(.05,Math.min(view.s*f,6));
  view.x=px-wx*view.s; view.y=py-wy*view.s;
  draw(); hideTip();
},{passive:false});

const tip=document.getElementById("tip");
function showTip(cx,cy,n){
  tip.style.display="block";
  tip.innerHTML="<b style='color:"+colorOf(n)+"'>"+esc(n.name)+"</b> · "+esc(n.lab)+" · deg "+n.deg;
  const r=stage.getBoundingClientRect();
  tip.style.left=(cx-r.left+14)+"px"; tip.style.top=(cy-r.top+14)+"px";
}
function hideTip(){ tip.style.display="none"; }

document.getElementById("zin").onclick =()=>zoomBtn(1.25);
document.getElementById("zout").onclick=()=>zoomBtn(0.8);
document.getElementById("fit").onclick =fit;
function zoomBtn(f){ const px=W/2,py=H/2,[wx,wy]=screenToWorld(px,py);
  view.s=Math.max(.05,Math.min(view.s*f,6)); view.x=px-wx*view.s; view.y=py-wy*view.s; draw(); }

/* ============================================================= DETAIL */
const detail=document.getElementById("detail");
function select(i){
  selected=i; hover=-1; renderDetail(N[i]); detail.classList.add("open"); draw();
}
function deselect(){ selected=-1; detail.classList.remove("open"); draw(); }
document.getElementById("dclose").onclick=deselect;

function renderDetail(n){
  const c=colorOf(n);
  const badge=document.getElementById("dbadge");
  badge.textContent=n.lab; badge.style.background=c;
  document.getElementById("dname").textContent=n.name;

  const dd=document.getElementById("ddesc");
  if(n.p.description){ dd.style.display="block";
    document.getElementById("ddesctext").textContent=n.p.description; }
  else dd.style.display="none";

  const order=["id","knowledge_domain","subsystem","physics_domain","network_level","importance"];
  const labels={id:"ID",knowledge_domain:"知识域",subsystem:"子系统",physics_domain:"物理域",
                network_level:"网络层级",importance:"重要度"};
  let kv="";
  order.forEach(k=>{ if(n.p[k]!==undefined&&n.p[k]!=="")
    kv+="<div class='k'>"+labels[k]+"</div><div class='v'>"+esc(String(n.p[k]))+"</div>"; });
  kv+="<div class='k'>degree</div><div class='v'>"+n.deg+"</div>";
  document.getElementById("dprops").innerHTML=kv;

  const out=outAdj[n.i], inc=inAdj[n.i];
  document.getElementById("douth").textContent="关系 · 出 → ("+out.length+")";
  document.getElementById("dinh").textContent="关系 · 入 ← ("+inc.length+")";
  document.getElementById("dout").innerHTML=relRows(out,true);
  document.getElementById("din").innerHTML =relRows(inc,false);
  detail.scrollTop=0;
}
function relRows(eids,isOut){
  if(!eids.length) return "<div style='color:var(--faint);font-size:12px'>—</div>";
  return eids.map(ei=>{
    const e=E[ei], other=N[isOut?e.t:e.s];
    return "<div class='rel' data-i='"+other.i+"'>"+
           "<span class='sw' style='background:"+colorOf(other)+"'></span>"+
           "<span class='arr'>"+esc(e.r)+"</span>"+
           "<span class='nm'>"+esc(other.name)+"</span></div>";
  }).join("");
}
detail.addEventListener("click",e=>{
  const row=e.target.closest(".rel"); if(!row) return;
  select(parseInt(row.dataset.i,10));
});

/* ============================================================= SIDEBAR */
function buildSidebar(){
  const side=document.getElementById("side");
  let html="";
  html+="<div class='toolbar'><button id='reset'>重置筛选</button><button id='fit2'>适配视图</button></div>";
  html+="<label class='chk'><input type='checkbox' id='optLabels'> 显示节点标签</label>";

  FACET_ORDER.forEach(key=>{
    const f=DATA.facets[key]; if(!f) return;
    const entries=Object.entries(f).sort((a,b)=>b[1]-a[1]);
    const many=entries.length>12;
    const open = key==="_label" ? "" : (many?" closed":"");
    html+="<div class='facet"+open+"' data-k='"+key+"'>";
    html+="<h4><span class='car'>▾</span>"+FACET_LABELS[key]+
          " <span style='color:var(--faint);font-weight:400'>("+entries.length+")</span></h4>";
    html+="<div class='body'>";
    if(many) html+="<input class='mini' placeholder='过滤…' data-fk='"+key+"'>";
    entries.forEach(([val,ct])=>{
      const sw = key==="_label"
        ? "<span class='sw' style='background:"+(COLOR[val]||'#888')+"'></span>" : "";
      html+="<label class='opt' data-fk='"+key+"' data-v='"+escAttr(val)+"'>"+
            "<input type='checkbox'>"+sw+
            "<span class='nm' title='"+escAttr(val)+"'>"+esc(val)+"</span>"+
            "<span class='ct'>"+ct+"</span></label>";
    });
    html+="</div></div>";
  });

  /* relation types */
  const rels=DATA.relTypes;
  html+="<div class='facet closed' data-k='__rel'>";
  html+="<h4><span class='car'>▾</span>关系类型 <span style='color:var(--faint);font-weight:400'>("+rels.length+")</span></h4>";
  html+="<div class='body'><input class='mini' placeholder='过滤…' data-fk='__rel'>";
  rels.forEach(rt=>{
    html+="<label class='opt' data-fk='__rel' data-v='"+escAttr(rt)+"'>"+
          "<input type='checkbox'><span class='nm' title='"+escAttr(rt)+"'>"+esc(rt)+"</span></label>";
  });
  html+="</div></div>";
  side.innerHTML=html;

  /* wire facet collapse */
  side.querySelectorAll(".facet h4").forEach(h=>{
    h.onclick=()=>h.parentElement.classList.toggle("closed");
  });
  /* wire option toggles */
  side.querySelectorAll(".opt").forEach(opt=>{
    opt.addEventListener("click",ev=>{
      if(ev.target.tagName!=="INPUT"){ const box=opt.querySelector("input"); box.checked=!box.checked; }
      const k=opt.dataset.fk, v=opt.dataset.v, on=opt.querySelector("input").checked;
      const set = k==="__rel"?relSel:facetSel[k];
      if(on) set.add(v); else set.delete(v);
      apply();
    });
  });
  /* mini text filters inside facets */
  side.querySelectorAll(".mini").forEach(inp=>{
    inp.addEventListener("input",()=>{
      const q=inp.value.toLowerCase(), k=inp.dataset.fk;
      side.querySelectorAll(".opt[data-fk='"+CSS.escape(k)+"']").forEach(o=>{
        o.style.display=o.dataset.v.toLowerCase().indexOf(q)>=0?"":"none";
      });
    });
  });
  document.getElementById("reset").onclick=resetFilters;
  document.getElementById("fit2").onclick=fit;
  document.getElementById("optLabels").addEventListener("change",draw);
}
function resetFilters(){
  for(const k in facetSel) facetSel[k].clear();
  relSel.clear(); query=""; 
  document.getElementById("q").value="";
  document.getElementById("qx").style.display="none";
  document.querySelectorAll(".opt input").forEach(b=>b.checked=false);
  apply(); fit();
}

/* legend + stats */
function buildLegend(){
  document.getElementById("legend").innerHTML =
    DATA.labels.map(l=>"<div class='row'><span class='sw' style='background:"+COLOR[l]+"'></span>"+esc(l)+"</div>").join("");
  const s=DATA.stats;
  document.getElementById("stats").innerHTML=
    "<span><b>"+s.nodes+"</b> 节点</span><span><b>"+s.edges+"</b> 关系</span>"+
    "<span><b>"+s.nodeTypes+"</b> 类型</span><span><b>"+s.relTypes+"</b> 关系类型</span>";
}

/* ============================================================= TABLE */
let sortKey="deg", sortDir=-1;
function buildTable(){
  const cols=[["name","名称"],["lab","类型"],["subsystem","子系统"],
              ["physics_domain","物理域"],["network_level","层级"],
              ["knowledge_domain","知识域"],["deg","度"],["description","描述"]];
  const rows=[];
  for(let i=0;i<N.length;i++){ if(visNode[i]) rows.push(N[i]); }
  const getv=(n,k)=> k==="name"?n.name : k==="lab"?n.lab : k==="deg"?n.deg : (n.p[k]??"");
  rows.sort((a,b)=>{
    let va=getv(a,sortKey), vb=getv(b,sortKey);
    if(typeof va==="number"&&typeof vb==="number") return (va-vb)*sortDir;
    return String(va).localeCompare(String(vb),"zh")*sortDir;
  });
  let h="<table><thead><tr>";
  cols.forEach(([k,lab])=>{
    const ar=k===sortKey?(sortDir>0?" ▲":" ▼"):"";
    h+="<th data-k='"+k+"'>"+lab+"<span class='ar'>"+ar+"</span></th>";
  });
  h+="</tr></thead><tbody>";
  rows.forEach(n=>{
    h+="<tr data-i='"+n.i+"'>";
    h+="<td>"+esc(n.name)+"</td>";
    h+="<td><span class='pill' style='background:"+colorOf(n)+"'>"+esc(n.lab)+"</span></td>";
    h+="<td>"+esc(n.p.subsystem||"")+"</td>";
    h+="<td>"+esc(n.p.physics_domain||"")+"</td>";
    h+="<td class='num'>"+esc(String(n.p.network_level??""))+"</td>";
    h+="<td>"+esc(n.p.knowledge_domain||"")+"</td>";
    h+="<td class='num'>"+n.deg+"</td>";
    h+="<td class='tdesc'>"+esc((n.p.description||"").slice(0,140))+"</td>";
    h+="</tr>";
  });
  h+="</tbody></table>";
  const wrap=document.getElementById("tablewrap");
  wrap.innerHTML=h;
  wrap.querySelectorAll("th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k;
    if(k===sortKey) sortDir=-sortDir; else {sortKey=k; sortDir=(k==="deg")?-1:1;}
    buildTable();
  });
  wrap.querySelectorAll("tr[data-i]").forEach(tr=>tr.onclick=()=>{
    const i=parseInt(tr.dataset.i,10); showGraph(); select(i); fitNode(i);
  });
}
function fitNode(i){ const n=N[i]; view.s=Math.max(view.s,1.1);
  view.x=W/2-n.x*view.s; view.y=H/2-n.y*view.s; draw(); }

/* view switch */
const tablewrap=document.getElementById("tablewrap");
function showGraph(){ tablewrap.style.display="none";
  document.getElementById("vGraph").classList.add("on");
  document.getElementById("vTable").classList.remove("on"); draw(); }
function showTable(){ buildTable(); tablewrap.style.display="block";
  document.getElementById("vTable").classList.add("on");
  document.getElementById("vGraph").classList.remove("on"); }
document.getElementById("vGraph").onclick=showGraph;
document.getElementById("vTable").onclick=showTable;

/* ---------- search ---------- */
const qbox=document.getElementById("q"), qx=document.getElementById("qx");
let qtimer=null;
qbox.addEventListener("input",()=>{
  qx.style.display=qbox.value?"block":"none";
  clearTimeout(qtimer);
  qtimer=setTimeout(()=>{ query=qbox.value.trim().toLowerCase(); apply(); },120);
});
qx.onclick=()=>{ qbox.value=""; query=""; qx.style.display="none"; apply(); };

/* ---------- apply (recompute + redraw current view) ---------- */
function apply(){
  recompute();
  if(selected>=0 && !visNode[selected]) deselect();
  if(tablewrap.style.display==="block") buildTable(); else draw();
}

/* ---------- utils ---------- */
function esc(s){ return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function escAttr(s){ return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

/* ---------- boot ---------- */
buildSidebar(); buildLegend(); recompute();
window.addEventListener("resize",resize);
resize(); fit();
</script>
</body>
</html>
"""



# ----------------------------------------------------------------------------- #
#  CLI
# ----------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a Neo4j Aura CSV export into an offline interactive HTML graph."
    )
    ap.add_argument("csv", help="Path to the exported CSV file.")
    ap.add_argument("-o", "--output", default=None,
                    help="Output HTML path (default: <csv basename>.html).")
    ap.add_argument("--title", default="Knowledge Graph Explorer",
                    help="Title shown in the viewer.")
    ap.add_argument("--seed", type=int, default=7, help="Layout random seed.")
    args = ap.parse_args(argv)

    out_path = args.output or re.sub(r"\.csv$", "", args.csv, flags=re.I) + ".html"

    print("Reading %s ..." % args.csv)
    nodes, edges = load_graph(args.csv)
    print("  nodes: %d   edges: %d" % (len(nodes), len(edges)))

    print("Computing layout (one-time, baked into the file) ...")
    pos = compute_layout(nodes, edges, seed=args.seed)

    print("Building HTML ...")
    payload = build_payload(nodes, edges, pos, args.title)
    page = render_html(payload)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)

    size_kb = len(page.encode("utf-8")) / 1024.0
    print("Wrote %s (%.0f KB) — open it directly in any browser, fully offline."
          % (out_path, size_kb))
    return out_path


if __name__ == "__main__":
    main()
