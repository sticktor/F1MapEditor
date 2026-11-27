"""Automatische Aktualisierung (26.11.2026): Versionen, Release Notes, GitHub-Antwort, Download, Austausch, Dialoge."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from mapeditor import __version__, aktualisierung as akt

NOTIZEN = """Mapeditor is a Windows editor.

## New in 0.7.0

- **Seven:** neu

## New in 0.6.0

- **Six:** 3D view

## New in 0.5.1

- **Five one:** surfaces

**Unofficial fan project.**

## Download

`Mapeditor-0.7.0-win64.zip`
"""


def test_versionen():
    assert akt.version_tupel("v0.6.0") > akt.version_tupel("0.5.10") > akt.version_tupel("0.5.9")
    assert akt.version_tupel("0.6") == akt.version_tupel("0.6.0")
    assert akt.version_tupel("0.6.0-beta1") < akt.version_tupel("0.6.0")
    assert akt.ist_neuer("99.0.0") and not akt.ist_neuer(__version__)


def test_release_notes_ausschneiden():
    teile = akt.abschnitte(NOTIZEN)
    assert [v for v, _ in teile] == ["0.7.0", "0.6.0", "0.5.1"]
    assert "Unofficial" in teile[2][1] and "Download" not in teile[2][1]      # endet vor der nächsten Überschrift
    zwischen = akt.notizen_zwischen(NOTIZEN, "0.5.1", "0.7.0")
    assert zwischen.index("0.7.0") < zwischen.index("0.6.0") and "Five one" not in zwischen
    assert akt.notizen_zwischen(NOTIZEN, None, "0.6.0").startswith("## 0.6.0") and "Seven" not in \
        akt.notizen_zwischen(NOTIZEN, None, "0.6.0")
    assert "Six" in akt.mitgelieferte_notizen() or "New in" in akt.mitgelieferte_notizen()


def _antwort(tmp_path, version="99.0.0", mit_zip=True):
    """GitHub-Antwort als Datei mit file://-Assets (ZIP mit Mapeditor/Mapeditor.exe und Prüfsumme)."""
    zip_pfad = tmp_path / f"Mapeditor-{version}-win64.zip"
    with zipfile.ZipFile(zip_pfad, "w") as z:
        z.writestr("Mapeditor/Mapeditor.exe", b"MZ fake")
        z.writestr("Mapeditor/werkzeugkasten/x.py", b"print(1)")
    sha = tmp_path / (zip_pfad.name + ".sha256")
    sha.write_text(hashlib.sha256(zip_pfad.read_bytes()).hexdigest() + " *" + zip_pfad.name + "\n", encoding="ascii")
    assets = [{"name": sha.name, "browser_download_url": sha.as_uri(), "size": sha.stat().st_size}]
    if mit_zip:
        assets.insert(0, {"name": zip_pfad.name, "browser_download_url": zip_pfad.as_uri(),
                          "size": zip_pfad.stat().st_size})
    daten = {"tag_name": "v" + version, "html_url": "https://github.com/x/y/releases/tag/v" + version,
             "body": NOTIZEN, "draft": False, "prerelease": False, "assets": assets}
    api = tmp_path / "latest.json"
    api.write_text(json.dumps(daten), encoding="utf-8")
    return api, zip_pfad, sha


def test_abfragen_laden_pruefen_entpacken(tmp_path):
    api, zip_pfad, sha = _antwort(tmp_path)
    info = akt.abfragen(api.as_uri())
    assert info["version"] == "99.0.0" and info["zip_name"] == zip_pfad.name and info["sha_url"]
    stand = []
    ziel = akt.herunterladen(info["zip_url"], tmp_path / "dl" / "neu.zip", lambda g, t: stand.append(g))
    assert stand and stand[-1] == zip_pfad.stat().st_size
    assert akt.sha256_pruefen(ziel, sha.read_text(encoding="ascii"))
    assert not akt.sha256_pruefen(ziel, "0" * 64 + " *x")
    ordner = akt.entpacken(ziel, tmp_path / "ablage")
    assert (ordner / "Mapeditor.exe").is_file() and ordner.name == "Mapeditor"
    with pytest.raises(akt.UpdateFehler):                      # Abbruch
        akt.herunterladen(info["zip_url"], tmp_path / "dl" / "x.zip", abgebrochen=lambda: True)
    assert not (tmp_path / "dl" / "x.zip.part").exists()
    boese = tmp_path / "boese.zip"
    with zipfile.ZipFile(boese, "w") as z:
        z.writestr("../ausbruch.txt", b"x")
    with pytest.raises(akt.UpdateFehler):
        akt.entpacken(boese, tmp_path / "ablage2")
    with pytest.raises(akt.UpdateFehler):
        akt.abfragen((tmp_path / "fehlt.json").as_uri())


