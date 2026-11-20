"""Rewrite the marked blocks in the docs from today's facts.

Only text between a start and an end marker is touched:

    <!-- auto:start reads model=d_contracted_market_basket -->
    ...generated text...
    <!-- auto:end -->

In notebooks, a markdown cell whose metadata has "docs_sync": "model-sql model=X"
is replaced the same way. Everything else on the page stays hand-written.

Block types:
  reads         model=X          Bronze sources and Silver models the model reads
  model-sql     model=X          the dbt model SQL
  lineage       model=X [depth]  mermaid graph of everything upstream of X
  gold-columns  table=X          Gold column table; keeps hand-written columns (merge)
  cluster-by    table=X          the Gold clustering key
  screen-sql    query=Repo.meth  the SQL a screen runs through the Tenant API
  extract-sql   extract=key      the SQL a Bronze extract runs
"""
from pathlib import Path
import json
import re

from .common import log, read_json, read_text

BLOCK_RE = re.compile(r"(<!--\s*auto:start\s+([\w-]+)([^>]*)-->)(.*?)(<!--\s*auto:end\s*-->)", re.S)


class RenderError(Exception):
    pass


def _args(raw):
    return dict(re.findall(r"([\w-]+)=([^\s]+)", raw))


def _code(lang, text):
    return f"```{lang}\n{text.strip()}\n```"


class Renderer:
    def __init__(self, facts):
        self.silver = facts.get("silver") or {}
        self.gold = (facts.get("gold") or {}).get("tables", {})
        self.bronze = facts.get("bronze") or {}
        self.queries = (facts.get("screens") or {}).get("queries", {})

    def _model(self, a):
        name = a.get("model")
        if name not in self.silver:
            raise RenderError(f"unknown dbt model '{name}'")
        return self.silver[name]

    def _table(self, a):
        name = (a.get("table") or "").lower()
        if name not in self.gold:
            raise RenderError(f"unknown Gold table '{a.get('table')}'")
        return name, self.gold[name]

    def render(self, kind, a, old, page_dir=None):
        fn = getattr(self, "r_" + kind.replace("-", "_"), None)
        if not fn:
            raise RenderError(f"unknown block type '{kind}'")
        self.page_dir = page_dir
        return fn(a, old)

    def _link(self, name):
        """`name`, linked to its sibling entity page when that page exists."""
        if self.page_dir and (self.page_dir / f"{name}.md").exists():
            return f"[`{name}`]({name}.md)"
        return f"`{name}`"

    def r_reads(self, a, old):
        """What the model reads; add readby=yes to also list the models that read it."""
        m = self._model(a)
        bronze = ", ".join(f"`{s.split('.')[-1]}`" for s in m["sources"]) or "none"
        silver = ", ".join(self._link(r) for r in m["refs"]) or "none"
        text = f"Bronze sources: {bronze}.\n\nSilver models: {silver}."
        if a.get("readby") == "yes":
            users = sorted(n for n, x in self.silver.items() if a["model"] in x["refs"])
            text += "\n\nRead by: " + (", ".join(self._link(u) for u in users) or "none") + "."
        return text

    def r_model_sql(self, a, old):
        return _code("sql", self._model(a)["sql"])

    def r_lineage(self, a, old):
        target = a.get("model")
        self._model(a)
        depth = int(a.get("depth", 99))
        edges, seen, frontier = set(), {target}, [(target, 0)]
        while frontier:
            node, d = frontier.pop()
            if d >= depth or node not in self.silver:
                continue
            for src in self.silver[node]["sources"]:
                edges.add((f"bronze: {src.split('.')[-1]}", node))
            for ref in self.silver[node]["refs"]:
                edges.add((ref, node))
                if ref not in seen:
                    seen.add(ref)
                    frontier.append((ref, d + 1))
        ids = {}

        def nid(label):
            ids.setdefault(label, f"N{len(ids) + 1}")
            return ids[label]

        lines = ["```mermaid", "flowchart LR"]
        for a_, b_ in sorted(edges):
            lines.append(f'    {nid(a_)}["{a_}"] --> {nid(b_)}["{b_}"]')
        lines.append("```")
        return "\n".join(lines)

    def r_cluster_by(self, a, old):
        _, t = self._table(a)
        return ", ".join(f"`{c}`" for c in t.get("cluster_by") or []) or "none"

    def r_screen_sql(self, a, old):
        q = self.queries.get(a.get("query"))
        if not q:
            raise RenderError(f"unknown Tenant API query '{a.get('query')}'")
        sql = re.sub(r"\s+(FROM|WHERE|AND|OR|GROUP BY|ORDER BY|LIMIT|JOIN|LEFT JOIN|INNER JOIN)\s+",
                     lambda m: "\n" + m.group(1) + " ", q["sql"])
        return _code("sql", sql)

    def r_extract_sql(self, a, old):
        e = self.bronze.get(a.get("extract"))
        if not e:
            raise RenderError(f"unknown extract '{a.get('extract')}'")
        return _code("sql", e["sql"])

    def r_gold_columns(self, a, old):
        """Merge: Snowflake owns Column and Type (and Table); people own every other column."""
        name, t = self._table(a)
        rows = [l for l in old.strip().split("\n") if l.strip().startswith("|")]
        if rows:
            head = [h.strip() for h in rows[0].strip().strip("|").split("|")]
            body = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows[2:]]
        else:
            head, body = ["Column", "Type", "What it is used for"], []
        low = [h.lower() for h in head]
        if "column" not in low or "type" not in low:
            raise RenderError("gold-columns block needs a table with Column and Type headers")
        ci, ti = low.index("column"), low.index("type")
        tab = low.index("table") if "table" in low else None
        keep = {}
        for r in body:
            r += [""] * (len(head) - len(r))
            keep[r[ci].strip("`").lower()] = r
        # page order is kept (people sort these by importance); columns new in Snowflake
        # go at the end, in table order; columns dropped from Snowflake leave the table
        if a.get("order") == "table":
            order = list(t["columns"])
        else:
            order = [c for c in keep if c in t["columns"]] + [c for c in t["columns"] if c not in keep]
        out = []
        for col in order:
            info = t["columns"][col]
            r = list(keep.get(col) or [""] * len(head))
            if col not in keep:
                for i in range(len(head)):
                    if i not in (ci, ti, tab):
                        r[i] = "To describe"
            r[ci] = f"`{col}`"
            r[ti] = f"`{info['type']}`" if info["type"] else ""
            if tab is not None:
                r[tab] = f"`{name.upper()}`"
            out.append(r)
        lines = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
        lines += ["|" + "|".join(f" {c} " if c else " " for c in r) + "|" for r in out]
        return "\n".join(lines)


