"""Build 64px reference gallery from original local artwork; not YOLO training."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('portable', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    cards = json.loads((a.portable/'config/card_library.json').read_text(encoding='utf-8'))['cards']
    images = [np.asarray(Image.open(a.portable/c['image']).convert('RGB').resize((64,64), Image.Resampling.BILINEAR)) for c in cards]
    np.savez_compressed(a.output, images=np.stack(images), names=np.asarray([c['name'] for c in cards]),
                        rarities=np.asarray([c['rarity'] for c in cards]))
    print(f'Built {len(cards)} reference images: {a.output}')
