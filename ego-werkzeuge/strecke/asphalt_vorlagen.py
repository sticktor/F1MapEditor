"""Fahrbahnoberflächen und Reifenspuren aus den Spielstrecken: feste Katalogdatei, beim Bau
verlustfrei aus den Strecken-ERPs kopieren (wie ``spiel_weltkarten.biom_wueste``).

Katalog: ``strecke/daten/fahrbahn.json`` (ohne Spieltexturen), kleine Vorschaubilder unter
``strecke/daten/vorschau/<asphalt|skid>/``. Neu erzeugen (Katalog genau die Ausgabe, fehlende Vorschauen dazu):
``python -m strecke.asphalt_vorlagen analysieren <Spielordner>``.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Optional

from ego import erp, grafik

from . import umgebung

DATEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daten", "fahrbahn.json")
#: Vorschau: Asphalt = Albedo 160×160, Reifenspuren = Streifen hochkant 80×160
VORSCHAU_GROESSE = {"asphalt": (160, 160), "skid": (80, 160)}
ZIEL_MATERIAL = "por_track_a.material"
SKID_SLOT = "me_racingSkidsMap"
#: Zielslot (por_track_a) ← Quellslot je Shader
SLOTS = {
    "ts_ribbon": {s: s for s in ("me_albedoMap", "me_normalMap", "me_surfaceMap", "me_detailAlbedoMap",
                                 "me_detailSurfaceMap")},
    "lt_ribbon": {"me_albedoMap": "me_baseAlbedoMap", "me_normalMap": "me_baseNormalMap",
                  "me_surfaceMap": "me_baseSurfaceMap"},
}
NEBENFLAECHEN = ("off", "pit", "outfield", "rumble", "kerb", "drain", "runoff", "astro", "sausage",
                 "line", "ground")


class VorlagenFehler(Exception):
    pass


def _katalog_pfad(pfad=None):
    return pfad or DATEN


def katalog_laden(pfad=None) -> dict:
    """``fahrbahn.json``: {version, asphalt: {reihenfolge, vorlagen}, skid: {…}}."""
    p = _katalog_pfad(pfad)
    if not os.path.isfile(p):
        return {"version": 2, "asphalt": {"reihenfolge": [], "vorlagen": {}},
                "skid": {"reihenfolge": [], "vorlagen": {}}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def asphalt_katalog(pfad=None) -> dict:
    return katalog_laden(pfad).get("asphalt") or {"reihenfolge": [], "vorlagen": {}}


def skid_katalog(pfad=None) -> dict:
    return katalog_laden(pfad).get("skid") or {"reihenfolge": [], "vorlagen": {}}


def _aufloesen(gruppe: dict, vorlage: str) -> tuple[str, dict]:
    """Vorlagenname oder Alias → (Schlüssel, Eintrag)."""
    vorlagen = gruppe.get("vorlagen") or {}
    if vorlage in vorlagen:
        return vorlage, vorlagen[vorlage]
    for key, ein in vorlagen.items():
        if vorlage in (ein.get("aliase") or []):
            return key, ein
    raise VorlagenFehler("Unknown template %r (not in %s)" % (vorlage, _katalog_pfad()))


def _ziel_material(archiv: erp.ErpFile):
    material = next((r for r in archiv.resources
                     if r.identifier.endswith("/terrain/materials/" + ZIEL_MATERIAL)), None)
    if material is None:
        raise VorlagenFehler("%s missing in the carrier" % ZIEL_MATERIAL)
    return material


def _strecken_erp(spiel: str, strecke: str) -> str:
    for gruppe in ("2025_asset_groups", "2021_asset_groups"):
        treffer = sorted(glob.glob(os.path.join(
            spiel, gruppe, "environment_package", "tracks", strecke, "wep", "*_common.erp")))
        if treffer:
            return treffer[0]
    raise VorlagenFehler("No track ERP for %s in the game folder" % strecke)


def _shader_art(shader: str):
    for art in SLOTS:
        if shader.endswith("/" + art + ".nefx2"):
            return art
    return None


def _hauptmaterial(kandidaten):
    def rang(k):
        n = k.rsplit("/", 1)[-1][:-len(".material")]
        return (0 if "main" in n else 1, 0 if "_a" in n or n.endswith("surface") else 1, len(n), n)

    gut = [k for k in kandidaten
           if any(w in k.rsplit("/", 1)[-1] for w in ("track", "surface"))
           and not any(w in k.rsplit("/", 1)[-1] for w in NEBENFLAECHEN)]
    return sorted(gut, key=rang)[0] if gut else None


def _konstanten_uebernehmen(quelle: grafik.Material, ziel: grafik.Material, farbton: str = "neutral") -> dict:
    """Gemeinsame Tiling-/Detail-/Farb-Konstanten der Quelle → Ziel (nur float-Typen).

    ``farbton``: ``neutral`` (Standard) = Farbtöne nur mit ihrer Helligkeit, ``vorlage`` = unverändert wie in der Vorlage."""
    float_typen = {3, 0x20003, 0x30003, 0x40003}
    q_param = {p.name: p for p in quelle.parameter if p.art == 1}
    z_namen = {p.name for p in ziel.parameter if p.art == 1}
    erg = {}
    for name, p in q_param.items():
        if name not in z_namen or p.typ not in float_typen:
            continue
        if "HeapIndex" in name:
            continue
        # Tiling, Detail, Tint, Skids, Racing line, Wetness – nicht GSAA/Puddle-Flags
        if not any(t in name for t in (
                "Scale", "Strength", "Tint", "Modulate", "Rough", "Falloff",
                "Darkening", "Smoothing", "Offset", "Colour", "Color", "Bound",
                "wetness", "Wetness", "detail", "Detail", "baseMap", "skids",
                "racingLine", "dust")):
            continue
        erg[name] = p.wert
    # Farbstich (19.11.2026, Feature GP: Silverstone-Asphalt grün-grau): Der Grundfarbton der Vorlage ist auf die
    # Luftbild-Makrokarte ihrer Strecke abgestimmt (Silverstone 0,60/0,64/0,69 kühl, Abu Dhabi 0,43/0,38/0,34 warm).
    # Die Makrokarte übernehmen wir nicht, unter der Fahrbahn liegt neutrales Grau (Weltkarten 128/128/127) bzw. die
    # des Trägers. Deshalb nur die Helligkeit übernehmen, den Farbton neutral setzen.
    # 20.11.2026 (Bild des Nutzers, Feature GP): Auch der Staubton färbte die Fahrbahnränder olivgrün-grau. Der Staub liegt
    # abseits der Ideallinie (TrackInfo-Karte), die Staubkarte ist gleichmäßiges Rauschen; Silverstone 1/0,79/0,39 (Blau am
    # Rand im Bild 106 → 85). Deshalb gilt dasselbe für ``…Colour``/``…Color``.
    if farbton not in FARBTOENE:
        raise VorlagenFehler("Unknown colour tone %r (possible: %s)" % (farbton, ", ".join(FARBTOENE)))
    if farbton == "vorlage":
        return erg
    for name in [n for n in erg if n.endswith(("Tint", "Colour", "Color")) and len(erg[n]) == 3]:
        grau = sum(float(v) for v in erg[name]) / 3.0
        erg[name] = (grau, grau, grau)
    return erg


#: Farbton der Fahrbahnvorlage im Bau (20.11.2026): ``neutral`` = nur Helligkeit, ``vorlage`` = Farbe der Vorlage behalten
FARBTOENE = ("neutral", "vorlage")
#: Grundton (``me_baseMapTint``) des Portimão-Trägers; die Spielstrecken haben ihn im Katalog (``grundton``)
TRAEGER_GRUNDTON = [0.730, 0.708, 0.694]


def _index_aus_erp(pfad: str) -> grafik.RessourcenIndex:
    """Strecken-ERP plus übrige ERPs derselben Strecke (Texturen können dort liegen)."""
    return umgebung._ressourcen_index(pfad)


def einsetzen(archiv: erp.ErpFile, vorlage: str, *, spielordner=None, code="portimao",
              katalog_pfad=None, farbton: str = "neutral") -> dict:
    """Asphalttexturen von ``por_track_a`` auf die Vorlage umbiegen; Texturen aus dem Spiel kopieren.

    ``traeger``: keine Änderung. Rückgabe enthält ``mipmaps`` fürs Paket.
    """
    key, eintrag = _aufloesen(asphalt_katalog(katalog_pfad), vorlage)
    if eintrag.get("traeger"):
        return {"vorlage": key, "traeger": True, "mipmaps": []}
    spiel = umgebung.spielordner_finden(spielordner)
    if spiel is None:
        raise VorlagenFehler("Game folder required for track surface %r "
                             "(Tools → Set F1 25 game folder)" % key)
    strecke = eintrag.get("strecke") or key
    material = _ziel_material(archiv)
    ziel_mat = grafik.Material.aus_bytes(material.fragments[0].read())
    slots = eintrag.get("slots") or {}
    if not slots:
        raise VorlagenFehler("Template %s has no texture slots" % key)
    start = [kennung.split("?")[0] for kennung in slots.values()]
    idx = _index_aus_erp(_strecken_erp(spiel, strecke))
    try:
        neu, mips, fehlt = umgebung.ressourcen_kopieren(
            archiv, idx, start, code, spiel, praefix="asph_")
    finally:
        idx.schliessen()
    if fehlt:
        raise VorlagenFehler("Textures missing in the game data for %s: %s" % (key, ", ".join(fehlt[:5])))
    alt = ziel_mat.texturen()
    tausch = {}
    for slot, kennung in slots.items():
        alt_k = alt.get(slot)
        if alt_k:
            tausch[alt_k] = kennung.split("?")[0]
    if not tausch:
        raise VorlagenFehler("por_track_a has none of the slots %s" % sorted(slots))
    fehlend = [k for k in tausch.values() if archiv.find(k) is None]
    if fehlend:
        raise VorlagenFehler("Textures missing after copying for %s: %s" % (key, ", ".join(fehlend[:5])))
    konstanten = {}
    quelle_mat_kennung = eintrag.get("material")
    if quelle_mat_kennung and not eintrag.get("experimentell"):
        with erp.open_erp(_strecken_erp(spiel, strecke)) as q:
            qres = q.find(quelle_mat_kennung)
            if qres is not None:
                quell_mat = grafik.Material.aus_bytes(qres.fragments[0].read())
                konstanten = _konstanten_uebernehmen(quell_mat, ziel_mat, farbton)
    material.set_fragment_data(0, grafik.material_aendern(
        material.data(), texturen=tausch, konstanten=konstanten or None))
    return {"vorlage": key, "strecke": strecke, "experimentell": bool(eintrag.get("experimentell")), "farbton": farbton,
            "ressourcen_neu": len(neu), "konstanten": sorted(konstanten), "mipmaps": list(mips),
            "slots": sorted(slots)}


#: Kunstrasen: Zielslot (ts_ribbon des Trägers) → Quellslots nach Vorrang (lt_ribbon Melbourne, track_local Texas/Shanghai)
KUNSTRASEN_SLOTS = {"me_albedoMap": ("me_baseAlbedoMap", "me_albedoMap", "me_smoothTarmacMap"),
                    "me_normalMap": ("me_baseNormalMap", "me_normalMap", "me_smoothNormalMap"),
                    "me_surfaceMap": ("me_baseSurfaceMap", "me_surfaceMap")}
#: Asphaltanteile des Zielmaterials neutralisieren (sonst graue Detailtextur, aufgehellter Grundton, Staub)
KUNSTRASEN_KONSTANTEN = {"me_baseMapTint": (1.0, 1.0, 1.0), "me_detailStrength": (0.0,),
                         "me_detailRoughnessStrength": (0.0,), "me_dustStrength": (0.0,)}


def kunstrasen_einsetzen(archiv: erp.ErpFile, strecke: str, *, spielordner=None, code="portimao",
                         ziel_material="por_offtrack_a.material") -> dict:
    """Kunstrasen (25.11.2026, docs/kerbtypen.md): ein ungenutztes Fahrbahnmaterial des Trägers (ts_ribbon) bekommt
    Albedo, Normalen und Surface des Kunstrasen-Materials einer Spielstrecke (nur lesend aus dem Spielordner, Texturen
    ins Paket; Zuordnung der Slots nach Bedeutung, ``KUNSTRASEN_SLOTS``). Grundton, Detail- und Staubanteil des
    Asphalts werden neutralisiert."""
    spiel = umgebung.spielordner_finden(spielordner)
    if spiel is None:
        raise VorlagenFehler("Game folder required for artificial grass (Tools → Set F1 25 game folder)")
    ziel = next((r for r in archiv.resources if r.identifier.endswith("/terrain/materials/" + ziel_material)), None)
    if ziel is None:
        raise VorlagenFehler("%s missing in the carrier" % ziel_material)
    pfad = _strecken_erp(spiel, strecke)
    with erp.open_erp(pfad) as q:
        kandidaten = [r for r in q.resources if r.identifier.endswith(".material")
                      and any(w in r.identifier.rsplit("/", 1)[-1].lower() for w in ("astro", "turf"))
                      and "physics" not in r.identifier.lower()]
        if not kandidaten:
            raise VorlagenFehler("No artificial grass material in %s" % strecke)
        quelle = grafik.Material.aus_bytes(kandidaten[0].fragments[0].read())
        quelle_kennung = kandidaten[0].identifier
    ziel_mat = grafik.Material.aus_bytes(ziel.fragments[0].read())
    q_tex, z_tex = quelle.texturen(), ziel_mat.texturen()
    zuordnung = {}
    for z_slot, kandidaten in KUNSTRASEN_SLOTS.items():
        q_slot = next((k for k in kandidaten if k in q_tex), None)
        if z_slot in z_tex and q_slot:
            zuordnung[z_slot] = q_slot
    if "me_albedoMap" not in zuordnung:
        raise VorlagenFehler("%s and %s share no texture slots" % (quelle_kennung.rsplit("/", 1)[-1], ziel_material))
    gemeinsam = sorted(zuordnung)
    idx = _index_aus_erp(pfad)
    try:
        neu, mips, fehlt = umgebung.ressourcen_kopieren(archiv, idx, [q_tex[zuordnung[s]] for s in gemeinsam], code,
                                                        spiel, praefix="astr_")
    finally:
        idx.schliessen()
    if fehlt:
        raise VorlagenFehler("Textures missing in the game data for artificial grass: %s" % ", ".join(fehlt[:5]))
    tausch = {z_tex[s]: q_tex[zuordnung[s]] for s in gemeinsam}
    namen = {p.name for p in ziel_mat.parameter if p.art == 1}
    konstanten = {k: v for k, v in KUNSTRASEN_KONSTANTEN.items() if k in namen}
    ziel.set_fragment_data(0, grafik.material_aendern(ziel.data(), texturen=tausch, konstanten=konstanten or None))
    return {"strecke": strecke, "material": quelle_kennung.rsplit("/", 1)[-1],
            "slots": {s: zuordnung[s] for s in gemeinsam},
            "ressourcen_neu": len(neu), "konstanten": sorted(konstanten), "mipmaps": list(mips)}


def skid_einsetzen(archiv: erp.ErpFile, vorlage: str, *, spielordner=None, code="portimao",
                   katalog_pfad=None) -> dict:
    """``me_racingSkidsMap`` von ``por_track_a`` umbiegen; ``traeger`` = unverändert."""
    key, eintrag = _aufloesen(skid_katalog(katalog_pfad), vorlage)
    if eintrag.get("traeger"):
        return {"vorlage": key, "traeger": True, "mipmaps": []}
    spiel = umgebung.spielordner_finden(spielordner)
    if spiel is None:
        raise VorlagenFehler("Game folder required for tyre marks %r" % key)
    quelle = eintrag.get("quelle")
    if not quelle:
        raise VorlagenFehler("Template %s has no skid texture" % key)
    strecke = eintrag.get("strecke") or "abu_dhabi"
    material = _ziel_material(archiv)
    alt = grafik.Material.aus_bytes(material.fragments[0].read()).texturen().get(SKID_SLOT)
    if not alt:
        raise VorlagenFehler("%s has no slot %s" % (ZIEL_MATERIAL, SKID_SLOT))
    start = [quelle.split("?")[0]]
    idx = _index_aus_erp(_strecken_erp(spiel, strecke))
    try:
        neu, mips, fehlt = umgebung.ressourcen_kopieren(
            archiv, idx, start, code, spiel, praefix="skid_")
    finally:
        idx.schliessen()
    if fehlt:
        raise VorlagenFehler("Skid texture missing for %s: %s" % (key, ", ".join(fehlt[:3])))
    material.set_fragment_data(0, grafik.material_aendern(
        material.data(), texturen={alt: quelle.split("?")[0]}))
    return {"vorlage": key, "quelle": quelle, "ressourcen_neu": len(neu), "mipmaps": list(mips)}


_STRECKEN_NAMEN = {
    "abu_dhabi": "Abu Dhabi", "austria": "Austria", "bahrain": "Bahrain", "baku": "Baku",
    "brazil": "Brazil", "catalunya": "Catalunya", "hungaroring": "Hungaroring", "imola": "Imola",
    "jeddah": "Jeddah", "las_vegas": "Las Vegas", "losail": "Losail", "madrid": "Madrid",
    "melbourne": "Melbourne", "mexico": "Mexico", "miami": "Miami", "monaco": "Monaco",
    "montreal": "Montreal", "monza": "Monza", "portimao": "Portimão", "shanghai": "Shanghai",
    "silverstone": "Silverstone", "singapore": "Singapore", "spa_francorchamps": "Spa-Francorchamps",
    "suzuka": "Suzuka", "texas": "Texas", "zandvoort": "Zandvoort",
}


def _gruppen_name(strecken, namen=None) -> str:
    namen = namen or _STRECKEN_NAMEN
    labels = [namen.get(s, s.replace("_", " ").title()) for s in strecken]
    if len(labels) <= 3:
        return " / ".join(labels)
    return "%s / %s (+%d)" % (labels[0], labels[1], len(labels) - 2)


def _alt_type_aliase(alt_vorlagen: dict, schluessel: str, wert: str) -> list:
    """Alte ``typeN``-Aliase aus dem bestehenden Katalog übernehmen (gleiche Quelle/Slots)."""
    gefunden = []
    for ein in (alt_vorlagen or {}).values():
        if ein.get(schluessel) != wert:
            continue
        for a in ein.get("aliase") or []:
            if a.startswith("type") and a not in gefunden:
                gefunden.append(a)
    return gefunden


def _alt_vorschau(alt_vorlagen: dict, schluessel: str, wert: str, fallback: str) -> str:
    for ein in (alt_vorlagen or {}).values():
        if ein.get(schluessel) == wert and ein.get("vorschau"):
            return ein["vorschau"]
    return fallback


_TRAEGER_GRUNDALIASE = ("traeger", "portimao", "type3", "type12")


def asphalt_katalog_aus_roh(vorlagen_roh: dict, alt_asphalt=None) -> dict:
    """Gleiche Asphalt-Textur-MD5s zusammenfassen; Portimão-Finger → ``traeger``."""
    alt_vorlagen = (alt_asphalt or {}).get("vorlagen") or {}
    traeger_finger = (vorlagen_roh.get("portimao") or {}).get("finger")
    gruppen: dict[tuple, list] = {}
    traeger_aliase = ["traeger", "portimao"]
    for strecke, e in sorted(vorlagen_roh.items()):
        if traeger_finger and e["finger"] == traeger_finger:
            if strecke not in traeger_aliase:
                traeger_aliase.append(strecke)
            continue
        gruppen.setdefault(e["finger"], []).append(strecke)
    vorlagen, reihenfolge = {}, []
    # ts_ribbon zuerst, dann lt_ribbon (experimentell)
    for _finger, strecken in sorted(gruppen.items(), key=lambda kv: (
            1 if vorlagen_roh[kv[1][0]].get("experimentell") else 0, kv[1][0])):
        rep = strecken[0]
        e = vorlagen_roh[rep]
        aliase = list(strecken)
        for t in _alt_type_aliase(alt_vorlagen, "strecke", rep):
            if t not in aliase:
                aliase.append(t)
        # Fingerprint-Treffer: alte type-Aliase über gleiche Slot-Menge
        slot_sig = tuple(sorted((e.get("slots") or {}).items()))
        for ein in alt_vorlagen.values():
            if tuple(sorted((ein.get("slots") or {}).items())) == slot_sig:
                for t in ein.get("aliase") or []:
                    if t.startswith("type") and t not in aliase:
                        aliase.append(t)
        vorlagen[rep] = {
            "name": _gruppen_name(strecken), "aliase": aliase, "strecke": rep,
            "material": e["material"], "shader": e["shader"],
            "experimentell": e["experimentell"], "slots": e["slots"],
            "vorschau": _alt_vorschau(alt_vorlagen, "strecke", rep, rep + ".png"),
            **({"grundton": e["grundton"]} if e.get("grundton") else {}),
        }
        reihenfolge.append(rep)
    for t in _alt_type_aliase(alt_vorlagen, "traeger", True):
        if t not in traeger_aliase:
            traeger_aliase.append(t)
    # Alte manuelle Träger-Aliase (z. B. hungaroring), falls Finger nicht Portimão war
    alt_t = alt_vorlagen.get("traeger") or {}
    for a in alt_t.get("aliase") or []:
        if a not in traeger_aliase and a not in vorlagen:
            traeger_aliase.append(a)
    vorlagen["traeger"] = {
        "traeger": True,
        "name": "Carrier (Portimão)" if not [s for s in traeger_aliase if s not in _TRAEGER_GRUNDALIASE]
                else "Carrier (Portimão) / " + _gruppen_name([s for s in traeger_aliase if s not in _TRAEGER_GRUNDALIASE]),
        "aliase": traeger_aliase,
        "vorschau": alt_t.get("vorschau") or "type12.png",
    }
    reihenfolge = ["traeger"] + [k for k in reihenfolge if k != "traeger"]
    return {"reihenfolge": reihenfolge, "vorlagen": vorlagen}


def skid_katalog_aus_roh(skid_roh: dict, alt_skid=None) -> dict:
    """Gleiche ``me_racingSkidsMap``-Textur-MD5s zusammenfassen; Portimão → ``traeger``."""
    alt_vorlagen = (alt_skid or {}).get("vorlagen") or {}
    traeger_finger = (skid_roh.get("portimao") or {}).get("finger")
    gruppen: dict[str, list] = {}
    traeger_aliase = ["traeger", "portimao", "type5"]
    for strecke, e in sorted(skid_roh.items()):
        if traeger_finger and e["finger"] == traeger_finger:
            if strecke not in traeger_aliase:
                traeger_aliase.append(strecke)
            continue
        gruppen.setdefault(e["finger"], []).append(strecke)
    vorlagen = {
        "traeger": {
            "traeger": True, "name": "Carrier (Portimão)", "aliase": traeger_aliase,
            "vorschau": (alt_vorlagen.get("traeger") or {}).get("vorschau") or "traeger.png",
        }
    }
    reihenfolge = ["traeger"]
    for _finger, strecken in sorted(gruppen.items(), key=lambda kv: kv[1][0]):
        rep = strecken[0]
        for key, ein in alt_vorlagen.items():
            if not ein.get("traeger") and ein.get("quelle") == skid_roh[rep]["quelle"] and key in strecken:
                rep = key
                break
        e = skid_roh[rep]
        aliase = list(strecken)
        for t in _alt_type_aliase(alt_vorlagen, "quelle", e["quelle"]):
            if t not in aliase:
                aliase.append(t)
        vorlagen[rep] = {
            "name": _gruppen_name(strecken), "aliase": aliase, "strecke": rep,
            "quelle": e["quelle"],
            "vorschau": _alt_vorschau(alt_vorlagen, "quelle", e["quelle"], rep + ".png"),
        }
        reihenfolge.append(rep)
    return {"reihenfolge": reihenfolge, "vorlagen": vorlagen}


def analysieren(spiel: str, ziel: Optional[str] = None, log=print, vorschau: bool = True) -> dict:
    """Spiel-ERPs lesen, gleiche Oberflächen/Skids per Textur-MD5 zusammenfassen, ``fahrbahn.json`` schreiben.

    Mit ``vorschau`` entstehen fehlende Vorschaubilder neben der Katalogdatei (``vorschau/<art>/``)."""
    spiel = umgebung.spielordner_finden(spiel) or spiel
    if not os.path.isdir(os.path.join(spiel, "2025_asset_groups", "environment_package", "tracks")):
        raise VorlagenFehler("Not an F1 25 game folder: %s" % spiel)
    vorlagen_roh, skid_roh, fehler = {}, {}, {}
    for strecke, pfad in _strecken_erps(spiel):
        try:
            with erp.open_erp(pfad) as a:
                res = {r.identifier: r for r in a.resources}
                mats = {}
                for k, r in res.items():
                    if "/tracks/%s/terrain/materials/" % strecke not in k or not k.endswith(".material"):
                        continue
                    m = grafik.Material.aus_bytes(r.fragments[0].read())
                    if _shader_art(m.shader):
                        mats[k] = m
                haupt = _hauptmaterial(mats)
                if haupt is None:
                    fehler[strecke] = "no track material"
                    continue
                m = mats[haupt]
                art = _shader_art(m.shader)
                tx = m.texturen()
                slots, finger = {}, []
                for ziel_slot, quell_slot in SLOTS[art].items():
                    kennung = tx.get(quell_slot)
                    if not kennung:
                        continue
                    basis = kennung.split("?")[0]
                    r = res.get(basis + ".image") or res.get(basis)
                    if r is None:
                        continue
                    slots[ziel_slot] = basis
                    finger.append(r.hash.hex() if hasattr(r, "hash") and r.hash else basis)
                ton = m.konstanten().get("me_baseMapTint")
                vorlagen_roh[strecke] = {
                    "material": haupt, "shader": art, "experimentell": art != "ts_ribbon",
                    "grundton": [round(float(v), 3) for v in ton] if ton and len(ton) == 3 else None,
                    "slots": slots, "finger": tuple(finger),
                }
                skid_kennung = tx.get(SKID_SLOT)
                if skid_kennung:
                    basis = skid_kennung.split("?")[0]
                    r = res.get(basis + ".image") or res.get(basis)
                    skid_roh[strecke] = {
                        "quelle": basis,
                        "finger": (r.hash.hex() if r is not None and getattr(r, "hash", None)
                                   else basis),
                    }
                log("%-20s %s %d slots%s" % (
                    strecke, art, len(slots), "" if strecke not in skid_roh else " +skid"))
        except Exception as e:
            fehler[strecke] = "%s: %s" % (type(e).__name__, e)
    alt = katalog_laden()
    asphalt = asphalt_katalog_aus_roh(vorlagen_roh, alt.get("asphalt"))
    skid = skid_katalog_aus_roh(skid_roh, alt.get("skid"))
    out = {
        "version": 2,
        "asphalt": asphalt,
        "skid": skid,
        "fehler": fehler,
    }
    ziel = ziel or DATEN
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    with open(ziel, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("Wrote %s (%d asphalt groups, %d skid groups)" % (
        ziel, len(asphalt["reihenfolge"]) - 1, len(skid["reihenfolge"]) - 1))
    if vorschau:
        vorschau_erzeugen(spiel, out, os.path.join(os.path.dirname(ziel), "vorschau"), log=log)
    return out


def _textur_bild(spiel: str, strecke: str, kennung: str):
    """Höchste Stufe einer Strecken-Textur (mit externen Mip-Dateien) als Pillow-Bild."""
    from ego import grafik_textur
    with erp.open_erp(_strecken_erp(spiel, strecke)) as a:
        res = {r.identifier: r for r in a.resources}
        r = res.get(kennung + ".image") or res.get(kennung)
        if r is None:
            raise VorlagenFehler("Textures missing in the game data for %s: %s" % (strecke, kennung))
        textur = grafik.Textur.aus_ressource(r)
    extern = None
    pfad = grafik_textur.extern_datei_finden(textur, [os.path.join(spiel, g) for g in
                                                      ("2025_asset_groups", "2021_asset_groups")] + [spiel])
    if pfad:
        extern = grafik_textur.extern_stufen(textur, pfad)
    return grafik_textur.textur_bild(textur, extern)


def vorschau_erzeugen(spiel: str, katalog: dict, ordner: str, *, ersetzen: bool = False, log=print) -> list:
    """Fehlende Vorschaubilder aus dem Spielordner: Asphalt = Albedo verkleinert, Reifenspuren = Streifen hochkant.

    Träger-Einträge (Portimão-Mod, keine Spielstrecke) bleiben, wie sie sind. Gibt die geschriebenen Dateien zurück."""
    try:
        from PIL import Image
    except ImportError:
        log("Previews skipped: Pillow is not installed")
        return []
    geschrieben = []
    for art in ("asphalt", "skid"):
        for key, e in ((katalog.get(art) or {}).get("vorlagen") or {}).items():
            if e.get("traeger"):
                continue
            ziel = os.path.join(ordner, art, e.get("vorschau") or key + ".png")
            if os.path.isfile(ziel) and not ersetzen:
                continue
            kennung = (e.get("slots") or {}).get("me_albedoMap") if art == "asphalt" else e.get("quelle")
            if not kennung:
                continue
            try:
                bild = _textur_bild(spiel, e.get("strecke") or key, kennung).convert("RGBA")
            except (VorlagenFehler, OSError, ValueError) as fehler:
                log("Preview %s/%s failed: %s" % (art, key, fehler))
                continue
            if art == "skid" and bild.width > bild.height:
                bild = bild.transpose(Image.Transpose.ROTATE_90)
            bild = bild.resize(VORSCHAU_GROESSE[art], Image.LANCZOS)
            os.makedirs(os.path.dirname(ziel), exist_ok=True)
            bild.save(ziel, optimize=True)
            geschrieben.append(ziel)
            log("Preview %s/%s" % (art, os.path.basename(ziel)))
    return geschrieben


def _strecken_erps(spiel: str):
    gesehen = {}
    for gruppe in ("2025_asset_groups", "2021_asset_groups"):
        for p in sorted(glob.glob(os.path.join(
                spiel, gruppe, "environment_package", "tracks", "*", "wep", "*_common.erp"))):
            gesehen.setdefault(p.split(os.sep)[-3], p)
    return sorted(gesehen.items())


# Rückwärtskompatibel für alten Aufruf
def katalog_laden_alt(ordner=None):
    """Früher: asphalt_texturen/katalog.json. Jetzt fahrbahn.json → asphalt.vorlagen."""
    return {"vorlagen": asphalt_katalog().get("vorlagen") or {}}


def skid_katalog_laden(ordner=None):
    return {"vorlagen": skid_katalog().get("vorlagen") or {},
            "reihenfolge": skid_katalog().get("reihenfolge") or []}


if __name__ == "__main__":
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Track surface catalog (fahrbahn.json)")
    ap.add_argument("befehl", choices=("analysieren",), help="Rebuild fahrbahn.json from the game")
    ap.add_argument("spiel", help="F1 25 game folder")
    ap.add_argument("--ziel", default=DATEN, help="Output JSON path")
    ap.add_argument("--ohne-vorschau", action="store_true", help="Do not create missing preview images")
    args = ap.parse_args()
    if args.befehl == "analysieren":
        analysieren(args.spiel, args.ziel, vorschau=not args.ohne_vorschau)
