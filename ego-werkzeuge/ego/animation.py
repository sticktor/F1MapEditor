"""Knotenanimation der EGO-Engine: ``AnimClip`` und ``ACStringTable`` lesen und schreiben (A0, 17.11.2026).

Befund und Formate: docs/animationen.md, Bestand: ergebnisse/animationen_bestand.md.

- ``ACStringTable`` (``…idf?context=animstringtable``): pkg ``ANST`` mit ``{"strings": [Knotennamen]}``.
- ``AnimClip`` (``…idf?context=animation&clip=<name>``): Fragment ``temp`` = pkg ``ANIM`` (``name``,
  ``string_table``, ``strings``, ``packets`` mit ``interpolator``/``mintime``/``maxtime``/``channels``), Fragment
  ``syst`` = Zeiten und Schlüssel. Kanal: ``name``, ``name_index``, ``misc`` (4 × u16: Anzahl, Typ, Feld 3, Feld 4),
  ``keyptr``/``timesptr`` (Byte-Offsets in ``syst``), ``mins``.

Geschrieben werden nur Formate, die das Spiel in abgespielten Clips nutzt (Zandvoort-Turm):

| Interpolator | Typ | Inhalt |
|---|---|---|
| 5 | ``0x8003`` | Verschiebung float4 (x, y, z, 0), Zeiten float s |
| 5 | ``0x8002`` | Skalierung float3, Zeiten float s |
| 9 | ``0x4201`` | Drehung xyzw als 4 × i16/32767, Zeiten u16 in Bildern zu 60 Hz |
| 11 | ``0x4201`` | konstante Drehung 4 × i16 (ohne Zeiten) |

``pkg_erzeugen`` baut ein pkg aus Python-Werten so, wie die Spieldateien aufgebaut sind (Blöcke in
Breitensuche, Felder nach FNV-1a-Hash, Werte dedupliziert in Reihenfolge des ersten Auftretens); der Test
``tests/test_animation.py`` erzeugt damit alle Clips und Namenstabellen der Umgebung bitgleich neu.
"""

import collections
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import grafik, pkg

BILDRATE = 60.0                     # Zeitbasis der 16-Bit-Drehung (Turm: Bild 122 = 2,0333 s)
ART_DREHUNG, ART_SKALIERUNG, ART_VERSCHIEBUNG = 1, 2, 3

# (Interpolator, Kanaltyp ohne Art, Feld 3, Feld 4) der geschriebenen Formate
F_VEKTOR = (5, 0x8000, 528, 8)
F_DREHUNG16 = (9, 0x4200, 264, 4)
F_DREHUNG_KONST = (11, 0x4200, 264, 0)

ANIM_TYPEN = {"maxtime": b"fp32", "mintime": b"fp32", "interpolator": b"ui32", "name_index": b"si32",
              "mins": b"ui64", "misc": b"ui64", "keyptr": b"ui32", "timesptr": b"ui32"}
_FORMAT = {b"fp32": "<f", b"ui32": "<I", b"si32": "<i", b"ui64": "<Q", b"si64": "<q"}
_AUSRICHTUNG = {b"fp32": 4, b"ui32": 4, b"si32": 4, b"ui64": 8, b"si64": 8}

TYP_UNBEKANNT = {"AnimClip": 1, "ACStringTable": 1}
FRAGMENT_FLAGS = 16


class AnimationFehler(ValueError):
    pass


