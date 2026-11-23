"""Build packaging/letscrawl.ico and letscrawl.png from the brand app icon (02_BRAND/identidad).

Run once: python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
SRC = HERE / "icono-app-512.png"  # copy of LETSCRAWLIT/02_BRAND/identidad/png/icono-app-512.png

img = Image.open(SRC).convert("RGBA")
img.save(HERE / "letscrawl.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(HERE / "letscrawl.png")
print("ok", HERE / "letscrawl.ico")
