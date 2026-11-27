"""Querprofil neben der Fahrbahn: Randsteine, Grünstreifen, Kiesbett, Barriere, Boxengasse.

Je Seite und Station gibt es sechs Stützabstände e0..e5 (Meter ab Fahrbahnrand, nach
außen) und fünf Bänder dazwischen:

    Band 0  [e0, e1]  Grünkeil zwischen Fahrbahn und Boxengasse (nur Boxenseite)
    Band 1  [e1, e2]  Boxengasse
    Band 2  [e2, e3]  Randstein
    Band 3  [e3, e4]  Grünstreifen
    Band 4  [e4, e5]  Auslauf: Kiesbett, Gras oder Asphalt; bei e5 steht die Barriere

Seitenraum-Abschnitte (``seitenraum.abschnitte``, 27.10.2026) überschreiben je Bereich und Seite Randsteinbreite,
Grünstreifenbreite, Auslauf-Material und Barrierenabstand; ohne Abschnitte rechnet alles wie vorher.
Wulstrandsteine (25.11.2026) setzen Abschnitte mit ``wulst_hoehe`` > 0: erhöhte Stücke hinter dem Randstein, eigene
Klasse ``wulst`` über dem Grünstreifen (``wulst_stuecke``).
Bänder ohne Breite fallen weg. Weil alle Abstände stetig in s verlaufen, ergibt sich ein
lückenloses Netz (siehe geometrie.py). Die Barriere wird durch den freien Platz neben
der Strecke begrenzt (andere Streckenteile, Innenseite enger Kurven).
"""

import math

import numpy as np

from .definition import OBERFLAECHE
from .mittellinie import freiraum, periodisch_mittel, periodisch_minimum

SEITEN = (("links", 1.0), ("rechts", -1.0))


def _glatt01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _maske_bereich(n, a, e):
    """Maske für Indizes a..e (einschließlich, periodisch, a/e beliebig ganzzahlig)."""
    m = np.zeros(n, dtype=bool)
    if e - a + 1 >= n:
        m[:] = True
    else:
        m[np.arange(a, e + 1) % n] = True
    return m


class Querprofil:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def an(self, sq, laenge):
        """Profil an beliebigen Bogenlängen: Abstände linear, Bänder nach nächster Station."""
        sq = np.mod(np.asarray(sq, dtype=float), laenge)
        s_ext = np.concatenate([self.s, [laenge]])
        erg = {}
        for name, _ in SEITEN:
            e = self.seiten[name]["e"]
            e_ext = np.vstack([e, e[:1]])
            ei = np.column_stack([np.interp(sq, s_ext, e_ext[:, k]) for k in range(e.shape[1])])
            idx = np.clip(np.floor(sq / self.ds).astype(int), 0, len(self.s) - 1)
            erg[name] = {"e": ei, "band": self.seiten[name]["band"][idx]}
            teile = self.seiten[name].get("teile")
            if teile is not None:                    # Randsteinarten: Breiten/Höhen linear, Klasse nach Station
                t_ext = np.vstack([teile, teile[:1]])
                erg[name]["teile"] = np.column_stack([np.interp(sq, s_ext, t_ext[:, k]) for k in range(teile.shape[1])])
                erg[name]["rs_klasse"] = self.seiten[name]["rs_klasse"][idx]
        mauer = np.interp(sq, s_ext, np.concatenate([self.boxenmauer, self.boxenmauer[:1]]))
        return erg, mauer


def _abschnitte(ml, param):
    """Seitenraum-Abschnitte je Seite: [(Maske über die Stationen, Eintrag), …]."""
    st, L = ml.ref, ml.laenge
    s_e = np.mod(st.s + getattr(ml, "s_start", 0.0), L)
    erg = {name: [] for name, _ in SEITEN}
    for ab in (param.get("seitenraum") or {}).get("abschnitte") or []:
        von, bis = float(ab["von_s"]) % L, float(ab["bis_s"]) % L
        maske = (s_e >= von) & (s_e <= bis) if von <= bis else (s_e >= von) | (s_e <= bis)
        seite = ab.get("seite", "beide")
        for name, _ in SEITEN:
            if seite in ("beide", name):
                erg[name].append((maske, ab))
    return erg


