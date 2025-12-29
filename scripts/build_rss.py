import glob
import json
import os
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

RSS_PATH = "podcast.xml"
EP_DIR = "episodes"

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
    # Prefer explicit episode page URL if provided
    if ep.get("episode_url"):
        return str(ep["episode_url"]).strip()
    # Fallback: use the direct audio URL
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

def main():
    now_utc = datetime.now(timezone.utc)

    tree = ET.parse(RSS_PATH)
    root = tree.getroot()

    channel = root.find("channel")
    if channel is None:
        raise SystemExit("podcast.xml missing <channel>")

    # Remove existing <item> elements only
    for item in list(channel.findall("item")):
        channel.remove(item)

    episodes = load_episodes()

    for ep in episodes:
        if not is_publishable(ep, now_utc):
            continue

        publish_dt = parse_iso8601(ep["publish_at"])
        pubdate = rfc2822_from_dt(publish_dt)

        item = ET.SubElement(channel, "item")

        ensure_child_text(item, "title", str(ep["title"]).strip())
        ensure_child_text(item, "description", str(ep["description"]).strip())
        ensure_child_text(item, "link", episode_link(ep))

        # enclosure
        enc_attrib = {"url": str(ep["audio_url"]).strip(), "type": "audio/mpeg"}
        if ep.get("length"):
            enc_attrib["length"] = str(ep["length"]).strip()
        ET.SubElement(item, "enclosure", attrib=enc_attrib)

        ensure_child_text(item, "pubDate", pubdate)

        if ep.get("duration"):
            ensure_itunes_child_text(item, "duration", str(ep["duration"]).strip())

        guid_value = ep.get("guid") or ep.get("slug") or ep["audio_url"]
        guid_el = ET.SubElement(item, "guid", attrib={"isPermaLink": "false"})
        guid_el.text = str(guid_value).strip()

    tree.write(RSS_PATH, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
