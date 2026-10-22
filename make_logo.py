"""Generates logo.svg (tile), mark.svg (bars only) and icon.icns source: an abstract sound wave."""
import os

# relative bar heights, left to right: a voice-like envelope
HEIGHTS = [0.16, 0.34, 0.62, 0.30, 0.86, 1.0, 0.54, 0.74, 0.28, 0.46, 0.16]


def svg(size=1024, tile=True):
    n = len(HEIGHTS)
    span = size * 0.58
    pitch = span / (n - 1)
    w = pitch * 0.5
    maxh = size * 0.5
    cx, cy = size / 2, size / 2
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">',
         '<defs>'
         '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#f3f3f3"/></linearGradient>'
         '</defs>']
    if tile:
        r = size * 0.225
        s.append(f'<rect x="3" y="3" width="{size-6}" height="{size-6}" rx="{r}" fill="url(#bg)" stroke="#e5e5e5" stroke-width="6"/>')
    for i, h in enumerate(HEIGHTS):
        x = cx - span / 2 + i * pitch
        bh = max(h * maxh, w)
        s.append(f'<rect x="{x - w/2:.1f}" y="{cy - bh/2:.1f}" width="{w:.1f}" height="{bh:.1f}" rx="{w/2:.1f}" fill="#0a0a0a"/>')
    s.append('</svg>')
    return "\n".join(s)


here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
open(os.path.join(here, "logo.svg"), "w").write(svg())
open(os.path.join(here, "mark.svg"), "w").write(svg(tile=False))