def _update_markdown(text, renderer, page, errors, page_dir=None):
    def sub(m):
        start, kind, raw, old, end = m.groups()
        try:
            new = renderer.render(kind, _args(raw), old, page_dir)
        except RenderError as exc:
            errors.append(f"{page}: {kind}: {exc}")
            return m.group(0)
        return f"{start}\n{new}\n{end}"

    return BLOCK_RE.sub(sub, text)


def _update_notebook(text, renderer, page, errors, page_dir=None):
    nb = json.loads(text)
    changed = False
    for cell in nb.get("cells", []):
        spec = (cell.get("metadata") or {}).get("docs_sync")
        if not spec:
            continue
        kind, *rest = spec.split()
        old = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
        try:
            new = renderer.render(kind, _args(" ".join(rest)), old, page_dir)
        except RenderError as exc:
            errors.append(f"{page}: {kind}: {exc}")
            continue
        if new != old:
            parts = new.split("\n")
            cell["source"] = [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
            changed = True
    if not changed:
        return text
    return json.dumps(nb, indent=1, ensure_ascii=True) + ("\n" if text.endswith("\n") else "")


def run(cfg):
    cur = Path(cfg["facts"]["current"])
    facts = {l: read_json(cur / f"{l}.json", {}) for l in ("silver", "gold", "bronze", "screens")}
    renderer = Renderer(facts)
    root = Path(cfg["docs"]["root"])
    products = cfg["docs"].get("products") or [""]
    changed, errors = [], []
    for product in products:
        for f in sorted((root / product).rglob("*")):
            if f.suffix not in (".md", ".ipynb"):
                continue
            text = read_text(f)
            if "auto:start" not in text and "docs_sync" not in text:
                continue
            page = str(f.relative_to(root))
            new = (_update_notebook if f.suffix == ".ipynb" else _update_markdown)(text, renderer, page, errors, f.parent)
            if new != text:
                f.write_text(new, encoding="utf-8")
                changed.append(page)
    for e in errors:
        log("update: ERROR " + e)
    log(f"update: {len(changed)} pages rewritten")
    return {"changed": changed, "errors": errors}
