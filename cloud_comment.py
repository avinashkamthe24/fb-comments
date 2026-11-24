#!/usr/bin/env python3
"""
Runs on GitHub Actions every ~10 minutes.

pending/<post_id>.json  - written by your PC when a post is scheduled
done/<post_id>.json     - moved here after the comment is added (never commented twice)
failed/<post_id>.json   - moved here if Facebook says the post can't take a comment

Page tokens come from the FB_PAGES secret: {"<page_id>": "<page token>", ...}
Nothing secret is ever printed to the log.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "https://graph.facebook.com/v26.0"
ROOT = Path(__file__).resolve().parent
PENDING, DONE, FAILED = ROOT / "pending", ROOT / "done", ROOT / "failed"
GRACE = 60          # seconds after the publish time before commenting
GIVE_UP = 3 * 86400  # stop retrying a post 3 days after its publish time


def graph(method, path, **kw):
    r = requests.request(method, f"{API}/{path}", timeout=60, **kw)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.ok and "error" not in data:
        return data
    err = data.get("error", {})
    raise RuntimeError(f"{err.get('message') or r.text[:200]} (code {err.get('code')})")


def move(src, folder, info):
    folder.mkdir(exist_ok=True)
    (folder / src.name).write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    src.unlink()


def main():
    try:
        tokens = json.loads(os.environ.get("FB_PAGES", ""))
    except ValueError:
        sys.exit("FB_PAGES secret is missing or not valid JSON.")
    now = time.time()
    files = sorted(PENDING.glob("*.json"))
    print(f"{len(files)} post(s) waiting for a comment")
    for f in files:
        info = json.loads(f.read_text(encoding="utf-8"))
        post_id, page_id = info["post_id"], str(info["page_id"])
        if (DONE / f.name).exists():
            f.unlink()
            continue
        if now < info["publish_ts"] + GRACE:
            print(f"{post_id}: goes live later - waiting")
            continue
        token = tokens.get(page_id)
        if not token:
            print(f"{post_id}: no token for page {page_id} in FB_PAGES secret - skipped")
            continue
        try:
            if not info.get("video"):
                live = graph("GET", post_id, params={"fields": "is_published",
                                                     "access_token": token}).get("is_published")
                if not live:
                    print(f"{post_id}: not published yet - waiting")
                    continue
            res = graph("POST", f"{post_id}/comments",
                        data={"message": info["comment"], "access_token": token})
            info.update(comment_id=res.get("id", ""), commented_at=int(now))
            move(f, DONE, info)
            print(f"{post_id}: COMMENTED ({info.get('page', '')})")
        except RuntimeError as e:
            msg = str(e)
            permanent = "(code 100)" in msg or "does not exist" in msg
            if permanent or now > info["publish_ts"] + GIVE_UP:
                info["error"] = msg
                move(f, FAILED, info)
                print(f"{post_id}: FAILED, gave up - {msg}")
            else:
                print(f"{post_id}: failed, will retry - {msg}")


if __name__ == "__main__":
    main()
