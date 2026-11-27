"""Eigene 3D-Modelle (Blender → glTF 2.0 / OBJ) in Spielressourcen umsetzen (M1, 16.11.2026).

Hervorgegangen aus dem Entwurf vom 15.11.2026 (``docs/eigene_modelle.md``); im Spiel belegt durch die Testpakete M0
und M0b (``docs/eigene_modelle_m0.md``, ``docs/eigene_modelle_m0b.md``): Geometrie, Kennungen, Bindung, Material und
Textur komplett selbst erzeugt, UV und Händigkeit richtig, LOD-Wechsel, Basis-Vertex über 65.535 Vertices, neutrale
Karten. Projektablage, Zwischenspeicher und Katalog liegen in ``strecke/eigene_modelle.py``. Schreibt nie in den
Spielordner. Meldungen an den Nutzer (Fehler, Hinweise) sind englisch (English First), deutsche Fassung in
``Mapeditor/src/mapeditor/i18n/meldungen.csv``.

Ablauf (ein Aufruf): ``modell_umsetzen`` erzeugt Modell, Materialien und Texturen aus einer Szene:

    szene = laden("schild.glb")                        # glb_lesen / gltf_lesen / obj_lesen
    um = modell_umsetzen(szene, "usr_schild", "eaid://environment_package/tracks/portimao/objects", vorlage_bytes)
    in_archiv(archiv, um.ressourcen)
    pruefen(archiv, um.bau, quelle=spiel_index)        # Liste (Name, ok, Einzelheit)

Die Bausteine einzeln:

    bau = modell_bauen(szene, "eaid://environment_package/tracks/portimao/objects/idf/usr_schild.idf",
                       materialien={"Tafel": "eaid://…/vorhandenes.material"})
    bau.ressourcen += textur_ressourcen("eaid://…/usr_schild_01_d.tif", rgba, FORMAT_DIFFUS)
    bau.ressourcen.append(material_aus_vorlage(vorlage_bytes, "eaid://…/usr_schild_01.material", {...}))

Koordinaten: Das Spiel rechnet wie glTF **rechtshändig mit Y oben in Metern** (docs/formate/streckenlogik.md);
glTF-Daten werden deshalb unverändert übernommen. Blender (Z oben) exportiert glTF mit „+Y Up“ selbst richtig:
Blender (x, y, z) → Spiel (x, z, −y); die Blender-Draufsicht entspricht der Karte des Mapeditors (x rechts,
z unten). Die übliche Blender-Vorderseite (−Y, Ansicht „Vorn“) ist im Spiel +Z = ``vorne_grad`` 270.

Erzeugt wird das häufigste Layout statischer Objekte (46.749 von 67.790 Teilnetzen): vier Ströme
``POSITION float32x3 | NORMAL half4 | TANGENT half4 | TEXCOORD0 half4 (u, v, Lichtkarten-u, Lichtkarten-v)``,
16-Bit-Indizes mit Basis-Vertex, je LOD ein eigener Modellknoten.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import animation as _anim, erp, grafik

# --------------------------------------------------------------------------- Konstanten (aus den Originalen)
PUFFER_CODE_FLOAT = 39      # float32-Ströme (Stride 12/16)
PUFFER_CODE_HALB4 = 35      # float16x4 (Stride 8)
PUFFER_CODE_INDEX = 19      # u16-Indizes (Art 0x110A)
FORMAT_F32X3, FORMAT_HALB4 = 47, 35
FRAGMENT_FLAGS, FRAGMENT_FLAGS_PUFFERDATEN = 16, 4
EINHEIT = tuple(float(i % 5 == 0) for i in range(16))
INDEX_GRENZE = 65535
#: Texturformate (Codes der Engine): Standard je Slot von ``object_opaque`` in den Originalen
FORMAT_DIFFUS = 70          # BC7 sRGB (Originale meist BC1 sRGB = 54; BC7 ist mit dem vorhandenen Kodierer schärfer)
FORMAT_DIFFUS_BC1 = 54
FORMAT_NORMAL = 65          # BC5
FORMAT_OBERFLAECHE = 52     # BC1 linear
FORMAT_DIFFUS_ALPHA = 57    # BC3 sRGB: Farbkarte mit Alpha (``_da``), wie alle 730 ``object_alpha``-Materialien des Spiels
# Oberflächenkarte ``_s`` (``me_surfaceMap``), Kanäle abgeleitet aus 267 object_opaque-Karten (M0b, 15.11.2026):
# R fast immer 255 (vermutlich Verdeckung), G Glanz (Tafeln/Schilder 0, Monitore 174), B Metall (Tafeln 0,
# Metallschiene 255). Neutral = matt, nicht metallisch, ohne Verdeckung, wie ``garage_board_f2_*``/``trestle_grey``.
OBERFLAECHE_NEUTRAL = (255, 0, 0, 255)
NORMAL_FLACH = (128, 128, 255, 255)
NEUTRAL_KANTE = 16          # neutrale Karten 16 × 16 (Mips 16, 8, 4)
# Normalenkarten: Spiel erwartet DirectX-Grün (+G = Bild unten, M0-Spieltest „DX gewölbt“); Blender/glTF liefern
# OpenGL-Grün (+G = Bild oben) → beim Import Grünkanal umkehren (``normalenkarte_fuer_spiel``)
NORMALEN_KONVENTION_SPIEL = "dx"
# Transparenz und Maske (M5a): eigener Shader je glTF-``alphaMode``. Die Materialien dieser Shader haben dieselben
# drei Karten wie ``object_opaque``, dazu ``me_alphaThreshold``/``me_shadowAlphaThreshold`` (``object_alpha``
# außerdem ``me_energyConservingTransparency``); ihre Zeichnungen tragen Flag 0 statt 1.
SHADER_ALPHA = {"BLEND": "object_alpha.nefx2", "MASK": "object_mask.nefx2"}
#: A3 (18.11.2026, docs/animationen.md): Materialien mit eingebauter Bewegung, als Kopie einer Vorlage des Trägers.
#: ``wasser`` = laufende Normalenschichten (water), ``lauftext`` = leuchtende, laufende Textur (lights_scrolling,
#: z. B. LED-Band), ``fahne`` = wehendes Tuch mit laufender Textur und Wellen (object_flag). Wasser und Fahne brauchen
#: im Mesh einen Farbstrom COLOR0 wie alle ihre Spielmodelle (Vorgabe ohne glTF-Farben aus dem Spielbestand).
EFFEKT_SHADER = {"wasser": "water.nefx2", "lauftext": "lights_scrolling.nefx2", "fahne": "object_flag.nefx2"}
EFFEKT_FARBE = {"wasser": (0.85, 0.6, 0.0, 0.0), "fahne": (0.32, 0.18, 0.29, 0.0)}
#: glTF-Extras eines Materials (Blender: benutzerdefinierte Eigenschaft ``f1_effect``) → Effektart
GLTF_EFFEKT = {"water": "wasser", "scroll": "lauftext", "flag": "fahne"}
#: Standardwerte der Effekte (Spielbestand: Lauftext 0,05–0,1 und Leuchtstärke 250–40.000, alle Fahnen ``me_USpeed`` ≈ 0,3)
EFFEKT_STANDARD = {"lauftext": {"u": 0.1, "v": 0.0, "helligkeit": 250.0}, "fahne": {"u": 0.3},
                   "wasser": {"tempo": 1.0}}
PUFFER_CODE_FARBE, FORMAT_FARBE, VERWENDUNG_FARBE = 14, 14, 6     # COLOR0: R8G8B8A8 UNORM, Stride 4
VORLAGE_ART = {"BLEND": "alpha", "MASK": "mask"}
TEXTUR_FLAGS = 264
SRV_ART = 5
#: Budget aus dem Bestand (analyse/modell_formate.py): LOD 0 Median 1.100, p90 10.900, p99 41.800, Maximum 254.000
DREIECKE_WARNUNG, DREIECKE_FEHLER = 50000, 250000
UV_HALB_WARNUNG = 64.0
LICHT_UV_STANDARD = (0.5, 0.5)


class ModellImportFehler(ValueError):
    """Die Eingabe lässt sich nicht in ein Spielmodell umsetzen."""


# --------------------------------------------------------------------------- Zwischenformat
@dataclass
class Teil:
    """Ein Dreiecksnetz mit einem Material (glTF-Primitive bzw. OBJ-Materialgruppe), schon im Modellraum."""

    name: str
    material: str
    positionen: np.ndarray                  # (N, 3)
    dreiecke: np.ndarray                    # (M, 3)
    normalen: Optional[np.ndarray] = None   # (N, 3)
    tangenten: Optional[np.ndarray] = None  # (N, 4), w = ±1
    uv0: Optional[np.ndarray] = None        # (N, 2), v nach unten (0 = oberer Bildrand) wie glTF und Spiel
    uv1: Optional[np.ndarray] = None        # (N, 2) Lichtkarten-UV 0…1
    farben: Optional[np.ndarray] = None     # (N, 4) 0…1 (wird im Vier-Strom-Layout nicht geschrieben)
    knoten: Optional[str] = None            # bewegter Knoten (Szene.animation), an dem das Teil hängt; None = fest


@dataclass
class QuellMaterial:
    name: str
    basisfarbe: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    bild_basisfarbe: Optional[np.ndarray] = None   # RGBA uint8
    bild_normal: Optional[np.ndarray] = None
    bild_orm: Optional[np.ndarray] = None          # glTF: Verdeckung/Rauheit/Metall
    alpha: str = "OPAQUE"                          # glTF alphaMode: OPAQUE, BLEND, MASK
    doppelseitig: bool = False
    alpha_schwelle: float = 0.5                    # glTF alphaCutoff (nur MASK)
    metall: float = 0.0                            # glTF metallicFactor (Faktor zur Textur, sonst der Wert selbst)
    rauheit: float = 1.0                           # glTF roughnessFactor
    bild_verdeckung: Optional[np.ndarray] = None   # glTF occlusionTexture (Kanal R); oft dasselbe Bild wie bild_orm
    verdeckung_staerke: float = 1.0                # glTF occlusionTexture.strength
    effekt: Optional[dict] = None                  # A3: {"art": wasser|lauftext|fahne, …} aus den glTF-Extras


@dataclass
class Szene:
    teile: List[Teil] = field(default_factory=list)
    materialien: Dict[str, QuellMaterial] = field(default_factory=dict)
    hinweise: List[str] = field(default_factory=list)
    quelle: str = ""
    animation: Optional["_anim.Animation"] = None   # Knotenanimation (A1); Teile bleiben in der Ruhelage im Modellraum

    def grenzen(self) -> Tuple[np.ndarray, np.ndarray]:
        p = np.concatenate([t.positionen for t in self.teile])
        return p.min(0), p.max(0)

    def dreiecke(self) -> int:
        return int(sum(len(t.dreiecke) for t in self.teile))


# --------------------------------------------------------------------------- glTF 2.0 lesen
_KOMPONENTE = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_ANZAHL = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_NICHT_UNTERSTUETZT = ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_mesh_quantization")


def _bild_rgba(daten: bytes) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(daten)).convert("RGBA"), np.uint8)


class _Gltf:
    def __init__(self, j: dict, binaer: Optional[bytes], ordner: str):
        self.j, self.ordner = j, ordner
        self.puffer: List[bytes] = []
        for i, b in enumerate(j.get("buffers", [])):
            uri = b.get("uri")
            if uri is None:
                if binaer is None or i != 0:
                    raise ModellImportFehler("glTF: buffer %d has no data" % i)
                self.puffer.append(binaer)
            else:
                self.puffer.append(self._uri(uri))

    def _uri(self, uri: str) -> bytes:
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        from urllib.parse import unquote
        pfad = os.path.normpath(os.path.join(self.ordner, unquote(uri)))
        if os.path.commonpath([os.path.abspath(pfad), os.path.abspath(self.ordner)]) != os.path.abspath(self.ordner):
            raise ModellImportFehler("glTF: reference outside the folder: %s" % uri)
        with open(pfad, "rb") as f:
            return f.read()

    def sicht(self, nr: int) -> bytes:
        v = self.j["bufferViews"][nr]
        o = v.get("byteOffset", 0)
        return self.puffer[v["buffer"]][o:o + v["byteLength"]]

    def zugriff(self, nr: int) -> np.ndarray:
        a = self.j["accessors"][nr]
        if "sparse" in a:
            raise ModellImportFehler("glTF: sparse accessors are not supported")
        dt = np.dtype(_KOMPONENTE[a["componentType"]])
        k, n = _ANZAHL[a["type"]], a["count"]
        if "bufferView" not in a:
            return np.zeros((n, k), np.float64)
        v = self.j["bufferViews"][a["bufferView"]]
        roh = self.puffer[v["buffer"]]
        start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        breite = dt.itemsize * k
        schritt = v.get("byteStride") or breite
        if schritt == breite:
            w = np.frombuffer(roh, dt, n * k, start).reshape(n, k)
        else:
            m = np.frombuffer(roh, np.uint8, (n - 1) * schritt + breite, start)
            idx = (np.arange(n) * schritt)[:, None] + np.arange(breite)[None, :]
            w = np.ascontiguousarray(m[idx]).view(dt).reshape(n, k)
        w = w.astype(np.float64)
        if a.get("normalized"):
            w = {"u1": w / 255.0, "<u2": w / 65535.0, "i1": np.maximum(w / 127.0, -1.0),
                 "<i2": np.maximum(w / 32767.0, -1.0)}[_KOMPONENTE[a["componentType"]]]
        return w

    def bild(self, textur_info: Optional[dict]) -> Optional[np.ndarray]:
        if not textur_info:
            return None
        tex = self.j["textures"][textur_info["index"]]
        if "source" not in tex:
            return None
        b = self.j["images"][tex["source"]]
        daten = self.sicht(b["bufferView"]) if "bufferView" in b else self._uri(b["uri"])
        return _bild_rgba(daten)


def _gltf_effekt(extras: dict, name: str, hinweise: List[str]) -> Optional[dict]:
    """A3: Effekt eines Materials aus seinen glTF-Extras (Blender: benutzerdefinierte Eigenschaften des Materials,
    Export mit *Include → Custom Properties*): ``f1_effect`` = water|scroll|flag, dazu optional ``f1_speed_u``,
    ``f1_speed_v``, ``f1_brightness`` (Lauftext) und ``f1_speed`` (Faktor für Wasser)."""
    roh = extras.get("f1_effect")
    if not roh:
        return None
    art = GLTF_EFFEKT.get(str(roh).strip().lower())
    if art is None:
        hinweise.append("Material %s: unknown effect %s (water, scroll or flag)" % (name, roh))
        return None
    e = {"art": art}
    for schluessel, feld in (("f1_speed_u", "u"), ("f1_speed_v", "v"), ("f1_brightness", "helligkeit"), ("f1_speed", "tempo")):
        if schluessel in extras:
            try:
                e[feld] = float(extras[schluessel])
            except (TypeError, ValueError):
                hinweise.append("Material %s: %s must be a number" % (name, schluessel))
    return e


def _knotenmatrix(k: dict) -> np.ndarray:
    if "matrix" in k:
        return np.array(k["matrix"], float).reshape(4, 4).T        # glTF speichert spaltenweise
    return grafik.trs_matrix(k.get("translation", (0, 0, 0)), k.get("rotation", (0, 0, 0, 1)), k.get("scale", (1, 1, 1)))


def _gltf_szene(g: _Gltf, quelle: str) -> Szene:
    j = g.j
    benutzt = set(j.get("extensionsRequired", []))
    for e in _NICHT_UNTERSTUETZT:
        if e in benutzt:
            raise ModellImportFehler("glTF: extension %s is not supported (export without compression)" % e)
    sz = Szene(quelle=quelle)
    if j.get("asset", {}).get("version", "2.0")[:1] != "2":
        raise ModellImportFehler("glTF: only version 2.0 is supported")
    if j.get("skins"):
        sz.hinweise.append("Skinning is ignored (static objects only)")
    namen = []
    for i, m in enumerate(j.get("materials", [])):
        name = m.get("name") or "material_%d" % i
        while name in namen:
            name += "_"
        namen.append(name)
        pbr = m.get("pbrMetallicRoughness", {})
        for info in (pbr.get("baseColorTexture"), m.get("normalTexture"), pbr.get("metallicRoughnessTexture"),
                     m.get("occlusionTexture")):
            if info and info.get("texCoord", 0) != 0:
                sz.hinweise.append("Material %s: texture uses UV set %d, the game material uses UV0" % (name, info["texCoord"]))
            if info and "KHR_texture_transform" in info.get("extensions", {}):
                sz.hinweise.append("Material %s: KHR_texture_transform is ignored" % name)
        verdeckung = m.get("occlusionTexture")
        sz.materialien[name] = QuellMaterial(
            name, tuple(float(x) for x in pbr.get("baseColorFactor", (1, 1, 1, 1))), g.bild(pbr.get("baseColorTexture")),
            g.bild(m.get("normalTexture")), g.bild(pbr.get("metallicRoughnessTexture")),
            m.get("alphaMode", "OPAQUE"), bool(m.get("doubleSided", False)),
            alpha_schwelle=float(m.get("alphaCutoff", 0.5)),
            # glTF-Standard: fehlende Faktoren sind 1 (Blender schreibt sie nur, wenn sie davon abweichen)
            metall=float(pbr.get("metallicFactor", 1.0)), rauheit=float(pbr.get("roughnessFactor", 1.0)),
            bild_verdeckung=g.bild(verdeckung), verdeckung_staerke=float((verdeckung or {}).get("strength", 1.0)),
            effekt=_gltf_effekt(m.get("extras") or {}, name, sz.hinweise))
    wurzeln = j.get("scenes", [{}])[j.get("scene", 0)].get("nodes") if j.get("scenes") else None
    if wurzeln is None:
        kinder = {c for k in j.get("nodes", []) for c in k.get("children", [])}
        wurzeln = [i for i in range(len(j.get("nodes", []))) if i not in kinder]
    kanaele, anim_name = _gltf_kanaele(g, sz)
    knotennamen: Dict[int, str] = {}
    for nr in sorted(kanaele):
        n = _anim.clipname(j["nodes"][nr].get("name") or "node%d" % nr)
        while n in knotennamen.values():
            n += "_"
        knotennamen[nr] = n
    bewegt = []                       # (Knoten, bewegter Vorfahr, Welt des glTF-Elternknotens, Welt in Ruhe)
    welt_ruhe: Dict[int, np.ndarray] = {}
    stapel = [(n, np.eye(4), None) for n in wurzeln]
    while stapel:
        nr, eltern, anker = stapel.pop(0)
        k = j["nodes"][nr]
        welt = eltern @ _knotenmatrix(k)
        if nr in kanaele:
            bewegt.append((nr, anker, eltern, welt))
            welt_ruhe[nr] = welt
            anker_selbst = nr
        else:
            anker_selbst = anker
        stapel += [(c, welt, anker_selbst) for c in k.get("children", [])]
        if "mesh" not in k:
            continue
        mesh = j["meshes"][k["mesh"]]
        lin = welt[:3, :3]
        gespiegelt = np.linalg.det(lin) < 0
        nmat = np.linalg.inv(lin).T
        for pi, prim in enumerate(mesh.get("primitives", [])):
            if prim.get("mode", 4) != 4:
                sz.hinweise.append("Primitive without a triangle list left out (mode %s)" % prim.get("mode"))
                continue
            if "extensions" in prim and any(e in prim["extensions"] for e in _NICHT_UNTERSTUETZT):
                raise ModellImportFehler("glTF: compressed geometry is not supported")
            if prim.get("targets"):
                sz.hinweise.append("Morph targets are ignored")
            at = prim["attributes"]
            if "POSITION" not in at:
                continue
            pos = g.zugriff(at["POSITION"])[:, :3] @ lin.T + welt[:3, 3]
            tri = (g.zugriff(prim["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in prim
                   else np.arange(len(pos) // 3 * 3, dtype=np.int64).reshape(-1, 3))
            nrm = tan = None
            if "NORMAL" in at:
                nrm = g.zugriff(at["NORMAL"])[:, :3] @ nmat.T
                nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
            if "TANGENT" in at and nrm is not None:
                t4 = g.zugriff(at["TANGENT"])
                txyz = t4[:, :3] @ lin.T
                txyz /= np.maximum(np.linalg.norm(txyz, axis=1, keepdims=True), 1e-12)
                tan = np.concatenate([txyz, np.where(t4[:, 3:4] < 0, -1.0, 1.0) * (-1.0 if gespiegelt else 1.0)], axis=1)
            if gespiegelt:
                tri = tri[:, ::-1].copy()
            mat = namen[prim["material"]] if "material" in prim else "ohne_material"
            if mat == "ohne_material" and mat not in sz.materialien:
                sz.materialien[mat] = QuellMaterial(mat)
            farbe = g.zugriff(at["COLOR_0"]) if "COLOR_0" in at else None
            if farbe is not None and farbe.shape[1] == 3:
                farbe = np.concatenate([farbe, np.ones((len(farbe), 1))], axis=1)
            sz.teile.append(Teil("%s_%d" % (k.get("name") or mesh.get("name") or "mesh%d" % k["mesh"], pi), mat, pos, tri,
                                 nrm, tan, g.zugriff(at["TEXCOORD_0"])[:, :2] if "TEXCOORD_0" in at else None,
                                 g.zugriff(at["TEXCOORD_1"])[:, :2] if "TEXCOORD_1" in at else None, farbe,
                                 knotennamen.get(anker_selbst) if anker_selbst is not None else None))
    if not sz.teile:
        raise ModellImportFehler("glTF: no triangles found")
    if bewegt:
        sz.animation = _gltf_animation(j, kanaele, anim_name, knotennamen, bewegt, welt_ruhe, sz.hinweise)
        if sz.animation is None:
            for t in sz.teile:
                t.knoten = None
    return sz


#: Abtastung der glTF-Animation (A1): Bildraster der 16-Bit-Drehung, Bildrate für CUBICSPLINE, größter Drehschritt
#: zwischen zwei Schlüsseln, Toleranz „Kanal fest“
ANIM_BILDRATE = _anim.BILDRATE
ANIM_KUBISCH_HZ = 30.0
ANIM_MAX_DREHSCHRITT_GRAD = 30.0
ANIM_TOLERANZ = 1e-5


def _gltf_kanaele(g: "_Gltf", sz: "Szene") -> Tuple[Dict[int, Dict[str, Tuple[np.ndarray, np.ndarray, str]]], str]:
    """Kanäle der ersten glTF-Animation je Knoten: ({Knoten: {Pfad: (Zeiten, Werte, Interpolation)}}, Name)."""
    j = g.j
    anims = j.get("animations") or []
    if not anims:
        return {}, ""
    a = anims[0]
    name = a.get("name") or "loop"
    if len(anims) > 1:
        sz.hinweise.append("Only the first animation (%s) is used, %d more are ignored" % (name, len(anims) - 1))
    kanaele: Dict[int, Dict[str, Tuple[np.ndarray, np.ndarray, str]]] = {}
    morph = False
    for c in a.get("channels", []):
        ziel = c.get("target", {})
        pfad, nr = ziel.get("path"), ziel.get("node")
        if pfad == "weights":
            morph = True
            continue
        if nr is None or pfad not in ("translation", "rotation", "scale"):
            continue
        s = a["samplers"][c["sampler"]]
        zeiten = g.zugriff(s["input"])[:, 0]
        werte = g.zugriff(s["output"])
        interp = s.get("interpolation", "LINEAR")
        if interp not in ("LINEAR", "STEP", "CUBICSPLINE"):
            raise ModellImportFehler("glTF: unknown interpolation %s" % interp)
        if len(zeiten) == 0 or len(werte) != len(zeiten) * (3 if interp == "CUBICSPLINE" else 1):
            raise ModellImportFehler("glTF: animation sampler with mismatching key counts")
        kanaele.setdefault(nr, {})[pfad] = (zeiten, werte, interp)
    if morph:
        sz.hinweise.append("Morph target animation is ignored")
    return kanaele, name


def _gltf_animation(j, kanaele, anim_name, knotennamen, bewegt, welt_ruhe, hinweise) -> Optional["_anim.Animation"]:
    """Kurven der bewegten Knoten im Spielformat: lokal zum nächsten bewegten Vorfahren (feste Zwischenknoten
    eingerechnet), Zeiten ab 0 auf dem Bildraster von 60 Hz, Drehschritte höchstens ``ANIM_MAX_DREHSCHRITT_GRAD``."""
    alle = np.concatenate([z for kn in kanaele.values() for (z, _, _) in kn.values()])
    t0, t1 = float(alle.min()), float(alle.max())
    laenge = t1 - t0
    if laenge <= 0:
        hinweise.append("Animation %s has no duration and is ignored" % anim_name)
        return None
    if laenge > 0xFFFF / ANIM_BILDRATE:
        raise ModellImportFehler("Animation %s is %.0f s long (at most %.0f s)" % (anim_name, laenge, 0xFFFF / ANIM_BILDRATE))
    animation = _anim.Animation(_anim.clipname(anim_name), round(laenge * ANIM_BILDRATE) / ANIM_BILDRATE)

    def raster(x):
        return np.unique(np.round((np.asarray(x, float) - t0) * ANIM_BILDRATE)) / ANIM_BILDRATE

    for nr, anker, eltern_welt, welt in bewegt:
        k = j["nodes"][nr]
        if "matrix" in k:
            t_r, q_r, s_r = _anim.matrix_zerlegen(_knotenmatrix(k))
        else:
            t_r = np.array(k.get("translation", (0, 0, 0)), float)
            q_r = np.array(k.get("rotation", (0, 0, 0, 1)), float)
            s_r = np.array(k.get("scale", (1, 1, 1)), float)
        basis = np.linalg.inv(welt_ruhe[anker]) @ eltern_welt if anker is not None else eltern_welt
        kn = kanaele[nr]
        zeiten = [z for (z, _, _) in kn.values()]
        for z, _, interp in kn.values():
            if interp == "STEP" and len(z) > 1:
                zeiten.append(np.asarray(z[1:]) - 1.0 / ANIM_BILDRATE)
            elif interp == "CUBICSPLINE":
                zeiten.append(np.arange(z[0], z[-1], 1.0 / ANIM_KUBISCH_HZ))
        zeiten.append(np.array([t0, t1]))
        tz = raster(np.concatenate(zeiten))
        tz = tz[(tz >= 0) & (tz <= animation.laenge_s + 1e-9)]

        def lokal(t, kn=kn, basis=basis, t_r=t_r, q_r=q_r, s_r=s_r):
            tt = t + t0
            tr = _anim.kanal_wert(*kn["translation"], tt, False) if "translation" in kn else t_r
            q = _anim.kanal_wert(*kn["rotation"], tt, True) if "rotation" in kn else q_r
            sc = _anim.kanal_wert(*kn["scale"], tt, False) if "scale" in kn else s_r
            return _anim.matrix_zerlegen(basis @ grafik.trs_matrix(tr, q, sc))

        proben = [(float(t), lokal(float(t))) for t in tz]
        # große Drehschritte unterteilen (das Spiel interpoliert zwischen zwei Schlüsseln, glTF per slerp)
        i = 0
        while i < len(proben) - 1:
            (ta, (_, qa, _)), (tb, (_, qb, _)) = proben[i], proben[i + 1]
            winkel = 2 * math.degrees(math.acos(min(1.0, abs(float(np.dot(qa, qb))))))
            tm = round((ta + tb) / 2 * ANIM_BILDRATE) / ANIM_BILDRATE
            if winkel > ANIM_MAX_DREHSCHRITT_GRAD and ta < tm < tb:
                proben.insert(i + 1, (tm, lokal(tm)))
            else:
                i += 1
        tz = np.array([p[0] for p in proben])
        T = np.array([p[1][0] for p in proben])
        Q = _anim.quaternionen_glaetten(np.array([p[1][1] for p in proben]))
        S = np.array([p[1][2] for p in proben])
        ruhe_t, ruhe_q, ruhe_s = _anim.matrix_zerlegen(basis @ grafik.trs_matrix(t_r, q_r, s_r))
        if np.any(S <= 0):
            hinweise.append("Animation %s: negative scale of %s is not supported" % (anim_name, knotennamen[nr]))
        kurve = _anim.Knotenkurve(
            verschiebung=None if np.abs(T - ruhe_t[None, :]).max() < ANIM_TOLERANZ else (tz, T),
            drehung=None if np.abs(np.abs(Q @ ruhe_q) - 1).max() < ANIM_TOLERANZ else (tz, Q),
            skalierung=None if np.abs(S - ruhe_s[None, :]).max() < ANIM_TOLERANZ else (tz, S),
            ruhe_t=tuple(map(float, ruhe_t)), ruhe_r=tuple(map(float, ruhe_q)), ruhe_s=tuple(map(float, ruhe_s)))
        animation.knoten.append(_anim.AnimKnoten(knotennamen[nr], knotennamen[anker] if anker is not None else None,
                                                 welt, kurve))
    return animation


def glb_lesen(pfad) -> Szene:
    """Binäres glTF 2.0 (.glb) lesen; Knotentransformationen werden in die Vertices gerechnet."""
    with open(pfad, "rb") as f:
        d = f.read()
    if d[:4] != b"glTF" or struct.unpack_from("<I", d, 4)[0] != 2:
        raise ModellImportFehler("Not a GLB file of version 2: %s" % pfad)
    o, j, binaer = 12, None, None
    while o + 8 <= len(d):
        laenge, art = struct.unpack_from("<I4s", d, o)
        teil = d[o + 8:o + 8 + laenge]
        if art == b"JSON":
            j = json.loads(teil.decode("utf-8"))
        elif art == b"BIN\0" and binaer is None:
            binaer = teil
        o += 8 + laenge
    if j is None:
        raise ModellImportFehler("GLB without a JSON chunk")
    return _gltf_szene(_Gltf(j, binaer, os.path.dirname(os.path.abspath(pfad))), str(pfad))


def gltf_lesen(pfad) -> Szene:
    """Text-glTF 2.0 (.gltf) mit eingebetteten (data:) oder benachbarten Dateien."""
    with open(pfad, "r", encoding="utf-8") as f:
        j = json.load(f)
    return _gltf_szene(_Gltf(j, None, os.path.dirname(os.path.abspath(pfad))), str(pfad))


def obj_lesen(pfad, z_oben: bool = False) -> Szene:
    """Wavefront-OBJ (Rückfall): v/vt/vn/f/usemtl, Vielecke als Fächer. OBJ-``vt`` hat v nach oben und wird
    gedreht. ``z_oben``: Datei wurde mit Z oben geschrieben (Blender ohne Achsenumrechnung) → (x, z, −y)."""
    v, vt, vn = [], [], []
    gruppen: Dict[str, List[Tuple[Tuple[int, int, int], ...]]] = {}
    mat = "ohne_material"
    with open(pfad, "r", encoding="utf-8", errors="replace") as f:
        for zeile in f:
            t = zeile.split()
            if not t or t[0].startswith("#"):
                continue
            if t[0] == "v":
                v.append([float(x) for x in t[1:4]])
            elif t[0] == "vt":
                vt.append([float(t[1]), 1.0 - float(t[2]) if len(t) > 2 else 0.0])
            elif t[0] == "vn":
                vn.append([float(x) for x in t[1:4]])
            elif t[0] == "usemtl":
                mat = t[1] if len(t) > 1 else "ohne_material"
            elif t[0] == "f":
                ecken = []
                for e in t[1:]:
                    teile = (e.split("/") + ["", ""])[:3]
                    ecken.append(tuple((int(x) - 1 if int(x) > 0 else int(x)) if x else None for x in teile))
                for i in range(1, len(ecken) - 1):
                    gruppen.setdefault(mat, []).append((ecken[0], ecken[i], ecken[i + 1]))
    if not gruppen:
        raise ModellImportFehler("OBJ without faces: %s" % pfad)
    va, vta, vna = np.array(v, float).reshape(-1, 3), np.array(vt, float).reshape(-1, 2), np.array(vn, float).reshape(-1, 3)
    if z_oben:
        va = np.stack([va[:, 0], va[:, 2], -va[:, 1]], axis=1)
        vna = np.stack([vna[:, 0], vna[:, 2], -vna[:, 1]], axis=1) if len(vna) else vna
    sz = Szene(quelle=str(pfad))
    for name, flaechen in gruppen.items():
        ecken: Dict[Tuple, int] = {}
        tri = []
        for fl in flaechen:
            tri.append([ecken.setdefault(e, len(ecken)) for e in fl])
        schluessel = list(ecken)
        pos = va[[e[0] for e in schluessel]]
        uv = vta[[e[1] for e in schluessel]] if all(e[1] is not None for e in schluessel) and len(vta) else None
        nrm = vna[[e[2] for e in schluessel]] if all(e[2] is not None for e in schluessel) and len(vna) else None
        sz.teile.append(Teil(name, name, pos, np.array(tri, np.int64), nrm, None, uv))
        sz.materialien[name] = QuellMaterial(name)
    sz.hinweise.append("OBJ: materials by name only (the MTL file is not read)")
    return sz


def laden(pfad, **kw) -> Szene:
    endung = os.path.splitext(str(pfad))[1].lower()
    if endung == ".glb":
        return glb_lesen(pfad)
    if endung == ".gltf":
        return gltf_lesen(pfad)
    if endung == ".obj":
        return obj_lesen(pfad, **kw)
    raise ModellImportFehler("Unknown format %s (expected .glb, .gltf or .obj)" % endung)


# --------------------------------------------------------------------------- glTF schreiben (Tests, Maßvorlagen)
def glb_schreiben(szene: Szene, pfad) -> None:
    """Schreibt eine Szene als .glb (ein Mesh je Teil; Basisfarbe, Metall- und Rauheitsfaktor, Bilder für
    Basisfarbe/Normalen/ORM/Verdeckung als PNG, Alpha-Modus, Maskenschwelle und Doppelseitigkeit je Material).
    Für Tests und um ein Modell als Maßvorlage in Blender zu öffnen."""
    from PIL import Image
    binaer = bytearray()
    sichten, zugriffe, meshes, knoten, bilder, texturen, materialien = [], [], [], [], [], [], []

    def sicht(daten: bytes, ziel: Optional[int] = None) -> int:
        while len(binaer) % 4:
            binaer.append(0)
        v = {"buffer": 0, "byteOffset": len(binaer), "byteLength": len(daten)}
        if ziel:
            v["target"] = ziel
        binaer.extend(daten)
        sichten.append(v)
        return len(sichten) - 1

    def zugriff(a: np.ndarray, typ: str, komponente: int, ziel: int, grenzen: bool = False) -> int:
        z = {"bufferView": sicht(a.tobytes(), ziel), "componentType": komponente, "count": len(a), "type": typ}
        if grenzen:
            z["min"], z["max"] = a.min(0).tolist(), a.max(0).tolist()
        zugriffe.append(z)
        return len(zugriffe) - 1

    mat_index = {}
    for name, m in szene.materialien.items():
        eintrag = {"name": name, "pbrMetallicRoughness": {"baseColorFactor": list(m.basisfarbe),
                                                          "metallicFactor": float(m.metall)}}
        if m.rauheit != 1.0:
            eintrag["pbrMetallicRoughness"]["roughnessFactor"] = float(m.rauheit)
        def bild_index(rgba) -> int:
            puffer = io.BytesIO()
            Image.fromarray(np.asarray(rgba, np.uint8), "RGBA").save(puffer, "PNG")
            bilder.append({"bufferView": sicht(puffer.getvalue()), "mimeType": "image/png"})
            texturen.append({"source": len(bilder) - 1})
            return len(texturen) - 1

        if m.bild_basisfarbe is not None:
            eintrag["pbrMetallicRoughness"]["baseColorTexture"] = {"index": bild_index(m.bild_basisfarbe)}
        if m.bild_normal is not None:
            eintrag["normalTexture"] = {"index": bild_index(m.bild_normal)}
        if m.bild_orm is not None:
            eintrag["pbrMetallicRoughness"]["metallicRoughnessTexture"] = {"index": bild_index(m.bild_orm)}
        if m.bild_verdeckung is not None:
            gleich = m.bild_orm is not None and (m.bild_verdeckung is m.bild_orm or (
                m.bild_verdeckung.shape == m.bild_orm.shape and np.array_equal(m.bild_verdeckung, m.bild_orm)))
            eintrag["occlusionTexture"] = {"index": eintrag["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]
                                           if gleich else bild_index(m.bild_verdeckung)}
            if m.verdeckung_staerke != 1.0:
                eintrag["occlusionTexture"]["strength"] = float(m.verdeckung_staerke)
        if m.alpha != "OPAQUE":
            eintrag["alphaMode"] = m.alpha
            if m.alpha == "MASK" and m.alpha_schwelle != 0.5:
                eintrag["alphaCutoff"] = float(m.alpha_schwelle)
        if m.doppelseitig:
            eintrag["doubleSided"] = True
        mat_index[name] = len(materialien)
        materialien.append(eintrag)
    for t in szene.teile:
        at = {"POSITION": zugriff(np.asarray(t.positionen, "<f4"), "VEC3", 5126, 34962, True)}
        if t.normalen is not None:
            at["NORMAL"] = zugriff(np.asarray(t.normalen, "<f4"), "VEC3", 5126, 34962)
        if t.tangenten is not None:
            at["TANGENT"] = zugriff(np.asarray(t.tangenten, "<f4"), "VEC4", 5126, 34962)
        if t.uv0 is not None:
            at["TEXCOORD_0"] = zugriff(np.asarray(t.uv0, "<f4"), "VEC2", 5126, 34962)
        if t.uv1 is not None:
            at["TEXCOORD_1"] = zugriff(np.asarray(t.uv1, "<f4"), "VEC2", 5126, 34962)
        ind = np.asarray(t.dreiecke, "<u4").reshape(-1, 1)
        prim = {"attributes": at, "indices": zugriff(ind, "SCALAR", 5125, 34963), "mode": 4}
        if t.material in mat_index:
            prim["material"] = mat_index[t.material]
        meshes.append({"name": t.name, "primitives": [prim]})
        knoten.append({"name": t.name, "mesh": len(meshes) - 1})
    j = {"asset": {"version": "2.0", "generator": "ego-werkzeuge modell_import"}, "scene": 0,
         "scenes": [{"nodes": list(range(len(knoten)))}], "nodes": knoten, "meshes": meshes,
         "accessors": zugriffe, "bufferViews": sichten, "buffers": [{"byteLength": len(binaer)}]}
    if materialien:
        j["materials"] = materialien
    if bilder:
        j["images"], j["textures"] = bilder, texturen
    js = json.dumps(j, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    while len(binaer) % 4:
        binaer.append(0)
    with open(pfad, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binaer)))
        f.write(struct.pack("<I4s", len(js), b"JSON") + js)
        f.write(struct.pack("<I4s", len(binaer), b"BIN\0") + bytes(binaer))


# --------------------------------------------------------------------------- Geometrie aufbereiten
def tangenten_berechnen(pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Tangenten (N, 4) in der Konvention des Spiels (gemessen an 1.489 Originalmodellen): xyz zeigt entlang
    dP/du, senkrecht zur Normale; ``w · cross(N, T)`` zeigt **gegen** dP/dv (v wächst im Bild nach unten).
    Das ist dieselbe Konvention wie glTF/MikkTSpace."""
    pos, nrm, uv = np.asarray(pos, float), np.asarray(nrm, float), np.asarray(uv, float)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = uv[tri[:, 1]] - uv[tri[:, 0]], uv[tri[:, 2]] - uv[tri[:, 0]]
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    gut = np.abs(det) > 1e-14
    det = np.where(gut, det, 1.0)
    dpdu = np.where(gut[:, None], (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / det[:, None], 0.0)
    dpdv = np.where(gut[:, None], (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / det[:, None], 0.0)
    # flächengewichtet: |det| hebt sich gegen die Parametrisierung auf, übrig bleibt die Dreiecksfläche
    gew = np.linalg.norm(np.cross(e1, e2), axis=1, keepdims=True)
    lu = np.maximum(np.linalg.norm(dpdu, axis=1, keepdims=True), 1e-20)
    lv = np.maximum(np.linalg.norm(dpdv, axis=1, keepdims=True), 1e-20)
    su, sv = np.zeros_like(pos), np.zeros_like(pos)
    for k in range(3):
        np.add.at(su, tri[:, k], dpdu / lu * gew)
        np.add.at(sv, tri[:, k], dpdv / lv * gew)
    t = su - nrm * (su * nrm).sum(1, keepdims=True)
    laenge = np.linalg.norm(t, axis=1)
    ersatz = np.cross(nrm, np.array([0.0, 1.0, 0.0]))
    ersatz[np.linalg.norm(ersatz, axis=1) < 1e-6] = (1.0, 0.0, 0.0)
    t = np.where(laenge[:, None] > 1e-9, t, ersatz)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    w = np.where((np.cross(nrm, t) * sv).sum(1) > 0, -1.0, 1.0)
    return np.concatenate([t, w[:, None]], axis=1)


def huellkugel(p: np.ndarray) -> Tuple[float, float, float, float]:
    """Kleine, sichere Hüllkugel: Ritter-Näherung oder AABB-Mittelpunkt, die engere von beiden; Radius nach außen
    gerundet. (Originale: Radius im Median 0,96 × halbe AABB-Diagonale, also ebenfalls enger als die AABB-Kugel.)"""
    p = np.asarray(p, np.float64)
    mn, mx = p.min(0), p.max(0)
    c1 = (mn + mx) / 2
    r1 = float(np.sqrt(((p - c1) ** 2).sum(1).max()))
    a = p[np.argmax(((p - p[0]) ** 2).sum(1))]
    b = p[np.argmax(((p - a) ** 2).sum(1))]
    c2, r2 = (a + b) / 2, float(np.linalg.norm(b - a)) / 2
    for _ in range(8):
        d = np.sqrt(((p - c2) ** 2).sum(1))
        i = int(np.argmax(d))
        if d[i] <= r2 * (1 + 1e-9):
            break
        neu = (r2 + d[i]) / 2
        c2 = c2 + (p[i] - c2) * ((d[i] - r2) / (2 * d[i]))
        r2 = neu
    r2 = float(np.sqrt(((p - c2) ** 2).sum(1).max()))
    c, r = (c2, r2) if r2 < r1 else (c1, r1)
    c = c.astype(np.float32)
    r = float(np.sqrt(((p - c.astype(np.float64)) ** 2).sum(1).max()))
    return (float(c[0]), float(c[1]), float(c[2]), float(np.nextafter(np.float32(r * (1 + 1e-6) + 1e-6), np.float32(np.inf))))


def _teil_aufbereiten(t: Teil, hinweise: List[str]) -> Teil:
    pos = np.asarray(t.positionen, np.float64).reshape(-1, 3)
    tri = np.asarray(t.dreiecke, np.int64).reshape(-1, 3)
    if not len(tri) or not len(pos):
        raise ModellImportFehler("Part %s is empty" % t.name)
    if tri.min() < 0 or tri.max() >= len(pos):
        raise ModellImportFehler("Part %s: triangle indices outside the vertex list" % t.name)
    if not np.isfinite(pos).all():
        raise ModellImportFehler("Part %s: positions contain NaN/Inf" % t.name)
    flaeche = np.linalg.norm(np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]]), axis=1)
    entartet = flaeche <= 1e-12
    if entartet.any():
        hinweise.append("Part %s: %d degenerate triangles removed" % (t.name, int(entartet.sum())))
        tri = tri[~entartet]
        if not len(tri):
            raise ModellImportFehler("Part %s: only degenerate triangles" % t.name)
    if t.normalen is None:
        nrm = grafik.normalen_berechnen(pos, tri)
        hinweise.append("Part %s: normals missing, calculated from the faces (smooth)" % t.name)
    else:
        nrm = np.asarray(t.normalen, np.float64)
        nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    if t.uv0 is None:
        uv = np.zeros((len(pos), 2))
        hinweise.append("Part %s: no UV, the texture appears in one colour" % t.name)
    else:
        uv = np.asarray(t.uv0, np.float64)
        if np.abs(uv).max() > UV_HALB_WARNUNG:
            hinweise.append("Part %s: UV up to %.0f, imprecise with 16-bit floats (reduce the tiling)" % (
                t.name, np.abs(uv).max()))
    tan = tangenten_berechnen(pos, nrm, uv, tri) if t.tangenten is None else np.asarray(t.tangenten, np.float64)
    uv1 = np.tile(LICHT_UV_STANDARD, (len(pos), 1)) if t.uv1 is None else np.clip(np.asarray(t.uv1, np.float64), 0.0, 1.0)
    farben = None if t.farben is None else np.clip(np.asarray(t.farben, np.float64).reshape(len(pos), -1)[:, :4], 0.0, 1.0)
    # nur benutzte Vertices behalten
    benutzt, lokal = np.unique(tri, return_inverse=True)
    if len(benutzt) != len(pos):
        pos, nrm, uv, tan, uv1 = pos[benutzt], nrm[benutzt], uv[benutzt], tan[benutzt], uv1[benutzt]
        farben = None if farben is None else farben[benutzt]
        tri = lokal.reshape(-1, 3)
    return Teil(t.name, t.material, pos, tri, nrm, tan, uv, uv1, farben, knoten=t.knoten)


