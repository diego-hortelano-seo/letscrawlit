"""Draw packaging/letscrawl.ico (brand colours). Run once: python packaging/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
SIZE = 512
PURPLE, LILAC, WHITE = (51, 25, 66, 255), (199, 125, 255, 255), (255, 255, 255, 255)


def font(px: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        for folder in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (folder / name).exists():
                return ImageFont.truetype(str(folder / name), px)
    return ImageFont.load_default()


img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((16, 16, SIZE - 16, SIZE - 16), radius=110, fill=PURPLE)
f = font(250)
box = d.textbbox((0, 0), "LC", font=f)
w, h = box[2] - box[0], box[3] - box[1]
d.text(((SIZE - w) / 2 - box[0] - 18, (SIZE - h) / 2 - box[1] + 6), "LC", font=f, fill=WHITE)
d.ellipse((SIZE - 150, SIZE - 150, SIZE - 82, SIZE - 82), fill=LILAC)
img.save(HERE / "letscrawl.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(HERE / "letscrawl.png")
print("ok", HERE / "letscrawl.ico")
