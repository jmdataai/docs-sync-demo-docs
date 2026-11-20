"""Compare today's facts with the last approved facts, find the pages each change
touches, and check the pages against the code.

How a page is identified for a change:
  1. Marker: the page has <!-- auto:start ... model=X --> for the changed item.
     That block is rewritten automatically by update_blocks.py.
  2. Mention: the page names the changed item (model, table, extract, query method,
     or a changed column together with its table). Listed for a person to review.

Writes out/drift.json and out/drift_report.md.
"""
from pathlib import Path
import difflib
import json
import re

from .common import log, read_json, read_text, write_json

LAYERS = ("silver", "gold", "bronze", "screens")
MARKER_RE = re.compile(r"<!--\s*auto:start\s+([\w-]+)([^>]*)-->")


# ---------------------------------------------------------------- doc index
class DocIndex:
    def __init__(self, docs_root, products):
        self.root = Path(docs_root)
        self.files = {}
        folders = [self.root / p for p in products] if products else [self.root]
        for folder in folders:
            for f in sorted(folder.rglob("*")):
                if f.suffix in (".md", ".ipynb") and f.is_file():
                    text = read_text(f)
                    self.files[str(f.relative_to(self.root))] = text
        self.lower = {k: v.lower() for k, v in self.files.items()}

    def mentions(self, *terms):
        """Pages that contain every term as a whole word."""
        pats = [re.compile(r"(?<![\w])" + re.escape(t.lower()) + r"(?![\w])") for t in terms if t]
        return sorted(p for p, text in self.lower.items() if pats and all(x.search(text) for x in pats))

    def markers(self):
        """[(page, block_type, {key: value})] for every marker in md and notebook cells."""
        out = []
        for page, text in self.files.items():
            for m in MARKER_RE.finditer(text):
                out.append((page, m.group(1), dict(re.findall(r"([\w-]+)=([^\s]+)", m.group(2)))))
            if page.endswith(".ipynb"):
                try:
                    nb = json.loads(text)
                except ValueError:
                    continue
                for cell in nb.get("cells", []):
                    spec = (cell.get("metadata") or {}).get("docs_sync")
                    if spec:
                        kind, *rest = spec.split()
                        out.append((page, kind, dict(x.split("=", 1) for x in rest if "=" in x)))
        return out


# ---------------------------------------------------------------- diffs
def _set_diff(old, new):
    old, new = set(old or []), set(new or [])
    return sorted(new - old), sorted(old - new)


def _lines(sql):
    lines = [l.strip() for l in (sql or "").splitlines() if l.strip()]
    if len(lines) > 2:
        return lines
    flat = re.sub(r"\s+", " ", sql or "")
    return [l.strip() for l in re.split(r"(?=\b(?:AND|OR|FROM|WHERE|GROUP BY|ORDER BY)\b)", flat) if l.strip()]


def _sql_diff(old, new, limit=12):
    a, b = _lines(old), _lines(new)
    lines = [l for l in difflib.unified_diff(a, b, lineterm="", n=0) if not l.startswith(("---", "+++", "@@"))]
    return lines[:limit] + (["..."] if len(lines) > limit else [])


def diff_silver(old, new):
    changes = []
    for name in sorted(set(old) | set(new)):
        o, n = old.get(name), new.get(name)
        if not o:
            changes.append({"layer": "silver", "kind": "added", "item": name, "terms": [name],
                            "details": [f"new model `{n['file']}`"]})
            continue
        if not n:
            changes.append({"layer": "silver", "kind": "removed", "item": name, "terms": [name], "details": []})
            continue
        det, cols = [], []
        add, rem = _set_diff(o["refs"], n["refs"])
        if add or rem:
            det.append(f"Silver models read: +{add} -{rem}")
        add, rem = _set_diff(o["sources"], n["sources"])
        if add or rem:
            det.append(f"Bronze sources read: +{add} -{rem}")
        if o["materialized"] != n["materialized"]:
            det.append(f"materialised `{o['materialized']}` -> `{n['materialized']}`")
        add, rem = _set_diff(o["columns"], n["columns"])
        if add or rem:
            det.append(f"documented columns: +{add} -{rem}")
            cols += add + rem
        if o["sql_sha"] != n["sql_sha"]:
            det.append("model SQL changed")
        if o["file"] != n["file"]:
            det.append(f"file moved `{o['file']}` -> `{n['file']}`")
        if det:
            changes.append({"layer": "silver", "kind": "changed", "item": name, "terms": [name],
                            "columns": cols, "details": det,
                            "sql_diff": _sql_diff(o["sql"], n["sql"]) if o["sql_sha"] != n["sql_sha"] else []})
    return changes


