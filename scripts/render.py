"""Render the profile art into assets/ from content.toml and data/stats.json.

Every SVG comes in a dark and a light variant (README picks one with <picture>),
embeds only the glyphs it uses from fonts/ as a base64 @font-face, and animates
with CSS keyframes only (GitHub serves README SVGs as images: no JS, no external
CSS). The un-animated state of every element is its final state, so readers with
reduced motion, or a renderer that skips animation, still see the full design.

    python scripts/render.py
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import pathlib
import random
import sys
import tomllib
from collections import defaultdict

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CONTENT = tomllib.loads((ROOT / "content.toml").read_text(encoding="utf-8"))
STATS = json.loads((ROOT / "data" / "stats.json").read_text(encoding="utf-8"))

try:
    import brotli  # noqa: F401

    FLAVOR = "woff2"
except Exception:  # noqa: BLE001 - zlib WOFF is fine, just a little larger
    FLAVOR = "woff"

# "Ink & Amber", the same tokens as abhayemekar.me
THEMES = {
    "dark": {
        "bg": "#0b0b0c",
        "elev": "#131315",
        "fg": "#edeae4",
        "muted": "#a6a29a",
        "subtle": "#8b8680",
        "border": "#26262a",
        "strong": "#35353a",
        "cell": "#1c1c20",
        "accent": "#e8a33d",
        "accent_text": "#e8a33d",
        "ok": "#4ac26b",
    },
    "light": {
        "bg": "#f7f5f1",
        "elev": "#ffffff",
        "fg": "#14161a",
        "muted": "#55524c",
        "subtle": "#6b6862",
        "border": "#e0dcd4",
        "strong": "#cfc9bf",
        "cell": "#e9e5dd",
        "accent": "#c98a2a",
        "accent_text": "#8f5a0c",
        "ok": "#1a7f37",
    },
}

FONT_FILES = {
    "serif": "serif-400.ttf",
    "serif-i": "serif-400-italic.ttf",
    "sans": "sans-400.ttf",
    "sans-m": "sans-500.ttf",
    "sans-b": "sans-600.ttf",
    "mono": "mono-400.ttf",
    "mono-b": "mono-600.ttf",
}
FALLBACK = {
    "serif": "Georgia,'Times New Roman',serif",
    "sans": "'Segoe UI',Helvetica,Arial,sans-serif",
    "mono": "ui-monospace,SFMono-Regular,Consolas,monospace",
}
EASE = "cubic-bezier(.16,1,.3,1)"


class Face:
    def __init__(self, key: str):
        self.path = ROOT / "fonts" / FONT_FILES[key]
        tt = TTFont(self.path)
        self.cmap = tt.getBestCmap()
        self.adv = {g: m[0] for g, m in tt["hmtx"].metrics.items()}
        self.upm = tt["head"].unitsPerEm

    def width(self, s: str, size: float, tracking: float = 0) -> float:
        units = sum(self.adv.get(self.cmap.get(ord(c), ".notdef"), self.upm // 2) for c in s)
        return units * size / self.upm + tracking * size * len(s)


FACES = {k: Face(k) for k in FONT_FILES}


def measure(s: str, face: str, size: float, tracking: float = 0) -> float:
    return FACES[face].width(s, size, tracking)


def wrap(s: str, face: str, size: float, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial, face, size) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def f(n: float) -> str:
    return f"{n:.1f}".rstrip("0").rstrip(".")


class Svg:
    def __init__(self, w: int, h: int, theme: dict, title: str, desc: str):
        self.w, self.h, self.t = w, h, theme
        self.title, self.desc = title, desc
        self.body: list[str] = []
        self.css: list[str] = []
        self.used: dict[str, set[str]] = defaultdict(set)

    def add(self, s: str) -> None:
        self.body.append(s)

    def text(self, x, y, s, face, size, fill, *, anchor=None, tracking=0, cls="", style="", attrs=""):
        self.used[face].update(s)
        a = f' text-anchor="{anchor}"' if anchor else ""
        ls = f' letter-spacing="{f(tracking * size)}"' if tracking else ""
        c = f"ff-{face}"
        if cls:
            c += " " + cls
        st = f' style="{style}"' if style else ""
        return (
            f'<text x="{f(x)}" y="{f(y)}" class="{c}" font-size="{f(size)}" fill="{fill}"'
            f"{a}{ls}{st}{attrs}>{esc(s)}</text>"
        )

    def font_css(self) -> str:
        out = []
        for key, chars in sorted(self.used.items()):
            chars = set(chars) | {" "}
            tt = TTFont(FACES[key].path)
            opts = subset.Options()
            opts.layout_features = ["kern", "liga", "calt", "tnum"]
            opts.name_IDs = []
            opts.notdef_outline = True
            sub = subset.Subsetter(opts)
            sub.populate(text="".join(sorted(chars)))
            sub.subset(tt)
            tt.flavor = FLAVOR
            buf = io.BytesIO()
            tt.save(buf)
            b64 = base64.b64encode(buf.getvalue()).decode()
            fam = f"ay-{key}"
            generic = FALLBACK[key.split("-")[0]]
            out.append(
                f"@font-face{{font-family:'{fam}';src:url(data:font/{FLAVOR};base64,{b64}) format('{FLAVOR}')}}"
                f".ff-{key}{{font-family:'{fam}',{generic}}}"
            )
        return "".join(out)

    def render(self) -> str:
        css = (
            self.font_css()
            + "".join(self.css)
            + "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t d">'
            f'<title id="t">{esc(self.title)}</title><desc id="d">{esc(self.desc)}</desc>'
            f"<style>{css}</style>" + "".join(self.body) + "</svg>\n"
        )


def card_bg(svg: Svg, rx: int = 20) -> None:
    t = svg.t
    svg.add(
        f'<rect x=".5" y=".5" width="{svg.w - 1}" height="{svg.h - 1}" rx="{rx}" '
        f'fill="{t["bg"]}" stroke="{t["border"]}"/>'
    )


def fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def today_label() -> str:
    stamp = STATS.get("generated_at")
    d = dt.datetime.fromisoformat(stamp) if stamp else dt.datetime.now(dt.timezone.utc)
    return d.strftime("%d %b %Y").lstrip("0")


# --------------------------------------------------------------------------- hero


def hero(theme: dict) -> Svg:
    p = CONTENT["person"]
    svg = Svg(
        1200,
        420,
        theme,
        f"{p['name']} — {p['eyebrow']}",
        f"{p['lede_pre']} {p['lede_em']} {p['lede_post']} Focus: {', '.join(p['focus'])}.",
    )
    t = theme
    card_bg(svg, 24)
    svg.css.append(
        f"@keyframes up{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:none}}}}"
        f".up{{animation:up .9s {EASE} both}}"
        "@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}"
        ".pulse{animation:pulse 2.4s ease-in-out infinite}"
        "@keyframes ring{from{transform:scale(1);opacity:.6}to{transform:scale(3.2);opacity:0}}"
        ".ring{transform-box:fill-box;transform-origin:center;animation:ring 2.4s ease-out infinite}"
    )

    # eyebrow with a live dot
    svg.add(f'<circle cx="62" cy="70" r="5" fill="{t["accent"]}" class="ring"/>')
    svg.add(f'<circle cx="62" cy="70" r="5" fill="{t["accent"]}" class="pulse"/>')
    svg.add(svg.text(82, 76, p["eyebrow"].upper(), "mono-b", 16, t["subtle"], tracking=0.1))

    # name, decoded out of noise: a bg-coloured curtain shrinks left to right while
    # noise glyphs flicker at each letter just before it is uncovered
    nx, ny, nsize = 54, 186, 108
    name = p["name"]
    nw = measure(name, "serif", nsize)
    start, dur = 0.35, 1.25
    svg.add(svg.text(nx, ny, name, "serif", nsize, t["fg"]))
    svg.css.append(
        "@keyframes wipe{from{transform:scaleX(1)}to{transform:scaleX(0)}}"
        f".curtain{{transform:scaleX(0);transform-box:fill-box;transform-origin:right;"
        f"animation:wipe {dur}s cubic-bezier(.45,0,.55,1) {start}s both}}"
        "@keyframes scan{from{transform:translateX(0);opacity:1}97%{opacity:1}to{transform:translateX(var(--w));opacity:0}}"
        f".scan{{opacity:0;animation:scan {dur}s cubic-bezier(.45,0,.55,1) {start}s both}}"
        "@keyframes flick{from,to{opacity:1}}"
        ".nz{opacity:0;animation:flick .07s linear both;animation-fill-mode:none}"
    )
    svg.add(
        f'<rect class="curtain" x="{f(nx - 4)}" y="{ny - 100}" width="{f(nw + 16)}" height="128" fill="{t["bg"]}"/>'
    )
    rng = random.Random(7)
    noise = "#%&*+=<>/{}[]$@01?!~^"
    x = nx
    for ch in name:
        cw = measure(ch, "serif", nsize)
        if ch.strip():
            reveal = start + dur * ((x + cw * 0.7 - nx) / (nw + 16))
            for k in range(4):
                g = rng.choice(noise)
                d = reveal - 0.3 + k * 0.075
                svg.add(
                    svg.text(
                        x + cw / 2, ny - 8, g, "mono-b", 64, t["accent"], anchor="middle",
                        cls="nz", style=f"animation-delay:{d:.2f}s",
                    )
                )
        x += cw
    svg.add(
        f'<rect class="scan" x="{f(nx - 2)}" y="{ny - 92}" width="3" height="112" rx="1.5" '
        f'fill="{t["accent"]}" style="--w:{f(nw + 12)}px"/>'
    )

    # lede: sans with the emphasis in amber serif italic (as on abhayemekar.me)
    l1y, l2y = 252, 298
    svg.add(svg.text(56, l1y, p["lede_pre"], "sans", 33, t["muted"], cls="up", style="animation-delay:1.35s"))
    em_w = measure(p["lede_em"], "serif-i", 40)
    svg.add(svg.text(56, l2y, p["lede_em"], "serif-i", 40, t["accent_text"], cls="up", style="animation-delay:1.5s"))
    svg.add(
        svg.text(56 + em_w + 10, l2y, p["lede_post"], "sans", 33, t["muted"], cls="up", style="animation-delay:1.5s")
    )

    # focus chips
    cx, cy = 56, 334
    for i, label in enumerate(p["focus"]):
        tw = measure(label.upper(), "mono-b", 15, 0.06)
        w = tw + 34
        svg.add(
            f'<g class="up" style="animation-delay:{1.7 + i * 0.08:.2f}s">'
            f'<rect x="{f(cx)}" y="{cy}" width="{f(w)}" height="38" rx="19" fill="none" stroke="{t["strong"]}"/>'
            + svg.text(cx + 17, cy + 24.5, label.upper(), "mono-b", 15, t["muted"], tracking=0.06)
            + "</g>"
        )
        cx += w + 10

    # right: noise → signal. Cells start scattered and settle into a grid; the lit
    # ones are the real contribution days of the last 15 weeks.
    cal = STATS.get("activity", {}).get("calendar", [])
    weeks = 15
    days = cal[-(weeks * 7) :] if cal else []
    # align so each column is a Sunday-to-Saturday week, like GitHub's graph
    if days:
        first = dt.date.fromisoformat(days[0]["date"])
        pad = (first.weekday() + 1) % 7
        days = [None] * pad + days
        days = days[-(weeks * 7) :] if len(days) > weeks * 7 else days
    peak = max([d["count"] for d in days if d] or [1]) or 1
    cell, gap = 20, 6
    pitch = cell + gap
    gx = 1144 - (weeks * pitch - gap)
    gy = 96
    svg.css.append(
        "@keyframes settle{from{transform:translate(var(--dx),var(--dy)) rotate(var(--r)) scale(.6);opacity:0}"
        "30%{opacity:1}to{transform:none;opacity:1}}"
        f".cell{{transform-box:fill-box;transform-origin:center;animation:settle 1.6s {EASE} both}}"
        "@keyframes lit{from{fill:var(--off)}}"
        ".lit{animation:settle 1.6s " + EASE + " both,lit .9s ease-out both}"
    )
    for i in range(weeks * 7):
        col, row = divmod(i, 7)
        d = days[i] if i < len(days) else None
        count = d["count"] if d else 0
        x0, y0 = gx + col * pitch, gy + row * pitch
        dx = rng.uniform(-420, 60)
        dy = rng.uniform(-150, 150)
        r = rng.uniform(-120, 120)
        delay = 0.2 + col * 0.045 + rng.uniform(0, 0.25)
        if count:
            level = 0.35 + 0.65 * min(1, (count / peak) ** 0.6)
            fill = t["accent"]
            style = (
                f"--dx:{dx:.0f}px;--dy:{dy:.0f}px;--r:{r:.0f}deg;--off:{t['cell']};"
                f"animation-delay:{delay:.2f}s,{2.0 + col * 0.05:.2f}s"
            )
            svg.add(
                f'<rect class="cell lit" x="{x0}" y="{y0}" width="{cell}" height="{cell}" rx="5" '
                f'fill="{fill}" fill-opacity="{level:.2f}" style="{style}"/>'
            )
        else:
            style = f"--dx:{dx:.0f}px;--dy:{dy:.0f}px;--r:{r:.0f}deg;animation-delay:{delay:.2f}s"
            svg.add(
                f'<rect class="cell" x="{x0}" y="{y0}" width="{cell}" height="{cell}" rx="5" '
                f'fill="{t["cell"]}" style="{style}"/>'
            )
    gw = weeks * pitch - gap
    gh = 7 * pitch - gap
    svg.add(
        svg.text(
            gx + gw, gy + gh + 40, f"noise → signal · {weeks} weeks of commits", "mono", 15,
            t["subtle"], anchor="end", cls="up", style="animation-delay:2.4s",
        )
    )
    return svg


# --------------------------------------------------------------------------- project cards


def fill_placeholders(s: str) -> str:
    npm = STATS.get("npm", {})
    while (i := s.find("{npm:")) != -1:
        j = s.index("}", i)
        pkg = s[i + 5 : j]
        s = s[:i] + fmt_int(npm.get(pkg, "—")) + s[j + 1 :]
    return s


def project_version(p: dict) -> str | None:
    if p.get("version") == "release":
        return STATS.get("repos", {}).get("repos", {}).get(p["repo"], {}).get("release")
    return p.get("badge")


def card(p: dict, theme: dict) -> Svg:
    # Shown at ~49% of an ~830px README (about 406px), so 560 units scale by ~0.72:
    # every size below is picked for its on-screen size, not its SVG size.
    t = theme
    W, H, pad = 560, 376, 28
    facts = [fill_placeholders(x) for x in p["facts"]]
    svg = Svg(W, H, theme, f"{p['name']} — {p['tagline']}", " ".join(facts))
    card_bg(svg, 20)
    svg.css.append(
        "@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}.pulse{animation:pulse 2.4s ease-in-out infinite}"
    )
    svg.add(svg.text(pad, 48, p["kind"].upper(), "mono-b", 14, t["subtle"], tracking=0.06))

    # right-aligned pills: version, then live status (only when today's check passed)
    rx = W - pad
    live = STATS.get("live", {}).get(p["slug"], {})
    if live.get("ok"):
        w = measure("LIVE", "mono-b", 14, 0.06) + 38
        rx -= w
        svg.add(
            f'<rect x="{f(rx)}" y="28" width="{f(w)}" height="30" rx="15" fill="none" stroke="{t["ok"]}" stroke-opacity=".55"/>'
            f'<circle cx="{f(rx + 15)}" cy="43" r="4.5" fill="{t["ok"]}" class="pulse"/>'
            + svg.text(rx + 26, 48, "LIVE", "mono-b", 14, t["ok"], tracking=0.06)
        )
        rx -= 8
    version = project_version(p)
    if version:
        w = measure(version, "mono", 14) + 26
        rx -= w
        svg.add(
            f'<rect x="{f(rx)}" y="28" width="{f(w)}" height="30" rx="15" fill="none" stroke="{t["strong"]}"/>'
            + svg.text(rx + 13, 48, version, "mono", 14, t["muted"])
        )
    if measure(p["kind"].upper(), "mono-b", 14, 0.06) > rx - pad - 12:
        print(f"warn: {p['slug']} kind collides with pills")

    svg.add(svg.text(pad - 2, 118, p["name"], "serif", 58, t["fg"]))
    lines = wrap(p["tagline"], "sans", 21, W - 2 * pad)
    if len(lines) > 2:
        print(f"warn: {p['slug']} tagline wraps to {len(lines)} lines")
    for i, line in enumerate(lines[:2]):
        svg.add(svg.text(pad, 158 + i * 28, line, "sans", 21, t["muted"]))

    for i, fact in enumerate(facts):
        y = 238 + i * 30
        if measure(fact, "sans", 17.5) > W - 2 * pad - 24:
            print(f"warn: {p['slug']} fact too long: {fact}")
        svg.add(svg.text(pad, y, "→", "mono-b", 16, t["accent_text"]))
        svg.add(svg.text(pad + 24, y, fact, "sans", 17.5, t["fg"]))

    x = pad
    for chip in p["stack"]:
        w = measure(chip, "mono", 13.5) + 22
        if x + w > W - pad:
            break
        svg.add(
            f'<rect x="{f(x)}" y="322" width="{f(w)}" height="28" rx="14" fill="none" stroke="{t["border"]}"/>'
            + svg.text(x + 11, 341, chip, "mono", 13.5, t["subtle"])
        )
        x += w + 8
    return svg


# --------------------------------------------------------------------------- signal


def signal(theme: dict) -> Svg:
    t = theme
    a = STATS.get("activity", {})
    r = STATS.get("repos", {})
    live_count = sum(1 for v in STATS.get("live", {}).values() if v.get("ok"))
    npm_total = sum(STATS.get("npm", {}).values())
    tiles = [
        (fmt_int(a.get("contributions", "—")), "contributions"),
        (fmt_int(a.get("merged_prs_year", "—")), "PRs merged"),
        (fmt_int(r.get("releases", "—")), "releases shipped"),
        (fmt_int(live_count), "products live"),
        (fmt_int(npm_total), "npm downloads/30d"),
    ]
    svg = Svg(
        1200,
        496,
        theme,
        "Activity on my personal GitHub over the last 12 months",
        "; ".join(f"{v} {k}" for v, k in tiles),
    )
    card_bg(svg, 24)
    svg.css.append(
        f"@keyframes up{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:none}}}}"
        f".up{{animation:up .8s {EASE} both}}"
        "@keyframes grow{from{transform:scaleY(0)}}"
        f".bar{{transform-box:fill-box;transform-origin:bottom;animation:grow .9s {EASE} both}}"
    )

    # KPI tiles
    x0, y0, gap = 40, 40, 14
    tw = (1200 - 2 * x0 - 4 * gap) / 5
    for i, (value, label) in enumerate(tiles):
        x = x0 + i * (tw + gap)
        svg.add(
            f'<g class="up" style="animation-delay:{0.1 + i * 0.08:.2f}s">'
            f'<rect x="{f(x)}" y="{y0}" width="{f(tw)}" height="124" rx="16" fill="{t["elev"]}" stroke="{t["border"]}"/>'
            + svg.text(x + 20, y0 + 68, value, "serif", 62, t["fg"])
            + svg.text(x + 20, y0 + 100, label.upper(), "mono-b", 14, t["subtle"], tracking=0.04)
            + "</g>"
        )

    # weekly bars
    cal = a.get("calendar", [])
    weeks: list[tuple[str, int]] = []
    for i in range(0, len(cal), 7):
        chunk = cal[i : i + 7]
        weeks.append((chunk[0]["date"], sum(d["count"] for d in chunk)))
    weeks = weeks[-53:]
    bx0, bx1, by0, by1 = 40, 1160, 214, 362
    pitch = (bx1 - bx0) / max(len(weeks), 1)
    bw = pitch * 0.62
    peak = max([w for _, w in weeks] or [1]) or 1
    svg.add(f'<line x1="{bx0}" y1="{by1 + 0.5}" x2="{bx1}" y2="{by1 + 0.5}" stroke="{t["border"]}"/>')
    peak_i = max(range(len(weeks)), key=lambda i: weeks[i][1]) if weeks else 0
    last_month = None
    for i, (start, total) in enumerate(weeks):
        x = bx0 + i * pitch + (pitch - bw) / 2
        h = max(3, (by1 - by0) * (total / peak) ** 0.75) if total else 3
        fill = t["accent"] if total else t["strong"]
        svg.add(
            f'<rect class="bar" x="{f(x)}" y="{f(by1 - h)}" width="{f(bw)}" height="{f(h)}" rx="{f(min(4, bw / 2))}" '
            f'fill="{fill}" style="animation-delay:{0.5 + i * 0.018:.3f}s"/>'
        )
        month = start[:7]
        if month != last_month and (i == 0 or int(start[8:]) <= 7):
            label = dt.date.fromisoformat(start).strftime("%b")
            svg.add(svg.text(x, by1 + 28, label.upper(), "mono", 14, t["subtle"], tracking=0.04))
            last_month = month
    if weeks:
        px = bx0 + peak_i * pitch + pitch / 2
        anchor = "end" if px > 1000 else "start"
        svg.add(
            svg.text(
                px + (-10 if anchor == "end" else 10), by0 + 14, f"busiest week · {peak}",
                "mono", 15, t["subtle"], anchor=anchor, cls="up", style="animation-delay:1.6s",
            )
        )

    # languages
    langs = r.get("languages", [])
    total = sum(x["bytes"] for x in langs) or 1
    top = [x for x in langs if x["bytes"] / total >= 0.05][:4]
    other = total - sum(x["bytes"] for x in top)
    parts = [(x["name"], x["bytes"] / total) for x in top] + ([("Other", other / total)] if other > 0 else [])
    shades = [1, 0.66, 0.42, 0.26]
    ly = 420
    x = 40.0
    width = 1120
    svg.add(f'<clipPath id="lb"><rect x="40" y="{ly}" width="{width}" height="10" rx="5"/></clipPath><g clip-path="url(#lb)">')
    for i, (name, share) in enumerate(parts):
        w = width * share
        fill = t["accent"] if name != "Other" else t["strong"]
        op = shades[i] if name != "Other" else 1
        svg.add(f'<rect x="{f(x)}" y="{ly}" width="{f(w + 0.5)}" height="10" fill="{fill}" fill-opacity="{op}"/>')
        x += w
    svg.add("</g>")
    lx = 40.0
    for i, (name, share) in enumerate(parts):
        fill = t["accent"] if name != "Other" else t["strong"]
        op = shades[i] if name != "Other" else 1
        label = f"{name} {share * 100:.0f}%"
        svg.add(f'<rect x="{f(lx)}" y="{ly + 30}" width="12" height="12" rx="3" fill="{fill}" fill-opacity="{op}"/>')
        svg.add(svg.text(lx + 20, ly + 41.5, label, "mono", 15, t["muted"]))
        lx += 20 + measure(label, "mono", 15) + 28
    svg.add(
        svg.text(1160, ly + 41.5, f"updated {today_label()}", "mono", 14, t["subtle"], anchor="end")
    )
    return svg


# --------------------------------------------------------------------------- path


def path(theme: dict) -> Svg:
    t = theme
    items = CONTENT["path"]
    svg = Svg(
        1200,
        392,
        theme,
        "Career path",
        " → ".join(f"{i['when']}: {i['role']}, {i['org']}" for i in items),
    )
    card_bg(svg, 24)
    x0, x1, ly = 72, 1128, 200
    n = len(items)
    xs = [x0 + i * (x1 - x0) / (n - 1) for i in range(n)]
    length = x1 - x0
    draw = 2.0
    svg.css.append(
        f"@keyframes draw{{from{{stroke-dashoffset:{length}}}}}"
        f".line{{stroke-dasharray:{length};animation:draw {draw}s cubic-bezier(.65,0,.35,1) .2s both}}"
        "@keyframes pop{from{transform:scale(0)}}"
        f".node{{transform-box:fill-box;transform-origin:center;animation:pop .5s {EASE} both}}"
        f"@keyframes up{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}"
        f".up{{animation:up .7s {EASE} both}}"
        "@keyframes ring{from{transform:scale(1);opacity:.7}to{transform:scale(2.8);opacity:0}}"
        ".ring{transform-box:fill-box;transform-origin:center;animation:ring 2.2s ease-out 2.4s infinite both}"
    )
    svg.add(f'<line x1="{x0}" y1="{ly}" x2="{x1}" y2="{ly}" stroke="{t["strong"]}" stroke-width="2"/>')
    svg.add(
        f'<line class="line" x1="{x0}" y1="{ly}" x2="{x1}" y2="{ly}" stroke="{t["accent"]}" stroke-width="2"/>'
    )
    extents: list[tuple[bool, float, float, str]] = []
    for i, (item, x) in enumerate(zip(items, xs)):
        at = 0.2 + draw * (x - x0) / length
        above = i % 2 == 0
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        tx = x - 14 if i == 0 else x + 14 if i == n - 1 else x
        current = item.get("current")
        if current:
            svg.add(f'<circle cx="{f(x)}" cy="{ly}" r="9" fill="{t["accent"]}" class="ring"/>')
        svg.add(
            f'<circle class="node" cx="{f(x)}" cy="{ly}" r="{9 if current else 7}" '
            f'fill="{t["accent"] if current else t["bg"]}" stroke="{t["accent"]}" stroke-width="2.5" '
            f'style="animation-delay:{at:.2f}s"/>'
        )
        tick_y0, tick_y1 = (ly - 16, ly - 30) if above else (ly + 16, ly + 30)
        svg.add(
            f'<line x1="{f(x)}" y1="{tick_y0}" x2="{f(x)}" y2="{tick_y1}" stroke="{t["strong"]}" '
            f'class="up" style="animation-delay:{at:.2f}s"/>'
        )
        base = 54 if above else 258
        g = [
            svg.text(tx, base, item["when"].upper(), "mono-b", 15, t["accent_text"], anchor=anchor, tracking=0.04),
            svg.text(tx, base + 32, item["role"], "sans-b", 24, t["fg"], anchor=anchor),
            svg.text(tx, base + 60, item["org"], "sans", 20, t["muted"], anchor=anchor),
            svg.text(tx, base + 88, item["note"], "sans", 17.5, t["subtle"], anchor=anchor),
        ]
        widest = max(
            measure(item["when"].upper(), "mono-b", 15, 0.04),
            measure(item["note"], "sans", 17.5),
            measure(item["role"], "sans-b", 24),
            measure(item["org"], "sans", 20),
        )
        left = tx if anchor == "start" else tx - widest if anchor == "end" else tx - widest / 2
        extents.append((above, left, left + widest, item["org"]))
        svg.add(f'<g class="up" style="animation-delay:{at + 0.1:.2f}s">' + "".join(g) + "</g>")
    for (a_up, _, a_r, a_org), (b_up, b_l, _, b_org) in zip(extents, extents[2:]):
        if a_up == b_up and a_r + 24 > b_l:
            print(f"warn: path labels overlap: {a_org} / {b_org} by {a_r + 24 - b_l:.0f}px")
    return svg


# --------------------------------------------------------------------------- stack


def stack(theme: dict) -> Svg:
    t = theme
    groups = CONTENT["stack"]
    label_w = 200
    cx0, cx1 = 40 + label_w, 1160
    rows = []
    for g in groups:
        lines: list[list[tuple[str, float]]] = [[]]
        x = cx0
        for item in g["items"]:
            w = measure(item, "sans-m", 19) + 36
            if x + w > cx1 and lines[-1]:
                lines.append([])
                x = cx0
            lines[-1].append((item, w))
            x += w + 10
        rows.append((g, lines))
    chip_h, line_gap, pad = 42, 10, 22
    h = 2 * 24 + sum(len(ls) * chip_h + (len(ls) - 1) * line_gap + 2 * pad for _, ls in rows)
    svg = Svg(
        1200,
        int(h),
        theme,
        "Toolbox",
        "; ".join(f"{g['label']}: {', '.join(g['items'])}" for g in groups),
    )
    card_bg(svg, 24)
    svg.css.append(
        f"@keyframes up{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}"
        f".up{{animation:up .6s {EASE} both}}"
    )
    y = 24
    k = 0
    for ri, (g, lines) in enumerate(rows):
        block = len(lines) * chip_h + (len(lines) - 1) * line_gap + 2 * pad
        if ri:
            svg.add(f'<line x1="40" y1="{y}" x2="1160" y2="{y}" stroke="{t["border"]}"/>')
        svg.add(svg.text(40, y + pad + 27, g["label"].upper(), "mono-b", 15, t["subtle"], tracking=0.06))
        cy = y + pad
        for line in lines:
            x = cx0
            for item, w in line:
                strong = item in g.get("strong", [])
                stroke = t["accent"] if strong else t["strong"]
                fill = f'fill="{t["accent"]}" fill-opacity=".1"' if strong else 'fill="none"'
                color = t["accent_text"] if strong else t["fg"]
                svg.add(
                    f'<g class="up" style="animation-delay:{0.05 + k * 0.025:.3f}s">'
                    f'<rect x="{f(x)}" y="{f(cy)}" width="{f(w)}" height="{chip_h}" rx="21" {fill} stroke="{stroke}"/>'
                    + svg.text(x + 18, cy + 27.5, item, "sans-m", 19, color)
                    + "</g>"
                )
                x += w + 10
                k += 1
            cy += chip_h + line_gap
        y += block
    return svg


# --------------------------------------------------------------------------- link buttons


def button(link: dict, theme: dict) -> Svg:
    t = theme
    kind, label = link["kind"].upper(), link["label"]
    kw = measure(kind, "mono-b", 13, 0.08)
    lw = measure(label, "sans-m", 17)
    w = int(24 + kw + 14 + lw + 12 + 14 + 22)
    svg = Svg(w, 52, theme, f"{link['kind']}: {label}", link["url"])
    svg.add(
        f'<rect x=".5" y=".5" width="{w - 1}" height="51" rx="26" fill="{t["bg"]}" stroke="{t["strong"]}"/>'
    )
    svg.add(svg.text(24, 31, kind, "mono-b", 13, t["accent_text"], tracking=0.08))
    svg.add(svg.text(24 + kw + 14, 32, label, "sans-m", 17, t["fg"]))
    ax = 24 + kw + 14 + lw + 12
    svg.add(
        f'<path d="M{f(ax)} {f(32)}l9 -9m-6 0h6v6" fill="none" stroke="{t["muted"]}" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return svg


# --------------------------------------------------------------------------- main


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ASSETS.mkdir(exist_ok=True)
    jobs = {"hero": hero, "signal": signal, "path": path, "stack": stack}
    for p in CONTENT["project"]:
        jobs[f"card-{p['slug']}"] = lambda theme, p=p: card(p, theme)
    for link in CONTENT.get("link", []):
        jobs[f"link-{link['slug']}"] = lambda theme, link=link: button(link, theme)
    for name, fn in jobs.items():
        for mode, theme in THEMES.items():
            out = ASSETS / f"{name}-{mode}.svg"
            out.write_text(fn(theme).render(), encoding="utf-8")
        print(f"{name}: {(ASSETS / f'{name}-dark.svg').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