def _raeumlich_sortiert(tri: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """Dreiecke in Z-Ordnung (Morton-Code der Schwerpunkte, 10 Bit je Achse über die AABB): benachbarte Dreiecke
    liegen danach nah beieinander in der Liste, so dass ``_stuecke`` räumlich zusammenhängende Stücke schneidet."""
    s = np.asarray(pos, np.float64)[tri].mean(axis=1)
    lo, hi = s.min(0), s.max(0)
    q = np.clip(((s - lo) / np.maximum(hi - lo, 1e-12) * 1023).astype(np.uint64), 0, 1023)
    code = np.zeros(len(tri), np.uint64)
    for bit in range(10):
        for achse in range(3):
            code |= ((q[:, achse] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(3 * bit + achse)
    return tri[np.argsort(code, kind="stable")]


def _stuecke(tri: np.ndarray, vertices: int, grenze: int = INDEX_GRENZE, pos: Optional[np.ndarray] = None
             ) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Teilt ein Netz so, dass jedes Stück höchstens ``grenze`` + 1 Vertices nutzt (16-Bit-Indizes).
    Rückgabe je Stück: (benutzte Vertexnummern, Dreiecke mit lokalen Indizes). Mit ``pos`` wird zusätzlich eine
    räumlich sortierte Reihenfolge geteilt und die mit weniger Vertices genommen; sonst teilt die Dreiecksreihenfolge, und liegen Nachbardreiecke weit auseinander in
    der Liste, verdoppeln sich die Vertices an jeder Schnittstelle (M0: 180.810 statt 90.513)."""
    if vertices <= grenze + 1:
        return [(np.arange(vertices), tri)]
    if pos is not None:
        a, b = _stuecke(tri, vertices, grenze), _stuecke(_raeumlich_sortiert(tri, pos), vertices, grenze)
        return a if sum(len(x) for x, _ in a) <= sum(len(x) for x, _ in b) else b
    aus, karte, liste, lokal = [], {}, [], []
    for a, b, c in tri.tolist():
        neu = sum(1 for x in {a, b, c} if x not in karte)
        if len(liste) + neu > grenze + 1:
            aus.append((np.array(liste, np.int64), np.array(lokal, np.int64)))
            karte, liste, lokal = {}, [], []
        dreieck = []
        for x in (a, b, c):
            if x not in karte:
                karte[x] = len(liste)
                liste.append(x)
            dreieck.append(karte[x])
        lokal.append(dreieck)
    aus.append((np.array(liste, np.int64), np.array(lokal, np.int64)))
    return aus


def _doppelseitig(t: Teil) -> Teil:
    """Rückseiten als Geometrie (M5a): jedes Dreieck noch einmal mit umgekehrtem Umlaufsinn auf eigenen Vertices mit
    umgekehrter Normale. Die Tangente bleibt (dP/du ist dasselbe), ihr ``w`` wechselt das Vorzeichen, damit
    ``w · cross(N, T)`` weiter gegen dP/dv zeigt. Von jeder Seite ist genau eine der beiden Flächen sichtbar (die
    andere ist Rückseite und wird nicht gezeichnet), es flimmert also nichts. Gilt für jeden Shader und jeden Träger
    (``object_opaque_double`` gibt es nur für deckende Flächen und nicht in jedem Träger)."""
    n = len(t.positionen)
    tan = np.array(t.tangenten, np.float64, copy=True)
    tan[:, 3] *= -1.0
    return Teil(t.name, t.material, np.concatenate([t.positionen, t.positionen]),
                np.concatenate([t.dreiecke, t.dreiecke[:, ::-1] + n]), np.concatenate([t.normalen, -t.normalen]),
                np.concatenate([t.tangenten, tan]), np.concatenate([t.uv0, t.uv0]), np.concatenate([t.uv1, t.uv1]),
                None if t.farben is None else np.concatenate([t.farben, t.farben]), knoten=t.knoten)


def _in_knotenraum(teile: Sequence[Teil], anim: "_anim.Animation") -> List[Teil]:
    """Teile bewegter Knoten aus der Ruhelage im Modellraum in den Raum ihres Knotens rechnen (A1)."""
    import dataclasses
    inv = {k.name: np.linalg.inv(k.welt_ruhe) for k in anim.knoten}
    aus = []
    for t in teile:
        if t.knoten is None:
            aus.append(t)
            continue
        if t.knoten not in inv:
            raise ModellImportFehler("Moving part %s is missing in the animation of LOD 0" % t.knoten)
        m = inv[t.knoten]
        lin = m[:3, :3]
        pos = np.asarray(t.positionen, np.float64) @ lin.T + m[:3, 3]
        nrm = tan = None
        if t.normalen is not None:
            nrm = np.asarray(t.normalen, np.float64) @ np.linalg.inv(lin)
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        if t.tangenten is not None:
            t4 = np.asarray(t.tangenten, np.float64)
            txyz = t4[:, :3] @ lin.T
            txyz /= np.maximum(np.linalg.norm(txyz, axis=1, keepdims=True), 1e-12)
            tan = np.concatenate([txyz, t4[:, 3:4]], axis=1)
        aus.append(dataclasses.replace(t, positionen=pos, normalen=nrm, tangenten=tan))
    return aus


# --------------------------------------------------------------------------- Ressourcen
@dataclass
class Ressource:
    kennung: str
    typ: str
    fragmente: List[Tuple[bytes, bytes, int, bool]]   # (Name, Daten, Flags, komprimieren)
    unknown: int

    @property
    def daten(self) -> bytes:
        return self.fragmente[0][1]


def _res(kennung: str, typ: str, daten: bytes) -> Ressource:
    return Ressource(kennung, typ, [(b"temp", daten, FRAGMENT_FLAGS, True)], grafik.TYP_UNBEKANNT[typ])


def _puffer_res(kennung: str, art: int, stride: int, code: int, daten: bytes) -> Ressource:
    kopf = grafik.GfxPuffer(art, stride, len(daten) // stride, code).kopf_bytes()
    return Ressource(kennung, "GfxBufferRes", [(b"temp", kopf, FRAGMENT_FLAGS, False),
                                               (b"temp", daten, FRAGMENT_FLAGS_PUFFERDATEN, True)],
                     grafik.TYP_UNBEKANNT["GfxBufferRes"])


@dataclass
class Bau:
    basis: str
    ressourcen: List[Ressource]
    slots: Dict[str, str]                 # Materialslot ("01") → Quellmaterial
    bindung: Dict[str, str]               # Materialslot → Material-Kennung
    aabb: Tuple[Tuple[float, ...], Tuple[float, ...]]
    kugel: Tuple[float, float, float, float]
    lods: List[List[Teil]]                # aufbereitete Teile je LOD (für die Prüfung)
    bericht: dict
    hinweise: List[str]

    @property
    def modell(self) -> str:
        return self.basis[:-len(".idf")]


def lod_abstaende_vorschlag(lods: int, sichtweite_m: float) -> Tuple[float, ...]:
    """LOD-Block wie in den Originalen: je Wechsel der Abstand doppelt, am Ende die Sichtweite
    (z. B. 80, 80, 200, 200, 800). Wechsel bei 10 % und 25 % der Sichtweite, weitere jeweils × 2,5."""
    if lods == 1:
        return (float(sichtweite_m),)
    wechsel = [sichtweite_m * 0.1 * 2.5 ** i for i in range(lods - 1)]
    wechsel = [min(w, sichtweite_m * (0.5 + 0.4 * i / max(1, lods - 2))) for i, w in enumerate(wechsel)]
    aus = []
    for w in wechsel:
        aus += [round(w), round(w)]
    return tuple(float(x) for x in aus + [sichtweite_m])


def modell_bauen(szenen, basis: str, materialien: Optional[Dict[str, str]] = None, *, sichtweite_m: float = 2500.0,
                 lod_abstaende: Optional[Sequence[float]] = None, opak: Optional[Dict[str, bool]] = None,
                 mit_bindung: bool = True, streng: bool = True, slotnamen: Optional[Dict[str, str]] = None,
                 doppelseitig: Optional[Dict[str, bool]] = None, farbstrom: Optional[Dict[str, Sequence[float]]] = None) -> Bau:
    """Erzeugt GfxModelRes, ModelProxyRes, je LOD ein GfxMeshRes, vier Vertexpuffer, einen Indexpuffer und die
    Materialbindung ``<modell>.emb?context=default``.

    ``szenen``: eine Szene oder eine Liste (LOD 0 zuerst). ``basis``: Kennung ``eaid://…/<name>.idf``.
    ``materialien``: Quellmaterial → Kennung eines GfxMaterialRes (vorhanden oder mit ``material_aus_vorlage``
    erzeugt). ``opak``: Quellmaterial → False für Alpha-/Maskenmaterial (Zeichnungs-Flag 0 statt 1).
    ``slotnamen``: Quellmaterial → Name des Materialslots (Standard ``01``, ``02`` … wie bei den meisten
    Originalen), z. B. um die Slots einer vorhandenen Bindung zu treffen. ``doppelseitig``: Quellmaterial → True
    ergänzt die Rückseiten als Geometrie (doppelte Dreieckszahl, ``_doppelseitig``). ``farbstrom``: Quellmaterial →
    RGBA-Vorgabe; diese Materialien bekommen den fünften Strom COLOR0 (glTF ``COLOR_0`` oder die Vorgabe, A3)."""
    if isinstance(szenen, Szene):
        szenen = [szenen]
    if not basis.startswith("eaid://") or not basis.endswith(".idf"):
        raise ModellImportFehler("Base identifier must be eaid://…/<name>.idf")
    name = basis.rsplit("/", 1)[-1][:-len(".idf")]
    hinweise: List[str] = []
    for s in szenen:
        hinweise += s.hinweise
    anim = szenen[0].animation if szenen[0].animation is not None and szenen[0].animation.knoten else None
    if anim is not None:     # A1: Teile bewegter Knoten in deren Raum, der Knotenbaum trägt die Ruhelage
        lods = [[_teil_aufbereiten(t, hinweise) for t in _in_knotenraum(s.teile, anim)] for s in szenen]
    else:
        lods = [[_teil_aufbereiten(t, hinweise) for t in s.teile] for s in szenen]
    if doppelseitig:
        lods = [[_doppelseitig(t) if doppelseitig.get(t.material) else t for t in teile] for teile in lods]
    n0 = sum(len(t.dreiecke) for t in lods[0])
    if n0 > DREIECKE_FEHLER and streng:
        raise ModellImportFehler("%d triangles in LOD 0 (largest model of the game: 254,000)" % n0)
    if n0 > DREIECKE_WARNUNG:
        hinweise.append("%d triangles in LOD 0 (99 %% of the game's models have fewer than 42,000)" % n0)
    for i in range(1, len(lods)):
        if sum(len(t.dreiecke) for t in lods[i]) >= sum(len(t.dreiecke) for t in lods[i - 1]):
            hinweise.append("LOD %d does not have fewer triangles than LOD %d" % (i, i - 1))
    # Materialslots nach erstem Auftreten in LOD 0, dann in den weiteren LODs
    reihenfolge: List[str] = []
    for teile in lods:
        for t in teile:
            if t.material not in reihenfolge:
                reihenfolge.append(t.material)
    if len(reihenfolge) > 99:
        raise ModellImportFehler("More than 99 materials")
    slot_von = {m: (slotnamen or {}).get(m, "%02d" % (i + 1)) for i, m in enumerate(reihenfolge)}
    if len(set(slot_von.values())) != len(slot_von):
        raise ModellImportFehler("Material slots are not unique: %s" % sorted(slot_von.values()))
    materialien = materialien or {}
    fehlend = [m for m in reihenfolge if m not in materialien]
    if fehlend and mit_bindung:
        raise ModellImportFehler("No game materials for: %s" % ", ".join(fehlend))
    opak = opak or {}
    # Modellknoten (Originale: <name>, <name>_x1, <name>_x2 … je LOD; bewegte Teile wie das Riesenrad: bewegter Knoten
    # <name>_<knoten> mit einem Geometrieknoten <name>_<knoten>_x<lod> je LOD darunter)
    knoten = [grafik.ModellKnoten("Scene Root", -1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), EINHEIT, 0, 0)]
    knoten_von: Dict[Tuple[Optional[str], int], int] = {}
    for li in range(len(lods)):
        if anim is None or any(t.knoten is None for t in lods[li]):
            knoten_von[(None, li)] = len(knoten)
            knoten.append(grafik.ModellKnoten(name if li == 0 else "%s_x%d" % (name, li), 0, (0.0, 0.0, 0.0),
                                              (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), EINHEIT, 1, len(knoten)))
    gruppen: List[Optional[str]] = [None]
    anim_index: Dict[str, int] = {}
    if anim is not None:
        for k in anim.knoten:
            kn = "%s_%s" % (name, k.name)
            anim_index[k.name] = len(knoten)
            knoten.append(grafik.ModellKnoten(kn, anim_index[k.eltern] if k.eltern else 0, tuple(k.kurve.ruhe_t),
                                              tuple(k.kurve.ruhe_r), tuple(k.kurve.ruhe_s), EINHEIT, 0, len(knoten)))
            gruppen.append(k.name)
            for li in range(len(lods)):
                if any(t.knoten == k.name for t in lods[li]):
                    knoten_von[(k.name, li)] = len(knoten)
                    knoten.append(grafik.ModellKnoten("%s_x%d" % (kn, li), anim_index[k.name], (0.0, 0.0, 0.0),
                                                      (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0), EINHEIT, 1, len(knoten)))
    # Vertexpool und Indizes
    farbstrom = {m: tuple(float(x) for x in c) for m, c in (farbstrom or {}).items() if m in reihenfolge}
    pos_l, nrm_l, tan_l, uv_l, ind_l, farb_l = [], [], [], [], [], []
    gruppen_pos: Dict[Optional[str], List[np.ndarray]] = {}
    v_off = i_off = 0
    meshes: List[grafik.GfxMesh] = []
    for li, teile in enumerate(lods):
        mesh = grafik.GfxMesh(lod=li)
        mesh.vb_kennungen = [grafik.vb_kennung(basis, k) for k in range(5 if farbstrom else 4)]
        mesh.ib_kennungen = [grafik.ib_kennung(basis, 0)]
        mesh.elemente = [grafik.VertexElement(0, 0, 1, 0, FORMAT_F32X3), grafik.VertexElement(1, 0, 2, 0, FORMAT_HALB4),
                         grafik.VertexElement(2, 0, 3, 0, FORMAT_HALB4), grafik.VertexElement(3, 0, 5, 0, FORMAT_HALB4)]
        mesh.strom_tabelle = [(1, 0), (1, 1), (1, 2), (1, 3)]
        if farbstrom:
            mesh.elemente.append(grafik.VertexElement(4, 0, VERWENDUNG_FARBE, 0, FORMAT_FARBE))
            mesh.strom_tabelle.append((1, 4))
        for g in gruppen:
          if (g, li) not in knoten_von:
            continue
          ki, erste = knoten_von[(g, li)], len(mesh.zeichnungen)
          for m in reihenfolge:
            gruppe = [t for t in teile if t.material == m and t.knoten == g]
            if not gruppe:
                continue
            if slot_von[m] not in mesh.materialnamen:
                mesh.materialnamen.append(slot_von[m])
            mi = mesh.materialnamen.index(slot_von[m])
            # alle Teile eines Materials zu einem Netz, dann in 16-Bit-Stücke
            versatz, tri_alle = 0, []
            for t in gruppe:
                tri_alle.append(t.dreiecke + versatz)
                versatz += len(t.positionen)
            p = np.concatenate([t.positionen for t in gruppe])
            n = np.concatenate([t.normalen for t in gruppe])
            tg = np.concatenate([t.tangenten for t in gruppe])
            uv = np.concatenate([np.concatenate([t.uv0, t.uv1], axis=1) for t in gruppe])
            if farbstrom:
                vorgabe = farbstrom.get(m, (0.0, 0.0, 0.0, 0.0))
                fb = np.concatenate([np.tile(vorgabe, (len(t.positionen), 1)) if t.farben is None or m not in farbstrom
                                     else np.asarray(t.farben, np.float64) for t in gruppe])
            for benutzt, tri in _stuecke(np.concatenate(tri_alle), len(p), pos=p):
                pp = p[benutzt].astype(np.float32)
                pos_l.append(pp)
                gruppen_pos.setdefault(g, []).append(pp)
                nrm_l.append(np.concatenate([n[benutzt], np.zeros((len(benutzt), 1))], axis=1))
                tan_l.append(tg[benutzt])
                uv_l.append(uv[benutzt])
                ind_l.append(tri.astype("<u2").ravel())
                if farbstrom:
                    farb_l.append(np.round(np.clip(fb[benutzt], 0, 1) * 255).astype(np.uint8))
                ti = len(mesh.teilnetze)
                mesh.eintraege.append((ki, ti))
                stroeme = 5 if m in farbstrom else 4
                mesh.teilnetze.append(grafik.Teilnetz(stroeme, grafik.TOPOLOGIE_DREIECKSLISTE, 2, i_off, int(tri.size), v_off,
                                                      huellkugel(pp), 8 * ti, 1, mi, 0,
                                                      [0, 1, 2, 3, 4 if stroeme == 5 else 0, 0, 0, 0]))
                mesh.zeichnungen.append(grafik.Zeichnung(1 if opak.get(m, True) else 0, ti, i_off, int(tri.size)))
                v_off += len(benutzt)
                i_off += int(tri.size)
          mesh.knoten.append(grafik.MeshKnoten(EINHEIT, 0, ki, 0, erste, len(mesh.zeichnungen) - erste, 0, 0))
        fehler = mesh.pruefen()
        if fehler:
            raise ModellImportFehler("Mesh LOD %d is invalid: %s" % (li, fehler))
        meshes.append(mesh)
    pos = np.concatenate(pos_l)
    res: List[Ressource] = [
        _puffer_res(grafik.vb_kennung(basis, 0), grafik.PUFFER_VERTEX, 12, PUFFER_CODE_FLOAT, pos.astype("<f4").tobytes()),
        _puffer_res(grafik.vb_kennung(basis, 1), grafik.PUFFER_VERTEX, 8, PUFFER_CODE_HALB4,
                    np.concatenate(nrm_l).astype("<f2").tobytes()),
        _puffer_res(grafik.vb_kennung(basis, 2), grafik.PUFFER_VERTEX, 8, PUFFER_CODE_HALB4,
                    np.concatenate(tan_l).astype("<f2").tobytes()),
        _puffer_res(grafik.vb_kennung(basis, 3), grafik.PUFFER_VERTEX, 8, PUFFER_CODE_HALB4,
                    np.concatenate(uv_l).astype("<f2").tobytes()),
        _puffer_res(grafik.ib_kennung(basis, 0), grafik.PUFFER_INDEX, 2, PUFFER_CODE_INDEX, np.concatenate(ind_l).tobytes()),
    ]
    if farbstrom:
        res.insert(4, _puffer_res(grafik.vb_kennung(basis, 4), grafik.PUFFER_VERTEX, 4, PUFFER_CODE_FARBE,
                                  np.concatenate(farb_l).tobytes()))
    for li, mesh in enumerate(meshes):
        res.append(_res(grafik.mesh_kennung(basis, li), "GfxMeshRes", mesh.to_bytes()))
    p0 = pos[:sum(len(x) for x in pos_l[:len(meshes[0].teilnetze)])]
    alle = pos                                   # AABB und Kugel über alle LODs: nie zu klein
    if anim is not None:                         # bewegte Teile: Ecken ihrer AABB über die ganze Bewegung
        punkte = [np.concatenate(gruppen_pos[None]).astype(np.float64)] if None in gruppen_pos else []
        ecken = {}
        for g, arrs in gruppen_pos.items():
            if g is not None:
                q = np.concatenate(arrs).astype(np.float64)
                lo, hi = q.min(0), q.max(0)
                ecken[g] = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        for zeit in anim.zeitpunkte():
            welt = anim.weltmatrizen(float(zeit))
            for g, e in ecken.items():
                punkte.append(e @ welt[g][:3, :3].T + welt[g][:3, 3])
        alle = np.concatenate(punkte).astype(np.float32)
    aabb = (tuple(float(x) for x in alle.min(0)), tuple(float(x) for x in alle.max(0)))
    kugel = huellkugel(alle)
    abst = tuple(float(x) for x in (lod_abstaende if lod_abstaende is not None
                                    else lod_abstaende_vorschlag(len(lods), sichtweite_m)))
    if len(abst) != 2 * len(lods) - 1:
        raise ModellImportFehler("LOD distances: %d values instead of %d" % (len(abst), 2 * len(lods) - 1))
    modell = grafik.GfxModell(lod_anzahl=len(lods), knoten_anzahl=len(knoten), namen_anzahl=len(knoten),
                              mesh_kennungen=[grafik.mesh_kennung(basis, li) for li in range(len(lods))], kugel=kugel,
                              aabb_min=aabb[0], aabb_max=aabb[1], lod_abstaende=abst, lod_wert=2, knoten=knoten)
    res.append(_res(grafik.modell_kennung(basis), "GfxModelRes", modell.to_bytes()))
    res.append(_res(grafik.proxy_kennung(basis), "ModelProxyRes", grafik.ModellProxy.aus_modell(modell).to_bytes()))
    clip = None
    if anim is not None:
        clip_res, clip = _anim.clip_ressourcen(basis, anim.name, {"%s_%s" % (name, k.name): k.kurve for k in anim.knoten},
                                               anim.laenge_s)
        res += clip_res
    bindung = {slot_von[m]: materialien[m] for m in reihenfolge if m in materialien}
    if mit_bindung:
        mats = []
        for m in reihenfolge:
            if materialien[m] not in mats:
                mats.append(materialien[m])
        b = grafik.MaterialBindung([slot_von[m] for m in reihenfolge], mats,
                                   [mats.index(materialien[m]) for m in reihenfolge])
        res.append(_res(basis[:-len(".idf")] + ".emb?context=default", "GfxMatBindRes", b.to_bytes()))
    bericht = {"basis": basis, "lods": len(lods), "dreiecke": [int(sum(len(t.dreiecke) for t in x)) for x in lods],
               "vertices": int(len(pos)), "vertices_lod0": int(len(p0)), "teilnetze": [len(m.teilnetze) for m in meshes],
               "materialslots": {slot_von[m]: m for m in reihenfolge}, "aabb_min": list(aabb[0]), "aabb_max": list(aabb[1]),
               "kugel": list(kugel), "lod_abstaende": list(abst), "ressourcen": len(res),
               "bytes": int(sum(len(f[1]) for r in res for f in r.fragmente))}
    if anim is not None:
        bericht["animation"] = {"clip": clip, "name": anim.name, "laenge_s": round(float(anim.laenge_s), 4),
                                "knoten": len(anim.knoten), "schluessel": anim.schluessel(),
                                "bewegte_teile": int(sum(1 for x in lods[0] if x.knoten is not None))}
    return Bau(basis, res, {slot_von[m]: m for m in reihenfolge}, bindung, aabb, kugel, lods, bericht, hinweise)


# --------------------------------------------------------------------------- Texturen und Materialien
def bc4_kodieren(kanal: np.ndarray) -> bytes:
    """Ein Kanal (H, W) uint8 → BC4-Blöcke (Achtwertmodus), wie der Alphateil von BC3."""
    k = np.asarray(kanal, np.uint8)
    h, w = k.shape
    if h % 4 or w % 4:
        k = np.pad(k, ((0, -h % 4), (0, -w % 4)), mode="edge")
        h, w = k.shape
    b = k.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.float32)
    a0, a1 = b.max(1), b.min(1)
    pal = np.stack([a0, a1] + [((7 - j) * a0 + j * a1) / 7 for j in range(1, 7)], axis=1)
    idx = np.argmin((b[:, :, None] - pal[:, None, :]) ** 2, axis=2)
    idx[a0 == a1] = 0
    bits = np.sum(idx.astype(np.uint64) << (3 * np.arange(16, dtype=np.uint64)), axis=1, dtype=np.uint64)
    out = np.empty((len(b), 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    out[:, 2:8] = bits.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
    return out.tobytes()


def bc5_kodieren(rgba: np.ndarray) -> bytes:
    """Rot und Grün als zwei BC4-Blöcke (Normalenkarten ``_n``: x in Rot, y in Grün, z rechnet der Shader)."""
    a = np.asarray(rgba, np.uint8)
    r = np.frombuffer(bc4_kodieren(a[:, :, 0]), np.uint8).reshape(-1, 8)
    g = np.frombuffer(bc4_kodieren(a[:, :, 1]), np.uint8).reshape(-1, 8)
    return np.concatenate([r, g], axis=1).tobytes()


def _zweierpotenz(n: int) -> int:
    p = 4
    while p * 2 <= n * 1.5 and p < 4096:      # zur nächsten Zweierpotenz (ab 1,5-fach aufrunden)
        p *= 2
    return p


def zielgroesse(breite: int, hoehe: int, max_kante: int = 2048) -> Tuple[int, int]:
    """Texturgröße im Spiel: beide Kanten zur nächsten Zweierpotenz, dann gemeinsam halbiert, bis die längere Kante
    ``max_kante`` einhält (das Seitenverhältnis der Zweierpotenzen bleibt, kleinste Kante 4)."""
    zw, zh = _zweierpotenz(breite), _zweierpotenz(hoehe)
    while max(zw, zh) > max_kante and max(zw, zh) > 4:
        zw, zh = max(4, zw // 2), max(4, zh // 2)
    return zw, zh


def bild_vorbereiten(rgba: np.ndarray, max_kante: int = 2048) -> Tuple[np.ndarray, Optional[str]]:
    """Bild auf Zweierpotenzen (4…max_kante) bringen. Rückgabe (RGBA, Hinweis oder None)."""
    from PIL import Image
    a = np.asarray(rgba, np.uint8)
    if a.ndim != 3 or a.shape[2] != 4:
        raise ModellImportFehler("Image must be RGBA")
    h, w = a.shape[:2]
    zw, zh = zielgroesse(w, h, max_kante)
    if (zw, zh) == (w, h):
        return a, None
    neu = np.asarray(Image.fromarray(a, "RGBA").resize((zw, zh), Image.Resampling.LANCZOS), np.uint8)
    return neu, "Image %d×%d resized to %d×%d (powers of two, at most %d)" % (w, h, zw, zh, max_kante)


def textur_ressourcen(kennung_tif: str, rgba: np.ndarray, format: int = FORMAT_DIFFUS) -> List[Ressource]:
    """Neue Textur vollständig im ERP (ohne externe ``.mipmaps``): GfxSurfaceRes ``<kennung>.image`` (Kopf 40 B +
    Mip-Kette bis 4 px) und GfxSRVResource ``<kennung>``. 3.756 Originaltexturen sind ebenso aufgebaut."""
    from . import grafik_textur_schreiben as gts
    if not kennung_tif.endswith(".tif"):
        raise ModellImportFehler("Texture identifier must end in .tif")
    a = np.asarray(rgba, np.uint8)
    h, w = a.shape[:2]
    if w & (w - 1) or h & (h - 1) or min(w, h) < 4:
        raise ModellImportFehler("Texture size %d×%d is not a power of two ≥ 4" % (w, h))
    stufen = gts.mip_kette(a)
    kodieren = bc5_kodieren if format == FORMAT_NORMAL else (lambda s: gts.bild_kodieren(s, format))
    daten = b"".join(kodieren(s) for s in stufen)
    kopf = grafik.TEXTUR_KOPF.pack(2, TEXTUR_FLAGS, format, w, h, 1, len(stufen), 1, 0, 0)
    bild = Ressource(kennung_tif + ".image", "GfxSurfaceRes",
                     [(b"temp", kopf, FRAGMENT_FLAGS, True), (b"temp", daten, FRAGMENT_FLAGS, True)],
                     grafik.TYP_UNBEKANNT["GfxSurfaceRes"])
    sicht = grafik.TexturSicht(SRV_ART, format, 0, len(stufen), 0, 0, kennung_tif + ".image")
    return [bild, _res(kennung_tif, "GfxSRVResource", sicht.to_bytes())]


def normalenkarte_fuer_spiel(rgba: np.ndarray, quelle: str = "gl") -> np.ndarray:
    """Normalenkarte in die Spielkonvention (DirectX-Grün) bringen. ``quelle`` = ``gl`` (Blender, glTF, Substance
    „OpenGL“: +G = Bild oben) kehrt den Grünkanal um, ``dx`` lässt das Bild unverändert."""
    a = np.array(rgba, np.uint8, copy=True)
    if quelle not in ("gl", "dx"):
        raise ModellImportFehler("Unknown normal map convention %r (gl or dx)" % quelle)
    if quelle != NORMALEN_KONVENTION_SPIEL:
        a[..., 1] = 255 - a[..., 1]
    return a


def neutrale_karten(praefix: str, oberflaeche: Sequence[int] = OBERFLAECHE_NEUTRAL
                    ) -> Tuple[List[Ressource], Dict[str, str]]:
    """Eigene einfarbige Oberflächen- (``_s``, BC1 linear) und flache Normalenkarte (``_n``, BC5) für ein geklontes
    Material, damit es keine Karten des Vorlagenmaterials erbt (M0: Blockstreifen der Startampel-Oberflächenkarte).
    ``praefix`` ohne Endung, z. B. ``…/textures/usr_neutral``. Rückgabe (Ressourcen, {Slot: Kennung})."""
    k = NEUTRAL_KANTE
    s = np.empty((k, k, 4), np.uint8)
    s[...] = np.asarray(oberflaeche, np.uint8)
    n = np.empty((k, k, 4), np.uint8)
    n[...] = np.asarray(NORMAL_FLACH, np.uint8)
    ks, kn = praefix + "_s.tif", praefix + "_n.tif"
    res = textur_ressourcen(ks, s, FORMAT_OBERFLAECHE) + textur_ressourcen(kn, n, FORMAT_NORMAL)
    return res, {"me_surfaceMap": ks, "me_normalMap": kn}


def _kanal(wert: float) -> int:
    return int(round(255.0 * min(1.0, max(0.0, float(wert)))))


def oberflaeche_konstant(q: QuellMaterial) -> Tuple[int, int, int, int]:
    """Farbe der Oberflächenkarte ``_s`` für ein Material ohne ORM-Bild: R Verdeckung (255 = keine),
    G Glanz = 1 − Rauheit, B Metall. Rauheit 1 und Metall 0 ergeben die neutrale Karte (255, 0, 0)."""
    return (255, _kanal(1.0 - q.rauheit), _kanal(q.metall), 255)


def oberflaechenkarte(q: QuellMaterial) -> Optional[np.ndarray]:
    """Oberflächenkarte ``_s`` des Spiels (RGBA uint8) aus den glTF-Bildern eines Materials, None ohne Bild.

    glTF: ``metallicRoughnessTexture`` G = Rauheit, B = Metall (je mal Faktor), ``occlusionTexture`` R = Verdeckung
    (1 = keine; ``strength`` mischt gegen 1). Spiel (Namen der einfarbigen Karten des Spiels: ``blank_surface_01``
    (255, 0, 0), ``…_satin`` (255, 174, 33), ``…_glossy_01`` (255, 255, 0), ``…_glass`` (255, 255, 255)):
    R Verdeckung, G Glanz = 1 − Rauheit, B Metall. Die Größe gibt das ORM-Bild vor, sonst das Verdeckungsbild."""
    if q.bild_orm is None and q.bild_verdeckung is None:
        return None
    from PIL import Image
    orm = None if q.bild_orm is None else np.asarray(q.bild_orm, np.uint8)
    ao = None if q.bild_verdeckung is None else np.asarray(q.bild_verdeckung, np.uint8)
    h, w = (orm if orm is not None else ao).shape[:2]
    aus = np.empty((h, w, 4), np.uint8)
    aus[..., 3] = 255
    if ao is None:
        aus[..., 0] = 255
    else:
        if ao.shape[:2] != (h, w):
            ao = np.asarray(Image.fromarray(ao, "RGBA").resize((w, h), Image.Resampling.LANCZOS), np.uint8)
        k = min(1.0, max(0.0, float(q.verdeckung_staerke)))
        aus[..., 0] = np.rint(255.0 - k * (255.0 - ao[..., 0].astype(np.float64)))
    if orm is None:
        aus[..., 1], aus[..., 2] = _kanal(1.0 - q.rauheit), _kanal(q.metall)
    else:
        rau = np.clip(orm[..., 1].astype(np.float64) * float(q.rauheit), 0.0, 255.0)
        aus[..., 1] = np.rint(255.0 - rau)
        aus[..., 2] = np.rint(np.clip(orm[..., 2].astype(np.float64) * float(q.metall), 0.0, 255.0))
    return aus


def farbkarte_mit_alpha(q: QuellMaterial) -> np.ndarray:
    """Farbkarte ``_da`` für Transparenz und Maske: Basisfarb-Bild (sonst weiß), Alpha mal ``baseColorFactor[3]``.
    Die Tönung des Spielmaterials hat keinen Alphawert, die Deckkraft steht deshalb immer im Bild."""
    if q.bild_basisfarbe is None:
        a = np.full((NEUTRAL_KANTE, NEUTRAL_KANTE, 4), 255, np.uint8)
    else:
        a = np.array(q.bild_basisfarbe, np.uint8, copy=True)
    faktor = min(1.0, max(0.0, float(q.basisfarbe[3]) if len(q.basisfarbe) > 3 else 1.0))
    if faktor != 1.0:
        a[..., 3] = np.rint(a[..., 3].astype(np.float64) * faktor)
    return a


_VORLAGE_NULL = ("me_diffuseMapHeapIndex", "me_normalMapHeapIndex", "me_surfaceMapHeapIndex", "me_receiveSSRTShadows",
                 "me_roughnessLinkedOcclusion", "me_suppressSSAO", "me_useCubemaps", "me_useSSRT", "me_glowLevel",
                 "me_selfIlluminationLevel", "me_translucency")


def vorlage_alpha_finden(quelle, modus: str) -> Optional[str]:
    """Kennung eines Vorlagenmaterials für glTF ``BLEND`` (Shader ``object_alpha``) oder ``MASK``
    (``object_mask``) in Archiv oder Index; None, wenn der Träger keines hat.

    Genommen wird ein unauffälliges Material: genau die drei Karten in drei verschiedenen Texturen, weiße Tönung,
    kein Leuchten, alle Schalter 0 (bei ``object_alpha`` ``me_energyConservingTransparency`` 1, wie 49 von 51 im
    Träger; Ganzzahlkonstanten schreibt der Werkzeugkasten nicht um). Unter den Kandidaten das mit den wenigsten
    Parametern, dann nach Kennung: die Wahl hängt nicht von der Reihenfolge im Archiv ab."""
    shader = SHADER_ALPHA[modus]
    idx = grafik._als_index(quelle)
    kandidaten = []
    for r in idx.alle("GfxMaterialRes"):
        try:
            m = grafik.Material.aus_bytes(r.data())
        except Exception:  # noqa: BLE001
            continue
        tex = m.texturen()
        if not m.shader.endswith("/" + shader) or sorted(tex) != sorted(VORLAGE_SLOTS) or len(set(tex.values())) != 3:
            continue
        k = m.konstanten()
        if "me_alphaThreshold" not in k or "me_shadowAlphaThreshold" not in k:
            continue
        if tuple(round(x, 3) for x in k.get("me_diffuseTint", (1, 1, 1))) != (1, 1, 1):
            continue
        if any(k.get(n, (0,))[0] != 0 for n in _VORLAGE_NULL):
            continue
        if modus == "BLEND" and k.get("me_energyConservingTransparency", (1,))[0] != 1:
            continue
        kandidaten.append((len(m.parameter), r.identifier))
    return min(kandidaten)[1] if kandidaten else None


def vorlagen_alpha(quelle) -> Tuple[Dict[str, str], Dict[str, bytes]]:
    """Vorlagen für Transparenz und Maske aus dem Träger: ({``alpha``/``mask``: Kennung}, {…: Bytes}).
    Fehlt ein Shader im Träger, fehlt der Schlüssel; solche Materialien zeichnet die Umsetzung deckend (Hinweis)."""
    idx = grafik._als_index(quelle)
    kennungen, roh = {}, {}
    for modus, art in VORLAGE_ART.items():
        k = vorlage_alpha_finden(idx, modus)
        if k is not None:
            kennungen[art], roh[art] = k, idx.get(k).data()
    return kennungen, roh


def vorlagen_effekte(quelle) -> Tuple[Dict[str, str], Dict[str, bytes]]:
    """A3: Vorlagen der Bewegungsmaterialien aus dem Träger: ({``wasser``/``lauftext``/``fahne``: Kennung}, {…: Bytes}).
    Je Shader das Material mit den wenigsten Parametern und verschiedenen Texturen in allen Slots (dann nach Kennung,
    unabhängig von der Reihenfolge im Archiv). Fehlt ein Shader, fehlt der Schlüssel (Material dann gewöhnlich, Hinweis)."""
    idx = grafik._als_index(quelle)
    kandidaten: Dict[str, List[Tuple[int, str]]] = {}
    for r in idx.alle("GfxMaterialRes"):
        try:
            m = grafik.Material.aus_bytes(r.data())
        except Exception:  # noqa: BLE001
            continue
        for art, shader in EFFEKT_SHADER.items():
            tex = m.texturen()
            if m.shader.endswith("/" + shader) and tex and len(set(tex.values())) == len(tex):
                kandidaten.setdefault(art, []).append((len(m.parameter), r.identifier))
    kennungen = {art: min(k)[1] for art, k in kandidaten.items()}
    return kennungen, {art: idx.get(k).data() for art, k in kennungen.items()}


def effekt_pruefen(e) -> dict:
    """Effektangabe (``modelle.json`` → ``effekte`` oder glTF-Extras) prüfen und mit Standardwerten auffüllen."""
    if isinstance(e, str):
        e = {"art": e}
    if not isinstance(e, dict) or e.get("art") not in EFFEKT_SHADER:
        raise ModellImportFehler("Material effect must be wasser, lauftext or fahne (%r)" % (e,))
    aus = dict(EFFEKT_STANDARD.get(e["art"], {}))
    for k, v in e.items():
        if k == "art":
            continue
        if k not in ("u", "v", "helligkeit", "tempo") or isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(float(v)):
            raise ModellImportFehler("Material effect: field %s must be u, v, helligkeit or tempo with a number" % k)
        aus[k] = float(v)
    aus["art"] = e["art"]
    return aus


def material_aus_vorlage(vorlage: bytes, kennung: str, texturen: Optional[Dict[str, str]] = None,
                         konstanten: Optional[Dict[str, Sequence[float]]] = None) -> Ressource:
    """Neues GfxMaterialRes als Kopie eines Spielmaterials (Shader, Parameter und Konstantenpuffer bleiben):
    ``texturen`` = {Slot (z. B. ``me_diffuseMap``): neue ``.tif``-Kennung}, ``konstanten`` wie
    ``grafik.material_aendern`` (z. B. ``me_diffuseTint``). Nicht genannte Slots behalten die Textur der Vorlage;
    diese muss dann im Paket vorhanden sein."""
    if not kennung.endswith(".material"):
        raise ModellImportFehler("Material identifier must end in .material")
    m = grafik.Material.aus_bytes(vorlage)
    alt = m.texturen()
    tausch: Dict[str, str] = {}
    for slot, neu in (texturen or {}).items():
        if slot not in alt:
            raise ModellImportFehler("Template material has no texture slot %s (available: %s)" % (slot, ", ".join(sorted(alt))))
        andere = [s for s, k in alt.items() if k == alt[slot] and s != slot and (texturen or {}).get(s) != neu]
        if andere:
            raise ModellImportFehler("Template uses the same texture in %s and %s, choose another template" % (slot, andere[0]))
        tausch[alt[slot]] = neu
    return _res(kennung, "GfxMaterialRes", grafik.material_aendern(vorlage, konstanten=konstanten, texturen=tausch))


def vorlage_material_finden(quelle, shader: str = "object_opaque.nefx2", slots=("me_diffuseMap", "me_normalMap", "me_surfaceMap")
                            ) -> Optional[str]:
    """Kennung eines unauffälligen Materials mit dem Shader (weiße Tönung, kein Leuchten) in Archiv oder Index."""
    idx = grafik._als_index(quelle)
    for r in idx.alle("GfxMaterialRes"):
        try:
            m = grafik.Material.aus_bytes(r.data())
        except Exception:  # noqa: BLE001
            continue
        if not m.shader.endswith("/" + shader) or sorted(m.texturen()) != sorted(slots):
            continue
        k = m.konstanten()
        if len(set(m.texturen().values())) != len(slots):
            continue
        if tuple(round(x, 3) for x in k.get("me_diffuseTint", (1, 1, 1))) == (1, 1, 1) and \
                k.get("me_glowLevel", (0.0,))[0] == 0 and k.get("me_selfIlluminationLevel", (0.0,))[0] == 0 and \
                k.get("me_translucency", (0.0,))[0] == 0:
            return r.identifier
    return None


# --------------------------------------------------------------------------- Umsetzung in einem Schritt (M1)
#: Version der Umsetzung; geht in den Schlüssel des Zwischenspeichers ein (strecke/eigene_modelle.py). Erhöhen, wenn
#: sich die erzeugten Ressourcen bei gleicher Eingabe ändern.
UMSETZUNG_VERSION = 4          # 2 = M5a (Transparenz, Maske, doppelseitig, Glanz/Metall), 3 = A1 (Animation), 4 = A3 (Effekte)
#: Texturslots, die das Vorlagenmaterial haben muss (Shader ``object_opaque``)
VORLAGE_SLOTS = ("me_diffuseMap", "me_normalMap", "me_surfaceMap")
NAME_MUSTER = re.compile(r"[a-z][a-z0-9_]{0,47}")


@dataclass
class Umsetzung:
    """Ergebnis von ``modell_umsetzen``: alle Ressourcen eines eigenen Modells (Modell, Materialien, Texturen)."""

    name: str
    basis: str                            # eaid://…/<name>.idf
    ressourcen: List[Ressource]
    bericht: dict                         # JSON-fähig
    hinweise: List[str]
    bau: Optional[Bau] = None             # None, wenn aus dem Zwischenspeicher geladen (keine Eingabegeometrie)

    @property
    def modell(self) -> str:
        return self.basis[:-len(".idf")]


def modell_umsetzen(szenen, name: str, wurzel: str, vorlage_material: bytes, *, sichtweite_m: float = 2500.0,
                    lod_abstaende: Optional[Sequence[float]] = None, textur_max: int = 1024,
                    textur_format: int = FORMAT_DIFFUS, normalen: str = "gl", streng: bool = True,
                    vorlagen: Optional[Dict[str, bytes]] = None, effekte: Optional[Dict[str, dict]] = None) -> Umsetzung:
    """Setzt eine Szene (oder eine Liste, LOD 0 zuerst) vollständig in Spielressourcen um.

    ``name``: Modellname (Kleinbuchstaben, Ziffern, ``_``; üblich ``usr_<name>``). ``wurzel``: Objektordner der
    Strecke, ``eaid://environment_package/tracks/<code>/objects``. Es entstehen ``<wurzel>/idf/<name>.idf…``,
    ``<wurzel>/materials/<name>_<NN>.material`` und ``<wurzel>/textures/<name>_…``.

    ``vorlage_material``: Bytes eines ``object_opaque``-Materials (Slots ``VORLAGE_SLOTS``); davon bleiben nur Shader
    und Konstanten. Jedes Material bekommt eigene neutrale Karten (matt, nicht metallisch, flache Normalen; M0b),
    dazu aus dem Quellmaterial: Basisfarbe → ``me_diffuseTint``, Basisfarb-Bild → ``me_diffuseMap`` (BC7 sRGB bzw.
    ``textur_format``, höchstens ``textur_max`` Pixel Kante), Normalenkarte → ``me_normalMap`` (BC5; ``normalen`` =
    Konvention der Quelle: ``gl`` für Blender/glTF, ``dx``). Gleiche Bilder werden nur einmal gespeichert.

    M5a: Metall, Rauheit und Verdeckung (Faktoren oder Bilder) → ``me_surfaceMap`` (``oberflaechenkarte``; matt und
    nicht metallisch bleibt die neutrale Karte). ``alphaMode`` ``BLEND``/``MASK`` → Material als Kopie von
    ``vorlagen["alpha"]`` (Shader ``object_alpha``) bzw. ``vorlagen["mask"]`` (``object_mask``), Farbkarte mit Alpha
    in BC3 sRGB, ``alphaCutoff`` → ``me_alphaThreshold``, Zeichnungs-Flag 0. Fehlt die Vorlage, wird das Material
    deckend gezeichnet (Hinweis). Doppelseitige Materialien bekommen ihre Rückseiten als Geometrie.

    A3: ``effekte`` = {Quellmaterial: {``art``: ``wasser``/``lauftext``/``fahne``, ``u``, ``v``, ``helligkeit``,
    ``tempo``}} (sonst die glTF-Extras ``f1_effect`` …) → Kopie von ``vorlagen[art]`` (``vorlagen_effekte``) mit den
    eigenen Karten und Geschwindigkeiten; Wasser und Fahne bekommen den Farbstrom COLOR0."""
    if isinstance(szenen, Szene):
        szenen = [szenen]
    if not NAME_MUSTER.fullmatch(name or ""):
        raise ModellImportFehler("Model name %r is invalid (lower-case letters, digits and _, starting with a letter)" % name)
    if not wurzel.startswith("eaid://") or wurzel.endswith("/"):
        raise ModellImportFehler("Object folder must be eaid://…/objects")
    vorhanden = grafik.Material.aus_bytes(vorlage_material).texturen()
    fehlend = [s for s in VORLAGE_SLOTS if s not in vorhanden]
    if fehlend:
        raise ModellImportFehler("Template material has no texture slot %s (available: %s)" % (
            fehlend[0], ", ".join(sorted(vorhanden))))
    hinweise: List[str] = []
    tex = "%s/textures/%s" % (wurzel, name)
    extra, neutral = neutrale_karten(tex + "_neutral")
    weiss = np.full((NEUTRAL_KANTE, NEUTRAL_KANTE, 4), 255, np.uint8)
    extra += textur_ressourcen(tex + "_neutral_d.tif", weiss, FORMAT_DIFFUS)
    neutral["me_diffuseMap"] = tex + "_neutral_d.tif"
    reihenfolge: List[str] = []
    for s in szenen:
        for t in s.teile:
            if t.material not in reihenfolge:
                reihenfolge.append(t.material)
    bilder: Dict[Tuple[str, bytes], str] = {}      # (Art, Hash) → Kennung: gleiche Bilder nur einmal
    texturen, materialien, mat_bericht = [], {}, {}
    opak: Dict[str, bool] = {}
    doppelt: Dict[str, bool] = {}
    farbstrom: Dict[str, Tuple[float, ...]] = {}

    def textur(nr: int, art: str, bild: np.ndarray, fmt: int, mat: str) -> str:
        h0, w0 = np.asarray(bild).shape[:2]
        bild, geaendert = bild_vorbereiten(bild, textur_max)
        if geaendert:
            hinweise.append("Material %s: image %d×%d resized to %d×%d (powers of two, at most %d)" % (
                mat, w0, h0, bild.shape[1], bild.shape[0], textur_max))
        schluessel = (art, hashlib.sha256(repr(bild.shape).encode() + bild.tobytes()).digest())
        if schluessel not in bilder:
            kennung = "%s_%02d_%s.tif" % (tex, nr, art)
            res = textur_ressourcen(kennung, bild, fmt)
            extra.extend(res)
            bilder[schluessel] = kennung
            texturen.append({"kennung": kennung, "breite": int(bild.shape[1]), "hoehe": int(bild.shape[0]), "format": int(fmt),
                             "bytes": int(len(res[0].fragmente[1][1]))})
        return bilder[schluessel]

    for i, m in enumerate(reihenfolge):
        q = next((s.materialien[m] for s in szenen if m in s.materialien), QuellMaterial(m))
        effekt = (effekte or {}).get(m) or q.effekt
        if effekt:
            effekt = effekt_pruefen(effekt)
            roh_effekt = (vorlagen or {}).get(effekt["art"])
            if roh_effekt is None:
                hinweise.append("Material %s: the carrier has no %s material, the effect %s is left out" % (
                    m, EFFEKT_SHADER[effekt["art"]].split(".")[0], effekt["art"]))
            else:
                kennung = "%s/materials/%s_%02d.material" % (wurzel, name, i + 1)
                slots_e, konst_e = _effekt_material(effekt, q, roh_effekt, lambda art, bild, fmt, nr=i + 1, m=m:
                                                    textur(nr, art, bild, fmt, m), neutral, normalen)
                extra.append(material_aus_vorlage(roh_effekt, kennung, slots_e, konst_e))
                materialien[m] = kennung
                opak[m] = True
                doppelt[m] = bool(q.doppelseitig)
                if effekt["art"] in EFFEKT_FARBE:
                    farbstrom[m] = EFFEKT_FARBE[effekt["art"]]
                mat_bericht[m] = {"kennung": kennung, "art": effekt["art"], "effekt": effekt,
                                  "doppelseitig": bool(q.doppelseitig),
                                  "texturen": {s: k.rsplit("/", 1)[-1] for s, k in slots_e.items()}}
                continue
        slots = dict(neutral)
        roh_alpha = (vorlagen or {}).get(VORLAGE_ART.get(q.alpha, ""))
        if q.bild_basisfarbe is not None and roh_alpha is None:      # mit Alpha-Vorlage entsteht unten die Karte _da
            slots["me_diffuseMap"] = textur(i + 1, "d", q.bild_basisfarbe, textur_format, m)
        if q.bild_normal is not None:
            slots["me_normalMap"] = textur(i + 1, "n", normalenkarte_fuer_spiel(q.bild_normal, normalen), FORMAT_NORMAL, m)
        karte = oberflaechenkarte(q)
        oberflaeche = oberflaeche_konstant(q)
        if karte is not None:
            slots["me_surfaceMap"] = textur(i + 1, "s", karte, FORMAT_OBERFLAECHE, m)
        elif oberflaeche != tuple(OBERFLAECHE_NEUTRAL):
            slots["me_surfaceMap"] = textur(i + 1, "s", np.full((NEUTRAL_KANTE, NEUTRAL_KANTE, 4), oberflaeche, np.uint8),
                                            FORMAT_OBERFLAECHE, m)
        toenung = tuple(float(min(1.0, max(0.0, x))) for x in q.basisfarbe[:3])
        konstanten: Dict[str, Sequence[float]] = {"me_diffuseTint": toenung}
        roh, art = vorlage_material, "opaque"
        if q.alpha in VORLAGE_ART:
            if roh_alpha is None:
                hinweise.append("Material %s: the carrier has no %s material, transparency (%s) is drawn opaque" % (
                    m, SHADER_ALPHA[q.alpha].split(".")[0], q.alpha))
            else:
                roh, art = roh_alpha, VORLAGE_ART[q.alpha]
                slots["me_diffuseMap"] = textur(i + 1, "da", farbkarte_mit_alpha(q), FORMAT_DIFFUS_ALPHA, m)
                schwelle = float(min(1.0, max(0.0, q.alpha_schwelle))) if q.alpha == "MASK" else 0.0
                konstanten["me_alphaThreshold"] = (schwelle,)
                konstanten["me_shadowAlphaThreshold"] = (schwelle if q.alpha == "MASK" else 0.5,)
        elif q.alpha != "OPAQUE":
            hinweise.append("Material %s: unknown alpha mode %s, the material is drawn opaque" % (m, q.alpha))
        opak[m] = art == "opaque"
        doppelt[m] = bool(q.doppelseitig)
        kennung = "%s/materials/%s_%02d.material" % (wurzel, name, i + 1)
        extra.append(material_aus_vorlage(roh, kennung, slots, konstanten))
        materialien[m] = kennung
        mat_bericht[m] = {"kennung": kennung, "toenung": [round(x, 4) for x in toenung], "art": art,
                          "doppelseitig": bool(q.doppelseitig),
                          "oberflaeche": "bild" if karte is not None else [int(x) for x in oberflaeche[:3]],
                          "texturen": {s: k.rsplit("/", 1)[-1] for s, k in slots.items()}}
        if art != "opaque":
            mat_bericht[m]["alpha_schwelle"] = round(konstanten["me_alphaThreshold"][0], 4)
    basis = "%s/idf/%s.idf" % (wurzel, name)
    bau = modell_bauen(szenen, basis, materialien, sichtweite_m=sichtweite_m, lod_abstaende=lod_abstaende, streng=streng,
                       opak=opak, doppelseitig=doppelt, farbstrom=farbstrom or None)
    ressourcen = bau.ressourcen + extra
    bericht = dict(bau.bericht)
    bericht.update(name=name, modell=basis[:-len(".idf")], materialien=mat_bericht, texturen=texturen,
                   ressourcen=len(ressourcen), bytes=int(sum(len(f[1]) for r in ressourcen for f in r.fragmente)),
                   textur_bytes=int(sum(t["bytes"] for t in texturen)), normalen=normalen, textur_max=int(textur_max),
                   version=UMSETZUNG_VERSION)
    return Umsetzung(name, basis, ressourcen, bericht, hinweise + bau.hinweise, bau)


def _effekt_material(effekt: dict, q: QuellMaterial, roh: bytes, textur, neutral: Dict[str, str], normalen: str
                     ) -> Tuple[Dict[str, str], Dict[str, Sequence[float]]]:
    """Slots und Konstanten eines Bewegungsmaterials (A3). ``textur(art, bild, format)`` legt eine Textur an."""
    vorhanden = grafik.Material.aus_bytes(roh).texturen()
    konst = grafik.Material.aus_bytes(roh).konstanten()
    slots: Dict[str, str] = {}
    art = effekt["art"]
    if art == "lauftext":          # leuchtende Karte = Farbbild (sonst weiß), Oberfläche neutral
        bild = q.bild_basisfarbe if q.bild_basisfarbe is not None else np.full((NEUTRAL_KANTE, NEUTRAL_KANTE, 4), 255, np.uint8)
        if q.bild_basisfarbe is None and tuple(q.basisfarbe[:3]) != (1.0, 1.0, 1.0):
            bild = np.full((NEUTRAL_KANTE, NEUTRAL_KANTE, 4), [int(round(255 * min(1, max(0, c)))) for c in q.basisfarbe[:3]] + [255],
                           np.uint8)
        if "me_effectMap" in vorhanden:
            slots["me_effectMap"] = textur("d", bild, FORMAT_DIFFUS)
        if "me_surfaceMap" in vorhanden:
            slots["me_surfaceMap"] = neutral["me_surfaceMap"]
        return slots, {"me_USpeed": (effekt["u"],), "me_VSpeed": (effekt["v"],),
                       "me_selfIlluminationLevel": (effekt["helligkeit"],)}
    if art == "fahne":             # Farbbild mit Alpha (Maske 0,5 wie alle Spielfahnen), Normalen, Oberfläche neutral
        slots["me_diffuseMap"] = textur("da", farbkarte_mit_alpha(q), FORMAT_DIFFUS_ALPHA)
        slots["me_normalMap"] = (textur("n", normalenkarte_fuer_spiel(q.bild_normal, normalen), FORMAT_NORMAL)
                                 if q.bild_normal is not None else neutral["me_normalMap"])
        slots["me_surfaceMap"] = neutral["me_surfaceMap"]
        k: Dict[str, Sequence[float]] = {"me_USpeed": (effekt["u"],)}
        if "me_diffuseTint" in konst:
            k["me_diffuseTint"] = tuple(float(min(1.0, max(0.0, x))) for x in q.basisfarbe[:3]) if q.bild_basisfarbe is None \
                else (1.0, 1.0, 1.0)
        if "v" in effekt and "me_VSpeed" in konst:
            k["me_VSpeed"] = (effekt["v"],)
        return {s: v for s, v in slots.items() if s in vorhanden}, k
    # wasser: Karten der Vorlage, eigene Farb-/Normalenkarte, wenn vorhanden; Geschwindigkeiten × tempo
    if q.bild_basisfarbe is not None and "me_diffuseMap" in vorhanden:
        slots["me_diffuseMap"] = textur("d", q.bild_basisfarbe, FORMAT_DIFFUS)
    if q.bild_normal is not None and "me_normalMap" in vorhanden:
        slots["me_normalMap"] = textur("n", normalenkarte_fuer_spiel(q.bild_normal, normalen), FORMAT_NORMAL)
    k = {n: (float(v[0]) * effekt.get("tempo", 1.0),) for n, v in konst.items()
         if n.startswith("me_normalLayer") and n.endswith("Speed")}
    return slots, k


# --------------------------------------------------------------------------- Archiv
def in_archiv(archiv: erp.ErpFile, ressourcen: Sequence[Ressource], ersetzen: bool = False) -> List[str]:
    """Ressourcen in ein ERP-Archiv im Speicher eintragen (Typfeld, Fragment-Flags und Kompression wie die
    Originale, MD5 neu). Vorhandene Kennungen sind ein Fehler, außer ``ersetzen``."""
    doppelt = [r.kennung for r in ressourcen if archiv.find(r.kennung) is not None]
    if doppelt and not ersetzen:
        raise ModellImportFehler("Identifier already exists: %s" % doppelt[0])
    neu = []
    for r in ressourcen:
        if archiv.find(r.kennung) is not None:
            archiv.remove_resource(r.kennung)
        n = archiv.add_resource(r.kennung, r.typ, [(name, daten) for name, daten, _, _ in r.fragmente], unknown=r.unknown)
        for fr, (_, daten, flags, komprimieren) in zip(n.fragments, r.fragmente):
            if not komprimieren:
                fr.set_data(daten, compress=False)
            fr.flags = flags
        n.update_hash()
        neu.append(r.kennung)
    return neu


def _dreieckssuppe(pos: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Dreiecke als sortierte (M, 9)-Tabelle der Eckpositionen (float32), unabhängig von der Vertexreihenfolge;
    die Ecken jedes Dreiecks werden zyklisch auf die kleinste Ecke gedreht (Umlaufsinn bleibt)."""
    e = np.asarray(pos, np.float32)[tri]                                  # (M, 3, 3)

    def kleiner(a, b):                                                    # lexikographisch a < b
        return (a[:, 0] < b[:, 0]) | ((a[:, 0] == b[:, 0]) & ((a[:, 1] < b[:, 1]) | ((a[:, 1] == b[:, 1]) & (a[:, 2] < b[:, 2]))))

    start = np.zeros(len(e), np.int64)
    start[kleiner(e[:, 1], e[:, 0])] = 1
    bisher = e[np.arange(len(e)), start]
    start[kleiner(e[:, 2], bisher)] = 2
    idx = (start[:, None] + np.arange(3)[None, :]) % 3
    e = e[np.arange(len(e))[:, None], idx].reshape(-1, 9)
    return e[np.lexsort(e.T[::-1])]


def pruefen(archiv: erp.ErpFile, bau: Bau, quelle=None) -> List[Tuple[str, bool, str]]:
    """Offline-Prüfung eines eingetragenen Modells. ``quelle``: Index mit Spielressourcen, in dem Verweise
    liegen dürfen, die nicht im Archiv sind (vorhandene Materialien und Texturen)."""
    aus: List[Tuple[str, bool, str]] = []

    def ok(name, bedingung, text=""):
        aus.append((name, bool(bedingung), text))

    idx = grafik._als_index(archiv)
    aussen = grafik._als_index(quelle) if quelle is not None else None
    basis = bau.basis
    eigene = [r.kennung for r in bau.ressourcen]
    fehlen = [k for k in eigene if archiv.find(k) is None]
    ok("Ressourcen vorhanden", not fehlen, "%d von %d%s" % (len(eigene) - len(fehlen), len(eigene), (", fehlt " + fehlen[0]) if fehlen else ""))
    if fehlen:
        return aus
    ok("MD5 stimmt", all(archiv.find(k).verify() for k in eigene))
    soll_typ = {r.kennung: (r.typ, r.unknown, [f[2] for f in r.fragmente]) for r in bau.ressourcen}
    falsch = [k for k in eigene if (archiv.find(k).resource_type, archiv.find(k).unknown,
                                    [f.flags for f in archiv.find(k).fragments]) != soll_typ[k]]
    ok("Typ, Typfeld und Fragment-Flags wie die Originale", not falsch, falsch[0] if falsch else "")
    modell = grafik.GfxModell.aus_bytes(idx[grafik.modell_kennung(basis)].data())
    ok("GfxModelRes: Rundlauf bitgleich", modell.to_bytes() == idx[grafik.modell_kennung(basis)].data())
    proxy = grafik.ModellProxy.aus_bytes(idx[grafik.proxy_kennung(basis)].data())
    ok("ModelProxyRes: Rundlauf, Knoten wie im Modell",
       proxy.to_bytes() == idx[grafik.proxy_kennung(basis)].data() and proxy.knoten == modell.knoten)
    animiert = bau.bericht.get("animation")
    ok("Modell: LODs, Knoten, Abstände", modell.lod_anzahl == len(bau.lods) == len(modell.mesh_kennungen)
       and (animiert or len(modell.knoten) == 1 + len(bau.lods)) and len(modell.lod_abstaende) == 2 * len(bau.lods) - 1
       and list(modell.lod_abstaende) == sorted(modell.lod_abstaende), str(modell.lod_abstaende))
    puffer_ok, mesh_ok, geo_ok, kugel_ok, index_ok, einzel = True, True, True, True, True, []
    alle_pos = []
    for li, mk in enumerate(modell.mesh_kennungen):
        roh = idx[mk].data()
        mesh = grafik.GfxMesh.aus_bytes(roh)
        mesh_ok &= mesh.to_bytes() == roh and not mesh.pruefen() and mesh.lod == li
        if animiert:     # Einträge und Mesh-Knoten zeigen auf Geometrieknoten, Zeichnungen lückenlos aufgeteilt
            mesh_ok &= all(e[1] == i and modell.knoten[e[0]].hat_mesh == 1 for i, e in enumerate(mesh.eintraege))
            mesh_ok &= sum(k.zeichnung_anzahl for k in mesh.knoten) == len(mesh.zeichnungen) and \
                all(modell.knoten[k.modell_knoten].hat_mesh == 1 for k in mesh.knoten)
        else:
            mesh_ok &= all(e == (1 + li, i) for i, e in enumerate(mesh.eintraege)) and \
                [k.modell_knoten for k in mesh.knoten] == [1 + li]
        vb = [grafik.GfxPuffer.aus_ressource(idx[k]) for k in mesh.vb_kennungen]
        ib = grafik.GfxPuffer.aus_ressource(idx[mesh.ib_kennungen[0]])
        puffer_ok &= not any(p.pruefen() for p in vb + [ib]) and len({p.anzahl for p in vb}) == 1
        puffer_ok &= [(p.stride, p.code) for p in vb] in ([(12, 39), (8, 35), (8, 35), (8, 35)],
                                                          [(12, 39), (8, 35), (8, 35), (8, 35), (4, PUFFER_CODE_FARBE)]) \
            and (ib.stride, ib.code) == (2, 19)
        ind = ib.indizes()
        pos = np.frombuffer(vb[0].daten, "<f4").reshape(-1, 3)
        for t in mesh.teilnetze:
            index_ok &= t.start_index + t.index_anzahl <= len(ind)
            teil = ind[t.start_index:t.start_index + t.index_anzahl].astype(np.int64) + t.basis_vertex
            index_ok &= len(teil) > 0 and teil.max() < vb[0].anzahl
            p = pos[teil].astype(np.float64)
            kugel_ok &= bool((np.sqrt(((p - np.array(t.kugel[:3])) ** 2).sum(1)) <= t.kugel[3] + 1e-6).all())
        geladen = grafik.modell_laden(idx, basis, lod=li, welt=not animiert)   # bewegt: Knotenraum wie bau.lods
        ist = np.concatenate([_dreieckssuppe(n.positionen, n.dreiecke) for n in geladen.netze])
        soll = np.concatenate([_dreieckssuppe(t.positionen.astype(np.float32), t.dreiecke) for t in bau.lods[li]])
        ist, soll = ist[np.lexsort(ist.T[::-1])], soll[np.lexsort(soll.T[::-1])]
        gleich = ist.shape == soll.shape and np.array_equal(ist, soll)
        geo_ok &= gleich
        einzel.append("LOD %d: %d Dreiecke%s" % (li, len(ist), "" if gleich else " ≠ Eingabe"))
        # Normalen, Tangenten und UV vertexweise gegen die Eingabe (halbe Genauigkeit)
        n_ist = np.concatenate([n.attribute["NORMAL0"][:, :3] for n in geladen.netze])
        t_ist = np.concatenate([n.attribute["TANGENT0"] for n in geladen.netze])
        laenge = np.linalg.norm(n_ist, axis=1)
        geo_ok &= bool(np.abs(laenge - 1).max() < 2e-3) and set(np.unique(t_ist[:, 3]).tolist()) <= {-1.0, 1.0}
        geo_ok &= bool(np.abs((n_ist * t_ist[:, :3]).sum(1)).max() < 0.02)
        if animiert:
            geladen = grafik.modell_laden(idx, basis, lod=li)                  # Ruhelage im Modellraum
        alle_pos.append(np.concatenate([n.positionen for n in geladen.netze]))
    ok("GfxMeshRes: Rundlauf bitgleich, Einträge/Knoten je LOD", mesh_ok)
    ok("Puffer: Köpfe wie die Originale, Größe = Stride × Anzahl", puffer_ok)
    ok("Indizes im gültigen Bereich (Basis-Vertex)", index_ok)
    ok("Geometrie = Eingabe (Dreiecke bitgleich in float32, Normalen/Tangenten gültig)", geo_ok, "; ".join(einzel))
    ok("Hüllkugeln der Teilnetze enthalten ihre Vertices", kugel_ok)
    p = np.concatenate(alle_pos).astype(np.float64)
    if animiert:
        ok("AABB umfasst die Geometrie in Ruhe (und die Bewegung)",
           bool((p.min(0) >= np.array(modell.aabb_min) - 1e-4).all() and (p.max(0) <= np.array(modell.aabb_max) + 1e-4).all()),
           "%s … %s" % (np.round(modell.aabb_min, 3).tolist(), np.round(modell.aabb_max, 3).tolist()))
        cr, tr = archiv.find(animiert["clip"]), None
        if cr is not None:
            clip = _anim.clip_lesen(cr.fragments[0].read(), cr.fragments[1].read())
            tr = archiv.find(clip.string_table)
            namen = {k.name for k in modell.knoten}
            ok("Clip und Namenstabelle lesbar, Kanäle auf bewegte Modellknoten",
               tr is not None and set(clip.kurven()) <= namen and set(clip.kurven()) <= set(_anim.stringtabelle_lesen(tr.data()))
               and all(len(v) == 3 for v in clip.kurven().values()),
               "%s, %d Knoten, %.2f s" % (animiert["clip"].rsplit("&clip=", 1)[-1], len(clip.kurven()), clip.laenge))
        else:
            ok("Clip vorhanden", False, animiert["clip"])
    else:
        ok("AABB = Grenzen der Geometrie", np.allclose(p.min(0), modell.aabb_min, atol=1e-6) and np.allclose(p.max(0), modell.aabb_max, atol=1e-6),
           "%s … %s" % (np.round(modell.aabb_min, 3).tolist(), np.round(modell.aabb_max, 3).tolist()))
    ok("Modellkugel enthält alle Vertices", (np.sqrt(((p - np.array(modell.kugel[:3])) ** 2).sum(1)) <= modell.kugel[3] + 1e-6).all(),
       "Radius %.3f, halbe AABB-Diagonale %.3f" % (modell.kugel[3], float(np.linalg.norm(p.max(0) - p.min(0))) / 2))
    # Material- und Texturverweise
    br = archiv.find(bau.modell + ".emb?context=default")
    if br is None:
        ok("Materialbindung vorhanden", False)
        return aus
    bind = grafik.MaterialBindung.aus_bytes(br.data())
    slots = set()
    for mk in modell.mesh_kennungen:
        slots |= set(grafik.GfxMesh.aus_bytes(idx[mk].data()).materialnamen)
    ok("Bindung: Rundlauf, deckt alle Materialslots", bind.to_bytes() == br.data() and slots <= set(bind.slots),
       ", ".join("%s→%s" % (s, bind.material_fuer(s).rsplit("/", 1)[-1]) for s in bind.slots))
    offen, texturen = [], 0
    for mk in bind.materialien:
        mr = idx.get(mk) or (aussen.get(mk) if aussen else None)
        if mr is None:
            offen.append(mk)
            continue
        mat = grafik.Material.aus_bytes(mr.data())
        for tk in set(list(mat.texturen().values()) + [x.textur for x in mat.parameter if x.klasse == 3 and x.textur]):
            sr = idx.get(tk) or (aussen.get(tk) if aussen else None)
            if sr is None:
                offen.append(tk)
                continue
            sicht = grafik.TexturSicht.aus_bytes(sr.fragments[0].read())
            ir = idx.get(sicht.bild) or (aussen.get(sicht.bild) if aussen else None)
            if ir is None:
                offen.append(sicht.bild)
                continue
            t = grafik.Textur.aus_ressource(ir)
            texturen += 1
            if t.pruefen() or sicht.format != t.format or sicht.mips != t.mips:
                offen.append("fehlerhaft: " + sicht.bild)
    ok("Materialien und Texturen auflösbar und gültig", not offen, "%d Texturen%s" % (texturen, (", offen: " + offen[0]) if offen else ""))
    return aus
