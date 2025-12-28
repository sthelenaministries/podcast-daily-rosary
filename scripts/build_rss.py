import glob
import json
import os
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

RSS_PATH = "podcast.xml"
EP_DIR = "episodes"

# Namespaces in your RSS
NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "atom": "http://www.w3.org/2005/Atom",
}

# Ensure ElementTree preserves namespaces on write
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

def load_episodes():
    eps = []
    for p in glob.glob(os.path.join(EP_DIR, "*.json")):
        with open(p, "r", encoding="utf-8") as f:
            eps.append(json.load(f))

    # Sort newest first by publish_at (fallback to episode_date)
    def sort_key(e):
        return e.get("publish_at") or e.get("episode_date") or ""
    eps.sort(key=sort_key, reverse=True)
    return eps

def parse_iso8601(dt_str: str) -> datetime | None:
    """
    Accepts ISO8601 like:
      2026-01-03T05:00:00-05:00
      2026-01-03T10:00:00Z
    Returns aware datetime in UTC.
    """
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
        # If user supplied no timezone, assume UTC (better than guessing)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def rfc2822_from_dt(dt_utc: datetime) -> str:
    # RFC 2822 / RSS pubDate format
    return dt_utc.strftime("%a, %d %b %Y %H:%M:%S +0000")

def due_to_publish(ep: dict, now_utc: datetime) -> bool:
    # Must have scheduled publish time
    dt = parse_iso8601(ep.get("publish_at", ""))
    if dt is None:
        return False
    # Must have audio + title + description
    if not (ep.get("audio_url") and ep.get("title") and ep.get("description")):
        return False
    return now_utc >= dt

def ensure_child_text(parent: ET.Element, tag: str, text: str, attrib: dict | None = None) -> ET.Element:
    child = ET.SubElement(parent, tag, attrib=attrib or {})
    child.text = text
    return child

def ensure_itunes_child_text(parent: ET.Element, local_tag: str, text: str) -> ET.Element:
    # tag like itunes:duration
    tag = f"{{{NS['itunes']}}}{local_tag}"
    child = ET.SubElement(parent, tag)
    child.text = text
    return child

def main():
    now_utc = datetime.now(timezone.utc)

    tree = ET.parse(RSS_PATH)
    root = tree.getroot()

    channel = root.find("channel")
    if channel is None:
        raise SystemExit("podcast.xml missing <channel>")

    # Remove existing <item> elements only (preserve everything else)
    for item in list(channel.findall("item")):
        channel.remove(item)

    episodes = load_episodes()

    for ep in episodes:
        if not due_to_publish(ep, now_utc):
            continue

        publish_dt = parse_iso8601(ep["publish_at"])
        pubdate = rfc2822_from_dt(publish_dt)

        item = ET.SubElement(channel, "item")

        ensure_child_text(item, "title", str(ep["title"]).strip())
        ensure_child_text(item, "description", str(ep["description"]).strip())

        # enclosure
        enc_attrib = {"url": str(ep["audio_url"]).strip(), "type": "audio/mpeg"}
        if ep.get("length"):
            # length optional but supported by your template
            enc_attrib["length"] = str(ep["length"]).strip()
        ET.SubElement(item, "enclosure", attrib=enc_attrib)

        ensure_child_text(item, "pubDate", pubdate)

        if ep.get("duration"):
            ensure_itunes_child_text(item, "duration", str(ep["duration"]).strip())

        # guid (stable)
        guid_value = ep.get("guid") or ep.get("slug") or ep["audio_url"]
        guid_el = ET.SubElement(item, "guid", attrib={"isPermaLink": "false"})
        guid_el.text = str(guid_value).strip()

    tree.write(RSS_PATH, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