def _ueberschreiben(werte, abschnitte, feld):
    """Kopie von ``werte`` mit den Feldwerten der Abschnitte (späterer Abschnitt gewinnt); None = unverändert."""
    neu, gesetzt = np.array(werte, dtype=float), False
    for maske, ab in abschnitte:
        if ab.get(feld) is not None:
            neu[maske] = float(ab[feld])
            gesetzt = True
    return neu if gesetzt else None


def boxen_spurversatz(pbox):
    """Versatz der Boxenlinie von der Gassenmitte Richtung Strecke (m), begrenzt, damit die Spur in der Gasse bleibt."""
    breite = float(pbox.get("breite", 12.0) or 12.0)
    return max(0.0, min(float(pbox.get("spur_versatz_m", 0.0)), breite / 2 - float(pbox.get("spur_halbbreite_m", 2.5))))


#: Boxen, die der Träger mindestens belegt (Portimão-Mod 1.0.0/1.0.2: 11 Teams in Box 1–11; Box 12 = MyTeam, für sie
#: hält ``garagen_u`` eine Garage mehr frei). Fehlende Boxen rechnet ``spiel_welt.plaetze_berechnen`` geradlinig hinter
#: der letzten weiter; mehr als eine läge hinter dem Garagengebäude im Gras (PH-01, 17.11.2026,
#: ``ego-werkzeuge/ergebnisse/ph01_pitgarage23.md``)
BOXEN_MIN = 11


def boxen_anzahl(pbox):
    """Wirksame Boxenzahl: Eingabe ``boxen``, mindestens ``BOXEN_MIN``; 0 oder fehlend bleibt 0 (keine Boxen)."""
    n = int(pbox.get("boxen") or 0)
    return max(n, BOXEN_MIN) if n > 0 else 0


def boxen_u(pbox, U):
    """Lage der Boxen längs der Boxengasse (m ab Einfahrtsbeginn): mittig im vollbreiten Abschnitt."""
    a = pbox["anlauf_m"] + pbox["uebergang_m"]
    b = U - a
    n = boxen_anzahl(pbox)
    span = (n - 1) * pbox["box_abstand_m"]
    if span > b - a:
        raise ValueError("Pit boxes do not fit into the full-width section")
    return (a + b) / 2 + (np.arange(n) - (n - 1) / 2) * pbox["box_abstand_m"]


def garagen_u(pbox, U):
    """(Anfang, Ende) des Garagengebäudes längs der Boxengasse oder None (kein Gebäude)."""
    if not pbox.get("gebaeude", True):
        return None
    us = boxen_u(pbox, U)
    # eine Garage mehr am Ende: der Träger braucht Box 12 (MyTeam), spiel_welt setzt sie hinter die letzte Box
    ende = min(us[-1] + 1.5 * pbox["box_abstand_m"], U - pbox["anlauf_m"] - pbox["uebergang_m"])
    return float(us[0] - pbox["box_abstand_m"] / 2), float(ende)


#: Boxengasse höchstens bis hierher in überhöhte Kurven und höchstens dieser Anteil der Runde (Issue #5, Bristol:
#: Oval 1.668 m, Gasse ±400 m reichte in beide 17°-Steilkurven und über 84 % der Runde; Spiel hing beim Start)
BOX_MAX_QUERNEIGUNG_GRAD = 4.0
BOX_ABSTAND_STEILKURVE_M = 20.0
BOX_MAX_ANTEIL_RUNDE = 0.45
#: Untergrenzen, auf die Anlauf, Übergang und Grünstreifen bei zu kurzer Gasse schrumpfen (10.11.2026, Möglichkeit A/B
#: in docs/boxengasse_mindestlaenge.md): Monaco hat etwa 300 m Boxengasse
BOX_MIN_ANLAUF_M = 25.0
BOX_MIN_UEBERGANG_M = 40.0
BOX_MIN_ABSTAND_FAHRBAHN_M = 10.0


