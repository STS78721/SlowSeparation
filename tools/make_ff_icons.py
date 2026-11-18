"""Draw the SteelTemple SlowSeparation icons (original art, no game assets).

The mark is an anti-grav racer seen from above, a swept chevron hull in a hot-magenta to cyan gradient with twin
engine glows, over neon speed streaks on a midnight-indigo rounded tile. Everything is drawn here with Pillow at
4x and downsampled, so the files can be regenerated at any time:

  runtime/ff.ico            ff.exe, the setup wizard and the installer window (16..256 px)
  runtime/ff.png            512 px tile for the launcher header and the README
  runtime/ff_craft.png      512 px craft alone on transparency (launcher banner emblem)
  launcher/assets/ff.ico    the launcher executable

Usage: python tools/make_ff_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SS = 4  # supersampling factor

INDIGO_TOP = (22, 18, 52)
INDIGO_BOT = (6, 5, 18)
MAGENTA = (255, 46, 136)
CYAN = (64, 224, 255)
WHITE = (246, 244, 255)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def vertical_gradient(size: int, top, bot) -> Image.Image:
    g = Image.new("RGB", (1, size))
    for y in range(size):
        g.putpixel((0, y), lerp(top, bot, y / max(1, size - 1)))
    return g.resize((size, size))


def diagonal_gradient(size: int, c0, c1) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = min(1.0, max(0.0, (x * 0.35 + (size - y) * 0.65) / size))
            px[x, y] = lerp(c1, c0, t)
    return img


def craft_points(cx: float, cy: float, s: float) -> list[tuple[float, float]]:
    """Hull outline: nose, right wing tip, right tail notch, left tail notch, left wing tip."""
    return [
        (cx, cy - s),
        (cx + s * 0.66, cy + s * 0.66),
        (cx + s * 0.22, cy + s * 0.34),
        (cx - s * 0.22, cy + s * 0.34),
        (cx - s * 0.66, cy + s * 0.66),
    ]


def draw_craft(size: int, cx: float, cy: float, s: float) -> Image.Image:
    """The racer on transparency, size x size (already supersampled coordinates)."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # engine glow underneath
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for side in (-1, 1):
        ex, ey = cx + side * s * 0.42, cy + s * 0.62
        r = s * 0.26
        gd.ellipse((ex - r, ey - r, ex + r, ey + r), fill=CYAN + (170,))
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.16))
    layer.alpha_composite(glow)

    # hull with gradient fill
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(craft_points(cx, cy, s), fill=255)
    hull = diagonal_gradient(size, MAGENTA, CYAN).convert("RGBA")
    hull.putalpha(mask)
    layer.alpha_composite(hull)

    d = ImageDraw.Draw(layer)
    w = max(1, int(s * 0.045))
    d.line(craft_points(cx, cy, s) + [craft_points(cx, cy, s)[0]], fill=WHITE + (235,), width=w, joint="curve")
    # spine and canopy
    d.line([(cx, cy - s * 0.62), (cx, cy + s * 0.22)], fill=WHITE + (150,), width=max(1, w // 2))
    canopy = [(cx, cy - s * 0.52), (cx + s * 0.12, cy - s * 0.10), (cx, cy + s * 0.05), (cx - s * 0.12, cy - s * 0.10)]
    d.polygon(canopy, fill=INDIGO_BOT + (235,), outline=WHITE + (200,))
    # engine cores
    for side in (-1, 1):
        ex, ey = cx + side * s * 0.42, cy + s * 0.60
        r = s * 0.075
        d.ellipse((ex - r, ey - r, ex + r, ey + r), fill=(235, 252, 255, 255))
    return layer


def draw_streaks(size: int, d: ImageDraw.ImageDraw, area: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = area
    rows = (0.16, 0.28, 0.42, 0.58, 0.72, 0.86)
    lens = (0.46, 0.30, 0.62, 0.40, 0.56, 0.34)
    offs = (0.04, 0.52, 0.10, 0.58, 0.02, 0.46)
    th = max(2, int(size * 0.012))
    for i, (r, ln, of) in enumerate(zip(rows, lens, offs)):
        col = MAGENTA if i % 2 == 0 else CYAN
        y = y0 + (y1 - y0) * r
        xs = x0 + (x1 - x0) * of
        xe = xs + (x1 - x0) * ln
        steps = 24
        for k in range(steps):
            a = int(150 * (k + 1) / steps)
            sx = xs + (xe - xs) * k / steps
            ex = xs + (xe - xs) * (k + 1) / steps
            d.line([(sx, y), (ex, y)], fill=col + (a,), width=th)


def tile(px: int) -> Image.Image:
    size = px * SS
    rad = int(size * 0.22)
    bg = vertical_gradient(size, INDIGO_TOP, INDIGO_BOT).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), rad, fill=255)

    art = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if px >= 32:   # streaks and the rim read only from 32 px up
        draw_streaks(size, ImageDraw.Draw(art), (size * 0.06, size * 0.10, size * 0.94, size * 0.92))
    bg.alpha_composite(art)
    s = size * (0.36 if px >= 32 else 0.42)
    bg.alpha_composite(draw_craft(size, size * 0.5, size * 0.50, s))

    if px >= 32:
        rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(rim).rounded_rectangle((0, 0, size - 1, size - 1), rad, outline=MAGENTA + (190,),
                                              width=max(2, int(size * 0.018)))
        bg.alpha_composite(rim)
    bg.putalpha(Image.composite(bg.getchannel("A"), Image.new("L", (size, size), 0), mask))
    return bg.resize((px, px), Image.LANCZOS)


def craft_only(px: int) -> Image.Image:
    size = px * SS
    img = draw_craft(size, size * 0.5, size * 0.47, size * 0.40)
    return img.resize((px, px), Image.LANCZOS)


def save_ico(path: Path, sizes=(16, 20, 24, 32, 40, 48, 64, 96, 128, 256)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [tile(s) for s in sizes]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print(f"{path} ({', '.join(str(s) for s in sizes)})")


def main() -> None:
    save_ico(ROOT / "runtime" / "ff.ico")
    save_ico(ROOT / "launcher" / "assets" / "ff.ico")
    tile(512).save(ROOT / "runtime" / "ff.png")
    print(ROOT / "runtime" / "ff.png")
    craft_only(512).save(ROOT / "runtime" / "ff_craft.png")
    print(ROOT / "runtime" / "ff_craft.png")


if __name__ == "__main__":
    main()
