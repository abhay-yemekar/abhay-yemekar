"""Render the profile art into assets/ from content.toml and data/stats.json.

Every SVG comes in a dark and a light variant (README picks one with <picture>),
embeds only the glyphs it uses from fonts/ as a base64 @font-face, and animates
with CSS keyframes only (GitHub serves README SVGs as images: no JS, no external
CSS, no external images). The un-animated state of every element is its final
state, so readers with reduced motion, or a renderer that skips animation, still
see the full design.

Sizes are chosen for how big things look on github.com: the README column is
~830px wide, so a 1200-unit SVG renders at ~0.69x and a half-width card at ~0.72x.

    python scripts/render.py
"""

from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import pathlib
import sys
import tomllib
from collections import defaultdict

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CONTENT = tomllib.loads((ROOT / "content.toml").read_text(encoding="utf-8"))
STATS = json.loads((ROOT / "data" / "stats.json").read_text(encoding="utf-8"))
ICONS = json.loads((ROOT / "data" / "icons.json").read_text(encoding="utf-8"))

try:
    import brotli  # noqa: F401

    FLAVOR = "woff2"
except Exception:  # noqa: BLE001 - zlib WOFF is fine, just a little larger
    FLAVOR = "woff"

# "Graphite": near-black, zinc neutrals, one electric-blue accent.
THEMES = {
    "dark": {
        "bg": "#0a0a0b", "surface": "#111114", "surface2": "#18181b", "border": "#1f1f23", "strong": "#2e2e35",
        "fg": "#fafafa", "muted": "#a1a1aa", "subtle": "#71717a", "faint": "#3f3f46",
        "accent": "#3b82f6", "accent_text": "#60a5fa", "glow": "#3b82f6", "glow_op": ".16", "ok": "#22c55e",
        "kw": "#c084fc", "str": "#4ade80", "id": "#60a5fa", "punc": "#e4e4e7", "cmt": "#8b8b94",
        "overlay": "#0a0a0b",
    },
    "light": {
        "bg": "#ffffff", "surface": "#fafafa", "surface2": "#f4f4f5", "border": "#e4e4e7", "strong": "#d4d4d8",
        "fg": "#09090b", "muted": "#52525b", "subtle": "#71717a", "faint": "#a1a1aa",
        "accent": "#2563eb", "accent_text": "#1d4ed8", "glow": "#3b82f6", "glow_op": ".10", "ok": "#16a34a",
        "kw": "#9333ea", "str": "#15803d", "id": "#2563eb", "punc": "#27272a", "cmt": "#71717a",
        "overlay": "#ffffff",
    },
}

FONT_FILES = {
    "geist": "geist-400.ttf",
    "geist-m": "geist-500.ttf",
    "geist-s": "geist-600.ttf",
    "geist-x": "geist-800.ttf",
    "mono": "geistmono-400.ttf",
    "mono-b": "geistmono-600.ttf",
}
FALLBACK = {
    "geist": "'Segoe UI',Helvetica,Arial,sans-serif",
    "mono": "ui-monospace,SFMono-Regular,Consolas,monospace",
}
EASE = "cubic-bezier(.16,1,.3,1)"
SHORT_NAMES = {
    "Node.js": "Node.js", "Google Gemini": "Gemini", "Google Chrome": "Chrome", "OpenAPI Initiative": "OpenAPI",
    "JSON Web Tokens": "JWT", "GitHub Actions": "Actions", "Next.js": "Next.js", "Tailwind CSS": "Tailwind",
    "SQLAlchemy": "SQLAlchemy", "Drizzle": "Drizzle ORM",
}


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