def _boxen_spanne(pbox):
    """Länge der Boxenreihe samt MyTeam-Garage und je einer Box Luft (``garagen_u``, spiel_welt)."""
    n = boxen_anzahl(pbox)
    return (n + 2) * pbox.get("box_abstand_m", 0.0) if n else 0.0


def boxen_mindestlaenge(pbox, verkuerzt=True):
    """Kürzeste Gasse (m): mit ``verkuerzt`` bei den Untergrenzen von Anlauf und Übergang, sonst mit den Eingaben."""
    an, ue = float(pbox.get("anlauf_m", 0.0)), float(pbox.get("uebergang_m", 0.0))
    if verkuerzt:
        an, ue = min(an, BOX_MIN_ANLAUF_M), min(ue, BOX_MIN_UEBERGANG_M)
    return max(2 * (an + ue) + 20, 2 * (an + ue) + _boxen_spanne(pbox) + 10)


def boxen_uebergaenge(pbox, U):
    """Anlauf, Übergang und Grünstreifen für eine Gasse der Länge ``U``.

    Reicht ``U`` für die Eingaben, bleiben sie. Sonst schrumpfen Anlauf und Übergang anteilig bis auf
    ``BOX_MIN_ANLAUF_M``/``BOX_MIN_UEBERGANG_M`` (A) und der Grünstreifen im selben Verhältnis wie der Übergang, nicht
    unter ``BOX_MIN_ABSTAND_FAHRBAHN_M`` (B: gleicher Winkel beim seitlichen Abwandern).
    Rückgabe {"anlauf_m", "uebergang_m", "abstand_fahrbahn", "verkuerzt"}."""
    an, ue = float(pbox["anlauf_m"]), float(pbox["uebergang_m"])
    abstand = float(pbox.get("abstand_fahrbahn", 0.0))
    aus = {"anlauf_m": an, "uebergang_m": ue, "abstand_fahrbahn": abstand, "verkuerzt": False}
    rest = U - (_boxen_spanne(pbox) + 10 if pbox.get("boxen") else 20)
    platz = rest / 2                                   # je Ende für Anlauf + Übergang
    if platz >= an + ue:
        return aus
    an_neu = max(min(an, BOX_MIN_ANLAUF_M), an * platz / (an + ue))
    ue_neu = max(min(ue, BOX_MIN_UEBERGANG_M), platz - an_neu)
    abstand_neu = max(min(abstand, BOX_MIN_ABSTAND_FAHRBAHN_M), abstand * ue_neu / ue) if ue > 0 else abstand
    return {"anlauf_m": round(an_neu, 1), "uebergang_m": round(ue_neu, 1), "abstand_fahrbahn": round(abstand_neu, 1),
            "verkuerzt": True}


