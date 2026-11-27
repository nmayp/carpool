"""Embed subsetted Latin Modern fonts in SVG figures.

GitHub shows README images as <img>, which can't load external fonts, so each
SVG carries its own @font-face with a WOFF2 subset limited to the characters it
uses. Source fonts: Latin Modern (GUST Font License), from a TeX Live install.
"""
import base64
import io
from pathlib import Path

LM_DIRS = [Path("/usr/share/texmf/fonts/opentype/public/lm"),
           Path("/usr/share/texlive/texmf-dist/fonts/opentype/public/lm")]
FACES = {
    "LMRoman": ("lmroman10-regular.otf", "normal", "normal"),
    "LMRomanBold": ("lmroman10-bold.otf", "normal", "normal"),
    "LMMono": ("lmmono10-regular.otf", "normal", "normal"),
}

SERIF = "LMRoman, 'Latin Modern Roman', 'Computer Modern', Georgia, serif"
SERIF_BOLD = "LMRomanBold, 'Latin Modern Roman', Georgia, serif"
MONO = "LMMono, 'Latin Modern Mono', ui-monospace, monospace"


def _find(fname: str) -> Path:
    for d in LM_DIRS:
        if (d / fname).exists():
            return d / fname
    raise FileNotFoundError(f"{fname}: install TeX Live's lm fonts")


def font_face_css(text: str, faces=("LMRoman", "LMRomanBold", "LMMono")) -> str:
    from fontTools import subset
    from fontTools.ttLib import TTFont

    chars = "".join(sorted(set(text) | set(" 0123456789")))
    css = []
    for name in faces:
        fname, style, weight = FACES[name]
        font = TTFont(_find(fname))
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern", "liga"]
        opts.name_IDs = []
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(text=chars)
        sub.subset(font)
        buf = io.BytesIO()
        font.flavor = "woff2"
        font.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode()
        css.append(f"@font-face{{font-family:{name};src:url(data:font/woff2;base64,{b64}) format('woff2');"
                   f"font-style:{style};font-weight:{weight}}}")
    return "".join(css)


def visible_text(svg_body: str) -> str:
    """All characters that may be drawn, so the subset covers them."""
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", " ", svg_body))
