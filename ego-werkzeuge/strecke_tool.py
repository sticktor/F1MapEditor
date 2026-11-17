"""Kommandozeile für den formatunabhängigen Streckengenerator (AP6) und eigene 3D-Modelle (M1).

    python strecke_tool.py erzeugen DEFINITION.json AUSGABEORDNER
    python strecke_tool.py vorschau DEFINITION.json AUSGABEORDNER
    python strecke_tool.py modell info MODELL.glb [LOD1.glb …] [--json] [--vorschau BILD.png]
    python strecke_tool.py modell pruefen MODELL.glb [LOD1.glb …] [--traeger PAKET.zip|ERP] [--json]
    python strecke_tool.py modell pruefen --projekt PROJEKT [--name NAME] [--traeger …]
    python strecke_tool.py modell hinzufuegen PROJEKT MODELL.glb [--lod LOD1.glb …] [--name NAME] [Optionen]
    python strecke_tool.py modell liste PROJEKT [--json]
    python strecke_tool.py modell entfernen PROJEKT NAME

``modell``: eigene Modelle aus Blender (glTF 2.0 ``.glb``, auch ``.gltf``/``.obj``), siehe docs/eigene_modelle.md.
``pruefen`` mit Träger setzt das Modell vollständig um und prüft die Ressourcen in einem ERP im Speicher; es wird
nichts geschrieben und nie in den Spielordner.
"""
import argparse
import json
import sys


def _strecke(args):
    from strecke.erzeugung import erzeugen
    try:
        erg = erzeugen(args.definition,args.ausgabe,nur_vorschau=args.befehl=='vorschau')
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        print('Fehler: %s' % exc,file=sys.stderr)
        return 1
    b = erg['bericht']
    print('%s: %.1f m, %d Kurven, Runde %s' % (b['name'],b['laenge_m'],b['kurven'],b['rundenzeit']))
    print('%d Dreiecke; Berechnung %.2f s; Ausgabe: %s' % (b['dreiecke'],b['berechnung_s'],args.ausgabe))
    for warnung in b['warnungen']:
        print('Warnung: %s' % warnung)
    return 0


# =============================================================================== eigene Modelle
def _statistik_zeigen(titel, s):
    print(titel)
    for text in s.get('fehler', []):
        print('  Fehler:   %s' % text)
    if 'dreiecke' in s:
        print('  Größe:    %.2f × %.2f × %.2f m (x, y, z), Unterkante %.2f m, Radius %.2f m' % (
            s['groesse_m'][0], s['groesse_m'][1], s['groesse_m'][2], s['unterkante_m'], s['radius_m']))
        print('  Netz:     %s Dreiecke (je LOD), %d Vertices, Teilnetze %s, LOD-Abstände %s' % (
            '/'.join(str(x) for x in s['dreiecke']), s['vertices'], s['teilnetze'], s['lod_abstaende']))
        for m in s['materialien']:
            bild = '%d×%d' % tuple(m['bild']) if m['bild'] else 'ohne Bild'
            normal = ', Normalenkarte %d×%d' % tuple(m['normalenkarte']) if m['normalenkarte'] else ''
            print('  Material: %s, Farbe %s, %s%s' % (m['name'], m['basisfarbe'][:3], bild, normal))
        print('  Speicher: Geometrie %.1f kB, Texturen etwa %.1f kB' % (s['geometrie_bytes'] / 1e3, s['textur_bytes'] / 1e3))
    for text in s.get('warnungen', []):
        print('  Warnung:  %s' % text)
    for text in s.get('hinweise', []):
        print('  Hinweis:  %s' % text)


def _voll_pruefen(projekt, eintraege, traeger, als_json):
    """Vollständige Umsetzung je Modell in einem leeren ERP im Speicher, Verweise dürfen im Träger liegen."""
    from ego import erp, modell_import as M
    from strecke import eigene_modelle as E, umgebung
    traeger = traeger if traeger != 'standard' else umgebung.standard_traeger()
    if not traeger:
        print('Fehler: kein Träger (--traeger PAKET.zip oder ERP)', file=sys.stderr)
        return 1, []
    quelle = umgebung.traeger_oeffnen(traeger)
    ergebnisse, fehler = [], 0
    try:
        _, vorlage = E.vorlage_material(quelle)
        for e in eintraege:
            um = E.umsetzen(projekt, e, vorlage, cache=None)
            archiv = erp.ErpFile()
            M.in_archiv(archiv, um.ressourcen)
            faelle = M.pruefen(archiv, um.bau, quelle=quelle)
            schlecht = [f for f in faelle if not f[1]]
            fehler += len(schlecht)
            ergebnisse.append({'name': e['name'], 'bericht': um.bericht, 'hinweise': um.hinweise,
                               'pruefungen': [{'name': n, 'ok': ok, 'einzelheit': t} for n, ok, t in faelle]})
            if not als_json:
                print('%s: %d Ressourcen, %.1f kB, %d Prüfungen, %d Fehler' % (
                    um.bericht['name'], um.bericht['ressourcen'], um.bericht['bytes'] / 1e3, len(faelle), len(schlecht)))
                for n, ok, t in faelle:
                    if not ok:
                        print('  FEHLER  %s %s' % (n, t))
                for h in um.hinweise:
                    print('  Hinweis: %s' % h)
    finally:
        quelle.close()
    return (1 if fehler else 0), ergebnisse


