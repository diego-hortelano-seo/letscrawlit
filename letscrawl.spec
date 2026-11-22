# PyInstaller recipe for the Windows program:  pyinstaller letscrawl.spec  ->  dist/LetsCrawlit/LetsCrawlit.exe
# (the window) and dist/LetsCrawlit/letscrawlit-cli.exe (the command line, for CI and scheduled audits).
# A folder (not a single .exe): starts faster and antivirus programs flag it far less often.
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# The version shown in the .exe's Properties comes from app/config.py, so it never goes stale.
VERSION = re.search(r'^VERSION = "([^"]+)"', Path("app/config.py").read_text(encoding="utf-8"), re.M).group(1)
_nums = tuple(int(n) for n in (VERSION.split(".") + ["0"] * 4)[:4])
Path("build").mkdir(exist_ok=True)
Path("build/version_info.txt").write_text(
    Path("packaging/version_info.txt").read_text(encoding="utf-8")
    .replace("(0, 1, 0, 0)", repr(_nums)).replace('"0.1.0"', f'"{VERSION}"'), encoding="utf-8")

datas = [("app/templates", "app/templates"), ("app/static", "app/static"), ("app/locales", "app/locales"), ("app/knowledge", "app/knowledge"), (".env.example", ".")]
binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("uvicorn") + ["multipart", "python_multipart"]

for pkg in ("spellchecker", "certifi", "pptx", "openpyxl"):
    datas += collect_data_files(pkg)
# Playwright's driver renders JavaScript pages (with the Edge that comes with Windows; no Chromium inside)
for pkg in ("playwright", "webview"):
    d, b, h = collect_all(pkg)
    datas, binaries, hiddenimports = datas + d, binaries + b, hiddenimports + h

a = Analysis(
    ["desktop.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter", "matplotlib", "numpy", "pandas", "IPython"],
    noarchive=True,  # Python modules as files in _internal: both programs share them instead of each carrying 17 MB
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LetsCrawlit",
    icon="packaging/letscrawl.ico",
    console=False,
    version="build/version_info.txt",
    upx=False,
)
# Same libraries, another entry point: a console program for CI (it needs the output and the exit code).
# app.cli is already inside pyz (collect_submodules("app")), so only the script changes.
cli_exe = EXE(
    pyz,
    a.scripts[:-1] + [("cli_main", "cli_main.py", "PYSOURCE")],
    [],
    exclude_binaries=True,
    name="letscrawlit-cli",
    icon="packaging/letscrawl.ico",
    console=True,
    version="build/version_info.txt",
    upx=False,
)
coll = COLLECT(exe, cli_exe, a.binaries, a.datas, strip=False, upx=False, name="LetsCrawlit")
