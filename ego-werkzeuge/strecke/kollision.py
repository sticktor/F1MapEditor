"""Kollision für eigene 3D-Modelle (M5b, 16.11.2026, docs/eigene_modelle_m5b.md).

Umgebungsobjekte sind reine Kulisse: Instanzen im Layer ``dressing``/``speedtrees`` haben keine Fahrphysik, Autos
fahren hindurch. Der einfachste Weg zu einer Kollision ist derselbe wie bei den Mauern der Strecke: senkrechte
Flächen mit dem Oberflächencode ``SCON`` (Beton) in der Fahrphysik ``TRN~``. Der Bau erzeugt die ``TRN~`` ohnehin
ganz neu (``spiel_geometrie.geometrie_einsetzen`` → ``ego.trn_erzeugen``); die Quader werden dort vor dem
Verschweißen angehängt, Quadtree, Kacheln und Kantenflags entstehen wie für alle anderen Dreiecke.

Ein Quader ist der Grundriss der Modell-AABB (``umgebung.grundriss``: gedreht und skaliert wie die Instanz), von der
Unterkante des Modells (0,5 m in den Boden versenkt) bis zur Oberkante. Die Höhe der Instanz kommt aus
``umgebung.bodenlage`` auf denselben sichtbaren Flächen wie in der Umgebungsphase (``umgebung.sichtflaechen``), also
steht der Quader genau dort, wo das Modell steht. Nur die vier Seitenflächen kommen in die Physik (wie die Mauern,
``mauer_physik='senkrecht'``); Normalen zeigen nach außen wie bei den Mauern des Generators.

Einstellung:
    ``<projekt>/modelle/modelle.json`` → Eintrag ``kollision``: ``keine`` (Standard) oder ``quader``;
    ``umgebung.json`` → Objekt ``kollision`` (optional): ``keine`` oder ``quader`` überschreibt den Wert des Modells.
Nur eigene Modelle (``usr_…``); Spielmodelle bekommen keine Kollision (Objektwert wird dort nur gemeldet).
Ohne Objekte mit Kollision ändert sich nichts am Bau (gleiche Bytes).
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

#: Werte je Modell (modelle.json)
ARTEN = ("keine", "quader")
#: Oberflächencode der Kollisionsflächen (wie die Mauern, ``spiel_geometrie.KLASSEN['mauer']``)
CODE = "SCON"
#: So weit reicht der Quader unter die Unterkante des Modells (in den Boden, keine Lücke am Hang)
EINSINKEN_M = 0.5
#: Mindesthöhe über dem Boden (flache Modelle: das Auto soll nicht darüber hinweg springen)
MINDEST_HOEHE_M = 1.0
#: Mindestkante des Grundrisses (sehr dünne Modelle, z. B. Schilder; entartete Flächen vermeiden)
MINDEST_KANTE_M = 0.2
#: Klassen des Generatornetzes, die eine Kollisionsbox nicht berühren soll (Regel UMG_KOLLISION_FAHRBAHN)
SPERR_KLASSEN = ("asphalt", "randstein", "linie", "boxengasse", "startbox", "markierung")
#: Rasterweite der Proben im Grundriss (Regel UMG_KOLLISION_FAHRBAHN)
PROBE_M = 0.5


def objekte(daten: dict, projekt) -> Tuple[List[Tuple[dict, dict]], List[str]]:
    """Objekte mit Kollision: ([(Objekt, Katalogeintrag des eigenen Modells)], [Hinweise]).

    ``daten``: normalisierte ``umgebung.json`` (``umgebung.laden``/``normalisieren``). ``projekt``: Projektordner mit
    ``modelle/``. Die Modelltabelle wird nur gelesen; erst wenn ein Objekt eine Kollision bekommt, wird die Statistik
    geänderter Dateien aufgefrischt (wie in ``umgebung.eigene_einsetzen``). Fehlende oder fehlerhafte Modelle werden
    übergangen (die Umgebungsphase bricht dafür ohnehin ab, Regel UM-02)."""
    from . import eigene_modelle as em
    from . import umgebung as umg
    hinweise: List[str] = []
    obj = (daten or {}).get("objekte") or []
    if not obj:
        return [], hinweise
    basis = umg.projektordner(projekt)
    tabelle = {}
    if basis and os.path.exists(em.tabelle_pfad(basis)):
        try:
            tabelle = {e["name"]: e for e in em.laden(basis)["modelle"]}
        except (em.ModellFehler, OSError, ValueError):
            tabelle = {}
    gewaehlt = []
    for o in obj:
        name = umg.eigener_name(o["modell"])
        wert = o.get("kollision")
        if name is None:
            if wert == "quader":
                hinweise.append("Object %s: collision only works for own models (game model %s keeps none)"
                                % (o["id"], o["modell"].rsplit("/", 1)[-1]))
            continue
        e = tabelle.get(name)
        art = wert if wert is not None else (e or {}).get("kollision", "keine")
        if art == "quader" and e is not None:
            gewaehlt.append((o, name))
    if not gewaehlt:
        return [], hinweise
    try:
        em.aktualisieren(basis)
        gut, _ = umg.projektmodelle(basis)
    except (em.ModellFehler, umg.UmgebungFehler, OSError, ValueError):
        return [], hinweise
    aus = []
    for o, name in gewaehlt:
        if name in gut:
            aus.append((o, gut[name]))
    return aus, hinweise


def quader(objekt: dict, eintrag: dict, hoehen, versatz=(0.0, 0.0, 0.0)) -> Optional[dict]:
    """Kollisionsquader eines Objekts in Weltkoordinaten: ``ecken`` (4×2, x/z, Reihenfolge wie ``umgebung.grundriss``),
    ``unten``/``oben`` (Welt-y). None ohne Boden (die Umgebungsphase lässt das Objekt dann aus) oder ohne AABB."""
    from . import umgebung as umg
    ecken = umg.grundriss(objekt, eintrag, versatz)
    lage = umg.bodenlage(objekt, eintrag, hoehen, versatz)
    if ecken is None or lage is None:
        return None
    k = float(objekt.get("skala", 1.0))
    lo, hi = eintrag["aabb_min"], eintrag["aabb_max"]
    unten = lage["y"] + float(lo[1]) * k - EINSINKEN_M
    oben = max(lage["y"] + float(hi[1]) * k, lage["boden"] + MINDEST_HOEHE_M)
    ecken = _mindestkante(np.asarray(ecken, float))
    return {"id": objekt["id"], "modell": objekt["modell"].rsplit("/", 1)[-1], "ecken": ecken,
            "unten": float(unten), "oben": float(oben), "y": float(lage["y"])}


def _mindestkante(ecken: np.ndarray) -> np.ndarray:
    """Grundriss auf mindestens ``MINDEST_KANTE_M`` je Seite aufweiten (um die Mitte)."""
    m = ecken.mean(0)
    a, b = ecken[1] - ecken[0], ecken[3] - ecken[0]
    la, lb = np.linalg.norm(a), np.linalg.norm(b)
    ea = a / la if la > 1e-9 else np.array([1.0, 0.0])
    eb = b / lb if lb > 1e-9 else np.array([-ea[1], ea[0]])
    ha, hb = max(la, MINDEST_KANTE_M) / 2.0, max(lb, MINDEST_KANTE_M) / 2.0
    return np.array([m - ea * ha - eb * hb, m + ea * ha - eb * hb, m + ea * ha + eb * hb, m - ea * ha + eb * hb])


def netz(quader_liste: Sequence[dict]) -> Tuple[np.ndarray, np.ndarray]:
    """Seitenflächen aller Quader: (Vertices N×3, Dreiecke M×3). Je Quader 8 Vertices, 8 Dreiecke; Normalen nach außen
    (Kreuzprodukt (b−a)×(c−a) wie bei den Mauern des Generators)."""
    p, t = [], []
    for q in quader_liste:
        e = np.asarray(q["ecken"], float)
        # Umlaufsinn: Grundriss positiv orientiert in (x, z) (Shoelace > 0); dann zeigt (b−a)×(c−a) der Dreiecke unten
        # i, oben j, unten j nach rechts von der Kante i→j, also nach außen
        flaeche = 0.5 * float(np.sum(e[:, 0] * np.roll(e[:, 1], -1) - np.roll(e[:, 0], -1) * e[:, 1]))
        if flaeche < 0:
            e = e[::-1]
        basis = sum(len(x) for x in p)
        v = np.vstack([np.c_[e[:, 0], np.full(4, q["unten"]), e[:, 1]],
                       np.c_[e[:, 0], np.full(4, q["oben"]), e[:, 1]]])
        tri = []
        for i in range(4):
            j = (i + 1) % 4
            # unten i, unten j, oben j, oben i
            tri += [(i, 4 + j, j), (i, 4 + i, 4 + j)]
        p.append(v)
        t.append(np.asarray(tri, np.int64) + basis)
    if not p:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.concatenate(p), np.concatenate(t)


def quader_berechnen(paare: Sequence[Tuple[dict, dict]], hoehen, versatz) -> Tuple[List[dict], List[str]]:
    """Quader aller Objekte mit Kollision; Objekte ohne Boden werden ausgelassen (Hinweis)."""
    aus, ohne = [], []
    for o, e in paare:
        q = quader(o, e, hoehen, versatz)
        if q is None:
            ohne.append(o["id"])
        else:
            aus.append(q)
    return aus, ohne


def bericht(quader_liste: Sequence[dict], ohne: Sequence[str], hinweise: Sequence[str]) -> dict:
    return {"anzahl": len(quader_liste), "dreiecke": 8 * len(quader_liste), "code": CODE,
            "ohne_boden": list(ohne), "hinweise": list(hinweise),
            "quader": [{"id": q["id"], "modell": q["modell"],
                        "ecken": [[round(float(x), 3), round(float(z), 3)] for x, z in q["ecken"]],
                        "unten": round(q["unten"], 3), "oben": round(q["oben"], 3)} for q in quader_liste]}


# =============================================================================== Prüfregel UMG_KOLLISION_FAHRBAHN
def proben(ecken: np.ndarray, schritt: float = PROBE_M) -> np.ndarray:
    """Punkte im Grundriss (Raster ``schritt`` entlang der beiden Kanten, Ränder eingeschlossen)."""
    e = np.asarray(ecken, float)
    a, b = e[1] - e[0], e[3] - e[0]
    na = max(2, int(np.ceil(np.linalg.norm(a) / schritt)) + 1)
    nb = max(2, int(np.ceil(np.linalg.norm(b) / schritt)) + 1)
    s, t = np.meshgrid(np.linspace(0, 1, na), np.linspace(0, 1, nb))
    return e[0] + s.reshape(-1, 1) * a + t.reshape(-1, 1) * b


def fahrbahn_treffer(flaechen, namen: Sequence[str], ecken: np.ndarray) -> List[str]:
    """Klassen aus ``SPERR_KLASSEN``, die der Grundriss (Definitionskoordinaten) überdeckt."""
    gefunden = set()
    for x, z in proben(ecken):
        for i, _ in flaechen.treffer(float(x), float(z)):
            n = namen[int(flaechen.klasse[i])]
            if n in SPERR_KLASSEN:
                gefunden.add(n)
    return sorted(gefunden)
