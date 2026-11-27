"""Modellbibliothek über alle Projekte (20.11.2026): eigene 3D-Modelle einmal ablegen, in jedem Projekt übernehmen.

Ablage: ``%LOCALAPPDATA%\\Mapeditor\\modellbibliothek\\<name>\\`` mit den GLB/glTF/OBJ-Dateien (Hauptdatei und LODs)
und ``eintrag.json`` (Name, Dateien, Einstellungen wie in ``<projekt>/modelle/modelle.json``, Kurzstatistik).
Reine Dateilogik ohne Qt und ohne Werkzeugkasten; ins Projekt übernimmt der normale Modellimport
(``eigene_modelle.hinzufuegen``) mit den gespeicherten Einstellungen.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from pathlib import Path

#: Einstellungen eines Modells, die mitgehen (Teilmenge von ``eigene_modelle.STANDARD``)
OPTIONEN = ("kategorie", "vorne_grad", "sichtweite_m", "lod_abstaende", "textur_max", "textur_format", "normalen",
            "kollision", "animation", "effekte")
EINTRAG = "eintrag.json"
_NAME = re.compile(r"^[a-z0-9_]{1,40}$")


class BibliothekFehler(ValueError):
    """Bibliothek oder Eintrag ungültig."""


def ordner(basis=None) -> Path:
    """Bibliotheksordner (wird angelegt). ``basis`` ersetzt ``%LOCALAPPDATA%\\Mapeditor`` (Tests)."""
    if basis is None:
        basis = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Mapeditor"
    pfad = Path(basis) / "modellbibliothek"
    pfad.mkdir(parents=True, exist_ok=True)
    return pfad


def liste(basis=None) -> list:
    """Alle Einträge (sortiert nach Name); unlesbare Ordner werden übersprungen."""
    erg = []
    for unter in sorted(ordner(basis).iterdir()):
        datei = unter / EINTRAG
        if not datei.is_file():
            continue
        try:
            eintrag = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if all((unter / d).is_file() for d in [eintrag.get("datei")] + list(eintrag.get("lod_dateien") or [])):
            eintrag["ordner"] = str(unter)
            erg.append(eintrag)
    return erg


def speichern(projekt, modell_eintrag: dict, basis=None, ersetzen=False) -> dict:
    """Ein eigenes Modell des Projekts (Eintrag aus ``modelle/modelle.json``) in die Bibliothek kopieren."""
    name = str(modell_eintrag.get("name") or "")
    if not _NAME.match(name):
        raise BibliothekFehler("Invalid model name %r" % name)
    quelle = Path(projekt) / "modelle"
    dateien = [modell_eintrag.get("datei")] + list(modell_eintrag.get("lod_dateien") or [])
    if not dateien[0] or not all((quelle / d).is_file() for d in dateien):
        raise BibliothekFehler("Files of model %s are missing in the project" % name)
    ziel = ordner(basis) / name
    if ziel.exists():
        if not ersetzen:
            raise BibliothekFehler("Model %s is already in the library" % name)
        shutil.rmtree(ziel)
    ziel.mkdir(parents=True)
    for d in dateien:
        shutil.copy2(quelle / d, ziel / d)
    stat = modell_eintrag.get("statistik") or {}
    eintrag = {"schema": 1, "name": name, "datei": dateien[0], "lod_dateien": dateien[1:],
               "optionen": {k: modell_eintrag[k] for k in OPTIONEN if k in modell_eintrag},
               "dreiecke": (stat.get("dreiecke") or [0])[0], "groesse_m": stat.get("groesse_m"),
               "animation": bool(stat.get("animation")),
               "effekte": sorted({(m.get("effekt") or {}).get("art") for m in stat.get("materialien") or []
                                  if m.get("effekt")}),
               "gespeichert": datetime.date.today().isoformat()}
    (ziel / EINTRAG).write_text(json.dumps(eintrag, ensure_ascii=False, indent=1), encoding="utf-8")
    eintrag["ordner"] = str(ziel)
    return eintrag


def entfernen(name, basis=None) -> None:
    """Eintrag samt Dateien aus der Bibliothek löschen."""
    if not _NAME.match(str(name)):
        raise BibliothekFehler("Invalid model name %r" % name)
    ziel = ordner(basis) / str(name)
    if ziel.exists():
        shutil.rmtree(ziel)


def import_auftrag(eintrag: dict) -> dict:
    """Pfade und Einstellungen für den Modellimport ins Projekt (``modell_importieren``)."""
    basis = Path(eintrag["ordner"])
    return {"datei": str(basis / eintrag["datei"]), "lods": [str(basis / d) for d in eintrag.get("lod_dateien") or []],
            "name": eintrag["name"], "optionen": dict(eintrag.get("optionen") or {})}