# =============================================================================== pkg-Erzeuger
def pkg_erzeugen(kennung: bytes, wurzel: dict, typen: Optional[Dict[str, bytes]] = None) -> bytes:
    """pkg aus Python-Werten: dict → ``!idi`` (Version 1, Felder nach FNV-1a), list → ``!ili``, str → ``!sbi``,
    Zahlen → ``!vbi`` mit dem Datentyp ``typen[feldname]`` (Listenelemente: Typ des Listenfelds).
    Blockreihenfolge Breitensuche, dann ``!vca`` und die Wertetabellen in Reihenfolge der ersten Nutzung."""
    typen = typen or {}
    if len(kennung) != 4:
        raise AnimationFehler("pkg-Kennung muss vier Bytes haben")
    bloecke: List[pkg.Block] = []
    tabellen: List[pkg.Block] = []
    tab_index: Dict[bytes, int] = {}           # Datentyp (b"" = Strings) → Index in !vca
    werte_index: Dict[bytes, Dict[bytes, int]] = {}
    puffer: Dict[int, bytearray] = {}

    def tabelle(datentyp: bytes) -> int:
        if datentyp not in tab_index:
            if datentyp == b"":
                b = pkg.Block(b"!sbi")
            else:
                if datentyp not in _FORMAT:
                    raise AnimationFehler("Datentyp %r nicht unterstützt" % datentyp)
                b = pkg.Block(b"!vbi", datentyp=datentyp, elementgroesse=struct.calcsize(_FORMAT[datentyp]))
            tab_index[datentyp] = len(tabellen)
            puffer[len(tabellen)] = bytearray()
            werte_index[datentyp] = {}
            tabellen.append(b)
        return tab_index[datentyp]

    def wert(v, feldname: str) -> pkg.Verweis:
        if isinstance(v, str):
            roh = v.encode("utf-8", "surrogateescape")
            if len(roh) > 255:
                raise AnimationFehler("Zeichenkette über 255 Bytes: %r" % v[:40])
            datentyp, daten = b"", bytes([len(roh)]) + roh + b"\0"
        else:
            datentyp = typen.get(feldname)
            if datentyp is None:
                raise AnimationFehler("Kein Datentyp für Feld %r" % feldname)
            daten = struct.pack(_FORMAT[datentyp], v)
        ti = tabelle(datentyp)
        b, idx, puf = tabellen[ti], werte_index[datentyp], puffer[ti]
        if daten not in idx:
            idx[daten] = len(puf) if datentyp == b"" else len(puf) // b.elementgroesse
            puf += daten
        return pkg.Verweis(ti, idx[daten])

    def neuer_block(obj) -> pkg.Block:
        b = pkg.Block(b"!idi", version=1) if isinstance(obj, dict) else pkg.Block(b"!ili")
        bloecke.append(b)
        return b

    wurzelblock = neuer_block(wurzel)
    offen = collections.deque([(wurzel, wurzelblock, "")])
    while offen:
        obj, block, name = offen.popleft()
        if isinstance(obj, dict):
            for k in sorted(obj, key=pkg.fnv1a):
                v = obj[k]
                name_ref = wert(k, "")
                if isinstance(v, (dict, list)):
                    kind = neuer_block(v)
                    offen.append((v, kind, k))
                    ref = pkg.Verweis(0x80, 0, kind)
                else:
                    ref = wert(v, k)
                block.felder.append(pkg.Feld(name_ref, ref, pkg.fnv1a(k)))
        else:
            for v in obj:
                if isinstance(v, (dict, list)):
                    kind = neuer_block(v)
                    offen.append((v, kind, name))
                    block.werte.append(pkg.Verweis(0x80, 0, kind))
                else:
                    block.werte.append(wert(v, name))
    for i, t in enumerate(tabellen):
        t.daten = bytes(puffer[i])
    vca = pkg.Block(b"!vca", werte=[pkg.Verweis(0x80, 0, t) for t in tabellen])
    paket = pkg.Paket(kennung, pkg.Verweis(0x80, 0, wurzelblock), pkg.Verweis(0x80, 0, vca),
                      bloecke + [vca] + tabellen)
    daten = pkg.schreiben(paket)
    return daten + b"\0" * ((-len(daten)) % 4)        # Originale enden auf 4 Byte aufgefüllt


# =============================================================================== Namenstabelle
def stringtabelle_erzeugen(namen: Sequence[str]) -> bytes:
    return pkg_erzeugen(b"ANST", {"strings": list(namen)})


