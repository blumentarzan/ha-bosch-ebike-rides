#!/usr/bin/env python3
"""Watch the upstream projects that track Bosch's eBike APIs.

Compares the git blob SHA of selected upstream files with the ones recorded in
.github/upstream.json. On a change it prints a Markdown report (changed files,
links, API hosts / paths / client ids that appear upstream but not in our
client) and exits 1, so the workflow opens an issue.

    python scripts/upstream_watch.py            # check
    python scripts/upstream_watch.py --update   # accept the current upstream state
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / ".github" / "upstream.json"
OURS = "\n".join(p.read_text() for p in (ROOT / "ebike_rides" / "app" / "bosch").glob("*.py"))
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
PATTERNS = [
    re.compile(r"https://[a-z0-9.-]+\.(?:cloud|com)"),
    re.compile(r"[\"'](/(?:v\d|activity|bike-profile)[^\"' ?{]*)"),
    re.compile(r"client_id[\"']?\s*[:=]\s*[\"']([a-z0-9-]+)[\"']", re.I),
]


def gh(path: str):
    req = urllib.request.Request(f"https://api.github.com/{path}")
    req.add_header("Accept", "application/vnd.github+json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    cfg = json.loads(CFG.read_text())
    update = "--update" in sys.argv
    report: list[str] = []
    changed = False
    for repo, spec in cfg["repos"].items():
        head = gh(f"repos/{repo}/commits?per_page=1")[0]["sha"]
        for f in spec["files"]:
            try:
                meta = gh(f"repos/{repo}/contents/{f}")
            except Exception as err:  # noqa: BLE001
                report.append(f"- ⚠️ `{repo}/{f}` not readable ({err}) – moved or renamed?")
                changed = True
                continue
            if spec["sha"].get(f) == meta["sha"]:
                continue
            if update:
                spec["sha"][f] = meta["sha"]
                continue
            changed = True
            text = base64.b64decode(meta["content"]).decode(errors="replace")
            found = set()
            for pat in PATTERNS:
                for m in pat.finditer(text):
                    found.add(m.group(1) if m.groups() else m.group(0))
            missing = sorted(x for x in found if x not in OURS)
            report.append(
                f"- `{repo}/{f}` changed "
                f"([file](https://github.com/{repo}/blob/{head}/{f}), "
                f"[history](https://github.com/{repo}/commits/{head}/{f}))"
            )
            if missing:
                report.append(
                    "  - API strings upstream that our client does not use: "
                    + ", ".join(f"`{m}`" for m in missing[:25])
                )
        if update:
            spec["head"] = head
    if update:
        CFG.write_text(json.dumps(cfg, indent=2) + "\n")
        print("upstream.json updated")
        return 0
    if changed:
        print("## Upstream Bosch API projects changed\n")
        print("\n".join(report))
        print(
            "\nCheck whether endpoints, auth or field names moved, adapt "
            "`ebike_rides/app/bosch/`, then run `python scripts/upstream_watch.py --update`."
        )
        return 1
    print("no upstream changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
