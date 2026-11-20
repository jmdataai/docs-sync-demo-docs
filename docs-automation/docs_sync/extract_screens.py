"""Screen query facts from pdp-tenant and pdp-experience-api.

Tenant API: every @Query(..., nativeQuery = true) in the repository classes is
rebuilt into one SQL string. String constants it concatenates (DbConstants and
friends) are resolved, so a change to a shared filter shows up on every query
that uses it.

Experience API: a fingerprint per service file under the configured folders, so
a change there flags the Product Summary for review.

Output:
  queries: {"Repo.method": {repository, method, sql, sql_sha, own_sha, tables, columns, constants}}
  constants: {NAME: resolved SQL} for the shared SQL fragments the queries use
  experience: {relative_file: sha}
"""
from pathlib import Path
import re

from .common import log, read_text, sha

TOKEN_RE = re.compile(r'"""(.*?)"""|"((?:\\.|[^"\\])*)"|([A-Za-z_][\w.]*)|(\+)|([(),;=])', re.S)
UNESCAPE = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'"}


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: UNESCAPE.get(m.group(1), m.group(1)), s)


def _read_expr(text, pos, stop_chars):
    """Read a Java string expression ("a" + CONST + "b") from pos until a top-level stop char.
    Returns ([("str", value) | ("ref", NAME)], end_pos)."""
    parts, depth = [], 0
    for m in TOKEN_RE.finditer(text, pos):
        block, s, ident, plus, punct = m.groups()
        if punct:
            if punct == "(":
                depth += 1
            elif punct == ")":
                if depth == 0 and ")" in stop_chars:
                    return parts, m.start()
                depth -= 1
            elif punct in stop_chars and depth == 0:
                return parts, m.start()
            continue
        if block is not None:
            parts.append(("str", block))
        elif s is not None:
            parts.append(("str", _unescape(s)))
        elif ident:
            parts.append(("ref", ident.split(".")[-1]))
    return parts, len(text)


def read_constants(files):
    raw = {}
    decl = re.compile(r"static\s+final\s+String\s+(\w+)\s*=", re.S)
    for f in files:
        text = read_text(f)
        for m in decl.finditer(text):
            parts, _ = _read_expr(text, m.end(), {";"})
            raw[m.group(1)] = parts
    resolved = {}

    def resolve(name, seen=()):
        if name in resolved:
            return resolved[name]
        if name not in raw or name in seen:
            return None
        out = []
        for kind, val in raw[name]:
            if kind == "str":
                out.append(val)
            else:
                r = resolve(val, seen + (name,))
                out.append(r if r is not None else "${" + val + "}")
        resolved[name] = "".join(out)
        return resolved[name]

    for n in raw:
        resolve(n)
    return resolved


def _tables(sql):
    return sorted({t.split(".")[-1].upper() for t in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", sql, re.I)
                   if t.upper() not in {"SELECT", "LATERAL", "TABLE"}})


def _columns(sql):
    return sorted({c.lower() for c in re.findall(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)\b", sql)})


def extract(cfg):
    src = cfg["sources"]["tenant"]
    root = Path(src["java_dir"])
    const_files = sorted(root.glob(src.get("constants_glob", "**/constants/*.java")))
    constants = read_constants(const_files)

    queries = {}
    q_re = re.compile(r"@Query\s*\(")
    for f in sorted(root.glob(src.get("repositories_glob", "**/repositories/*.java"))):
        text = read_text(f)
        repo = f.stem
        for m in q_re.finditer(text):
            # find the end of @Query(...), skipping parentheses inside string literals
            depth, j, in_str = 1, m.end(), False
            while j < len(text) and depth:
                ch = text[j]
                if in_str:
                    if ch == "\\":
                        j += 1
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                j += 1
            annotation = text[m.start():j]
            if not re.search(r"nativeQuery\s*=\s*true", annotation):
                continue
            # the SQL is the `value =` argument, wherever it sits, else the first argument
            vm = re.search(r"\bvalue\s*=\s*", annotation)
            start = m.start() + vm.end() if vm else m.end()
            parts, _ = _read_expr(text[:j - 1], start, {",", ")"})
            meth = re.search(r"[\w<>\[\],\s?]+\s+(\w+)\s*\(", text[j:j + 600])
            method = meth.group(1) if meth else f"query_at_{m.start()}"
            used = sorted({v for k, v in parts if k == "ref"})
            template = "".join(v if k == "str" else "${" + v + "}" for k, v in parts)
            sql = "".join(v if k == "str" else (constants.get(v) if constants.get(v) is not None else "${" + v + "}")
                          for k, v in parts)
            sql = re.sub(r"[ \t]+", " ", sql).strip()
            key = f"{repo}.{method}"
            n = 2
            while key in queries:
                key = f"{repo}.{method}#{n}"
                n += 1
            queries[key] = {"repository": repo, "method": method, "sql": sql, "sql_sha": sha(sql),
                            "own_sha": sha(template),
                            "tables": _tables(sql), "columns": _columns(sql), "constants": used,
                            "file": str(f.relative_to(root))}

    experience = {}
    exp = cfg["sources"].get("experience") or {}
    if exp.get("src_dir"):
        base = Path(exp["src_dir"])
        for folder in exp.get("folders") or ["."]:
            for f in sorted((base / folder).rglob("*.ts")):
                if ".test." in f.name:
                    continue
                experience[str(f.relative_to(base))] = sha(read_text(f))

    log(f"screens: {len(queries)} native queries in the Tenant API, {len(constants)} SQL constants, "
        f"{len(experience)} Experience API files")
    return {"queries": queries, "experience": experience,
            "constants": {n: v for n, v in constants.items() if any(n in q["constants"] for q in queries.values())}}