def _modell(args):
    import os
    import shutil
    import tempfile
    from ego import modell_import as M
    from strecke import eigene_modelle as E
    try:
        if args.aktion == 'info':
            s = E.untersuchen(args.dateien)
            if args.vorschau and not s['fehler']:
                E.vorschau(args.dateien, args.vorschau)
            if args.json:
                print(json.dumps(s, ensure_ascii=False, indent=1))
            else:
                _statistik_zeigen(' + '.join(os.path.basename(d) for d in args.dateien), s)
            return 1 if s['fehler'] else 0
        if args.aktion == 'pruefen':
            if bool(args.projekt) == bool(args.dateien):
                print('Fehler: entweder Modelldateien oder --projekt angeben', file=sys.stderr)
                return 1
            temp = None
            try:
                if args.projekt:
                    projekt = args.projekt
                    daten = E.aktualisieren(projekt)
                    eintraege = [E.eintrag(daten, E.modellname(args.name))] if args.name else daten['modelle']
                else:
                    temp = projekt = tempfile.mkdtemp(prefix='modell_pruefen_')
                    eintraege = [E.hinzufuegen(projekt, args.dateien[0], lods=args.dateien[1:])]
                aus, code = {'modelle': [{'name': e['name'], 'statistik': e['statistik']} for e in eintraege]}, 0
                if any(e['statistik'].get('fehler') for e in eintraege):
                    code = 1
                if not args.json:
                    for e in eintraege:
                        _statistik_zeigen('%s%s (%s)' % (E.PRAEFIX, e['name'], ', '.join(e['statistik']['dateien'])), e['statistik'])
                if args.traeger and code == 0:
                    code, aus['umsetzung'] = _voll_pruefen(projekt, eintraege, args.traeger, args.json)
                if args.json:
                    print(json.dumps(aus, ensure_ascii=False, indent=1))
                return code
            finally:
                if temp:
                    shutil.rmtree(temp, ignore_errors=True)
        if args.aktion == 'hinzufuegen':
            optionen = {k: getattr(args, k) for k in _MODELL_OPTIONEN if getattr(args, k) is not None}
            e = E.hinzufuegen(args.projekt, args.datei, name=args.name, lods=args.lod or (), ersetzen=args.ersetzen, **optionen)
            _statistik_zeigen('%s%s hinzugefügt (%s)' % (E.PRAEFIX, e['name'], E.tabelle_pfad(args.projekt)), e['statistik'])
            return 0
        if args.aktion == 'aendern':
            optionen = {k: getattr(args, k) for k in _MODELL_OPTIONEN if getattr(args, k) is not None}
            e = E.aendern(args.projekt, E.modellname(args.name), **optionen)
            print('%s%s geändert: %s' % (E.PRAEFIX, e['name'], ', '.join('%s=%s' % (k, e[k]) for k in sorted(optionen))))
            return 0
        if args.aktion == 'liste':
            daten = E.aktualisieren(args.projekt)
            if args.json:
                print(json.dumps({'modelle': daten['modelle'], 'katalog': E.katalogeintraege(args.projekt, auffrischen=False)},
                                 ensure_ascii=False, indent=1))
                return 0
            if not daten['modelle']:
                print('Keine eigenen Modelle in %s' % E.ordner(args.projekt))
            for e in daten['modelle']:
                s = e.get('statistik') or {}
                if s.get('fehler'):
                    print('%-24s FEHLER %s' % (E.PRAEFIX + e['name'], s['fehler'][0]))
                else:
                    print('%-24s %-10s %6d Dreiecke  %.1f × %.1f × %.1f m  vorne %3.0f°  %s%s' % (
                        E.PRAEFIX + e['name'], e['kategorie'], s['dreiecke'][0], s['groesse_m'][0], s['groesse_m'][1],
                        s['groesse_m'][2], e['vorne_grad'], ', '.join(s['dateien']),
                        '  Kollision: Quader' if e.get('kollision') == 'quader' else ''))
            return 0
        if args.aktion == 'entfernen':
            e = E.entfernen(args.projekt, E.modellname(args.name))
            print('%s%s entfernt' % (E.PRAEFIX, e['name']))
            return 0
    except (E.ModellFehler, M.ModellImportFehler, OSError) as exc:
        print('Fehler: %s' % exc, file=sys.stderr)
        return 1
    return 1


