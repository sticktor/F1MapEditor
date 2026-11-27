"""3D-Ansicht (21.11.2026, docs/3d_ansicht.md): Daten für das zweite Fenster, ohne Qt.

Aus dem Ergebnis eines Fahrlinienlaufs (``szene_laden``: Generatornetz und Logik als ``.npz``), ``umgebung.json`` und dem
Katalog entstehen Dreiecksnetze für Qt Quick 3D: Strecke je Oberflächenklasse, Boden mit Hügeln und Bergen, Objekte als
Quader bzw. einfache Baumformen (zu je einem Netz zusammengefasst), Einblendungen (Ideallinie nach Tempo, Bremstafeln,
DRS-Zonen, Sektoren, Startplätze, Streckenbereich) und die Kamerapfade (Onboard, Verfolger, TV). Koordinaten: Generator =
Definition (x rechts, y oben, z in der Draufsicht nach unten), wie die Objekte in ``umgebung.json``.

Ein Netz ist ein dict ``{"pos": (n,3) float32, "nor": (n,3), "uv": (n,2) oder None, "farbe": (n,4) oder None,
"idx": (m,) uint32}``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

from . import umgebung

#: Oberflächenklassen in der Reihenfolge des Generators (strecke/definition.OBERFLAECHEN)
KLASSEN = ["asphalt", "randstein", "gras", "kies", "mauer", "boxengasse", "linie", "auslauf", "startbox", "gebaeude",
           "wulst", "randstein_anfang", "randstein_ende", "randstein_tief", "kunstrasen"]
#: Farben je Klasse ohne Textur (sRGB 0–1)
KLASSEN_FARBEN = {"asphalt": (0.24, 0.24, 0.26), "randstein": (0.80, 0.20, 0.16), "gras": (0.31, 0.54, 0.24),
                  "kies": (0.78, 0.71, 0.54), "mauer": (0.80, 0.80, 0.80), "boxengasse": (0.33, 0.33, 0.36),
                  "linie": (0.95, 0.95, 0.95), "auslauf": (0.29, 0.29, 0.31), "startbox": (1.0, 1.0, 1.0),
                  "gebaeude": (0.63, 0.63, 0.66)}
#: Farben der Objektkategorien (wie die Karte, umgebung.KATEGORIE_FARBEN)
KATEGORIE_RGB = {k: tuple(int(v.lstrip("#")[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
                 for k, v in umgebung.KATEGORIE_FARBEN.items()}
PFLANZEN = ("baum", "busch", "palme")
#: Augenhöhe im Auto und Verfolgerkamera (m)
AUGE_M, VERFOLGER_HINTEN_M, VERFOLGER_HOCH_M = 1.0, 9.0, 3.5
#: Boden: Raster und Rand um die Strecke (m)
BODEN_RASTER_M, BODEN_RAND_M = 8.0, 600.0
#: Oberflächen, die höher als der Boden ragen dürfen (zählen nicht für die Bodenhöhe)
BODEN_OHNE = ("mauer", "gebaeude", "wulst")
#: Abstand des Bodens unter der Fahrbahn (m)
BODEN_UNTER_M = 0.25


def cache_ordner() -> Path:
    basis = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Mapeditor" / "ansicht3d"
    basis.mkdir(parents=True, exist_ok=True)
    return basis


def netz_datei(schluessel: str) -> Path:
    """Ablage des Netzes eines Fahrlinienlaufs (je Definitionsstand, ``check_key``)."""
    return cache_ordner() / ("netz_" + hashlib.sha1(str(schluessel).encode()).hexdigest()[:16] + ".npz")


# ------------------------------------------------------------------ Laden
class Szene:
    """Generatornetz und Logik eines Fahrlinienlaufs."""

    def __init__(self, pfad):
        d = np.load(pfad, allow_pickle=False)
        self.vertices = d["vertices"].astype(np.float32)
        self.normalen = d["normalen"].astype(np.float32)
        self.uv = d["uv"].astype(np.float32)
        self.dreiecke = d["dreiecke"].astype(np.uint32)
        self.oberflaeche = d["oberflaeche"].astype(np.int32)
        self.namen = [str(n) for n in d["namen"]]
        self.logik = json.loads(str(d["logik"]))
        ideal = self.logik.get("ideallinie") or {}
        self.linie = np.asarray(ideal.get("position") or [], float).reshape(-1, 3)
        self.tempo = np.asarray(ideal.get("geschwindigkeit_m_s") or [], float)
        if len(self.tempo) != len(self.linie):
            self.tempo = np.full(len(self.linie), 50.0)
        seg = np.linalg.norm(np.diff(np.vstack([self.linie, self.linie[:1]]), axis=0), axis=1) if len(self.linie) else []
        self.s = np.r_[0.0, np.cumsum(seg)[:-1]] if len(self.linie) else np.zeros(0)
        self.laenge = float(np.sum(seg)) if len(self.linie) else 0.0
        # Zeit je Station (Runde mit dem Tempo der Ideallinie)
        if len(self.linie):
            dt = np.asarray(seg) / np.maximum(self.tempo, 1.0)
            self.t = np.r_[0.0, np.cumsum(dt)[:-1]]
            self.rundenzeit = float(np.sum(dt))
        else:
            self.t, self.rundenzeit = np.zeros(0), 0.0

    # ------------------------------------------------------------------ Strecke
    def klassen_netze(self) -> dict:
        """{Klasse: Netz} für jede Oberflächenklasse mit Dreiecken (eigene Vertices je Klasse)."""
        erg = {}
        for i, name in enumerate(self.namen):
            m = self.oberflaeche == i
            if not m.any():
                continue
            tri = self.dreiecke[m]
            idx, inv = np.unique(tri.ravel(), return_inverse=True)
            erg[name] = {"pos": self.vertices[idx], "nor": self.normalen[idx], "uv": self.uv[idx], "farbe": None,
                         "idx": inv.astype(np.uint32)}
        return erg

    def hoehe_nahe(self, punkte_xz: np.ndarray, schritt: int = 3) -> tuple:
        """(Höhe des nächsten Streckenvertex, Abstand) je Punkt (x, z); grob, für den Boden."""
        v = self.vertices[::schritt]
        erg_h = np.empty(len(punkte_xz))
        erg_d = np.empty(len(punkte_xz))
        for a in range(0, len(punkte_xz), 512):
            teil = punkte_xz[a:a + 512]
            d2 = (teil[:, None, 0] - v[None, :, 0]) ** 2 + (teil[:, None, 1] - v[None, :, 2]) ** 2
            i = d2.argmin(1)
            erg_h[a:a + 512] = v[i, 1]
            erg_d[a:a + 512] = np.sqrt(d2[np.arange(len(teil)), i])
        return erg_h, erg_d

    # ------------------------------------------------------------------ Kamera
    def station_index(self, s: float) -> int:
        if not len(self.s):
            return 0
        return int(np.clip(np.searchsorted(self.s, float(s) % max(self.laenge, 1e-6), side="right") - 1, 0, len(self.s) - 1))

    def punkt(self, s: float) -> np.ndarray:
        """Punkt der Ideallinie bei Station ``s`` (linear zwischen den Stützpunkten)."""
        if not len(self.linie):
            return np.zeros(3)
        s = float(s) % self.laenge
        i = self.station_index(s)
        j = (i + 1) % len(self.linie)
        seg = (self.s[j] if j else self.laenge) - self.s[i]
        a = 0.0 if seg <= 1e-9 else (s - self.s[i]) / seg
        return self.linie[i] * (1 - a) + self.linie[j] * a

    def station_nach_zeit(self, t: float) -> float:
        """Station zur Rundenzeit ``t`` (mit dem Tempo der Ideallinie)."""
        if not len(self.t) or self.rundenzeit <= 0:
            return 0.0
        t = float(t) % self.rundenzeit
        return float(np.interp(t, np.r_[self.t, self.rundenzeit], np.r_[self.s, self.laenge]))

    def zeit_bei(self, s: float) -> float:
        if not len(self.t):
            return 0.0
        return float(np.interp(float(s) % self.laenge, np.r_[self.s, self.laenge], np.r_[self.t, self.rundenzeit]))

    def richtung_bei(self, p) -> np.ndarray:
        """Waagrechte Fahrtrichtung (Einheitsvektor) der Ideallinie am nächstgelegenen Punkt zu ``p``."""
        if len(self.linie) < 3:
            return np.array([0.0, 0.0, 1.0])
        p = np.asarray(p, float)
        i = int(np.argmin((self.linie[:, 0] - p[0]) ** 2 + (self.linie[:, 2] - p[2]) ** 2))
        d = self.linie[(i + 1) % len(self.linie)] - self.linie[i - 1]
        d[1] = 0.0
        n = float(np.linalg.norm(d))
        return d / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])

    def tempo_bei(self, s: float) -> float:
        return float(self.tempo[self.station_index(s)]) if len(self.tempo) else 0.0

    def kamera(self, art: str, s: float) -> tuple:
        """(Position, Blickziel) für ``onboard`` (Augenhöhe, Blick 20 m voraus), ``verfolger`` (hinten, oben) und ``tv``
        (fester Punkt neben der Strecke je 250-m-Abschnitt, Blick aufs Auto)."""
        p = self.punkt(s)
        vor = self.punkt(s + 20.0)
        hinten = self.punkt(s - VERFOLGER_HINTEN_M)
        if art == "onboard":
            return p + [0, AUGE_M, 0], vor + [0, AUGE_M * 0.8, 0]
        if art == "verfolger":
            return hinten + [0, VERFOLGER_HOCH_M, 0], p + [0, 1.0, 0]
        # TV: Kamera am Anfang des 250-m-Abschnitts, 35 m nach außen, 8 m hoch
        abschnitt = math.floor(float(s) / 250.0) * 250.0 + 125.0
        q = self.punkt(abschnitt)
        f = self.punkt(abschnitt + 5.0) - self.punkt(abschnitt - 5.0)
        f[1] = 0.0
        f /= max(float(np.linalg.norm(f)), 1e-6)
        rechts = np.array([-f[2], 0.0, f[0]])
        return q + rechts * 35.0 + [0, 8.0, 0], p + [0, 1.0, 0]

    def auto_lage(self, s: float) -> tuple:
        """(Position, Gierwinkel in Grad für Qt Quick 3D) des Autos auf der Ideallinie."""
        p = self.punkt(s)
        f = self.punkt(s + 2.0) - self.punkt(s - 2.0)
        return p, math.degrees(math.atan2(f[0], f[2]))


def szene_speichern(pfad, erg) -> str:
    """Im Arbeitsprozess: Netz und Logik eines ``erzeugung.berechnen``-Ergebnisses für die 3D-Ansicht ablegen."""
    netz, logik = erg["netz"], erg["logik"]
    teil = {k: logik.get(k) for k in ("ideallinie", "bremsmarker", "drs", "x_mode", "sektoren", "startplaetze",
                                       "boxenplaetze", "start_ziel", "laenge_m", "rundenzeit_s", "kurven")}
    namen = list(netz.get("oberflaechen_namen") or KLASSEN)
    arrays = dict(vertices=np.asarray(netz["vertices"], np.float32), normalen=np.asarray(netz["normalen"], np.float32),
                  uv=np.asarray(netz["uv"], np.float32), dreiecke=np.asarray(netz["dreiecke"], np.uint32),
                  oberflaeche=np.asarray(netz["oberflaeche"], np.uint8), namen=np.asarray(namen),
                  logik=np.asarray(json.dumps(teil, default=_json_wert)))
    pruef = hashlib.sha1()
    for k in sorted(arrays):
        pruef.update(k.encode())
        pruef.update(np.ascontiguousarray(arrays[k]).tobytes())
    pruefsumme = pruef.hexdigest()
    # gleicher Inhalt schon da (Netz von der Platte gezeigt, Lauf rechnet denselben Stand): nicht neu schreiben, sonst
    # lädt die Ansicht neu und die Spielgrafik verliert ihren Zwischenspeicher (3d_ladeperformance.md)
    if _pruefsumme(pfad) == pruefsumme and welt_datei(pfad).is_file():
        return str(pfad)
    tmp = str(pfad) + ".tmp.npz"
    np.savez_compressed(tmp, pruefsumme=np.asarray(pruefsumme), **arrays)
    welt_speichern(welt_datei(pfad), erg)
    os.replace(tmp, pfad)
    alte = sorted(Path(pfad).parent.glob("netz_*.npz"), key=lambda q: q.stat().st_mtime, reverse=True)
    for q in alte[12:]:                 # Zwischenspeicher klein halten
        for datei in (q, welt_datei(q)):
            try:
                datei.unlink()
            except OSError:
                pass
    return str(pfad)


def _pruefsumme(pfad):
    """Prüfsumme des Inhalts eines gespeicherten Netzes (ältere Dateien: None)."""
    try:
        with np.load(pfad, allow_pickle=False) as d:
            return str(d["pruefsumme"]) if "pruefsumme" in d.files else None
    except (OSError, ValueError, KeyError):
        return None


def welt_datei(netz_pfad) -> Path:
    """Logik (Ausgabeachsen) und aufgelöste Definition zum Netz, für die Spiel-Assets (Bauschritte der Welt)."""
    return Path(str(netz_pfad)[:-4] + ".welt.json")


def welt_speichern(pfad, erg) -> None:
    """Im Arbeitsprozess: ``{"logik", "param"}`` wie der Bau sie liest (``strecke_logik.json``,
    ``definition_aufgeloest.json``). Ohne Werkzeugkasten-Funktion entfällt die Datei (Spiel-Assets ohne Welt)."""
    try:
        defn = erg["definition"]
        param = defn.param
        try:
            from strecke import erzeugung
            logik = erzeugung._logik_achsen(erg["logik"], param["ausgabe"]["achsen"])
        except (ImportError, AttributeError, KeyError):
            logik = erg["logik"]
        tmp = str(pfad) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"logik": logik, "param": param}, f, default=_json_wert, allow_nan=False)
        os.replace(tmp, pfad)
    except (KeyError, TypeError, ValueError, OSError, AttributeError):
        try:
            Path(pfad).unlink()
        except OSError:
            pass


def _json_wert(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    raise TypeError(type(v))


# ------------------------------------------------------------------ Grundformen
def _leer():
    return {"pos": np.zeros((0, 3), np.float32), "nor": np.zeros((0, 3), np.float32), "uv": None, "farbe": None,
            "idx": np.zeros(0, np.uint32)}


def zusammenfuegen(netze) -> dict:
    """Mehrere Netze zu einem (Farbe Pflicht oder für alle fehlend)."""
    netze = [n for n in netze if len(n["idx"])]
    if not netze:
        return _leer()
    pos, nor, farbe, uv, idx, basis = [], [], [], [], [], 0
    for n in netze:
        pos.append(n["pos"])
        nor.append(n["nor"])
        farbe.append(n["farbe"] if n["farbe"] is not None else np.ones((len(n["pos"]), 4), np.float32))
        uv.append(n["uv"] if n["uv"] is not None else np.zeros((len(n["pos"]), 2), np.float32))
        idx.append(n["idx"] + basis)
        basis += len(n["pos"])
    return {"pos": np.concatenate(pos).astype(np.float32), "nor": np.concatenate(nor).astype(np.float32),
            "uv": np.concatenate(uv).astype(np.float32), "farbe": np.concatenate(farbe).astype(np.float32),
            "idx": np.concatenate(idx).astype(np.uint32)}


_QUADER_FLAECHEN = [((0, 1, 0), [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)]),
                    ((0, -1, 0), [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)]),
                    ((1, 0, 0), [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)]),
                    ((-1, 0, 0), [(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)]),
                    ((0, 0, 1), [(0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1)]),
                    ((0, 0, -1), [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])]


def _einheitsquader():
    pos, nor, idx = [], [], []
    for n, ecken in _QUADER_FLAECHEN:
        b = len(pos)
        pos += ecken
        nor += [n] * 4
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return np.asarray(pos, np.float32), np.asarray(nor, np.float32), np.asarray(idx, np.uint32)


def _kegel(segmente=8):
    """Kegel (Spitze oben) und Zylinder-Stamm: Baum als Einheitsform (Radius 1, Höhe 1)."""
    w = np.linspace(0, 2 * math.pi, segmente, endpoint=False)
    pos, nor, idx = [], [], []
    for k in range(segmente):          # Krone 0.25 … 1.0
        a, b = w[k], w[(k + 1) % segmente]
        p = [(math.cos(a), 0.25, math.sin(a)), (math.cos(b), 0.25, math.sin(b)), (0.0, 1.0, 0.0)]
        n = np.cross(np.subtract(p[1], p[0]), np.subtract(p[2], p[0]))
        n = -n / max(np.linalg.norm(n), 1e-9)
        base = len(pos)
        pos += p
        nor += [tuple(n)] * 3
        idx += [base, base + 2, base + 1]
    stamm = len(pos)                   # Stamm als schmaler Quader
    qp, qn, qi = _einheitsquader()
    qp = (qp - [0.5, 0.0, 0.5]) * [0.15, 0.25, 0.15]
    pos += [tuple(p) for p in qp]
    nor += [tuple(n) for n in qn]
    idx += [stamm + i for i in qi]
    return np.asarray(pos, np.float32), np.asarray(nor, np.float32), np.asarray(idx, np.uint32), len(qp)


def _transformiert(pos, nor, x, y, z, drehung_grad, skala_xyz):
    """Zeilenvektor-Konvention wie ``umgebung.lagematrix``: lokales x' = lx·cos + lz·sin, z' = −lx·sin + lz·cos."""
    w = math.radians(drehung_grad)
    c, s = math.cos(w), math.sin(w)
    p = pos * np.asarray(skala_xyz, np.float32)
    px = p[:, 0] * c + p[:, 2] * s
    pz = -p[:, 0] * s + p[:, 2] * c
    nx = nor[:, 0] * c + nor[:, 2] * s
    nz = -nor[:, 0] * s + nor[:, 2] * c
    return (np.column_stack([px + x, p[:, 1] + y, pz + z]).astype(np.float32),
            np.column_stack([nx, nor[:, 1], nz]).astype(np.float32))


# ------------------------------------------------------------------ Boden
def boden(szene: Szene, gelaende: dict | None = None, raster_m: float = BODEN_RASTER_M, rand_m: float = BODEN_RAND_M,
          loch=None, lochrand=None, uv_m: float = 8.0, farbig: bool = True):
    """Höhenfeld um die Strecke: nahe der Strecke deren Höhe, weiter weg flach auf die mittlere Streckenhöhe, dazu die
    Hügel aus ``umgebung.json`` (wie ``spiel_geometrie.huegel_anwenden``, an der Strecke ausgeblendet). Der Boden liegt
    überall mindestens ``BODEN_UNTER_M`` unter den flachen Oberflächen der Strecke (ohne Mauern und Gebäude), sonst
    schaut er an Hängen und in Steilkurven als Grasfleck durch die Fahrbahn. Rückgabe: Netz mit Farbe.

    Mit Außengelände (Spielgrafik): ``loch`` = (x0, z0, Kante) des Lochs, der Boden füllt genau das Loch (und die
    Strecke) und geht zum Rand hin in die Höhe des Lochrands ``lochrand`` (n, 3) über, wie die Grundfläche des Baus.
    ``uv_m`` = Meter je Texturkachel, ``farbig=False`` ohne Grüntönung (echte Grastextur)."""
    ohne = [i for i, n in enumerate(szene.namen) if n in BODEN_OHNE]
    flach = ~np.isin(szene.oberflaeche, ohne)
    v = szene.vertices[np.unique(szene.dreiecke[flach].ravel())] if flach.any() else szene.vertices
    if loch is not None:
        lx, lz, lk = (float(w) for w in loch)
        x0, x1 = min(lx, v[:, 0].min() - 40.0), max(lx + lk, v[:, 0].max() + 40.0)
        z0, z1 = min(lz, v[:, 2].min() - 40.0), max(lz + lk, v[:, 2].max() + 40.0)
    else:
        x0, x1 = v[:, 0].min() - rand_m, v[:, 0].max() + rand_m
        z0, z1 = v[:, 2].min() - rand_m, v[:, 2].max() + rand_m
    gx = np.arange(x0, x1 + raster_m, raster_m)
    gz = np.arange(z0, z1 + raster_m, raster_m)
    nx, nz = len(gx), len(gz)
    X, Z = np.meshgrid(gx, gz)
    pts = np.column_stack([X.ravel(), Z.ravel()])
    # Streckenhöhe je Zelle (Mittel der Vertices), dann schrittweise nach außen fortsetzen (Abstand in Zellen)
    ix = np.clip(np.round((v[:, 0] - x0) / raster_m).astype(int), 0, nx - 1)
    iz = np.clip(np.round((v[:, 2] - z0) / raster_m).astype(int), 0, nz - 1)
    summe = np.zeros((nz, nx))
    zahl = np.zeros((nz, nx))
    np.add.at(summe, (iz, ix), v[:, 1])
    np.add.at(zahl, (iz, ix), 1)
    bekannt = zahl > 0
    H0 = np.where(bekannt, summe / np.maximum(zahl, 1), 0.0)
    D = np.where(bekannt, 0.0, np.inf)
    for k in range(1, int(260.0 / raster_m) + 2):
        neu_h, neu_ok = H0.copy(), bekannt.copy()
        for dz, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(np.roll(H0, dz, 0), dx, 1)
            sk = np.roll(np.roll(bekannt, dz, 0), dx, 1)
            setzen = sk & ~neu_ok
            neu_h[setzen] = sh[setzen]
            neu_ok |= setzen
        D[neu_ok & ~bekannt] = k * raster_m
        H0, bekannt = neu_h, neu_ok
    h_nah = H0.ravel()
    d = np.where(np.isfinite(D), D, 1e9).ravel()
    grund = float(np.median(v[:, 1]))
    blend = np.clip((d - 40.0) / 160.0, 0, 1)
    h = h_nah * (1 - blend) + grund * blend
    for f in (gelaende or {}).get("huegel", []):
        r = max(float(f["radius_m"]), 1.0)
        dd = np.hypot(pts[:, 0] - f["x"], pts[:, 1] - f["z"])
        innen = dd < r
        maske = np.clip((d - 30.0) / 40.0, 0, 1)
        h[innen] += float(f["hoehe_m"]) * 0.5 * (1 + np.cos(np.pi * dd[innen] / r)) * maske[innen]
    if loch is not None and lochrand is not None and len(lochrand):
        h = _an_lochrand(h, pts, loch, np.asarray(lochrand, float))
    # Unter die Fahrbahn drücken: jede Ecke der Zelle eines Fahrbahnpunkts so weit senken, dass auch die höchste
    # Ecke darunter liegt (gilt für jede Teilung der Zelle in Dreiecke). Punkte: Vertices, Kanten- und Dreiecksmitten
    # der flachen Oberflächen (26.11.2026: breite Fahrbahndreiecke ließen Grasflecken in der Mitte durch), dazu jeder
    # Bodenpunkt, der unter einem Fahrbahndreieck liegt, auf dessen Höhe
    H = h.reshape(nz, nx).copy()
    tri = szene.dreiecke[flach]
    ecke = szene.vertices[tri].astype(np.float64)
    proben = np.concatenate([v, ecke.mean(1), (ecke[:, 0] + ecke[:, 1]) / 2, (ecke[:, 1] + ecke[:, 2]) / 2,
                             (ecke[:, 2] + ecke[:, 0]) / 2])
    gx_p, gz_p, gh_p = _unter_dreiecken(ecke, x0, z0, raster_m, nx, nz)
    if len(gx_p):
        deckel = np.full(H.shape, np.inf)
        np.minimum.at(deckel, (gz_p, gx_p), gh_p - BODEN_UNTER_M)
        H = np.minimum(H, deckel)
    fx = np.clip((proben[:, 0] - x0) / raster_m, 0, nx - 1.001)
    fz = np.clip((proben[:, 2] - z0) / raster_m, 0, nz - 1.001)
    i0, j0 = fz.astype(int), fx.astype(int)
    for _ in range(2):
        ecken = np.maximum.reduce([H[i0, j0], H[i0, j0 + 1], H[i0 + 1, j0], H[i0 + 1, j0 + 1]])
        grenze = proben[:, 1] - BODEN_UNTER_M
        zu_hoch = ecken > grenze
        if not zu_hoch.any():
            break
        deckel = np.full(H.shape, np.inf)
        for di, dj in ((0, 0), (0, 1), (1, 0), (1, 1)):
            np.minimum.at(deckel, (i0[zu_hoch] + di, j0[zu_hoch] + dj), grenze[zu_hoch])
        H = np.minimum(H, deckel)
    h = H.ravel()
    pos = np.column_stack([pts[:, 0], h, pts[:, 1]]).astype(np.float32)
    gz_, gx_ = np.gradient(H, raster_m)
    nor = np.column_stack([-gx_.ravel(), np.ones(nx * nz), -gz_.ravel()])
    nor /= np.linalg.norm(nor, axis=1)[:, None]
    steil = np.clip(1.0 - nor[:, 1], 0, 0.4) / 0.4
    if farbig:
        farbe = np.column_stack([0.30 + 0.12 * steil, 0.52 - 0.12 * steil, 0.22 + 0.04 * steil, np.ones(len(pos))])
    else:                                        # echte Textur: nur steile Hänge etwas erdiger
        farbe = np.column_stack([1.0 + 0.05 * steil, 1.0 - 0.15 * steil, 1.0 - 0.2 * steil, np.ones(len(pos))])
    i = np.arange(nx * nz).reshape(nz, nx)
    a, b, c, e = i[:-1, :-1].ravel(), i[:-1, 1:].ravel(), i[1:, 1:].ravel(), i[1:, :-1].ravel()
    idx = np.column_stack([a, e, c, a, c, b]).ravel().astype(np.uint32)
    uv = np.column_stack([pts[:, 0] / uv_m, pts[:, 1] / uv_m]).astype(np.float32)
    return {"pos": pos, "nor": nor.astype(np.float32), "uv": uv, "farbe": farbe.astype(np.float32), "idx": idx}


def _unter_dreiecken(ecke, x0, z0, raster_m, nx, nz):
    """Bodenpunkte (Spalte, Zeile) unter Dreiecken ``ecke`` (n, 3, 3) samt Höhe des Dreiecks dort (Draufsicht)."""
    ex, ez = (ecke[:, :, 0] - x0) / raster_m, (ecke[:, :, 2] - z0) / raster_m
    j0 = np.maximum(np.ceil(ex.min(1)).astype(np.int64), 0)
    j1 = np.minimum(np.floor(ex.max(1)).astype(np.int64), nx - 1)
    i0 = np.maximum(np.ceil(ez.min(1)).astype(np.int64), 0)
    i1 = np.minimum(np.floor(ez.max(1)).astype(np.int64), nz - 1)
    breite, hoehe = j1 - j0 + 1, i1 - i0 + 1
    ok = (breite > 0) & (hoehe > 0)
    if not ok.any():
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)
    t = np.flatnonzero(ok)
    anzahl = (breite * hoehe)[t]
    welche = np.repeat(t, anzahl)
    lauf = np.arange(int(anzahl.sum())) - np.repeat(np.cumsum(anzahl) - anzahl, anzahl)
    gx = j0[welche] + lauf % breite[welche]
    gz = i0[welche] + lauf // breite[welche]
    x, z = ex[welche], ez[welche]
    d = (z[:, 1] - z[:, 2]) * (x[:, 0] - x[:, 2]) + (x[:, 2] - x[:, 1]) * (z[:, 0] - z[:, 2])
    gut = np.abs(d) > 1e-12
    d = np.where(gut, d, 1.0)
    l0 = ((z[:, 1] - z[:, 2]) * (gx - x[:, 2]) + (x[:, 2] - x[:, 1]) * (gz - z[:, 2])) / d
    l1 = ((z[:, 2] - z[:, 0]) * (gx - x[:, 2]) + (x[:, 0] - x[:, 2]) * (gz - z[:, 2])) / d
    l2 = 1.0 - l0 - l1
    m = gut & (l0 >= -1e-6) & (l1 >= -1e-6) & (l2 >= -1e-6)
    y = ecke[welche, :, 1]
    return gx[m], gz[m], (l0 * y[:, 0] + l1 * y[:, 1] + l2 * y[:, 2])[m]


#: Übergang des Bodens in die Höhe des Lochrands (m vom Lochrand nach innen)
LOCHRAND_UEBERGANG_M = 160.0


def _an_lochrand(h, pts, loch, rand, uebergang=LOCHRAND_UEBERGANG_M):
    """Höhen nahe am Lochrand in die Höhe des nächsten Randvertex überblenden (außerhalb des Lochs ganz)."""
    lx, lz, lk = (float(w) for w in loch)
    innen = np.minimum.reduce([pts[:, 0] - lx, lx + lk - pts[:, 0], pts[:, 1] - lz, lz + lk - pts[:, 1]])
    w = np.clip(innen / uebergang, 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    band = np.flatnonzero(w < 1.0)
    if not len(band):
        return h
    h = h.copy()
    rxz = rand[:, (0, 2)]
    for a in range(0, len(band), 2048):
        teil = band[a:a + 2048]
        d2 = ((pts[teil, None, :] - rxz[None, :, :]) ** 2).sum(-1)
        hr = rand[d2.argmin(1), 1]
        h[teil] = h[teil] * w[teil] + hr * (1.0 - w[teil])
    return h


def netz_laden(pfad) -> dict:
    """Netz aus einer ``.npz`` der Spiel-Assets (pos, nor, idx, optional uv und farbe)."""
    d = np.load(pfad, allow_pickle=False)
    return {"pos": d["pos"].astype(np.float32), "nor": d["nor"].astype(np.float32),
            "uv": d["uv"].astype(np.float32) if "uv" in d.files else None,
            "farbe": d["farbe"].astype(np.float32) if "farbe" in d.files else None,
            "idx": d["idx"].astype(np.uint32)}


def _stand(pfad) -> list:
    try:
        st = os.stat(pfad)
        return [str(pfad), st.st_size, int(st.st_mtime)]
    except (OSError, TypeError):
        return [str(pfad), None, None]


def assets_index_datei(anfrage: dict) -> Path:
    """Ergebnis der Spielgrafik zu einer Anfrage (Träger, Netz, Logik, Berge, Versatz, Baueinstellungen, Stand des
    Werkzeugkasten-Moduls): Der Editor liest es ohne Arbeitsprozess, wenn es schon da ist (3d_ladeperformance.md)."""
    modul = Path(anfrage.get("toolkit") or ".") / "strecke" / "ansicht_assets.py"
    teile = [_stand(anfrage.get("traeger")), _stand(anfrage.get("szene")), _stand(anfrage.get("welt")) if anfrage.get("welt")
             else None, ((anfrage.get("umgebung") or {}).get("gelaende") or {}).get("berge"), anfrage.get("versatz"),
             anfrage.get("bauen"), sorted(anfrage.get("teile") or []), _stand(modul)]
    schluessel = hashlib.sha1(json.dumps(teile, sort_keys=True, default=str).encode()).hexdigest()[:20]
    return Path(anfrage.get("cache") or spielassets_ordner()) / ("anfrage_%s.json" % schluessel)


def assets_index_lesen(anfrage: dict):
    """Gespeichertes Ergebnis zur Anfrage, wenn alle Netzdateien noch da sind; sonst None."""
    datei = assets_index_datei(anfrage)
    try:
        erg = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    netze = [e.get("npz") for e in (erg.get("oberflaechen") or {}).values()]
    netze += [e.get("npz") for teil in ("aussen", "welt") for e in ((erg.get(teil) or {}).get("netze") or [])]
    if not all(n and os.path.exists(n) for n in netze):
        return None
    return erg


def assets_index_schreiben(anfrage: dict, erg: dict) -> None:
    datei = assets_index_datei(anfrage)
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        tmp = datei.with_suffix(".tmp")
        tmp.write_text(json.dumps(erg), encoding="utf-8")
        os.replace(tmp, datei)
        alte = sorted(datei.parent.glob("anfrage_*.json"), key=lambda q: q.stat().st_mtime, reverse=True)
        for q in alte[20:]:
            q.unlink()
    except OSError:
        pass


def spielassets_ordner() -> Path:
    """Zwischenspeicher der Spielgrafik (Texturen und Netze aus Träger und Spielordner, nur lokal)."""
    p = cache_ordner() / "spielassets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def berge(szene: Szene, gelaende: dict | None, weite_m: float = 9000.0, raster_m: float = 150.0):
    """Ferne Kulisse: grobes Höhenfeld ±``weite_m`` um die Strecke mit den Bergen aus ``umgebung.json``; innen 6 m tiefer
    als der Boden (vom Boden verdeckt)."""
    v = szene.vertices
    cx, cz = float(v[:, 0].mean()), float(v[:, 2].mean())
    g = np.arange(-weite_m, weite_m + raster_m, raster_m)
    X, Z = np.meshgrid(g + cx, g + cz)
    pts = np.column_stack([X.ravel(), Z.ravel()])
    grund = float(np.median(v[:, 1]))
    h = np.full(len(pts), grund - 6.0)
    for f in (gelaende or {}).get("berge", []):
        r = max(float(f["radius_m"]), 1.0)
        dd = np.hypot(pts[:, 0] - f["x"], pts[:, 1] - f["z"])
        innen = dd < r
        h[innen] += float(f["hoehe_m"]) * 0.5 * (1 + np.cos(np.pi * dd[innen] / r))
    n = len(g)
    pos = np.column_stack([pts[:, 0], h, pts[:, 1]]).astype(np.float32)
    H = h.reshape(n, n)
    gz_, gx_ = np.gradient(H, raster_m)
    nor = np.column_stack([-gx_.ravel(), np.ones(n * n), -gz_.ravel()])
    nor /= np.linalg.norm(nor, axis=1)[:, None]
    fels = np.clip((h - grund) / 400.0, 0, 1)
    farbe = np.column_stack([0.30 + 0.16 * fels, 0.42 + 0.0 * fels, 0.26 + 0.12 * fels, np.ones(len(pos))])
    i = np.arange(n * n).reshape(n, n)
    a, b, c, e = i[:-1, :-1].ravel(), i[:-1, 1:].ravel(), i[1:, 1:].ravel(), i[1:, :-1].ravel()
    idx = np.column_stack([a, e, c, a, c, b]).ravel().astype(np.uint32)
    return {"pos": pos, "nor": nor.astype(np.float32), "uv": None, "farbe": farbe.astype(np.float32), "idx": idx}


def nahgrenze(hoehe_ueber_boden: float) -> float:
    """Nahe Schnittebene der Kamera (m): 2 % der Höhe über dem Boden, 0,3 … 10 m."""
    return float(min(max(0.02 * max(hoehe_ueber_boden, 0.0), 0.3), 10.0))


class Bodenhoehe:
    """Höhe des Bodennetzes an beliebigen Punkten (wie gezeichnet), für Objekte und das Auswählen."""

    def __init__(self, netz, raster_m=BODEN_RASTER_M):
        p = netz["pos"]
        self.x0, self.z0 = float(p[:, 0].min()), float(p[:, 2].min())
        self.nx = int(round((p[:, 0].max() - self.x0) / raster_m)) + 1
        self.nz = len(p) // self.nx
        self.r = raster_m
        self.h = p[:, 1].reshape(self.nz, self.nx)

    def __call__(self, x, z):
        """Höhe wie gezeichnet: je Zelle zwei Dreiecke mit der Diagonale (i, j)–(i+1, j+1) (``boden``)."""
        fx = np.clip((np.asarray(x, float) - self.x0) / self.r, 0, self.nx - 1.001)
        fz = np.clip((np.asarray(z, float) - self.z0) / self.r, 0, self.nz - 1.001)
        i, j = fz.astype(int), fx.astype(int)
        w, u = fz - i, fx - j
        h = self.h
        ha, hb, hc, he = h[i, j], h[i, j + 1], h[i + 1, j + 1], h[i + 1, j]
        return np.where(w >= u, ha + (hc - he) * u + (he - ha) * w, ha + (hb - ha) * u + (hc - hb) * w)


# ------------------------------------------------------------------ Objekte
def objekt_netze(objekte, index, hoehe, eigene=frozenset(), ausgelassen=frozenset()) -> dict:
    """Objekte als Platzhalter, nach Art zusammengefasst: ``{"quader": Netz, "baeume": Netz}``. Quader = Modell-AABB ×
    Maßstab in der Farbe der Kategorie; Bäume/Büsche/Palmen als Kegel mit Stamm (Höhe aus der AABB). Objekte in
    ``ausgelassen`` (z. B. mit echtem Modell gezeigt) entfallen."""
    qp, qn, qi = _einheitsquader()
    kp, kn, ki, n_stamm = _kegel()
    quader, baeume = [], []
    for o in objekte:
        if o["id"] in ausgelassen:
            continue
        e = index.get(o["modell"]) or {}
        kat = e.get("kategorie") or "sonstiges"
        lo = np.asarray(e.get("aabb_min") or [-1.5, 0, -1.5], float)
        hi = np.asarray(e.get("aabb_max") or [1.5, 3, 1.5], float)
        k = float(o.get("skala", 1.0))
        y = float(hoehe(o["x"], o["z"])) + float(o.get("hoehe_m", 0.0))
        rgb = KATEGORIE_RGB.get(kat, (0.55, 0.6, 0.66))
        if kat in PFLANZEN:
            rad = max(float(max(hi[0] - lo[0], hi[2] - lo[2])) / 2.0, 0.8) * k
            hoch = max(float(hi[1] - lo[1]), 2.0) * k
            pos, nor = _transformiert(kp, kn, o["x"], y, o["z"], o.get("drehung_grad", 0.0), (rad, hoch, rad))
            farbe = np.tile([*rgb, 1.0], (len(pos), 1)).astype(np.float32)
            farbe[-n_stamm:] = (0.40, 0.28, 0.18, 1.0)
            baeume.append({"pos": pos, "nor": nor, "uv": None, "farbe": farbe, "idx": ki})
        else:
            groesse = np.maximum(hi - lo, 0.2) * k
            lokal = qp * groesse + lo * k
            pos, nor = _transformiert(lokal, qn, o["x"], y, o["z"], o.get("drehung_grad", 0.0), (1, 1, 1))
            farbe = np.tile([*rgb, 1.0], (len(pos), 1)).astype(np.float32)
            quader.append({"pos": pos, "nor": nor, "uv": None, "farbe": farbe, "idx": qi})
    return {"quader": zusammenfuegen(quader), "baeume": zusammenfuegen(baeume)}


#: Fassung der Spielmodelle im Zwischenspeicher (adapter.MODELL_3D_FASSUNG: Teile je Material)
MODELL_3D_FASSUNG = 3


def modell_3d_lesen(ziel):
    """Fertig vorbereitetes Spielmodell (``<ziel>.json`` mit Teilen je Material) oder None (neu vorbereiten)."""
    try:
        daten = json.loads(Path(ziel).with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if daten.get("fassung") != MODELL_3D_FASSUNG or not all(Path(t["npz"]).exists() for t in daten.get("teile") or [{}]
                                                              if t.get("npz")):
        return None
    return daten if daten.get("teile") else None


def instanz_xml(objekte, hoehe) -> str:
    """Instanztabelle für Qt Quick 3D ``FileInstancing``: je Objekt Position (auf dem Boden + ``hoehe_m``),
    Drehung um die Hochachse (wie ``_transformiert``) und Maßstab."""
    zeilen = ['<?xml version="1.0" encoding="UTF-8" ?>', "<InstanceTable>"]
    for o in objekte:
        k = float(o.get("skala", 1.0))
        y = float(hoehe(o["x"], o["z"])) + float(o.get("hoehe_m", 0.0)) if hoehe is not None else float(o.get("hoehe_m", 0.0))
        zeilen.append('  <Instance position="%.3f %.3f %.3f" scale="%.4f %.4f %.4f" eulerRotation="0 %.3f 0"/>'
                      % (float(o["x"]), y, float(o["z"]), k, k, k, float(o.get("drehung_grad", 0.0))))
    zeilen.append("</InstanceTable>")
    return "\n".join(zeilen) + "\n"


# ------------------------------------------------------------------ Einblendungen
def band(punkte, breite, farben, hoeher=0.08) -> dict:
    """Band entlang ``punkte`` (n,3) mit Breite und Farbe je Punkt (Ideallinie, Zonen)."""
    p = np.asarray(punkte, float)
    if len(p) < 2:
        return _leer()
    t = np.gradient(p, axis=0)
    t[:, 1] = 0
    t /= np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
    r = np.column_stack([-t[:, 2], np.zeros(len(t)), t[:, 0]])
    b = np.broadcast_to(np.asarray(breite, float), (len(p),))[:, None] / 2.0
    links, rechts = p - r * b + [0, hoeher, 0], p + r * b + [0, hoeher, 0]
    pos = np.empty((2 * len(p), 3))
    pos[0::2], pos[1::2] = links, rechts
    farbe = np.repeat(np.asarray(farben, float).reshape(-1, 4), 2, axis=0) if np.ndim(farben) > 1 else np.tile(farben, (2 * len(p), 1))
    i = np.arange(len(p) - 1) * 2
    idx = np.column_stack([i, i + 1, i + 3, i, i + 3, i + 2]).ravel()
    return {"pos": pos.astype(np.float32), "nor": np.tile([0, 1, 0], (len(pos), 1)).astype(np.float32), "uv": None,
            "farbe": np.asarray(farbe, np.float32), "idx": idx.astype(np.uint32)}


def tempo_farben(tempo) -> np.ndarray:
    """Rot (langsam) → Gelb → Grün (schnell), wie die Karte."""
    v = np.asarray(tempo, float)
    a = np.clip((v - v.min()) / max(float(np.ptp(v)), 1e-6), 0, 1)
    r = np.clip(2 - 2 * a, 0, 1)
    g = np.clip(2 * a, 0, 1)
    return np.column_stack([r, g, np.full(len(a), 0.1), np.ones(len(a))])


def einblendungen(szene: Szene) -> dict:
    """{Name: Netz} der Einblendungen: Ideallinie (Tempo), DRS-Zonen, Bremstafeln, Startplätze, Sektorlinien."""
    erg = {}
    if len(szene.linie):
        erg["ideallinie"] = band(np.vstack([szene.linie, szene.linie[:1]]), 0.6,
                                 tempo_farben(np.r_[szene.tempo, szene.tempo[:1]]), hoeher=0.06)
    lg = szene.logik
    zonen = []
    for z in lg.get("drs") or []:
        a = z.get("aktivierung_s_m", z.get("aktivierung_m"))
        e = z.get("ende_s_m", z.get("ende_m"))
        if a is None or e is None:
            continue
        a, e = float(a), float(e)
        laenge = (e - a) % max(szene.laenge, 1e-6)
        s = a + np.arange(0, laenge + 1e-6, 4.0)
        zonen.append(band([szene.punkt(x) for x in s], 11.0, (0.2, 0.9, 0.3, 0.35), hoeher=0.05))
    erg["drs"] = zusammenfuegen(zonen)
    qp, qn, qi = _einheitsquader()
    tafeln, plaetze, sektoren, schilder = [], [], [], {}
    grau = (0.55, 0.56, 0.58, 1.0)
    for b in lg.get("bremsmarker") or []:
        # Entfernungstafel (26.11.2026): quer zur Fahrtrichtung, Vorderseite zu den ankommenden Autos, Zahl als Textur
        p = np.asarray(b.get("position") or [0, 0, 0], float)
        d = szene.richtung_bei(p)
        r = np.cross(d, [0.0, 1.0, 0.0])
        for seite in (-1, 1):                                   # zwei Pfosten
            fuss = p + r * seite * (TAFEL_BREITE_M / 2 - 0.08)
            tafeln.append(_ausgerichtet(qp, qn, qi, fuss, d, 0.08, TAFEL_UNTEN_M + TAFEL_HOEHE_M, 0.08, grau))
        rueck = p + d * 0.06                                    # Rückseite hinter der Zahl
        tafeln.append(_ausgerichtet(qp, qn, qi, rueck + [0, TAFEL_UNTEN_M, 0], d, TAFEL_BREITE_M, TAFEL_HOEHE_M, 0.1,
                                    (0.30, 0.31, 0.33, 1.0)))
        schilder.setdefault(int(b.get("meter") or 0), []).append(_tafel(p - d * 0.01, d, r))
    for sp in lg.get("startplaetze") or []:
        # Startplatz als halbtransparente Fläche in Fahrtrichtung (Fahrzeuggröße), knapp über dem Asphalt
        p = np.asarray(sp.get("position") or [0, 0, 0], float)
        d = szene.richtung_bei(p)
        plaetze.append(_ausgerichtet(qp, qn, qi, p - d * STARTPLATZ_LAENGE_M / 2 + [0, 0.03, 0], d, STARTPLATZ_BREITE_M,
                                     0.02, STARTPLATZ_LAENGE_M, (0.25, 0.65, 1.0, 0.45)))
    for sek in lg.get("sektoren") or []:
        s = sek.get("s_start_m")
        if not s:
            continue
        p = szene.punkt(float(s))
        sektoren.append(_ausgerichtet(qp, qn, qi, p, szene.richtung_bei(p), 16.0, 0.08, 0.5, (0.75, 0.3, 0.9, 1)))
    erg["tafeln"] = {m: zusammenfuegen(liste) for m, liste in sorted(schilder.items())}
    erg["bremstafeln"] = zusammenfuegen(tafeln)
    erg["startplaetze"] = zusammenfuegen(plaetze)
    erg["sektoren"] = zusammenfuegen(sektoren)
    return erg


#: Entfernungstafeln und Startplätze der Einblendungen (m)
TAFEL_BREITE_M, TAFEL_HOEHE_M, TAFEL_UNTEN_M = 1.4, 1.1, 0.6
STARTPLATZ_BREITE_M, STARTPLATZ_LAENGE_M = 2.0, 5.6


def _ausgerichtet(qp, qn, qi, fuss, d, quer, hoch, laengs, farbe) -> dict:
    """Quader mit Fußpunkt-Mitte ``fuss`` (x quer, y hoch, z längs in Richtung ``d``, nur waagrecht gedreht)."""
    d = np.asarray(d, float)
    r = np.cross(d, [0.0, 1.0, 0.0])
    lokal = (qp - [0.5, 0.0, 0.5]) * [quer, hoch, laengs]
    pos = np.asarray(fuss, float) + lokal[:, :1] * r + lokal[:, 1:2] * [0.0, 1.0, 0.0] + lokal[:, 2:] * d
    nor = qn[:, :1] * r + qn[:, 1:2] * [0.0, 1.0, 0.0] + qn[:, 2:] * d
    return {"pos": pos.astype(np.float32), "nor": nor.astype(np.float32), "uv": None,
            "farbe": np.tile(farbe, (len(pos), 1)).astype(np.float32), "idx": qi}


def _tafel(fuss, d, r) -> dict:
    """Vorderseite einer Entfernungstafel (Viereck mit UV 0…1), sichtbar für Autos in Richtung ``d``."""
    unten, oben = TAFEL_UNTEN_M, TAFEL_UNTEN_M + TAFEL_HOEHE_M
    h = TAFEL_BREITE_M / 2
    ecken = [fuss - r * h + [0, unten, 0], fuss + r * h + [0, unten, 0], fuss + r * h + [0, oben, 0],
             fuss - r * h + [0, oben, 0]]
    return {"pos": np.asarray(ecken, np.float32), "nor": np.tile(-np.asarray(d, np.float32), (4, 1)),
            "uv": np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32), "farbe": None,
            "idx": np.array([0, 1, 2, 0, 2, 3], np.uint32)}


def tafel_textur(meter: int, ordner: Path | None = None) -> Path:
    """Bild einer Entfernungstafel (weiß, schwarze Zahl), im Zwischenspeicher."""
    from PIL import Image, ImageDraw, ImageFont
    pfad = (ordner or cache_ordner()) / ("tafel_%d.png" % int(meter))
    if pfad.exists():
        return pfad
    bild = Image.new("RGB", (256, 200), (246, 246, 246))
    zeichnen = ImageDraw.Draw(bild)
    zeichnen.rectangle([0, 0, 255, 199], outline=(30, 30, 30), width=8)
    try:
        schrift = ImageFont.truetype("arialbd.ttf", 120)
    except OSError:
        schrift = ImageFont.load_default(size=110) if hasattr(ImageFont, "load_default") else None
    text = str(int(meter))
    zeichnen.text((128, 104), text, fill=(20, 20, 20), font=schrift, anchor="mm")
    bild.save(pfad)
    return pfad


def bereich_rahmen(versatz=(0.0, 0.0, 0.0), hoehe_m=0.0) -> dict:
    """Rahmen des Streckenbereichs (Loch des Außengeländes) als flaches Band, in Definitionskoordinaten."""
    x0, x1 = umgebung.WELT_X[0] - versatz[0], umgebung.WELT_X[1] - versatz[0]
    z0, z1 = umgebung.WELT_Z[0] - versatz[2], umgebung.WELT_Z[1] - versatz[2]
    ecken = [(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]
    pkt = []
    for (ax, az), (bx, bz) in zip(ecken, ecken[1:]):
        for a in np.linspace(0, 1, 40, endpoint=False):
            pkt.append((ax + (bx - ax) * a, hoehe_m + 0.5, az + (bz - az) * a))
    pkt.append(pkt[0])
    return band(pkt, 4.0, (1.0, 0.55, 0.1, 0.8), hoeher=0.0)


# ------------------------------------------------------------------ Texturen
def texturen(ordner: Path | None = None) -> dict:
    """Einfache Texturen (PNG im Zwischenspeicher): Gras, Kies, Randsteinstreifen, Beton. Rückgabe {Name: Pfad}."""
    from PIL import Image
    ordner = ordner or cache_ordner()
    erg = {}
    rng = np.random.default_rng(7)

    def rauschen(basis, staerke, groesse=256):
        n = rng.random((groesse // 8, groesse // 8))
        n = np.asarray(Image.fromarray((n * 255).astype(np.uint8)).resize((groesse, groesse), Image.BICUBIC), float) / 255
        fein = rng.random((groesse, groesse))
        werte = np.clip(np.asarray(basis)[None, None, :] * (1 + staerke * (n[..., None] - 0.5) + 0.15 * (fein[..., None] - 0.5)), 0, 1)
        return Image.fromarray((werte * 255).astype(np.uint8))

    for name, basis, staerke in (("gras", (0.36, 0.48, 0.27), 0.45), ("kies", (0.78, 0.71, 0.55), 0.35),
                                 ("beton", (0.74, 0.74, 0.72), 0.2), ("asphalt", (0.26, 0.26, 0.28), 0.25)):
        pfad = ordner / ("tex2_%s.png" % name)
        if not pfad.exists():
            rauschen(basis, staerke).save(pfad)
        erg[name] = pfad
    pfad = ordner / "tex2_randstein.png"
    if not pfad.exists():
        bild = np.zeros((64, 64, 3), np.uint8)
        bild[:32] = (205, 40, 35)
        bild[32:] = (240, 240, 240)
        Image.fromarray(bild).save(pfad)
    erg["randstein"] = pfad
    return erg


# ------------------------------------------------------------------ Auswählen
def naechstes_objekt(objekte, x, z, radius_m=8.0):
    """Kennung des Objekts, dessen Mittelpunkt (x, z) am nächsten liegt (innerhalb ``radius_m``), sonst None."""
    best, best_d = None, radius_m
    for o in objekte:
        d = math.hypot(o["x"] - x, o["z"] - z)
        if d < best_d:
            best, best_d = o["id"], d
    return best


def blick_auf_karte(position, ziel) -> tuple:
    """(x, z, Richtung in Grad wie ``umgebung.winkel``) der Kamera für den Sichtkegel in der 2D-Karte."""
    dx, dz = float(ziel[0] - position[0]), float(ziel[2] - position[2])
    return float(position[0]), float(position[2]), umgebung.winkel(dx, dz)


# ---------------------------------------------------------------------------------------------- Kamerasteuerung
def blickrichtung(gier: float, nick: float) -> np.ndarray:
    """Blickrichtung zu Qt-Eulerwinkeln (x = Nicken, y = Gieren; Kamera schaut entlang −z)."""
    g, n = math.radians(gier), math.radians(nick)
    return np.array([-math.sin(g) * math.cos(n), math.sin(n), -math.cos(g) * math.cos(n)])


def _drehen_um(v: np.ndarray, achse: np.ndarray, grad: float) -> np.ndarray:
    """Rodrigues: ``v`` um die Einheitsachse ``achse`` drehen."""
    w = math.radians(grad)
    c, s = math.cos(w), math.sin(w)
    return v * c + np.cross(achse, v) * s + achse * float(np.dot(achse, v)) * (1 - c)


class Kamerafuehrung:
    """Editor-Kamera wie in Blender und Unity, ohne Qt (21.11.2026).

    Zustand: Position, Gieren, Nicken und ein Drehpunkt. Umkreisen dreht die Kamera um den Drehpunkt (den Punkt unter
    der Maus beim Drücken), Verschieben bewegt beides parallel zum Boden, Zoomen fährt auf den Punkt unter der Maus zu,
    Fliegen bewegt die Kamera mit WASD/QE; das Flugtempo wächst mit der Höhe über dem Boden.
    """
    NICK_GRENZE = 89.0
    ABSTAND_MIN, ABSTAND_MAX = 1.5, 30000.0

    def __init__(self):
        self.pos = np.array([0.0, 400.0, 600.0])
        self.gier = 0.0
        self.nick = -35.0
        self.drehpunkt = np.zeros(3)
        self.flugfaktor = 1.0
        self._flug = None                 # (start, ziel, start_dreh, ziel_dreh, t, dauer)

    # Ableitungen
    @property
    def vorn(self) -> np.ndarray:
        return blickrichtung(self.gier, self.nick)

    @property
    def rechts(self) -> np.ndarray:
        g = math.radians(self.gier)
        return np.array([math.cos(g), 0.0, -math.sin(g)])

    @property
    def vorn_eben(self) -> np.ndarray:
        g = math.radians(self.gier)
        return np.array([-math.sin(g), 0.0, -math.cos(g)])

    @property
    def abstand(self) -> float:
        return float(np.linalg.norm(self.drehpunkt - self.pos))

    def euler(self) -> tuple:
        return (self.nick, self.gier, 0.0)

    def ziel(self) -> np.ndarray:
        """Punkt, auf den die Kamera schaut (für die Karte)."""
        return self.pos + self.vorn * max(self.abstand, 50.0)

    # Setzen
    def blick(self, pos, ziel):
        """Kamera nach Position und Zielpunkt setzen (z. B. aus einer Fahrkamera übernehmen)."""
        pos, ziel = np.asarray(pos, float), np.asarray(ziel, float)
        f = ziel - pos
        n = float(np.linalg.norm(f))
        if n < 1e-6:
            return
        f /= n
        self.pos = pos.copy()
        self.nick = math.degrees(math.asin(max(-1.0, min(1.0, f[1]))))
        self.gier = math.degrees(math.atan2(-f[0], -f[2]))
        self.drehpunkt = ziel.copy()
        self._flug = None

    def winkel(self, pos, nick, gier, abstand=150.0):
        self.pos = np.asarray(pos, float).copy()
        self.nick = max(-self.NICK_GRENZE, min(self.NICK_GRENZE, float(nick)))
        self.gier = float(gier)
        self.drehpunkt = self.pos + self.vorn * abstand
        self._flug = None

    def alles_zeigen(self, lo, hi, nick=-50.0):
        """Ganze Strecke ins Bild: Drehpunkt in der Mitte, Abstand nach der Ausdehnung, Gieren bleibt."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        mitte = (lo + hi) / 2
        weite = float(max(hi[0] - lo[0], hi[2] - lo[2], 50.0))
        self.nick = nick
        self.drehpunkt = mitte
        self.pos = mitte - self.vorn * weite * 0.85
        self._flug = None

    # Mausbewegungen
    def kreisen(self, d_gier: float, d_nick: float):
        """Um den Drehpunkt kreisen (Maus waagrecht = gieren, senkrecht = nicken)."""
        nick_neu = max(-self.NICK_GRENZE, min(self.NICK_GRENZE, self.nick + d_nick))
        d_nick = nick_neu - self.nick
        rel = self.pos - self.drehpunkt
        rel = _drehen_um(rel, np.array([0.0, 1.0, 0.0]), d_gier)
        self.gier += d_gier
        rel = _drehen_um(rel, self.rechts, d_nick)
        self.nick = nick_neu
        self.pos = self.drehpunkt + rel
        self._flug = None

    def blicken(self, d_gier: float, d_nick: float):
        """Umsehen an Ort und Stelle (rechte Maustaste); der Drehpunkt wandert mit."""
        abstand = max(self.abstand, 20.0)
        self.gier += d_gier
        self.nick = max(-self.NICK_GRENZE, min(self.NICK_GRENZE, self.nick + d_nick))
        self.drehpunkt = self.pos + self.vorn * abstand
        self._flug = None

    def schieben(self, dx_px: float, dy_px: float, hoehe_px: float, sichtfeld_grad: float = 62.0):
        """Parallel zum Boden verschieben, so dass der Boden der Maus folgt."""
        m_je_px = 2.0 * max(self.abstand, 2.0) * math.tan(math.radians(sichtfeld_grad) / 2) / max(hoehe_px, 1.0)
        neigung = max(abs(math.sin(math.radians(self.nick))), 0.35)
        d = -self.rechts * dx_px * m_je_px + self.vorn_eben * dy_px * m_je_px / neigung
        self.pos = self.pos + d
        self.drehpunkt = self.drehpunkt + d
        self._flug = None

    def zoomen(self, stufen: float, punkt=None):
        """Mausrad: auf ``punkt`` (Treffer unter der Maus) zu- bzw. davon wegfahren; ohne Treffer auf den Drehpunkt."""
        ziel = self.drehpunkt if punkt is None else np.asarray(punkt, float)
        weg = ziel - self.pos
        n = float(np.linalg.norm(weg))
        if n < 1e-6:
            return
        neu = max(self.ABSTAND_MIN, min(self.ABSTAND_MAX, n * 0.8 ** stufen))
        self.pos = ziel - weg / n * neu
        if punkt is not None:                              # Drehpunkt auf den Blickstrahl nachziehen
            tiefe = max(float(np.dot(ziel - self.pos, self.vorn)), self.ABSTAND_MIN)
            self.drehpunkt = self.pos + self.vorn * tiefe
        self._flug = None

    # Tastatur
    def flugtempo(self, hoehe_ueber_boden: float, schnell=False) -> float:
        """m/s: 12 m/s nahe am Boden, mit der Höhe wachsend (100 m hoch = 120 m/s), Umschalt ×4, Mausrad ×1,25."""
        v = min(max(12.0, 1.2 * max(hoehe_ueber_boden, 0.0)), 2500.0) * self.flugfaktor
        return v * (4.0 if schnell else 1.0)

    def bewegen(self, vor: float, seit: float, hoch: float, dt: float, tempo: float, fliegen: bool):
        """WASD/QE. ``fliegen`` (rechte Maustaste gehalten): entlang der Blickrichtung; sonst parallel zum Boden."""
        if not (vor or seit or hoch):
            return
        vorn = self.vorn if fliegen else self.vorn_eben
        d = vorn * vor + self.rechts * seit + np.array([0.0, 1.0, 0.0]) * hoch
        n = float(np.linalg.norm(d))
        if n < 1e-9:
            return
        d = d / n * tempo * dt
        self.pos = self.pos + d
        self.drehpunkt = self.drehpunkt + d
        self._flug = None

    # Anflug (Doppelklick, F)
    def anfliegen(self, punkt, abstand=None, dauer=0.35):
        """Weich auf ``punkt`` zufliegen: Blickrichtung bleibt, Abstand höchstens 80 m (oder ``abstand``)."""
        punkt = np.asarray(punkt, float)
        a = float(abstand) if abstand is not None else min(max(self.abstand, 8.0), 80.0)
        self._flug = (self.pos.copy(), punkt - self.vorn * a, self.drehpunkt.copy(), punkt, 0.0, max(dauer, 1e-3))

    def schritt(self, dt: float) -> bool:
        """Anflug fortsetzen; True, solange er läuft."""
        if self._flug is None:
            return False
        p0, p1, d0, d1, t, dauer = self._flug
        t = min(1.0, t + dt / dauer)
        k = t * t * (3 - 2 * t)
        self.pos = p0 + (p1 - p0) * k
        self.drehpunkt = d0 + (d1 - d0) * k
        self._flug = None if t >= 1.0 else (p0, p1, d0, d1, t, dauer)
        return True
