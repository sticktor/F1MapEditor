"""Umgebungswerkzeuge (20.11.2026): Reihe entlang der Strecke, Band/Innenfeld, Tore über der Fahrbahn, Fundament,
Sichtkorridor, Modellbibliothek, Swofty-Addons."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from mapeditor import bibliothek, umgebung
from mapeditor.adapter import build_command

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ego-werkzeuge"))


def _oval(a=400.0, b=300.0, n=400):
    """Gegen den Uhrzeigersinn auf der Karte (z unten): Start bei (a, 0), Richtung −z."""
    return [(a * math.cos(t), -b * math.sin(t)) for t in np.linspace(0, 2 * math.pi, n, endpoint=False)]


def test_reihe_steht_neben_der_strecke_und_schaut_zu_ihr():
    linie = umgebung.Linie(_oval())
    objekte, info = umgebung.reihe(linie, "eaid://m", 0, 200, 50, "beide", 30, vorne_grad=270)
    assert len(objekte) == 10 and info["gesetzt"] == 10 and not info["ab_mauer"]
    innen = objekte[0]                       # erste Station, links = innen
    assert innen["x"] == pytest.approx(370, abs=0.5) and innen["z"] == pytest.approx(0, abs=0.5)
    # Vorderseite (270° im Modell) + Drehung zeigt nach +x, also zur Strecke bei x = 400
    assert (innen["drehung_grad"] + 270) % 360 == pytest.approx(0, abs=0.5)
    laengs, _ = umgebung.reihe(linie, "eaid://m", 0, 0, 10, "rechts", 30, ausrichtung="laengs", vorne_grad=0)
    assert len(laengs) == 1 and laengs[0]["drehung_grad"] == pytest.approx(90, abs=1)   # Fahrtrichtung −z = 90°
    with pytest.raises(umgebung.StreuFehler):
        umgebung.reihe(linie, "eaid://m", 0, 2000, 0.5, "beide", 30)


def test_band_und_innenfeld_streuen():
    linie = umgebung.Linie(_oval())
    band = umgebung.GebietBand(linie, 100, 600, "rechts", 15, 60)
    objekte, info = umgebung.streuen(band, ["eaid://t"], 50, 1)
    assert objekte and all(band.enthaelt([(o["x"], o["z"])])[0] for o in objekte)
    _i, seitlich = linie.naechster([(o["x"], o["z"]) for o in objekte])
    assert (seitlich >= 15 - 1e-6).all() and (seitlich <= 60 + 1e-6).all()      # rechts = außen beim Oval
    innen = umgebung.GebietInnen(linie)
    assert innen.flaeche_m2() == pytest.approx(math.pi * 400 * 300, rel=0.03)
    objekte, _ = umgebung.streuen(innen, ["eaid://t"], 2, 3)
    assert objekte and linie.innen([(o["x"], o["z"]) for o in objekte]).all()


def test_objekt_ueber_fahrbahn_und_fundament():
    o = umgebung.normalisiere_objekt({"id": "a", "modell": "eaid://x", "x": 0, "z": 0, "ueber_fahrbahn": True,
                                      "boden": "fundament"})
    assert o["ueber_fahrbahn"] is True and o["boden"] == "fundament"
    assert "ueber_fahrbahn" not in umgebung.normalisiere_objekt({"id": "b", "modell": "eaid://x", "x": 0, "z": 0,
                                                                 "ueber_fahrbahn": False})
    with pytest.raises(ValueError):
        umgebung.normalisiere_objekt({"id": "c", "modell": "eaid://x", "x": 0, "z": 0, "ueber_fahrbahn": "ja"})


def test_fundament_ebnet_gelaende_ein():
    from strecke import spiel_geometrie as G
    xs, zs = np.meshgrid(np.arange(-100, 101, 8.0), np.arange(-100, 101, 8.0))
    gp = np.c_[xs.ravel(), xs.ravel() * 0.1, zs.ravel()]           # Hang 10 %
    strecke = np.array([[-300.0, 0, -300], [-300.0, 0, 300]])
    bericht = G.fundamente_anwenden(gp, strecke, [{"id": "t", "ecken": [[-25, -11], [25, -11], [25, 11], [-25, 11]]}])
    innen = (np.abs(gp[:, 0]) <= 25) & (np.abs(gp[:, 2]) <= 11)
    assert np.ptp(gp[innen, 1]) < 1e-9 and bericht["fundamente"][0]["hoehe"] == pytest.approx(2.8)
    weit = np.abs(gp[:, 0]) >= 48
    assert np.allclose(gp[weit, 1], gp[weit, 0] * 0.1)               # außerhalb des Rands unverändert


def test_pvs_korridor_im_bau(tmp_path):
    basis = (tmp_path / "toolkit", tmp_path / "projekt", tmp_path / "aus", tmp_path / "pakete")
    for p in basis:
        p.mkdir()
    (basis[0] / "neue_strecke_tool.py").write_text("")
    args = build_command(basis[0], basis[1], basis[2], basis[3], "x1", options={"pvs_korridor": 45})
    assert args[args.index("--pvs-korridor") + 1] == "45"
    assert "--pvs-korridor" not in build_command(basis[0], basis[1], basis[2], basis[3], "x2", options={"pvs_korridor": 0})
    with pytest.raises(ValueError):
        build_command(basis[0], basis[1], basis[2], basis[3], "x3", options={"pvs_korridor": 5})


def test_modellbibliothek(tmp_path):
    projekt = tmp_path / "p"
    (projekt / "modelle").mkdir(parents=True)
    (projekt / "modelle" / "ente.glb").write_bytes(b"glb")
    (projekt / "modelle" / "ente_lod1.glb").write_bytes(b"lod")
    eintrag = {"name": "ente", "datei": "ente.glb", "lod_dateien": ["ente_lod1.glb"], "kollision": "quader",
               "animation": "an", "effekte": {}, "statistik": {"dreiecke": [8920, 2268]}}
    gespeichert = bibliothek.speichern(projekt, eintrag, basis=tmp_path / "lib")
    assert gespeichert["optionen"]["kollision"] == "quader" and gespeichert["dreiecke"] == 8920
    liste = bibliothek.liste(tmp_path / "lib")
    assert [e["name"] for e in liste] == ["ente"]
    auftrag = bibliothek.import_auftrag(liste[0])
    assert Path(auftrag["datei"]).read_bytes() == b"glb" and len(auftrag["lods"]) == 1
    with pytest.raises(bibliothek.BibliothekFehler):
        bibliothek.speichern(projekt, eintrag, basis=tmp_path / "lib")
    bibliothek.entfernen("ente", tmp_path / "lib")
    assert bibliothek.liste(tmp_path / "lib") == []


def test_swofty_addons_dialog_entfernt_nur_unbenutzte_traeger(app, monkeypatch):
    from mapeditor import build
    addons = [{"id": "mapeditor.a", "version": "1", "zustand": "enabled", "name": "A", "gb": 0.4,
               "braucht": ["mapeditor.traeger_1"]},
              {"id": "mapeditor.traeger_1", "version": "1", "zustand": "enabled", "name": "T", "gb": 2.5, "braucht": []},
              {"id": "swofty.core", "version": "1", "zustand": "built in", "name": "Core", "gb": 0, "braucht": []}]
    befehle = []
    monkeypatch.setattr("mapeditor.adapter.swofty_addons", lambda programm: list(addons))
    monkeypatch.setattr(build.QMessageBox, "question", staticmethod(lambda *a, **k: build.QMessageBox.Yes))
    d = build.SwoftyAddonsDialog("swofty.exe", runner=befehle.append)
    assert d.table.rowCount() == 3 and d.table.item(1, 5).text() == "mapeditor.a"
    d.table.selectRow(1)                     # nur der Träger: wird gebraucht → bleibt
    d._remove()
    assert befehle == []
    d.table.clearSelection()
    d._run("remove", [addons[0], addons[1]])
    assert befehle == [["swofty.exe", "addons", "remove", "mapeditor.a"], ["swofty.exe", "addons", "remove",
                                                                          "mapeditor.traeger_1"]]
    d.close()
