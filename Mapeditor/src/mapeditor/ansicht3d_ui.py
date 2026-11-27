"""3D-Ansicht im zweiten Fenster (21.11.2026, docs/3d_ansicht.md): Qt Quick 3D in einem ``QQuickWidget``.

Stufe 1 Probefahrt (Netz, Boden, Objekte als Quader/Bäume, Kameras Frei/Onboard/Verfolger/TV/Übersicht, Abspielen,
Tempofaktor, Station), Stufe 2 Aussehen (Texturen, Himmel, Sonne nach Tageszeit, Nebel, Berge, Einblendungen als Ebenen),
Stufe 3 echte Modelle (eigene GLB über ``RuntimeLoader``, Spielmodelle aus dem Spielordner über den Arbeitsprozess
``modell_3d``), Stufe 4 Bearbeiten (Objekt wählen, verschieben, drehen, löschen, Katalogmodell setzen, Ansichten merken).
Die Daten kommen aus ``ansicht3d`` (ohne Qt), Änderungen laufen über das Dokument des Hauptfensters (Rückgängig).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QByteArray, QEvent, QMetaObject, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QVector3D
from PySide6.QtQuick3D import QQuick3DGeometry
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QInputDialog, QLabel, QMainWindow, QMenu, QProgressBar,
                               QSlider, QToolBar, QToolButton, QVBoxLayout)

from . import ansicht3d, umgebung
from .i18n import N_, tr

A = QQuick3DGeometry.Attribute
KAMERAS = (("frei", N_("Editor (orbit · fly)")), ("onboard", N_("Onboard")), ("verfolger", N_("Chase")),
           ("tv", N_("TV")))
#: Tasten der Editor-Kamera: (vor, seitlich, hoch)
FLUG_TASTEN = {Qt.Key_W: (1, 0, 0), Qt.Key_Up: (1, 0, 0), Qt.Key_S: (-1, 0, 0), Qt.Key_Down: (-1, 0, 0),
               Qt.Key_D: (0, 1, 0), Qt.Key_Right: (0, 1, 0), Qt.Key_A: (0, -1, 0), Qt.Key_Left: (0, -1, 0),
               Qt.Key_E: (0, 0, 1), Qt.Key_PageUp: (0, 0, 1), Qt.Key_Q: (0, 0, -1), Qt.Key_PageDown: (0, 0, -1)}
KNOPF_LINKS, KNOPF_RECHTS, KNOPF_MITTE = 1, 2, 4
HILFE_KAMERA = N_("Editor camera: left drag orbits around the point under the mouse, right drag looks around "
                  "(hold it and fly with W A S D, Q/E down/up, Shift fast, wheel = flying speed), middle or "
                  "Shift+left drag pans, wheel zooms to the mouse, double-click flies there, Home shows the whole "
                  "track, F focuses the selection or the car.")


def _datei_kennung(pfad):
    """``Pfad|Zeitstempel|Größe`` einer Datei oder None, wenn sie fehlt."""
    try:
        st = Path(pfad).stat()
    except (OSError, TypeError):
        return None
    return "%s|%d|%d" % (pfad, st.st_mtime_ns, st.st_size)


def _einstellung_spielgrafik() -> bool:
    """Spielgrafik der 3D-Ansicht eingeschaltet? (gemerkt; aus im Rauchtest)"""
    try:
        from .ansicht import settings
        wert = settings().value("ansicht3d/spielgrafik", False)
    except Exception:  # noqa: BLE001
        return False
    return str(wert).lower() in ("1", "true")


def _wert(x) -> int:
    return int(getattr(x, "value", x))


def _hat(mods, modifier) -> bool:
    """Umschalttaste gedrückt? ``mods`` als Zahl (aus QML) oder als Qt-Flag."""
    return bool(_wert(mods) & _wert(modifier))
TEMPO = (0.25, 0.5, 1.0, 2.0, 4.0)
EBENEN = (("umgebung", N_("Environment objects")), ("baeume", N_("Trees and plants")),
          ("ideallinie", N_("Racing line (speed)")), ("drs", N_("DRS zones")), ("bremstafeln", N_("Braking boards")),
          ("startplaetze", N_("Grid slots")), ("sektoren", N_("Sector lines")), ("bereich", N_("Track area")),
          ("berge", N_("Mountains")), ("aussen", N_("Outer terrain (game look)")),
          ("welt", N_("Pit buildings and boards (game look)")))
#: Plätze für echte Modelle in der Szene (Spielmodelle je Modelltyp, eigene Modelle je Objekt)
SPIEL_PLAETZE, EIGENE_PLAETZE = 128, 40
#: Höchstens so viele Spielmodell-Typen laden (je Typ ein bis mehrere Teile = Plätze)
SPIEL_TYPEN = 60
#: Spielgrafik (docs/3d_spielassets.md): Netze der Trägerwelt (je Albedo-Textur eines) und des Außengeländes
WELT_PLAETZE, AUSSEN_PLAETZE = 128, 2
#: Entfernungstafeln der Einblendungen: je Meterzahl ein Netz mit eigener Textur
TAFEL_PLAETZE = 8
#: Aussehen der Klassen ohne Spielgrafik: (selbst erzeugte Textur oder None, Farbe, Rauheit)
KLASSEN_AUSSEHEN = {"asphalt": ("asphalt", "#ffffff", 0.85), "boxengasse": ("asphalt", "#ffffff", 0.85),
                    "auslauf": ("asphalt", "#ffffff", 0.85), "randstein": ("randstein", "#ffffff", 0.7),
                    "gras": ("gras", "#ffffff", 1.0), "kies": ("kies", "#ffffff", 1.0),
                    "mauer": ("beton", "#ffffff", 0.9), "gebaeude": ("beton", "#ffffff", 0.9),
                    "linie": (None, "#f2f2f2", 0.6), "startbox": (None, "#ffffff", 0.6),
                    "wulst": (None, "#f2c200", 0.55), "randstein_anfang": (None, "#eeeeee", 0.7),
                    "randstein_ende": (None, "#eeeeee", 0.7), "randstein_tief": ("randstein", "#ffffff", 0.8),
                    "kunstrasen": (None, "#3fa34d", 0.95)}


class Geometrie(QQuick3DGeometry):
    """Dreiecksnetz aus ``ansicht3d`` (pos, nor, optional uv und farbe) für ein ``Model``."""

    def setzen(self, netz):
        self.clear()
        if netz is None or not len(netz["idx"]):
            self.update()
            return
        spalten, attribute, versatz = [netz["pos"], netz["nor"]], [(A.PositionSemantic, 0), (A.NormalSemantic, 12)], 24
        if netz.get("uv") is not None:
            spalten.append(netz["uv"])
            attribute.append((A.TexCoord0Semantic, versatz))
            versatz += 8
        if netz.get("farbe") is not None:
            spalten.append(netz["farbe"])
            attribute.append((A.ColorSemantic, versatz))
            versatz += 16
        daten = np.ascontiguousarray(np.hstack(spalten).astype(np.float32))
        self.setStride(versatz)
        self.setVertexData(QByteArray(daten.tobytes()))
        self.setIndexData(QByteArray(np.ascontiguousarray(netz["idx"], np.uint32).tobytes()))
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
        for semantik, off in attribute:
            self.addAttribute(semantik, off, A.F32Type)
        self.addAttribute(A.IndexSemantic, 0, A.U32Type)
        lo, hi = netz["pos"].min(0), netz["pos"].max(0)
        self.setBounds(QVector3D(*map(float, lo)), QVector3D(*map(float, hi)))
        self.update()


def _url(pfad):
    return QUrl.fromLocalFile(str(pfad)).toString()


def szene_qml(texturen: dict) -> str:
    """QML der Szene: Kamera, Licht, Himmel, je Oberflächenklasse ein Modell, Boden, Berge, Objekte, Einblendungen,
    Auto, Auswahlrahmen, Plätze für echte Modelle; ``treffer(x, y)`` liefert den Szenenpunkt unter der Maus."""
    def textur(name, quelle=""):
        return ('    Texture { id: %s; objectName: "%s"; source: "%s"; tilingModeHorizontal: Texture.Repeat; '
                'tilingModeVertical: Texture.Repeat; generateMipmaps: true; mipFilter: Texture.Linear }'
                % (name, name, quelle))

    def mat_wahl(name, farbe, rau):
        """Material mit Textur ``name`` (ohne Quelle nur die Farbe); Quelle, Kachelung und Farbe setzt Python um."""
        return ('PrincipledMaterial { objectName: "%sm"; baseColor: "%s"; roughness: %s; cullMode: Material.NoCulling; '
                'baseColorMap: %s.source.toString() !== "" ? %s : null }' % (name, farbe, rau, name, name))

    teile = []
    for k in ansicht3d.KLASSEN:
        tex, farbe, rau = KLASSEN_AUSSEHEN.get(k, (None, "#888888", 0.9))
        teile.append(textur("kt_" + k, _url(texturen[tex]) if tex else ""))
        teile.append('    Model { objectName: "k_%s"; pickable: true; materials: %s }' % (k, mat_wahl("kt_" + k, farbe, rau)))
    for i in range(AUSSEN_PLAETZE):
        teile.append(textur("at_%d" % i))
        teile.append('    Model { objectName: "a_%d"; visible: false; pickable: true; materials: %s }'
                     % (i, mat_wahl("at_%d" % i, "#ffffff", 1.0)))
    for i in range(WELT_PLAETZE):
        teile.append(textur("wt_%d" % i))
        teile.append('    Model { objectName: "w_%d"; visible: false; pickable: true; materials: %s }'
                     % (i, mat_wahl("wt_%d" % i, "#b8b8b8", 0.8)))
    teile.append(textur("bt", _url(texturen["gras"])))
    for i in range(TAFEL_PLAETZE):                     # Entfernungstafeln: Vorderseite mit der Meterzahl
        teile.append('    Texture { id: tft_%d; objectName: "tft_%d"; generateMipmaps: true; mipFilter: Texture.Linear }'
                     % (i, i))
        teile.append('    Model { objectName: "tf_%d"; visible: false; materials: PrincipledMaterial { roughness: 0.6; '
                     'cullMode: Material.BackFaceCulling; baseColorMap: tft_%d } }' % (i, i))
    modelle = "\n".join(teile)
    farbe_mat = "PrincipledMaterial { vertexColorsEnabled: true; roughness: 0.95; cullMode: Material.NoCulling }"
    unbeleuchtet = ("PrincipledMaterial { vertexColorsEnabled: true; lighting: PrincipledMaterial.NoLighting; "
                    "alphaMode: PrincipledMaterial.Blend; cullMode: Material.NoCulling }")
    ebenen = "\n".join('    Model { objectName: "e_%s"; materials: %s }' % (n, unbeleuchtet if n in ("ideallinie", "drs", "bereich", "startplaetze")
                                                                          else farbe_mat)
                       for n in ("ideallinie", "drs", "bremstafeln", "startplaetze", "sektoren", "bereich"))
    # Spielmodelle (27.11.2026): je Teil (Modell × Material) ein Netz im Modellraum, Lagen als Instanztabelle
    # (FileInstancing, XML im Zwischenspeicher); Blätter mit Alpha-Maske (``maske``/``schwelle`` setzt Python)
    spiel = "\n".join('    Model { id: s_%d; objectName: "s_%d"; visible: false; pickable: true; property bool maske: false; '
                      'property real schwelle: 0.5; instancing: FileInstancing { objectName: "si_%d" } '
                      'materials: PrincipledMaterial { objectName: "sm_%d"; baseColorMap: Texture { objectName: "st_%d"; '
                      'flipV: true; generateMipmaps: true; mipFilter: Texture.Linear } roughness: 0.8; '
                      'cullMode: Material.NoCulling; alphaMode: s_%d.maske ? PrincipledMaterial.Mask : '
                      'PrincipledMaterial.Opaque; alphaCutoff: s_%d.schwelle; '
                      # Ohne Tiefen-Vorpass mit Alpha-Test schreibt die Maske die Tiefe der ganzen Blattkarte: dahinter
                      # bliebe die Löschfarbe stehen (weiße Bäume, 27.11.2026)
                      'depthDrawMode: s_%d.maske ? Material.OpaquePrePassDepthDraw : Material.OpaqueOnlyDepthDraw; '
                      'specularAmount: s_%d.maske ? 0.1 : 0.5 } }'
                      % (i, i, i, i, i, i, i, i, i) for i in range(SPIEL_PLAETZE))
    eigene = "\n".join('    RuntimeLoader { objectName: "u_%d"; visible: false }' % i for i in range(EIGENE_PLAETZE))
    return """
