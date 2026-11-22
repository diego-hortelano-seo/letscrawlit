"""Draw the installer's side and corner pictures (brand colours). Run once: python packaging/make_wizard.py

Inno Setup wants BMPs: the side panel at 164x314 and the corner logo at 55x55 at 100 % scale,
plus larger copies so they stay sharp on 125/150/200 % screens.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
PURPLE, DEEP, LILAC, PINK, WHITE = (51, 25, 66), (43, 15, 61), (199, 125, 255), (255, 139, 209), (255, 255, 255)
SCALES = (100, 125, 150, 200)


def font(px: int, bold=True):
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    for folder in (Path("C:/Windows/Fonts"),):
        if (folder / name).exists():
            return ImageFont.truetype(str(folder / name), px)
    return ImageFont.load_default()


def side(scale: int) -> Image.Image:
    k = scale / 100
    w, h = round(164 * k), round(314 * k)
    img = Image.new("RGB", (w, h), DEEP)
    d = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient deep purple -> purple
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(round(DEEP[i] + (PURPLE[i] - DEEP[i]) * t) for i in range(3)))
    # soft lilac and pink glows
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((-w * .6, h * .55, w * .9, h * 1.25), fill=(70, 30, 95))
    g.ellipse((w * .35, -h * .15, w * 1.5, h * .35), fill=(85, 35, 90))
    img = Image.composite(glow, img, glow.convert("L").point(lambda v: 110 if v else 0))
    d = ImageDraw.Draw(img)
    logo = Image.open(HERE / "letscrawl.png").convert("RGBA").resize((round(72 * k),) * 2, Image.LANCZOS)
    img.paste(logo, (round(18 * k), round(26 * k)), logo)
    d.text((round(18 * k), round(112 * k)), "LetsCrawlit", font=font(round(25 * k)), fill=WHITE)
    d.text((round(19 * k), round(146 * k)), "Auditorías SEO", font=font(round(13 * k), False), fill=LILAC)
    d.text((round(19 * k), round(164 * k)), "en tu ordenador", font=font(round(13 * k), False), fill=LILAC)
    d.rounded_rectangle((round(19 * k), round(194 * k), round(55 * k), round(198 * k)), radius=round(2 * k), fill=PINK)
    return img


def corner(scale: int) -> Image.Image:
    s = round(55 * scale / 100)
    img = Image.new("RGB", (s, s), WHITE)
    logo = Image.open(HERE / "letscrawl.png").convert("RGBA").resize((s, s), Image.LANCZOS)
    img.paste(logo, (0, 0), logo)
    return img


for sc in SCALES:
    side(sc).save(HERE / f"wizard-{sc}.bmp")
    corner(sc).save(HERE / f"wizard-small-{sc}.bmp")
print("ok")