def diff_gold(old, new):
    changes = []
    ot, nt = old.get("tables", {}), new.get("tables", {})
    for name in sorted(set(ot) | set(nt)):
        o, n = ot.get(name), nt.get(name)
        if not o or not n:
            changes.append({"layer": "gold", "kind": "added" if n else "removed", "item": name.upper(),
                            "terms": [name], "details": []})
            continue
        det, cols = [], []
        add, rem = _set_diff(o["columns"], n["columns"])
        if add:
            det.append(f"columns added: {add}")
        if rem:
            det.append(f"columns removed: {rem}")
        cols += add + rem
        for c in sorted(set(o["columns"]) & set(n["columns"])):
            if o["columns"][c]["type"] != n["columns"][c]["type"]:
                det.append(f"`{c}` type `{o['columns'][c]['type']}` -> `{n['columns'][c]['type']}`")
                cols.append(c)
            if o["columns"][c].get("masking") != n["columns"][c].get("masking"):
                det.append(f"`{c}` masking `{o['columns'][c].get('masking')}` -> `{n['columns'][c].get('masking')}`")
                cols.append(c)
        if o.get("cluster_by") != n.get("cluster_by"):
            det.append(f"cluster key {o.get('cluster_by')} -> {n.get('cluster_by')}")
        if det:
            changes.append({"layer": "gold", "kind": "changed", "item": name.upper(), "terms": [name],
                            "columns": sorted(set(cols)), "details": det})
    op, np_ = old.get("procedures", {}), new.get("procedures", {})
    for f in sorted(set(op) | set(np_)):
        o, n = op.get(f), np_.get(f)
        if not o or not n or o["sha"] != n["sha"]:
            targets = (n or o)["targets"]
            changes.append({"layer": "gold", "kind": "added" if not o else "removed" if not n else "changed",
                            "item": f, "terms": [f.replace(".sql", "")] + targets,
                            "details": [f"load procedure for {targets or 'no COPY INTO target'}"]})
    return changes


def diff_bronze(old, new):
    changes = []
    for key in sorted(set(old) | set(new)):
        o, n = old.get(key), new.get(key)
        cur = n or o
        terms = [cur["bronze_table"] or key]
        if not o or not n:
            changes.append({"layer": "bronze", "kind": "added" if n else "removed", "item": key,
                            "terms": terms, "details": [f"writes `{cur['bronze_table']}`"]})
            continue
        det = []
        for field, label in (("sql_sha", "extract SQL changed"), ("source_table", "source table changed"),
                             ("bronze_table", "Bronze table name changed"), ("sql_path", "SQL file moved")):
            if o.get(field) != n.get(field):
                det.append(label if field == "sql_sha" else f"{label}: `{o.get(field)}` -> `{n.get(field)}`")
        if det:
            changes.append({"layer": "bronze", "kind": "changed", "item": key, "terms": terms, "details": det,
                            "sql_diff": _sql_diff(o["sql"], n["sql"]) if o["sql_sha"] != n["sql_sha"] else []})
    return changes


def diff_screens(old, new):
    changes = []
    oq, nq = old.get("queries", {}), new.get("queries", {})
    for key in sorted(set(oq) | set(nq)):
        o, n = oq.get(key), nq.get(key)
        cur = n or o
        terms = [cur["repository"]]
        if not o or not n:
            changes.append({"layer": "screens", "kind": "added" if n else "removed", "item": key,
                            "terms": terms, "details": [f"reads {cur['tables']}"]})
            continue
        if o["sql_sha"] != n["sql_sha"]:
            if o.get("own_sha") and o.get("own_sha") == n.get("own_sha"):
                continue  # only a shared constant changed: reported once, below
            add, rem = _set_diff(o["columns"], n["columns"])
            det = ["query SQL changed"] + ([f"columns used: +{add} -{rem}"] if add or rem else [])
            changes.append({"layer": "screens", "kind": "changed", "item": key, "terms": terms,
                            "tables": n["tables"], "details": det, "sql_diff": _sql_diff(o["sql"], n["sql"])})
    oc, nc = old.get("constants", {}), new.get("constants", {})
    for name in sorted(set(oc) | set(nc)):
        if oc.get(name) == nc.get(name):
            continue
        users = sorted(k for k, q in nq.items() if name in q["constants"])
        changes.append({"layer": "screens", "kind": "changed" if name in oc and name in nc else ("added" if name in nc else "removed"),
                        "item": f"shared SQL filter {name}", "terms": [name] + sorted({nq[u]["repository"] for u in users}),
                        "details": [f"used by {len(users)} queries: " + ", ".join(f"`{u}`" for u in users[:8])
                                    + (" and more" if len(users) > 8 else "")],
                        "sql_diff": _sql_diff(oc.get(name), nc.get(name))})
    oe, ne = old.get("experience", {}), new.get("experience", {})
    for f in sorted(set(oe) | set(ne)):
        if oe.get(f) != ne.get(f):
            stem = Path(f).stem
            changes.append({"layer": "screens", "kind": "changed" if f in oe and f in ne else ("added" if f in ne else "removed"),
                            "item": f"experience-api/{f}", "terms": [stem], "details": ["Experience API service changed"]})
    return changes