def stringtabelle_lesen(daten: bytes) -> List[str]:
    return list(pkg.lesen(daten).inhalt()["strings"])


# =============================================================================== Clip lesen
@dataclass
class Kanal:
    """Ein Kanal eines Clips. ``werte``: (n, 3) Verschiebung/Skalierung bzw. (n, 4) Quaternion xyzw;
    ``None``, wenn das Format nicht entschlüsselt ist (komprimierte Formate)."""

    knoten: str
    art: int                     # 1 Drehung, 2 Skalierung, 3 Verschiebung
    interpolator: int
    typ: int
    anzahl: int
    zeiten: Optional[np.ndarray] = None
    werte: Optional[np.ndarray] = None


@dataclass
class Clip:
    name: str
    string_table: str
    laenge: float
    kanaele: List[Kanal] = field(default_factory=list)

    def kurven(self) -> Dict[str, Dict[int, Kanal]]:
        out: Dict[str, Dict[int, Kanal]] = {}
        for k in self.kanaele:
            out.setdefault(k.knoten, {})[k.art] = k
        return out


def misc_zerlegen(misc: int) -> Tuple[int, int, int, int]:
    return tuple((misc >> s) & 0xFFFF for s in (0, 16, 32, 48))


def misc_bauen(anzahl: int, typ: int, f3: int, f4: int) -> int:
    if not 0 < anzahl <= 0xFFFF:
        raise AnimationFehler("Schlüsselanzahl %d außerhalb 1…65535" % anzahl)
    return anzahl | typ << 16 | f3 << 32 | f4 << 48


def clip_lesen(temp: bytes, syst: bytes) -> Clip:
    inh = pkg.lesen(temp).inhalt()
    laenge = max([p["maxtime"] for p in inh.get("packets", [])] or [0.0])
    clip = Clip(inh.get("name", ""), inh.get("string_table", ""), laenge)
    for p in inh.get("packets", []):
        ip = p["interpolator"]
        for c in p["channels"]:
            if "misc" not in c:
                raise AnimationFehler("Parameterclip (keycount/semantic) wird nicht gelesen")
            n, typ, f3, f4 = misc_zerlegen(c["misc"])
            k = Kanal(c["name"], typ & 0xF, ip, typ, n)
            kp, tp = c["keyptr"], c["timesptr"]
            if ip == 5 and typ in (0x8003, 0x8002):
                breite = 4 if typ == 0x8003 else 3
                k.zeiten = np.frombuffer(syst, "<f4", n, tp).astype(float)
                k.werte = np.frombuffer(syst, "<f4", n * breite, kp).reshape(n, breite)[:, :3].astype(float)
            elif ip == 2 and typ == 0x8401:
                k.zeiten = np.frombuffer(syst, "<f4", n, tp).astype(float)
                k.werte = np.frombuffer(syst, "<f4", n * 4, kp).reshape(n, 4).astype(float)
            elif ip == 9 and typ == 0x4201:
                k.zeiten = np.frombuffer(syst, "<u2", n, tp) / BILDRATE
                k.werte = np.frombuffer(syst, "<i2", n * 4, kp).reshape(n, 4) / 32767.0
            elif ip == 11 and typ == 0x4201:
                k.zeiten = np.zeros(1)
                k.werte = np.frombuffer(syst, "<i2", 4, kp).reshape(1, 4) / 32767.0
            elif ip == 10 and typ == 0x4002:          # konstante Skalierung, 1.0 = 32767
                k.zeiten = np.zeros(1)
                k.werte = np.frombuffer(syst, "<i2", 3, kp).reshape(1, 3) / 32767.0
            clip.kanaele.append(k)
    return clip


