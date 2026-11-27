"""Einstiegspunkt der gebauten Anwendung (PyInstaller). Ã„ndert keine Quellen in src/.

Normalstart: Editor. ``--vorschau-arbeiter``: Generatorvorschau-Prozess (Adapter).
Umgebungsvariable ``MAPEDITOR_RAUCHTEST=1``: Selbsttest ohne Bedienung (siehe rauchtest.py).
``--multiprocessing-fork``: Arbeitsprozess des Werkzeugkastens (``ego/parallel.py``), über ``freeze_support``.
"""
import os
import sys


def _rauchtest():
    import json
    import time
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from mapeditor import adapter

    t0 = time.perf_counter()
    app = QApplication(sys.argv[:1])
    projekt = Path(adapter.toolkit_path()) / "strecke" / "beispiele" / "teststrecke.json"
    window = MainWindow(Project.open(projekt))
    window.show()
    t_fenster = time.perf_counter() - t0
    frist = time.perf_counter() + float(os.environ.get("MAPEDITOR_RAUCHTEST_FRIST", "120"))
    while window.preview is None and time.perf_counter() < frist:
        app.processEvents()
        time.sleep(0.02)
    ok = window.preview is not None
    bericht = {"ok": ok, "fenster_s": round(t_fenster, 2),
               "vorschau_s": round(time.perf_counter() - t0, 2),
               "werkzeugkasten": str(adapter.toolkit_path()),
               "stationen": len(window.preview["curvature"]) if ok else 0,
               "status": window.status_label.text()}
    print(json.dumps(bericht, ensure_ascii=False))
    window.close()
    return 0 if ok else 1


def _rauchtest_3d():
    """3D-Ansicht in der gepackten Anwendung (26.11.2026): Editor mit Teststrecke, Fahrlinie abwarten (liefert das
    Netz), Arbeitsbereich „3D“ öffnen, zeichnen lassen und prüfen: keine QML-Fehler, Szene geladen, Bild nicht leer.
    Eigene Einstellungen (Organisation ``MapeditorRauchtest``), Bild nach ``MAPEDITOR_RAUCHTEST_BILD``."""
    import json
    import time
    from pathlib import Path
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
    QCoreApplication.setOrganizationName("MapeditorRauchtest")
    QCoreApplication.setApplicationName("MapeditorRauchtest")
    from PySide6.QtCore import QSettings
    QSettings().clear()                          # kein gemerkter Arbeitsbereich aus früheren Läufen
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from mapeditor import adapter

    t0 = time.perf_counter()
    app = QApplication(sys.argv[:1])
    projekt = Path(os.environ.get("MAPEDITOR_RAUCHTEST_PROJEKT")
                   or Path(adapter.toolkit_path()) / "strecke" / "beispiele" / "teststrecke.json")
    window = MainWindow(Project.open(projekt))
    window.resize(1400, 900)
    window.show()
    frist = time.perf_counter() + float(os.environ.get("MAPEDITOR_RAUCHTEST_FRIST", "300"))
    while window.netz_3d is None and time.perf_counter() < frist:
        app.processEvents()
        time.sleep(0.02)
    bericht = {"netz_s": round(time.perf_counter() - t0, 2), "netz": bool(window.netz_3d)}
    fehler, szene, bild_ok, import_fehler = [], False, False, None
    try:
        window.set_workspace("3d", merken=False)
        ende = time.perf_counter() + 6.0
        while time.perf_counter() < ende:
            app.processEvents()
            time.sleep(0.02)
        a = window.ansicht_3d
        if a is None:
            raise RuntimeError("3D view not created: " + window.status_label.text())
        fehler = list(a.fehler)
        szene = a.szene is not None
        if os.environ.get("MAPEDITOR_RAUCHTEST_PROJEKT"):     # eigene .glb über RuntimeLoader (Asset-Importer)
            a.echte_modelle_laden()
            ende = time.perf_counter() + 8.0
            while time.perf_counter() < ende:
                app.processEvents()
                time.sleep(0.02)
            from PySide6.QtCore import QObject
            lader = [a.wurzel.findChild(QObject, "u_%d" % i) for i in range(40)]
            sichtbar = [u for u in lader if u is not None and u.property("visible")]
            bericht["eigene_modelle"] = len(sichtbar)
            # RuntimeLoader: errorString lautet bei Erfolg „Success!“ (status ist aus Python nicht lesbar)
            texte = [str(u.property("errorString") or "").strip() for u in sichtbar]
            bericht["eigene_geladen"] = sum(t == "Success!" for t in texte)
            bericht["eigene_fehler"] = [t for t in texte if t and t != "Success!"]
            if bericht["eigene_fehler"]:
                fehler.extend(bericht["eigene_fehler"])
        bild = a.view.grabFramebuffer()
        ziel = os.environ.get("MAPEDITOR_RAUCHTEST_BILD")
        if ziel:
            bild.save(ziel)
        farben = {bild.pixel(x, y) for x in range(0, bild.width(), 37) for y in range(0, bild.height(), 37)}
        bild_ok = bild.width() > 100 and len(farben) > 20
        bericht.update(bildgroesse=[bild.width(), bild.height()], farben=len(farben))
    except Exception as e:  # noqa – Befund statt Absturz
        import traceback
        import_fehler = "".join(traceback.format_exception_only(type(e), e)).strip()
    ok = bericht["netz"] and szene and not fehler and bild_ok and import_fehler is None
    bericht.update(ok=ok, szene=szene, qml_fehler=fehler, ausnahme=import_fehler)
    print(json.dumps(bericht, ensure_ascii=False))
    window.document.undo.setClean()
    window.close()
    return 0 if ok else 1


