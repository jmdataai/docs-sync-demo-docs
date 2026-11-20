# docs-sync demo: docs repo

Plays the part of `open-data-artefacts`. It holds:

- `docs/`: the pages of a small, made-up "Sales Insights" product, in the same
  folder layout as the real product docs (Product Summary, Bronze, Silver, Gold, Charts, notebooks)
- `docs-automation/`: the docs sync scripts, copied unchanged from the client package.
  Only `config.yml` is different: it points at the demo code repo.
- `.github/workflows/docs-sync.yml`: the workflow that reads the code and opens the pull request

Text between `<!-- auto:start ... -->` and `<!-- auto:end -->` is written by the scripts.
Everything else is written by people and is never changed by the scripts.

Run it on your own machine (Python 3.10 or later), with the code repo cloned into `sources/`:

```bash
git clone https://github.com/jmdataai/docs-sync-demo-code sources/docs-sync-demo-code -b develop
pip install -r requirements.txt -r docs-automation/requirements.txt
PYTHONPATH=docs-automation python -m docs_sync all
cat docs-automation/out/drift_report.md
```

All data, names and tables here are invented for the demo.
