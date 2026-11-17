"""Eigene 3D-Modelle im Projekt (M1, 16.11.2026): Ablage, Untersuchung, Umsetzung mit Zwischenspeicher, Katalog.

Einordnung und Plan: ``docs/eigene_modelle.md`` (M1), Format und Umsetzung: ``ego/modell_import.py``.
**Noch nicht im Bau und nicht im Mapeditor eingehängt** (das sind M2 und M4); dieses Modul ist der Vertrag dafür.

Projektablage::

    <projekt>/modelle/modelle.json          Tabelle der eigenen Modelle (Schema 1)
    <projekt>/modelle/<name>.glb            LOD 0 (auch .gltf ohne Nebendateien oder .obj)
    <projekt>/modelle/<name>_lod1.glb …     weitere LODs (optional)

Eintrag in ``modelle.json``::

    {"name": "schild", "datei": "schild.glb", "lod_dateien": ["schild_lod1.glb"], "kategorie": "sonstiges",
     "vorne_grad": 270.0, "sichtweite_m": 2500.0, "lod_abstaende": null, "textur_max": 1024, "textur_format": "bc7",
     "normalen": "gl", "hash": "<sha256 der Dateien>", "statistik": {…}}

LODs: entweder eigene Dateien (``lod_dateien``) oder in einer Datei Objekte mit Namensende ``_LOD1``, ``_LOD2`` …
(Objekte ohne Endung sind LOD 0). Im Spiel heißt das Modell ``usr_<name>``; Kennungen tragen den Streckencode des
Trägers (``portimao``) und laufen damit durch die Umbenennung des Baus (zu prüfen in M2).

Meldungen an den Nutzer sind englisch (English First), deutsche Fassung in ``meldungen.csv`` des Mapeditors.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import time
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ego import modell_import as M

ORDNER, DATEI, SCHEMA = "modelle", "modelle.json", 1
#: Vorsatz der Modellnamen im Spiel (trennt eigene Modelle von denen des Spiels)
PRAEFIX = "usr_"
ENDUNGEN = (".glb", ".gltf", ".obj")
#: Streckencode des Trägers; der Bau benennt alle Kennungen auf den Zielcode um
TRAEGER_CODE = "portimao"
#: Bevorzugtes Vorlagenmaterial im Träger (object_opaque; in M0/M0b im Spiel belegt). Von ihm bleiben nur Shader und
#: Konstanten, alle Karten kommen aus der Umsetzung.
VORLAGE_MATERIAL = "core_lights_start_a_01.material"
KATEGORIEN = ("baum", "busch", "palme", "gebaeude", "zelt", "moebel", "flagge", "licht", "sonstiges")
TEXTUR_FORMATE = {"bc7": M.FORMAT_DIFFUS, "bc1": M.FORMAT_DIFFUS_BC1}
STANDARD = {"kategorie": "sonstiges", "vorne_grad": 270.0, "sichtweite_m": 2500.0, "lod_abstaende": None,
            "textur_max": 1024, "textur_format": "bc7", "normalen": "gl", "kollision": "keine"}
#: Kollision je Modell (M5b, strecke/kollision.py): ``keine`` oder ``quader`` (Grundriss-Box als SCON-Flächen in TRN~)
KOLLISION_ARTEN = ("keine", "quader")
#: Plausible Größe eines Kulissenobjekts (größte Ausdehnung in Metern); außerhalb meist ein Einheitenfehler
GROESSE_MIN_M, GROESSE_MAX_M = 0.05, 500.0
#: Höchstens so viele Umsetzungen bleiben im Zwischenspeicher (älteste zuerst gelöscht)
CACHE_EINTRAEGE = 60
_LOD_NAME = re.compile(r"_lod(\d)(?:_\d+)?$", re.IGNORECASE)


class ModellFehler(ValueError):
    """Projektablage oder Modell ungültig."""


# =============================================================================== Namen und Kennungen
def modellname(text: str) -> str:
    """Gültiger Modellname aus einem Dateinamen oder Wunschnamen: Kleinbuchstaben, Ziffern, ``_``; höchstens 40
    Zeichen, beginnt mit einem Buchstaben. Ein führendes ``usr_`` entfällt (es wird im Spiel wieder vorangestellt)."""
    name = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    if name.startswith(PRAEFIX):
        name = name[len(PRAEFIX):]
    name = name[:40].strip("_")
    if not name:
        raise ModellFehler("Model name %r has no usable characters (letters, digits, _)" % text)
    if not name[0].isalpha():
        name = ("m_" + name)[:40]
    return name


def wurzel(code: str = TRAEGER_CODE) -> str:
    return "eaid://environment_package/tracks/%s/objects" % code


def kennung(name: str, code: str = TRAEGER_CODE) -> str:
    """Basis-Kennung ohne ``.idf`` (Feld ``modell`` in Katalog und ``umgebung.json``)."""
    return "%s/idf/%s%s" % (wurzel(code), PRAEFIX, name)


# =============================================================================== Projektablage
def ordner(projekt: str) -> str:
    return os.path.join(os.path.abspath(projekt), ORDNER)


def tabelle_pfad(projekt: str) -> str:
    return os.path.join(ordner(projekt), DATEI)


def _eintrag_pruefen(e: dict) -> dict:
    if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not isinstance(e.get("datei"), str):
        raise ModellFehler("modelle.json: every model needs name and datei")
    if modellname(e["name"]) != e["name"]:
        raise ModellFehler("modelle.json: model name %r is invalid" % e["name"])
    aus = dict(STANDARD)
    aus.update({k: v for k, v in e.items() if v is not None or k == "lod_abstaende"})
    aus["lod_dateien"] = list(e.get("lod_dateien") or [])
    for d in [aus["datei"]] + aus["lod_dateien"]:
        if os.path.basename(d) != d or os.path.splitext(d)[1].lower() not in ENDUNGEN:
            raise ModellFehler("modelle.json: file %r must be a .glb, .gltf or .obj file in the modelle folder" % d)
    if aus["kategorie"] not in KATEGORIEN:
        raise ModellFehler("Model %s: unknown category %r" % (aus["name"], aus["kategorie"]))
    if aus["textur_format"] not in TEXTUR_FORMATE:
        raise ModellFehler("Model %s: texture format must be bc7 or bc1 (%r)" % (aus["name"], aus["textur_format"]))
    if aus["normalen"] not in ("gl", "dx"):
        raise ModellFehler("Model %s: normal map convention must be gl or dx (%r)" % (aus["name"], aus["normalen"]))
    if aus["kollision"] not in KOLLISION_ARTEN:
        raise ModellFehler("Model %s: collision must be keine or quader (%r)" % (aus["name"], aus["kollision"]))
    for feld in ("vorne_grad", "sichtweite_m", "textur_max"):
        w = aus[feld]
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(float(w)):
            raise ModellFehler("Model %s: field %s must be a finite number (%r)" % (aus["name"], feld, w))
    if not 10.0 <= float(aus["sichtweite_m"]) <= 20000.0:
        raise ModellFehler("Model %s: view distance must be 10 to 20000 m (%s)" % (aus["name"], aus["sichtweite_m"]))
    if int(aus["textur_max"]) not in (64, 128, 256, 512, 1024, 2048):
        raise ModellFehler("Model %s: largest texture size must be a power of two from 64 to 2048 (%s)" % (
            aus["name"], aus["textur_max"]))
    aus["vorne_grad"], aus["sichtweite_m"], aus["textur_max"] = float(aus["vorne_grad"]) % 360.0, float(aus["sichtweite_m"]), int(aus["textur_max"])
    if aus["lod_abstaende"] is not None:
        aus["lod_abstaende"] = [float(x) for x in aus["lod_abstaende"]]
    return aus


def laden(projekt: str) -> dict:
    """Tabelle der eigenen Modelle (``{"schema": 1, "modelle": […]}``); ohne Datei leer."""
    pfad = tabelle_pfad(projekt)
    if not os.path.exists(pfad):
        return {"schema": SCHEMA, "modelle": []}
    try:
        with open(pfad, encoding="utf-8-sig") as f:
            roh = json.load(f)
    except (OSError, ValueError) as exc:
        raise ModellFehler("modelle.json cannot be read: %s" % exc)
    if not isinstance(roh, dict) or roh.get("schema") != SCHEMA or not isinstance(roh.get("modelle"), list):
        raise ModellFehler("modelle.json: unknown layout (expected schema %d)" % SCHEMA)
    modelle = [_eintrag_pruefen(e) for e in roh["modelle"]]
    namen = [e["name"] for e in modelle]
    doppelt = sorted({n for n in namen if namen.count(n) > 1})
    if doppelt:
        raise ModellFehler("modelle.json: model name %s is used twice" % doppelt[0])
    return {"schema": SCHEMA, "modelle": modelle}


def speichern(projekt: str, daten: dict) -> str:
    os.makedirs(ordner(projekt), exist_ok=True)
    pfad = tabelle_pfad(projekt)
    aus = {"schema": SCHEMA, "modelle": sorted((_eintrag_pruefen(e) for e in daten["modelle"]), key=lambda e: e["name"])}
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(aus, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, pfad)
    return pfad


def eintrag(daten: dict, name: str) -> dict:
    for e in daten["modelle"]:
        if e["name"] == name:
            return e
    raise ModellFehler("Model %s is not in this project" % name)


def dateien(projekt: str, e: dict) -> List[str]:
    """Absolute Pfade der Modelldateien, LOD 0 zuerst."""
    return [os.path.join(ordner(projekt), d) for d in [e["datei"]] + list(e.get("lod_dateien") or [])]


def datei_hash(pfade: Sequence[str]) -> str:
    h = hashlib.sha256()
    for p in pfade:
        with open(p, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def _eigenstaendig(pfad: str) -> None:
    """Eine ``.gltf`` darf im Projekt keine Nebendateien brauchen (die Ablage kopiert genau eine Datei)."""
    if not pfad.lower().endswith(".gltf"):
        return
    try:
        with open(pfad, encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError) as exc:
        raise ModellFehler("%s cannot be read: %s" % (os.path.basename(pfad), exc))
    for b in list(j.get("buffers", [])) + list(j.get("images", [])):
        uri = b.get("uri")
        if uri is not None and not uri.startswith("data:"):
            raise ModellFehler("%s refers to the separate file %s, export as glTF Binary (.glb) instead" % (
                os.path.basename(pfad), uri))


def hinzufuegen(projekt: str, quelle: str, name: Optional[str] = None, lods: Sequence[str] = (), ersetzen: bool = False,
                **optionen) -> dict:
    """Kopiert ein Modell (und weitere LOD-Dateien) in die Projektablage, untersucht es und trägt es ein.

    ``optionen``: Felder aus ``STANDARD`` (kategorie, vorne_grad, sichtweite_m, lod_abstaende, textur_max,
    textur_format, normalen, kollision). Ein unlesbares Modell wird nicht eingetragen (``ModellFehler``/``ModellImportFehler``).
    Rückgabe: der Eintrag samt ``statistik``."""
    unbekannt = sorted(set(optionen) - set(STANDARD))
    if unbekannt:
        raise ModellFehler("Unknown model option %s" % unbekannt[0])
    quellen = [quelle] + list(lods)
    for q in quellen:
        if os.path.splitext(q)[1].lower() not in ENDUNGEN:
            raise ModellFehler("%s is not a .glb, .gltf or .obj file" % os.path.basename(q))
        if not os.path.isfile(q):
            raise ModellFehler("File not found: %s" % q)
        _eigenstaendig(q)
    name = modellname(name or os.path.splitext(os.path.basename(quelle))[0])
    daten = laden(projekt)
    alt = next((e for e in daten["modelle"] if e["name"] == name), None)
    if alt is not None and not ersetzen:
        raise ModellFehler("Model %s already exists in this project" % name)
    statistik = untersuchen(quellen, **{k: optionen[k] for k in ("textur_max", "lod_abstaende", "sichtweite_m") if k in optionen})
    if statistik["fehler"]:
        raise ModellFehler(statistik["fehler"][0])
    ziel = ordner(projekt)
    os.makedirs(ziel, exist_ok=True)
    namen = [name + os.path.splitext(quelle)[1].lower()] + [
        "%s_lod%d%s" % (name, i + 1, os.path.splitext(q)[1].lower()) for i, q in enumerate(lods)]
    if alt is not None:
        for d in [alt["datei"]] + alt["lod_dateien"]:
            if d not in namen and os.path.exists(os.path.join(ziel, d)):
                os.remove(os.path.join(ziel, d))
    for q, d in zip(quellen, namen):
        if os.path.abspath(q) != os.path.abspath(os.path.join(ziel, d)):
            shutil.copyfile(q, os.path.join(ziel, d))
    e = dict(STANDARD)
    e.update(optionen)
    e.update(name=name, datei=namen[0], lod_dateien=namen[1:])
    e["hash"] = datei_hash([os.path.join(ziel, d) for d in namen])
    statistik["dateien"] = list(namen)
    e["statistik"] = statistik
    e = _eintrag_pruefen(e)
    daten["modelle"] = [x for x in daten["modelle"] if x["name"] != name] + [e]
    speichern(projekt, daten)
    return e


def entfernen(projekt: str, name: str, dateien_loeschen: bool = True) -> dict:
    daten = laden(projekt)
    e = eintrag(daten, name)
    daten["modelle"] = [x for x in daten["modelle"] if x["name"] != name]
    speichern(projekt, daten)
    if dateien_loeschen:
        for p in dateien(projekt, e):
            if os.path.exists(p):
                os.remove(p)
    return e


def aendern(projekt: str, name: str, **optionen) -> dict:
    """Felder eines Eintrags ändern (kategorie, vorne_grad, sichtweite_m, lod_abstaende, textur_max, textur_format,
    normalen, kollision); die Statistik wird neu berechnet."""
    unbekannt = sorted(set(optionen) - set(STANDARD))
    if unbekannt:
        raise ModellFehler("Unknown model option %s" % unbekannt[0])
    daten = laden(projekt)
    e = eintrag(daten, name)
    e.update(optionen)
    e = _eintrag_pruefen(e)
    e["statistik"] = untersuchen(dateien(projekt, e), textur_max=e["textur_max"], lod_abstaende=e["lod_abstaende"],
                                 sichtweite_m=e["sichtweite_m"])
    e["hash"] = datei_hash(dateien(projekt, e))
    daten["modelle"] = [x for x in daten["modelle"] if x["name"] != name] + [e]
    speichern(projekt, daten)
    return e


# =============================================================================== Laden und Untersuchen
def lods_trennen(szene: M.Szene) -> List[M.Szene]:
    """Objekte mit Namensende ``_LOD1``, ``_LOD2`` … einer Datei auf LOD-Szenen verteilen (ohne Endung: LOD 0)."""
    stufen: Dict[int, List[M.Teil]] = {}
    for t in szene.teile:
        m = _LOD_NAME.search(t.name)
        stufen.setdefault(int(m.group(1)) if m else 0, []).append(t)
    if list(stufen) == [0]:
        return [szene]
    if sorted(stufen) != list(range(len(stufen))):
        raise ModellFehler("LOD objects must be numbered without gaps (_LOD1, _LOD2 …), found %s" % ", ".join(
            "LOD %d" % i for i in sorted(stufen)))
    return [M.Szene(stufen[i], szene.materialien, szene.hinweise if i == 0 else [], szene.quelle) for i in sorted(stufen)]


def szenen_laden(pfade: Sequence[str]) -> List[M.Szene]:
    """Modelldateien lesen (LOD 0 zuerst). Eine einzelne Datei darf die LODs als Objekte ``…_LOD<n>`` enthalten."""
    szenen = [M.laden(p) for p in pfade]
    return lods_trennen(szenen[0]) if len(szenen) == 1 else szenen


def _bildgroesse(bild, textur_max: int) -> Optional[List[int]]:
    if bild is None:
        return None
    h, w = np.asarray(bild).shape[:2]
    return [int(x) for x in M.zielgroesse(w, h, textur_max)]


def untersuchen(pfade: Sequence[str], textur_max: int = STANDARD["textur_max"], lod_abstaende=None,
                sichtweite_m: float = STANDARD["sichtweite_m"]) -> dict:
    """Kennzahlen und Meldungen zu einem Modell, ohne Träger und ohne etwas zu schreiben (JSON-fähig).

    ``fehler``: das Modell lässt sich nicht umsetzen; ``warnungen``: fragwürdig (Größe, Dreieckszahl);
    ``hinweise``: was die Umsetzung anpasst oder auslässt."""
    aus = {"dateien": [os.path.basename(p) for p in pfade], "fehler": [], "warnungen": [], "hinweise": []}
    try:
        szenen = szenen_laden(pfade)
        bau = M.modell_bauen(szenen, kennung("pruefung") + ".idf", mit_bindung=False, streng=False,
                             sichtweite_m=sichtweite_m, lod_abstaende=lod_abstaende)
    except (M.ModellImportFehler, ModellFehler, OSError, ValueError, KeyError, IndexError) as exc:
        aus["fehler"].append(str(exc) or exc.__class__.__name__)
        return aus
    hinweise = aus["hinweise"]
    hinweise.extend(bau.hinweise)
    b = bau.bericht
    lo, hi = np.array(b["aabb_min"]), np.array(b["aabb_max"])
    groesse = hi - lo
    aus.update(lods=b["lods"], dreiecke=b["dreiecke"], vertices=b["vertices"], teilnetze=b["teilnetze"],
               aabb_min=[round(float(x), 4) for x in lo], aabb_max=[round(float(x), 4) for x in hi],
               groesse_m=[round(float(x), 3) for x in groesse],
               radius_m=round(float(max(math.hypot(x, z) for x in (lo[0], hi[0]) for z in (lo[2], hi[2]))), 3),
               unterkante_m=round(float(lo[1]), 3), lod_abstaende=b["lod_abstaende"])
    materialien, speicher = [], 0
    benutzt = [m for m in b["materialslots"].values()]
    for name in benutzt:
        q = next((s.materialien[name] for s in szenen if name in s.materialien), M.QuellMaterial(name))
        bild, normal = _bildgroesse(q.bild_basisfarbe, textur_max), _bildgroesse(q.bild_normal, textur_max)
        orm = _bildgroesse(q.bild_orm if q.bild_orm is not None else q.bild_verdeckung, textur_max)
        for g in (bild, normal):
            if g:
                speicher += int(g[0] * g[1] * 4 / 3)          # BC7/BC5/BC3: 1 Byte je Pixel, Mip-Kette + 1/3
        if orm:
            speicher += int(orm[0] * orm[1] * 2 / 3)          # BC1: ein halbes Byte je Pixel
        # M5a: Transparenz, Maske, doppelseitig, Metall und Rauheit setzt die Umsetzung um (kein Hinweis mehr);
        # nur ein Träger ohne object_alpha/object_mask führt dort noch zu einem Hinweis.
        materialien.append({"name": name, "basisfarbe": [round(float(x), 4) for x in q.basisfarbe], "bild": bild,
                            "normalenkarte": normal, "alpha": q.alpha, "doppelseitig": bool(q.doppelseitig),
                            "alpha_schwelle": round(float(q.alpha_schwelle), 4), "metall": round(float(q.metall), 4),
                            "rauheit": round(float(q.rauheit), 4), "oberflaechenkarte": orm})
        if q.alpha not in ("OPAQUE", "BLEND", "MASK"):
            hinweise.append("Material %s: unknown alpha mode %s, the material is drawn opaque" % (name, q.alpha))
    aus.update(materialien=materialien, textur_bytes=int(speicher), geometrie_bytes=int(b["bytes"]))
    warnungen = aus["warnungen"]
    if b["dreiecke"][0] > M.DREIECKE_FEHLER:
        aus["fehler"].append("%d triangles in LOD 0 (largest model of the game: 254,000)" % b["dreiecke"][0])
    if float(groesse.max()) < GROESSE_MIN_M:
        warnungen.append("Model is only %.3f m in size, check the unit (1 Blender unit = 1 m)" % float(groesse.max()))
    elif float(groesse.max()) > GROESSE_MAX_M:
        warnungen.append("Model is %.0f m in size, check the unit (1 Blender unit = 1 m)" % float(groesse.max()))
    if lo[1] > 0.05:
        hinweise.append("Model starts %.2f m above its origin. With ground mode Automatic the build puts its lower edge on the ground" % float(lo[1]))
    mitte = (lo + hi) / 2
    if max(abs(float(mitte[0])), abs(float(mitte[2]))) > max(1.0, 0.75 * float(max(groesse[0], groesse[2]))):
        warnungen.append("Origin is %.1f m beside the model, rotating and placing use the origin" % float(math.hypot(mitte[0], mitte[2])))
    return aus


def aktualisieren(projekt: str) -> dict:
    """Statistik aller Einträge neu berechnen, deren Dateien sich geändert haben; fehlende Dateien bleiben als
    Fehler in der Statistik stehen. Rückgabe: die Tabelle."""
    daten = laden(projekt)
    geaendert = False
    for e in daten["modelle"]:
        pfade = dateien(projekt, e)
        fehlt = [os.path.basename(p) for p in pfade if not os.path.isfile(p)]
        if fehlt:
            neu, h = {"dateien": [os.path.basename(p) for p in pfade], "fehler": ["File not found: %s" % fehlt[0]],
                      "warnungen": [], "hinweise": []}, None
        else:
            h = datei_hash(pfade)
            if h == e.get("hash") and e.get("statistik"):
                continue
            neu = untersuchen(pfade, textur_max=e["textur_max"], lod_abstaende=e["lod_abstaende"], sichtweite_m=e["sichtweite_m"])
        if neu != e.get("statistik") or h != e.get("hash"):
            e["statistik"], e["hash"], geaendert = neu, h, True
    if geaendert:
        speichern(projekt, daten)
    return daten


# =============================================================================== Katalog
def katalogeintraege(projekt: str, code: str = TRAEGER_CODE, auffrischen: bool = True) -> List[dict]:
    """Katalogeinträge der eigenen Modelle im Format von ``umgebung.katalog`` (Feld ``quelle``: ``projekt``), dazu
    ``vorne_grad``/``vorne_quelle`` wie im Katalog des Mapeditors und ``datei``, ``dreiecke``, ``hinweise``.
    Modelle mit Fehlern fehlen im Katalog (sie stehen mit ``fehler`` in der Tabelle)."""
    daten = aktualisieren(projekt) if auffrischen else laden(projekt)
    aus = []
    for e in daten["modelle"]:
        s = e.get("statistik") or {}
        if s.get("fehler") or "aabb_min" not in s:
            continue
        aus.append({"modell": kennung(e["name"], code), "name": PRAEFIX + e["name"], "familie": "projekt",
                    "kategorie": e["kategorie"], "groesse_m": s["groesse_m"], "radius_m": s["radius_m"],
                    "aabb_min": [round(x, 3) for x in s["aabb_min"]], "aabb_max": [round(x, 3) for x in s["aabb_max"]],
                    "quelle": "projekt", "strecken": [], "im_traeger": False, "vorne_grad": e["vorne_grad"],
                    "vorne_quelle": "projekt", "datei": e["datei"], "dreiecke": s["dreiecke"][0],
                    "kollision": e["kollision"],
                    "hinweise": list(s.get("warnungen", [])) + list(s.get("hinweise", []))})
    return aus


# =============================================================================== Umsetzung mit Zwischenspeicher
def zwischenspeicher_ordner() -> str:
    basis = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(basis, "ego-werkzeuge", "modelle")


class Vorlage(bytes):
    """Bytes des deckenden Vorlagenmaterials, dazu die Vorlagen für Transparenz und Maske (M5a).

    Eine ``bytes``-Unterklasse, damit alle Aufrufer von ``vorlage_material``/``umsetzen`` (Bau, Kommandozeile,
    Paketprüfung) unverändert bleiben: ``weitere`` = {``alpha``/``mask``: Bytes}, ``kennungen`` = {``opaque``,
    ``alpha``, ``mask``: Kennung im Träger}. Einfache ``bytes`` gelten als Vorlage ohne Transparenz."""

    weitere: Dict[str, bytes] = {}
    kennungen: Dict[str, str] = {}

    @classmethod
    def aus(cls, roh: bytes, weitere: Optional[Dict[str, bytes]] = None, kennungen: Optional[Dict[str, str]] = None
            ) -> "Vorlage":
        v = cls(roh)
        v.weitere, v.kennungen = dict(weitere or {}), dict(kennungen or {})
        return v


def vorlage_material(archiv) -> Tuple[str, bytes]:
    """Vorlagenmaterial aus dem Träger: ``VORLAGE_MATERIAL``, sonst ein unauffälliges ``object_opaque``-Material.
    Die Bytes sind eine ``Vorlage`` und tragen die Vorlagen für Transparenz (``object_alpha``) und Maske
    (``object_mask``), soweit der Träger sie hat (``modell_import.vorlagen_alpha``)."""
    k = next((r.identifier for r in archiv if r.resource_type == "GfxMaterialRes"
              and r.identifier.endswith("/" + VORLAGE_MATERIAL)), None) or M.vorlage_material_finden(archiv)
    if k is None:
        raise ModellFehler("The carrier has no suitable template material (object_opaque with three maps)")
    kennungen, weitere = M.vorlagen_alpha(archiv)
    return k, Vorlage.aus(archiv.find(k).data(), weitere, dict(kennungen, opaque=k))


def _packen(um: M.Umsetzung, pfad: str) -> None:
    """Umsetzung als ZIP (ohne Kompression): ``index.json`` + ein Eintrag je Fragment. Kein Pickle (Datei ist Daten)."""
    index = {"name": um.name, "basis": um.basis, "bericht": um.bericht, "hinweise": um.hinweise, "ressourcen": []}
    tmp = pfad + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for i, r in enumerate(um.ressourcen):
            fragmente = []
            for j, (fname, daten, flags, komprimieren) in enumerate(r.fragmente):
                datei = "%d_%d.bin" % (i, j)
                z.writestr(datei, daten)
                fragmente.append({"name": fname.hex(), "flags": int(flags), "komprimieren": bool(komprimieren), "datei": datei})
            index["ressourcen"].append({"kennung": r.kennung, "typ": r.typ, "unknown": int(r.unknown), "fragmente": fragmente})
        z.writestr("index.json", json.dumps(index, ensure_ascii=False))
    os.replace(tmp, pfad)


def _entpacken(pfad: str) -> M.Umsetzung:
    with zipfile.ZipFile(pfad) as z:
        index = json.loads(z.read("index.json").decode("utf-8"))
        ressourcen = [M.Ressource(r["kennung"], r["typ"],
                                  [(bytes.fromhex(f["name"]), z.read(f["datei"]), int(f["flags"]), bool(f["komprimieren"]))
                                   for f in r["fragmente"]], int(r["unknown"])) for r in index["ressourcen"]]
    return M.Umsetzung(index["name"], index["basis"], ressourcen, index["bericht"], list(index["hinweise"]), None)


def _cache_aufraeumen(ordner_: str) -> None:
    try:
        alle = sorted((os.path.join(ordner_, f) for f in os.listdir(ordner_) if f.endswith(".zip")), key=os.path.getmtime)
        for p in alle[:-CACHE_EINTRAEGE]:
            os.remove(p)
    except OSError:
        pass


def schluessel(pfade: Sequence[str], e: dict, code: str, vorlage_roh: bytes) -> str:
    weitere = getattr(vorlage_roh, "weitere", None) or {}
    teile = [M.UMSETZUNG_VERSION, datei_hash(pfade), e["name"], code, hashlib.sha256(vorlage_roh).hexdigest(),
             e["sichtweite_m"], e["lod_abstaende"], e["textur_max"], e["textur_format"], e["normalen"],
             {art: hashlib.sha256(roh).hexdigest() for art, roh in sorted(weitere.items())}]
    return hashlib.sha256(json.dumps(teile, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def umsetzen(projekt: str, e: dict, vorlage_roh: bytes, code: str = TRAEGER_CODE, cache: Optional[str] = "standard"
             ) -> M.Umsetzung:
    """Alle Spielressourcen eines Projektmodells (``modell_import.modell_umsetzen``), mit Zwischenspeicher je
    Dateiinhalt, Optionen, Streckencode und Vorlagenmaterial. ``cache``: Ordner, ``"standard"`` =
    ``%LOCALAPPDATA%\\ego-werkzeuge\\modelle``, None = ohne. ``bericht["aus_zwischenspeicher"]`` sagt, woher das
    Ergebnis kommt; aus dem Zwischenspeicher fehlt ``bau`` (Eingabegeometrie für ``modell_import.pruefen``)."""
    e = _eintrag_pruefen(e)
    pfade = dateien(projekt, e)
    fehlt = [p for p in pfade if not os.path.isfile(p)]
    if fehlt:
        raise ModellFehler("File not found: %s" % fehlt[0])
    cache = zwischenspeicher_ordner() if cache == "standard" else cache
    pfad = os.path.join(cache, schluessel(pfade, e, code, vorlage_roh) + ".zip") if cache else None
    if pfad and os.path.exists(pfad):
        try:
            um = _entpacken(pfad)
            um.bericht["aus_zwischenspeicher"] = True
            os.utime(pfad, None)
            return um
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            pass
    t0 = time.time()
    um = M.modell_umsetzen(szenen_laden(pfade), PRAEFIX + e["name"], wurzel(code), vorlage_roh,
                           sichtweite_m=e["sichtweite_m"], lod_abstaende=e["lod_abstaende"], textur_max=e["textur_max"],
                           textur_format=TEXTUR_FORMATE[e["textur_format"]], normalen=e["normalen"],
                           vorlagen=getattr(vorlage_roh, "weitere", None))
    kennungen = getattr(vorlage_roh, "kennungen", None)
    if kennungen:
        um.bericht["vorlagen"] = dict(kennungen)
    um.bericht["laufzeit_s"] = round(time.time() - t0, 3)
    if pfad:
        try:
            os.makedirs(cache, exist_ok=True)
            _packen(um, pfad)
            _cache_aufraeumen(cache)
        except OSError:
            pass
    um.bericht["aus_zwischenspeicher"] = False
    return um


# =============================================================================== Vorschau
def _unterteilung(n: int) -> np.ndarray:
    """Baryzentrische Gewichte (n², 3, 3) der n² kleinen Dreiecke eines in n Schritten unterteilten Dreiecks."""
    aus = []
    for i in range(n):
        for j in range(n - i):
            aus.append([(n - i - j, i, j), (n - i - j - 1, i + 1, j), (n - i - j - 1, i, j + 1)])
            if i + j <= n - 2:
                aus.append([(n - i - j - 1, i + 1, j), (n - i - j - 2, i + 1, j + 1), (n - i - j - 1, i, j + 1)])
    return np.array(aus, np.float64) / n


def vorschau_bild(szenen, groesse: int = 256, azimut_grad: float = 35.0, hoehe_grad: float = 25.0,
                  hintergrund: Tuple[int, int, int, int] = (38, 40, 44, 255), max_dreiecke: int = 150000):
    """Schrägbild des Modells von vorn rechts oben (Vorderseite = +Z), flach schattiert, als PIL-Bild (RGBA).

    Orthographisch, Maler-Algorithmus, nur Vorderseiten (wie das Spiel). Farbe je Dreieck: Basisfarbe; Flächen mit
    Bild werden für die Vorschau fein unterteilt und bekommen die Texturfarbe am Schwerpunkt der Teilstücke. Sehr
    große Modelle werden für das Bild ausgedünnt."""
    from PIL import Image, ImageDraw
    if isinstance(szenen, M.Szene):
        szenen = [szenen]
    sz = szenen[0]
    az, el = math.radians(azimut_grad), math.radians(hoehe_grad)
    d = np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)])     # Modell → Kamera
    rechts = np.cross([0.0, 1.0, 0.0], d)
    rechts /= np.linalg.norm(rechts)
    oben = np.cross(d, rechts)
    licht = d + 0.6 * oben + 0.35 * rechts
    licht /= np.linalg.norm(licht)
    kante = int(groesse) * 2
    bild = Image.new("RGBA", (kante, kante), hintergrund)
    alle = np.concatenate([np.asarray(t.positionen, np.float64).reshape(-1, 3) for t in sz.teile]) if sz.teile else np.zeros((0, 3))
    if not len(alle):
        return bild.resize((groesse, groesse), Image.Resampling.LANCZOS)
    flach = np.stack([alle @ rechts, alle @ oben], axis=1)
    lo, hi = flach.min(0), flach.max(0)
    massstab = (kante * 0.88) / max(float((hi - lo).max()), 1e-9)
    mitte = (lo + hi) / 2
    gesamt = sum(len(np.asarray(t.dreiecke).reshape(-1, 3)) for t in sz.teile)
    schritt = max(1, int(math.ceil(gesamt / max(1, max_dreiecke))))
    ecken_l, tiefe_l, farbe_l = [], [], []
    for t in sz.teile:
        pos = np.asarray(t.positionen, np.float64).reshape(-1, 3)
        tri = np.asarray(t.dreiecke, np.int64).reshape(-1, 3)[::schritt]
        e = pos[tri]
        n = np.cross(e[:, 1] - e[:, 0], e[:, 2] - e[:, 0])
        laenge = np.linalg.norm(n, axis=1)
        gut = laenge > 1e-12
        gut[gut] &= (n[gut] / laenge[gut, None]) @ d > 1e-6                   # nur Vorderseiten
        e, tri, n = e[gut], tri[gut], n[gut] / laenge[gut, None]
        if not len(e):
            continue
        q = sz.materialien.get(t.material) or M.QuellMaterial(t.material)
        basis = np.clip(np.array(q.basisfarbe[:3], np.float64), 0.0, 1.0) ** (1 / 2.2)
        hell = 0.38 + 0.62 * np.clip(n @ licht, 0.0, 1.0)
        if q.bild_basisfarbe is None or t.uv0 is None:
            ecken_l.append(np.stack([e @ rechts, e @ oben], axis=2))
            tiefe_l.append((e @ d).mean(axis=1))
            farbe_l.append(np.clip(basis[None, :] * hell[:, None], 0.0, 1.0))
            continue
        tex = np.asarray(q.bild_basisfarbe, np.uint8)
        uv = np.asarray(t.uv0, np.float64)[tri]
        px = np.stack([e @ rechts, e @ oben], axis=2) * massstab
        laengste = np.max([np.linalg.norm(px[:, a] - px[:, b], axis=1) for a, b in ((0, 1), (1, 2), (2, 0))], axis=0)
        teile = np.clip(np.ceil(laengste / 6.0), 1, 40).astype(int)             # Teilstücke von etwa 6 Bildpunkten
        if int((teile ** 2).sum()) > 400000:
            teile = np.maximum(1, (teile * math.sqrt(400000.0 / float((teile ** 2).sum()))).astype(int))
        for k in np.unique(teile):
            w = _unterteilung(int(k))
            wahl = teile == k
            ek = np.einsum("sab,tbc->tsac", w, e[wahl]).reshape(-1, 3, 3)
            uvk = np.einsum("sab,tbc->tsac", w, uv[wahl]).reshape(-1, 3, 2).mean(axis=1)
            x = np.clip(((uvk[:, 0] % 1.0) * tex.shape[1]).astype(int), 0, tex.shape[1] - 1)
            y = np.clip(((uvk[:, 1] % 1.0) * tex.shape[0]).astype(int), 0, tex.shape[0] - 1)
            ecken_l.append(np.stack([ek @ rechts, ek @ oben], axis=2))
            tiefe_l.append((ek @ d).mean(axis=1))
            farbe_l.append(np.clip(basis[None, :] * tex[y, x, :3] / 255.0 * np.repeat(hell[wahl], len(w))[:, None], 0.0, 1.0))
    if not ecken_l:
        return bild.resize((groesse, groesse), Image.Resampling.LANCZOS)
    ecken, tiefe, farbe = np.concatenate(ecken_l), np.concatenate(tiefe_l), np.concatenate(farbe_l)
    px = np.empty_like(ecken)
    px[..., 0] = kante / 2 + (ecken[..., 0] - mitte[0]) * massstab
    px[..., 1] = kante / 2 - (ecken[..., 1] - mitte[1]) * massstab
    zeichner = ImageDraw.Draw(bild)
    rgb = (farbe * 255).astype(np.uint8)
    for i in np.argsort(tiefe, kind="stable"):
        p = px[i]
        zeichner.polygon([(float(p[0, 0]), float(p[0, 1])), (float(p[1, 0]), float(p[1, 1])), (float(p[2, 0]), float(p[2, 1]))],
                         fill=(int(rgb[i, 0]), int(rgb[i, 1]), int(rgb[i, 2]), 255))
    return bild.resize((groesse, groesse), Image.Resampling.LANCZOS)


def vorschau(pfade: Sequence[str], ziel: Optional[str] = None, groesse: int = 256) -> bytes:
    """Vorschaubild eines Modells als PNG-Bytes; mit ``ziel`` auch als Datei."""
    puffer = io.BytesIO()
    vorschau_bild(szenen_laden(pfade), groesse).save(puffer, "PNG")
    if ziel:
        os.makedirs(os.path.dirname(os.path.abspath(ziel)) or ".", exist_ok=True)
        with open(ziel, "wb") as f:
            f.write(puffer.getvalue())
    return puffer.getvalue()
