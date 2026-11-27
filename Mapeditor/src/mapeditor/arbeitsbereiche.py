"""Arbeitsbereiche (21.11.2026): umschaltbare Aufteilungen des Hauptfensters wie in Blender.

Jeder Bereich legt fest, welche Flächen (Seitenleiste, Eigenschaften, unterer Bereich), welche Reiter, welcher Reiter
vorn, welches Werkzeug und ob die 3D-Ansicht neben der Karte zu sehen ist, dazu die Größen der Teiler. Was der Nutzer
in einem Bereich umstellt (Menü Ansicht, Teiler ziehen, Reiter wechseln), merkt sich dieser Bereich
(``QSettings``: ``arbeitsbereich/<schlüssel>``); „Arbeitsbereich zurücksetzen“ stellt die Vorgabe wieder her.
Kartenebenen und Automatiken gelten in allen Bereichen gleich.
"""

from __future__ import annotations

import json

from .ansicht import FEATURES, settings
from .i18n import N_

#: (Schlüssel, Name, Beschreibung)
BEREICHE = (
    ("strecke", N_("Track"), N_("Draw and shape the track: map, properties, height and banking profiles")),
    ("umgebung", N_("Environment"), N_("Place and plant objects: map, catalogue and object properties")),
    ("3d", N_("3D"), N_("Map and 3D view side by side: drive the lap, look around, move objects in 3D")),
    ("bauen", N_("Review"), N_("Checks and analysis before building the package")),
    ("alles", N_("All"), N_("Everything at once (the layout before workspaces)")),
)
SCHLUESSEL = [b[0] for b in BEREICHE]
#: Werkzeuge der Karte je Arbeitsbereich (27.11.2026, eine Leiste: nur diese stehen neben den Bereichen);
#: „runde“ = Runde öffnen/schließen
WERKZEUGE = {"strecke": ("auswahl", "punkte", "nachzeichnen", "kameras", "runde"),
             "umgebung": ("auswahl", "umgebung", "streuen"),
             "3d": ("auswahl",),
             "bauen": ("auswahl",),
             "alles": ("auswahl", "punkte", "umgebung", "streuen", "nachzeichnen", "kameras", "runde")}


def werkzeuge(name: str) -> tuple:
    return WERKZEUGE.get(name, WERKZEUGE["alles"])


def bereich_fuer_werkzeug(modus: str):
    """Arbeitsbereich, in dem das Werkzeug zu Hause ist (für Tastenkürzel aus anderen Bereichen), sonst None."""
    return next((b for b in ("strecke", "umgebung") if modus in WERKZEUGE[b]), None)
NAMEN = {b[0]: b[1] for b in BEREICHE}
BESCHREIBUNG = {b[0]: b[2] for b in BEREICHE}
#: Schalter, die je Arbeitsbereich gelten (Gruppen „Areas“ und „Tabs“ der Ansicht)
SCHALTER = [key for key, group, *_ in FEATURES if group in ("Areas", "Tabs")]

_ALLE_REITER = [k for k in SCHALTER if k.startswith("reiter_")]


def _nur_reiter(*sichtbar):
    return {k: k in sichtbar for k in _ALLE_REITER}


_H = [0.17, 0.58, 0.25]          # Seitenleiste | Mitte | Eigenschaften (ausgeblendete Flächen zählen nicht)
#: Vorgaben: Schalter (fehlende = an), Reiter vorn, Werkzeug, 3D neben der Karte, Anteile der Teiler
VORGABEN = {
    "strecke": {"schalter": {"seitenleiste": True, "eigenschaften": True, "unterer_bereich": True,
                             **_nur_reiter("reiter_hoehe", "reiter_neigung", "reiter_querschnitt",
                                           "reiter_geschwindigkeit", "reiter_pruefung")},
                "reiter": "reiter_hoehe", "werkzeug": "auswahl", "3d": False,
                "anteile": {"horizontal": _H, "vertikal": [0.68, 0.32]}},
    "umgebung": {"schalter": {"seitenleiste": False, "eigenschaften": True, "unterer_bereich": True,
                              **_nur_reiter("reiter_umgebung", "reiter_pruefung")},
                 "reiter": "reiter_umgebung", "werkzeug": "umgebung", "3d": False,
                 "anteile": {"horizontal": _H, "vertikal": [0.6, 0.4]}},
    "3d": {"schalter": {"seitenleiste": False, "eigenschaften": False, "unterer_bereich": False},
           "reiter": None, "werkzeug": None, "3d": True,
           "anteile": {"horizontal": _H, "vertikal": [0.68, 0.32], "karte3d": [0.32, 0.68]}},
    "bauen": {"schalter": {"seitenleiste": False, "eigenschaften": False, "unterer_bereich": True,
                           **_nur_reiter("reiter_pruefung", "reiter_auswertung", "reiter_geschwindigkeit")},
              "reiter": "reiter_pruefung", "werkzeug": "auswahl", "3d": False,
              "anteile": {"horizontal": _H, "vertikal": [0.45, 0.55]}},
    "alles": {"schalter": {}, "reiter": None, "werkzeug": None, "3d": False,
              "anteile": {"horizontal": _H, "vertikal": [0.65, 0.35]}},
}


def vorgabe(name: str) -> dict:
    v = VORGABEN[name]
    return {"schalter": {k: v["schalter"].get(k, True) for k in SCHALTER}, "reiter": v["reiter"],
            "werkzeug": v["werkzeug"], "3d": v["3d"], "anteile": dict(v["anteile"]), "groessen": {}}


def laden(name: str) -> dict:
    """Vorgabe mit den gemerkten Änderungen des Nutzers."""
    stand = vorgabe(name)
    try:
        gemerkt = json.loads(settings().value(f"arbeitsbereich/{name}", "") or "{}")
    except (TypeError, ValueError):
        gemerkt = {}
    if isinstance(gemerkt, dict):
        stand["schalter"].update({k: bool(v) for k, v in (gemerkt.get("schalter") or {}).items() if k in SCHALTER})
        if gemerkt.get("reiter") in _ALLE_REITER:
            stand["reiter"] = gemerkt["reiter"]
        groessen = gemerkt.get("groessen") or {}
        stand["groessen"] = {k: [int(x) for x in v] for k, v in groessen.items()
                             if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)}
    return stand


def merken(name: str, stand: dict):
    """Abweichungen von der Vorgabe speichern (Schalter, Reiter vorn, Teilergrößen)."""
    v = vorgabe(name)
    daten = {"schalter": {k: on for k, on in stand["schalter"].items() if v["schalter"].get(k) != on},
             "reiter": stand.get("reiter"), "groessen": stand.get("groessen") or {}}
    settings().setValue(f"arbeitsbereich/{name}", json.dumps(daten))


def vergessen(name: str):
    settings().remove(f"arbeitsbereich/{name}")


def aktiv_laden(standard: str = "strecke") -> str:
    name = str(settings().value("arbeitsbereich/aktiv", standard) or standard)
    return name if name in SCHLUESSEL else standard


def aktiv_merken(name: str):
    settings().setValue("arbeitsbereich/aktiv", name)


def groessen_aus_anteilen(anteile, gesamt: int) -> list:
    gesamt = max(int(gesamt), 1)
    return [int(round(a * gesamt)) for a in anteile]
