import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"


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

    try:
        with urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {e.code} {detail}") from e


def run(cmd: str):
    subprocess.check_call(cmd, shell=True)


def run_capture(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def parse_issue_form(body: str) -> dict:
    """
    GitHub issue forms render into markdown like:
    ### Episode date (YYYY-MM-DD)
    2025-12-26
    ### Episode title
    ...
    """
    fields = {}
    pattern = r"^###\s+(.*?)\s*\n([\s\S]*?)(?=^\#\#\#\s+|\Z)"
    for m in re.finditer(pattern, body, flags=re.MULTILINE):
        key = m.group(1).strip()
        val = m.group(2).strip()
        val = re.sub(r"\n{3,}", "\n\n", val).strip()
        fields[key] = val

    def get(k, default=""):
        return fields.get(k, default).strip()

    return {
        "episode_date": get("Episode date (YYYY-MM-DD)"),
        "episode_title": get("Episode title"),
        "mysteries": get("Mysteries (Rosary)"),
        "audio_url": get("Audio URL (Archive.org direct file URL)"),
        "archive_item_id": get("Archive.org item identifier (optional)"),
        "publish_at": get("Publish at (ISO 8601 with timezone)"),
        "notes": get("Notes for the description (optional)"),
    }


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def weekday_from_date(yyyy_mm_dd: str) -> str:
    dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return dt.strftime("%A")


def load_description_template(episode_date: str) -> str:
    weekday = weekday_from_date(episode_date)
    path = f"descriptions/{weekday}-Description.txt"

    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing description file: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def build_episode_url(episode_date: str, mysteries: str) -> str:
    weekday = weekday_from_date(episode_date)
    mysteries_slug = slugify(mysteries)

    return (
        f"https://sthelenaministries.com/{episode_date}-rosary-"
        f"{slugify(weekday)}-"
        f"the-{mysteries_slug}-"
        f"daily-catholic-prayer"
    )


def issue_has_label(issue: dict, label_name: str) -> bool:
    return any(label.get("name") == label_name for label in issue.get("labels", []))


def replace_issue_labels(token: str, repo: str, issue_number: int, remove_label: str, add_label: str):
    issue = gh_api("GET", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}", token)
    labels = [l["name"] for l in issue.get("labels", [])]

    new_labels = [x for x in labels if x != remove_label]
    if add_label not in new_labels:
        new_labels.append(add_label)

    gh_api("PUT", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels", token, data=new_labels)


def ensure_git_clean_main():
    run('git config user.name "shm-bot"')
    run('git config user.email "actions@users.noreply.github.com"')
    run("git fetch origin main")
    run("git checkout main")
    run("git reset --hard origin/main")


def git_has_changes(path: str) -> bool:
    status = run_capture(f"git status --porcelain -- {path}")
    return bool(status)


def main():
    token = os.environ["GH_TOKEN"]
    issue_number = int(os.environ["ISSUE_NUMBER"])
    repo = os.environ["REPO"]

    issue = gh_api("GET", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}", token)

    if issue.get("state") != "open":
        print(f"Issue #{issue_number} is not open. Exiting.")
        return

    if not issue_has_label(issue, "status: queued"):
        print(f"Issue #{issue_number} is not labeled status: queued. Exiting.")
        return

    issue_body = issue.get("body") or ""
    data = parse_issue_form(issue_body)

    if not data["episode_date"] or not data["episode_title"] or not data["mysteries"]:
        print("Issue is missing required fields. Ensure the Issue Form was used.")
        sys.exit(1)

    if not data["publish_at"]:
        print("Missing publish_at (scheduled release time).")
        sys.exit(1)

    date = data["episode_date"]
    slug = f"{date}-{slugify(data['mysteries'])}"
    episode_path = f"episodes/{slug}.json"

    description = load_description_template(data["episode_date"])
    episode_url = build_episode_url(data["episode_date"], data["mysteries"])

    episode = {
        "slug": slug,
        "show": "daily-rosary",
        "episode_url": episode_url,
        "episode_date": data["episode_date"],
        "title": data["episode_title"],
        "mysteries": data["mysteries"],
        "audio_url": data["audio_url"],
        "archive_item_id": data["archive_item_id"],
        "description": description,
        "source_issue": issue_number,
        "publish_at": data["publish_at"],
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }

    ensure_git_clean_main()
    os.makedirs("episodes", exist_ok=True)

    if os.path.exists(episode_path):
        print(f"{episode_path} already exists. Marking issue approved and exiting.")
        replace_issue_labels(
            token=token,
            repo=repo,
            issue_number=issue_number,
            remove_label="status: queued",
            add_label="status: approved",
        )
        return

    with open(episode_path, "w", encoding="utf-8") as f:
        json.dump(episode, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if not git_has_changes(episode_path):
        print(f"No changes detected for {episode_path}. Marking issue approved and exiting.")
        replace_issue_labels(
            token=token,
            repo=repo,
            issue_number=issue_number,
            remove_label="status: queued",
            add_label="status: approved",
        )
        return

    run(f"git add {episode_path}")
    run(f'git commit -m "Create episode {slug} from issue #{issue_number}"')
    run("git push origin main")

    replace_issue_labels(
        token=token,
        repo=repo,
        issue_number=issue_number,
        remove_label="status: queued",
        add_label="status: approved",
    )

    print(f"Created {episode_path} and marked issue #{issue_number} as approved.")


if __name__ == "__main__":
    main()
