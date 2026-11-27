# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec: Mapeditor als Windows-Ordnerdistribution (onedir).
# Der Werkzeugkasten (ego-werkzeuge) wird NICHT hier, sondern von bauen.py nach
# dist/Mapeditor/werkzeugkasten/ kopiert (dort sucht ihn adapter.toolkit_path()).
# Hier werden nur seine Python-Abhängigkeiten eingefroren.
import os
import PySide6
SRC = os.path.abspath(os.path.join(SPECPATH, "..", "src"))
ICON = os.path.join(SRC, "mapeditor", "mapeditor.ico")

# Laufzeitabhängigkeiten des (als Quelltext mitgelieferten) Werkzeugkastens:
# Importanalyse siehe docs/verteilung.md. Der Werkzeugkasten wird erst zur Laufzeit
# per sys.path geladen, PyInstaller sieht diese Importe also nicht von selbst.
hidden = [
    "numpy", "zstandard", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "PIL.ImageFilter",
    "uuid", "zlib", "zipfile", "hashlib", "struct", "traceback", "html", "importlib",
    "dataclasses", "contextlib", "collections", "datetime", "argparse", "shutil", "subprocess",
    "csv", "tempfile", "unicodedata", "xml.etree.ElementTree",  # strecke/importe.py (Schritt F)
    "multiprocessing", "concurrent.futures", "concurrent.futures.process", "concurrent.futures.thread",  # ego/parallel.py
]
excludes = [
    "tkinter", "pytest", "setuptools", "pip", "unittest", "pydoc_data",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # QtQml/QtQuick/QtQuickWidgets/QtQuick3D gehören seit 0.6.0 dazu (3D-Ansicht, docs/3d_ansicht.md)
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtSvg", "PySide6.QtSvgWidgets",   # QtOpenGL nicht: PySide6.QtQuick braucht es
]

a = Analysis(
    [os.path.join(SPECPATH, "start.py")],
    pathex=[SRC],
    # 3D-Ansicht: Asset-Importer für eigene .glb-Modelle (RuntimeLoader); der QtQuick3D-Hook nimmt ihn nicht mit
    binaries=[(os.path.join(os.path.dirname(PySide6.__file__), "plugins", "assetimporters", "assimp.dll"),
               os.path.join("PySide6", "plugins", "assetimporters"))],
    datas=[(ICON, "mapeditor"),           # Fenstersymbol zur Laufzeit
           (os.path.join(SRC, "mapeditor", "i18n", "*.csv"), os.path.join("mapeditor", "i18n")),   # Übersetzungstabellen
           (os.path.join(SRC, "mapeditor", "i18n", "*.html"), os.path.join("mapeditor", "i18n")),   # Hilfe de/en
           # Release Notes für „What's new“ nach einem Update (aktualisierung.mitgelieferte_notizen)
           # (Arbeitsstand: packaging/release; öffentliches Repository: im Hauptordner)
           (next(p for p in (os.path.join(SPECPATH, "release", "RELEASE_NOTES.md"),
                             os.path.join(SPECPATH, "..", "..", "RELEASE_NOTES.md")) if os.path.exists(p)), "mapeditor")],
    hiddenimports=hidden + ["mapeditor", "mapeditor.adapter", "mapeditor.window", "mapeditor.project",
                            # 3D-Ansicht: erst beim Öffnen importiert, PyInstaller sieht das nicht von selbst
                            "mapeditor.ansicht3d", "mapeditor.ansicht3d_ui",
                            "mapeditor.aktualisierung", "mapeditor.aktualisierung_ui", "ssl", "urllib.request", "PySide6.QtQml", "PySide6.QtQuick",
                            "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtOpenGL"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Mapeditor",
    console=False,            # Fensteranwendung; Arbeiter/Rauchtest nutzen stdout trotzdem (Pipe)
    icon=ICON,                # erzeugt von packaging/icon_erzeugen.py
    version=os.path.join(SPECPATH, "versionsinfo.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Mapeditor")
