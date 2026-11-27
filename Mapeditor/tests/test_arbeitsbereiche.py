"""Arbeitsbereiche (21.11.2026): Strecke, Umgebung, 3D, Bauen, Alles; gemerkte Änderungen je Bereich."""
from __future__ import annotations

import pytest

from mapeditor import arbeitsbereiche


def test_vorgaben_und_merken(app):
    assert set(arbeitsbereiche.SCHLUESSEL) == set(arbeitsbereiche.VORGABEN)
    assert "seitenleiste" in arbeitsbereiche.SCHALTER and "raster" not in arbeitsbereiche.SCHALTER
    alles = arbeitsbereiche.vorgabe("alles")
    assert all(alles["schalter"].values()) and not alles["3d"]
    stand = arbeitsbereiche.laden("umgebung")
    assert stand["schalter"]["seitenleiste"] is False and stand["reiter"] == "reiter_umgebung"
    stand["schalter"]["seitenleiste"] = True
    stand["reiter"] = "reiter_pruefung"
    stand["groessen"] = {"vertikal": [500, 300]}
    arbeitsbereiche.merken("umgebung", stand)
    wieder = arbeitsbereiche.laden("umgebung")
    assert wieder["schalter"]["seitenleiste"] is True and wieder["reiter"] == "reiter_pruefung"
    assert wieder["groessen"] == {"vertikal": [500, 300]}
    arbeitsbereiche.vergessen("umgebung")
    assert arbeitsbereiche.laden("umgebung")["schalter"]["seitenleiste"] is False
    arbeitsbereiche.aktiv_merken("bauen")
    assert arbeitsbereiche.aktiv_laden() == "bauen"
    assert arbeitsbereiche.groessen_aus_anteilen([0.25, 0.75], 1000) == [250, 750]


def test_hauptfenster_wechselt_bereiche(app, tmp_path):
    pytest.importorskip("PySide6.QtQuick3D")
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    from test_ansicht3d import _oval_szene
    _oval_szene(tmp_path)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.netz_3d = str(tmp_path / "netz.npz")
    w.resize(1400, 900)
    w.show()
    try:
        assert w.arbeitsbereich == "alles" and w.bereich_stand is None          # Tests: alte Aufteilung
        w.set_workspace("umgebung")
        assert w.view_state["seitenleiste"] is False and w.view_state["eigenschaften"] is True
        assert w.results.currentWidget() is w.env_panel and w.map.mode == "umgebung"
        assert not w.results.isTabVisible(w.results.indexOf(w.height_page))
        assert w.bereich_leiste.currentIndex() == arbeitsbereiche.SCHLUESSEL.index("umgebung")
        w.set_workspace("strecke")
        assert w.view_state["seitenleiste"] is True and w.map.mode == "auswahl"
        assert w.results.currentWidget() is w.height_page
        assert not w.results.isTabVisible(w.results.indexOf(w.env_panel))
        w.set_mode("umgebung")                                                  # Werkzeug zieht seinen Bereich nach
        assert w.arbeitsbereich == "umgebung"
        w.set_workspace("3d")
        a = w.ansicht_3d
        assert a is not None and a.parent() is w.platz_3d and w.platz_3d.isVisible() and a.isVisible()
        assert not w.results.isVisible() and not w.left_panel.isVisible()
        assert w.karten_split.sizes()[1] > w.karten_split.sizes()[0]
        w.open_3d_view()                                                        # herauslösen
        assert a.isWindow() and not w.platz_3d.isVisible()
        w.set_workspace("3d")                                                   # wieder einbetten
        assert a.parent() is w.platz_3d and not a.isWindow()
        w.set_workspace("bauen")
        assert not w.platz_3d.isVisible() and not a.takt.isActive()             # ausgeblendet: Takt steht
        assert w.results.currentWidget() is w.issue_panel
        w.bereich_leiste.setCurrentIndex(arbeitsbereiche.SCHLUESSEL.index("alles"))
        assert w.arbeitsbereich == "alles" and all(w.view_state[k] for k in arbeitsbereiche.SCHALTER)
    finally:
        w.document.undo.setClean()
        w.close()


def test_ohne_qt_quick_3d_kein_absturz(app, monkeypatch):
    """Fehlt Qt Quick 3D (z. B. unvollständig gepackt, 26.11.2026), fällt der Arbeitsbereich 3D auf „Track“ zurück und
    das 3D-Fenster öffnet sich nicht – statt eines Absturzes beim Start."""
    import builtins
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    echt = builtins.__import__

    def ohne_3d(name, *args, **kwargs):
        if name.endswith("ansicht3d_ui") or (args and args[2] and "ansicht3d_ui" in (args[2] or ())):
            raise ImportError("DLL load failed while importing QtQuick3D")
        return echt(name, *args, **kwargs)
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    try:
        monkeypatch.setattr(builtins, "__import__", ohne_3d)
        w.set_workspace("3d")
        assert w.arbeitsbereich == "strecke" and w.ansicht_3d is None
        assert "QtQuick3D" in w.status_label.text()                          # Meldung mit Ursache (jede Sprache)
        w.open_3d_view()
        assert w.ansicht_3d is None
    finally:
        monkeypatch.undo()
        w.document.undo.setClean()
        w.close()


def test_eine_leiste_mit_werkzeugen_je_bereich(app):
    """Eine Leiste (27.11.2026): Arbeitsbereiche links in der Werkzeugleiste, daneben nur die Werkzeuge des Bereichs,
    Projektinfo in der Statusleiste, Kürzel eines fremden Werkzeugs wechselt den Bereich."""
    from mapeditor.i18n import tr
    from mapeditor.project import Project
    from mapeditor.window import MainWindow
    from conftest import EXAMPLES
    w = MainWindow(Project.open(EXAMPLES / "teststrecke.json"), auto_preview=False)
    w.resize(1400, 900)
    w.show()
    try:
        leiste = w.toolbar
        assert leiste.widgetForAction(leiste.actions()[0]) is w.bereich_leiste      # Bereiche ganz links
        assert w.project_heading.parent() is w.statusBar() or w.statusBar().isAncestorOf(w.project_heading)
        assert "Teststrecke" in w.project_heading.text()
        assert w.mode_actions["umgebung"].text() == tr("Place")
        assert tr(arbeitsbereiche.NAMEN["bauen"]) == tr("Review")
        sichtbar = lambda: {m for m, a in w.mode_actions.items() if a.isVisible()}     # noqa: E731
        w.set_workspace("umgebung")
        assert sichtbar() == {"auswahl", "umgebung", "streuen"} and not w.close_action.isVisible()
        w.set_workspace("strecke")
        assert {"auswahl", "nachzeichnen", "kameras"} <= sichtbar() and "umgebung" not in sichtbar()
        assert w.close_action.isVisible()
        w.set_workspace("bauen")
        assert sichtbar() == {"auswahl"}
        w.set_mode("kameras")                                    # Kürzel K im Bereich Review: wechselt zu Track
        assert w.arbeitsbereich == "strecke" and w.map.mode == "kameras" and w.mode_actions["kameras"].isVisible()
        w.set_mode("streuen")                                    # B: Umgebung
        assert w.arbeitsbereich == "umgebung" and w.map.mode == "streuen"
        w.set_workspace("alles")
        assert {"auswahl", "umgebung", "streuen", "nachzeichnen", "kameras"} <= sichtbar()
    finally:
        w.document.undo.setClean()
        w.close()
