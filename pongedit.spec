# -*- mode: python ; coding: utf-8 -*-
# Build : pyinstaller pongedit.spec --noconfirm   (sur la plateforme visée)
import ast
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

def _skip(p):
    return "__pycache__" in p.parts or ".bak" in p.name

# Le code de l'app est livré en fichiers (pas dans l'exécutable) → PyInstaller ne voit pas
# ses imports : on les déclare explicitement en lisant le code.
hidden = set()
sources = [ROOT / "pong_edit.py", *(p for p in (ROOT / "pongedit").rglob("*.py") if not _skip(p))]
for src in sources:
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        else:
            continue
        for n in names:
            top = n.split(".")[0]
            if top in ("pongedit", "pong_edit", "AppKit", "Foundation", "objc"):
                continue
            hidden.add(n if top in ("PySide6", "PIL") else top)
hidden |= {"PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
           "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PIL.ImageFont", "PIL.ImageDraw"}

datas = [(str(ROOT / "pong_edit.py"), "code")]
for f in (ROOT / "pongedit").rglob("*"):
    if f.is_file() and not _skip(f):
        datas.append((str(f), str(Path("code") / f.parent.relative_to(ROOT))))

a = Analysis(["launcher.py"], pathex=[str(ROOT)], datas=datas,
             hiddenimports=sorted(hidden), excludes=["tkinter", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PingPongEdit",
          console=False,
          icon=str(ROOT / "assets" / ("icon.ico" if sys.platform.startswith("win") else "icon.icns")))
coll = COLLECT(exe, a.binaries, a.datas, name="PingPongEdit")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Ping Pong Edit.app", icon=str(ROOT / "assets" / "icon.icns"),
                 bundle_identifier="fr.eliotgourdoux.pingpongedit")
