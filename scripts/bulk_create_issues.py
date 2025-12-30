import csv
import json
import os
import sys
from datetime import datetime
from urllib.parse import quote, urlparse, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

GITHUB_API = "https://api.github.com"

# Your sample CSV uses: episode_date, Weekday, audio_url, mysteries, title, publish_at
REQUIRED_COLUMNS = ["episode_date", "audio_url", "mysteries", "title", "publish_at"]

def gh_api(method: str, url: str, token: str, data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "shm-bulk-issue-bot",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, method=method, headers=headers, data=body)

    try:
        with urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {e.code} for {url}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"GitHub API URLError for {url}: {e.reason}") from e

def normalize_episode_date(s: str) -> str:
    """
    Converts CSV dates like 12/31/2025 or 1/1/2026 to YYYY-MM-DD.
    """
    s = (s or "").strip()
    if not s:
        return ""
    # Try common M/D/YYYY
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Unrecognized episode_date format: {s}")

def normalize_mysteries(s: str) -> str:
    """
    Input: 'Joyful' or 'Joyful Mysteries' -> 'Joyful Mysteries'
    Also handles case differences.
    """
    s = (s or "").strip()
    if not s:
        return ""
    s_clean = " ".join(s.split())  # collapse whitespace
    # Title-case first word
    s_clean = s_clean[:1].upper() + s_clean[1:].lower() if s_clean.isupper() or s_clean.islower() else s_clean
    # Standardize specific names
    base = s_clean.replace("mystery", "Mystery").replace("mysteries", "Mysteries")
    # If already includes "Mysteries", keep
    if "Mysteries" in base:
        # Ensure proper Title Case for the first word
        first = base.split()[0].capitalize()
        rest = " ".join(base.split()[1:])
        return f"{first} {rest}".strip()
    # Otherwise append
    return f"{base.capitalize()} Mysteries"

def archive_item_id_from_audio_url(audio_url: str) -> str:
    """
    For Archive.org download URLs:
      https://archive.org/download/<item_id>/<filename>
    returns <item_id>
    """
    if not audio_url:
        return ""
    parsed = urlparse(audio_url)
    parts = [p for p in parsed.path.split("/") if p]
    # Expect ["download", "<item_id>", "<filename>"]
    if len(parts) >= 3 and parts[0] == "download":
        return parts[1]
    return ""

def episode_key(episode_date_yyyy_mm_dd: str, mysteries_norm: str) -> str:
    return f"daily-rosary|{episode_date_yyyy_mm_dd}|{mysteries_norm}"

def search_duplicate(repo: str, token: str, key: str) -> int:
    q = f'repo:{repo} in:body "Episode Key: {key}"'
    url = f"{GITHUB_API}/search/issues?q={quote(q)}"
    result = gh_api("GET", url, token)
    return int(result.get("total_count", 0))

def build_issue_body(ep_date: str, ep_title: str, mysteries: str, publish_at: str, audio_url: str, archive_item_id: str) -> str:
    # Match your existing headings so generate_description.py can parse it.
    return f"""Episode Key: {episode_key(ep_date, mysteries)}

### Episode date (YYYY-MM-DD)
{ep_date}

### Episode title
{ep_title}

### Mysteries (Rosary)
{mysteries}

### Publish at (ISO 8601 with timezone)
{publish_at}

### Audio URL (Archive.org direct file URL)
{audio_url}

### Archive.org item identifier (optional)
{archive_item_id}

### Notes for the description (optional)

""".strip()

def create_issue(repo: str, token: str, title: str, body: str, labels: list[str]):
    url = f"{GITHUB_API}/repos/{repo}/issues"
    payload = {"title": title, "body": body, "labels": labels}
    return gh_api("POST", url, token, payload)

def main():
    token = os.environ.get("GH_TOKEN")
    repo = os.environ.get("REPO")
    csv_path = os.environ.get("CSV_PATH", "bulk_episodes.csv")
    queue_label = os.environ.get("QUEUE_LABEL", "status: queued")

    if not token or not repo:
        print("Missing GH_TOKEN or REPO environment variables.")
        sys.exit(1)

    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        sys.exit(1)

    created = 0
    skipped = 0
    errors = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            print(f"CSV missing required columns: {missing}")
            print(f"Found columns: {headers}")
            sys.exit(1)

        for i, row in enumerate(reader, start=2):
            try:
                # ignore Weekday
                ep_date = normalize_episode_date(row.get("episode_date", ""))
                ep_title = (row.get("title") or "").strip()
                mysteries = normalize_mysteries(row.get("mysteries", ""))
                publish_at = (row.get("publish_at") or "").strip()
                audio_url = (row.get("audio_url") or "").strip()

                if not (ep_date and ep_title and mysteries and publish_at and audio_url):
                    print(f"Line {i}: missing required data; skipping.")
                    skipped += 1
                    continue

                key = episode_key(ep_date, mysteries)
                dup_count = search_duplicate(repo, token, key)
                if dup_count > 0:
                    print(f"Line {i}: duplicate found for key [{key}] ({dup_count} match); skipping.")
                    skipped += 1
                    continue

                archive_item_id = archive_item_id_from_audio_url(audio_url)

                body = build_issue_body(
                    ep_date=ep_date,
                    ep_title=ep_title,
                    mysteries=mysteries,
                    publish_at=publish_at,
                    audio_url=audio_url,
                    archive_item_id=archive_item_id,
                )

                issue = create_issue(repo, token, title=ep_title, body=body, labels=[queue_label])
                issue_num = issue.get("number")
                print(f"Line {i}: created issue #{issue_num} for key [{key}]")
                created += 1

            except Exception as e:
                print(f"Line {i}: ERROR: {e}")
                errors += 1

    print(f"\nDone. Created: {created}, Skipped: {skipped}, Errors: {errors}")
    if errors > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