# =============================================================================== Clip schreiben
@dataclass
class Knotenkurve:
    """Bewegung eines Knotens. Je Art (Zeiten s, Werte): Verschiebung (n, 3), Drehung (n, 4) xyzw, Skalierung
    (n, 3). Fehlt eine Art, gilt die Ruhelage ``ruhe_*`` (aus dem Modellknoten)."""

    verschiebung: Optional[Tuple[Sequence[float], Sequence[Sequence[float]]]] = None
    drehung: Optional[Tuple[Sequence[float], Sequence[Sequence[float]]]] = None
    skalierung: Optional[Tuple[Sequence[float], Sequence[Sequence[float]]]] = None
    ruhe_t: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    ruhe_r: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    ruhe_s: Tuple[float, float, float] = (1.0, 1.0, 1.0)


def quaternionen_glaetten(q: np.ndarray) -> np.ndarray:
    """Normieren und Vorzeichen so wählen, dass benachbarte Schlüssel in derselben Hemisphäre liegen."""
    q = np.asarray(q, float).reshape(-1, 4)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]
    return q


def drehung_um_achse(achse, winkel_rad) -> np.ndarray:
    a = np.asarray(achse, float)
    a = a / np.linalg.norm(a)
    s = math.sin(winkel_rad / 2)
    return np.array([a[0] * s, a[1] * s, a[2] * s, math.cos(winkel_rad / 2)])


def drehung_kurve(achse, umdrehungen: float, dauer_s: float, schritte_je_umdrehung: int = 8):
    """Gleichmäßige Drehung (Zeiten, Quaternionen); höchstens 360°/schritte zwischen zwei Schlüsseln, damit die
    Interpolation den richtigen Weg nimmt."""
    n = max(2, int(math.ceil(abs(umdrehungen) * schritte_je_umdrehung)) + 1)
    t = np.linspace(0.0, dauer_s, n)
    q = np.array([drehung_um_achse(achse, 2 * math.pi * umdrehungen * x / dauer_s) for x in t])
    return t, q


def _ausrichten(blob: bytearray, n: int) -> None:
    blob += b"\0" * ((-len(blob)) % n)


