# Spec PyInstaller multipiattaforma: `pyinstaller Vettore.spec`
from PyInstaller.utils.hooks import collect_all

datas = [("app/static", "app/static")]
binaries = []
hiddenimports = []
for pacchetto in ("uvicorn", "fastapi", "starlette"):
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
