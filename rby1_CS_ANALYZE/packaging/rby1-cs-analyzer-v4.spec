from pathlib import Path
import sys

root = Path(SPECPATH).parent

a = Analysis(
    [
        str(root / "packaging" / "entrypoints" / "launcher_entry.py"),
        str(root / "packaging" / "entrypoints" / "cli_entry.py"),
    ],
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
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto"],
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
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
launcher_scripts = [entry for entry in a.scripts if entry[0] != "cli_entry"]
cli_scripts = [entry for entry in a.scripts if entry[0] != "launcher_entry"]
launcher = EXE(
    pyz,
    launcher_scripts,
    [],
    exclude_binaries=True,
    name="rby1-cs-analyzer-v4",
    console=sys.platform != "win32",
)
cli = EXE(
    pyz,
    cli_scripts,
    [],
    exclude_binaries=True,
    name="rby1-cs-analyzer-v4-cli",
    console=True,
)
coll = COLLECT(
    launcher,
    cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="rby1-cs-analyzer-v4",
)
