"""One-time font prep: pull Geist and Geist Mono (OFL) from google/fonts, pin the
variable fonts to the static weights the art uses, and trim them to Latin.

The renderer subsets these again per SVG, so this only keeps the repo small.

    python scripts/build_fonts.py            # download + build into fonts/
    python scripts/build_fonts.py --src DIR  # build from already-downloaded TTFs
"""

from __future__ import annotations

import argparse
import pathlib
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "fonts"
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl"

SOURCES = {
    "Geist-VF.ttf": f"{GF}/geist/Geist%5Bwght%5D.ttf",
    "GeistMono-VF.ttf": f"{GF}/geistmono/GeistMono%5Bwght%5D.ttf",
}

# output name -> (source file, wght)
BUILDS = {
    "geist-400.ttf": ("Geist-VF.ttf", 400),
    "geist-500.ttf": ("Geist-VF.ttf", 500),
    "geist-600.ttf": ("Geist-VF.ttf", 600),
    "geist-800.ttf": ("Geist-VF.ttf", 800),
    "geistmono-400.ttf": ("GeistMono-VF.ttf", 400),
    "geistmono-600.ttf": ("GeistMono-VF.ttf", 600),
}

LATIN = "U+0020-007E,U+00A0-00FF,U+2013-2014,U+2018-201D,U+2022,U+2026,U+2190-2193,U+2197,U+25CF,U+2713,U+00B7"


def build(src_dir: pathlib.Path) -> None:
    OUT.mkdir(exist_ok=True)
    for out_name, (src_name, wght) in BUILDS.items():
        font = instancer.instantiateVariableFont(TTFont(src_dir / src_name), {"wght": wght})
        opts = subset.Options()
        opts.layout_features = ["kern", "liga", "calt", "tnum"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=subset.parse_unicodes(LATIN))
        sub.subset(font)
        font.save(OUT / out_name)
        print(f"{out_name}: {(OUT / out_name).stat().st_size // 1024} KB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=pathlib.Path)
    args = ap.parse_args()
    src = args.src
    if src is None:
        src = ROOT / ".fontsrc"
        src.mkdir(exist_ok=True)
        for name, url in SOURCES.items():
            if not (src / name).exists():
                urllib.request.urlretrieve(url, src / name)
    build(src)


if __name__ == "__main__":
    main()
