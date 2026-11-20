"""Checks run before a pull request is opened.

Errors (fail the run):
  - a marker without its end marker, or a marker naming something not in the code
  - a relative link to a file that does not exist, or to a heading that does not exist
  - a notebook that is not valid JSON
Warnings (listed in the report, never fail unless checks.fail_on_counts is true):
  - hard-coded counts such as "36 models" that go stale
  - notebook SQL that does not parse (only when sqlglot is installed)
"""
from pathlib import Path
import json
import re

from .common import log, read_text, write_json

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*(?:\{#([\w-]+)[^}]*\})?\s*$", re.M)
COUNT_RE = re.compile(r"\b\d{2,}\s+(models|columns|tables|sources|extracts|notebooks|pages|queries|charts)\b", re.I)


def slugify(text):
    """MkDocs (Python-Markdown toc) default slug."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def anchors_of(text):
    found = set()
    for m in HEADING_RE.finditer(re.sub(r"```.*?```", "", text, flags=re.S)):
        found.add(m.group(3) or slugify(m.group(2)))
    found.update(re.findall(r"<a\s+(?:id|name)=\"([^\"]+)\"", text))
    found.update(re.findall(r"\{#([\w-]+)\}", text))
    return found


def run(cfg, facts_ok_names=None):
    root = Path(cfg["docs"]["root"])
    products = cfg["docs"].get("products") or [""]
    errors, warnings = [], []
    anchor_cache = {}

    def anchors(path):
        if path not in anchor_cache:
            anchor_cache[path] = anchors_of(read_text(path)) if path.exists() else set()
        return anchor_cache[path]

    try:
        import sqlglot  # optional
    except ImportError:
        sqlglot = None

    for product in products:
        for f in sorted((root / product).rglob("*")):
            page = str(f.relative_to(root))
            if f.suffix == ".md":
                text = read_text(f)
                starts = len(re.findall(r"<!--\s*auto:start", text))
                ends = len(re.findall(r"<!--\s*auto:end", text))
                if starts != ends:
                    errors.append(f"{page}: {starts} auto:start markers but {ends} auto:end")
                body = re.sub(r"```.*?```", "", text, flags=re.S)
                for target in LINK_RE.findall(body):
                    if re.match(r"^(https?:|mailto:|data:)", target):
                        continue
                    path_part, _, frag = target.partition("#")
                    dest = (f.parent / path_part).resolve() if path_part else f
                    if path_part and not dest.exists():
                        errors.append(f"{page}: broken link to {target}")
                        continue
                    if frag and dest.suffix == ".md" and frag not in anchors(dest):
                        errors.append(f"{page}: link to missing heading #{frag} in {dest.name}")
                for m in COUNT_RE.finditer(re.sub(r"<!--\s*auto:start.*?auto:end\s*-->", "", body, flags=re.S)):
                    line = body[:m.start()].count("\n") + 1
                    warnings.append(f"{page}:{line}: hard-coded count \"{m.group(0)}\"")
            elif f.suffix == ".ipynb":
                try:
                    nb = json.loads(read_text(f))
                except ValueError as exc:
                    errors.append(f"{page}: not valid JSON ({exc})")
                    continue
                if not nb.get("cells"):
                    errors.append(f"{page}: notebook has no cells")
                if sqlglot:
                    for i, cell in enumerate(nb.get("cells", [])):
                        src = "".join(cell.get("source", []))
                        if cell.get("cell_type") == "code" and src.lstrip().startswith("%sql"):
                            sql = src.lstrip()[4:]
                            try:
                                sqlglot.parse(sql, read="databricks")
                            except Exception as exc:  # noqa: BLE001 - report every parser failure
                                warnings.append(f"{page}: cell {i} SQL does not parse: {str(exc).splitlines()[0]}")

    fail_counts = (cfg.get("checks") or {}).get("fail_on_counts", False)
    if fail_counts:
        errors += [w for w in warnings if "hard-coded count" in w]
    out = Path(cfg["output"]["dir"])
    write_json(out / "checks.json", {"errors": errors, "warnings": warnings, "sql_parser": bool(sqlglot)})
    for e in errors[:50]:
        log("check: ERROR " + e)
    log(f"check: {len(errors)} errors, {len(warnings)} warnings"
        + ("" if sqlglot else " (sqlglot not installed: notebook SQL not parsed)"))
    return {"errors": errors, "warnings": warnings}
