"""Open or refresh the docs-sync pull request through the GitHub REST API.

Works on github.com and GitHub Enterprise Server (uses GITHUB_API_URL), needs only
the Python standard library, so the runner does not need the gh CLI.

    python -m docs_sync.open_pr --head docs-sync/develop --base main \
        --title "Docs sync: changes from develop" --body-file docs-automation/out/drift_report.md

Environment: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_API_URL (all set by Actions).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MAX_BODY = 60000  # GitHub refuses bodies over 65536 characters


def call(method, url, token, data=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read() or "null")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--head", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    a = p.parse_args(argv)

    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    owner = repo.split("/")[0]
    body = open(a.body_file, encoding="utf-8").read()
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n_Report cut to fit the pull request. The full report is in the run's artifacts._\n"

    try:
        existing = call("GET", f"{api}/repos/{repo}/pulls?state=open&head={owner}:{a.head}", token)
        if existing:
            pr = call("PATCH", f"{api}/repos/{repo}/pulls/{existing[0]['number']}", token,
                      {"title": a.title, "body": body})
            print(f"updated pull request #{pr['number']}: {pr['html_url']}")
        else:
            pr = call("POST", f"{api}/repos/{repo}/pulls", token,
                      {"title": a.title, "head": a.head, "base": a.base, "body": body})
            print(f"opened pull request #{pr['number']}: {pr['html_url']}")
    except urllib.error.HTTPError as exc:
        print(f"GitHub API error {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
