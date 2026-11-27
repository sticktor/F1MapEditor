"""3D-Ansicht (21.11.2026): Daten ohne Qt (Szene, Boden, Objekte, Einblendungen, Kameras) und das Fenster."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from mapeditor import ansicht3d


def _oval_szene(tmp_path):
    """Kleines Generatorergebnis: Ring aus Asphalt (Radius 300 ± 6 m), Ideallinie in der Mitte, 50 m/s."""
    n = 360
    w = np.linspace(0, 2 * math.pi, n, endpoint=False)
    mitte = np.column_stack([300 * np.cos(w), np.zeros(n), -300 * np.sin(w)])
    innen, aussen = mitte * (294 / 300), mitte * (306 / 300)
    v = np.empty((2 * n, 3), np.float32)
    v[0::2], v[1::2] = innen, aussen
    i = np.arange(n) * 2
    j = (i + 2) % (2 * n)
    tri = np.column_stack([i, i + 1, j + 1, i, j + 1, j]).reshape(-1, 3)
    netz = {"vertices": v, "normalen": np.tile([0, 1, 0], (2 * n, 1)).astype(np.float32),
            "uv": np.zeros((2 * n, 2), np.float32), "dreiecke": tri.astype(np.uint32),
            "oberflaeche": np.zeros(len(tri), np.uint8), "oberflaechen_namen": ansicht3d.KLASSEN}
    logik = {"ideallinie": {"position": mitte.tolist(), "geschwindigkeit_m_s": [50.0] * n},
             "drs": [{"aktivierung_s_m": 100.0, "ende_s_m": 400.0}], "bremsmarker": [{"position": [0, 0, 310]}],
             "startplaetze": [{"position": [300, 0, 0]}], "sektoren": [{"s_start_m": 600.0}]}
    pfad = tmp_path / "netz.npz"
    ansicht3d.szene_speichern(pfad, {"netz": netz, "logik": logik})
    return ansicht3d.Szene(pfad)


def test_szene_kamera_und_zeit(tmp_path):
    sz = _oval_szene(tmp_path)
    assert sz.laenge == pytest.approx(2 * math.pi * 300, rel=0.01)
    assert sz.rundenzeit == pytest.approx(sz.laenge / 50.0, rel=0.01)
    assert sz.station_nach_zeit(10.0) == pytest.approx(500.0, rel=0.01)
    pos, ziel = sz.kamera("onboard", 0.0)
    assert pos[1] == pytest.approx(ansicht3d.AUGE_M) and np.linalg.norm(ziel - pos) > 10
    pos, ziel = sz.kamera("verfolger", 100.0)
    assert pos[1] == pytest.approx(ansicht3d.VERFOLGER_HOCH_M)
    pos, _ = sz.kamera("tv", 100.0)
    assert abs(math.hypot(pos[0], pos[2]) - 300.0) > 20          # neben der Strecke
    assert set(sz.klassen_netze()) == {"asphalt"}


def test_boden_objekte_einblendungen(tmp_path):
    sz = _oval_szene(tmp_path)
    gelaende = {"huegel": [{"x": 0.0, "z": 0.0, "hoehe_m": 20.0, "radius_m": 150.0}], "berge": []}
    b = ansicht3d.boden(sz, gelaende)
    h = ansicht3d.Bodenhoehe(b)
    assert -0.6 < float(h(300.0, 0.0)) <= -ansicht3d.BODEN_UNTER_M + 1e-6   # knapp unter der Strecke
    v = sz.vertices
    assert np.all(v[:, 1] - h(v[:, 0], v[:, 2]) >= ansicht3d.BODEN_UNTER_M - 1e-4)   # nirgends durch die Fahrbahn
    assert ansicht3d.nahgrenze(1.0) == pytest.approx(0.3) and ansicht3d.nahgrenze(1500.0) == pytest.approx(10.0)
    assert float(h(0.0, 0.0)) > 15.0                                # Hügel in der Mitte
    objekte = [{"id": "a", "modell": "eaid://x/tribuene", "x": 340.0, "z": 0.0, "skala": 1.0, "drehung_grad": 90.0},
               {"id": "b", "modell": "eaid://x/baum", "x": 360.0, "z": 0.0, "skala": 1.0}]
    index = {"eaid://x/tribuene": {"kategorie": "gebaeude", "aabb_min": [-10, 0, -5], "aabb_max": [10, 8, 5]},
             "eaid://x/baum": {"kategorie": "baum", "aabb_min": [-2, 0, -2], "aabb_max": [2, 10, 2]}}
    netze = ansicht3d.objekt_netze(objekte, index, h)
    assert len(netze["quader"]["idx"]) == 36 and len(netze["baeume"]["idx"]) > 0
    q = netze["quader"]["pos"]
    assert np.ptp(q[:, 0]) == pytest.approx(10, abs=0.01) and np.ptp(q[:, 2]) == pytest.approx(20, abs=0.01)  # gedreht
    assert ansicht3d.objekt_netze(objekte, index, h, ausgelassen={"a", "b"})["quader"]["idx"].size == 0
    e = ansicht3d.einblendungen(sz)
    assert {"ideallinie", "drs", "bremstafeln", "startplaetze", "sektoren"} <= set(e)
    assert all(len(e[k]["idx"]) for k in ("ideallinie", "drs", "bremstafeln", "startplaetze", "sektoren"))
    assert ansicht3d.naechstes_objekt(objekte, 341.0, 1.0) == "a"
    assert ansicht3d.naechstes_objekt(objekte, 0.0, 0.0) is None
    x, z, grad = ansicht3d.blick_auf_karte(np.array([0, 0, 0]), np.array([0, 0, -10]))
    assert grad == pytest.approx(90.0)


def test_fenster_oeffnet_und_faehrt(app, tmp_path):
    pytest.importorskip("PySide6.QtQuick3D")
    from mapeditor.ansicht3d_ui import Ansicht3D
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    sz = _oval_szene(tmp_path)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.netz_3d = str(tmp_path / "netz.npz")
    try:
        a = Ansicht3D(w)
        assert a.fehler == []
        assert a.szene is not None and a.szene.laenge == pytest.approx(sz.laenge)
        for art, _ in (("onboard", 0), ("verfolger", 0), ("tv", 0), ("uebersicht", 0), ("frei", 0)):
            a.kamera_setzen(art)
        a.kamera_setzen("onboard")
        a.station_setzen(500.0)
        assert a.s == pytest.approx(500.0) and w.map.kamera_3d is not None
        a.close()
        assert w.map.kamera_3d is None
    finally:
        w.document.undo.setClean()
        w.close()


def test_kamerafuehrung_kreisen_zoomen_fliegen():
    k = ansicht3d.Kamerafuehrung()
    k.blick([0.0, 100.0, 100.0], [0.0, 0.0, 0.0])
    assert k.nick == pytest.approx(-45.0) and k.gier == pytest.approx(0.0)
    abstand = k.abstand
    k.kreisen(90.0, 0.0)                                       # Viertelkreis um den Drehpunkt
    assert k.abstand == pytest.approx(abstand)
    assert float(np.dot(k.drehpunkt - k.pos, k.vorn)) / k.abstand == pytest.approx(1.0)
    assert k.pos[0] == pytest.approx(100.0, abs=1e-6)
    k.kreisen(0.0, -80.0)                                      # Nicken begrenzt
    assert k.nick == pytest.approx(-ansicht3d.Kamerafuehrung.NICK_GRENZE)
    k.blick([0.0, 100.0, 100.0], [0.0, 0.0, 0.0])
    k.zoomen(1.0, [10.0, 0.0, 0.0])                            # auf den Punkt unter der Maus zu
    assert np.linalg.norm(k.pos - [10, 0, 0]) == pytest.approx(0.8 * np.linalg.norm(np.array([0, 100, 100]) - [10, 0, 0]))
    vorher = k.pos.copy()
    k.schieben(100.0, 0.0, 800.0)                              # Maus nach rechts: Kamera nach links
    assert (k.pos - vorher)[0] < 0 and abs((k.pos - vorher)[1]) < 1e-9
    assert k.flugtempo(0.0) == pytest.approx(12.0) and k.flugtempo(100.0) == pytest.approx(120.0)
    assert k.flugtempo(100.0, schnell=True) == pytest.approx(480.0)
    vorher = k.pos.copy()
    k.bewegen(1, 0, 0, 1.0, 10.0, fliegen=False)               # W ohne rechte Maus: parallel zum Boden
    assert np.linalg.norm(k.pos - vorher) == pytest.approx(10.0) and k.pos[1] == pytest.approx(vorher[1])
    k.anfliegen([500.0, 0.0, 0.0], abstand=30.0)
    while k.schritt(0.05):
        pass
    assert k.drehpunkt == pytest.approx([500.0, 0.0, 0.0]) and k.abstand == pytest.approx(30.0)
    k.alles_zeigen([-100, 0, -50], [100, 10, 50])
    assert k.nick == pytest.approx(-50.0) and k.drehpunkt == pytest.approx([0, 5, 0])


def test_fenster_maus_und_tasten(app, tmp_path):
    pytest.importorskip("PySide6.QtQuick3D")
    from PySide6.QtCore import Qt
    from mapeditor.ansicht3d_ui import Ansicht3D, KNOPF_LINKS, KNOPF_RECHTS
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    _oval_szene(tmp_path)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.netz_3d = str(tmp_path / "netz.npz")
    try:
        a = Ansicht3D(w)
        assert a.kamera_art == "frei" and a.kam.nick == pytest.approx(-50.0)     # erstes Bild: ganze Strecke
        a.kamera_setzen("onboard")
        a.station_setzen(200.0)
        a._maus_druck(KNOPF_LINKS, 0, 100.0, 100.0, True, 300.0, 0.0, 0.0)           # Ziehen verlässt die Fahrkamera
        assert a.kamera_art == "frei" and a.kam.drehpunkt == pytest.approx([300, 0, 0])
        gier = a.kam.gier
        a._maus_zug(150.0, 100.0, False, 0, 0, 0)
        a._maus_los(KNOPF_LINKS, 150.0, 100.0, False, 0, 0, 0)
        assert a.kam.gier == pytest.approx(gier - 15.0)
        abstand = a.kam.abstand
        a._maus_rad(1.0, 100.0, 100.0, False, 0, 0, 0)
        assert a.kam.abstand == pytest.approx(abstand * 0.8)
        a._maus_druck(KNOPF_RECHTS, 0, 100.0, 100.0, False, 0, 0, 0)
        a._maus_rad(1.0, 100.0, 100.0, False, 0, 0, 0)                               # rechte Maus + Rad = Flugtempo
        assert a.kam.flugfaktor == pytest.approx(1.25)
        vorher = a.kam.pos.copy()
        a._tasten[Qt.Key_W] = (1, 0, 0)
        a._letzte -= 0.05
        a._schritt()
        assert np.linalg.norm(a.kam.pos - vorher) > 0.5
        a._tasten.clear()
        a._maus_los(KNOPF_RECHTS, 100.0, 100.0, False, 0, 0, 0)
        a.close()
    finally:
        w.document.undo.setClean()
        w.close()


def test_boden_im_loch_des_aussengelaendes(tmp_path):
    """Spielgrafik (26.11.2026): Boden füllt das Loch und geht am Rand in die Höhe des Lochrands über."""
    sz = _oval_szene(tmp_path)
    loch = (-1000.0, -1000.0, 2000.0)
    seite = np.linspace(-1000, 1000, 81)
    rand = np.concatenate([np.column_stack([seite, np.full(81, 30.0), np.full(81, z)]) for z in (-1000.0, 1000.0)]
                          + [np.column_stack([np.full(81, x), np.full(81, 30.0), seite]) for x in (-1000.0, 1000.0)])
    b = ansicht3d.boden(sz, None, loch=loch, lochrand=rand, uv_m=8.0, farbig=False)
    p = b["pos"]
    assert p[:, 0].min() == pytest.approx(-1000.0) and p[:, 2].min() == pytest.approx(-1000.0)
    assert p[:, 0].max() >= 1000.0 - 1e-3
    h = ansicht3d.Bodenhoehe(b)
    assert float(h(-1000.0, 0.0)) == pytest.approx(30.0, abs=0.5)        # am Lochrand: Höhe des Rands
    assert float(h(0.0, 0.0)) < 5.0                                       # innen: Strecke
    v = sz.vertices
    assert np.all(v[:, 1] - h(v[:, 0], v[:, 2]) >= ansicht3d.BODEN_UNTER_M - 1e-4)
    assert np.allclose(b["farbe"][:, :3].max(), 1.05, atol=0.06)          # echte Textur: keine Grüntönung
    assert b["uv"][1, 0] - b["uv"][0, 0] == pytest.approx(ansicht3d.BODEN_RASTER_M / 8.0)


def _assets(tmp_path):
    """Kleines Ergebnis wie ``spielassets_3d``: Asphalt mit Textur, ein Außengelände, zwei Weltnetze."""
    from PIL import Image
    png = tmp_path / "tex.png"
    Image.new("RGB", (8, 8), (90, 90, 90)).save(png)
    quad = {"pos": np.array([[0, 0, 0], [10, 0, 0], [10, 0, 10], [0, 0, 10]], np.float32),
            "nor": np.tile([0, 1, 0], (4, 1)).astype(np.float32), "uv": np.zeros((4, 2), np.float32),
            "idx": np.array([0, 1, 2, 0, 2, 3], np.uint32)}

    def npz(name, dy=0.0):
        pfad = tmp_path / (name + ".npz")
        np.savez(pfad, **dict(quad, pos=quad["pos"] + [0, dy, 0]))
        return str(pfad)
    rand = tmp_path / "rand.npz"
    np.savez(rand, rand=np.array([[-1000, 5, -1000], [1000, 5, 1000]], np.float32))
    return {"oberflaechen": {"asphalt": {"npz": npz("asphalt"), "png": str(png), "tint": [0.7, 0.7, 0.7],
                                         "uv_faktor": [0.5, 0.5]}},
            "boden": {"png": str(png), "tint": [1, 1, 1], "uv_m": 8.0},
            "aussen": {"netze": [{"npz": npz("aussen", -5), "png": str(png), "tint": [1, 1, 1]}],
                       "loch": [-1000.0, -1000.0, 2000.0], "rand": str(rand)},
            "welt": {"netze": [{"npz": npz("welt", 1), "png": None}, {"npz": npz("welt2", 2), "png": str(png)}],
                     "modelle": 2, "instanzen": 3}}


def test_spielgrafik_anwenden_und_ausschalten(app, tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtQuick3D")
    from PySide6.QtCore import QObject
    from mapeditor import ansicht3d_ui
    from mapeditor.ansicht3d_ui import Ansicht3D
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    _oval_szene(tmp_path)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.netz_3d = str(tmp_path / "netz.npz")
    anfragen = []
    monkeypatch.setattr(Ansicht3D, "_assets_anfordern", lambda self: anfragen.append(1))
    try:
        a = Ansicht3D(w)
        assert a.fehler == []
        obj = lambda n: a.wurzel.findChild(QObject, n)              # noqa: E731
        synthetisch = obj("kt_asphalt").property("source").toLocalFile()
        assert synthetisch.endswith("tex2_asphalt.png") and obj("kt_linie").property("source").toString() == ""
        a.assets, a.assets_anfrage = _assets(tmp_path), a._assets_signatur()
        a.spielgrafik_setzen(True)
        assert not anfragen                                         # passendes Ergebnis: keine neue Anfrage
        assert obj("kt_asphalt").property("source").toLocalFile().endswith("tex.png")
        assert obj("kt_asphalt").property("flipV") is True and obj("kt_asphalt").property("scaleU") == pytest.approx(0.5)
        assert obj("kt_gras").property("source").toLocalFile().endswith("tex2_gras.png")   # ohne Spielnetz: wie bisher
        assert obj("a_0").property("visible") and not obj("a_1").property("visible")
        assert obj("w_0").property("visible") and obj("w_1").property("visible") and not obj("w_2").property("visible")
        assert obj("wt_1").property("source").toLocalFile().endswith("tex.png")
        assert obj("wt_0").property("source").toString() == ""
        assert not obj("berge").property("visible")                 # Außengelände statt Kunstberge
        assert a.boden_hoehe.x0 == pytest.approx(-1000.0)           # Boden genau im Loch
        a._ebene("welt", False)
        assert not obj("w_0").property("visible")
        a._ebene("aussen", False)
        assert not obj("a_0").property("visible") and obj("berge").property("visible")
        assert a.boden_hoehe.x0 > -1000.0                           # wieder Rand um die Strecke
        a._ebene("aussen", True)
        a._ebene("welt", True)
        a.spielgrafik_setzen(False)
        assert obj("kt_asphalt").property("source").toLocalFile() == synthetisch
        assert not obj("a_0").property("visible") and not obj("w_0").property("visible")
        a.spielgrafik_setzen(True)
        assert obj("w_0").property("visible")
        alt = a.szene_stand
        a.szene_stand = "anderes|1|1"                               # anderes Netz: altes Ergebnis passt nicht
        assert a._aktive_assets() is None
        a.szene_stand = alt
        anzahl = len(anfragen)
        a._spielgrafik_anwenden()
        assert len(anfragen) == anzahl                             # kein doppelter Auftrag
        boden_vorher = a.boden_hoehe
        a._umgebung_setzen()
        assert a.boden_hoehe is boden_vorher                       # Boden nicht doppelt gerechnet
        assert ansicht3d_ui._einstellung_spielgrafik() is True       # gemerkt
        a.close()
    finally:
        from PySide6.QtCore import QSettings
        QSettings().remove("ansicht3d/spielgrafik")
        w.document.undo.setClean()
        w.close()


def test_spielassets_arbeitsprozess_ohne_traeger(tmp_path):
    from mapeditor import adapter
    with pytest.raises(ValueError):
        adapter.calculate_game_assets_3d({"traeger": str(tmp_path / "fehlt.zip"), "szene": "x", "cache": str(tmp_path)})


def test_netz_nur_bei_neuem_inhalt_schreiben_und_neu_laden(app, tmp_path):
    """Ladezeit (3d_ladeperformance.md): gleicher Inhalt → Datei bleibt (Zeitstempel), neuer Inhalt → Ansicht lädt neu."""
    import os
    sz = _oval_szene(tmp_path)
    pfad = tmp_path / "netz.npz"
    ansicht3d.welt_datei(pfad).write_text("{}", encoding="utf-8")       # wie mit Logik gespeichert
    stand = pfad.stat().st_mtime_ns
    netz = {"vertices": sz.vertices, "normalen": sz.normalen, "uv": sz.uv, "dreiecke": sz.dreiecke,
            "oberflaeche": sz.oberflaeche.astype(np.uint8), "oberflaechen_namen": sz.namen}
    os.utime(pfad, ns=(stand - 10**9, stand - 10**9))
    alt = pfad.stat().st_mtime_ns
    ansicht3d.szene_speichern(pfad, {"netz": netz, "logik": sz.logik})
    assert pfad.stat().st_mtime_ns == alt                                 # unverändert: nicht neu geschrieben
    netz["vertices"] = sz.vertices + np.float32(1.0)
    ansicht3d.szene_speichern(pfad, {"netz": netz, "logik": sz.logik})
    assert pfad.stat().st_mtime_ns != alt
    assert ansicht3d.Szene(pfad).vertices[0, 0] == pytest.approx(sz.vertices[0, 0] + 1.0)


def test_assets_index_ohne_arbeitsprozess(app, tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtQuick3D")
    from mapeditor import adapter
    from mapeditor.ansicht3d_ui import Ansicht3D
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    _oval_szene(tmp_path)
    traeger = tmp_path / "traeger.zip"
    traeger.write_bytes(b"PK")
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.netz_3d = str(tmp_path / "netz.npz")
    gestartet = []
    monkeypatch.setattr(adapter.ProcessJob, "start", lambda self, *a: gestartet.append(a))
    monkeypatch.setattr(adapter, "carrier_path", lambda *a, **k: traeger)
    try:
        a = Ansicht3D(w)
        anfrage, fehler = a._assets_eingaben()
        assert fehler is None
        anfrage["cache"] = str(tmp_path / "cache")
        monkeypatch.setattr(Ansicht3D, "_assets_eingaben", lambda self: (dict(anfrage), None))
        erg = _assets(tmp_path)
        assert ansicht3d.assets_index_lesen(anfrage) is None
        ansicht3d.assets_index_schreiben(anfrage, erg)
        assert ansicht3d.assets_index_lesen(anfrage) == erg
        a.spielgrafik_setzen(True)
        assert not gestartet and a.assets == erg                     # Treffer: ohne Arbeitsprozess
        assert ansicht3d.assets_index_datei(dict(anfrage, versatz=[1, 0, 0])) != ansicht3d.assets_index_datei(anfrage)
        Path(erg["welt"]["netze"][0]["npz"]).unlink()                 # Datei weg: Index ungültig
        assert ansicht3d.assets_index_lesen(anfrage) is None
        a.close()
    finally:
        from PySide6.QtCore import QSettings
        QSettings().remove("ansicht3d/spielgrafik")
        w.document.undo.setClean()
        w.close()


def test_netz_von_der_platte(app, tmp_path, monkeypatch):
    from mapeditor import ansicht3d as a3
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    monkeypatch.setattr(a3, "cache_ordner", lambda: tmp_path)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    try:
        assert not w.netz_von_platte() and w.netz_3d is None
        a3.netz_datei(w.current_key()).write_bytes(b"x")
        assert w.netz_von_platte() and w.netz_3d == str(a3.netz_datei(w.current_key()))
        assert not w._welt_3d_vorhanden()                             # ohne Logik: Lauf trotzdem anstoßen
    finally:
        w.document.undo.setClean()
        w.close()


def test_mehrere_modelle_unbekannt():
    from mapeditor import adapter
    erg = adapter.calculate_models_3d({"auftraege": [{"modell": "eaid://gibt/es/nicht", "ziel": "x.npz"}]})
    assert "error" in erg["modelle"]["eaid://gibt/es/nicht"]


def test_tafeln_und_startplaetze_ausgerichtet(tmp_path):
    """Einblendungen (26.11.2026): Entfernungstafeln quer zur Fahrtrichtung mit Zahl, Startplätze in Fahrtrichtung."""
    sz = _oval_szene(tmp_path)
    sz.logik["bremsmarker"] = [{"position": [0.0, 0.0, -310.0], "meter": 100},
                               {"position": [0.0, 0.0, -315.0], "meter": 100}, {"position": [310, 0, 0], "meter": 50}]
    e = ansicht3d.einblendungen(sz)
    assert set(e["tafeln"]) == {50, 100} and len(e["tafeln"][100]["idx"]) == 12
    t = e["tafeln"][50]
    d = sz.richtung_bei([310, 0, 0])
    assert np.allclose(t["nor"][0], -d, atol=1e-5)                       # Vorderseite zu den ankommenden Autos
    assert np.ptp(t["pos"][:, 1]) == pytest.approx(ansicht3d.TAFEL_HOEHE_M)
    assert abs(float(np.dot(t["pos"][1] - t["pos"][0], d))) < 1e-4       # quer zur Fahrtrichtung
    assert t["uv"] is not None and ansicht3d.tafel_textur(50, tmp_path).is_file()
    sp = e["startplaetze"]["pos"]                                         # Startplatz bei (300, 0, 0), Ring: Richtung ±z
    assert np.ptp(sp[:, 2]) == pytest.approx(ansicht3d.STARTPLATZ_LAENGE_M, abs=0.3)
    assert np.ptp(sp[:, 0]) == pytest.approx(ansicht3d.STARTPLATZ_BREITE_M, abs=0.3)
    assert e["startplaetze"]["farbe"][0, 3] < 1.0                         # halbtransparent


def test_antwort_lesen_mit_text_davor():
    from mapeditor import adapter
    assert adapter.antwort_lesen('{"a": 1}') == {"a": 1}
    assert adapter.antwort_lesen('Process SpawnProcess-1:\nTraceback {x}\n{"modelle": {"m": {"npz": "x"}}}') == \
        {"modelle": {"m": {"npz": "x"}}}
    with pytest.raises(ValueError):
        adapter.antwort_lesen("kein json")


def test_ladeanzeige_und_katalog_nachladen(app, tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtQuick3D")
    from mapeditor.ansicht3d_ui import Ansicht3D
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    _oval_szene(tmp_path)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    try:
        a = Ansicht3D(w)
        a.show()
        assert a.szene is None and a.ladeanzeige.isVisible() and a.ladeanzeige.mitte    # leer: groß in der Mitte
        assert not a.ladeanzeige.balken.isVisible()                                     # kein Lauf: nur Hinweis
        w.analysis_job = object()
        a._ladeanzeige_aktualisieren()
        assert a.ladeanzeige.balken.isVisible()
        w.analysis_job = None
        w.netz_3d = str(tmp_path / "netz.npz")
        a.aktualisieren()
        assert a.szene is not None and not a.ladeanzeige.isVisible()
        a.assets_job = object()
        a._ladeanzeige_aktualisieren()
        assert a.ladeanzeige.isVisible() and not a.ladeanzeige.mitte                    # Hintergrund: unten links
        a.assets_job = None
        # Load real models ohne geladenen Katalog: Katalog anstoßen, danach weiter
        from mapeditor import umgebung
        w.document.project.umgebung = umgebung.neu(False)
        w.document.project.umgebung["objekte"] = [{"id": "x", "modell": "eaid://a/b", "x": 0.0, "z": 0.0}]
        geladen = []
        monkeypatch.setattr(w, "load_env_catalog", lambda *a_, **k: geladen.append(1))
        w.map.env_index = {}
        a.echte_modelle_laden()
        assert geladen and a._echte_warten and a.ladeanzeige.isVisible()
        w.map.env_index = {"eaid://a/b": {"kategorie": "gebaeude", "quelle": "projekt"}}  # Katalog da: weiter
        a.aktualisieren()
        assert not a._echte_warten and not a.modell_jobs                   # nichts aus dem Spiel zu laden
        a.close()
    finally:
        w.document.undo.setClean()
        w.close()


def test_boden_nicht_durch_breite_fahrbahndreiecke(tmp_path):
    """Grasflecken (26.11.2026): 40 m breite Fahrbahn nur mit Randvertices, außen eine Grasböschung auf 3 m: der Boden darf
    auch in der Mitte der Dreiecke nicht durchstoßen (Höhe wie gezeichnet, zwei Dreiecke je Zelle)."""
    n = 120
    w = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ring = lambda r, y: np.column_stack([r * np.cos(w), np.full(n, y), -r * np.sin(w)])   # noqa: E731
    v = np.concatenate([ring(280, 0.0), ring(320, 0.0), ring(320, 0.0), ring(340, 3.0)]).astype(np.float32)
    i = np.arange(n)
    j = (i + 1) % n
    tri, ober = [], []
    for band, klasse in ((0, 0), (2, 2)):                    # Asphalt innen, Gras außen
        a0, a1 = band * n, (band + 1) * n
        tri += [np.column_stack([a0 + i, a1 + i, a1 + j]), np.column_stack([a0 + i, a1 + j, a0 + j])]
        ober += [np.full(2 * n, klasse)]
    tri = np.concatenate(tri).astype(np.uint32)
    netz = {"vertices": v, "normalen": np.tile([0, 1, 0], (len(v), 1)).astype(np.float32),
            "uv": np.zeros((len(v), 2), np.float32), "dreiecke": tri, "oberflaeche": np.concatenate(ober).astype(np.uint8),
            "oberflaechen_namen": ansicht3d.KLASSEN}
    pfad = tmp_path / "breit.npz"
    ansicht3d.szene_speichern(pfad, {"netz": netz, "logik": {"ideallinie": {"position": ring(300, 0).tolist()}}})
    sz = ansicht3d.Szene(pfad)
    h = ansicht3d.Bodenhoehe(ansicht3d.boden(sz, None))
    e = sz.vertices[sz.dreiecke].astype(float)
    rng = np.random.default_rng(5)
    for _ in range(10):
        g = rng.dirichlet([1, 1, 1], len(e))
        p = (e * g[:, :, None]).sum(1)
        assert np.all(p[:, 1] - h(p[:, 0], p[:, 2]) >= 0.1)          # nirgends durch die Oberfläche


def test_instanztabelle_und_modellcache(tmp_path):
    """Bäume (27.11.2026): Instanztabelle für FileInstancing, Zwischenspeicher der Modellteile mit Fassung."""
    import json as _json
    objekte = [{"x": 10.0, "z": -5.0, "drehung_grad": 30.0, "skala": 1.5, "hoehe_m": 0.5},
               {"x": 0.0, "z": 0.0}]
    text = ansicht3d.instanz_xml(objekte, lambda x, z: 2.0)
    assert text.count("<Instance ") == 2 and text.startswith("<?xml")
    assert 'position="10.000 2.500 -5.000"' in text and 'scale="1.5000 1.5000 1.5000"' in text
    assert 'eulerRotation="0 30.000 0"' in text
    ziel = tmp_path / "abc.npz"
    assert ansicht3d.modell_3d_lesen(ziel) is None
    teil = tmp_path / "abc_0.npz"
    np.savez(teil, pos=np.zeros((3, 3), np.float32), nor=np.zeros((3, 3), np.float32), idx=np.arange(3, dtype=np.uint32))
    daten = {"fassung": ansicht3d.MODELL_3D_FASSUNG, "teile": [{"npz": str(teil), "png": None, "maske": 0.3}]}
    ziel.with_suffix(".json").write_text(_json.dumps(daten), encoding="utf-8")
    assert ansicht3d.modell_3d_lesen(ziel)["teile"][0]["maske"] == 0.3
    ziel.with_suffix(".json").write_text(_json.dumps(dict(daten, fassung=1)), encoding="utf-8")
    assert ansicht3d.modell_3d_lesen(ziel) is None                     # alte Fassung: neu vorbereiten
    ziel.with_suffix(".json").write_text(_json.dumps(daten), encoding="utf-8")
    teil.unlink()
    assert ansicht3d.modell_3d_lesen(ziel) is None                     # Teil fehlt


def test_modellstufe_und_alpha_ausbluten():
    from PIL import Image
    from mapeditor import adapter
    assert adapter._modell_stufe(3, True) == 1 and adapter._modell_stufe(3, False) == 2      # Pflanze: nie Billboard
    assert adapter._modell_stufe(1, True) == 0 and adapter._modell_stufe(2, False) == 1
    a = np.zeros((4, 4, 4), np.uint8)
    a[..., :3] = 255                                                    # durchsichtig weiß (wie im Spiel)
    a[:2, :, :] = (40, 80, 20, 255)                                     # sichtbares Blatt
    b = np.asarray(adapter._alpha_ausbluten(Image.fromarray(a, "RGBA")))
    assert (b[2:, :, :3] == (40, 80, 20)).all() and (b[..., 3] == a[..., 3]).all()


def test_baeume_als_instanzen_mit_maske(app, tmp_path):
    pytest.importorskip("PySide6.QtQuick3D")
    from PySide6.QtCore import QObject
    from mapeditor import umgebung
    from mapeditor.ansicht3d_ui import Ansicht3D
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    _oval_szene(tmp_path)
    quad = {"pos": np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], np.float32),
            "nor": np.tile([0, 0, 1], (3, 1)).astype(np.float32), "uv": np.zeros((3, 2), np.float32),
            "idx": np.arange(3, dtype=np.uint32)}
    teile = []
    for k, maske in enumerate((None, 0.3)):
        pfad = tmp_path / ("baum_%d.npz" % k)
        np.savez(pfad, **quad)
        teile.append({"npz": str(pfad), "png": None, "maske": maske})
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.netz_3d = str(tmp_path / "netz.npz")
    try:
        a = Ansicht3D(w)
        assert a.fehler == []
        w.document.project.umgebung = umgebung.neu(False)
        w.document.project.umgebung["objekte"] = [
            {"id": "b%d" % i, "modell": "eaid://x/st_oak", "x": 300.0 + i * 20, "z": 50.0, "drehung_grad": 10.0 * i}
            for i in range(5)]
        a.spielmodelle = {"eaid://x/st_oak": {"teile": teile}}
        a.ebenen["echte"] = True
        gezeigt = a._echte_setzen(w.document.env["objekte"])
        assert gezeigt == {"b0", "b1", "b2", "b3", "b4"}                       # keine Kegel für diese Bäume
        obj = lambda n: a.wurzel.findChild(QObject, n)                          # noqa: E731
        assert obj("s_0").property("visible") and obj("s_1").property("visible") and not obj("s_2").property("visible")
        assert not obj("s_0").property("maske") and obj("s_1").property("maske")
        assert obj("s_1").property("schwelle") == pytest.approx(0.3)
        quelle = obj("si_0").property("source").toLocalFile()
        assert quelle == obj("si_1").property("source").toLocalFile()           # eine Tabelle für alle Teile
        assert Path(quelle).read_text(encoding="utf-8").count("<Instance ") == 5
        a.close()
    finally:
        w.document.undo.setClean()
        w.close()
