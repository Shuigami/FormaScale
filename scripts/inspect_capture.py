"""Inspect original captures and prepare reproducible, oriented JPEG copies.

Usage: python scripts/inspect_capture.py captures/bose_01
Sharpness is descriptive only: scene texture also changes Laplacian variance.
No image is discarded automatically and originals are never modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageDraw
from pillow_heif import register_heif_opener

register_heif_opener()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('capture', type=Path)
args = parser.parse_args()
root = args.capture.resolve()
prepared = root / 'prepared' / 'images'
reports = root / 'reports'
prepared.mkdir(parents=True, exist_ok=True)
reports.mkdir(parents=True, exist_ok=True)
records, thumbnails = [], []
for path in sorted((root / 'images').iterdir()):
    if path.suffix.lower() not in {'.heic', '.heif', '.jpg', '.jpeg', '.png'}:
        continue
    record = {'source': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    try:
        with Image.open(path) as original:
            exif = original.getexif()
            record.update(camera=str(exif.get(272, '')), focal_length=str(exif.get(37386, '')))
            image = ImageOps.exif_transpose(original).convert('RGB')
            record['original_size'] = list(image.size)
            image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
            destination = prepared / (path.name + '.jpg')
            image.save(destination, quality=95, subsampling=0)
            gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
            record.update(prepared=destination.name, prepared_size=list(image.size),
                laplacian_variance=round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
                dark_fraction=round(float(np.mean(gray < 10)), 4),
                bright_fraction=round(float(np.mean(gray > 245)), 4))
            tile = Image.new('RGB', (260, 220), 'white')
            thumb = image.copy()
            thumb.thumbnail((250, 185))
            tile.paste(thumb, ((260-thumb.width)//2, 0))
            ImageDraw.Draw(tile).text((5, 190), path.name, fill='black')
            thumbnails.append(tile)
    except Exception as exc:
        record['error'] = str(exc)
    records.append(record)
for start in range(0, len(thumbnails), 20):
    tiles = thumbnails[start:start+20]
    sheet = Image.new('RGB', (260*5, 220*((len(tiles)+4)//5)), '#dddddd')
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i%5)*260, (i//5)*220))
    sheet.save(reports / f'contact_{start//20+1:02d}.jpg', quality=90)
valid = [r for r in records if 'error' not in r]
counts = Counter(r['sha256'] for r in records)
summary = {'images': len(records), 'readable': len(valid),
    'duplicate_content_count': sum(n-1 for n in counts.values()),
    'resolutions': dict(Counter(str(r['original_size']) for r in valid)),
    'cameras': dict(Counter(r['camera'] for r in valid)),
    'lowest_sharpness': [{'source': r['source'], 'score': r['laplacian_variance']} for r in sorted(valid, key=lambda r: r['laplacian_variance'])[:5]],
    'limitations': ['Sharpness scores do not prove blur.', 'No overlap, calibration or geometric accuracy validated yet.', 'JPEG copies are resized to a maximum dimension of 2000 pixels; originals preserved.']}
(reports / 'image_audit.json').write_text(json.dumps({'summary': summary, 'images': records}, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
if len(valid) != len(records):
    raise SystemExit(1)
