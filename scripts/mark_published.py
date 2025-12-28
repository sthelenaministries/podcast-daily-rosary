import glob
import json
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen

GITHUB_API = "https://api.github.com"

def parse_iso8601(dt_str: str):
    if not dt_str or not dt_str.strip():
        return None
    s = dt_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def is_publishable(ep: dict, now_utc: datetime) -> bool:
    dt = parse_iso8601(ep.get("publish_at", ""))
    if dt is None:
        return False
    if not (ep.get("audio_url") and ep.get("title") and ep.get("description")):
        return False
    return now_utc >= dt

def gh_api(method: str, url: str, token: str, data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "shm-daily-rosary-bot",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, method=method, headers=headers, data=body)
    with urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def main():
    token = os.environ["GH_TOKEN"]
    repo = os.environ["REPO"]  # owner/name

    now_utc = datetime.now(timezone.utc)

    episode_files = glob.glob("episodes/*.json")
    if not episode_files:
        print("No episodes found.")
        return

    for p in episode_files:
        with open(p, "r", encoding="utf-8") as f:
            ep = json.load(f)

        if not is_publishable(ep, now_utc):
            continue

        issue_number = ep.get("source_issue")
        if not issue_number:
            continue

        # Get current issue labels
        issue = gh_api("GET", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}", token)
        labels = [l["name"] for l in issue.get("labels", [])]

        # Ensure status: published exists
        if "status: published" not in labels:
            labels.append("status: published")

        # Optional cleanup: remove needs-review once published
        labels = [x for x in labels if x != "status: needs-review"]

        gh_api("PUT", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels", token, data=labels)
        print(f"Issue #{issue_number}: labeled as status: published")

if __name__ == "__main__":
    main()