def test_austausch_skript(tmp_path):
    alt, neu = tmp_path / "Mapeditor", tmp_path / ".Mapeditor-update-9" / "Mapeditor"
    skript = akt.austausch_skript(4242, alt, neu, tmp_path / "u.cmd")
    text = skript.read_text(encoding="utf-8")
    assert 'PID eq 4242' in text and f'move "{alt}" "{alt}.alt"' in text and f'move "{neu}" "{alt}"' in text
    assert f'start "" "{alt / "Mapeditor.exe"}"' in text and f'start "" "{neu / "Mapeditor.exe"}"' in text
    assert akt.ablage("9.9.9", tmp_path / "Mapeditor") == tmp_path / ".Mapeditor-update-9.9.9"


def test_was_ist_neu_nur_nach_update(app, monkeypatch):
    from PySide6.QtCore import QSettings
    from mapeditor import aktualisierung_ui as ui
    monkeypatch.setattr(akt, "mitgelieferte_notizen", lambda: NOTIZEN)
    QSettings().clear()
    assert ui.notizen_nach_update("0.6.0") is None                       # erste Installation
    assert ui.notizen_nach_update("0.6.0") is None                       # schon gesehen
    text = ui.notizen_nach_update("0.7.0")                                # Update von 0.6.0
    assert "Seven" in text and "Six" not in text
    QSettings().clear()
    QSettings().setValue("sprache", "de")                                 # Nutzer einer Version ohne den Schlüssel
    text = ui.notizen_nach_update("0.6.0")
    assert "Six" in text and "Seven" not in text and "Five" not in text


def test_dialoge_und_manuelle_suche(app, tmp_path, monkeypatch):
    from mapeditor import aktualisierung_ui as ui
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    api, _, _ = _antwort(tmp_path)
    info = akt.abfragen(api.as_uri())
    d = ui.UpdateDialog(info)
    assert not d.herunterladbar                                            # Quelltext: Release-Seite statt Download
    assert "99.0.0" in d.windowTitle() or "99.0.0" in d.findChildren(ui.QLabel)[0].text()
    browser = d.findChildren(ui.QTextBrowser)[0]
    assert "Seven" in browser.toPlainText()
    d.ueberspringen.click()
    assert d.aktion == "ueberspringen" and ui.settings().value("update/uebersprungen") == "99.0.0"
    gezeigt = []
    monkeypatch.setattr(ui.UpdateDialog, "exec", lambda self: gezeigt.append(self.info["version"]))
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a, **k: gezeigt.append("aktuell"))
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a, **k: gezeigt.append("fehler"))
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    try:
        suche = ui.pruefen(w, manuell=True, url=api.as_uri())
        from PySide6.QtTest import QTest
        for _ in range(100):
            if gezeigt:
                break
            QTest.qWait(20)
        assert gezeigt == ["99.0.0"]                                       # manuell: übersprungene Version trotzdem
        gezeigt.clear()
        ui.pruefen(w, manuell=False, url=api.as_uri())
        QTest.qWait(400)
        assert gezeigt == []                                               # automatisch: übersprungen bleibt still
        api_alt, _, _ = _antwort(tmp_path / "alt" if (tmp_path / "alt").mkdir() is None else tmp_path,
                                 version=__version__)
        ui.pruefen(w, manuell=True, url=api_alt.as_uri())
        for _ in range(100):
            if gezeigt:
                break
            QTest.qWait(20)
        assert gezeigt == ["aktuell"]
        del suche
    finally:
        w.document.undo.setClean()
        w.close()
