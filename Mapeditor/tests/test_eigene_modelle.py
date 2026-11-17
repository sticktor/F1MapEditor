"""Eigene 3D-Modelle im Mapeditor (M4, 16.11.2026, docs/eigene_modelle_m1.md).

Arbeitsprozess-Aufgaben ``modell_importieren``/``modell_entfernen``, Katalog mit Quelle ``projekt``, Vorschaubild aus
der Datei im Projekt, Umgebungsreiter (Filter „Own models“, Angaben, Entfernen-Knopf) und Import im Hauptfenster.
"""

import json
import sys

import pytest
from PySide6.QtWidgets import QMessageBox

from mapeditor import adapter
from mapeditor.project import Project
from mapeditor.umgebung_ui import EIGENE, EnvironmentPanel
from mapeditor.window import MainWindow
from conftest import EXAMPLES, ROOT

WERKZEUG = ROOT / "ego-werkzeuge"


def _module():
    sys.path.insert(0, str(WERKZEUG))
    sys.path.insert(0, str(WERKZEUG / "tests"))
    from ego import modell_import as M
    try:
        import modell_import_test as T
    except ImportError:
        pytest.skip("Test helper of the toolkit (ego-werkzeuge/tests) is not part of the public source")
    return M, T


@pytest.fixture
def glb(tmp_path):
    M, T = _module()
    pfad = tmp_path / "Mein Schild.glb"
    M.glb_schreiben(T.schild_szene(0), str(pfad))
    lod = tmp_path / "Mein Schild_LOD1.glb"
    M.glb_schreiben(T.schild_szene(1), str(lod))
    return pfad, lod


@pytest.fixture
def projekt(tmp_path):
    p = Project.open(EXAMPLES / "teststrecke.json")
    p.save(tmp_path / "projekt")
    return tmp_path / "projekt"


def test_worker_import_katalog_vorschau_entfernen(glb, projekt, tmp_path):
    pfad, lod = glb
    e = adapter.calculate_model_import({"projekt": str(projekt), "datei": str(pfad), "lods": [str(lod)]})
    assert e["name"] == "mein_schild" and e["modell"].endswith("/idf/usr_mein_schild")
    assert (projekt / "modelle" / "mein_schild.glb").is_file() and (projekt / "modelle" / "mein_schild_lod1.glb").is_file()
    assert e["statistik"]["lods"] == 2 and e["statistik"]["dreiecke"] == [36, 24]
    eigene = adapter._eigene_modelle_katalog({"projekt": str(projekt)})
    k = eigene["modelle"][0]
    assert k["quelle"] == "projekt" and k["name"] == "usr_mein_schild" and k["modell"] == e["modell"]
    assert eigene["tabelle"]["modelle"][0]["name"] == "mein_schild"
    ziel = tmp_path / "vorschau" / "v.png"
    aus = adapter.calculate_env_preview({"modell": k["modell"], "pfad": str(ziel), "groesse": 96, "projekt": str(projekt)})
    assert aus["pfad"] == str(ziel) and ziel.stat().st_size > 500
    with pytest.raises(ValueError):          # gleicher Name ein zweites Mal
        adapter.calculate_model_import({"projekt": str(projekt), "datei": str(pfad)})
    weg = adapter.calculate_model_remove({"projekt": str(projekt), "name": "mein_schild"})
    assert weg["name"] == "mein_schild" and not (projekt / "modelle" / "mein_schild.glb").exists()
    assert adapter._eigene_modelle_katalog({"projekt": str(projekt)})["modelle"] == []
    assert adapter._eigene_modelle_katalog({"projekt": None}) is None


def test_worker_fehlerhafte_datei(projekt, tmp_path):
    kaputt = tmp_path / "kaputt.glb"
    kaputt.write_bytes(b"kein glb")
    with pytest.raises(ValueError) as fehler:
        adapter.calculate_model_import({"projekt": str(projekt), "datei": str(kaputt)})
    assert "GLB" in str(fehler.value)
    assert not (projekt / "modelle" / "kaputt.glb").exists()


EIGEN = "eaid://environment_package/tracks/portimao/objects/idf/usr_ente"
SPIEL = "eaid://environment_package/core_assets/buildings/tribune_a"
KATALOG = {"modelle": [
    {"modell": EIGEN, "name": "usr_ente", "familie": "projekt", "kategorie": "sonstiges", "groesse_m": [9, 7, 11],
     "radius_m": 7.2, "aabb_min": [-4.6, 0, -5.5], "aabb_max": [4.6, 7, 5.5], "quelle": "projekt", "im_traeger": False,
     "vorne_grad": 270.0, "vorne_quelle": "projekt", "datei": "ente.glb", "dreiecke": 8920,
     "hinweise": ["Model starts 1.00 m above its origin. With ground mode Automatic the build puts its lower edge on the ground"]},
    {"modell": SPIEL, "name": "tribune_a", "familie": "tribune", "kategorie": "gebaeude", "groesse_m": [40, 12, 14],
     "radius_m": 21.2, "aabb_min": [-20, 0, -7], "aabb_max": [20, 12, 7], "quelle": "tracks/spa/wep/spa_common.erp",
     "im_traeger": False}]}


