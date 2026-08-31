from pathlib import Path
import sys

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "packaging" / "entrypoints" / "launcher_entry.py")],
    pathex=[str(root / "backend")],
    binaries=[],
    datas=[
        (str(root / "frontend" / "dist"), "frontend/dist"),
        (str(root / "config"), "config"),
        (
            str(root / "backend" / "rby1_analyzer" / "diagnostics" / "rules"),
            "rby1_analyzer/diagnostics/rules",
        ),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "tkinter",
        "torch",
        "scipy",
        "pytest",
        "_pytest",
        "pygments",
        "pkg_resources",
        "setuptools",
        "pip",
        "wheel",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rby1-cs-analyzer-v5",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=sys.platform != "win32",
)