# ---------------------------------------------------------------- page checks
def _build_table(text):
    rows = {}
    for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", text, re.M):
        rows[m.group(1).strip()] = m.group(2).strip()
    return rows


def _ticks(text):
    return [t.split(".")[-1] for t in re.findall(r"`([^`]+)`", text or "")]


def verify_pages(index, silver, gold):
    """Facts that a page states today and the code disagrees with."""
    issues = []
    for page, text in index.files.items():
        if not page.endswith(".md"):
            continue
        h1 = re.search(r"^#\s+(.+)$", text, re.M)
        title = h1.group(1).strip().strip("`") if h1 else ""
        if "/03_Silver/entities/" in "/" + page and title in silver:
            model = silver[title]
            build = _build_table(text.split("## What it reads")[0])
            mf = (build.get("Model file") or "").strip("`")
            if mf and not model["file"].endswith(mf):
                issues.append((page, f"Model file says `{mf}`; the code has `{model['file']}`"))
            mat = (build.get("Materialisation") or "").strip("`")
            if mat and model["materialized"] and mat != model["materialized"]:
                issues.append((page, f"Materialisation says `{mat}`; the project builds `{model['materialized']}`"))
            reads = re.search(r"## What it reads(.*?)(\n## |\Z)", text, re.S)
            if reads:
                body = reads.group(1)
                bs = re.search(r"Bronze sources?:([^\n]*)", body)
                if bs:
                    stated = set(_ticks(bs.group(1)))
                    actual = {s.split(".")[-1] for s in model["sources"]}
                    if stated != actual:
                        issues.append((page, f"Bronze sources differ: page {sorted(stated)}, code {sorted(actual)}"))
                sm = re.search(r"Silver models?:([^\n]*)", body)
                if sm:
                    stated = set(_ticks(sm.group(1)))
                    if stated != set(model["refs"]):
                        issues.append((page, f"Silver models differ: page {sorted(stated)}, code {model['refs']}"))
        if "/04_Gold/entities/" in "/" + page and title.lower() in gold.get("tables", {}):
            table = gold["tables"][title.lower()]
            stated = {}
            for block in re.findall(r"((?:^\|.*\|\s*$\n?)+)", text, re.M):
                lines = [l for l in block.strip().split("\n") if not re.match(r"^\|\s*-", l)]
                head = [h.strip().lower() for h in lines[0].strip("|").split("|")]
                if "column" not in head or "type" not in head:
                    continue
                ci, ti = head.index("column"), head.index("type")
                tab = head.index("table") if "table" in head else None
                for l in lines[1:]:
                    cells = [c.strip().strip("`") for c in l.strip().strip("|").split("|")]
                    if len(cells) <= max(ci, ti):
                        continue
                    if tab is not None and cells[tab].upper() != title.upper():
                        continue
                    stated[cells[ci].lower()] = cells[ti].lower().replace(" ", "")
            if stated:
                actual = {c: v["type"].replace(" ", "") for c, v in table["columns"].items()}
                missing, extra = sorted(set(actual) - set(stated)), sorted(set(stated) - set(actual))
                if missing:
                    issues.append((page, f"columns in Snowflake but not on the page: {missing}"))
                if extra:
                    issues.append((page, f"columns on the page but not in Snowflake: {extra}"))
                for c in sorted(set(actual) & set(stated)):
                    if stated[c] and actual[c] and stated[c] != actual[c]:
                        issues.append((page, f"`{c}` type: page `{stated[c]}`, Snowflake `{actual[c]}`"))
    return issues


