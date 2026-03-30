import glob
import json
import os
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from urllib.request import urlopen
from urllib.parse import urlparse, unquote

RSS_PATH = "podcast.xml"
EP_DIR = "episodes"
MAX_ITEMS = 500
PUBLISHED_ISSUES_PATH = ".published_issues.json"

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "atom": "http://www.w3.org/2005/Atom",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def load_episodes():
    eps = []
    for p in glob.glob(os.path.join(EP_DIR, "*.json")):
        with open(p, "r", encoding="utf-8") as f:
            eps.append(json.load(f))

    def sort_key(e):
        return e.get("publish_at") or e.get("episode_date") or ""

    eps.sort(key=sort_key, reverse=True)
    return eps


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


def rfc2822_from_dt(dt_utc: datetime) -> str:
    return dt_utc.strftime("%a, %d %b %Y %H:%M:%S +0000")


def episode_link(ep: dict) -> str:
    if ep.get("episode_url"):
        return str(ep["episode_url"]).strip()
    return str(ep["audio_url"]).strip()


def is_publishable(ep: dict, now_utc: datetime) -> bool:
    dt = parse_iso8601(ep.get("publish_at", ""))
    if dt is None:
        return False
    if not (ep.get("audio_url") and ep.get("title") and ep.get("description")):
        return False
    return now_utc >= dt


def ensure_child_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = text
    return child


def ensure_itunes_child_text(parent: ET.Element, local_tag: str, text: str) -> ET.Element:
    tag = f"{{{NS['itunes']}}}{local_tag}"
    child = ET.SubElement(parent, tag)
    child.text = text
    return child


def get_archive_file_size(item_id: str, filename: str) -> int | None:
    url = f"https://archive.org/metadata/{item_id}"
    with urlopen(url) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    for f in data.get("files", []):
        if f.get("name") == filename and "size" in f:
            try:
                return int(f["size"])
            except ValueError:
                return None
    return None


def filename_from_audio_url(audio_url: str) -> str | None:
    if not audio_url:
        return None

    parsed = urlparse(audio_url)
    if not parsed.path:
        return None

    name = os.path.basename(parsed.path)
    if not name:
        return None

    return unquote(name)


def episode_guid(ep: dict) -> str:
    return str(ep.get("guid") or ep.get("slug") or ep["audio_url"]).strip()


def existing_rss_guids(channel: ET.Element) -> set[str]:
    guids = set()

    for item in channel.findall("item"):
        guid_el = item.find("guid")
        if guid_el is not None and guid_el.text and guid_el.text.strip():
            guids.add(guid_el.text.strip())
            continue

        link_el = item.find("link")
        if link_el is not None and link_el.text and link_el.text.strip():
            guids.add(link_el.text.strip())

    return guids


def write_published_issues(issue_numbers: list[int]):
    with open(PUBLISHED_ISSUES_PATH, "w", encoding="utf-8") as f:
        json.dump(issue_numbers, f, indent=2)
        f.write("\n")


def main():
    now_utc = datetime.now(timezone.utc)

    tree = ET.parse(RSS_PATH)
    root = tree.getroot()

    channel = root.find("channel")
    if channel is None:
        raise SystemExit("podcast.xml missing <channel>")

    old_guids = existing_rss_guids(channel)

    for item in list(channel.findall("item")):
        channel.remove(item)

    episodes = load_episodes()

    publishable = [ep for ep in episodes if is_publishable(ep, now_utc)]

    if MAX_ITEMS and len(publishable) > MAX_ITEMS:
        publishable = publishable[:MAX_ITEMS]

    newly_published_issue_numbers = []

    for ep in publishable:
        publish_dt = parse_iso8601(ep["publish_at"])
        pubdate = rfc2822_from_dt(publish_dt)

        item = ET.SubElement(channel, "item")

        ensure_child_text(item, "title", str(ep["title"]).strip())
        ensure_child_text(item, "description", str(ep["description"]).strip())
        ensure_child_text(item, "link", episode_link(ep))

        enc_attrib = {"url": str(ep["audio_url"]).strip(), "type": "audio/mpeg"}
        filename = filename_from_audio_url(str(ep["audio_url"]).strip())
        if filename:
            size = get_archive_file_size("sthelena-daily-rosary", filename)
            if size:
                enc_attrib["length"] = str(size)
        ET.SubElement(item, "enclosure", attrib=enc_attrib)

        ensure_child_text(item, "pubDate", pubdate)

        if ep.get("duration"):
            ensure_itunes_child_text(item, "duration", str(ep["duration"]).strip())

        guid_value = episode_guid(ep)
        guid_el = ET.SubElement(item, "guid", attrib={"isPermaLink": "false"})
        guid_el.text = guid_value

        if guid_value not in old_guids:
            source_issue = ep.get("source_issue")
            if isinstance(source_issue, int):
                newly_published_issue_numbers.append(source_issue)
            elif isinstance(source_issue, str) and source_issue.strip().isdigit():
                newly_published_issue_numbers.append(int(source_issue.strip()))

    ET.indent(tree, space="  ", level=0)
    tree.write(RSS_PATH, encoding="utf-8", xml_declaration=True)

    newly_published_issue_numbers = sorted(set(newly_published_issue_numbers))
    write_published_issues(newly_published_issue_numbers)

    print(f"Newly published issues: {newly_published_issue_numbers}")


if __name__ == "__main__":
    main()