def clip_erzeugen(clip_name: str, tabelle_kennung: str, kurven: Dict[str, Knotenkurve], laenge_s: float,
                  namen: Optional[Sequence[str]] = None) -> Tuple[bytes, bytes, List[str]]:
    """Clip aus Knotenkurven: (temp, syst, Namen der Stringtabelle). Jeder Knoten bekommt drei Kanäle
    (Verschiebung, Drehung, Skalierung) wie in den Originalen; feste Arten werden ein Schlüssel bzw. eine
    konstante Drehung. Zeiten der Drehung werden auf 1/60 s gerundet."""
    if not kurven:
        raise AnimationFehler("Clip ohne Knoten")
    if not (0 < laenge_s <= 0xFFFF / BILDRATE):
        raise AnimationFehler("Cliplänge %.3f s außerhalb 0…%.0f s" % (laenge_s, 0xFFFF / BILDRATE))
    namen = sorted(set(namen or []) | set(kurven))
    idx = {n: i for i, n in enumerate(namen)}

    vektor, dreh, konst = [], [], []          # (Knoten, Art, Zeiten, Werte)
    for knoten in sorted(kurven):
        k = kurven[knoten]
        for art, kurve, ruhe in ((ART_VERSCHIEBUNG, k.verschiebung, k.ruhe_t), (ART_SKALIERUNG, k.skalierung, k.ruhe_s)):
            if kurve is None:
                vektor.append((knoten, art, np.array([laenge_s]), np.array([ruhe], float)))
            else:
                t, w = np.asarray(kurve[0], float), np.asarray(kurve[1], float).reshape(-1, 3)
                if len(t) != len(w) or len(t) == 0 or np.any(np.diff(t) <= 0):
                    raise AnimationFehler("%s: Zeiten müssen streng steigen und zu den Werten passen" % knoten)
                vektor.append((knoten, art, t, w))
        if k.drehung is None:
            konst.append((knoten, quaternionen_glaetten(np.array([k.ruhe_r]))))
        else:
            t = np.round(np.asarray(k.drehung[0], float) * BILDRATE).astype(int)
            q = quaternionen_glaetten(k.drehung[1])
            if len(t) != len(q) or len(t) == 0 or np.any(np.diff(t) <= 0):
                raise AnimationFehler("%s: Drehzeiten müssen auf 1/60 s gerundet streng steigen" % knoten)
            if t.min() < 0 or t.max() > 0xFFFF:
                raise AnimationFehler("%s: Drehzeit außerhalb 0…65535 Bilder" % knoten)
            dreh.append((knoten, t, q))
    # Verschiebung vor Skalierung je Knoten wie im Turm-Clip (dort: erst alle Verschiebungen, dann Skalierungen)
    vektor.sort(key=lambda x: (0 if x[1] == ART_VERSCHIEBUNG else 1, x[0]))

    syst = bytearray()
    pakete = []
    # Paket Interpolator 5: erst alle Zeiten, dann alle Schlüssel (16-Byte-ausgerichtet)
    zeiger = []
    for knoten, art, t, w in vektor:
        _ausrichten(syst, 4)
        zeiger.append(len(syst))
        syst += np.asarray(t, "<f4").tobytes()
    kanaele = []
    for (knoten, art, t, w), tp in zip(vektor, zeiger):
        _ausrichten(syst, 16)
        kp = len(syst)
        if art == ART_VERSCHIEBUNG:
            syst += np.concatenate([w, np.zeros((len(w), 1))], axis=1).astype("<f4").tobytes()
        else:
            syst += w.astype("<f4").tobytes()
        kanaele.append({"name_index": idx[knoten], "mins": 0, "name": knoten, "keyptr": kp, "timesptr": tp,
                        "misc": misc_bauen(len(t), F_VEKTOR[1] | art, F_VEKTOR[2], F_VEKTOR[3])})
    pakete.append({"maxtime": float(laenge_s), "mintime": 0.0, "interpolator": F_VEKTOR[0], "channels": kanaele})
    if konst:
        _ausrichten(syst, 8)
        start = len(syst)
        kanaele = []
        for knoten, q in konst:
            kp = len(syst)
            syst += np.round(q[0] * 32767).astype("<i2").tobytes()
            kanaele.append({"name_index": idx[knoten], "mins": 0, "name": knoten, "keyptr": kp, "timesptr": start,
                            "misc": misc_bauen(1, F_DREHUNG_KONST[1] | ART_DREHUNG, F_DREHUNG_KONST[2], F_DREHUNG_KONST[3])})
        pakete.append({"maxtime": 0.0, "mintime": 0.0, "interpolator": F_DREHUNG_KONST[0], "channels": kanaele})
    if dreh:
        zeiger = []
        for knoten, t, q in dreh:
            _ausrichten(syst, 4)
            zeiger.append(len(syst))
            syst += t.astype("<u2").tobytes()
        kanaele = []
        for (knoten, t, q), tp in zip(dreh, zeiger):
            _ausrichten(syst, 4)
            kp = len(syst)
            syst += np.round(q * 32767).astype("<i2").tobytes()
            kanaele.append({"name_index": idx[knoten], "mins": 0, "name": knoten, "keyptr": kp, "timesptr": tp,
                            "misc": misc_bauen(len(t), F_DREHUNG16[1] | ART_DREHUNG, F_DREHUNG16[2], F_DREHUNG16[3])})
        pakete.append({"maxtime": float(laenge_s), "mintime": 0.0, "interpolator": F_DREHUNG16[0], "channels": kanaele})
    temp = pkg_erzeugen(b"ANIM", {"packets": pakete, "string_table": tabelle_kennung, "name": clip_name,
                                  "strings": []}, ANIM_TYPEN)
    return temp, bytes(syst), namen


# =============================================================================== Ressourcen und Kennungen
def clip_kennung(basis_idf: str, clip_name: str) -> str:
    """``eaid://…/<name>.idf`` → ``…/<name>.idf?context=animation&clip=<clip>``."""
    return "%s?context=animation&clip=%s" % (basis_idf, clip_name)


