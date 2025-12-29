import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from openai import OpenAI
from openai import APIConnectionError, APIError, RateLimitError, APITimeoutError

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
    with urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

def run(cmd: str):
    subprocess.check_call(cmd, shell=True)

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

def openai_chat_completion(api_key: str, prompt: str) -> str:
    """
    Uses the OpenAI Python SDK (Responses API).
    Returns the model's plain text output.
    """

    api_key: str = os.environ["OPENAI_API_KEY"],
    model: str = "gpt-4.1-mini",
    system: str = "You are a careful Catholic ministry copywriter. Follow all rules exactly.",
    temperature: float = 0.4,
    max_output_tokens: int = 600,
    timeout_seconds: int = 60,
    client = OpenAI(api_key=api_key, timeout=timeout_seconds)

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        # The SDK exposes a convenience accessor for the concatenated text.
        return (resp.output_text or "").strip() or _extract_text_fallback(resp)

    except APITimeoutError as e:
        raise RuntimeError(f"OpenAI timeout after {timeout_seconds}s") from e
    except RateLimitError as e:
        raise RuntimeError(f"OpenAI rate limited: {e}") from e
    except APIConnectionError as e:
        raise RuntimeError(f"OpenAI connection error: {e}") from e
    except APIError as e:
        # Includes 4xx/5xx API responses surfaced by the SDK
        raise RuntimeError(f"OpenAI API error: {e}") from e


def _extract_text_fallback(resp) -> str:
    """
    Fallback extractor in case output_text is empty/unavailable.
    Walks response.output[*].content[*] for output_text/text blocks.
    """
    chunks: list[str] = []
    for item in getattr(resp, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            ptype = getattr(part, "type", None)
            text = getattr(part, "text", None)
            if ptype in ("output_text", "text") and text:
                chunks.append(text)

    text_out = "\n".join(chunks).strip()
    if not text_out:
        raise RuntimeError("OpenAI response missing text output.")
    return text_out

def main():
    token = os.environ["GH_TOKEN"]
    openai_key = os.environ["OPENAI_API_KEY"]
    issue_number = int(os.environ["ISSUE_NUMBER"])
    repo = os.environ["REPO"]  # owner/name

    print("OPENAPIKEY present:",bool(k), "len:",len(k))
    # Fetch issue
    issue = gh_api("GET", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}", token)
    issue_body = issue.get("body") or ""
    issue_title = issue.get("title") or f"Issue {issue_number}"

    data = parse_issue_form(issue_body)

    # Basic required fields
    if not data["episode_date"] or not data["episode_title"] or not data["mysteries"]:
        print("Issue is missing required fields. Ensure the Issue Form was used.")
        sys.exit(1)

    if not data["publish_at"]:
        print("Missing publish_at (scheduled release time).")
        sys.exit(1)

    # Create slug + paths
    date = data["episode_date"]
    slug = f"{date}-{slugify(data['mysteries'])}"
    episode_path = f"episodes/{slug}.json"
    draft_path = f"drafts/{slug}.md"

    # Build prompt (faith-safe: no prayer/scripture quoting, no promises, pastoral tone)
    prompt = f"""
TASK:
Generate a single, unified, SEO-optimized description for an episode of the “St. Helena Ministries – Daily Rosary” podcast.

HARD RULES (must follow):
- Do NOT paraphrase, rewrite, summarize, or modify any prayers or Scripture.
- Do NOT quote Scripture.
- You MAY reference Scripture only by book, chapter, and verse (no quotations).
- Do NOT invent theological explanations, promises, or spiritual outcomes.
- Tone: pastoral, reverent, calm, invitational. No hype, no sales language, no emotional manipulation.
- Output must be suitable for human review before publication.

MUST INCLUDE (in one unified description):
1) Reverent overview of the Daily Rosary episode
2) Gentle, non-commercial “Support This Ministry” call-to-action
3) Brief cross-promotion of the Divine Office podcast

EPISODE CONTEXT:
- Episode date: {data['episode_date']}
- Episode title: {data['episode_title']}
- Mysteries: {data['mysteries']}
- Notes: {data['notes'] or "(none)"}

LINKING / BRANDING:
- If you include links, use placeholder text only (no raw URLs). Example: “Visit our website” or “Support this ministry”.
- Keep it concise enough for podcast platforms (roughly 120–220 words).
- End with a short, peaceful closing line (one sentence).

Return ONLY the final description text, no headings, no bullet labels, no metadata.
""".strip()

    description = openai_chat_completion(openai_key, prompt)

    # Prepare episode JSON (canonical store)
    episode = {
        "slug": slug,
        "show": "daily-rosary",
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

    os.makedirs("episodes", exist_ok=True)
    os.makedirs("drafts", exist_ok=True)

    with open(episode_path, "w", encoding="utf-8") as f:
        json.dump(episode, f, ensure_ascii=False, indent=2)

    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(description.strip() + "\n")

    # Create branch + commit
    branch = f"draft/issue-{issue_number}-{slug}"
    run('git config user.name "shm-bot"')
    run('git config user.email "actions@users.noreply.github.com"')
    run(f"git checkout -b {branch}")
    run(f"git add {episode_path} {draft_path}")
    run(f'git commit -m "Draft description for {slug} (issue #{issue_number})"')
    run(f"git push --set-upstream origin {branch}")

    # Create PR
    pr = gh_api("POST", f"{GITHUB_API}/repos/{repo}/pulls", token, data={
        "title": f"Draft: {data['episode_title']} ({data['episode_date']})",
        "head": branch,
        "base": "main",
        "body": f"Auto-generated draft from issue #{issue_number}.\n\nPlease review/edit before merging.",
    })
    pr_number = pr.get("number")

    # Update labels: remove queued, add needs-review
    labels = [l["name"] for l in issue.get("labels", [])]
    new_labels = [x for x in labels if x != "status: queued"]
    if "status: needs-review" not in new_labels:
        new_labels.append("status: needs-review")
    gh_api("PUT", f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels", token, data=new_labels)

    print(f"Created PR #{pr_number} for {slug}")

if __name__ == "__main__":
    main()
