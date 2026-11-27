"""Build the README figures (SVG) from the raw results.

    python docs/assets/make_figures.py

Style: white page, black ink, greys, one accent blue (#1840ff); Latin Modern serif for text and
Latin Modern Mono, uppercase and tracked, for labels (embedded, see fonts.py). Series are told
apart by fill pattern as well as colour: hatched black, solid black, solid blue, and every bar
carries its value.
"""
import json
import math
from pathlib import Path

from fonts import MONO, SERIF, SERIF_BOLD, font_face_css, visible_text

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

INK, INK2, INK3, RULE, BLUE, PAPER = "#000000", "#444444", "#666666", "#e4e4e4", "#1840ff", "#ffffff"


def load(name):
    return json.loads((ROOT / "results" / name).read_text())


def acc(r):
    return int(str(r["accepted"]).split("/")[0])


def cents(r):
    return 100 * r["usd"] / acc(r)


def mean(xs):
    return sum(xs) / len(xs)


def first(runs, prefix):
    return next(r for r in runs if r["route"].startswith(prefix))


# ---------------------------------------------------------------- data
ops = load("ops24-claude-code.json")["runs"]
hold = load("squad48-holdout-claude-code.json")["runs"]
ops_lean = load("ops24-claude-lean.json")["runs"]
hold_lean = load("squad48-holdout-claude-lean.json")["runs"]
squad = load("squad48-claude-code.json")["runs"]

# per set: (agent call per task, lean call per task, carpool first run on lean calls)
BARS = [
    ("24 ops tasks", cents(ops[0]), cents(ops_lean[0]), cents(first(ops_lean, "carpool#1"))),
    ("48 SQuAD questions", cents(hold[0]), cents(hold_lean[0]), cents(first(hold_lean, "carpool#1"))),
]

SWEEP = [(k, cents(next(r for r in squad if r["route"] == f"carpool k={k}"))) for k in (1, 4, 12, 24)]
SWEEP.append((48, mean([cents(r) for r in squad if r["route"].startswith("carpool#")])))
SWEEP_BASE = cents(squad[0])


# ---------------------------------------------------------------- drawing helpers
def svg(w, h, body, title):
    css = font_face_css(visible_text(body) + title)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{title}">\n<title>{title}</title>\n<style>{css}</style>\n'
            f'<defs><pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" '
            f'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="4" stroke="{INK}" stroke-width="1.1"/>'
            f'</pattern></defs>\n<rect width="{w}" height="{h}" fill="{PAPER}"/>\n{body}</svg>\n')


def mono(x, y, s, size=10.5, fill=INK3, anchor="start", spacing=1.4):
    return (f'<text x="{x}" y="{y}" font-family="{MONO}" font-size="{size}" fill="{fill}" '
            f'letter-spacing="{spacing}" text-anchor="{anchor}">{s.upper()}</text>')


def serif(x, y, s, size=14, fill=INK, anchor="start", bold=False):
    fam = SERIF_BOLD if bold else SERIF
    return f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" fill="{fill}" text-anchor="{anchor}">{s}</text>'


def header(fig, label, title, sub):
    return [mono(32, 34, f"Fig. {fig} · {label}"),
            serif(32, 66, title, size=24, bold=True),
            serif(32, 90, sub, size=14, fill=INK2)]


def footnote(y, text):
    return [f'<line x1="32" y1="{y}" x2="128" y2="{y}" stroke="{INK}" stroke-width="0.8"/>',
            serif(32, y + 18, text, size=12, fill=INK3)]


def swatch(x, y, kind):
    if kind == "hatch":
        return f'<rect x="{x}" y="{y}" width="14" height="10" fill="url(#hatch)" stroke="{INK}" stroke-width="0.8"/>'
    return f'<rect x="{x}" y="{y}" width="14" height="10" fill="{INK if kind == "black" else BLUE}"/>'


