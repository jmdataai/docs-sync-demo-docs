"""Gold facts from eag-snowflake-py-schemachange.

Replays the versioned migrations (V*__*.sql) in version order to get the current
tables, columns, types, clustering keys and masking policies, the same way the
database ends up after every migration has run. Repeatable scripts (R__*.sql) are
the load procedures; for those we record a fingerprint and the tables they load.

Output:
  tables: {name: {columns: {col: {type, masking}}, cluster_by: [...], defined_in: [...]}}
  procedures: {file: {sha, targets: [...], stage_paths: [...]}}
"""
from pathlib import Path
import re

from .common import log, read_text, sha

STOP_WORDS = {"NULL", "NOT", "DEFAULT", "COLLATE", "MASKING", "WITH", "COMMENT", "CONSTRAINT",
              "PRIMARY", "UNIQUE", "REFERENCES", "AUTOINCREMENT", "IDENTITY", "TAG", "FOREIGN"}


def _version_key(path):
    m = re.match(r"V([\d._]+?)__", path.name)
    parts = [int(p) for p in re.split(r"[._]", m.group(1)) if p != ""] if m else [0]
    return tuple(parts + [10**9] * (6 - len(parts)))  # V1.1.1 sorts before V1_1, as the repo intends


def _clean(sql):
    sql = re.sub(r"\$\$.*?\$\$", "''", sql, flags=re.S)       # procedure bodies
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _name(token):
    return token.strip().strip('"').split(".")[-1].strip('"').lower()


def _split_top(text, sep=","):
    out, depth, cur, quote = [], 0, [], None
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def _parse_coldef(defn):
    words = defn.strip().split()
    if not words or words[0].upper() in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN"}:
        return None
    col = _name(words[0])
    type_words = []
    for w in words[1:]:
        if w.upper() in STOP_WORDS:
            break
        type_words.append(w)
    masking = None
    m = re.search(r"MASKING\s+POLICY\s+([\w.]+)", defn, re.I)
    if m:
        masking = m.group(1).lower()
    return col, {"type": " ".join(type_words).lower(), "masking": masking}


def _body_after(stmt, start):
    """Text inside the first balanced (...) at or after `start`, and the rest after it."""
    i = stmt.find("(", start)
    if i < 0:
        return None, stmt[start:]
    depth = 0
    for j in range(i, len(stmt)):
        if stmt[j] == "(":
            depth += 1
        elif stmt[j] == ")":
            depth -= 1
            if depth == 0:
                return stmt[i + 1:j], stmt[j + 1:]
    return stmt[i + 1:], ""


