"""Bronze facts from eag-python-extractor.

Reads config/extracts.json (what each extract writes and which SQL file it runs)
and config/sql_config.yml (the source table per environment), plus the SQL files.

Output, one entry per extract key (for example dCag):
  name, type, bronze_table, input_path, sql_path, sql, sql_sha, source_table
"""
import json
from pathlib import Path

import yaml

from .common import log, read_text, sha


def extract(cfg):
    src = cfg["sources"]["extractor"]
    pkg = Path(src["package_dir"])
    env = src.get("env", "dev")
    extracts = json.loads(read_text(pkg / "config" / "extracts.json"))
    sql_cfg = yaml.safe_load(read_text(pkg / "config" / "sql_config.yml")) or {}
    env_tables = ((sql_cfg.get(env) or {}).get("extracts")) or {}

    out = {}
    for key, e in extracts.items():
        sql_path = e.get("sqlPath") or ""
        sql_file = pkg / sql_path if sql_path else None
        sql = read_text(sql_file) if sql_file and sql_file.exists() else ""
        output = (e.get("outputTablePath") or "").rstrip("/")
        out[key] = {
            "name": e.get("name", ""),
            "type": e.get("type", ""),
            "bronze_table": output.split("/")[-1] if output else "",
            "input_path": e.get("inputTablePath", ""),
            "sql_path": sql_path,
            "sql": sql,
            "sql_sha": sha(sql),
            "source_table": (env_tables.get(key) or {}).get("tableName", ""),
            "sql_missing": bool(sql_path) and not sql,
        }
    log(f"bronze: {len(out)} extracts (source tables for env '{env}')")
    return out
