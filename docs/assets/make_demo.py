"""Animated terminal demo for the README, built from a real `carpool stats` run.

    python docs/assets/make_demo.py

The output is captured live from the committed Claude Code results, so the demo
shows measured numbers, not a mock. CSS animation only (GitHub strips scripts).
"""
import html
import subprocess
import sys
from pathlib import Path

from fonts import MONO, font_face_css

ROOT = Path(__file__).resolve().parents[2]
CMD = "carpool stats results/squad48-holdout-claude-lean.json"

out = subprocess.run([sys.executable, "-m", "carpool.cli", "stats", "results/squad48-holdout-claude-lean.json"],
                     cwd=ROOT, capture_output=True, text=True, check=True).stdout.rstrip("\n").splitlines()

CH, LH, PAD, TOP = 7.83, 19, 22, 52  # char width (13px mono), line height, padding, first line
width = int(max(len(CMD) + 2, *(len(l) for l in out)) * CH + 2 * PAD)
height = TOP + (len(out) + 2) * LH + PAD

css = [
    "text{font-family:%s;font-size:13px;white-space:pre}" % MONO,
    ".l{opacity:0;animation:show .01s forwards}",
    "@keyframes show{to{opacity:1}}",
    "@keyframes type{from{width:0}to{width:%dpx}}" % int((len(CMD) + 2) * CH),
    "#typed{animation:type 1.6s steps(%d) .4s forwards}" % (len(CMD) + 2),
    "@keyframes blink{50%{opacity:0}}",
    ".cur{animation:blink 1s steps(1) infinite}",
]
lines = []
y = TOP
lines.append(f'<clipPath id="clip"><rect id="typed" x="{PAD}" y="{y - 14}" width="0" height="{LH}"/></clipPath>')
lines.append(f'<text x="{PAD}" y="{y}" clip-path="url(#clip)"><tspan fill="#1840ff">$</tspan>'
             f'<tspan fill="#000000"> {html.escape(CMD)}</tspan></text>')
delay = 2.3
for i, l in enumerate(out):
    y += LH
    color = "#666666"
    if l.startswith("carpool"):
        color = "#1840ff"
    elif l.startswith(("baseline", "route", "bench/")):
        color = "#000000"
    lines.append(f'<text class="l" style="animation-delay:{delay + i * 0.18:.2f}s" x="{PAD}" y="{y}" '
                 f'fill="{color}">{html.escape(l)}</text>')
y += LH * 1.4
end = delay + len(out) * 0.18 + 0.2
lines.append(f'<text class="l" style="animation-delay:{end:.2f}s" x="{PAD}" y="{y:.0f}" fill="#1840ff">$ '
             f'<tspan class="cur" fill="#000000">▍</tspan></text>')

FONTS = font_face_css(CMD + "".join(out) + "$▍CARPOOL·REAL CLAUDE CODE RESULTS", faces=("LMMono",))
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height:.0f}" viewBox="0 0 {width} {height:.0f}" role="img" aria-label="Terminal running {CMD}: 48 SQuAD questions on lean Claude calls; carpool uses 3 calls instead of 48, 46 of 48 accepted against 42, and costs 78% less per accepted answer on the first run, with 0 regressions.">
<title>carpool stats on real Claude Code results</title>
<style>{FONTS}{"".join(css)}</style>
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1:.0f}" fill="#ffffff" stroke="#000000"/>
<line x1="0" y1="30.5" x2="{width}" y2="30.5" stroke="#000000" stroke-width="0.6"/>
<text x="{PAD}" y="20" fill="#666666" style="font-size:10.5px;letter-spacing:1.4px">CARPOOL · MEASURED ON LEAN CLAUDE CALLS</text>
{chr(10).join(lines)}
</svg>
'''
(Path(__file__).resolve().parent / "demo.svg").write_text(svg)
print(f"demo.svg {width}x{height:.0f}, {len(out)} output lines")