def tabelle_kennung(basis_idf: str) -> str:
    return basis_idf + "?context=animstringtable"


def clip_ressourcen(basis_idf: str, clip_name: str, kurven: Dict[str, Knotenkurve], laenge_s: float,
                    namen: Optional[Sequence[str]] = None):
    """Ressourcen (``modell_import.Ressource``) für Clip und Stringtabelle; Rückgabe (Ressourcen, Clip-Kennung)."""
    from .modell_import import Ressource
    tk = tabelle_kennung(basis_idf)
    temp, syst, alle = clip_erzeugen(clip_name, tk, kurven, laenge_s, namen)
    ck = clip_kennung(basis_idf, clip_name)
    return [Ressource(tk, "ACStringTable", [(b"temp", stringtabelle_erzeugen(alle), FRAGMENT_FLAGS, True)],
                      TYP_UNBEKANNT["ACStringTable"]),
            Ressource(ck, "AnimClip", [(b"temp", temp, FRAGMENT_FLAGS, True), (b"syst", syst, FRAGMENT_FLAGS, True)],
                      TYP_UNBEKANNT["AnimClip"])], ck


# =============================================================================== Modell mit Drehpunkt
def _abtasten(kurve, t: float, n_werte: int):
    zeiten, werte = np.asarray(kurve[0], float), np.asarray(kurve[1], float).reshape(-1, n_werte)
    if len(zeiten) == 1 or t <= zeiten[0]:
        return werte[0]
    if t >= zeiten[-1]:
        return werte[-1]
    i = int(np.searchsorted(zeiten, t)) - 1
    a = (t - zeiten[i]) / (zeiten[i + 1] - zeiten[i])
    if n_werte == 4:                                        # nlerp genügt für die Hüllenschätzung
        q0, q1 = werte[i], werte[i + 1]
        if np.dot(q0, q1) < 0:
            q1 = -q1
        q = (1 - a) * q0 + a * q1
        return q / np.linalg.norm(q)
    return (1 - a) * werte[i] + a * werte[i + 1]


def kurve_matrix(k: Knotenkurve, t: float) -> np.ndarray:
    tr = _abtasten(k.verschiebung, t, 3) if k.verschiebung else k.ruhe_t
    q = _abtasten(k.drehung, t, 4) if k.drehung else k.ruhe_r
    s = _abtasten(k.skalierung, t, 3) if k.skalierung else k.ruhe_s
    return grafik.trs_matrix(tr, q, s)


