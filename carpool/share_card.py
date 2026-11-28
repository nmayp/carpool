"""Render share cards from saved benchmark results."""
from pathlib import Path

REPO = "github.com/nmayp/carpool"


def card_rows(results):
    runs = results["runs"]
    baseline = runs[0]
    run = next((r for r in runs[1:] if r["route"].split("#")[0] == "carpool"), None)
    if run is None:
        raise ValueError("results must include a baseline followed by a carpool run")

    def cost(row):
        usd = row.get("usd")
        return "unavailable" if usd is None else f"${usd:.4f}"

    fallbacks = run.get("fallbacks")
    return [
        ("Model calls", f"{baseline['calls']} to {run['calls']}"),
        ("Total cost", f"{cost(baseline)} to {cost(run)}"),
        ("Accepted answers", f"{baseline['accepted']} to {run['accepted']}"),
        ("Carpool fallbacks", "unavailable" if fallbacks is None else str(len(fallbacks))),
    ]


def write_card(results, output):
    try:
        from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
    except ImportError as exc:
        raise ValueError("install the card extra with pip install 'carpool-agents[card]'") from exc

    rows = card_rows(results)
    image = Image.new("RGB", (1200, 630), "white")
    draw = ImageDraw.Draw(image)
    blue = "#1840ff"
    title = ImageFont.load_default(size=70)
    face = ImageFont.load_default(size=35)
    small = ImageFont.load_default(size=25)
    draw.rectangle((0, 0, 1200, 14), fill=blue)
    draw.text((67, 48), "carpool", font=title, fill="black")
    subtitle = f"{Path(results['tasks']).stem}  /  {results['backend']}"
    if draw.textlength(subtitle, font=small) > 1066:
        while draw.textlength(subtitle + "...", font=small) > 1066:
            subtitle = subtitle[:-1]
        subtitle += "..."
    draw.text((69, 134), subtitle, font=small, fill=blue)
    for index, (label, value) in enumerate(rows):
        y = 200 + index * 85
        draw.line((69, y, 1131, y), fill=blue, width=1)
        draw.text((69, y + 18), label, font=face, fill="black")
        draw.text((1131, y + 18), value, font=face, fill="black", anchor="ra")
    draw.text((69, 574), REPO, font=small, fill=blue)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_itxt("Description", "\n".join([subtitle, *(f"{k}: {v}" for k, v in rows), REPO]))
    image.save(output, format="PNG", pnginfo=metadata)
