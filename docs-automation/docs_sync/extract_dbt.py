"""Silver facts from the dbt project.

Preferred input: target/manifest.json (+ catalog.json for column types), which the
dbt deploy pipeline already builds with `dbt docs generate`.
Fallback: read the dbt project files directly (model SQL + schema yml +
dbt_project.yml). The fallback needs no Databricks connection.

Output, one entry per model:
  name, file, materialized, tags, refs, sources, columns{name: {type, description}},
  sql (raw model SQL), sql_sha
"""
from pathlib import Path
import re

import yaml

from .common import log, read_json, read_text, sha

REF_RE = re.compile(r"\bref\s*\(\s*['\"]([\w.]+)['\"]\s*(?:,\s*['\"]([\w.]+)['\"]\s*)?\)")
SRC_RE = re.compile(r"\bsource\s*\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)")
CFG_MAT_RE = re.compile(r"config\s*\((?:[^)]*?)materialized\s*=\s*['\"](\w+)['\"]", re.S)
CFG_TAGS_RE = re.compile(r"config\s*\((?:[^)]*?)tags\s*=\s*\[([^\]]*)\]", re.S)


def _model_from_manifest(node, catalog_node):
    deps = node.get("depends_on", {}).get("nodes", [])
    refs = sorted(d.split(".")[-1] for d in deps if d.startswith("model."))
    sources = sorted(".".join(d.split(".")[-2:]) for d in deps if d.startswith("source."))
    columns = {}
    for cname, col in (node.get("columns") or {}).items():
        columns[cname.lower()] = {"type": col.get("data_type") or "", "description": col.get("description") or ""}
    for cname, col in ((catalog_node or {}).get("columns") or {}).items():
        entry = columns.setdefault(cname.lower(), {"type": "", "description": ""})
        entry["type"] = entry["type"] or col.get("type", "")
    sql = node.get("raw_code") or node.get("raw_sql") or ""
    return {
        "name": node["name"],
        "file": node.get("original_file_path", ""),
        "materialized": (node.get("config") or {}).get("materialized", ""),
        "tags": sorted(node.get("tags") or []),
        "refs": refs,
        "sources": sources,
        "columns": columns,
        "sql": sql,
        "sql_sha": sha(sql),
    }


def from_manifest(manifest_path, catalog_path=None, project_name=None):
    manifest = read_json(manifest_path)
    catalog = read_json(catalog_path, {}) if catalog_path else {}
    project_name = project_name or manifest.get("metadata", {}).get("project_name")
    models = {}
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model":
            continue
        if project_name and node.get("package_name") != project_name:
            continue
        models[node["name"]] = _model_from_manifest(node, catalog.get("nodes", {}).get(uid))
    log(f"dbt: {len(models)} models read from manifest")
    return models


def _path_config(project_cfg, project_name, rel_parts):
    """Walk dbt_project.yml `models:` block along the model's folder path.
    Deeper folders override shallower ones, as dbt does."""
    found = {}
    node = (project_cfg.get("models") or {}).get(project_name) or {}

    def take(n):
        for k, v in n.items():
            if k.startswith("+"):
                found[k[1:]] = v
            elif k in ("materialized", "tags") and not isinstance(v, dict):
                found[k] = v

    take(node)
    for part in rel_parts:
        node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            break
        take(node)
    return found


def from_project(project_dir):
    project_dir = Path(project_dir)
    project_cfg = yaml.safe_load(read_text(project_dir / "dbt_project.yml")) or {}
    project_name = project_cfg.get("name")
    model_paths = project_cfg.get("model-paths") or ["models"]

    yml_models = {}
    for mp in model_paths:
        for yml in sorted((project_dir / mp).rglob("*.yml")):
            try:
                doc = yaml.safe_load(read_text(yml)) or {}
            except yaml.YAMLError as exc:
                log(f"dbt: skipped unreadable yml {yml}: {exc}")
                continue
            for m in doc.get("models") or []:
                if isinstance(m, dict) and m.get("name"):
                    yml_models[m["name"]] = m

    models = {}
    for mp in model_paths:
        base = project_dir / mp
        for sql_file in sorted(base.rglob("*.sql")):
            name = sql_file.stem
            sql = read_text(sql_file)
            rel = sql_file.relative_to(base)
            cfg = _path_config(project_cfg, project_name, rel.parts[:-1])
            ym = yml_models.get(name, {})
            ycfg = ym.get("config") or {}
            materialized = cfg.get("materialized", "")
            materialized = ycfg.get("materialized", materialized)
            m = CFG_MAT_RE.search(sql)
            if m:
                materialized = m.group(1)
            tags = set()
            for t in (cfg.get("tags"), ycfg.get("tags"), ym.get("tags")):
                if isinstance(t, str):
                    tags.add(t)
                elif isinstance(t, list):
                    tags.update(t)
            m = CFG_TAGS_RE.search(sql)
            if m:
                tags.update(x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip())
            columns = {}
            for col in ym.get("columns") or []:
                if isinstance(col, dict) and col.get("name"):
                    columns[col["name"].lower()] = {
                        "type": col.get("data_type") or "",
                        "description": (col.get("description") or "").strip(),
                    }
            models[name] = {
                "name": name,
                "file": str(Path(mp) / rel),
                "materialized": materialized,
                "tags": sorted(tags),
                "refs": sorted({r[1] or r[0] for r in REF_RE.findall(sql)}),
                "sources": sorted({f"{s}.{t}" for s, t in SRC_RE.findall(sql)}),
                "columns": columns,
                "sql": sql,
                "sql_sha": sha(sql),
            }
    log(f"dbt: {len(models)} models read from project files (no manifest)")
    return models


def extract(cfg):
    dbt = cfg["sources"]["dbt"]
    manifest = dbt.get("manifest")
    if manifest and Path(manifest).exists():
        return from_manifest(manifest, dbt.get("catalog"), dbt.get("project_name"))
    if manifest:
        log(f"dbt: manifest not found at {manifest}; reading the project files instead")
    return from_project(dbt["project_dir"])
