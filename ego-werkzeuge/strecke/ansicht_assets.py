"""Spiel-Assets für die 3D-Ansicht des Mapeditors (26.11.2026, Mapeditor/docs/3d_spielassets.md).

Nur lesend aus Trägerpaket und Spielordner. Alles landet als PNG/NPZ im Zwischenspeicher des Nutzers und wird nie
weitergegeben. Die Rechnung folgt dem Bau, damit die Ansicht zeigt, was ins Spiel kommt:

* **A Oberflächen:** Albedo der Baumaterialien je Generatorklasse (Asphalt bzw. die gewählte Fahrbahnvorlage,
  Randstein-Atlas mit Anfangs-/Endstücken, Wulst, Gras/Kies im gewählten Biom, Beton, Kunstrasen), UV wie im Bau
  (``spiel_geometrie.vorschau_netze`` = ``render_netze``), Kachelung und Tönung aus den Materialkonstanten.
* **B Außengelände** des Trägers (``*_outer_terrain``) mit den Bergen aus ``umgebung.json``
  (``umgebung.berge_einsetzen``) und den Karten des Baus (``spiel_weltkarten.aussen_bilder``) bzw. den Luftbildern
  des Trägers ohne Weltkarten; dazu Loch und Lochrand für den Boden der Ansicht.
* **C/D Welt nach den Bauschritten:** Entfernungstafeln, Aufräumen, Logik, Plätze, Garagen und Trägerreste laufen im
  Speicher auf dem Träger (``spiel_welt``, ``spiel_reste``); übrig bleiben Garagen, Kommandostände, Livery-Tafeln,
  Entfernungstafeln usw. Barrieren, Zäune und Schilder des Trägers entfernt der Bau, sie erscheinen deshalb nicht.

Koordinaten der Ausgabe: Definition (Spiel − Spielweltversatz), wie das Generatornetz der Ansicht.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from typing import Dict, Iterable, List, Optional

import numpy as np

from ego import erp, grafik as g, grafik_textur as gx

#: Fassung der Zwischenspeicher-Inhalte (bei Änderungen der Rechnung erhöhen)
FASSUNG = 4
MATERIALIEN = 'eaid://environment_package/tracks/%s/terrain/materials/'
#: Klasse → (Material, Albedo-Slot, UV-Faktor u/v aus den Materialkonstanten, Tönungskonstante)
OBERFLAECHEN = {
    'asphalt': ('por_track_a.material', 'me_albedoMap', (0.5, 0.5), 'me_baseMapTint'),
    'boxengasse': ('por_track_pit_01.material', 'me_albedoMap', (0.5, 0.5), 'me_baseMapTint'),
    'auslauf': ('por_track_pit_01.material', 'me_albedoMap', (0.5, 0.5), 'me_baseMapTint'),
    'randstein': ('por_rumble_triangle_b_02.material', 'me_albedoMap', (1.0, 1.0), None),
    'randstein_tief': ('por_rumble_triangle_b_02.material', 'me_albedoMap', (1.0, 1.0), None),
    'randstein_anfang': ('por_rumble_triangle_b_01.material', 'me_albedoMap', (1.0, 1.0), None),
    'randstein_ende': ('por_rumble_triangle_b_03.material', 'me_albedoMap', (1.0, 1.0), None),
    'wulst': ('por_rumble_sausagekerb_a_02.material', 'me_albedoMap', (1.0, 1.0), None),
    'gras': ('por_grass_infield_a_01.material', 'me_layer2_albedoMap', (4.0, 4.0), 'me_layer2_tint'),
    'kies': ('por_gravel_a.material', 'me_layer1_albedoMap', (12.0, 6.0), 'me_layer1_tint'),
    'mauer': ('por_concrete_a_01.material', 'me_diffuseMap', (1.0, 1.0), 'me_diffuseTint'),
    'gebaeude': ('por_concrete_a_01.material', 'me_diffuseMap', (1.0, 1.0), 'me_diffuseTint'),
    'kunstrasen': ('por_offtrack_a.material', 'me_albedoMap', (1.0, 1.0), None),
}
#: Boden der Ansicht (Grundfläche des Baus: Grasmaterial), Meter je Kachel
BODEN_KACHEL_M = 8.0
#: Wüste: Schicht der Biomtextur für Gras bzw. Kies (spiel_weltkarten.BIOM_WUESTE)
BIOM_SCHICHT = {'gras': 2, 'kies': 1}
#: Außengelände ohne Weltkarten: Luftbild-Slot je Material
AUSSEN_LUFTBILD = {'por_terrain_b.material': 'me_diffuse2Map', 'por_terrain_c.material': 'me_diffuseMap'}
#: Welt: Ebenen ohne sichtbare Modelle bzw. mit deckungsgleichen Varianten
WELT_EBENEN_AUS = ('sky', 'vfx')
#: Garagen: nur eine Variante zeigen (die übrigen Ebenen liegen für andere Spielmodi deckungsgleich darüber)
WELT_GARAGEN = ('objects_garages_f1',)
#: Modelle ohne sichtbaren Körper (Schattenkörper, Regenblocker) oder Bodendecals mit Transparenz
WELT_MODELLE_AUS = ('garage_shadow', 'rainblocker', 'pit_decal')
#: Albedo-Slots von Objektmaterialien nach Vorrang
ALBEDO_SLOTS = ('me_albedoMap', 'me_baseColourMap', 'me_baseColorMap', 'me_diffuseMap', 'me_baseAlbedoMap',
                'me_albedo1Map')


class AssetFehler(ValueError):
    pass


# ------------------------------------------------------------------ Quellen
class Traeger:
    """Trägerpaket (ZIP mit ERP und ``mipmaps/``) oder ERP-Datei, nur lesend. ``archiv`` darf im Speicher verändert
    werden (Bauschritte für C/D), geschrieben wird nie."""

    def __init__(self, pfad, arbeit: str):
        from . import umgebung
        self.pfad = os.fspath(pfad)
        self.arbeit = arbeit
        os.makedirs(arbeit, exist_ok=True)
        self._zip = None
        self._mips = {}
        self._mip_ordner = []
        if self.pfad.lower().endswith('.zip'):
            self._zip = zipfile.ZipFile(self.pfad)
            for n in self._zip.namelist():
                if n.lower().endswith('.mipmaps'):
                    self._mips[n.rsplit('/', 1)[-1]] = n
            name = next((n for n in self._zip.namelist() if n.lower().endswith('.erp') and '/' not in n.strip('/')),
                        None)
            if name is None:
                raise AssetFehler('No ERP in the carrier package')
            self.code = os.path.splitext(name)[0]
        else:
            self.code = os.path.splitext(os.path.basename(self.pfad))[0]
            self._mip_ordner = [os.path.dirname(os.path.abspath(self.pfad))]
        self.archiv = umgebung.traeger_oeffnen(self.pfad)
        self.index = g.RessourcenIndex()
        self.index.hinzufuegen(self.archiv)

    def schliessen(self):
        try:
            self.archiv.close()
        except Exception:  # noqa: BLE001
            pass
        if self._zip is not None:
            self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.schliessen()

    def mip_datei(self, t: g.Textur) -> Optional[str]:
        if not t.extern_pfad:
            return None
        name = t.extern_pfad.rsplit('/', 1)[-1]
        if self._zip is not None:
            mitglied = self._mips.get(name) or next((v for k, v in self._mips.items() if k.endswith('_' + name)), None)
            if mitglied is None:
                return None
            ziel = os.path.join(self.arbeit, 'mips', mitglied.rsplit('/', 1)[-1])
            if not os.path.exists(ziel):
                os.makedirs(os.path.dirname(ziel), exist_ok=True)
                with self._zip.open(mitglied) as q, open(ziel + '.tmp', 'wb') as z:
                    while True:
                        b = q.read(1 << 20)
                        if not b:
                            break
                        z.write(b)
                os.replace(ziel + '.tmp', ziel)
            return ziel
        return gx.extern_datei_finden(t, self._mip_ordner)

    def material(self, name: str) -> Optional[g.Material]:
        r = self.archiv.find(MATERIALIEN % self.code + name)
        if r is None:
            r = next((x for x in self.archiv.resources if x.identifier.endswith('/terrain/materials/' + name)), None)
        return g.Material.aus_bytes(r.data()) if r is not None else None

    def bild(self, kennung: str, max_px: int = 1024):
        return textur_bild(self.index, kennung, self.mip_datei, max_px)


class Spielstrecke:
    """Ressourcen einer Spielstrecke aus dem Spielordner (nur lesend), Texturen mit externen Stufen."""

    def __init__(self, spiel: str, erp_pfad: str):
        from . import umgebung
        self.spiel = spiel
        self.index = umgebung._ressourcen_index(erp_pfad)
        self._ordner = [os.path.join(spiel, x) for x in ('2025_asset_groups', '2021_asset_groups')] + [spiel]

    def mip_datei(self, t):
        return gx.extern_datei_finden(t, self._ordner)

    def bild(self, kennung, max_px=1024):
        return textur_bild(self.index, kennung, self.mip_datei, max_px)

    def schliessen(self):
        self.index.schliessen()


def textur_bild(index, kennung: str, mip_datei, max_px: int = 1024, alpha: bool = False):
    """Pillow-Bild (RGB) einer Textur über ihre Kennung (SRV oder Bild), höchstens ``max_px`` Kantenlänge; externe
    Stufen nur so weit wie nötig. None, wenn die Textur fehlt."""
    if not kennung:
        return None
    r = index.get(kennung) or index.get(kennung + '.image')
    if r is None:
        return None
    if 'SRV' in r.resource_type:
        r = index.get(g.TexturSicht.aus_bytes(r.data()).bild)
        if r is None:
            return None
    t = g.Textur.aus_ressource(r)
    ext = None
    if t.extern_pfad and t.schichten == 1:
        pfad = mip_datei(t)
        if pfad:
            # nur die Stufen, die für max_px nötig sind (größte zuerst, Kante = Innenkante · 2^Anzahl); die größeren
            # gar nicht erst entpacken (LZ4 in Python, 4096er Stufe ≈ 0,1 s)
            n = len(t.extern_mips)
            ab = 0
            while ab < n and max(t.breite, t.hoehe) * (1 << (n - ab - 1)) >= max_px:
                ab += 1
            ext = gx.extern_stufen(t, pfad, ab=ab) or None
    bild = gx.textur_bild(t, ext).convert('RGBA' if alpha else 'RGB')      # RGBA: Ausschnitt (Blätter)
    if max(bild.size) > max_px:
        bild.thumbnail((max_px, max_px))
    return bild


def _mittelfarbe(png) -> Optional[np.ndarray]:
    """Mittlere Farbe (RGB 0…1) eines Bildes oder None."""
    if not png or not os.path.exists(png):
        return None
    from PIL import Image
    with Image.open(png) as bild:
        return np.asarray(bild.convert('RGB').resize((64, 64)), float).reshape(-1, 3).mean(0) / 255.0


def _schluessel(*teile) -> str:
    return hashlib.sha1(json.dumps([FASSUNG, *teile], sort_keys=True, default=str).encode()).hexdigest()[:16]


def _datei_stand(pfad) -> list:
    st = os.stat(pfad)
    return [os.path.abspath(pfad), st.st_size, int(st.st_mtime)]


def _netz_speichern(pfad, pos, nor, uv, idx, farbe=None):
    teile = dict(pos=np.asarray(pos, np.float32), nor=np.asarray(nor, np.float32),
                 idx=np.asarray(idx, np.uint32).ravel())
    if uv is not None:
        teile['uv'] = np.asarray(uv, np.float32)
    if farbe is not None:
        teile['farbe'] = np.asarray(farbe, np.float32)
    tmp = pfad + '.tmp.npz'
    np.savez(tmp, **teile)               # unkomprimiert: der Editor lädt ohne zlib (3d_ladeperformance.md)
    os.replace(tmp, pfad)
    return pfad


def _tint(material: Optional[g.Material], name: Optional[str], faktor=(1.0, 1.0, 1.0)) -> list:
    wert = (1.0, 1.0, 1.0)
    if material is not None and name:
        k = material.konstanten().get(name)
        if k is not None and len(k) >= 3:
            wert = tuple(float(v) for v in k[:3])
    return [round(min(max(w * f, 0.0), 2.0), 4) for w, f in zip(wert, faktor)]


# ------------------------------------------------------------------ A: Oberflächen
def oberflaechen_texturen(tr: Traeger, ordner: str, *, asphalt=None, kunstrasen='melbourne', biom='standard',
                          farbton='standard', spielordner=None, meldungen=None) -> dict:
    """{Klasse: {"png", "tint", "uv_faktor"}} plus ``_boden``; Texturen aus dem Träger, der Fahrbahnvorlage (Asphalt),
    der Kunstrasen-Spielstrecke und dem Biom (Gras/Kies) aus dem Spielordner."""
    from . import asphalt_vorlagen as av, spiel_weltkarten as wk, umgebung
    meldungen = meldungen if meldungen is not None else []
    os.makedirs(ordner, exist_ok=True)
    faktor = wk.FARBTOENE.get(farbton, (1.0, 1.0, 1.0))
    spiel = umgebung.spielordner_finden(spielordner)
    erg, bilder = {}, {}

    def ablegen(schluessel, bild):
        pfad = os.path.join(ordner, 'tex_%s.png' % hashlib.sha1(schluessel.encode()).hexdigest()[:16])
        if bild is not None and not os.path.exists(pfad):
            bild.save(pfad, compress_level=1)
        return pfad if bild is not None or os.path.exists(pfad) else None

    def aus_traeger(material, slot):
        key = 'traeger|%s|%s' % (material, slot)
        if key not in bilder:
            m = tr.material(material)
            kennung = (m.texturen() if m else {}).get(slot)
            pfad = os.path.join(ordner, 'tex_%s.png' % hashlib.sha1(key.encode()).hexdigest()[:16])
            bilder[key] = pfad if os.path.exists(pfad) else ablegen(key, tr.bild(kennung) if kennung else None)
        return bilder[key]

    for klasse, (material, slot, uv_f, tint_name) in OBERFLAECHEN.items():
        m = tr.material(material)
        erg[klasse] = {'png': aus_traeger(material, slot), 'tint': _tint(m, tint_name), 'uv_faktor': list(uv_f),
                       'material': material}
    # Fahrbahnvorlage (nur por_track_a = Asphalt, wie asphalt_vorlagen.einsetzen)
    if asphalt and asphalt != 'traeger':
        try:
            key, eintrag = av._aufloesen(av.asphalt_katalog(), asphalt)
            if not eintrag.get('traeger'):
                if spiel is None:
                    raise AssetFehler('game folder missing')
                kennung = (eintrag.get('slots') or {}).get('me_albedoMap', '').split('?')[0]
                q = Spielstrecke(spiel, av._strecken_erp(spiel, eintrag.get('strecke') or key))
                try:
                    pfad = ablegen('asphalt|%s|%s' % (key, kennung), q.bild(kennung))
                finally:
                    q.schliessen()
                if pfad:
                    erg['asphalt']['png'] = pfad
                    erg['asphalt']['tint'] = [1.0, 1.0, 1.0]
        except Exception as exc:  # noqa: BLE001  Vorschau: Träger-Asphalt statt Abbruch
            meldungen.append('Track surface %s: %s' % (asphalt, exc))
    # Kunstrasen aus der Spielstrecke (wie asphalt_vorlagen.kunstrasen_einsetzen)
    if spiel is not None and kunstrasen:
        try:
            pfad_erp = av._strecken_erp(spiel, kunstrasen)
            with erp.open_erp(pfad_erp) as a:
                kand = [r for r in a.resources if r.identifier.endswith('.material')
                        and any(w in r.identifier.rsplit('/', 1)[-1].lower() for w in ('astro', 'turf'))
                        and 'physics' not in r.identifier.lower()]
                tex = g.Material.aus_bytes(kand[0].data()).texturen() if kand else {}
            kennung = next((tex[s] for s in av.KUNSTRASEN_SLOTS['me_albedoMap'] if s in tex), None)
            if kennung:
                q = Spielstrecke(spiel, pfad_erp)
                try:
                    pfad = ablegen('kunstrasen|%s|%s' % (kunstrasen, kennung), q.bild(kennung))
                finally:
                    q.schliessen()
                if pfad:
                    erg['kunstrasen'].update(png=pfad, uv_faktor=[0.5, 0.5])
        except Exception as exc:  # noqa: BLE001
            meldungen.append('Artificial grass %s: %s' % (kunstrasen, exc))
    else:
        erg['kunstrasen']['png'] = None
    # Biom Wüste: Schichttexturen von Bahrain (wie spiel_weltkarten.biom_wueste)
    if biom == 'wueste':
        try:
            if spiel is None:
                raise AssetFehler('game folder missing')
            q = Spielstrecke(spiel, os.path.join(spiel, '2025_asset_groups', 'environment_package', 'tracks',
                                                 'bahrain', 'wep', 'bahrain_common.erp'))
            try:
                for klasse, schicht in BIOM_SCHICHT.items():
                    kennung = wk.BIOMES + '%s_dh.tif' % wk.BIOM_WUESTE[schicht]
                    pfad = ablegen('biom|%s' % kennung, q.bild(kennung))
                    if pfad:
                        erg[klasse]['png'] = pfad
            finally:
                q.schliessen()
        except Exception as exc:  # noqa: BLE001
            meldungen.append('Desert biome: %s' % exc)
    for klasse in BIOM_SCHICHT:
        erg[klasse]['tint'] = [round(min(t * f, 2.0), 4) for t, f in zip(erg[klasse]['tint'], faktor)]
    # Gras (und damit der Boden): Mittelfarbe wie das Gras des Außengeländes im Bau (spiel_weltkarten.AUSSEN_FARBEN),
    # sonst wirkt die Schichttextur ohne Makrokarte und Bodenbewuchs zu hell (Wüste) bzw. zu dunkel
    ziel = np.asarray(wk.AUSSEN_FARBEN.get(biom, wk.AUSSEN_FARBEN['standard'])[0], float) * np.asarray(faktor) / 255.0
    mittel = _mittelfarbe(erg['gras'].get('png'))
    if mittel is not None:
        # bis 3-fach: Die Grastextur ist bräunlich (≈ 88/81/47), Farbton „saftig“ braucht Grün ×1,9 (27.11.2026)
        erg['gras']['tint'] = [round(float(np.clip(z / max(m, 1e-3), 0.25, 3.0)), 4) for z, m in zip(ziel, mittel)]
    for klasse, e in erg.items():
        _toenung_einrechnen(e, ordner)
    erg['_boden'] = dict(erg['gras'], uv_m=32.0 / erg['gras']['uv_faktor'][0])   # Gras: UV 1/32 m × Kachelung
    return erg


def _toenung_einrechnen(eintrag: dict, ordner: str) -> None:
    """Tönung in die Textur einrechnen (neue PNG, Tönung danach 1): Die 3D-Ansicht setzt sie sonst als Materialfarbe,
    und die kann nicht über 1 gehen (``QColor``); Gras blieb dort bei der bräunlichen Rohtextur."""
    t = eintrag.get('tint')
    png = eintrag.get('png')
    if not png or not t or all(abs(float(x) - 1.0) < 1e-3 for x in t) or not os.path.exists(png):
        return
    from PIL import Image
    ziel = os.path.join(ordner, '%s_t%s.png' % (os.path.splitext(os.path.basename(png))[0],
                                                hashlib.sha1(json.dumps(t).encode()).hexdigest()[:8]))
    if not os.path.exists(ziel):
        with Image.open(png) as bild:
            a = np.asarray(bild.convert('RGB'), np.float32) * np.asarray(t[:3], np.float32)
        Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), 'RGB').save(ziel, compress_level=1)
    eintrag['png'] = ziel
    eintrag['tint_eingerechnet'] = list(t)
    eintrag['tint'] = [1.0, 1.0, 1.0]


def oberflaechen_netze(szene_npz: str, param: Optional[dict], ordner: str) -> Dict[str, str]:
    """{Klasse: NPZ} mit Positionen, Normalen, UV wie im Bau (Definitionskoordinaten); Linien leicht angehoben."""
    from . import spiel_geometrie as sg
    os.makedirs(ordner, exist_ok=True)
    d = np.load(szene_npz, allow_pickle=False)
    netze = sg.vorschau_netze(d['vertices'], d['normalen'], d['uv'], d['dreiecke'], d['oberflaeche'],
                              [str(n) for n in d['namen']], param)
    je = {}
    for n in netze:
        je.setdefault(n['klasse'], []).append(n)
    erg = {}
    for klasse, teile in je.items():
        pos, nor, uv, idx, basis = [], [], [], [], 0
        for n in teile:
            p = np.asarray(n['pos'], np.float64).copy()
            if klasse in ('linie', 'startbox'):
                p[:, 1] += 0.015               # über dem Asphalt darunter (im Spiel überblendet)
            pos.append(p)
            nor.append(n['normalen'])
            uv.append(n['uv'])
            idx.append(np.asarray(n['dreiecke']).ravel() + basis)
            basis += len(p)
        pfad = os.path.join(ordner, 'klasse_%s.npz' % klasse)
        erg[klasse] = _netz_speichern(pfad, np.concatenate(pos), np.concatenate(nor), np.concatenate(uv),
                                      np.concatenate(idx))
    return erg


# ------------------------------------------------------------------ B: Außengelände
def aussengelaende(tr: Traeger, ordner: str, versatz, *, umgebung_daten=None, weltkarten=True, biom='standard',
                   farbton='standard', groesse=1024) -> dict:
    """Netze des Außengeländes (mit Bergen) samt Karte, Loch (x0, z0, Kante) und Lochrand (Definitionskoordinaten)."""
    from . import spiel_geometrie as sg, spiel_weltkarten as wk, umgebung
    from PIL import Image
    os.makedirs(ordner, exist_ok=True)
    v = np.asarray(versatz, np.float64)
    if umgebung_daten and (umgebung_daten.get('gelaende') or {}).get('berge'):
        umgebung.berge_einsetzen(tr.archiv, umgebung_daten, versatz=tuple(v))
    netze = wk.aussen_netze(tr.archiv)
    bilder = {}
    if weltkarten:
        f = np.array(wk.FARBTOENE.get(farbton, (1.0, 1.0, 1.0)))
        farben = tuple(np.clip(c * f, 0, 255) for c in wk.AUSSEN_FARBEN.get(biom, wk.AUSSEN_FARBEN['standard']))
        for material, (rgb, loch, _h, _k) in wk.aussen_bilder(netze, farben, groesse).items():
            bilder[material] = Image.fromarray(np.asarray(rgb, np.uint8)[..., :3], 'RGB')
    erg = {'netze': []}
    for material, n in sorted(netze.items()):
        bild = bilder.get(material)
        tint = [1.0, 1.0, 1.0]
        if bild is None:
            m = tr.material(material)
            slot = AUSSEN_LUFTBILD.get(material, 'me_diffuseMap')
            kennung = (m.texturen() if m else {}).get(slot)
            bild = tr.bild(kennung, 2048) if kennung else None
            tint = _tint(m, 'me_diffuse2Tint' if slot == 'me_diffuse2Map' else 'me_diffuseTint')
            tint = [min(2.0 * t, 1.0) for t in tint]     # Luftbilder sind schon hell; Shader-Tönung nur halb
        png = None
        if bild is not None:
            png = os.path.join(ordner, 'aussen_%s.png' % material.split('.')[0])
            bild.save(png, compress_level=1)
        p = np.asarray(n.positionen, np.float64) - v
        nor = n.normalen if n.normalen is not None else g.normalen_berechnen(n.positionen, n.dreiecke)
        npz = _netz_speichern(os.path.join(ordner, 'aussen_%s.npz' % material.split('.')[0]), p, nor, n.uvs,
                              n.dreiecke)
        erg['netze'].append({'npz': npz, 'png': png, 'tint': tint, 'material': material})
    loch = sg.loch_lesen(tr.archiv)
    rand = np.asarray(loch['rand'], np.float64).reshape(-1, 3) - v
    rand_npz = os.path.join(ordner, 'lochrand.npz')
    np.savez(rand_npz, rand=rand.astype(np.float32))
    erg['loch'] = [loch['x0'] - v[0], loch['z0'] - v[2], loch['kante']]
    erg['rand'] = rand_npz
    return erg


# ------------------------------------------------------------------ C/D: Welt nach den Bauschritten
def welt_instanzen(tr: Traeger, logik: dict, *, versatz=(0.0, 0.0, 0.0), reste=True, boxen_korrektur=True,
                   raster=None, protokoll=None) -> List[dict]:
    """Bauschritte der Welt im Speicher (wie ``neue_strecke_tool``: Entfernungstafeln, Aufräumen, Logik, Plätze,
    Garagen, Reste) mit dem Spielweltversatz des Projekts. Rückgabe: [{"modell", "bindung", "ebene", "matrix"}] der
    sichtbaren Instanzen (Spielkoordinaten)."""
    from . import platzierung
    alt = platzierung.VERSATZ
    platzierung.setzen(tuple(float(x) for x in versatz))
    try:
        return _welt_instanzen(tr, logik, reste, boxen_korrektur, raster, protokoll)
    finally:
        platzierung.setzen(alt)


def _welt_instanzen(tr, logik, reste, boxen_korrektur, raster, protokoll):
    from ego import pkg
    from . import spiel_reste, spiel_welt
    a = tr.archiv
    zustand = spiel_welt.TraegerZustand.erfassen(a)
    zustand.raster = raster or None
    if logik.get('bremsmarker'):
        spiel_welt.bremsmarker_setzen(a, zustand, logik['bremsmarker'])
    regeln = None
    garagen = spiel_welt.garagen_modell(logik)
    if garagen == 'traeger':
        regeln = dict(spiel_welt.LAYER_REGELN)
        for kurz in spiel_welt.GARAGEN_LAYER:
            regeln[kurz] = (spiel_welt.ANPASSEN, 'Garagenmodule des Trägers, an die neuen Boxen versetzt')
    spiel_welt.welt_aufraeumen(a, zustand, regeln)
    lg, plaetze, prot = spiel_welt.logik_vorbereiten(logik, zustand, 0.0, 0.0, boxen_korrektur_an=boxen_korrektur)
    spiel_welt.platz_layer_setzen(a, zustand, plaetze)
    if garagen == 'traeger':
        spiel_welt.garagen_versetzen(a, zustand, lg, prot)
    if reste:
        spiel_reste.reste_anpassen(a, zustand, lg, prot)
    erg = []
    for kurz, r in spiel_welt.layer_ressourcen(a, zustand.code).items():
        if kurz in WELT_EBENEN_AUS or (kurz.startswith('objects_garages') and kurz not in WELT_GARAGEN):
            continue
        p = pkg.lesen(r.fragments[0].read())
        for i in p.instanzen():
            if not i.modell or i.matrix is None or any(w in str(i.modell) for w in WELT_MODELLE_AUS):
                continue
            bindung = i._wert('overrides', 'Model', 'mat binding') or None
            erg.append({'modell': str(i.modell), 'bindung': str(bindung) if bindung else None, 'ebene': kurz,
                        'matrix': [float(x) for x in i.matrix]})
    if protokoll is not None:
        protokoll['welt'] = {'instanzen': len(erg), 'garagen': garagen}
    return erg


def _albedo(material: g.Material) -> Optional[str]:
    tex = material.texturen()
    for s in ALBEDO_SLOTS:
        if s in tex:
            return tex[s]
    return next((v for k, v in tex.items() if any(w in k.lower() for w in ('albedo', 'diffuse', 'colour', 'color'))),
                None)


def _modell_teile(tr: Traeger, modell: str, bindung: Optional[str], lod_max=1) -> List[dict]:
    """Teilnetze eines Modells (Modellraum) mit Albedo-Kennung je Material (Bindung der Instanz oder Standard)."""
    lm = g.modell_laden(tr.index, modell, lod=0)
    if len(lm.meshes) > 1 and lod_max:
        lm = g.modell_laden(tr.index, modell, lod=min(lod_max, len(lm.meshes) - 1))
    basis = g.basis_kennung(lm.kennung)
    emb = tr.index.get(bindung) if bindung else None
    emb = emb or tr.index.get(basis[:-4] + '.emb?context=default')
    mb = g.MaterialBindung.aus_bytes(emb.data()) if emb is not None else None
    teile = []
    for n in lm.netze:
        if not len(n.dreiecke):
            continue
        albedo = None
        if mb is not None:
            mk = mb.material_fuer(n.material)
            mr = tr.index.get(mk) if mk else None
            if mr is not None:
                try:
                    albedo = _albedo(g.Material.aus_bytes(mr.data()))
                except Exception:  # noqa: BLE001
                    albedo = None
        nor = n.normalen if n.normalen is not None else g.normalen_berechnen(n.positionen, n.dreiecke)
        uv = n.uvs if n.uvs is not None else np.zeros((len(n.positionen), 2), np.float32)
        teile.append({'pos': np.asarray(n.positionen, np.float64), 'nor': np.asarray(nor, np.float64),
                      'uv': np.asarray(uv, np.float32), 'tri': np.asarray(n.dreiecke).ravel(), 'albedo': albedo})
    return teile


def welt_netze(tr: Traeger, instanzen: Iterable[dict], ordner: str, versatz, *, max_px=512, meldungen=None) -> dict:
    """Instanzen nach Albedo-Textur zusammengefasst: {"netze": [{"npz", "png"}], "modelle", "instanzen"}."""
    meldungen = meldungen if meldungen is not None else []
    os.makedirs(ordner, exist_ok=True)
    v = np.asarray(versatz, np.float64)
    gruppen = {}
    for e in instanzen:
        gruppen.setdefault((e['modell'], e['bindung']), []).append(np.asarray(e['matrix'], np.float64).reshape(4, 4))
    je_textur = {}
    modelle = 0
    for (modell, bindung), matrizen in sorted(gruppen.items(), key=lambda kv: (kv[0][0], kv[0][1] or '')):
        try:
            teile = _modell_teile(tr, modell, bindung)
        except Exception as exc:  # noqa: BLE001  ein unlesbares Modell darf die Ansicht nicht verhindern
            meldungen.append('%s: %s' % (modell.rsplit('/', 1)[-1], exc))
            continue
        modelle += 1
        for t in teile:
            ziel = je_textur.setdefault(t['albedo'], {'pos': [], 'nor': [], 'uv': [], 'idx': [], 'n': 0})
            for M in matrizen:
                R, T = M[:3, :3], M[3, :3]
                p = t['pos'] @ R + T - v
                try:
                    nr = t['nor'] @ np.linalg.inv(R).T
                except np.linalg.LinAlgError:
                    nr = t['nor']
                nr /= np.maximum(np.linalg.norm(nr, axis=1), 1e-9)[:, None]
                if np.linalg.det(R) < 0:
                    tri = t['tri'].reshape(-1, 3)[:, ::-1].ravel()
                else:
                    tri = t['tri']
                ziel['pos'].append(p)
                ziel['nor'].append(nr)
                ziel['uv'].append(t['uv'])
                ziel['idx'].append(tri + ziel['n'])
                ziel['n'] += len(p)
    netze = []
    for k, (albedo, d) in enumerate(sorted(je_textur.items(), key=lambda kv: kv[0] or '')):
        if not d['n']:
            continue
        name = hashlib.sha1(str(albedo).encode()).hexdigest()[:16]
        png = None
        if albedo:
            png = os.path.join(ordner, 'welt_%s.png' % name)
            if not os.path.exists(png):
                try:
                    bild = tr.bild(albedo, max_px)
                except Exception:  # noqa: BLE001
                    bild = None
                if bild is not None:
                    bild.save(png, compress_level=1)
                else:
                    png = None
        npz = _netz_speichern(os.path.join(ordner, 'welt_%s.npz' % name), np.concatenate(d['pos']),
                              np.concatenate(d['nor']), np.concatenate(d['uv']), np.concatenate(d['idx']))
        netze.append({'npz': npz, 'png': png})
    return {'netze': netze, 'modelle': modelle, 'instanzen': sum(len(m) for m in gruppen.values())}


# ------------------------------------------------------------------ Gesamt
def _info_lesen(ordner: str):
    """Fertiges Ergebnis eines Teils (``info.json``) oder None; frischt den Zeitstempel auf (Aufräumen nach Alter)."""
    info = os.path.join(ordner, 'info.json')
    if not os.path.exists(info):
        return None
    try:
        with open(info, encoding='utf-8') as f:
            daten = json.load(f)
        os.utime(ordner)
        return daten
    except (OSError, ValueError):
        return None


def _info_schreiben(ordner: str, daten) -> None:
    os.makedirs(ordner, exist_ok=True)
    tmp = os.path.join(ordner, 'info.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(daten, f)
    os.replace(tmp, os.path.join(ordner, 'info.json'))


def _teil_rechnen(auftrag):
    """Ein fehlender Teil im eigenen Prozess (``ego.parallel``): öffnet den Träger selbst. Rückgabe (Ergebnis,
    Meldungen)."""
    art, a = auftrag
    meldungen = []
    with Traeger(a['traeger'], os.path.join(a['cache'], 'arbeit', art)) as tr:
        if art == 'tex':
            erg = oberflaechen_texturen(tr, a['ordner'], asphalt=a['asphalt'], kunstrasen=a['kunst'], biom=a['biom'],
                                        farbton=a['farbton'], spielordner=a['spielordner'], meldungen=meldungen)
        elif art == 'aussen':
            erg = aussengelaende(tr, a['ordner'], a['versatz'], umgebung_daten=a['umgebung'],
                                 weltkarten=a['weltkarten'], biom=a['biom'], farbton=a['farbton'])
        else:
            inst = welt_instanzen(tr, a['logik'], versatz=a['versatz'], reste=a['reste'], boxen_korrektur=a['korr'],
                                  raster=a['raster'])
            erg = welt_netze(tr, inst, a['ordner'], a['versatz'], meldungen=meldungen)
    _info_schreiben(a['ordner'], erg)
    return erg, meldungen


def vorbereiten(traeger, szene_npz: str, cache: str, *, welt_json: Optional[str] = None, umgebung_daten=None,
                versatz=(0.0, 0.0, 0.0), bauen: Optional[dict] = None, spielordner=None,
                teile=('oberflaechen', 'aussen', 'welt'), prozesse: Optional[int] = None) -> dict:
    """Alle Teile für die 3D-Ansicht vorbereiten (zwischengespeichert je Träger und Eingaben). ``welt_json``: Logik
    (Ausgabeachsen) und aufgelöste Definition des Fahrlinienlaufs (``{"logik", "param"}``), ``bauen``: Baueinstellungen
    des Projekts (asphalt_vorlage, weltkarten, biom, farbton, traeger_reste, boxen_korrektur, raster).

    Ladezeit (3d_ladeperformance.md): Zwischenspeicher-Treffer ohne den Träger zu öffnen; fehlende Teile (Texturen,
    Außengelände, Welt) laufen gleichzeitig in Arbeitsprozessen (``ego.parallel``, ``EGO_PROZESSE=1`` = nacheinander)."""
    from ego import parallel
    bauen = bauen or {}
    meldungen = []
    stand = _datei_stand(traeger)
    daten = {}
    if welt_json and os.path.exists(welt_json):
        with open(welt_json, encoding='utf-8') as f:
            daten = json.load(f)
    param = daten.get('param') or {}
    biom, farbton = bauen.get('biom') or 'standard', bauen.get('farbton') or 'standard'
    weltkarten = bauen.get('weltkarten', True) is not False
    kunst = (param.get('kunstrasen') or {}).get('vorlage') or 'melbourne'
    gemeinsam = dict(traeger=os.fspath(traeger), cache=cache, versatz=tuple(float(x) for x in versatz), biom=biom,
                     farbton=farbton)
    erg = {'meldungen': meldungen}
    fertig, offen = {}, []
    if 'oberflaechen' in teile:
        ordner = os.path.join(cache, 'tex_' + _schluessel('tex', stand, bauen.get('asphalt_vorlage'), kunst, biom,
                                                           farbton, bool(spielordner)))
        fertig['tex'] = _info_lesen(ordner)
        if fertig['tex'] is None:
            offen.append(('tex', dict(gemeinsam, ordner=ordner, asphalt=bauen.get('asphalt_vorlage'), kunst=kunst,
                                      spielordner=spielordner)))
    if 'aussen' in teile:
        berge = ((umgebung_daten or {}).get('gelaende') or {}).get('berge')
        ordner = os.path.join(cache, 'aussen_' + _schluessel('aussen', stand, berge, list(versatz), weltkarten, biom,
                                                              farbton))
        fertig['aussen'] = _info_lesen(ordner)
        if fertig['aussen'] is None:
            offen.append(('aussen', dict(gemeinsam, ordner=ordner, umgebung=umgebung_daten, weltkarten=weltkarten)))
    if 'welt' in teile and daten.get('logik'):
        reste = bauen.get('traeger_reste', True) is not False
        korr = bauen.get('boxen_korrektur', True) is not False
        raster = int(bauen.get('raster') or 0)
        ordner = os.path.join(cache, 'welt_' + _schluessel('welt', stand, _datei_stand(welt_json), list(versatz),
                                                            reste, korr, raster))
        fertig['welt'] = _info_lesen(ordner)
        if fertig['welt'] is None:
            offen.append(('welt', dict(gemeinsam, ordner=ordner, logik=daten['logik'], reste=reste, korr=korr,
                                       raster=raster)))
    if offen:
        n = min(len(offen), parallel.anzahl(len(offen)))
        for (art, _a), (teil, mel) in zip(offen, parallel.abbilden(_teil_rechnen, offen, prozesse=n)):
            fertig[art] = teil
            meldungen.extend(mel)
    if 'oberflaechen' in teile:
        ordner = os.path.join(cache, 'uv_' + _schluessel('uv', _datei_stand(szene_npz), param.get('geometrie'),
                                                          param.get('randstein'), param.get('fahrbahn')))
        netze = _info_lesen(ordner)
        if netze is None:
            netze = oberflaechen_netze(szene_npz, param, ordner)
            _info_schreiben(ordner, netze)
        tex = fertig.get('tex') or {}
        erg['oberflaechen'] = {kl: dict(tex.get(kl) or {}, npz=p) for kl, p in netze.items()}
        erg['boden'] = tex.get('_boden')
    if fertig.get('aussen') is not None:
        erg['aussen'] = fertig['aussen']
    if fertig.get('welt') is not None:
        erg['welt'] = fertig['welt']
    if offen:
        _aufraeumen(cache)
    return erg


def _aufraeumen(cache: str, behalten: int = 6):
    """Zwischenspeicher klein halten: je Art die jüngsten ``behalten`` Ordner, ausgepackte Mip-Dateien weg."""
    import shutil
    try:
        eintraege = [e for e in os.scandir(cache) if e.is_dir() and e.name != 'arbeit']
    except OSError:
        return
    je_art = {}
    for e in eintraege:
        je_art.setdefault(e.name.split('_', 1)[0], []).append(e)
    for liste in je_art.values():
        liste.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        for e in liste[behalten:]:
            shutil.rmtree(e.path, ignore_errors=True)
    shutil.rmtree(os.path.join(cache, 'arbeit'), ignore_errors=True)       # ausgepackte Mip-Dateien
