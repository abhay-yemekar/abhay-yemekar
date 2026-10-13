"""Pull the tech logos used by content.toml from Simple Icons (CC0) into data/icons.json.

Run after adding a logo slug to content.toml. Pinned so the art never changes underneath us.
    python scripts/fetch_icons.py
"""

import json
import pathlib
import re
import tomllib
import urllib.request

VERSION = "16.32.0"
CDN = f"https://cdn.jsdelivr.net/npm/simple-icons@{VERSION}"
ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTENT = tomllib.loads((ROOT / "content.toml").read_text(encoding="utf-8"))


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "abhay-yemekar-profile-art"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def slugs() -> set[str]:
    out = set()
    for cap in CONTENT.get("capability", []):
        out.update(t for t in cap["tools"] if not t.startswith("text:"))
    for p in CONTENT.get("project", []):
        out.update(t for t in p.get("logos", []))
    return out


def main() -> None:
    meta = json.loads(get(f"{CDN}/data/simple-icons.json"))
    meta = meta["icons"] if isinstance(meta, dict) else meta
    by_slug = {i.get("slug") or re.sub(r"[^a-z0-9]", "", i["title"].lower()): i for i in meta}
    icons = {}
    for slug in sorted(slugs()):
        svg = get(f"{CDN}/icons/{slug}.svg").decode()
        icons[slug] = {
            "title": by_slug[slug]["title"],
            "hex": "#" + by_slug[slug]["hex"],
            "path": re.search(r' d="([^"]+)"', svg).group(1),
        }
    (ROOT / "data" / "icons.json").write_text(json.dumps(icons, indent=1) + "\n", encoding="utf-8")
    print(f"{len(icons)} icons from simple-icons@{VERSION}")


if __name__ == "__main__":
    main()
