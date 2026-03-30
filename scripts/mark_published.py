import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GITHUB_API = "https://api.github.com"
PUBLISHED_ISSUES_PATH = ".published_issues.json"


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


def load_published_issue_numbers() -> list[int]:
    if not os.path.exists(PUBLISHED_ISSUES_PATH):
        return []

    with open(PUBLISHED_ISSUES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        return []

    result = []
    for value in data:
        if isinstance(value, int):
            result.append(value)
        elif isinstance(value, str) and value.strip().isdigit():
            result.append(int(value.strip()))

    return sorted(set(result))


def main():
    token = os.environ["GH_TOKEN"]
    repo = os.environ["REPO"]

    issue_numbers = load_published_issue_numbers()
    if not issue_numbers:
        print("No newly published issues to update.")
        return

    for issue_number in issue_numbers:
        issue = gh_api("GET", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}", token)
        labels = [l["name"] for l in issue.get("labels", [])]

        labels = [x for x in labels if x not in ("status: approved", "status: published")]
        if "status: complete" not in labels:
            labels.append("status: complete")

        gh_api("PUT", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels", token, data=labels)
        gh_api("PATCH", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}", token, data={"state": "closed"})

        print(f"Issue #{issue_number}: closed and labeled status: complete")


if __name__ == "__main__":
    main()
