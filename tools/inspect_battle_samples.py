"""Create a read-only source screenshot contact sheet for manual annotation."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
paths = sorted(args.source.glob("20*-battle.png"))
sheet = Image.new("RGB", (720, len(paths)*275), "white")
draw = ImageDraw.Draw(sheet)
for i, path in enumerate(paths):
    image = Image.open(path).convert("RGB")
    if image.size != (720, 1280):
        raise ValueError(f"Unexpected screenshot size: {path}")
    draw.text((8, i*275), path.name, fill="black")
    sheet.paste(image.crop((0, 880, 720, 1135)), (0, i*275+20))
args.output.parent.mkdir(parents=True, exist_ok=True)
sheet.save(args.output)
