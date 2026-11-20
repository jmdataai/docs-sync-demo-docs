"""Shared helpers: config loading, JSON facts, hashing, logging."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE.parent / "config.yml"


def log(msg):
    print(f"[docs-sync] {msg}", file=sys.stderr)


def sha(text):
    """Short, stable fingerprint of a piece of text (whitespace-normalised)."""
    norm = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def load_config(path=None):
    path = Path(path or os.environ.get("DOCS_SYNC_CONFIG") or DEFAULT_CONFIG)
    path = path.resolve()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Relative paths in config.yml are relative to the docs repo root, which is the
    # folder above docs-automation/ (override with DOCS_SYNC_ROOT).
    root = Path(os.environ.get("DOCS_SYNC_ROOT") or path.parent.parent).resolve()
    path_keys = ("dir", "manifest", "catalog", "root", "current", "approved")

    def fix(value):
        if isinstance(value, dict):
            return {k: (str(_abs(v)) if isinstance(v, str) and k.endswith(path_keys) else fix(v))
                    for k, v in value.items()}
        return value

    def _abs(v):
        p = Path(v)
        return p if p.is_absolute() else root / p

    for key in ("sources", "docs", "facts", "output"):
        if key in cfg:
            cfg[key] = fix(cfg[key])
    cfg["_config_path"] = str(path)
    cfg["_root"] = str(root)
    return cfg


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
