#!/usr/bin/env python3
"""Draws packaging/icon.ico (and icon.png): a rosewood tile with the studio's
brass disc and the five fret colours. Run it again after changing the design."""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
FRETS = ["#1fb34a", "#e5383b", "#f5c518", "#2f80ed", "#f2711c"]


def draw(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.rounded_rectangle((32 * s, 32 * s, 992 * s, 992 * s), radius=200 * s, fill="#2f211f")
    d.rounded_rectangle((32 * s, 32 * s, 992 * s, 992 * s), radius=200 * s, outline="#4d3b36", width=int(12 * s))
    cx, cy, r = 512 * s, 430 * s, 270 * s
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#c9a15a")
    r2 = 205 * s
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), outline="#e2be77", width=int(10 * s))
    r3 = 92 * s
    d.ellipse((cx - r3, cy - r3, cx + r3, cy + r3), fill="#241a1a")
    gem_r, y = 58 * s, 820 * s
    for i, c in enumerate(FRETS):
        x = (512 + (i - 2) * 150) * s
        d.ellipse((x - gem_r, y - gem_r * 0.72, x + gem_r, y + gem_r * 0.72), fill=c)
        d.ellipse((x - gem_r * 0.45, y - gem_r * 0.42, x + gem_r * 0.45, y - gem_r * 0.08), fill=(255, 255, 255, 120))
    return img


if __name__ == "__main__":
    big = draw()
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(HERE, "icon.png"))
    big.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote icon.ico and icon.png")
