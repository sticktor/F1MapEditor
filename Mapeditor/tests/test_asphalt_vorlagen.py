"""Fahrbahnkatalog (fahrbahn.json) und ERP-Kopier-Backend (PR #7 von Ftoaster)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ego-werkzeuge"))

from strecke import asphalt_vorlagen  # noqa: E402


def test_fahrbahn_katalog_liegt_im_repo():
    daten = asphalt_vorlagen.katalog_laden()
    assert daten.get("version") == 2
    asphalt = daten["asphalt"]
    assert "traeger" in asphalt["vorlagen"]
    assert asphalt["vorlagen"]["traeger"].get("traeger") is True
    assert asphalt["reihenfolge"][0] == "traeger"
    # Katalog = Ausgabe von analysieren: Abu Dhabi allein, Monaco & Co. mit eigenem Material (Baku-Gruppe)
    assert asphalt["vorlagen"]["abu_dhabi"]["aliase"][0] == "abu_dhabi"
    assert "monza" in asphalt["vorlagen"]["baku"]["aliase"] and "monaco" in asphalt["vorlagen"]["baku"]["aliase"]
    assert {"hungaroring", "catalunya", "melbourne"} <= set(asphalt["vorlagen"])
    skid = daten["skid"]
    assert skid["vorlagen"]["traeger"].get("traeger") is True
    assert skid["reihenfolge"][0] == "traeger"
    assert {"abu_dhabi", "losail", "hungaroring", "silverstone"} == set(skid["vorlagen"]) - {"traeger"}
    assert "spa_francorchamps" in skid["vorlagen"]["silverstone"]["aliase"]
    assert "miami" in skid["vorlagen"]["losail"]["aliase"]
    assert "type1" in skid["vorlagen"]["abu_dhabi"]["aliase"]


def test_vorschaubilder_vollstaendig():
    """Jeder Katalogeintrag hat sein Vorschaubild, und es liegen keine übrigen Bilder herum."""
    daten = asphalt_vorlagen.katalog_laden()
    ordner = Path(asphalt_vorlagen.DATEN).parent / "vorschau"
    for art in ("asphalt", "skid"):
        namen = {e["vorschau"] for e in daten[art]["vorlagen"].values()}
        assert namen == {p.name for p in (ordner / art).glob("*.png")}, art


def test_alias_aufloesen_und_traeger_noop():
    key, ein = asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "monza")
    assert key == "baku" and ein["strecke"] == "baku"
    key, ein = asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "type1")
    assert key == "abu_dhabi"
    with pytest.raises(asphalt_vorlagen.VorlagenFehler, match="Unknown"):
        asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "gibt_es_nicht")
    assert asphalt_vorlagen.einsetzen(None, "traeger") == {
        "vorlage": "traeger", "traeger": True, "mipmaps": []}
    assert asphalt_vorlagen.skid_einsetzen(None, "type5")["traeger"] is True
    assert asphalt_vorlagen._aufloesen(asphalt_vorlagen.skid_katalog(), "miami")[0] == "losail"
    with pytest.raises(asphalt_vorlagen.VorlagenFehler):       # lt_ribbon-Strecken haben keine Reifenspurkarte
        asphalt_vorlagen._aufloesen(asphalt_vorlagen.skid_katalog(), "bahrain")
    assert asphalt_vorlagen._aufloesen(asphalt_vorlagen.asphalt_katalog(), "hungaroring")[0] == "hungaroring"


@pytest.fixture
def window(app, example_preview):
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES

    project = Project.open(EXAMPLES / "teststrecke.json")
    w = MainWindow(project, auto_preview=False)
    w.install_preview(example_preview)
    yield w
    w.document.undo.setClean()
    w.close()


def test_build_dialog_fuellt_fahrbahn(window):
    """Combo-Boxen lesen fahrbahn.json und zeigen Streckennamen; Standard = Träger."""
    w = window
    w.open_build()
    d = w.build_dialog
    assert d.asphalt.count() >= 10
    assert d.asphalt.itemData(0) == "traeger" and d.asphalt.itemText(0) == "Träger (Portimão)"
    assert "Abu Dhabi" in d.asphalt.itemText(d.asphalt.findData("abu_dhabi"))
    assert d.asphalt.currentData() == "traeger"
    assert d.skid.count() >= 2
    assert d.skid.itemData(0) == "traeger"
    assert d.skid.currentData() == "traeger"
    # Alias aus altem Spielstand / skid_vergleich-Namen
    d._fill_skid("type5")
    assert d.skid.currentData() == "traeger"
    d._fill_skid("miami")
    assert d.skid.currentData() == "losail"
    d._fill_skid("bahrain")                 # nicht (mehr) im Katalog: Träger
    assert d.skid.currentData() == "traeger"
    assert d.skid.count() == 5
    d._fill_asphalt("monza")
    assert d.asphalt.currentData() == "baku"
    d._fill_asphalt("hungaroring")
    assert d.asphalt.currentData() == "hungaroring"


def test_fahrbahn_json_ohne_spieltexturen():
    """Katalog darf keine extrahierten Spieltexturen enthalten (nur eaid + Vorschau-Dateinamen)."""
    roh = (ROOT / "ego-werkzeuge" / "strecke" / "daten" / "fahrbahn.json").read_text(encoding="utf-8")
    assert "asphalt_texturen" not in roh
    daten = json.loads(roh)
    for key, ein in daten["asphalt"]["vorlagen"].items():
        if ein.get("traeger"):
            continue
        assert ein.get("slots"), key
        assert all(v.startswith("eaid://") for v in ein["slots"].values()), key
        assert "/" not in (ein.get("vorschau") or ""), key
    for key, ein in daten["skid"]["vorlagen"].items():
        if ein.get("traeger"):
            continue
        assert (ein.get("quelle") or "").startswith("eaid://"), key


def test_skid_katalog_aus_roh_merged_per_md5():
    """analysieren-Pfad: gleiche Skid-Textur-Hash → eine Gruppe; Portimão → traeger."""
    roh = {
        "portimao": {"quelle": "eaid://skid/portimao", "finger": "aaa"},
        "abu_dhabi": {"quelle": "eaid://skid/a", "finger": "bbb"},
        "monza": {"quelle": "eaid://skid/a", "finger": "bbb"},
        "bahrain": {"quelle": "eaid://skid/b", "finger": "ccc"},
        "imola": {"quelle": "eaid://skid/b", "finger": "ccc"},
        "hungaroring": {"quelle": "eaid://skid/portimao", "finger": "aaa"},
    }
    alt = {"vorlagen": {
        "abu_dhabi": {"quelle": "eaid://skid/a", "aliase": ["type1"], "vorschau": "abu_dhabi.png"},
        "imola": {"quelle": "eaid://skid/b", "aliase": ["type3", "bahrain"], "vorschau": "imola.png"},
        "traeger": {"traeger": True, "aliase": ["type5"], "vorschau": "traeger.png"},
    }}
    skid = asphalt_vorlagen.skid_katalog_aus_roh(roh, alt)
    assert skid["reihenfolge"][0] == "traeger"
    assert "hungaroring" in skid["vorlagen"]["traeger"]["aliase"]
    assert "type5" in skid["vorlagen"]["traeger"]["aliase"]
    assert "monza" in skid["vorlagen"]["abu_dhabi"]["aliase"]
    assert "type1" in skid["vorlagen"]["abu_dhabi"]["aliase"]
    assert skid["vorlagen"]["abu_dhabi"]["vorschau"] == "abu_dhabi.png"
    assert "bahrain" in skid["vorlagen"]["imola"]["aliase"] or "bahrain" in skid["vorlagen"]["bahrain"]["aliase"]
    assert "portimao" not in skid["vorlagen"] or skid["vorlagen"].get("portimao", {}).get("traeger")


def test_fahrbahn_pruefregeln_unbekannt_und_spielordner(monkeypatch):
    from strecke import pruefregeln, umgebung

    m = []
    gp = pruefregeln.fahrbahn_pruefen("gibt_es_nicht", "traeger", m)
    assert "FAHRBAHN_UNBEKANNT" in gp
    assert any(x["id"] == "FAHRBAHN_UNBEKANNT" for x in m)

    m2 = []
    monkeypatch.setattr(umgebung, "spielordner_finden", lambda *a, **k: None)
    gp2 = pruefregeln.fahrbahn_pruefen("abu_dhabi", "traeger", m2)
    assert "FAHRBAHN_SPIELORDNER" in gp2
    assert any(x["id"] == "FAHRBAHN_SPIELORDNER" for x in m2)

    m3 = []
    monkeypatch.setattr(umgebung, "spielordner_finden", lambda *a, **k: None)
    gp3 = pruefregeln.profil_pruefen(asphalt_vorlage="traeger", skid_vorlage="traeger", m=m3)
    assert "FAHRBAHN_UNBEKANNT" in gp3 and "FAHRBAHN_SPIELORDNER" in gp3
    assert not any(x["id"] in ("FAHRBAHN_UNBEKANNT", "FAHRBAHN_SPIELORDNER") for x in m3)


def test_fahrbahn_pruefregel_traeger_ohne_katalog(monkeypatch, tmp_path):
    """Standard (Träger) braucht weder Katalog noch Spielordner; eine Vorlage ohne Katalog meldet FB-01."""
    from strecke import pruefregeln, umgebung

    monkeypatch.setattr(asphalt_vorlagen, "DATEN", str(tmp_path / "fehlt.json"))
    monkeypatch.setattr(umgebung, "spielordner_finden", lambda *a, **k: None)
    m = []
    pruefregeln.fahrbahn_pruefen("traeger", "traeger", m)
    pruefregeln.fahrbahn_pruefen(None, None, m)
    assert m == []
    pruefregeln.fahrbahn_pruefen("abu_dhabi", None, m)
    assert [x["id"] for x in m] == ["FAHRBAHN_UNBEKANNT"]


def test_farbton_der_vorlage_wird_neutral():
    """Feature GP 19.11.2026: Silverstones kühler Grundfarbton färbte den Asphalt grün-grau (ohne seine Makrokarte)."""
    from types import SimpleNamespace as P
    quelle = P(parameter=[P(name="me_baseMapTint", art=1, typ=0x30003, wert=(0.6038, 0.6445, 0.6939)),
                          P(name="me_detailStrength", art=1, typ=3, wert=(0.4,)),
                          P(name="me_dustColour", art=1, typ=0x30003, wert=(1.0, 0.7913, 0.3916))])
    ziel = P(parameter=[P(name="me_baseMapTint", art=1, typ=0x30003, wert=(0.73, 0.71, 0.69)),
                        P(name="me_detailStrength", art=1, typ=3, wert=(1.0,)),
                        P(name="me_dustColour", art=1, typ=0x30003, wert=(1.0, 0.888, 0.672))])
    erg = asphalt_vorlagen._konstanten_uebernehmen(quelle, ziel)
    r, g, b = erg["me_baseMapTint"]
    assert r == g == b and abs(r - (0.6038 + 0.6445 + 0.6939) / 3) < 1e-9
    assert erg["me_detailStrength"] == (0.4,)
    d = erg["me_dustColour"]
    assert d[0] == d[1] == d[2] and abs(d[0] - (1.0 + 0.7913 + 0.3916) / 3) < 1e-9


def test_farbton_vorlage_bleibt_und_katalog_kennt_grundton():
    """Option „Farbton der Vorlage behalten“ (20.11.2026) und Grundton je Vorlage im Katalog (Vorschau im Editor)."""
    from types import SimpleNamespace as P
    quelle = P(parameter=[P(name="me_baseMapTint", art=1, typ=0x30003, wert=(0.6038, 0.6445, 0.6939))])
    ziel = P(parameter=[P(name="me_baseMapTint", art=1, typ=0x30003, wert=(0.73, 0.71, 0.69))])
    assert asphalt_vorlagen._konstanten_uebernehmen(quelle, ziel, "vorlage")["me_baseMapTint"] == (0.6038, 0.6445, 0.6939)
    with pytest.raises(asphalt_vorlagen.VorlagenFehler):
        asphalt_vorlagen._konstanten_uebernehmen(quelle, ziel, "bunt")
    vorlagen = asphalt_vorlagen.asphalt_katalog()["vorlagen"]
    assert all(len(e.get("grundton") or []) == 3 for e in vorlagen.values())
    assert vorlagen["silverstone"]["grundton"] == [0.604, 0.644, 0.694]