def test_reiter_filter_angaben_entfernen(app):
    panel = EnvironmentPanel()
    panel.set_catalog(KATALOG)
    assert panel.list.count() == 2
    panel.category.setCurrentIndex(panel.category.findData(EIGENE))
    assert panel.list.count() == 1 and EIGEN == panel.list.item(0).data(256)
    panel.select_models([EIGEN])
    text = panel.details.text()
    assert "ente.glb" in text and "8" in text and "⚠" in text
    assert not panel.remove_model_button.isHidden()
    gemeldet = []
    panel.remove_model_requested.connect(gemeldet.append)
    panel.remove_model_button.click()
    assert gemeldet == ["ente"]
    panel.category.setCurrentIndex(0)
    panel.select_models([SPIEL])
    assert panel.remove_model_button.isHidden()
    importiert = []
    panel.import_requested.connect(lambda: importiert.append(True))
    panel.import_button.click()
    assert importiert == [True]


def test_fenster_import_ohne_ordner_fragt_nach_speichern(app, monkeypatch):
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    try:
        w.document.project.folder = None
        gezeigt = []
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: gezeigt.append(a[2]))
        w.import_own_model("egal.glb")
        assert gezeigt and w.model_job is None
    finally:
        w.document.undo.setClean()
        w.close()


def test_fenster_import_ergebnis(app, projekt, glb, monkeypatch):
    w = MainWindow(Project.open(projekt), auto_preview=False)
    try:
        geladen = []
        monkeypatch.setattr(w, "load_env_catalog", lambda neu=False: geladen.append(neu))
        assert w._model_project() == str(projekt)
        e = adapter.calculate_model_import({"projekt": str(projekt), "datei": str(glb[0])})
        w._model_import_done(str(glb[0]), 0, json.dumps(e))
        assert geladen == [False] and w.model_select_after == e["modell"]
        assert "usr_mein_schild" in w.status_label.text()
        w._model_import_done("x.glb", 1, json.dumps({"error": "Not a GLB file of version 2: x.glb"}))
        assert "x.glb" in w.status_label.text() or "GLB" in w.status_label.text()
        w._env_catalog_done(0, json.dumps(KATALOG | {"modelle": KATALOG["modelle"] + [
            dict(KATALOG["modelle"][0], modell=e["modell"], name="usr_mein_schild")]}))
        assert w.env_panel.category.currentData() == EIGENE and w.env_panel.armed_model() == e["modell"]
    finally:
        w.document.undo.setClean()
        w.close()


def test_kollision_haekchen_und_arbeitsprozess(app, glb, projekt):
    """M5b: Häkchen „Collision“ in den Angaben eines eigenen Modells, Aufgabe ``modell_aendern``."""
    katalog = {"modelle": [dict(KATALOG["modelle"][0], kollision="keine"), KATALOG["modelle"][1]]}
    panel = EnvironmentPanel()
    panel.set_catalog(katalog)
    panel.select_models([EIGEN])
    assert not panel.collision_box.isHidden() and not panel.collision_box.isChecked()
    gemeldet = []
    panel.collision_changed.connect(lambda name, an: gemeldet.append((name, an)))
    panel.collision_box.click()
    assert gemeldet == [("ente", True)]
    panel.set_catalog({"modelle": [dict(KATALOG["modelle"][0], kollision="quader"), KATALOG["modelle"][1]]})
    panel.select_models([EIGEN])
    assert panel.collision_box.isChecked() and gemeldet == [("ente", True)]   # Anzeige setzt keinen Auftrag ab
    panel.select_models([SPIEL])
    assert panel.collision_box.isHidden()
    panel.set_catalog(KATALOG)                       # Werkzeugkasten ohne M5b: kein Feld, kein Häkchen
    panel.select_models([EIGEN])
    assert panel.collision_box.isHidden()
    pfad, _lod = glb
    adapter.calculate_model_import({"projekt": str(projekt), "datei": str(pfad)})
    e = adapter.calculate_model_change({"projekt": str(projekt), "name": "mein_schild", "optionen": {"kollision": "quader"}})
    assert e["kollision"] == "quader"
    tabelle = json.loads((projekt / "modelle" / "modelle.json").read_text(encoding="utf-8"))
    assert tabelle["modelle"][0]["kollision"] == "quader"
    with pytest.raises(Exception):
        adapter.calculate_model_change({"projekt": str(projekt), "name": "mein_schild", "optionen": {"kollision": "kugel"}})