_MODELL_OPTIONEN = ('kategorie', 'vorne_grad', 'sichtweite_m', 'textur_max', 'textur_format', 'normalen', 'kollision')


def _modell_optionen(p):
    p.add_argument('--kategorie')
    p.add_argument('--vorne-grad', dest='vorne_grad', type=float, help='Vorderseite im Modellraum (Standard 270 = +z, Blender −Y)')
    p.add_argument('--sichtweite', dest='sichtweite_m', type=float)
    p.add_argument('--textur-max', dest='textur_max', type=int)
    p.add_argument('--textur-format', dest='textur_format', choices=('bc7', 'bc1'))
    p.add_argument('--normalen', choices=('gl', 'dx'), help='Konvention der Normalenkarten (Blender/glTF: gl)')
    p.add_argument('--kollision', choices=('keine', 'quader'),
                   help='M5b: Grundriss-Box als Mauerflächen (SCON) in der Fahrphysik; Standard keine')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mittellinie und Breiten in neutrale Streckendaten umwandeln; eigene 3D-Modelle prüfen.')
    sub = parser.add_subparsers(dest='befehl',required=True)
    for name, beschr in (('erzeugen','Geometrie, Logik und Vorschau erzeugen'),
                         ('vorschau','Nur SVG/PNG und Berechnungsbericht erzeugen')):
        p = sub.add_parser(name,help=beschr)
        p.add_argument('definition',metavar='DEFINITION.json')
        p.add_argument('ausgabe',metavar='AUSGABEORDNER')
    m = sub.add_parser('modell', help='eigene 3D-Modelle (glTF/OBJ) untersuchen, prüfen und im Projekt ablegen')
    ms = m.add_subparsers(dest='aktion', required=True)
    p = ms.add_parser('info', help='Kennzahlen und Hinweise zu einer Modelldatei')
    p.add_argument('dateien', nargs='+', metavar='MODELL', help='LOD 0, danach weitere LOD-Dateien')
    p.add_argument('--json', action='store_true')
    p.add_argument('--vorschau', metavar='BILD.png', help='Schrägbild des Modells schreiben')
    p = ms.add_parser('pruefen', help='Modell(e) untersuchen; mit --traeger vollständig umsetzen und offline prüfen')
    p.add_argument('dateien', nargs='*', metavar='MODELL')
    p.add_argument('--projekt', help='Projektordner: alle eigenen Modelle (oder --name)')
    p.add_argument('--name')
    p.add_argument('--traeger', nargs='?', const='standard', help='Träger-Paket oder -ERP für Vorlagenmaterial und Verweise '
                   '(ohne Wert: Portimão-Träger des Werkzeugkastens)')
    p.add_argument('--json', action='store_true')
    p = ms.add_parser('hinzufuegen', help='Modell in die Projektablage <projekt>/modelle kopieren und eintragen')
    p.add_argument('projekt')
    p.add_argument('datei', metavar='MODELL')
    p.add_argument('--lod', action='append', metavar='DATEI', help='weitere LOD-Datei (mehrfach, LOD 1 zuerst)')
    p.add_argument('--name')
    _modell_optionen(p)
    p.add_argument('--ersetzen', action='store_true')
    p = ms.add_parser('aendern', help='Einstellungen eines eigenen Modells ändern (z. B. --kollision quader)')
    p.add_argument('projekt')
    p.add_argument('name')
    _modell_optionen(p)
    p = ms.add_parser('liste', help='eigene Modelle des Projekts')
    p.add_argument('projekt')
    p.add_argument('--json', action='store_true')
    p = ms.add_parser('entfernen', help='Modell aus der Projektablage löschen')
    p.add_argument('projekt')
    p.add_argument('name')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    return _modell(args) if args.befehl == 'modell' else _strecke(args)


if __name__=='__main__':
    sys.exit(main())