def bar(x, y, w, h, kind):
    if kind == "hatch":
        return f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{h}" fill="url(#hatch)" stroke="{INK}" stroke-width="0.8"/>'
    return f'<rect x="{x}" y="{y}" width="{w:.1f}" height="{h}" fill="{INK if kind == "black" else BLUE}"/>'


# ---------------------------------------------------------------- figure 1
def fig_cost():
    W, H = 760, 380
    x0, x1, vmax = 200, 700, 7.0
    sx = lambda v: x0 + (x1 - x0) * v / vmax
    b = header(1, "Cost", "Cost per accepted answer", "US cents. Same tasks, same model; lower is better.")
    series = [("one Claude Code agent call per task", "hatch"), ("one lean call per task", "black"),
              ("carpool, one request", "blue")]
    lx = 32
    for label, kind in series:
        b.append(swatch(lx, 108, kind))
        b.append(serif(lx + 20, 117.5, label, size=13, fill=INK2))
        lx += 20 + len(label) * 6.4 + 26
    top, bottom = 140, 312
    for v in range(0, 8):
        x = sx(v)
        b.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="{RULE}" stroke-width="0.6"/>')
        b.append(mono(f"{x:.1f}", bottom + 16, f"{v}¢", anchor="middle", spacing=0.5))
    b.append(f'<line x1="{x0}" y1="{top}" x2="{x0}" y2="{bottom}" stroke="{INK}" stroke-width="0.8"/>')
    y = top + 10
    for name, agent, lean, pool in BARS:
        b.append(serif(x0 - 14, y + 28, name, size=14, anchor="end"))
        for v, kind in ((agent, "hatch"), (lean, "black"), (pool, "blue")):
            w = max(sx(v) - x0, 2)
            b.append(bar(x0, y, w, 16, kind))
            b.append(mono(f"{x0 + w + 8:.1f}", y + 12, f"{v:.2f}¢", fill=INK, spacing=0.4))
            y += 19
        y += 22
    b += footnote(H - 36, "Claude Sonnet 5.5 via Claude Code, list prices; carpool shown on lean calls, first run.")
    return svg(W, H, "\n".join(b) + "\n", "Cost per accepted answer: agent call per task, lean call per task, carpool")


# ---------------------------------------------------------------- figure 2
def fig_sweep():
    W, H = 760, 360
    x0, x1, y0, y1, vmax = 72, 720, 270, 120, 10.0
    sx = lambda k: x0 + (x1 - x0) * (math.log2(k) / math.log2(48))
    sy = lambda v: y0 - (y0 - y1) * v / vmax
    b = header(2, "Group size", "Bigger groups, lower cost",
               "Cost per accepted answer by tasks per request; 48 SQuAD questions, agent calls, first run.")
    for v in range(0, 11, 2):
        y = sy(v)
        b.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="{RULE}" stroke-width="0.6"/>')
        b.append(mono(x0 - 10, f"{y + 3.5:.1f}", f"{v}¢", anchor="end", spacing=0.5))
    b.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" stroke="{INK}" stroke-width="0.8"/>')
    for k, _ in SWEEP:
        b.append(mono(f"{sx(k):.1f}", y0 + 18, str(k), anchor="middle", spacing=0.5))
    b.append(mono((x0 + x1) / 2, y0 + 38, "tasks per request · log scale", anchor="middle"))
    yb = sy(SWEEP_BASE)
    b.append(f'<line x1="{x0}" y1="{yb:.1f}" x2="{x1}" y2="{yb:.1f}" stroke="{INK}" stroke-width="0.8" stroke-dasharray="3 3"/>')
    b.append(serif(x1, f"{yb - 7:.1f}", f"one call per task: {SWEEP_BASE:.2f}¢", size=12.5, fill=INK2, anchor="end"))
    pts = " ".join(f"{sx(k):.1f},{sy(v):.1f}" for k, v in SWEEP)
    b.append(f'<polyline points="{pts}" fill="none" stroke="{BLUE}" stroke-width="1.6" stroke-linejoin="round"/>')
    for k, v in SWEEP:
        b.append(f'<circle cx="{sx(k):.1f}" cy="{sy(v):.1f}" r="3.5" fill="{BLUE}" stroke="{PAPER}" stroke-width="1.5"/>')
    k, v = SWEEP[0]
    b.append(mono(f"{sx(k) + 12:.1f}", f"{sy(v) - 4:.1f}", f"{v:.2f}¢", fill=INK, spacing=0.4))
    k, v = SWEEP[-1]
    b.append(mono(f"{sx(k) - 8:.1f}", f"{sy(v) - 10:.1f}", f"{v:.2f}¢", fill=INK, anchor="end", spacing=0.4))
    b += footnote(H - 28, "First SQuAD run, before the checker fix; the fixed holdout runs are cheaper still.")
    return svg(W, H, "\n".join(b) + "\n", "Cost per accepted answer by group size")


# ---------------------------------------------------------------- figure 0
def fig_how():
    W, H = 760, 300
    b = header(0, "Method", "Pay for the fixed part once, not once per task",
               "H: system prompt, tools, instructions (about 26k tokens for a Claude Code agent call).")
    b.append(swatch(32, 108, "hatch"))
    b.append(serif(52, 117.5, "fixed part H", size=13, fill=INK2))
    b.append(swatch(160, 108, "blue"))
    b.append(serif(180, 117.5, "one task: question and evidence", size=13, fill=INK2))
    b.append(mono(32, 150, "one call per task"))
    b.append(serif(32, 168, "24 requests; H paid 24 times", size=13, fill=INK3))
    y = 180
    for _ in range(6):
        b.append(bar(32, y, 200, 14, "hatch"))
        b.append(bar(236, y, 28, 14, "blue"))
        y += 18
    b.append(mono(148, y + 14, "× 24", anchor="middle"))
    b.append(serif(352, 240, "→", size=26, fill=INK2, anchor="middle"))
    b.append(mono(420, 150, "carpool"))
    b.append(serif(420, 168, "1 request; labelled tasks; H paid once", size=13, fill=INK3))
    b.append(bar(420, 180, 200, 14, "hatch"))
    x, y = 420, 198
    for i in range(24):
        b.append(bar(x, y, 28, 14, "blue"))
        x += 32
        if (i + 1) % 8 == 0:
            x, y = 420, y + 18
    b.append(serif(420, y + 14, "then check each answer against its source,", size=13, fill=INK2))
    b.append(serif(420, y + 32, "and redo only the failures, one at a time.", size=13, fill=INK2))
    return svg(W, H, "\n".join(b) + "\n", "How carpool works: the fixed part is paid once")


if __name__ == "__main__":
    for old in list(OUT.glob("*-dark.svg")) + list(OUT.glob("*-light.svg")):
        old.unlink()
    (OUT / "cost-per-answer.svg").write_text(fig_cost())
    (OUT / "group-size.svg").write_text(fig_sweep())
    (OUT / "how-it-works.svg").write_text(fig_how())
    print("bars", [(n, round(a, 2), round(l, 2), round(c, 2)) for n, a, l, c in BARS])
    print("sweep", [(k, round(v, 2)) for k, v in SWEEP], "base", round(SWEEP_BASE, 2))