def luminance(hex_: str) -> float:
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    h = hex_.lstrip("#")
    r, g, b = (ch(h[i : i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def icon_color(slug: str, t: dict) -> str:
    hex_ = ICONS[slug]["hex"]
    return hex_ if contrast(hex_, t["surface2"]) >= 2.2 else t["fg"]


def tool_name(slug: str) -> str:
    title = ICONS[slug]["title"]
    return SHORT_NAMES.get(title, title)


def fill_placeholders(s: str) -> str:
    npm = STATS.get("npm", {})
    while (i := s.find("{npm:")) != -1:
        j = s.index("}", i)
        s = s[:i] + fmt_int(npm.get(s[i + 5 : j], "—")) + s[j + 1 :]
    live = sum(1 for v in STATS.get("live", {}).values() if v.get("ok"))
    prs = STATS.get("activity", {}).get("merged_prs_year", "—")
    return s.replace("{live}", str(live)).replace("{prs}", fmt_int(prs))


def fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def today_label() -> str:
    stamp = STATS.get("generated_at")
    d = dt.datetime.fromisoformat(stamp) if stamp else dt.datetime.now(dt.timezone.utc)
    return d.strftime("%d %b %Y").lstrip("0")


class Svg:
    def __init__(self, w: int, h: int, theme: dict, title: str, desc: str):
        self.w, self.h, self.t = w, h, theme
        self.title, self.desc = title, desc
        self.defs: list[str] = []
        self.body: list[str] = []
        self.css: list[str] = []
        self.used: dict[str, set[str]] = defaultdict(set)

    def add(self, s: str) -> None:
        self.body.append(s)

    def text(self, x, y, s, face, size, fill, *, anchor=None, tracking=0, cls="", style="", attrs=""):
        self.used[face].update(s)
        a = f' text-anchor="{anchor}"' if anchor else ""
        ls = f' letter-spacing="{f(tracking * size)}"' if tracking else ""
        c = f"ff-{face}" + (f" {cls}" if cls else "")
        st = f' style="{style}"' if style else ""
        return (
            f'<text x="{f(x)}" y="{f(y)}" class="{c}" font-size="{f(size)}" fill="{fill}"'
            f"{a}{ls}{st}{attrs}>{esc(s)}</text>"
        )

    def icon(self, slug: str, x: float, y: float, size: float, color: str) -> str:
        s = size / 24
        return f'<path transform="translate({f(x)} {f(y)}) scale({s:.4f})" d="{ICONS[slug]["path"]}" fill="{color}"/>'

    def font_css(self) -> str:
        out = []
        for key, chars in sorted(self.used.items()):
            tt = TTFont(FACES[key].path)
            opts = subset.Options()
            opts.layout_features = ["kern", "liga", "calt", "tnum"]
            opts.name_IDs = []
            opts.notdef_outline = True
            sub = subset.Subsetter(opts)
            sub.populate(text="".join(sorted(set(chars) | {" "})))
            sub.subset(tt)
            tt.flavor = FLAVOR
            buf = io.BytesIO()
            tt.save(buf)
            b64 = base64.b64encode(buf.getvalue()).decode()
            generic = FALLBACK["mono" if key.startswith("mono") else "geist"]
            out.append(
                f"@font-face{{font-family:'ay-{key}';src:url(data:font/{FLAVOR};base64,{b64}) format('{FLAVOR}')}}"
                f".ff-{key}{{font-family:'ay-{key}',{generic}}}"
            )
        return "".join(out)

    def render(self) -> str:
        css = (
            self.font_css()
            + "".join(self.css)
            + "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
        )
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t d">'
            f'<title id="t">{esc(self.title)}</title><desc id="d">{esc(self.desc)}</desc>'
            f"<style>{css}</style>{defs}" + "".join(self.body) + "</svg>\n"
        )


UP = (
    "@keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}"
    f".up{{animation:up .8s {EASE} both}}"
)


def frame(svg: Svg, rx: int = 24, fill: str | None = None) -> None:
    t = svg.t
    svg.add(
        f'<rect x=".5" y=".5" width="{svg.w - 1}" height="{svg.h - 1}" rx="{rx}" '
        f'fill="{fill or t["bg"]}" stroke="{t["border"]}"/>'
    )


# --------------------------------------------------------------------------- hero


def code_lines(p: dict) -> list[list[tuple[str, str]]]:
    lines = [[("const ", "kw"), ("abhay", "id"), (" = {", "punc")]]
    for key, value in p["code"].items():
        toks = [(f"  {key}", "punc"), (": ", "punc")]
        if isinstance(value, list):
            toks.append(("[", "punc"))
            for i, v in enumerate(value):
                toks += ([(", ", "punc")] if i else []) + [(f'"{v}"', "str")]
            toks.append(("]", "punc"))
        else:
            toks.append((f'"{value}"', "str"))
        lines.append(toks + [(",", "punc")])
    lines.append([("};", "punc")])
    lines.append([(fill_placeholders(p["code_footer"]), "cmt")])
    return lines


def hero(theme: dict) -> Svg:
    p = CONTENT["person"]
    t = theme
    svg = Svg(
        1200, 420, theme,
        f"{p['name']}, {p['role_1']} {p['role_2']}",
        " ".join(p["tagline"]) + " " + p["meta"],
    )
    svg.defs.append(
        f'<radialGradient id="glow" cx="78%" cy="45%" r="45%"><stop offset="0" stop-color="{t["glow"]}" stop-opacity="{t["glow_op"]}"/>'
        f'<stop offset="1" stop-color="{t["glow"]}" stop-opacity="0"/></radialGradient>'
    )
    frame(svg)
    svg.add('<rect x="1" y="1" width="1198" height="418" rx="24" fill="url(#glow)"/>')
    svg.css.append(UP)

    svg.add(svg.text(56, 92, p["name"].upper(), "mono-b", 15, t["subtle"], tracking=0.16, cls="up", style="animation-delay:.05s"))
    svg.add(svg.text(52, 172, p["role_1"], "geist-x", 76, t["fg"], tracking=-0.035, cls="up", style="animation-delay:.15s"))
    svg.add(svg.text(52, 252, p["role_2"], "geist-x", 76, t["accent"], tracking=-0.035, cls="up", style="animation-delay:.25s"))
    for i, line in enumerate(p["tagline"]):
        svg.add(svg.text(56, 304 + i * 31, line, "geist", 21, t["muted"], cls="up", style=f"animation-delay:{.4 + i * .06:.2f}s"))
    svg.add(svg.text(56, 382, p["meta"], "mono", 14, t["subtle"], cls="up", style="animation-delay:.6s", attrs=' xml:space="preserve"'))

    # editor window that types itself
    x0, y0, w, h = 596, 48, 560, 324
    svg.add(
        f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="14" fill="{t["surface"]}" stroke="{t["strong"]}"/>'
        f'<path d="M{x0} {y0 + 40}h{w}" stroke="{t["border"]}"/>'
        f'<circle cx="{x0 + 22}" cy="{y0 + 20}" r="6" fill="#ff5f57"/><circle cx="{x0 + 42}" cy="{y0 + 20}" r="6" fill="#febc2e"/>'
        f'<circle cx="{x0 + 62}" cy="{y0 + 20}" r="6" fill="#28c840"/>'
    )
    svg.add(svg.text(x0 + w / 2, y0 + 25, p["code_file"], "mono", 13, t["subtle"], anchor="middle"))
    size, lh = 15, 30
    cw = measure("0", "mono", size)
    cx = x0 + 52
    svg.css.append(
        "@keyframes type{from{transform:scaleX(1)}to{transform:scaleX(0)}}"
        ".cur{transform-box:fill-box;transform-origin:right;transform:scaleX(0)}"
        "@keyframes ride{from{transform:translateX(0)}to{transform:translateX(var(--w))}}"
        "@keyframes on{from,to{opacity:1}}"
        ".car{opacity:0}"
        "@keyframes blink{0%,49%{opacity:0}50%,100%{opacity:1}}"
    )
    at = 0.7
    lines = code_lines(p)
    for i, toks in enumerate(lines):
        y = y0 + 76 + i * lh
        svg.add(svg.text(x0 + 34, y, str(i + 1), "mono", 13, t["faint"], anchor="end"))
        x = cx
        for txt, kind in toks:
            svg.add(svg.text(x, y, txt, "mono", size, t[kind], attrs=' xml:space="preserve"'))
            x += measure(txt, "mono", size)
        n = sum(len(txt) for txt, _ in toks)
        dur = max(0.25, n * 0.026)
        width = n * cw
        if width > w - 70:
            print(f"warn: code line {i + 1} overflows the editor")
        svg.add(
            f'<rect class="cur" x="{f(cx - 1)}" y="{y - 20}" width="{f(width + 2)}" height="28" fill="{t["surface"]}" '
            f'style="animation:type {dur:.2f}s steps({n}) {at:.2f}s both"/>'
        )
        svg.add(
            f'<rect class="car" x="{f(cx)}" y="{y - 17}" width="2" height="22" fill="{t["accent"]}" '
            f'style="--w:{f(width)}px;animation:ride {dur:.2f}s steps({n}) {at:.2f}s both,on {dur + 0.12:.2f}s linear {at:.2f}s"/>'
        )
        at += dur + 0.12
        last = (cx + width, y)
    svg.add(
        f'<rect x="{f(last[0] + 4)}" y="{last[1] - 17}" width="9" height="22" rx="1.5" fill="{t["accent"]}" '
        f'style="animation:blink 1.1s steps(1) {at:.2f}s infinite backwards"/>'
    )
    return svg


# --------------------------------------------------------------------------- project cards


def project_version(p: dict) -> str | None:
    if p.get("version") == "release":
        return STATS.get("repos", {}).get("repos", {}).get(p["repo"], {}).get("release")
    return p.get("badge")


def shot_uri(rel: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode((ROOT / rel).read_bytes()).decode()


def flow_art(svg: Svg, steps: list[str], W: int, H: int) -> None:
    """Pipeline diagram for projects without a UI: their real steps, from the README."""
    t = svg.t
    svg.defs.append(
        f'<pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.1" fill="{t["faint"]}" fill-opacity=".55"/></pattern>'
    )
    svg.add(f'<rect width="{W}" height="{H}" fill="{t["surface2"]}"/><rect width="{W}" height="{H}" fill="url(#dots)"/>')
    size, padx, ph, gap = 15, 14, 36, 26
    widths = [measure(s, "mono", size) + 2 * padx for s in steps]
    rows: list[list[int]] = [[]]
    x = 0
    for i, wd in enumerate(widths):
        if rows[-1] and x + wd > W - 80:
            rows.append([])
            x = 0
        rows[-1].append(i)
        x += wd + gap
    total_h = len(rows) * ph + (len(rows) - 1) * 26
    y = (H - total_h) / 2
    for r, row in enumerate(rows):
        row_w = sum(widths[i] for i in row) + gap * (len(row) - 1)
        x = (W - row_w) / 2
        for k, i in enumerate(row):
            last = i == len(steps) - 1
            stroke = t["accent"] if last else t["strong"]
            fill = t["accent"] if last else t["bg"]
            color = "#ffffff" if last else t["fg"]
            svg.add(
                f'<rect x="{f(x)}" y="{f(y)}" width="{f(widths[i])}" height="{ph}" rx="9" fill="{fill}" stroke="{stroke}"/>'
                + svg.text(x + padx, y + 23, steps[i], "mono", size, color)
            )
            if k < len(row) - 1:
                ax = x + widths[i] + 5
                svg.add(f'<path d="M{f(ax)} {f(y + ph / 2)}h{gap - 10}m-5 -4l5 4l-5 4" fill="none" stroke="{t["subtle"]}" stroke-width="1.6" stroke-linecap="round"/>')
            x += widths[i] + gap
        y += ph + 26


def card(p: dict, theme: dict) -> Svg:
    t = theme
    W, H, IH, pad = 560, 420, 236, 28
    metric = fill_placeholders(p.get("metric", ""))
    svg = Svg(W, H, theme, f"{p['name']}: {p['tagline']}", f"{p['kind']}. {metric}")
    svg.defs.append(
        f'<clipPath id="top"><path d="M.5 {IH}V18.5a18 18 0 0 1 18-18h{W - 37}a18 18 0 0 1 18 18V{IH}z"/></clipPath>'
        f'<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset=".72" stop-color="{t["overlay"]}" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="{t["overlay"]}" stop-opacity=".55"/></linearGradient>'
    )
    svg.css.append("@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}.pulse{animation:pulse 2.4s ease-in-out infinite}")
    frame(svg, 18)
    svg.add('<g clip-path="url(#top)">')
    if p.get("shot"):
        svg.add(f'<image href="{shot_uri(p["shot"])}" x="0" y="0" width="{W}" height="{IH}" preserveAspectRatio="xMidYMin slice"/>')
        svg.add(f'<rect width="{W}" height="{IH}" fill="url(#fade)"/>')
    else:
        flow_art(svg, p["flow"], W, IH)
    svg.add("</g>")
    svg.add(f'<path d="M.5 {IH}.5h{W - 1}" stroke="{t["border"]}"/>')

    # pills over the image: version, then live status (only when today's check passed)
    rx = W - 16
    live = STATS.get("live", {}).get(p["slug"], {})
    pill_bg = f'fill="{t["bg"]}" fill-opacity=".9"'
    if live.get("ok"):
        w = measure("LIVE", "mono-b", 13, 0.06) + 40
        rx -= w
        svg.add(
            f'<rect x="{f(rx)}" y="16" width="{f(w)}" height="30" rx="15" {pill_bg} stroke="{t["ok"]}" stroke-opacity=".6"/>'
            f'<circle cx="{f(rx + 16)}" cy="31" r="4.5" fill="{t["ok"]}" class="pulse"/>'
            + svg.text(rx + 27, 35.5, "LIVE", "mono-b", 13, t["ok"], tracking=0.06)
        )
        rx -= 8
    version = project_version(p)
    if version:
        w = measure(version, "mono", 13) + 26
        rx -= w
        svg.add(
            f'<rect x="{f(rx)}" y="16" width="{f(w)}" height="30" rx="15" {pill_bg} stroke="{t["strong"]}"/>'
            + svg.text(rx + 13, 35.5, version, "mono", 13, t["fg"])
        )

    svg.add(svg.text(pad, IH + 38, p["kind"].upper(), "mono-b", 13, t["subtle"], tracking=0.06))
    svg.add(svg.text(pad - 1, IH + 76, p["name"], "geist-x", 32, t["fg"], tracking=-0.02))
    lines = wrap(p["tagline"], "geist", 17.5, W - 2 * pad)
    if len(lines) > 2:
        print(f"warn: {p['slug']} tagline wraps to {len(lines)} lines")
    for i, line in enumerate(lines[:2]):
        svg.add(svg.text(pad, IH + 108 + i * 25, line, "geist", 17.5, t["muted"]))

    y = H - 34
    x = pad
    for slug in p["logos"]:
        svg.add(svg.icon(slug, x, y - 9, 20, icon_color(slug, t)))
        x += 32
    if metric:
        mw = measure(metric, "mono", 13)
        if x + mw > W - pad:
            print(f"warn: {p['slug']} metric collides with logos")
        svg.add(svg.text(W - pad, y + 6, metric, "mono", 13, t["subtle"], anchor="end"))
    return svg


# --------------------------------------------------------------------------- capabilities


def capabilities(theme: dict) -> Svg:
    t = theme
    caps = CONTENT["capability"]
    left_w, x1, pill_h, gap_x, gap_y, pad_y = 330, 1160, 42, 10, 10, 26
    x0 = 40 + left_w
    layout = []
    for cap in caps:
        rows: list[list[tuple[str, float]]] = [[]]
        x = x0
        for tool in cap["tools"]:
            label = tool[5:] if tool.startswith("text:") else tool_name(tool)
            w = measure(label, "geist-m", 16) + (30 if tool.startswith("text:") else 58)
            if x + w > x1 and rows[-1]:
                rows.append([])
                x = x0
            rows[-1].append((tool, w))
            x += w + gap_x
        proof = wrap(cap["proof"], "mono", 13.5, left_w - 40)
        tools_h = len(rows) * pill_h + (len(rows) - 1) * gap_y
        text_h = 30 + 22 * len(proof)
        layout.append((cap, rows, proof, max(tools_h, text_h) + 2 * pad_y))
    H = 16 + sum(b for *_, b in layout) + 16
    svg = Svg(
        1200, int(H), theme, "Capabilities",
        "; ".join(
            f"{c['name']}: " + ", ".join(x[5:] if x.startswith("text:") else tool_name(x) for x in c["tools"])
            + f" (proof: {c['proof']})"
            for c in caps
        ),
    )
    frame(svg)
    svg.css.append(UP)
    y = 16
    k = 0
    for ri, (cap, rows, proof, block) in enumerate(layout):
        if ri:
            svg.add(f'<path d="M40 {y}.5H1160" stroke="{t["border"]}"/>')
        ty = y + pad_y
        svg.add(svg.text(40, ty + 22, cap["name"], "geist-s", 21, t["fg"]))
        for i, line in enumerate(proof):
            svg.add(svg.text(40, ty + 50 + i * 22, ("proof → " if i == 0 else "") + line, "mono", 13.5, t["muted"]))
        cy = ty
        for row in rows:
            x = x0
            for tool, w in row:
                g = [f'<rect x="{f(x)}" y="{cy}" width="{f(w)}" height="{pill_h}" rx="12" fill="{t["surface2"]}" stroke="{t["border"]}"/>']
                if tool.startswith("text:"):
                    g.append(svg.text(x + 15, cy + 27, tool[5:], "geist-m", 16, t["muted"]))
                else:
                    g.append(svg.icon(tool, x + 14, cy + 10, 22, icon_color(tool, t)))
                    g.append(svg.text(x + 46, cy + 27, tool_name(tool), "geist-m", 16, t["fg"]))
                svg.add(f'<g class="up" style="animation-delay:{0.05 + k * 0.018:.3f}s">' + "".join(g) + "</g>")
                x += w + gap_x
                k += 1
            cy += pill_h + gap_y
        y += block
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
    svg = Svg(1200, 496, theme, "Activity on my personal GitHub over the last 12 months", "; ".join(f"{v} {k}" for v, k in tiles))
    frame(svg)
    svg.css.append(
        UP + "@keyframes grow{from{transform:scaleY(0)}}"
        f".bar{{transform-box:fill-box;transform-origin:bottom;animation:grow .9s {EASE} both}}"
    )
    x0, y0, gap = 40, 40, 14
    tw = (1200 - 2 * x0 - 4 * gap) / 5
    for i, (value, label) in enumerate(tiles):
        x = x0 + i * (tw + gap)
        svg.add(
            f'<g class="up" style="animation-delay:{0.1 + i * 0.08:.2f}s">'
            f'<rect x="{f(x)}" y="{y0}" width="{f(tw)}" height="124" rx="14" fill="{t["surface2"]}" stroke="{t["border"]}"/>'
            + svg.text(x + 20, y0 + 66, value, "geist-x", 52, t["fg"], tracking=-0.03)
            + svg.text(x + 20, y0 + 100, label.upper(), "mono-b", 13, t["subtle"], tracking=0.04)
            + "</g>"
        )
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
    svg.add(f'<path d="M{bx0} {by1}.5H{bx1}" stroke="{t["border"]}"/>')
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
            svg.add(svg.text(x, by1 + 28, dt.date.fromisoformat(start).strftime("%b").upper(), "mono", 13, t["subtle"], tracking=0.04))
            last_month = month
    if weeks:
        px = bx0 + peak_i * pitch + pitch / 2
        anchor = "end" if px > 1000 else "start"
        svg.add(svg.text(px + (-10 if anchor == "end" else 10), by0 + 14, f"busiest week · {peak}", "mono", 14, t["subtle"], anchor=anchor))
    langs = r.get("languages", [])
    total = sum(x["bytes"] for x in langs) or 1
    top = [x for x in langs if x["bytes"] / total >= 0.05][:4]
    other = total - sum(x["bytes"] for x in top)
    parts = [(x["name"], x["bytes"] / total) for x in top] + ([("Other", other / total)] if other > 0 else [])
    shades = [1, 0.62, 0.38, 0.22]
    ly, x, width = 420, 40.0, 1120
    svg.defs.append(f'<clipPath id="lb"><rect x="40" y="{ly}" width="{width}" height="10" rx="5"/></clipPath>')
    svg.add('<g clip-path="url(#lb)">')
    for i, (name, share) in enumerate(parts):
        fill, op = (t["strong"], 1) if name == "Other" else (t["accent"], shades[i])
        svg.add(f'<rect x="{f(x)}" y="{ly}" width="{f(width * share + 0.5)}" height="10" fill="{fill}" fill-opacity="{op}"/>')
        x += width * share
    svg.add("</g>")
    lx = 40.0
    for i, (name, share) in enumerate(parts):
        fill, op = (t["strong"], 1) if name == "Other" else (t["accent"], shades[i])
        label = f"{name} {share * 100:.0f}%"
        svg.add(f'<rect x="{f(lx)}" y="{ly + 30}" width="12" height="12" rx="3" fill="{fill}" fill-opacity="{op}"/>')
        svg.add(svg.text(lx + 20, ly + 41.5, label, "mono", 14, t["muted"]))
        lx += 20 + measure(label, "mono", 14) + 28
    svg.add(svg.text(1160, ly + 41.5, f"updated {today_label()}", "mono", 13, t["subtle"], anchor="end"))
    return svg


# --------------------------------------------------------------------------- path


def path(theme: dict) -> Svg:
    t = theme
    items = CONTENT["path"]
    svg = Svg(1200, 392, theme, "Career path", " → ".join(f"{i['when']}: {i['role']}, {i['org']}" for i in items))
    frame(svg)
    x0, x1, ly = 72, 1128, 200
    n = len(items)
    xs = [x0 + i * (x1 - x0) / (n - 1) for i in range(n)]
    length = x1 - x0
    draw = 2.0
    svg.css.append(
        UP + f"@keyframes draw{{from{{stroke-dashoffset:{length}}}}}"
        f".line{{stroke-dasharray:{length};animation:draw {draw}s cubic-bezier(.65,0,.35,1) .2s both}}"
        "@keyframes pop{from{transform:scale(0)}}"
        f".node{{transform-box:fill-box;transform-origin:center;animation:pop .5s {EASE} both}}"
        "@keyframes ring{from{transform:scale(1);opacity:.7}to{transform:scale(2.8);opacity:0}}"
        ".ring{transform-box:fill-box;transform-origin:center;animation:ring 2.2s ease-out 2.4s infinite both}"
    )
    svg.add(f'<line x1="{x0}" y1="{ly}" x2="{x1}" y2="{ly}" stroke="{t["strong"]}" stroke-width="2"/>')
    svg.add(f'<line class="line" x1="{x0}" y1="{ly}" x2="{x1}" y2="{ly}" stroke="{t["accent"]}" stroke-width="2"/>')
    extents = []
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
            f'fill="{t["accent"] if current else t["bg"]}" stroke="{t["accent"]}" stroke-width="2.5" style="animation-delay:{at:.2f}s"/>'
        )
        ty0, ty1 = (ly - 16, ly - 30) if above else (ly + 16, ly + 30)
        svg.add(f'<line x1="{f(x)}" y1="{ty0}" x2="{f(x)}" y2="{ty1}" stroke="{t["strong"]}"/>')
        base = 54 if above else 258
        g = [
            svg.text(tx, base, item["when"].upper(), "mono-b", 14, t["accent_text"], anchor=anchor, tracking=0.04),
            svg.text(tx, base + 32, item["role"], "geist-s", 23, t["fg"], anchor=anchor, tracking=-0.01),
            svg.text(tx, base + 59, item["org"], "geist", 19, t["muted"], anchor=anchor),
            svg.text(tx, base + 86, item["note"], "geist", 16.5, t["subtle"], anchor=anchor),
        ]
        widest = max(
            measure(item["when"].upper(), "mono-b", 14, 0.04), measure(item["role"], "geist-s", 23),
            measure(item["org"], "geist", 19), measure(item["note"], "geist", 16.5),
        )
        left = tx if anchor == "start" else tx - widest if anchor == "end" else tx - widest / 2
        extents.append((above, left, left + widest, item["org"]))
        svg.add(f'<g class="up" style="animation-delay:{at + 0.1:.2f}s">' + "".join(g) + "</g>")
    for (a_up, _, a_r, a_org), (b_up, b_l, _, b_org) in zip(extents, extents[2:]):
        if a_up == b_up and a_r + 24 > b_l:
            print(f"warn: path labels overlap: {a_org} / {b_org} by {a_r + 24 - b_l:.0f}px")
    return svg


# --------------------------------------------------------------------------- link buttons


def button(link: dict, theme: dict) -> Svg:
    t = theme
    kind, label = link["kind"].upper(), link["label"]
    kw = measure(kind, "mono-b", 13, 0.08)
    lw = measure(label, "geist-m", 17)
    w = int(24 + kw + 14 + lw + 14 + 12 + 22)
    svg = Svg(w, 52, theme, f"{link['kind']}: {label}", link["url"])
    svg.add(f'<rect x=".5" y=".5" width="{w - 1}" height="51" rx="26" fill="{t["surface"]}" stroke="{t["strong"]}"/>')
    svg.add(svg.text(24, 31, kind, "mono-b", 13, t["accent_text"], tracking=0.08))
    svg.add(svg.text(24 + kw + 14, 32, label, "geist-m", 17, t["fg"]))
    ax = 24 + kw + 14 + lw + 14
    svg.add(
        f'<path d="M{f(ax)} 32l9 -9m-6 0h6v6" fill="none" stroke="{t["muted"]}" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return svg


# --------------------------------------------------------------------------- main


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ASSETS.mkdir(exist_ok=True)
    jobs = {"hero": hero, "capabilities": capabilities, "signal": signal, "path": path}
    for p in CONTENT["project"]:
        jobs[f"card-{p['slug']}"] = lambda theme, p=p: card(p, theme)
    for link in CONTENT.get("link", []):
        jobs[f"link-{link['slug']}"] = lambda theme, link=link: button(link, theme)
    for name, fn in jobs.items():
        for mode, theme in THEMES.items():
            (ASSETS / f"{name}-{mode}.svg").write_text(fn(theme).render(), encoding="utf-8")
        print(f"{name}: {(ASSETS / f'{name}-dark.svg').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