def boxen_begrenzen(pbox, s, bank, L):
    """Einfahrt vor und Ausfahrt nach der Startlinie (m), auf die Gerade begrenzt.

    Kürzt beide Seiten so, dass die Gasse ``BOX_ABSTAND_STEILKURVE_M`` vor der ersten Station mit mehr als
    ``BOX_MAX_QUERNEIGUNG_GRAD`` Querneigung endet und zusammen höchstens ``BOX_MAX_ANTEIL_RUNDE`` der Runde lang ist.
    Nie kürzer als ``boxen_mindestlaenge`` (Anlauf und Übergang bei ihren Untergrenzen, alle Boxen); reicht auch das
    nicht, bleibt es bei den Eingaben (dann meldet die Prüfung den Konflikt). Kürzere Übergänge setzt danach
    ``boxen_uebergaenge``. Rückgabe ``(vor, nach, grund)``, ``grund`` None = unverändert."""
    vor0, nach0 = float(pbox["einfahrt_vor_start_m"]), float(pbox["ausfahrt_nach_start_m"])
    vor, nach, gruende = vor0, nach0, []
    s = np.mod(np.asarray(s, float), L)
    steil = np.abs(np.asarray(bank, float)) > math.radians(BOX_MAX_QUERNEIGUNG_GRAD)
    if steil.any():
        rueck = float(np.min(np.mod(L - s[steil], L))) - BOX_ABSTAND_STEILKURVE_M
        vorw = float(np.min(s[steil])) - BOX_ABSTAND_STEILKURVE_M
        if rueck < vor or vorw < nach:
            vor, nach = min(vor, max(rueck, 0.0)), min(nach, max(vorw, 0.0))
            gruende.append("banking")
    grenze = BOX_MAX_ANTEIL_RUNDE * L
    if vor + nach > grenze:
        f = grenze / (vor + nach)
        vor, nach = vor * f, nach * f
        gruende.append("lap share")
    if not gruende:
        return vor0, nach0, None
    # nie kürzer, als Anlauf, Übergänge (bei ihren Untergrenzen) und alle Boxen brauchen: fehlende Meter verteilen
    minimum = boxen_mindestlaenge(pbox)
    if vor + nach < minimum:
        fehlt = minimum - (vor + nach)
        dazu = min(fehlt / 2, vor0 - vor)
        vor += dazu
        nach = min(nach0, nach + fehlt - dazu)
        vor = min(vor0, vor + max(0.0, minimum - vor - nach))
        if vor + nach < minimum - 1e-6:
            return vor0, nach0, None
        gruende.append("room for the pit boxes")
    return round(vor, 1), round(nach, 1), ", ".join(gruende)