def modell_animierbar_machen(bau, gelenk: str, kurve: Knotenkurve, laenge_s: float, proben: int = 241):
    """Einfacher Fall für Tests und P1: das ganze Modell eines ``modell_import.Bau`` hängt an **einem** bewegten
    Knoten ``gelenk`` (Scene Root → gelenk → <name>, <name>_x1 …). Drehpunkt ist ``kurve.ruhe_t`` (Modellraum);
    Verschiebungsschlüssel sind Lagen dieses Drehpunkts. Ändert GfxModelRes, ModelProxyRes und die
    MeshKnoten der LODs im Bau; AABB und Hüllkugel umfassen die ganze Bewegung (``proben`` Zeitpunkte)."""
    res = {r.kennung: r for r in bau.ressourcen}
    mk = grafik.modell_kennung(bau.basis)
    m = grafik.GfxModell.aus_bytes(res[mk].daten)
    if any(k.name == gelenk for k in m.knoten):
        raise AnimationFehler("Knotenname %s schon vergeben" % gelenk)
    neu = [m.knoten[0], grafik.ModellKnoten(gelenk, 0, tuple(map(float, kurve.ruhe_t)), tuple(map(float, kurve.ruhe_r)),
                                            tuple(map(float, kurve.ruhe_s)), m.knoten[0].matrix, 0, 1)]
    for k in m.knoten[1:]:
        # Gegenverschiebung wie beim Zandvoort-Turm (mover +43,26 m, Geometrie −43,26 m): die Geometrie bleibt
        # im Modellraum, das Gelenk dreht um ``ruhe_t``.
        tr = tuple(float(k.translation[i] - kurve.ruhe_t[i]) for i in range(3))
        neu.append(grafik.ModellKnoten(k.name, 1, tr, k.rotation, k.skalierung, k.matrix, k.hat_mesh, len(neu)))
    m.knoten = neu
    m.knoten_anzahl = m.namen_anzahl = len(neu)
    # Hüllen über die Bewegung
    lo, hi = np.array(m.aabb_min, float), np.array(m.aabb_max, float)
    ecken = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    gegen = np.eye(4)
    gegen[:3, 3] = -np.asarray(kurve.ruhe_t, float)
    pts = []
    for t in np.linspace(0.0, laenge_s, proben):
        pts.append((ecken @ (kurve_matrix(kurve, float(t)) @ gegen).T)[:, :3])
    pts = np.concatenate(pts)
    m.aabb_min, m.aabb_max = tuple(map(float, pts.min(0))), tuple(map(float, pts.max(0)))
    mitte = (pts.min(0) + pts.max(0)) / 2
    m.kugel = tuple(map(float, mitte)) + (float(np.linalg.norm(pts - mitte, axis=1).max()),)
    res[mk].fragmente[0] = (res[mk].fragmente[0][0], m.to_bytes()) + tuple(res[mk].fragmente[0][2:])
    pk = grafik.proxy_kennung(bau.basis)
    res[pk].fragmente[0] = (res[pk].fragmente[0][0], grafik.ModellProxy.aus_modell(m).to_bytes()) + \
        tuple(res[pk].fragmente[0][2:])
    for li in range(m.lod_anzahl):
        kenn = grafik.mesh_kennung(bau.basis, li)
        mesh = grafik.GfxMesh.aus_bytes(res[kenn].daten)
        for mkn in mesh.knoten:
            mkn.modell_knoten += 1
        res[kenn].fragmente[0] = (res[kenn].fragmente[0][0], mesh.to_bytes()) + tuple(res[kenn].fragmente[0][2:])
    bau.aabb = (m.aabb_min, m.aabb_max)
    bau.kugel = m.kugel
    return bau


# =============================================================================== glTF-Animation (A1, 18.11.2026)
def quaternion_aus_matrix(r: np.ndarray) -> np.ndarray:
    """Drehmatrix 3×3 → Quaternion xyzw (w ≥ 0)."""
    r = np.asarray(r, float)
    spur = r[0, 0] + r[1, 1] + r[2, 2]
    if spur > 0:
        s = math.sqrt(spur + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q)
    q /= np.linalg.norm(q)
    return -q if q[3] < 0 else q


