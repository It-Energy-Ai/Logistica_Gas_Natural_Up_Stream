# Spec PyInstaller multipiattaforma: `pyinstaller Vettore.spec`
import sys

from PyInstaller.utils.hooks import collect_all

datas = [("app/static", "app/static"), ("app/schemas", "app/schemas")]
binaries = []
hiddenimports = []
# tzdata serve solo su Windows, dove zoneinfo non trova i fusi di sistema.
for pacchetto in ("uvicorn", "fastapi", "starlette", "lxml", *(("tzdata",) if sys.platform == "win32" else ())):
    d, b, h = collect_all(pacchetto)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "httpx"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="Vettore",
    console=True,
    upx=False,
)
