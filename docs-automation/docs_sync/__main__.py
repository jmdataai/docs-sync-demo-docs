"""Docs sync command line.

    python -m docs_sync all        extract -> update -> compare -> check (what CI runs)
    python -m docs_sync extract    write facts/current/*.json from the source repos
    python -m docs_sync compare    drift report: what changed, which pages it touches
    python -m docs_sync update     rewrite the marked blocks from facts/current
    python -m docs_sync check      links, anchors, markers, notebooks, hard-coded counts
    python -m docs_sync approve    copy facts/current to facts/approved (the new baseline)

Options: --config PATH (default: config.yml next to this package)
"""
import argparse
import shutil
import sys
from pathlib import Path

from . import check_docs, compare, extract_bronze, extract_dbt, extract_gold, extract_screens, update_blocks
from .common import load_config, log, write_json


def do_extract(cfg):
    cur = Path(cfg["facts"]["current"])
    write_json(cur / "silver.json", extract_dbt.extract(cfg))
    write_json(cur / "gold.json", extract_gold.extract(cfg))
    write_json(cur / "bronze.json", extract_bronze.extract(cfg))
    write_json(cur / "screens.json", extract_screens.extract(cfg))


def do_approve(cfg):
    cur, app = Path(cfg["facts"]["current"]), Path(cfg["facts"]["approved"])
    app.mkdir(parents=True, exist_ok=True)
    for f in cur.glob("*.json"):
        shutil.copy2(f, app / f.name)
    log(f"approve: {app} now holds today's facts")


def main(argv=None):
    p = argparse.ArgumentParser(prog="docs_sync", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["all", "extract", "compare", "update", "check", "approve"])
    p.add_argument("--config")
    p.add_argument("--approve", action="store_true",
                   help="with `all`: also stage today's facts as the approved baseline in the same pull request")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    out = Path(cfg["output"]["dir"])
    out.mkdir(parents=True, exist_ok=True)
    failed = False

    if args.command in ("extract", "all"):
        do_extract(cfg)
    # update before compare, so the page checks see the pages as they will be merged
    if args.command in ("update", "all"):
        res = update_blocks.run(cfg)
        failed |= bool(res["errors"])
        write_json(out / "update.json", res)
    if args.command in ("compare", "all"):
        compare.run(cfg)
    if args.command in ("check", "all"):
        res = check_docs.run(cfg)
        failed |= bool(res["errors"])
    if args.command == "approve" or (args.command == "all" and args.approve):
        do_approve(cfg)

    if args.command == "all":
        _append_summary(out)
    return 1 if failed else 0


def _append_summary(out):
    import json
    upd = json.loads((out / "update.json").read_text()) if (out / "update.json").exists() else {}
    chk = json.loads((out / "checks.json").read_text()) if (out / "checks.json").exists() else {}
    lines = ["## What this run changed", ""]
    if upd.get("changed"):
        lines += [f"- `{p}`" for p in upd["changed"]]
    else:
        lines.append("- No marked block needed rewriting.")
    if upd.get("errors") or chk.get("errors"):
        lines += ["", "## Errors (fix before merging)", ""] + [f"- {e}" for e in upd.get("errors", []) + chk.get("errors", [])]
    if chk.get("warnings"):
        lines += ["", f"<details><summary>{len(chk['warnings'])} warnings</summary>", ""]
        lines += [f"- {w}" for w in chk["warnings"][:200]] + ["", "</details>"]
    report = out / "drift_report.md"
    report.write_text(report.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