def _rauchtest_update():
    """Aktualisierung ohne Dialoge (26.11.2026): Release-JSON aus ``MAPEDITOR_UPDATE_URL`` (auch file://) abfragen,
    ZIP laden, Prüfsumme, entpacken, Austauschskript starten, beenden. Die neue Version startet mit
    ``MAPEDITOR_RAUCHTEST=update_ziel`` und schreibt nur eine Marke (``_rauchtest_update_ziel``)."""
    import json
    import tempfile
    from pathlib import Path
    from mapeditor import aktualisierung as akt
    from mapeditor.aktualisierung_ui import _skript_starten
    info = akt.abfragen(os.environ["MAPEDITOR_UPDATE_URL"])
    ablage = akt.ablage(info["version"])
    zip_pfad = ablage.parent / (ablage.name + ".zip")
    akt.herunterladen(info["zip_url"], zip_pfad)
    sha = akt.herunterladen(info["sha_url"], zip_pfad.with_name(zip_pfad.name + ".sha256"))
    ok = akt.sha256_pruefen(zip_pfad, sha.read_text(encoding="ascii"))
    sha.unlink()
    if not ok:
        print(json.dumps({"ok": False, "fehler": "checksum"}))
        return 1
    neu = akt.entpacken(zip_pfad, ablage)
    zip_pfad.unlink()
    skript = akt.austausch_skript(os.getpid(), akt.installation(), neu,
                                  Path(tempfile.gettempdir()) / "mapeditor_update_rauchtest.cmd")
    os.environ["MAPEDITOR_RAUCHTEST"] = "update_ziel"          # die neue Version meldet sich nur
    _skript_starten(skript)
    print(json.dumps({"ok": True, "version": info["version"], "neu": str(neu), "skript": str(skript)}))
    return 0


def _rauchtest_update_ziel():
    """Von der eingespielten Version gestartet: Marke mit Programmpfad und Version schreiben, beenden."""
    import json
    from pathlib import Path
    from mapeditor import __version__
    marke = Path(sys.executable).resolve().parent.parent / "update_rauchtest_marke.json"
    marke.write_text(json.dumps({"exe": str(Path(sys.executable).resolve()), "version": __version__}), encoding="utf-8")
    return 0


def _importtest():
    """Lädt alle Werkzeugkasten-Module aus der Distribution (Abhängigkeitsprüfung für spätere Bau-CLI)."""
    import importlib
    import json
    from pathlib import Path
    from mapeditor import adapter
    root = adapter.toolkit_path()
    sys.path.insert(0, str(root))
    fehler = {}
    namen = [f"{o}.{p.stem}" for o in ("ego", "strecke") for p in sorted((root / o).glob("*.py")) if p.stem != "__init__"]
    namen += ["neue_strecke_tool", "paket_tool", "erp_tool", "strecke_tool"]
    for n in namen:
        try:
            importlib.import_module(n)
        except Exception as e:  # noqa
            fehler[n] = f"{type(e).__name__}: {e}"
    print(json.dumps({"module": len(namen), "fehler": fehler}, ensure_ascii=False))
    return 1 if fehler else 0


def _paralleltest():
    """Arbeitsprozesse des Werkzeugkastens aus der EXE starten (ego/parallel.py; Packen wie beim Bau)."""
    import json
    from mapeditor import adapter
    sys.path.insert(0, str(adapter.toolkit_path()))
    from ego import erp, parallel
    daten = [bytes([i % 7]) * 4000 + bytes(range(256)) for i in range(16)]
    gepackt = parallel.abbilden(erp.fragment_packen, daten, prozesse=4)
    kennungen = set(parallel.abbilden(parallel.prozess_kennung, range(16), prozesse=4))
    ok = gepackt == [erp.fragment_packen(d) for d in daten] and os.getpid() not in kennungen
    print(json.dumps({"ok": ok, "arbeitsprozesse": len(kennungen), "kerne": parallel.anzahl()}))
    return 0 if ok else 1


if __name__ == "__main__":
    import multiprocessing
    try:
        multiprocessing.freeze_support()     # Arbeitsprozesse des Werkzeugkastens starten als Mapeditor.exe
    except OSError:
        # Arbeitsprozess, dessen Elternprozess schon beendet ist (Vorschau/Bau abgebrochen, während er startete):
        # OpenProcess auf die alte PID scheitert mit WinError 87. Still beenden statt Fehlerfenster (27.10.2026).
        if "--multiprocessing-fork" in sys.argv:
            os._exit(0)
        raise
    if os.environ.get("MAPEDITOR_PARALLELTEST") == "1":
        raise SystemExit(_paralleltest())
    if os.environ.get("MAPEDITOR_IMPORTTEST") == "1" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_importtest())
    if os.environ.get("MAPEDITOR_RAUCHTEST") == "update" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_rauchtest_update())
    if os.environ.get("MAPEDITOR_RAUCHTEST") == "update_ziel" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_rauchtest_update_ziel())
    if os.environ.get("MAPEDITOR_RAUCHTEST") == "3d" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_rauchtest_3d())
    if os.environ.get("MAPEDITOR_RAUCHTEST") == "1" and "--vorschau-arbeiter" not in sys.argv:
        raise SystemExit(_rauchtest())
    from mapeditor.__main__ import main
    raise SystemExit(main())