def querprofil_berechnen(ml, param, kurven):
    st = ml.ref
    n = st.anzahl
    ds = ml.ds
    L = ml.laenge
    warnungen = []
    pr, pg, pk, pb, pbox = (param["randstein"], param["gruenstreifen"], param["kiesbett"],
                           param["barriere"], param["boxengasse"])

    # ---- freier Platz je Seite
    fl, fr, gl, gr = freiraum(ml, max_abstand=2 * (pb["abstand"] + 40.0))
    frei = {}
    for (name, vz), D, wg in ((SEITEN[0], fl, gl), (SEITEN[1], fr, gr)):
        w = st.wl if vz > 0 else st.wr
        e = np.where(np.isfinite(D), (D - w - wg) / 2.0 - pb["luecke_m"] / 2.0, np.inf)
        innen = vz * st.kappa > 0
        with np.errstate(divide="ignore"):
            grenze = np.where(innen, 0.85 / np.abs(st.kappa) - w, np.inf)
        e = np.minimum(e, grenze)
        frei[name] = e

    # ---- Randsteine und Kiesbetten aus den Kurven
    kerb = {name: np.zeros(n) for name, _ in SEITEN}
    kies = {name: np.zeros(n, dtype=bool) for name, _ in SEITEN}
    vor, nach, anach = (int(round(pr[k] / ds)) for k in ("vorlauf_m", "nachlauf_m", "aussen_nachlauf_m"))
    for kv in kurven:
        a, e, sch = kv["i_anfang"], kv["i_ende"], kv["i_scheitel"]
        if e < a:
            e += n
        if sch < a:
            sch += n
        kmax = 1.0 / kv["min_radius_m"]
        innen = "links" if kv["richtung"] == "links" else "rechts"
        aussen = "rechts" if innen == "links" else "links"
        if kmax > pr["kruemmung_schwelle"]:
            kerb[innen] = np.maximum(kerb[innen], _maske_bereich(n, a - vor, e + nach))
            kerb[aussen] = np.maximum(kerb[aussen], _maske_bereich(n, sch, e + anach))
        if pk.get("aktiv", True) and kmax > pk["kruemmung_schwelle"]:
            kies[aussen] |= _maske_bereich(n, sch - int(round(pk["vorlauf_m"] / ds)),
                                           e + int(round(pk["nachlauf_m"] / ds)))
    rampe = max(1, int(round(pr["rampe_m"] / ds)))
    abschnitte = _abschnitte(ml, param)
    for name in kerb:
        eigene = _ueberschreiben(kerb[name] * pr["breite"], abschnitte[name], "randstein_breite")
        if eigene is None:
            kerb[name] = pr["breite"] * np.clip(periodisch_mittel(kerb[name], rampe), 0, 1)
        else:                                   # eigene Randsteinbreite (0 = keiner), Übergang wie die Automatik
            kerb[name] = np.maximum(periodisch_mittel(eigene, rampe), 0)
        for maske, ab in abschnitte[name]:
            if ab.get("auslauf") is not None:
                kies[name][maske] = ab["auslauf"] == "kies"
    asphalt = {name: np.zeros(n, dtype=bool) for name, _ in SEITEN}
    kunstrasen = {name: np.zeros(n, dtype=bool) for name, _ in SEITEN}
    for name in asphalt:
        for maske, ab in abschnitte[name]:
            if ab.get("auslauf") is not None:
                asphalt[name][maske] = ab["auslauf"] == "asphalt"
                kunstrasen[name][maske] = ab["auslauf"] == "kunstrasen"

    # ---- Boxengasse (Lage relativ zur Start-Ziel-Linie s = 0)
    keil = {name: np.zeros(n) for name, _ in SEITEN}
    boxb = {name: np.zeros(n) for name, _ in SEITEN}
    box_bereich = np.zeros(n, dtype=bool)
    garage = np.zeros(n, dtype=bool)          # Stationen neben dem Garagengebäude
    garage_gewicht = np.zeros(n)              # 1 am Gebäude, über 20 m auf 0 (Barriere dahinter)
    box_info = None
    if pbox.get("aktiv"):
        seite = pbox["seite"]
        vor, nach, _grund = boxen_begrenzen(pbox, st.s, ml.bank, L)
        s_ein = (L - vor) % L
        U = vor + nach
        if not 0 < U < L:
            raise ValueError("Pit lane must be shorter than one lap")
        uebergaenge = boxen_uebergaenge(pbox, U)
        if uebergaenge["verkuerzt"]:
            # kurze Gasse: kürzere Übergänge und schmalerer Grünstreifen (A/B); in die Parameter zurückschreiben, damit
            # Geometrie, Logik und Boxenplätze dieselben Werte nehmen
            for key in ("anlauf_m", "uebergang_m", "abstand_fahrbahn"):
                pbox[key] = uebergaenge[key]
        an, ue = pbox["anlauf_m"], pbox["uebergang_m"]
        if U < 2 * (an + ue) + 20:
            raise ValueError("Pit lane too short: entry and exit (%.0f m) need at least %.0f m"
                             % (U, boxen_mindestlaenge(pbox)))
        u = np.mod(st.s - s_ein, L)
        box_bereich = u < U
        v = U - u                                      # Restweg bis zum Ende
        breite = pbox["breite"] * np.where(box_bereich, np.minimum(_glatt01(u / an), _glatt01(v / an)), 0)
        k_ein = _glatt01((u - an) / ue)
        k_aus = _glatt01((v - an) / ue)
        keil[seite] = np.where(box_bereich, pbox["abstand_fahrbahn"] * np.minimum(k_ein, k_aus), 0)
        boxb[seite] = breite
        kerb[seite] = np.where(box_bereich, 0.0, kerb[seite])
        kies[seite] &= ~box_bereich
        asphalt[seite] &= ~box_bereich
        kunstrasen[seite] &= ~box_bereich
        gu = garagen_u(pbox, U)
        if gu is not None:
            garage = box_bereich & (u >= gu[0]) & (u <= gu[1])
            ausserhalb = np.maximum(gu[0] - u, u - gu[1])
            garage_gewicht = np.where(box_bereich, np.clip(1 - ausserhalb / 20.0, 0, 1), 0)
        box_info = {"seite": seite, "s_einfahrt": float(s_ein), "laenge_entlang_strecke": float(U), "begrenzt": _grund, "einfahrt_vor_start_m": vor, "ausfahrt_nach_start_m": nach,
                    "uebergaenge_verkuerzt": uebergaenge["verkuerzt"],
                    "u": u, "bereich": box_bereich, "garagen_u": gu}

    # ---- Randwinkel: Höhenänderung je Meter nach außen, je Seite; ohne Eintrag bleibt der Rand waagerecht
    rand = {}
    for name, vz in SEITEN:
        werte = np.zeros(n)
        for maske, ab in abschnitte[name]:
            w = ab.get("rand_neigung")
            if w == "strecke":
                werte[maske] = (vz * np.tan(ml.bank))[maske]     # gleiche Ebene wie die Fahrbahn
            elif w is not None:
                werte[maske] = math.tan(math.radians(float(w)))
        if np.any(werte):
            rand[name] = periodisch_mittel(werte, rampe)
    if rand:
        ml.rand_setzen(rand.get("links", np.zeros(n)), rand.get("rechts", np.zeros(n)),
                       float((param.get("seitenraum") or {}).get("rand_breite_m", 12.0)))

    # ---- Abstände und Bänder
    seiten = {}
    for name, vz in SEITEN:
        e_bar = np.full(n, float(pb["abstand"]))
        eigene = _ueberschreiben(e_bar, abschnitte[name], "barriere_abstand")
        if eigene is not None:
            e_bar = eigene
        verfuegbar = frei[name]
        eng = verfuegbar < pb["mindestabstand"]
        if np.any(eng):
            warnungen.append("%s: less than %.1f m of space to the next part of the track at %d "
                             "stations (barrier set to minimum distance)"
                             % (name, pb["mindestabstand"], int(eng.sum())))
        e_bar = np.minimum(e_bar, np.maximum(verfuegbar, pb["mindestabstand"]))
        h = int(round(15.0 / ds))
        e_bar = periodisch_mittel(periodisch_minimum(e_bar, h), h)
        e1 = keil[name]
        e2 = e1 + boxb[name]
        if pbox.get("aktiv") and name == pbox["seite"]:
            noetig = np.where(box_bereich, e2 + pbox["hinter_box_m"]
                              + garage_gewicht * pbox.get("gebaeude_tiefe_m", 0.0), 0)
            if np.any(noetig > verfuegbar + 1e-6):
                warnungen.append("Pit lane: not enough space beside the track at %d stations (may overlap another part "
                                 "of the track)"
                                 % int(np.sum(noetig > verfuegbar + 1e-6)))
            e_bar = np.maximum(e_bar, noetig)
        k = np.minimum(kerb[name], np.maximum(e_bar - e2, 0))
        # Randsteinarten (25.11.2026): abgesenkter Randstein als äußerer Teil des Randsteinbands, Profilhöhe je Station
        rs_param = param["randstein"]
        tief_b = _werte(n, rs_param.get("tief_breite_m", 0.0), abschnitte[name], "tief_breite")
        tief_t = _werte(n, rs_param.get("tief_m", 0.05), abschnitte[name], "tief_tiefe")
        hoehe = _werte(n, rs_param.get("profil_hoehe_m", 0.0), abschnitte[name], "randstein_hoehe")
        if pbox.get("aktiv") and name == pbox["seite"]:
            tief_b = np.where(box_bereich, 0.0, tief_b)
        tief_b = periodisch_mittel(tief_b, rampe)
        tief_b = np.minimum(tief_b, np.maximum(e_bar - e2 - k, 0))
        # Reste der Glättung unter 5 mm weg: sonst hauchdünne Teilstreifen, deren Zwischenpunkte beim Verschweißen
        # zusammenfallen (nichtmannigfaltige Kanten)
        k = np.where(k < 0.005, 0.0, k)
        tief_b = np.where(tief_b < 0.005, 0.0, tief_b)
        e3 = e2 + k + tief_b
        gruen = _ueberschreiben(np.full(n, float(pg["breite"])), abschnitte[name], "gruenstreifen_breite")
        if gruen is not None:
            gruen = periodisch_mittel(gruen, rampe)
        g = np.minimum(pg["breite"] if gruen is None else gruen, np.maximum(e_bar - e3, 0))
        boden = garage if (pbox.get("aktiv") and name == pbox["seite"]) else np.zeros(n, dtype=bool)
        if boden.any():                         # Garagenboden hinter der Boxengasse (Boxenasphalt)
            g = np.where(boden, pbox["gebaeude_tiefe_m"], g)
        e4 = e3 + g
        e5 = np.maximum(e_bar, e4)
        e = np.column_stack([np.zeros(n), e1, e2, e3, e4, e5])
        band = np.zeros((n, 5), dtype=np.int16)
        band[:, 0] = OBERFLAECHE["gras"]
        band[:, 1] = OBERFLAECHE["boxengasse"]
        band[:, 2] = OBERFLAECHE["randstein"]
        band[:, 3] = np.where(boden, OBERFLAECHE["boxengasse"], OBERFLAECHE["gras"])
        band[:, 4] = np.where(asphalt[name], OBERFLAECHE["auslauf"],
                              np.where(kies[name], OBERFLAECHE["kies"], OBERFLAECHE["gras"]))
        band[:, 4] = np.where(kunstrasen[name], OBERFLAECHE["kunstrasen"], band[:, 4])
        rs_klasse = _randstein_klassen(k, ds, rs_param)
        band[:, 2] = rs_klasse
        seiten[name] = {"e": e, "band": band, "frei": verfuegbar, "kies": kies[name],
                        "teile": np.column_stack([k, tief_b, hoehe, tief_t]), "rs_klasse": rs_klasse,
                        "randstein": k, "wulst": _wulst_werte(param, abschnitte[name], n,
                                                              box_bereich if (pbox.get("aktiv") and name == pbox["seite"])
                                                              else None)}

    mauer = np.full(n, np.nan)
    if pbox.get("aktiv"):
        e1 = seiten[pbox["seite"]]["e"][:, 1]
        abst = e1 - pbox["mauer_abstand_m"]
        mauer = np.where(box_bereich & (abst > pg["breite"] * 0.5 + 1.0), abst, np.nan)
    qp = Querprofil(s=st.s, ds=ds, seiten=seiten, boxenmauer=mauer, box=box_info, warnungen=warnungen)
    qp.wulst = wulst_stuecke(qp, param, L)
    weg = sum(1 for w in qp.wulst if w.get("kein_platz"))
    if weg:
        warnungen.append("Sausage kerbs: %d pieces left out (no room before the barrier)" % weg)
    qp.wulst = [w for w in qp.wulst if not w.get("kein_platz")]
    return qp