# ---------------------------------------------------------------- report
def run(cfg):
    cur_dir, app_dir = Path(cfg["facts"]["current"]), Path(cfg["facts"]["approved"])
    current = {l: read_json(cur_dir / f"{l}.json", {}) for l in LAYERS}
    approved = {l: read_json(app_dir / f"{l}.json") for l in LAYERS}
    baseline = all(v is None for v in approved.values())
    approved = {k: (v or {}) for k, v in approved.items()}

    index = DocIndex(cfg["docs"]["root"], cfg["docs"].get("products"))
    markers = index.markers()

    changes = [] if baseline else (
        diff_silver(approved["silver"], current["silver"]) + diff_gold(approved["gold"], current["gold"])
        + diff_bronze(approved["bronze"], current["bronze"]) + diff_screens(approved["screens"], current["screens"]))

    marker_keys = {"model", "table", "extract", "query"}
    for ch in changes:
        terms = [t for t in ch["terms"] if t]
        lowered = {x.lower() for x in terms + [ch["item"]]}
        mentioned = set()
        for t in terms:
            mentioned.update(index.mentions(t))
        # primary pages: the entity's own page or notebook, and pages naming a changed column
        primary = {p for p in mentioned if Path(p).stem.lower() in lowered}
        for col in ch.get("columns", []):
            for t in terms:
                primary.update(index.mentions(t, col))
        if ch["kind"] in ("added", "removed") or ch["layer"] == "screens":
            primary |= mentioned
        auto = sorted({p for p, kind, kv in markers
                       if any(kv.get(k, "").lower() in lowered for k in marker_keys)})
        ch["auto_pages"] = auto
        ch["review_pages"] = sorted(primary - set(auto))
        ch["also_mentioned"] = sorted(mentioned - primary - set(auto))

    issues = verify_pages(index, current["silver"], current["gold"])
    out = Path(cfg["output"]["dir"])
    report = {"baseline": baseline, "changes": changes,
              "page_issues": [{"page": p, "issue": i} for p, i in issues],
              "markers": len(markers)}
    write_json(out / "drift.json", report)
    (out / "drift_report.md").write_text(render_markdown(report), encoding="utf-8")
    log(f"compare: {len(changes)} changes, {len(issues)} page issues, {len(markers)} marked blocks"
        + (" (baseline run: no approved facts yet)" if baseline else ""))
    return report


def render_markdown(r):
    L = ["# Docs sync: drift report", ""]
    if r["baseline"]:
        L += ["This is the first run, so there is no approved snapshot to compare with. "
              "Merging this pull request stores today's facts as the approved baseline.", ""]
    else:
        L += [f"{len(r['changes'])} changes in the code since the last approved docs.", ""]
    names = {"silver": "Silver (dbt)", "gold": "Gold (Snowflake)", "bronze": "Bronze (extractor)",
             "screens": "Screens (Tenant and Experience API)"}
    for layer in LAYERS:
        items = [c for c in r["changes"] if c["layer"] == layer]
        if not items:
            continue
        L += [f"## {names[layer]}", ""]
        for c in items:
            L.append(f"### {c['kind'].capitalize()}: `{c['item']}`")
            L += [f"- {d}" for d in c["details"]]
            if c.get("sql_diff"):
                L += ["", "```diff"] + c["sql_diff"] + ["```"]
            if c.get("auto_pages"):
                L.append("- Updated automatically: " + ", ".join(f"`{p}`" for p in c["auto_pages"]))
            if c.get("review_pages"):
                L.append("- **Review by hand:** " + ", ".join(f"`{p}`" for p in c["review_pages"]))
            if c.get("also_mentioned"):
                L.append(f"- <details><summary>Also mentioned in {len(c['also_mentioned'])} pages</summary> "
                         + ", ".join(f"`{p}`" for p in c["also_mentioned"]) + "</details>")
            if not (c.get("auto_pages") or c.get("review_pages") or c.get("also_mentioned")):
                L.append("- No page mentions this yet. Decide whether the docs need it.")
            L.append("")
    if r["page_issues"]:
        L += ["## Pages that disagree with the code today", "",
              "These are checked on every run, changed or not.", "", "| Page | Issue |", "| --- | --- |"]
        L += [f"| `{i['page']}` | {i['issue'].replace('|', '/')} |" for i in r["page_issues"]]
        L.append("")
    L += ["## Reviewer checklist", "",
          "- [ ] The changes above match the release that triggered this run",
          "- [ ] Changed queries were run in Databricks (Silver) or Snowflake (Gold) and match the screen",
          "- [ ] Every page under **Review by hand** was read and updated where needed",
          "- [ ] Disagreements between the code, the PRA workbook and NocoDB were confirmed with the data owners",
          "- [ ] New findings and open questions were added to the quality pages", ""]
    return "\n".join(L)
