"""Генератор бренд-иконок LikeHub.

Рисуем в 4× и уменьшаем с LANCZOS: у Pillow нет сглаживания примитивов,
супersampling — единственный способ получить чистые края на 256 px.

Палитра унаследована от LikeCAD (семейство Like*):
  navy  #012E58 — рамка помещения, общий признак семейства
  amber #FEC400 — акцент; у LikeCAD это молния (электрика), у LikeHub — узел связи

Запуск: python generate_icons.py [--variant a|b|c]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY = (1, 46, 88, 255)
AMBER = (254, 196, 0, 255)
NAVY_LIGHT = (232, 238, 245, 255)  # рамка для тёмной темы
WHITE = (255, 255, 255, 255)

SS = 4  # коэффициент супersampling
BASE = 256


def new_canvas(size: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def draw_frame(draw: ImageDraw.ImageDraw, size: int, color: tuple[int, int, int, int]) -> float:
    """Квадратная рамка помещения — общий элемент семейства Like*.

    Возвращает внутренний радиус (половину стороны белого поля).
    """
    margin = size * 0.06
    thickness = size * 0.11
    draw.rectangle(
        [margin, margin, size - margin, size - margin],
        outline=color,
        width=int(thickness),
    )
    return (size / 2) - margin - thickness


def variant_hub(size: int, frame_color=NAVY, accent=AMBER, node=NAVY) -> Image.Image:
    """A — узел с лучами: буквальный «хаб»."""
    img, draw = new_canvas(size)
    inner = draw_frame(draw, size, frame_color)
    cx = cy = size / 2

    spoke_len = inner * 0.62
    spoke_w = int(size * 0.045)
    sat_r = size * 0.055
    core_r = size * 0.105

    for angle in (45, 135, 225, 315):
        rad = math.radians(angle)
        ex, ey = cx + spoke_len * math.cos(rad), cy + spoke_len * math.sin(rad)
        draw.line([cx, cy, ex, ey], fill=node, width=spoke_w)
        draw.ellipse([ex - sat_r, ey - sat_r, ex + sat_r, ey + sat_r], fill=node)

    draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=accent)
    return img


def variant_signal(size: int, frame_color=NAVY, accent=AMBER, node=NAVY) -> Image.Image:
    """B — узел с волнами: «объект на связи»."""
    img, draw = new_canvas(size)
    inner = draw_frame(draw, size, frame_color)
    cx = cy = size / 2

    core_r = size * 0.085
    draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=accent)

    arc_w = int(size * 0.05)
    for k, radius in enumerate((inner * 0.45, inner * 0.72)):
        box = [cx - radius, cy - radius, cx + radius, cy + radius]
        draw.arc(box, start=200, end=340, fill=node, width=arc_w)
        draw.arc(box, start=20, end=160, fill=node, width=arc_w)
    return img


def variant_monogram(size: int, frame_color=NAVY, accent=AMBER, node=NAVY) -> Image.Image:
    """C — монограмма «H» с акцентной перемычкой."""
    img, draw = new_canvas(size)
    draw_frame(draw, size, frame_color)
    cx = cy = size / 2

    stem_w = size * 0.075
    stem_h = size * 0.34
    gap = size * 0.105

    for dx in (-gap, gap):
        draw.rectangle(
            [cx + dx - stem_w / 2, cy - stem_h / 2, cx + dx + stem_w / 2, cy + stem_h / 2],
            fill=node,
        )
    bar_h = size * 0.075
    draw.rectangle(
        [cx - gap, cy - bar_h / 2, cx + gap, cy + bar_h / 2], fill=accent
    )
    return img


VARIANTS = {"a": variant_hub, "b": variant_signal, "c": variant_monogram}


def render(variant: str, size: int, dark: bool = False) -> Image.Image:
    fn = VARIANTS[variant]
    big = fn(
        size * SS,
        frame_color=NAVY_LIGHT if dark else NAVY,
        accent=AMBER,
        node=NAVY_LIGHT if dark else NAVY,
    )
    return big.resize((size, size), Image.LANCZOS)


def render_logo(variant: str, height: int, dark: bool = False) -> Image.Image:
    """Логотип: иконка + словесный знак. Требование brands — горизонтальный формат."""
    icon = render(variant, height, dark)
    text_color = NAVY_LIGHT if dark else NAVY

    font = None
    for candidate in (
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Supplemental/Futura.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, int(height * 0.42))
                break
            except OSError:
                continue
    if font is None:
        font = ImageFont.load_default()

    pad = int(height * 0.16)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), "LikeHub", font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]

    logo = Image.new("RGBA", (height + pad + text_w + pad, height), (0, 0, 0, 0))
    logo.paste(icon, (0, 0), icon)
    ImageDraw.Draw(logo).text(
        (height + pad, (height - text_h) / 2 - box[1]), "LikeHub", font=font, fill=text_color
    )
    return logo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default=None, help="a, b, c или пусто — собрать превью всех")
    parser.add_argument("--out", default=".", help="каталог вывода")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.variant is None:
        # Превью всех вариантов в светлой и тёмной теме одним листом.
        cell = BASE
        sheet = Image.new("RGBA", (cell * 3, cell * 2), (245, 245, 245, 255))
        dark_row = Image.new("RGBA", (cell * 3, cell), (28, 32, 38, 255))
        for i, key in enumerate("abc"):
            sheet.paste(render(key, cell), (cell * i, 0), render(key, cell))
            d = render(key, cell, dark=True)
            dark_row.paste(d, (cell * i, 0), d)
        sheet.paste(dark_row, (0, cell))
        sheet.save(out / "preview_variants.png")
        print(f"превью: {out / 'preview_variants.png'}")
        return

    v = args.variant
    render(v, 256).save(out / "icon.png")
    render(v, 512).save(out / "icon@2x.png")
    render(v, 256, dark=True).save(out / "dark_icon.png")
    render(v, 512, dark=True).save(out / "dark_icon@2x.png")
    render_logo(v, 256).save(out / "logo.png")
    render_logo(v, 512).save(out / "logo@2x.png")
    render_logo(v, 256, dark=True).save(out / "dark_logo.png")
    render_logo(v, 512, dark=True).save(out / "dark_logo@2x.png")
    print(f"вариант {v}: 8 файлов в {out}")


if __name__ == "__main__":
    main()