import QtQuick
import QtQuick3D
import QtQuick3D.Helpers
import QtQuick3D.AssetUtils

Item {
  id: wurzel
  View3D {
    id: view
    objectName: "view"
    anchors.fill: parent
    environment: SceneEnvironment {
      objectName: "umwelt"
      backgroundMode: SceneEnvironment.SkyBox
      lightProbe: Texture { textureData: ProceduralSkyTextureData { objectName: "himmel" } }
      probeExposure: 0.9
      antialiasingMode: SceneEnvironment.MSAA
      antialiasingQuality: SceneEnvironment.High
      fog: Fog { objectName: "nebel"; enabled: true; depthEnabled: true; depthNear: 800; depthFar: 12000; color: "#c4d2e0" }
    }
    PerspectiveCamera { id: cam; objectName: "cam"; clipNear: 0.3; clipFar: 25000; fieldOfView: 62 }
    DirectionalLight { objectName: "sonne"; eulerRotation.x: -45; eulerRotation.y: -30; brightness: 1.15
                       castsShadow: true; shadowMapQuality: Light.ShadowMapQualityHigh; shadowFactor: 55 }
%s
    Model { objectName: "boden"; pickable: true; materials: PrincipledMaterial { objectName: "btm"; vertexColorsEnabled: true
            roughness: 1.0; baseColorMap: bt } }
    Model { objectName: "berge"; pickable: true; materials: %s }
    Model { objectName: "quader"; pickable: true; materials: %s }
    Model { objectName: "baeume"; pickable: true; materials: %s }
%s
%s
%s
    Model { objectName: "auto"; source: "#Cube"; scale: Qt.vector3d(0.019, 0.011, 0.05)
            materials: PrincipledMaterial { baseColor: "#ff6a00"; roughness: 0.4 } }
    Model { objectName: "auswahl"; source: "#Cube"; visible: false; opacity: 0.45
            materials: PrincipledMaterial { baseColor: "#ffd400"; lighting: PrincipledMaterial.NoLighting
                                            alphaMode: PrincipledMaterial.Blend } }
  }
  function treffer(x, y) {
    var r = view.pick(x, y)
    return r.objectHit !== null ? [1, r.scenePosition.x, r.scenePosition.y, r.scenePosition.z] : [0, 0, 0, 0]
  }
}
""" % (modelle, farbe_mat, farbe_mat, farbe_mat, ebenen, spiel, eigene)


class Ladeanzeige(QFrame):
    """Hinweis mit Fortschrittsbalken über der 3D-Ansicht (26.11.2026): groß in der Mitte, solange die Ansicht leer
    ist, sonst klein unten links (Spielgrafik, Modelle). Ohne bekannte Schrittzahl läuft der Balken unbestimmt."""

    def __init__(self, eltern):
        super().__init__(eltern)
        self.setObjectName("ladeanzeige")
        self.setStyleSheet("#ladeanzeige { background: rgba(22, 27, 34, 225); border: 1px solid rgba(255,255,255,40); "
                           "border-radius: 8px; } #ladeanzeige QLabel { color: #e6ebf0; background: transparent; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        self.titel = QLabel()
        self.titel.setWordWrap(True)
        self.titel.setObjectName("ladetitel")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.balken = QProgressBar()
        self.balken.setTextVisible(False)
        self.balken.setFixedHeight(8)
        lay.addWidget(self.titel)
        lay.addWidget(self.text)
        lay.addWidget(self.balken)
        self.mitte = True
        self.hide()
        eltern.installEventFilter(self)

    def zeigen(self, titel, text="", mitte=False, laeuft=True):
        self.titel.setText("<b>%s</b>" % titel)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        self.balken.setVisible(laeuft)
        self.balken.setRange(0, 0)
        self.mitte = mitte
        self._lage()
        self.show()
        self.raise_()

    def _lage(self):
        e = self.parentWidget()
        if e is None:
            return
        breite = min(480, max(e.width() - 40, 200)) if self.mitte else min(340, max(e.width() - 24, 160))
        self.setFixedWidth(breite)
        self.adjustSize()
        if self.mitte:
            self.move((e.width() - self.width()) // 2, (e.height() - self.height()) // 2)
        else:
            self.move(12, e.height() - self.height() - 12)

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QEvent.Resize and self.isVisible():
            self._lage()
        return False


class Ansicht3D(QMainWindow):
    """Zweites Fenster mit der 3D-Ansicht. ``haupt`` ist das Hauptfenster (Dokument, Analyse, Katalog, Station)."""
    station_geaendert = Signal(float)

    def __init__(self, haupt):
        super().__init__(None)
        self.haupt = haupt
        self.setWindowTitle(tr("3D view"))
        self.resize(1280, 760)
        self.szene = None
        self.szene_pfad = None
        self.szene_stand = None          # Pfad und Zeitstempel des geladenen Netzes
        self._env_schluessel = None
        self.s = 0.0
        self.spielt = False
        self.tempo = 1.0
        self.kamera_art = "frei"
        self.kam = ansicht3d.Kamerafuehrung()
        self._pose = None                # (Position, Ziel) der letzten Fahrkamera
        self._maus_aktion = None         # kreisen / blick / schieben / objekt
        self._maus = (0.0, 0.0)
        self._knopf = 0
        self._tasten = {}
        self._erstes_bild = True
        self.ebenen = {n: n not in ("bereich",) for n, _ in EBENEN}
        self.ebenen["echte"] = False      # echte Modelle: mit „Game look“
        self._echte_versucht = set()       # Spielmodelle, die schon angefragt wurden (kein erneuter Lauf bei Fehlern)
        self._im_nachladen = False
        self.geometrien = {}
        self.boden_hoehe = None
        self.auswahl = None
        self.ziehen = None
        self.spielmodelle = {}           # Modell → {"teile": [{"npz", "png", "maske"}]} im Zwischenspeicher
        self.modell_jobs = []
        self.modell_job = None
        self.spielgrafik = _einstellung_spielgrafik()
        self.assets = None               # Ergebnis von ``spielassets_3d`` (docs/3d_spielassets.md)
        self.assets_anfrage = None       # Signatur der Anfrage, zu der ``assets`` gehört
        self.assets_job = None
        self._assets_neu = False         # während eines Laufs geänderte Eingaben: danach erneut anfragen
        self._lochrand = (None, None)
        self._boden_schluessel = None
        self._letzte = time.time()
        self._letzte_meldung = 0.0
        self.texturen = ansicht3d.texturen()
        self._treffer_beim_zug = False
        self.view = QQuickWidget(self)
        self.view.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.view.installEventFilter(self)
        qml = ansicht3d.cache_ordner() / "szene.qml"
        qml.write_text(szene_qml(self.texturen), encoding="utf-8")
        self.view.setSource(QUrl.fromLocalFile(str(qml)))
        self.fehler = [e.toString() for e in self.view.errors()]
        self.setCentralWidget(self.view)
        self.ladeanzeige = Ladeanzeige(self.view)
        self._echte_warten = False
        self.wurzel = self.view.rootObject()
        self._werkzeugleiste()
        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)
        self.takt = QTimer(self)
        self.takt.setInterval(16)
        self.takt.timeout.connect(self._schritt)
        self.takt.start()
        self.wache = QTimer(self)                 # neue Fahrlinie / geänderte Umgebung erkennen
        self.wache.setInterval(500)
        self.wache.timeout.connect(self.aktualisieren)
        self.wache.start()
        self.aktualisieren()

    # ------------------------------------------------------------------ Bedienleiste
    def _werkzeugleiste(self):
        leiste = QToolBar(tr("3D view"), self)
        leiste.setMovable(False)
        self.addToolBar(leiste)
        self.kamera_wahl = QComboBox()
        for k, text in KAMERAS:
            self.kamera_wahl.addItem(tr(text), k)
        self.kamera_wahl.setCurrentIndex(self.kamera_wahl.findData(self.kamera_art))
        self.kamera_wahl.currentIndexChanged.connect(lambda _i: self.kamera_setzen(self.kamera_wahl.currentData()))
        self.kamera_wahl.setToolTip(tr(HILFE_KAMERA))
        leiste.addWidget(QLabel(tr("Camera") + " "))
        leiste.addWidget(self.kamera_wahl)
        self.alles_knopf = QAction(tr("Whole track"), self)
        self.alles_knopf.setShortcut(QKeySequence(Qt.Key_Home))
        self.alles_knopf.setToolTip(tr("Show the whole track from above (Home)"))
        self.alles_knopf.triggered.connect(lambda: self.kamera_setzen("uebersicht"))
        leiste.addAction(self.alles_knopf)
        fokus = QAction(self)
        fokus.setShortcut(QKeySequence(Qt.Key_F))
        fokus.triggered.connect(self.fokussieren)
        self.addAction(fokus)
        self.abspielen = QAction(tr("Play"), self)
        self.abspielen.setShortcut(QKeySequence(Qt.Key_Space))
        self.abspielen.setCheckable(True)
        self.abspielen.toggled.connect(self._abspielen)
        leiste.addAction(self.abspielen)
        self.tempo_wahl = QComboBox()
        for f in TEMPO:
            self.tempo_wahl.addItem("×%s" % ("%g" % f).replace(".", ","), f)
        self.tempo_wahl.setCurrentIndex(TEMPO.index(1.0))
        self.tempo_wahl.currentIndexChanged.connect(lambda _i: setattr(self, "tempo", self.tempo_wahl.currentData()))
        self.tempo_wahl.setToolTip(tr("Playback speed (×1 = speed of the racing line)"))
        leiste.addWidget(self.tempo_wahl)
        self.regler = QSlider(Qt.Horizontal)
        self.regler.setRange(0, 1000)
        self.regler.setMinimumWidth(260)
        self.regler.setToolTip(tr("Position on the lap (station after the start/finish line)"))
        self.regler.sliderMoved.connect(lambda v: self.station_setzen(v / 1000.0 * (self.szene.laenge if self.szene else 0),
                                                                       melden=True))
        leiste.addWidget(self.regler)
        leiste.addSeparator()
        self.tageszeit = QSlider(Qt.Horizontal)
        self.tageszeit.setRange(6, 20)
        self.tageszeit.setValue(14)
        self.tageszeit.setMaximumWidth(110)
        self.tageszeit.setToolTip(tr("Time of day (sun position)"))
        self.tageszeit.valueChanged.connect(self._sonne)
        leiste.addWidget(QLabel(tr("Sun") + " "))
        leiste.addWidget(self.tageszeit)
        knopf = QToolButton()
        knopf.setText(tr("Layers"))
        knopf.setPopupMode(QToolButton.InstantPopup)
        menue = QMenu(knopf)
        for name, text in EBENEN:
            a = menue.addAction(tr(text))
            a.setCheckable(True)
            a.setChecked(self.ebenen[name])
            a.toggled.connect(lambda an, n=name: self._ebene(n, an))
        knopf.setMenu(menue)
        leiste.addWidget(knopf)
        # Spielgrafik und echte Modelle in einem Knopf (27.11.2026, Nutzerwunsch)
        self.spielgrafik_knopf = QAction(tr("Game look"), self)
        self.spielgrafik_knopf.setCheckable(True)
        self.spielgrafik_knopf.setChecked(self.spielgrafik)
        self.spielgrafik_knopf.setToolTip(tr("Shows the track as in the game: real surfaces (asphalt or the chosen track "
                                             "surface, kerbs, grass, gravel), the outer terrain of the carrier with its "
                                             "pit buildings, garages and braking boards, and the game's models for your "
                                             "environment objects including trees, plus your own models. Only read from "
                                             "the carrier package and the F1 25 folder into a local cache (the first "
                                             "time about 15 seconds)."))
        self.spielgrafik_knopf.toggled.connect(self.spielgrafik_setzen)
        leiste.addAction(self.spielgrafik_knopf)
        leiste.addSeparator()
        self.bearbeiten = QAction(tr("Edit"), self)
        self.bearbeiten.setCheckable(True)
        self.bearbeiten.setShortcut(QKeySequence(Qt.Key_Tab))
        self.bearbeiten.setToolTip(tr("Edit in 3D (Tab): click selects an object, drag moves it, R/Shift+R rotates, "
                                      "Del deletes. With a model chosen in the catalogue a click on the ground places "
                                      "it. Dragging empty ground still orbits."))
        self.bearbeiten.toggled.connect(self._bearbeiten)
        leiste.addAction(self.bearbeiten)
        self.ansichten = QComboBox()
        self.ansichten.setToolTip(tr("Saved views"))
        self.ansichten.activated.connect(self._ansicht_waehlen)
        leiste.addWidget(self.ansichten)
        merken = QAction(tr("Save view"), self)
        merken.setToolTip(tr("Remember the current camera view (stored per project on this computer)"))
        merken.triggered.connect(self.ansicht_merken)
        leiste.addAction(merken)
        self._ansichten_fuellen()
        for taste, wert in ((Qt.Key_R, 15.0), (Qt.SHIFT | Qt.Key_R, -15.0)):
            a = QAction(self)
            a.setShortcut(QKeySequence(taste))
            a.triggered.connect(lambda _c=False, w=wert: self._drehen(w))
            self.addAction(a)
        loeschen = QAction(self)
        loeschen.setShortcut(QKeySequence(Qt.Key_Delete))
        loeschen.triggered.connect(self._loeschen)
        self.addAction(loeschen)
        # Kürzel nur, solange die 3D-Ansicht den Fokus hat (eingebettet im Hauptfenster sonst doppelt belegt)
        for a in self.findChildren(QAction):
            if not a.shortcut().isEmpty():
                a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                if a not in self.actions():
                    self.addAction(a)

    def _obj(self, name):
        return self.wurzel.findChild(QObject, name) if self.wurzel is not None else None

    # ------------------------------------------------------------------ Daten
    def aktualisieren(self):
        """Neue Fahrlinie (Netz) oder geänderte Umgebung übernehmen."""
        pfad = getattr(self.haupt, "netz_3d", None)
        stand = _datei_kennung(pfad) if pfad else None
        # neue Datei oder dieselbe Datei neu geschrieben (Netz von der Platte, danach ersetzt vom Fahrlinienlauf)
        if stand is not None and stand != self.szene_stand:
            try:
                self.szene = ansicht3d.Szene(pfad)
            except (OSError, ValueError, KeyError):
                self.szene = None
            self.szene_pfad = pfad
            self.szene_stand = stand
            self._env_schluessel = None
            self._strecke_setzen()
        if self._echte_warten:                    # Katalog für „Load real models“ wurde nachgeladen
            if self._katalog_index():
                self.echte_modelle_laden()
            elif getattr(self.haupt, "env_job", None) is None:
                self._echte_warten = False
                self.status.setText(tr("Real models need the model catalogue, which could not be loaded (see the "
                                       "Environment tab)."))
        self._ladeanzeige_aktualisieren()
        if self.szene is None:
            self.status.setText(tr("Waiting for the racing line: the 3D view uses the latest racing-line run "
                                   "(Tools → Recalculate preview, or switch on automatic racing line)."))
            return
        env = self.haupt.document.env
        schluessel = hashlib.sha1(json.dumps([env.get("objekte"), env.get("gelaende")], sort_keys=True,
                                             default=str).encode()).hexdigest()
        if schluessel != self._env_schluessel:
            self._env_schluessel = schluessel
            self._umgebung_setzen()

    def _geo(self, name, netz):
        m = self._obj(name)
        if m is None:
            return
        g = self.geometrien.get(name)
        if g is None:
            g = self.geometrien[name] = Geometrie()
        g.setzen(netz)
        m.setProperty("geometry", g)

    def _strecke_setzen(self):
        sz = self.szene
        if sz is None:
            return
        env = self.haupt.document.env
        self._spielgrafik_anwenden()
        if self.spielgrafik and self.assets_anfrage != self._assets_signatur():
            self._assets_anfordern()          # neues Netz: Oberflächen und Welt passend dazu
        if self.spielgrafik and not self.ebenen.get("echte"):
            self.echte_modelle_laden()        # gemerkte Spielgrafik: Modelle gleich mit
        self._geo("berge", ansicht3d.berge(sz, env.get("gelaende")))
        einbl = ansicht3d.einblendungen(sz)
        self._tafeln_setzen(einbl.pop("tafeln", {}))
        for name, netz in einbl.items():
            self._geo("e_" + name, netz)
        versatz = self.haupt.document.project.manifest.get("versatz", [0, 0, 0])
        self._geo("e_bereich", ansicht3d.bereich_rahmen(versatz, float(np.median(sz.vertices[:, 1]))))
        for name in self.ebenen:
            self._ebene(name, self.ebenen[name])
        self._sonne(self.tageszeit.value())
        self.station_setzen(self.s)
        if self._erstes_bild:
            self._erstes_bild = False
            self._alles_zeigen()

    def _katalog_index(self):
        return getattr(getattr(self.haupt, "map", None), "env_index", None) or {}

    def _umgebung_setzen(self):
        sz = self.szene
        if sz is None:
            return
        env = self.haupt.document.env
        if self.spielgrafik and self.assets_anfrage is not None and self._assets_signatur() != self.assets_anfrage:
            self._assets_anfordern()          # z. B. Berge geändert: Außengelände neu (Rest aus dem Zwischenspeicher)
        self._boden_setzen()
        self._geo("berge", ansicht3d.berge(sz, env.get("gelaende")))
        objekte = env.get("objekte") or []
        if (self.spielgrafik and self.ebenen.get("echte") and self.modell_job is None and not self._echte_warten
                and not self._im_nachladen and {o["modell"] for o in objekte} - self._echte_versucht):
            self._im_nachladen = True             # neu gesetzte Spielmodelle nachladen (einmal je Änderung)
            try:
                self.echte_modelle_laden()
            finally:
                self._im_nachladen = False
        echte = self._echte_setzen(objekte) if self.ebenen.get("echte") else set()
        netze = ansicht3d.objekt_netze(objekte, self._katalog_index(), self.boden_hoehe, ausgelassen=echte)
        self._geo("quader", netze["quader"])
        self._geo("baeume", netze["baeume"])
        self._auswahl_zeigen()

    # ------------------------------------------------------------------ Stufe 3: echte Modelle
    def _eigene_dateien(self):
        """{Modellkennung: GLB-Pfad} der eigenen Modelle des Projekts."""
        basis = Path(self.haupt.document.project.basis)
        tabelle = basis / "modelle" / "modelle.json"
        erg = {}
        try:
            daten = json.loads(tabelle.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return erg
        for e in daten.get("modelle", []):
            erg[e.get("name")] = basis / "modelle" / e.get("datei", "")
        return erg

    def _echte_setzen(self, objekte) -> set:
        """Eigene Modelle (je Objekt ein ``RuntimeLoader``) und geladene Spielmodelle (je Typ ein zusammengefasstes Netz).
        Rückgabe: Kennungen der so gezeigten Objekte (keine Quader dafür)."""
        gezeigt = set()
        dateien = self._eigene_dateien()
        platz = 0
        for o in objekte:
            name = umgebung.kurzname(o["modell"])
            if not name.startswith("usr_") or name[4:] not in dateien or platz >= EIGENE_PLAETZE:
                continue
            pfad = dateien[name[4:]]
            if not pfad.exists():
                continue
            u = self._obj("u_%d" % platz)
            if u is None:
                break
            y = float(self.boden_hoehe(o["x"], o["z"])) + float(o.get("hoehe_m", 0.0))
            k = float(o.get("skala", 1.0))
            u.setProperty("source", QUrl.fromLocalFile(str(pfad)))
            u.setProperty("position", QVector3D(o["x"], y, o["z"]))
            u.setProperty("eulerRotation", QVector3D(0, float(o.get("drehung_grad", 0.0)), 0))
            u.setProperty("scale", QVector3D(k, k, k))
            u.setProperty("visible", True)
            gezeigt.add(o["id"])
            platz += 1
        for i in range(platz, EIGENE_PLAETZE):
            u = self._obj("u_%d" % i)
            if u is not None:
                u.setProperty("visible", False)
        # Spielmodelle: je Teil ein Platz, alle Objekte des Typs als Instanzen (GPU), Netz nur einmal
        nach_modell = {}
        for o in objekte:
            if o["modell"] in self.spielmodelle:
                nach_modell.setdefault(o["modell"], []).append(o)
        platz = 0
        self._platz_teil = {}
        for modell, liste in sorted(nach_modell.items()):
            daten = self.spielmodelle[modell]
            teile = daten.get("teile") or [{"npz": daten.get("npz"), "png": daten.get("png"), "maske": None}]
            if platz + len(teile) > SPIEL_PLAETZE:
                break
            tabelle = self._instanz_tabelle(modell, liste)
            for teil in teile:
                try:
                    netz = self._teil_netz(teil["npz"])
                except (OSError, KeyError, ValueError, TypeError):
                    continue
                self._geo("s_%d" % platz, netz)
                self._textur("st_%d" % platz, teil.get("png"), spiel=True)
                inst = self._obj("si_%d" % platz)
                if inst is not None:
                    inst.setProperty("source", QUrl.fromLocalFile(str(tabelle)))
                self._platz_teil[platz] = (modell, teil)
                m = self._obj("s_%d" % platz)
                if m is not None:
                    m.setProperty("maske", teil.get("maske") is not None)
                    m.setProperty("schwelle", float(teil.get("maske") or 0.5))
                    m.setProperty("visible", True)
                platz += 1
            gezeigt.update(o["id"] for o in liste)
        for i in range(platz, SPIEL_PLAETZE):
            m = self._obj("s_%d" % i)
            if m is not None:
                m.setProperty("visible", False)
        return gezeigt

    def _teil_netz(self, npz):
        """Netz eines Modellteils (Modellraum), einmal geladen."""
        cache = self.__dict__.setdefault("_teil_netze", {})
        if npz not in cache:
            cache[npz] = ansicht3d.netz_laden(npz)
        return cache[npz]

    def _instanz_tabelle(self, modell, objekte):
        """Instanztabelle (XML für FileInstancing) aller Objekte eines Modells: Lage auf dem Boden, Drehung, Maßstab.
        Dateiname nach Inhalt (FileInstancing lädt nur bei neuer Quelle neu)."""
        text = ansicht3d.instanz_xml(objekte, self.boden_hoehe)
        ordner = ansicht3d.cache_ordner() / "instanzen"
        ordner.mkdir(parents=True, exist_ok=True)
        pfad = ordner / ("%s.xml" % hashlib.sha1((modell + text).encode()).hexdigest()[:20])
        if not pfad.exists():
            pfad.write_text(text, encoding="utf-8")
        return pfad

    def echte_modelle_laden(self):
        """Spielmodelle der Umgebung (ohne Pflanzen, häufigste zuerst, höchstens 40 Typen) aus dem Spielordner holen.
        Ohne geladenen Katalog (Umgebungsreiter nie geöffnet) wird er zuerst geladen, danach geht es weiter."""
        from .adapter import ProcessJob, toolkit_path, worker_command
        index = self._katalog_index()
        objekte = self.haupt.document.env.get("objekte") or []
        if objekte and not index:
            self._echte_warten = True
            if getattr(self.haupt, "env_job", None) is None and hasattr(self.haupt, "load_env_catalog"):
                self.haupt.load_env_catalog()
            self.status.setText(tr("Loading the model catalogue for the real models …"))
            self._ladeanzeige_aktualisieren()
            return
        self._echte_warten = False
        zahl = {}
        # alle betrachteten Modelle merken (auch eigene und unbekannte), sonst lädt _umgebung_setzen sie endlos nach
        self._echte_versucht.update(o["modell"] for o in objekte)
        for o in self.haupt.document.env.get("objekte") or []:
            e = index.get(o["modell"]) or {}
            if e.get("quelle") == "projekt" or not e:        # seit 27.11.2026 auch Bäume, Büsche, Palmen
                continue
            zahl[o["modell"]] = zahl.get(o["modell"], 0) + 1
        ordner = ansicht3d.cache_ordner() / "modelle"
        self.modell_jobs = []
        for modell, _n in sorted(zahl.items(), key=lambda kv: -kv[1])[:SPIEL_TYPEN]:
            ziel = ordner / (hashlib.sha1(modell.encode()).hexdigest()[:16] + ".npz")
            self._echte_versucht.add(modell)
            fertig = ansicht3d.modell_3d_lesen(ziel)
            if fertig is not None:
                self.spielmodelle[modell] = fertig
            else:
                self.modell_jobs.append((modell, ziel))
        self.ebenen["echte"] = True
        try:
            self._toolkit = str(toolkit_path(self.haupt.document.project.manifest.get("werkzeugkasten")))
        except ValueError:
            self._toolkit = None
        self._ProcessJob, self._worker_command = ProcessJob, worker_command
        self._env_schluessel = None
        self._naechstes_modell()

    def _naechstes_modell(self):
        """Alle fehlenden Modelle in einem Arbeitsprozess (``modelle_3d``: Katalog einmal, Quellen parallel)."""
        if self.modell_job is not None:
            return
        if not self.modell_jobs or self._toolkit is None:
            self._env_schluessel = None
            self.aktualisieren()
            if self.spielmodelle:
                self.status.setText(tr("Real models: {anzahl} game models in the cache.", anzahl=len(self.spielmodelle)))
            self._ladeanzeige_aktualisieren()
            return
        auftraege = [{"modell": m, "ziel": str(z)} for m, z in self.modell_jobs]
        self.modell_jobs = []
        self.status.setText(tr("Loading {anzahl} game models …", anzahl=len(auftraege)))
        self.modell_job = self._ProcessJob(self)
        self.modell_job.done.connect(self._modelle_fertig)
        self.modell_job.start(self._worker_command(), {"task": "modelle_3d", "auftraege": auftraege,
                                                       "toolkit": self._toolkit})
        self._ladeanzeige_aktualisieren()

    def _modelle_fertig(self, code, text):
        job, self.modell_job = self.modell_job, None
        if job:
            job.deleteLater()
        fehler = None
        try:
            from .adapter import antwort_lesen
            erg = antwort_lesen(text)
            if code == 0 and "error" not in erg:
                for modell, e in (erg.get("modelle") or {}).items():
                    if "error" not in e:
                        self.spielmodelle[modell] = e
            else:
                fehler = erg.get("error")
        except (ValueError, TypeError):
            fehler = (text or "").strip()[-200:] or "?"
        self._naechstes_modell()
        if fehler:
            self.status.setText(tr("Real models could not be loaded: {fehler}", fehler=fehler))

    # ------------------------------------------------------------------ Spielgrafik (docs/3d_spielassets.md)
    def spielgrafik_setzen(self, an):
        """Echte Oberflächen, Außengelände und die Welt des Trägers an/aus (gemerkt)."""
        self.spielgrafik = bool(an)
        try:
            from .ansicht import settings
            settings().setValue("ansicht3d/spielgrafik", self.spielgrafik)
        except Exception:  # noqa: BLE001  Einstellung ist Beiwerk
            pass
        knopf = getattr(self, "spielgrafik_knopf", None)
        if knopf is not None and knopf.isChecked() != self.spielgrafik:
            knopf.blockSignals(True)          # sonst ruft das Signal diese Methode ein zweites Mal (doppelte Anfrage)
            knopf.setChecked(self.spielgrafik)
            knopf.blockSignals(False)
        if self.spielgrafik and (self.assets is None or self.assets_anfrage != self._assets_signatur()):
            self._assets_anfordern()
        self._spielgrafik_anwenden()
        if self.spielgrafik:
            self.echte_modelle_laden()            # Spielmodelle der Umgebung (auch Bäume) und eigene Modelle
        else:
            self.ebenen["echte"] = False          # wieder einfache Formen
            self._env_schluessel = None
            self.aktualisieren()

    def _assets_eingaben(self):
        """(Anfrage an den Arbeitsprozess, Fehlertext): Träger des Projekts, Netz und Logik des Fahrlinienlaufs."""
        from .adapter import carrier_path, toolkit_path
        doc = self.haupt.document
        man = doc.project.manifest
        try:
            toolkit = toolkit_path(man.get("werkzeugkasten"))
            traeger = carrier_path(man, toolkit)
        except ValueError as fehler:
            return None, str(fehler)
        if not traeger or not Path(traeger).is_file():
            return None, tr("no carrier package (Build → carrier package)")
        welt = ansicht3d.welt_datei(self.szene_pfad)
        return {"task": "spielassets_3d", "toolkit": str(toolkit), "traeger": str(traeger), "szene": str(self.szene_pfad),
                "welt": str(welt) if welt.exists() else None, "umgebung": doc.env,
                "versatz": list(man.get("versatz") or [0, 0, 0]), "bauen": dict(man.get("bauen") or {}),
                "cache": str(ansicht3d.spielassets_ordner())}, None

    def _assets_signatur(self):
        """Was die Spielgrafik bestimmt: Netz, Berge, Spielweltversatz und Baueinstellungen."""
        doc = self.haupt.document
        man = doc.project.manifest
        return json.dumps([str(self.szene_stand), (doc.env.get("gelaende") or {}).get("berge"), man.get("versatz"),
                           man.get("bauen")], sort_keys=True, default=str)

    def _assets_anfordern(self):
        if self.szene is None:
            return
        if self.assets_job is not None:
            self._assets_neu = True
            return
        anfrage, fehler = self._assets_eingaben()
        if anfrage is None:
            self.status.setText(tr("Game look not available: {fehler}", fehler=fehler))
            return
        from .adapter import ProcessJob, worker_command
        signatur = self._assets_signatur()
        self._assets_neu = False
        fertig = ansicht3d.assets_index_lesen(anfrage)      # schon einmal berechnet: ohne Arbeitsprozess
        if fertig is not None:
            self._assets_fertig(signatur, 0, json.dumps(fertig))
            return
        self.status.setText(tr("Preparing the game look from the carrier package … (the first time about 15 "
                               "seconds)"))
        self.assets_job = ProcessJob(self)
        self.assets_job.done.connect(lambda code, text, s=signatur: self._assets_fertig(s, code, text))
        self.assets_job.start(worker_command(), anfrage)
        self._ladeanzeige_aktualisieren()

    def _assets_fertig(self, signatur, code, text):
        job, self.assets_job = self.assets_job, None
        if job:
            job.deleteLater()
        try:
            from .adapter import antwort_lesen
            erg = antwort_lesen(text)
        except (ValueError, TypeError):
            erg = {"error": (text or "").strip()[-300:] or tr("no answer")}
        if code != 0 or "error" in erg:
            self.status.setText(tr("Game look not available: {fehler}", fehler=erg.get("error", "?")))
        else:
            self.assets, self.assets_anfrage = erg, signatur
            self._lochrand = (None, None)
            welt = erg.get("welt") or {}
            self.status.setText(tr("Game look: real surfaces, outer terrain and {modelle} carrier models "
                                   "({instanzen} placed as in the build).", modelle=welt.get("modelle", 0),
                                   instanzen=welt.get("instanzen", 0)))
            if self.spielgrafik:
                self._spielgrafik_anwenden()
        if self._assets_neu or (self.spielgrafik and self.assets_anfrage != self._assets_signatur()):
            self._assets_anfordern()
        self._ladeanzeige_aktualisieren()

    def _ladeanzeige_aktualisieren(self):
        """Was gerade lädt: leere Ansicht (Fahrlinie) groß in der Mitte, Spielgrafik und Modelle klein unten links."""
        anz = getattr(self, "ladeanzeige", None)
        if anz is None:
            return
        if self.szene is None:
            if getattr(self.haupt, "analysis_job", None) is not None or getattr(self.haupt, "auto_analysis", False):
                anz.zeigen(tr("Calculating the racing line …"),
                           tr("The 3D view shows the track as soon as its mesh is ready. This takes up to a minute "
                              "the first time, later the last state appears right away."), mitte=True)
            else:
                anz.zeigen(tr("No track mesh yet"), tr("Run the racing line (Check, or switch on the automatic racing "
                                                       "line) to see the track in 3D."), mitte=True, laeuft=False)
            return
        if self.assets_job is not None:
            anz.zeigen(tr("Preparing the game look …"),
                       tr("Textures, outer terrain and pit buildings from the carrier package (the first time about "
                          "15 seconds)."))
        elif self.modell_job is not None or self._echte_warten:
            anz.zeigen(tr("Loading real models …"), self.status.text())
        else:
            anz.hide()

    def _aktive_assets(self):
        """Ergebnis der Spielgrafik, wenn eingeschaltet und zum aktuellen Netz passend, sonst None."""
        a = self.assets
        if not self.spielgrafik or not a:
            return None
        if json.loads(self.assets_anfrage or "[null]")[0] != str(self.szene_stand):
            return None
        return a

    def _textur(self, name, quelle=None, skala=(1.0, 1.0), farbe=None, spiel=False):
        """Textur ``name`` setzen; ``spiel``: UV wie im Spiel (v nach unten), Qt Quick 3D zählt v nach oben."""
        t = self._obj(name)
        if t is not None:
            t.setProperty("source", QUrl.fromLocalFile(str(quelle)) if quelle else QUrl())
            t.setProperty("scaleU", float(skala[0]))
            t.setProperty("scaleV", float(skala[1]))
            t.setProperty("flipV", bool(spiel))
        m = self._obj(name + "m")
        if m is not None and farbe is not None:
            m.setProperty("baseColor", QColor(farbe) if isinstance(farbe, str) else
                          QColor.fromRgbF(*[min(max(float(c), 0.0), 1.0) for c in farbe[:3]]))

    def _spielgrafik_anwenden(self):
        """Strecke, Boden, Außengelände und Trägerwelt mit oder ohne Spielgrafik setzen."""
        sz = self.szene
        if sz is None:
            return
        a = self._aktive_assets()
        ober = (a or {}).get("oberflaechen") or {}
        standard = sz.klassen_netze()
        for k in ansicht3d.KLASSEN:
            tex, farbe, _rau = KLASSEN_AUSSEHEN.get(k, (None, "#888888", 0.9))
            e = ober.get(k)
            netz = None
            if e:
                try:
                    netz = ansicht3d.netz_laden(e["npz"])
                except (OSError, KeyError, ValueError):
                    netz = None
            if netz is not None:
                self._geo("k_" + k, netz)
                if e.get("png"):
                    self._textur("kt_" + k, e["png"], e.get("uv_faktor") or (1, 1), e.get("tint") or (1, 1, 1),
                                 spiel=True)
                else:
                    self._textur("kt_" + k, None, farbe=farbe)
            else:
                self._geo("k_" + k, standard.get(k))
                self._textur("kt_" + k, self.texturen[tex] if tex else None, farbe=farbe)
        aussen = ((a or {}).get("aussen") or {}).get("netze") or []
        for i in range(AUSSEN_PLAETZE):
            e = aussen[i] if i < len(aussen) else None
            m = self._obj("a_%d" % i)
            if e:
                try:
                    self._geo("a_%d" % i, ansicht3d.netz_laden(e["npz"]))
                    self._textur("at_%d" % i, e.get("png"), farbe=e.get("tint") or (1, 1, 1), spiel=True)
                except (OSError, KeyError, ValueError):
                    e = None
            if m is not None:
                m.setProperty("visible", bool(e) and self.ebenen["aussen"])
        welt = ((a or {}).get("welt") or {}).get("netze") or []
        for i in range(WELT_PLAETZE):
            e = welt[i] if i < len(welt) else None
            m = self._obj("w_%d" % i)
            if e:
                try:
                    self._geo("w_%d" % i, ansicht3d.netz_laden(e["npz"]))
                    self._textur("wt_%d" % i, e.get("png"), spiel=True)
                except (OSError, KeyError, ValueError):
                    e = None
            if m is not None:
                m.setProperty("visible", bool(e) and self.ebenen["welt"])
        self._boden_setzen()
        self._tafeln_sichtbar()

    def _tafeln_setzen(self, tafeln):
        """Entfernungstafeln je Meterzahl in die Plätze ``tf_*`` (Textur mit der Zahl)."""
        self._tafel_anzahl = 0
        for i, (meter, netz) in enumerate(sorted(tafeln.items())[:TAFEL_PLAETZE]):
            self._geo("tf_%d" % i, netz)
            t = self._obj("tft_%d" % i)
            if t is not None:
                t.setProperty("source", QUrl.fromLocalFile(str(ansicht3d.tafel_textur(meter))))
            self._tafel_anzahl = i + 1
        self._tafeln_sichtbar()

    def _tafeln_sichtbar(self):
        """Ersatztafeln nur ohne die echten Tafeln der Spielgrafik (Trägerwelt) zeigen."""
        echt = bool(((self._aktive_assets() or {}).get("welt") or {}).get("netze")) and self.ebenen.get("welt", True)
        an = self.ebenen.get("bremstafeln", True) and not echt
        e = self._obj("e_bremstafeln")
        if e is not None:
            e.setProperty("visible", an)
        for i in range(TAFEL_PLAETZE):
            m = self._obj("tf_%d" % i)
            if m is not None:
                m.setProperty("visible", an and i < getattr(self, "_tafel_anzahl", 0))

    def _boden_setzen(self):
        """Boden: mit Spielgrafik echte Grastextur, mit Außengelände genau im Loch und zum Lochrand hin angeglichen."""
        sz = self.szene
        if sz is None:
            return
        gelaende = self.haupt.document.env.get("gelaende")
        a = self._aktive_assets()
        bod = (a or {}).get("boden") or {}
        aussen = (a or {}).get("aussen") or {}
        mit_aussen = bool(aussen.get("netze")) and self.ebenen["aussen"]
        uv_m = float(bod.get("uv_m") or 8.0)
        loch = rand = None
        if mit_aussen and aussen.get("loch"):
            loch = aussen["loch"]
            if self._lochrand[0] != aussen.get("rand"):
                try:
                    self._lochrand = (aussen.get("rand"), np.load(aussen["rand"])["rand"])
                except (OSError, KeyError, ValueError, TypeError):
                    self._lochrand = (aussen.get("rand"), None)
            rand = self._lochrand[1]
        schluessel = json.dumps([str(self.szene_stand), gelaende, loch, aussen.get("rand") if loch else None, uv_m,
                                 bool(bod.get("png"))], sort_keys=True, default=str)
        if schluessel != self._boden_schluessel:          # nur bei geänderten Eingaben neu rechnen (je 0,3 s)
            b = ansicht3d.boden(sz, gelaende, loch=loch, lochrand=rand, uv_m=uv_m, farbig=not bod.get("png"))
            self.boden_hoehe = ansicht3d.Bodenhoehe(b)
            self._geo("boden", b)
            self._boden_schluessel = schluessel
        if bod.get("png"):
            self._textur("bt", bod["png"], farbe=bod.get("tint") or (1, 1, 1))
        else:
            self._textur("bt", self.texturen["gras"], farbe="#ffffff")
        berge = self._obj("berge")
        if berge is not None:
            berge.setProperty("visible", self.ebenen["berge"] and not mit_aussen)

    # ------------------------------------------------------------------ Ebenen, Sonne
    def _ebene(self, name, an):
        self.ebenen[name] = bool(an)
        ziele = {"umgebung": ["quader"], "baeume": ["baeume"], "berge": ["berge"], "bereich": ["e_bereich"]}
        if name == "echte":
            self._env_schluessel = None
            self.aktualisieren()
            return
        if name == "bremstafeln":
            self._tafeln_sichtbar()
            return
        if name in ("aussen", "welt", "berge"):
            a = self._aktive_assets() or {}
            if name == "welt":
                n = len((a.get("welt") or {}).get("netze") or [])
                for i in range(WELT_PLAETZE):
                    m = self._obj("w_%d" % i)
                    if m is not None:
                        m.setProperty("visible", bool(an) and i < n)
                self._tafeln_sichtbar()
            elif name == "aussen" and (a.get("aussen") or {}).get("netze"):
                n = len(a["aussen"]["netze"])
                for i in range(AUSSEN_PLAETZE):
                    m = self._obj("a_%d" % i)
                    if m is not None:
                        m.setProperty("visible", bool(an) and i < n)
                self._boden_setzen()          # Boden im Loch bzw. wieder mit Rand um die Strecke
            else:
                m = self._obj("berge")
                if m is not None:
                    m.setProperty("visible", bool(an) and not (self.ebenen["aussen"] and (a.get("aussen") or {}).get("netze")))
            return
        for obj in ziele.get(name, ["e_" + name]):
            m = self._obj(obj)
            if m is not None:
                m.setProperty("visible", bool(an))

    def _sonne(self, stunde):
        hoehe = max(4.0, 62.0 * math.sin(math.pi * (float(stunde) - 6.0) / 14.0))
        richtung = -90.0 + (float(stunde) - 6.0) / 14.0 * 180.0
        sonne = self._obj("sonne")
        if sonne is not None:
            sonne.setProperty("eulerRotation", QVector3D(-hoehe, richtung, 0))
            sonne.setProperty("brightness", 0.5 + 0.7 * hoehe / 62.0)
        himmel = self._obj("himmel")
        if himmel is not None:
            himmel.setProperty("sunLatitude", hoehe)
            himmel.setProperty("sunLongitude", richtung)

    # ------------------------------------------------------------------ Kamera und Fahrt
    def kamera_setzen(self, art):
        """Fahrkamera wählen oder Editor-Kamera (``frei``); ``uebersicht`` = Editor-Kamera über der ganzen Strecke."""
        if art == "uebersicht":
            self._editorkamera()
            self._alles_zeigen()
            return
        vorher, self.kamera_art = self.kamera_art, art
        if self.kamera_wahl.currentData() != art:
            self.kamera_wahl.blockSignals(True)
            self.kamera_wahl.setCurrentIndex(self.kamera_wahl.findData(art))
            self.kamera_wahl.blockSignals(False)
        if art == "frei":
            if vorher != "frei" and self._pose is not None:
                self.kam.blick(*self._pose)            # dort weitermachen, wo die Fahrkamera war
            self._kamera_anwenden()
            self.view.setFocus()
            self.status.setText(tr(HILFE_KAMERA))
        self._kamera_folgen()

    def _editorkamera(self):
        """Vor jeder Maus- oder Tastenbewegung: Fahrkamera verlassen, Abspielen anhalten."""
        if self.spielt:
            self.abspielen.setChecked(False)
        if self.kamera_art != "frei":
            self.kamera_setzen("frei")

    def _alles_zeigen(self):
        if self.szene is None:
            return
        v = self.szene.vertices
        self.kam.alles_zeigen(v.min(0), v.max(0))
        self._kamera_anwenden()

    def fokussieren(self):
        """F: auf das gewählte Objekt bzw. das Auto zufliegen."""
        self._editorkamera()
        o = self._objekt(self.auswahl) if self.auswahl else None
        if o is not None and self.boden_hoehe is not None:
            self.kam.anfliegen([o["x"], float(self.boden_hoehe(o["x"], o["z"])) + 2.0, o["z"]], abstand=40.0)
        elif self.szene is not None:
            p, _ = self.szene.auto_lage(self.s)
            self.kam.anfliegen([float(p[0]), float(p[1]) + 1.0, float(p[2])], abstand=25.0)

    def _kamera_anwenden(self):
        cam = self._obj("cam")
        if cam is None:
            return
        if self.boden_hoehe is not None:                   # nicht unter den Boden
            boden = float(self.boden_hoehe(float(self.kam.pos[0]), float(self.kam.pos[2]))) + 0.5
            if self.kam.pos[1] < boden:
                d = boden - self.kam.pos[1]
                self.kam.pos[1] += d
                self.kam.drehpunkt[1] += d
        cam.setProperty("position", QVector3D(*map(float, self.kam.pos)))
        self._nahgrenze(cam, self.kam.pos)
        cam.setProperty("eulerRotation", QVector3D(*map(float, self.kam.euler())))
        self._karte_melden(self.kam.pos, self.kam.ziel())

    def _nahgrenze(self, cam, pos):
        """Nahe Schnittebene mit der Höhe über dem Boden wachsen lassen (2 %, 0,3–10 m): sonst reicht die
        Tiefengenauigkeit aus großer Höhe nicht, Boden und Fahrbahn flackern ineinander."""
        boden = float(self.boden_hoehe(float(pos[0]), float(pos[2]))) if self.boden_hoehe is not None else 0.0
        cam.setProperty("clipNear", ansicht3d.nahgrenze(float(pos[1]) - boden))

    def _kamera_auf(self, pos, ziel):
        cam = self._obj("cam")
        if cam is None:
            return
        self._pose = (np.asarray(pos, float), np.asarray(ziel, float))
        cam.setProperty("position", QVector3D(*map(float, pos)))
        self._nahgrenze(cam, pos)
        cam.lookAt(QVector3D(*map(float, ziel)))
        self._karte_melden(pos, ziel)

    def _kamera_folgen(self):
        if self.szene is None or self.kamera_art == "frei":
            return
        pos, ziel = self.szene.kamera(self.kamera_art, self.s)
        self._kamera_auf(pos, ziel)

    def _karte_melden(self, pos, ziel):
        """Sichtkegel in der 2D-Karte (höchstens 10× je Sekunde)."""
        jetzt = time.time()
        if jetzt - self._letzte_meldung < 0.1:
            return
        self._letzte_meldung = jetzt
        karte = getattr(self.haupt, "map", None)
        if karte is not None:
            karte.kamera_3d = ansicht3d.blick_auf_karte(pos, ziel)
            karte.viewport().update()

    def _abspielen(self, an):
        self.spielt = bool(an)
        self.abspielen.setText(tr("Pause") if an else tr("Play"))
        self._letzte = time.time()
        if an and self.kamera_art == "frei":
            self.kamera_setzen("onboard")

    def station_setzen(self, s, melden=False):
        if self.szene is None:
            return
        self.s = float(s) % max(self.szene.laenge, 1e-6)
        p, gier = self.szene.auto_lage(self.s)
        auto = self._obj("auto")
        if auto is not None:
            auto.setProperty("position", QVector3D(float(p[0]), float(p[1]) + 0.55, float(p[2])))
            auto.setProperty("eulerRotation", QVector3D(0, gier, 0))
        self.regler.blockSignals(True)
        self.regler.setValue(int(round(self.s / self.szene.laenge * 1000)))
        self.regler.blockSignals(False)
        t = self.szene.zeit_bei(self.s)
        self.status.setText(tr("Station {s} m · {kmh} km/h · lap time {zeit} of {runde}", s=int(self.s),
                               kmh=int(round(self.szene.tempo_bei(self.s) * 3.6)),
                               zeit="%d:%04.1f" % (int(t // 60), t % 60),
                               runde="%d:%04.1f" % (int(self.szene.rundenzeit // 60), self.szene.rundenzeit % 60)))
        self._kamera_folgen()
        if melden:
            self.station_geaendert.emit(self.s)

    def _schritt(self):
        jetzt = time.time()
        dt, self._letzte = min(jetzt - self._letzte, 0.1), jetzt
        if self.spielt and self.szene is not None:
            self.station_setzen(self.s + self.szene.tempo_bei(self.s) * self.tempo * dt, melden=False)
            if int(jetzt * 5) != int((jetzt - dt) * 5):          # Hauptfenster 5× je Sekunde mitziehen
                self.station_geaendert.emit(self.s)
        elif self.kamera_art == "frei":
            bewegt = self.kam.schritt(dt)
            if self._tasten:
                vor = sum(t[0] for t in self._tasten.values())
                seit = sum(t[1] for t in self._tasten.values())
                hoch = sum(t[2] for t in self._tasten.values())
                boden = float(self.boden_hoehe(float(self.kam.pos[0]), float(self.kam.pos[2]))) if self.boden_hoehe else 0.0
                schnell = _hat(QApplication.keyboardModifiers(), Qt.ShiftModifier)
                self.kam.bewegen(vor, seit, hoch, dt, self.kam.flugtempo(self.kam.pos[1] - boden, schnell),
                                 fliegen=self._maus_aktion == "blick")
                bewegt = True
            if bewegt:
                self._kamera_anwenden()

    # ------------------------------------------------------------------ Maus und Tastatur
    def eventFilter(self, obj, ev):
        if obj is self.view:
            typ = ev.type()
            if typ in (QEvent.KeyPress, QEvent.KeyRelease) and ev.key() in FLUG_TASTEN and not ev.isAutoRepeat():
                if typ == QEvent.KeyPress and not _hat(ev.modifiers(), Qt.ControlModifier):
                    self._editorkamera()
                    self._tasten[ev.key()] = FLUG_TASTEN[ev.key()]
                else:
                    self._tasten.pop(ev.key(), None)
                return True
            if typ == QEvent.FocusOut:
                self._tasten.clear()
            if typ in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                       QEvent.Wheel):
                self._maus_ereignis(typ, ev)
                return True
        return super().eventFilter(obj, ev)

    def treffer(self, mx, my):
        """Szenenpunkt unter der Maus (``View3D.pick``) oder None."""
        if self.wurzel is None:
            return None
        try:
            erg = QMetaObject.invokeMethod(self.wurzel, "treffer", Q_RETURN_ARG("QVariant"),
                                           Q_ARG("QVariant", float(mx)), Q_ARG("QVariant", float(my)))
        except (RuntimeError, TypeError):
            return None
        if hasattr(erg, "toVariant"):                 # QML-Funktionen liefern einen QJSValue
            erg = erg.toVariant()
        if not isinstance(erg, (list, tuple)) or len(erg) < 4 or not erg[0]:
            return None
        return np.array([float(c) for c in erg[1:4]])

    def _maus_ereignis(self, typ, ev):
        pos = ev.position()
        mx, my = float(pos.x()), float(pos.y())
        if typ == QEvent.MouseMove:
            t = self.treffer(mx, my) if self._treffer_beim_zug else None
        else:
            t = self.treffer(mx, my)
        h = (t is not None,) + (tuple(t) if t is not None else (0.0, 0.0, 0.0))
        if typ == QEvent.MouseButtonPress:
            self._maus_druck(_wert(ev.button()), _wert(ev.modifiers()), mx, my, *h)
        elif typ == QEvent.MouseMove:
            self._maus_zug(mx, my, *h)
        elif typ == QEvent.MouseButtonRelease:
            self._maus_los(_wert(ev.button()), mx, my, *h)
        elif typ == QEvent.MouseButtonDblClick:
            self._maus_doppel(_wert(ev.button()), mx, my, *h)
        else:
            self._maus_rad(ev.angleDelta().y() / 120.0, mx, my, *h)

    def _maus_druck(self, knopf, mods, mx, my, treffer, x, y, z):
        self.view.setFocus(Qt.MouseFocusReason)
        self._maus = (mx, my)
        punkt = np.array([x, y, z], float) if treffer else None
        if knopf == KNOPF_LINKS and self.bearbeiten.isChecked() and not _hat(mods, Qt.AltModifier):
            if treffer and self._objekt_druck(x, z):
                self._maus_aktion = "objekt"
                self._knopf = knopf
                self._treffer_beim_zug = True
                return
        if knopf == KNOPF_RECHTS:
            aktion = "blick"
        elif knopf in (KNOPF_LINKS, KNOPF_MITTE):
            schieben = _hat(mods, Qt.ShiftModifier) or _hat(mods, Qt.ControlModifier)
            aktion = "schieben" if schieben else "kreisen"
        else:
            return
        if self._maus_aktion is not None and self._maus_aktion != "objekt":
            return                                         # zweite Taste während einer Bewegung: erste gilt
        self._editorkamera()
        if punkt is not None and aktion in ("kreisen", "schieben"):
            self.kam.drehpunkt = punkt                    # um den Punkt unter der Maus drehen
        self._maus_aktion, self._knopf = aktion, knopf

    def _maus_zug(self, mx, my, treffer, x, y, z):
        dx, dy = mx - self._maus[0], my - self._maus[1]
        self._maus = (mx, my)
        aktion = self._maus_aktion
        if aktion == "objekt":
            self._zug(treffer, x, y, z)
            return
        if aktion == "kreisen":
            self.kam.kreisen(-dx * 0.3, -dy * 0.3)
        elif aktion == "blick":
            self.kam.blicken(-dx * 0.2, -dy * 0.2)
        elif aktion == "schieben":
            self.kam.schieben(dx, dy, self.view.height())
        else:
            return
        self._kamera_anwenden()

    def _maus_los(self, knopf, mx, my, treffer, x, y, z):
        if self._maus_aktion is None or knopf != self._knopf:
            return
        if self._maus_aktion == "objekt":
            self._los(treffer, x, y, z)
        self._maus_aktion = None
        self._treffer_beim_zug = False

    def _maus_doppel(self, knopf, mx, my, treffer, x, y, z):
        if knopf == KNOPF_LINKS and treffer and self._maus_aktion != "objekt":
            self._editorkamera()
            self.kam.anfliegen([x, y, z])

    def _maus_rad(self, stufen, mx, my, treffer, x, y, z):
        self._editorkamera()
        if self._maus_aktion == "blick":                  # beim Fliegen: Tempo
            self.kam.flugfaktor = max(0.05, min(20.0, self.kam.flugfaktor * 1.25 ** stufen))
            self.status.setText(tr("Flying speed ×{faktor}", faktor=("%.2g" % self.kam.flugfaktor).replace(".", ",")))
            return
        self.kam.zoomen(stufen, [x, y, z] if treffer else None)
        self._kamera_anwenden()

    # ------------------------------------------------------------------ Stufe 4: Bearbeiten
    def _bearbeiten(self, an):
        if not an:
            self.auswahl = None
            self._auswahl_zeigen()
        self.status.setText(tr("Edit: click selects, drag moves, R rotates, Del deletes, a catalogue model is placed "
                               "with a click on the ground.") if an else "")

    def _objekte(self):
        return self.haupt.document.env.get("objekte") or []

    def _objekt_druck(self, x, z) -> bool:
        """Bearbeiten: Objekt unter der Maus wählen oder das Katalogmodell setzen; False = nichts getroffen."""
        if not self.haupt.document.env_editable:
            return False
        kennung = ansicht3d.naechstes_objekt(self._objekte(), x, z)
        modell = self.haupt.env_panel.armed_model() if hasattr(self.haupt, "env_panel") else None
        if kennung is None and modell:
            objekt = self.haupt.document.env_place(modell, x, z)
            self.auswahl = objekt["id"] if objekt else None
        else:
            self.auswahl = kennung
        self.ziehen = None
        if self.auswahl:
            self.haupt.document.env_selected = {self.auswahl}
            o = self._objekt(self.auswahl)
            self.ziehen = (x - o["x"], z - o["z"]) if o else None
        self._auswahl_zeigen()
        return self.auswahl is not None

    def _zug(self, treffer, x, y, z):
        if not treffer or self.ziehen is None or self.auswahl is None:
            return
        m = self._obj("auswahl")
        if m is not None:
            nx, nz = x - self.ziehen[0], z - self.ziehen[1]
            m.setProperty("position", QVector3D(nx, float(self.boden_hoehe(nx, nz)) + 2.0, nz))

    def _los(self, treffer, x, y, z):
        if self.ziehen is None or self.auswahl is None:
            self.ziehen = None
            return
        if treffer:
            nx, nz = x - self.ziehen[0], z - self.ziehen[1]
            o = self._objekt(self.auswahl)
            if o and math.hypot(nx - o["x"], nz - o["z"]) > 0.2:
                self.haupt.document.env_set({self.auswahl}, x=round(nx, 2), z=round(nz, 2))
        self.ziehen = None
        self._env_schluessel = None
        self.aktualisieren()

    def _objekt(self, kennung):
        return next((o for o in self._objekte() if o["id"] == kennung), None)

    def _auswahl_zeigen(self):
        m = self._obj("auswahl")
        if m is None:
            return
        o = self._objekt(self.auswahl) if self.auswahl else None
        if o is None or self.boden_hoehe is None:
            m.setProperty("visible", False)
            return
        e = self._katalog_index().get(o["modell"]) or {}
        lo = np.asarray(e.get("aabb_min") or [-1.5, 0, -1.5], float)
        hi = np.asarray(e.get("aabb_max") or [1.5, 3, 1.5], float)
        groesse = np.maximum(hi - lo, 1.0) * float(o.get("skala", 1.0)) + 0.6
        y = float(self.boden_hoehe(o["x"], o["z"])) + groesse[1] / 2
        m.setProperty("position", QVector3D(o["x"], y, o["z"]))
        m.setProperty("scale", QVector3D(*(groesse / 100.0)))
        m.setProperty("eulerRotation", QVector3D(0, float(o.get("drehung_grad", 0.0)), 0))
        m.setProperty("visible", True)

    def _drehen(self, grad):
        if not self.bearbeiten.isChecked() or not self.auswahl:
            return
        o = self._objekt(self.auswahl)
        if o:
            self.haupt.document.env_rotate({o["id"]: (float(o.get("drehung_grad", 0.0)) + grad) % 360.0}, tr("Rotate"))
            self._env_schluessel = None
            self.aktualisieren()

    def _loeschen(self):
        if not self.bearbeiten.isChecked() or not self.auswahl:
            return
        self.haupt.document.env_selected = {self.auswahl}
        self.haupt.document.env_delete()
        self.auswahl = None
        self._env_schluessel = None
        self.aktualisieren()

    # ------------------------------------------------------------------ Ansichten merken (wie die Kamera-Palette)
    def _ansichten_schluessel(self):
        return "ansicht3d/" + hashlib.sha1(str(self.haupt.document.project.basis).encode()).hexdigest()[:12]

    def _ansichten(self):
        from .ansicht import settings
        try:
            return json.loads(settings().value(self._ansichten_schluessel(), "[]"))
        except (ValueError, TypeError):
            return []

    def _ansichten_fuellen(self):
        self.ansichten.clear()
        self.ansichten.addItem(tr("Views …"), None)
        for v in self._ansichten():
            self.ansichten.addItem(v["name"], v)

    def ansicht_merken(self, name=None):
        cam = self._obj("cam")
        if cam is None:
            return
        if name is None:
            name, ok = QInputDialog.getText(self, tr("Save view"), tr("Name of the view"))
            if not ok or not name.strip():
                return
        if self.kamera_art != "frei":
            self.kamera_setzen("frei")
        liste = [v for v in self._ansichten() if v["name"] != name.strip()]
        liste.append({"name": name.strip(), "pos": [float(c) for c in self.kam.pos],
                      "rot": [float(c) for c in self.kam.euler()]})
        from .ansicht import settings
        settings().setValue(self._ansichten_schluessel(), json.dumps(liste))
        self._ansichten_fuellen()

    def _ansicht_waehlen(self, i):
        v = self.ansichten.itemData(i)
        cam = self._obj("cam")
        if not v or cam is None:
            return
        self.kamera_setzen("frei")
        self.kam.winkel(v["pos"], v["rot"][0], v["rot"][1])
        self._kamera_anwenden()

    def _anhalten(self):
        """Ausgeblendet oder geschlossen: Takt und Wache aus, Sichtkegel aus der Karte nehmen."""
        self.takt.stop()
        self.wache.stop()
        self._tasten.clear()
        karte = getattr(self.haupt, "map", None)
        if karte is not None:
            karte.kamera_3d = None
            karte.viewport().update()

    def showEvent(self, event):
        super().showEvent(event)
        self._letzte = time.time()
        self.takt.start()
        self.wache.start()
        self.aktualisieren()

    def hideEvent(self, event):
        self._anhalten()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._anhalten()
        super().closeEvent(event)