def matrix_zerlegen(m: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Affine 4×4 (Spaltenvektoren, ``grafik.trs_matrix``) → (Verschiebung, Quaternion xyzw, Skalierung).
    Scherung wird verworfen; eine Spiegelung landet als negatives x der Skalierung."""
    m = np.asarray(m, float)
    a = m[:3, :3]
    s = np.linalg.norm(a, axis=0)
    s = np.where(s < 1e-12, 1e-12, s)
    if np.linalg.det(a) < 0:
        s[0] = -s[0]
    return m[:3, 3].copy(), quaternion_aus_matrix(a / s[None, :]), s


def _slerp(q0: np.ndarray, q1: np.ndarray, a: float) -> np.ndarray:
    q0, q1 = np.asarray(q0, float), np.asarray(q1, float)
    d = float(np.dot(q0, q1))
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + a * (q1 - q0)
    else:
        w = math.acos(min(1.0, d))
        q = (math.sin((1 - a) * w) * q0 + math.sin(a * w) * q1) / math.sin(w)
    return q / np.linalg.norm(q)


def kanal_wert(zeiten: np.ndarray, werte: np.ndarray, interpolation: str, t: float, drehung: bool) -> np.ndarray:
    """Wert eines glTF-Animationskanals zur Zeit ``t`` (LINEAR, STEP, CUBICSPLINE wie in der glTF-Spezifikation;
    Drehung LINEAR = slerp). ``werte``: (n, k), bei CUBICSPLINE (3n, k) als Eingangstangente, Wert, Ausgangstangente."""
    n = len(zeiten)
    kubisch = interpolation == "CUBICSPLINE"
    wert = (lambda i: werte[3 * i + 1]) if kubisch else (lambda i: werte[i])
    if t <= zeiten[0] or n == 1:
        v = wert(0)
    elif t >= zeiten[-1]:
        v = wert(n - 1)
    else:
        i = int(np.searchsorted(zeiten, t, side="right")) - 1
        dt = float(zeiten[i + 1] - zeiten[i])
        s = (t - zeiten[i]) / dt if dt > 0 else 0.0
        if interpolation == "STEP":
            v = wert(i)
        elif kubisch:
            p0, b0, a1, p1 = werte[3 * i + 1], werte[3 * i + 2], werte[3 * (i + 1)], werte[3 * (i + 1) + 1]
            v = ((2 * s ** 3 - 3 * s ** 2 + 1) * p0 + (s ** 3 - 2 * s ** 2 + s) * dt * b0
                 + (-2 * s ** 3 + 3 * s ** 2) * p1 + (s ** 3 - s ** 2) * dt * a1)
        elif drehung:
            v = _slerp(wert(i), wert(i + 1), s)
        else:
            v = (1 - s) * wert(i) + s * wert(i + 1)
    v = np.asarray(v, float)
    if drehung:
        v = v / max(np.linalg.norm(v), 1e-12)
    return v


@dataclass
class AnimKnoten:
    """Bewegter Knoten eines Modells. ``kurve``: lokal zum Elternknoten ``eltern`` (None = Modellwurzel);
    ``welt_ruhe``: 4×4-Ruhelage im Modellraum (Geometrie wird mit ihrem Inversen in den Knotenraum gerechnet)."""

    name: str
    eltern: Optional[str]
    welt_ruhe: np.ndarray
    kurve: Knotenkurve


@dataclass
class Animation:
    """Eine Bewegung (ein Clip) eines Modells, Knoten in Eltern-vor-Kind-Reihenfolge."""

    name: str
    laenge_s: float
    knoten: List[AnimKnoten] = field(default_factory=list)
    hinweise: List[str] = field(default_factory=list)

    def weltmatrizen(self, t: float) -> Dict[str, np.ndarray]:
        welt: Dict[str, np.ndarray] = {}
        for k in self.knoten:
            lokal = kurve_matrix(k.kurve, t)
            welt[k.name] = (welt[k.eltern] @ lokal) if k.eltern else lokal
        return welt

    def zeitpunkte(self, proben: int = 121) -> np.ndarray:
        """Gleichmäßige Proben plus alle Schlüsselzeiten (für Hüllen über die ganze Bewegung)."""
        t = [np.linspace(0.0, self.laenge_s, proben)]
        for k in self.knoten:
            for kurve in (k.kurve.verschiebung, k.kurve.drehung, k.kurve.skalierung):
                if kurve is not None:
                    t.append(np.asarray(kurve[0], float))
        return np.unique(np.concatenate(t))

    def schluessel(self) -> int:
        return int(sum(len(c[0]) if c is not None else 1 for k in self.knoten
                       for c in (k.kurve.verschiebung, k.kurve.drehung, k.kurve.skalierung)))


def clipname(text: str) -> str:
    """Clipname wie im Spiel: Kleinbuchstaben, Ziffern, ``_`` (leer → ``loop``)."""
    n = re.sub(r"[^a-z0-9_]+", "_", str(text or "").lower()).strip("_")
    return n[:40] or "loop"