# ------------------------------------------------------------------ Randsteinarten (25.11.2026, docs/kerbtypen.md)
#: Länge der Anfangs- und Endstücke (eine Musterlänge des Randsteins, Portimão 3,4 m)
RANDSTEIN_ENDE_M = 3.4


def _werte(n, standard, abschnitte, feld):
    """Je Station der Wert aus den Seitenraum-Abschnitten (späterer gewinnt), sonst ``standard``."""
    werte = np.full(n, float(standard or 0.0))
    for maske, ab in abschnitte:
        if ab.get(feld) is not None:
            werte[maske] = float(ab[feld])
    return werte


def _randstein_klassen(k, ds, rs_param):
    """Oberflächenklasse des Randsteins je Station: Anfangs- und Endstück an den Enden jedes Randsteins (weiß,
    abgerundet), dazwischen das rot-weiße Mittelstück. Ohne ``enden`` überall das Mittelstück."""
    from .mittellinie import laeufe
    n = len(k)
    klasse = np.full(n, OBERFLAECHE["randstein"], np.int16)
    if not rs_param.get("enden", True):
        return klasse
    laenge = max(1, int(round(RANDSTEIN_ENDE_M / ds)))
    for a, e in laeufe(k > 0.05):
        if e - a >= n:                    # Randstein rundum: keine Enden
            continue
        stueck = min(laenge, max(1, (e - a) // 2))
        klasse[np.arange(a, a + stueck) % n] = OBERFLAECHE["randstein_anfang"]
        klasse[np.arange(e - stueck, e) % n] = OBERFLAECHE["randstein_ende"]
    return klasse


# ------------------------------------------------------------------ Wulstrandsteine (Sausage Kerbs)
def _wulst_werte(param, abschnitte, n, box_bereich):
    """Je Station Höhe (0 = keiner), Breite und Abstand hinter dem Randstein aus den Seitenraum-Abschnitten."""
    pw = param.get("wulst") or {}
    hoehe = np.zeros(n)
    breite = np.full(n, float(pw.get("breite_m", 0.6)))
    abstand = np.full(n, float(pw.get("abstand_m", 0.1)))
    stueck = np.full(n, float(pw.get("stueck_m", 4.0)))
    luecke = np.full(n, float(pw.get("luecke_m", 0.0)))
    for maske, ab in abschnitte:
        for feld, ziel in (("wulst_hoehe", hoehe), ("wulst_breite", breite), ("wulst_abstand", abstand),
                           ("wulst_stueck", stueck), ("wulst_luecke", luecke)):
            if ab.get(feld) is not None:
                ziel[maske] = float(ab[feld])
    if box_bereich is not None:
        hoehe[box_bereich] = 0.0
    return {"hoehe": hoehe, "breite": breite, "abstand": abstand, "stueck": stueck, "luecke": luecke}


def wulst_stuecke(qp, param, laenge):
    """Stücke der Wulstrandsteine: je zusammenhängendem Bereich mit Höhe > 0 Stücke von ``stueck_m`` mit
    ``luecke_m`` dazwischen (je Abschnitt ``wulst_stueck``/``wulst_luecke``). Rückgabe [{"seite", "s0", "s1",
    "innen_m" (ab Fahrbahnrand), "breite_m", "hoehe_m", "rampe_anfang", "rampe_ende"}]. Ohne Lücke stoßen die Stücke
    aneinander und laufen nur am Anfang und Ende des Bereichs schräg aus (durchgehender Wulst, Standard seit
    25.11.2026). Stücke ohne Platz vor der Barriere sind mit ``kein_platz`` markiert."""
    from .mittellinie import laeufe
    n, ds = len(qp.s), qp.ds
    erg = []
    for name, _ in SEITEN:
        seite = qp.seiten[name]
        w = seite["wulst"]
        for a, e in laeufe(w["hoehe"] > 1e-6):
            s_a, s_e = a * ds, e * ds
            s0 = s_a
            while s0 < s_e - 0.5:
                j = int(round(s0 / ds)) % n
                stueck = max(float(w["stueck"][j]), 0.5)
                luecke = max(float(w["luecke"][j]), 0.0)
                s1 = min(s0 + stueck, s_e)
                if s_e - s1 < 0.5:                      # kein Reststück unter 0,5 m
                    s1 = s_e
                i = int(round((s0 + s1) / 2 / ds)) % n
                innen = float(seite["e"][i, 3] + w["abstand"][i])
                breite, hoehe = float(w["breite"][i]), float(w["hoehe"][i])
                stueck_info = {"seite": name, "s0": float(s0), "s1": float(s1), "innen_m": innen, "breite_m": breite,
                               "hoehe_m": hoehe, "rampe_anfang": luecke > 0 or s0 <= s_a + 1e-9,
                               "rampe_ende": luecke > 0 or s1 >= s_e - 1e-9}
                if innen + breite > float(seite["e"][i, 5]) - 0.3 or innen < 0:
                    stueck_info["kein_platz"] = True
                erg.append(stueck_info)
                s0 = s1 + luecke
    return erg


def wulst_profil(t, hoehe):
    """Querprofil eines Wulsts: gerundet (sin^0,7), 0 an beiden Kanten; ``t`` 0…1 über die Breite."""
    return hoehe * np.sin(np.pi * np.clip(t, 0.0, 1.0)) ** 0.7
