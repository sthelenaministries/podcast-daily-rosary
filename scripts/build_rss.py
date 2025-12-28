import glob
import json
import os
from datetime import datetime
import xml.etree.ElementTree as ET

RSS_PATH = "podcasts.xml"
EP_DIR = "episodes"

def load_episodes():
    eps = []
    for p in sorted(glob.glob(os.path.join(EP_DIR, "*.json"))):
        with open(p, "r", encoding="utf-8") as f:
            eps.append(json.load(f))
    # newest first by episode_date
    def key(e):
        return e.get("episode_date", "")
    eps.sort(key=key, reverse=True)
    return eps

def rfc2822_date(yyyy_mm_dd: str) -> str:
    # Use noon UTC to avoid timezone edge weirdness
    dt = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return dt.strftime("%a, %d %b %Y 12:00:00 +0000")

def ensure_text(el, tag, text):
    child = el.find(tag)
    if child is None:
        child = ET.SubElement(el, tag)
    child.text = text
    return child

def main():
    tree = ET.parse(RSS_PATH)
    root = tree.getroot()

    channel = root.find("channel")
    if channel is None:
        raise SystemExit("podcasts.xml missing <channel>")

    # Remove existing items
    for item in list(channel.findall("item")):
        channel.remove(item)

    episodes = load_episodes()

    for e in episodes:
        title = (e.get("title") or "").strip()
        desc = (e.get("description") or "").strip()
        audio_url = (e.get("audio_url") or "").strip()

        if not title or not desc:
            # Skip incomplete drafts from appearing publicly
            continue
        if not audio_url:
            # Skip if no enclosure URL yet
            continue

        item = ET.SubElement(channel, "item")
        ensure_text(item, "title", title)
        ensure_text(item, "description", desc)
        ensure_text(item, "pubDate", rfc2822_date(e["episode_date"]))

        # guid: stable slug
        guid = ensure_text(item, "guid", e.get("slug", title))
        guid.set("isPermaLink", "false")

        # enclosure
        enc = ET.SubElement(item, "enclosure")
        enc.set("url", audio_url)
        enc.set("type", "audio/mpeg")
        # length is optional; leave off unless you store it

    tree.write(RSS_PATH, encoding="utf-8", xml_declaration=True)

if __name__ == "__main__":
    main()