def _apply(tables, stmt, source):
    s = " ".join(stmt.split())
    S = s.upper()

    m = re.match(r"CREATE\s+(OR\s+REPLACE\s+)?(?:TRANSIENT\s+|TEMPORARY\s+)?TABLE\s+(IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)", s, re.I)
    if m:
        name = _name(m.group(3))
        if m.group(2) and name in tables:
            return
        if re.search(r"\b(LIKE|CLONE|AS\s+SELECT)\b", S[m.end():m.end() + 40]):
            tables[name] = {"columns": {}, "cluster_by": [], "defined_in": [source], "note": "created by LIKE/CLONE/AS"}
            return
        body, rest = _body_after(s, m.end())
        cols = {}
        for d in _split_top(body or ""):
            parsed = _parse_coldef(d)
            if parsed:
                cols[parsed[0]] = parsed[1]
        cl = re.search(r"CLUSTER\s+BY\s*\(([^)]*)\)", rest, re.I)
        tables[name] = {
            "columns": cols,
            "cluster_by": [_name(c) for c in cl.group(1).split(",")] if cl else [],
            "defined_in": [source],
        }
        return

    m = re.match(r"DROP\s+TABLE\s+(IF\s+EXISTS\s+)?([\w.\"]+)", s, re.I)
    if m:
        tables.pop(_name(m.group(2)), None)
        return

    m = re.match(r"ALTER\s+TABLE\s+(IF\s+EXISTS\s+)?([\w.\"]+)\s+(.*)", s, re.I)
    if not m:
        return
    name, action = _name(m.group(2)), m.group(3)
    t = tables.setdefault(name, {"columns": {}, "cluster_by": [], "defined_in": [], "note": "altered before create"})
    if source not in t["defined_in"]:
        t["defined_in"].append(source)
    A = action.upper()

    if re.match(r"RENAME\s+TO\s+", A):
        new = _name(action.split()[-1])
        tables[new] = tables.pop(name)
        return
    mm = re.match(r"RENAME\s+COLUMN\s+([\w\"]+)\s+TO\s+([\w\"]+)", action, re.I)
    if mm:
        old, new = _name(mm.group(1)), _name(mm.group(2))
        if old in t["columns"]:
            t["columns"] = {(new if k == old else k): v for k, v in t["columns"].items()}
        return
    mm = re.match(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(.*)", action, re.I)
    if mm and not re.match(r"ADD\s+(CONSTRAINT|PRIMARY|UNIQUE|FOREIGN|ROW\s+ACCESS|SEARCH)", A):
        body = mm.group(1).strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
        for d in _split_top(body):
            d = re.sub(r"^\s*COLUMN\s+", "", d, flags=re.I)
            parsed = _parse_coldef(d)
            if parsed:
                t["columns"][parsed[0]] = parsed[1]
        return
    mm = re.match(r"DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(.*)", action, re.I)
    if mm and not re.match(r"DROP\s+(CONSTRAINT|PRIMARY|ROW|SEARCH|CLUSTERING)", A):
        for c in _split_top(mm.group(1)):
            t["columns"].pop(_name(c.replace("COLUMN", "")), None)
        return
    mm = re.match(r"CLUSTER\s+BY\s*\(([^)]*)\)", action, re.I)
    if mm:
        t["cluster_by"] = [_name(c) for c in mm.group(1).split(",")]
        return
    mm = re.match(r"(?:ALTER|MODIFY)\s+(?:COLUMN\s+)?(.*)", action, re.I)
    if mm:
        for item in _split_top(mm.group(1)):
            item = re.sub(r"^\s*COLUMN\s+", "", item.strip(), flags=re.I)
            if not item:
                continue
            col = _name(item.split()[0])
            entry = t["columns"].setdefault(col, {"type": "", "masking": None})
            ty = re.search(r"(?:SET\s+DATA\s+TYPE|\bTYPE)\s+(.+?)(?:\s+COLLATE\b|$)", item, re.I)
            if ty:
                entry["type"] = ty.group(1).strip().lower()
            mp = re.search(r"SET\s+MASKING\s+POLICY\s+([\w.]+)", item, re.I)
            if mp:
                entry["masking"] = mp.group(1).lower()
            if re.search(r"UNSET\s+MASKING\s+POLICY", item, re.I):
                entry["masking"] = None


def extract(cfg):
    mig_dir = Path(cfg["sources"]["snowflake"]["migrations_dir"])
    tables = {}
    versioned = sorted(mig_dir.glob("V*__*.sql"), key=_version_key)
    for f in versioned:
        for stmt in _clean(read_text(f)).split(";"):
            if stmt.strip():
                _apply(tables, stmt, f.name)

    procedures = {}
    for f in sorted(mig_dir.glob("R__*.sql")):
        text = read_text(f)
        targets = sorted({_name(t) for t in re.findall(r"COPY\s+INTO\s+([\w.\"]+)", text, re.I)})
        stages = sorted(set(re.findall(r"@[\w.]+/[\w./-]*", text)))
        procedures[f.name] = {"sha": sha(text), "targets": targets, "stage_paths": stages}

    log(f"gold: {len(tables)} tables from {len(versioned)} migrations, {len(procedures)} repeatable scripts")
    return {"tables": tables, "procedures": procedures}
