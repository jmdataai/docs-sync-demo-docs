# Docs sync

Keeps the product docs in `open-data-artefacts` in step with the code. On every
deploy of a source repo it reads the code, works out what changed since the docs
were last approved, rewrites the generated parts of the pages, and opens one pull
request with a drift report. A person reviews and merges. Nothing reaches the site
without that merge.

No AI is involved. Every generated line comes from a script reading the code, so
the same code always gives the same text.

## Where the files go

```
open-data-artefacts/                      (the docs repo)
├── .github/workflows/
│   ├── microsite.yml                     existing: builds and deploys the site on merge to main
│   └── docs-sync.yml                     NEW: copy from docs-automation/workflows/docs-sync.yml
├── docs-automation/                      NEW: this folder
│   ├── config.yml                        where the source repos are, which products to read
│   ├── requirements.txt                  pyyaml (sqlglot optional)
│   ├── docs_sync/                        the scripts
│   │   ├── extract_dbt.py                Silver: models, lineage, columns, model SQL
│   │   ├── extract_gold.py               Gold: tables, columns, types, cluster keys, load procedures
│   │   ├── extract_bronze.py             Bronze: extracts, their SQL, source tables
│   │   ├── extract_screens.py            Screens: the SQL behind every chart, shared filters
│   │   ├── update_blocks.py              rewrites the marked blocks in pages and notebooks
│   │   ├── compare.py                    drift report: what changed, which pages it touches
│   │   ├── check_docs.py                 links, anchors, markers, notebooks, hard-coded counts
│   │   ├── open_pr.py                    opens or refreshes the pull request
│   │   └── __main__.py                   the command line
│   ├── facts/approved/                   committed: the facts the current docs were approved against
│   ├── tests/test_docs_sync.py
│   └── workflows/                        templates to copy (see below)
└── docs/ ...                             the pages, unchanged except inside markers

each source repo (eag-databricks-dbt-transform, eag-snowflake-py-schemachange,
eag-python-extractor, pdp-tenant, pdp-experience-api)
└── .github/workflows/notify-docs.yml     NEW: copy from docs-automation/workflows/notify-docs.yml
```

## How it runs

1. **Trigger.** A push to the branch the docs follow runs `notify-docs.yml` in that
   source repo, which asks the docs repo to run `docs-sync.yml`. A weekly scheduled
   run and the "Run workflow" button cover anything missed.
2. **Collect.** `docs-sync.yml` checks out the source repos read-only into `sources/`.
   If the dbt storage settings are present it also downloads `manifest.json` and
   `catalog.json`, which the dbt deploy pipeline already uploads
   (`.github/scripts/run_upload_dbt_docs.sh` in the dbt repo). Without them the dbt
   project files are read directly.
3. **Extract.** Each extractor writes a JSON file to `facts/current/`:
   `silver.json`, `gold.json`, `bronze.json`, `screens.json`.
4. **Update.** `update_blocks.py` rewrites every marked block from those facts.
5. **Compare.** `compare.py` compares `facts/current` with `facts/approved` and writes
   `out/drift_report.md`.
6. **Check.** `check_docs.py` checks links, anchors, markers and notebooks. Errors fail the run.
7. **Pull request.** The updated pages and the new `facts/approved/` go to one pull
   request (branch `docs-sync/<branch>`), with the drift report as its description.
   Later runs refresh the same pull request until it is merged.
8. **Review and merge.** A reviewer works through the report. Merging updates the
   pages, makes today's facts the new approved baseline, and `microsite.yml` publishes.

## How it identifies what to update

There are three ways, from fully automatic to fully manual.

**1. Marked blocks: rewritten automatically.** A page marks the text that should
follow the code:

```markdown
## What it reads

<!-- auto:start reads model=d_contracted_market_basket -->
Bronze sources: `dp_market_basket_parquet`, `nocodb_curated_medispan_harvest_parquet`.

Silver models: [`m_master_drug_list`](m_master_drug_list.md).
<!-- auto:end -->
```

Only the text between the two markers changes. The markers are HTML comments, so
the site does not show them. In a notebook, the same instruction goes in a markdown
cell's metadata: `"docs_sync": "model-sql model=l_m_master_drug_list"`.

| Block | Arguments | What it writes |
| --- | --- | --- |
| `reads` | `model=` (`readby=yes` optional) | Bronze sources and Silver models the model reads, and what reads it |
| `model-sql` | `model=` | the dbt model SQL |
| `lineage` | `model=` (`depth=` optional) | a mermaid graph of everything upstream of the model |
| `gold-columns` | `table=` (`order=table` optional) | the Gold column table. Snowflake owns Column and Type; every other column is kept as people wrote it. New columns are added with "To describe", dropped columns are removed |
| `cluster-by` | `table=` | the Gold clustering key |
| `screen-sql` | `query=Repository.method` | the SQL a chart runs through the Tenant API |
| `extract-sql` | `extract=` | the SQL a Bronze extract runs |

**2. Mentions: listed for review.** For every change, the report lists the pages that
need a person:

- *Review by hand*: the item's own page or notebook (file named after it), and any
  page that names a changed column together with its table.
- *Also mentioned in*: every other page that names the item, folded so it does not crowd the report.

A change to a shared Tenant API filter (for example `FILTER_INDICATIONS`) is
reported once, with the queries that use it, instead of once per query.

**3. Page checks: every run, changed or not.** Entity pages are compared with the code:

- Silver entity pages: the Model file, Materialisation, Bronze sources and Silver models they state.
- Gold entity pages: every column and type in their column tables.

On today's docs this already finds that 24 New Rx Silver pages say `view` where the
project builds `table`, and that the New Rx `f_rebates_claim_temp` page lists fewer
Silver models than the model reads.

## The drift report

The report is the pull request description. It shows, per layer:

- what changed, with the SQL difference
- which pages were updated automatically
- which pages to review by hand
- the pages that disagree with the code today
- a reviewer checklist: run changed queries, confirm with data owners, add findings
- the files this run changed, any errors, and the warnings (such as hard-coded counts)

## Run it locally

```bash
# from the docs repo root, with the source repos cloned under sources/
pip install -r docs-automation/requirements.txt
PYTHONPATH=docs-automation python -m docs_sync all      # extract, update, compare, check
cat docs-automation/out/drift_report.md
PYTHONPATH=docs-automation python -m docs_sync approve  # only when you accept today's facts
cd docs-automation && python -m unittest discover -s tests -v
```

The other commands: `extract`, `update`, `compare`, `check`, `approve`.

## Setting it up

1. Copy `docs-automation/` into the docs repo and `workflows/docs-sync.yml` to `.github/workflows/`.
2. Copy `workflows/notify-docs.yml` into each source repo and set its branch.
3. Add the settings listed at the top of each workflow file (org, branch, tokens).
4. Run once by hand. The first run has nothing to compare with: merging that pull
   request stores today's facts as the baseline.
5. Add markers to pages, one product at a time. Start with "What it reads" on Silver
   entity pages and the column tables on Gold entity pages.
6. Clear the current MkDocs warnings, then set `DOCS_STRICT_BUILD` to `true`.

## Limits

- Mention search is by name. A page that describes an item without naming it is not found.
- Narrative text (filters explained, findings, business meaning) is never written by the
  scripts. The report flags it for a person.
- The fallback dbt reader reads `ref()` and `source()` calls from the SQL. The manifest is
  more exact, so set up the dbt storage settings when access allows.
- `pdp-react` is not read yet. Chart wiring changes there need a manual check.
