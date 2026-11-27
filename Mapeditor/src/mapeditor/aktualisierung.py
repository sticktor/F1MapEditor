"""Automatische Aktualisierung (26.11.2026): neue GitHub-Releases finden, herunterladen, prüfen, einspielen.

Ohne Qt (Dialoge in ``aktualisierung_ui``). Ablauf:

1. ``abfragen()`` liest ``releases/latest`` des öffentlichen Repositorys (kein Konto, keine persönlichen Daten; nur ein
   neutraler User-Agent). ``release_lesen()`` findet Version, Release Notes und die Assets
   ``Mapeditor-<version>-win64.zip`` und ``….zip.sha256``.
2. ``notizen_zwischen()`` schneidet aus den Release Notes (``## New in X``-Abschnitte) alles zwischen der laufenden und
   der neuen Version heraus; dieselbe Funktion liefert nach einem Update die Notizen für den „What's new“-Dialog aus
   der mitgelieferten ``RELEASE_NOTES.md``.
3. ``herunterladen()`` lädt das ZIP (Fortschritt, Abbruch), ``sha256_pruefen()`` vergleicht mit der Prüfsumme des
   Releases, ``entpacken()`` legt die neue Version neben die laufende Installation.
4. ``austausch_skript()`` schreibt ein kleines CMD-Skript, das nach dem Beenden des Editors den Programmordner
   tauscht und die neue Version startet. Klappt der Tausch nicht (Ordner gesperrt), startet es die neue Version aus
   ihrem Ablageordner. Nutzerdaten liegen nicht im Programmordner (Träger, Einstellungen, Projekte).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

from . import __version__

REPO = "sticktor/F1MapEditor"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
#: neutraler Kennzeichner für GitHub (keine Nutzerdaten)
USER_AGENT = "Mapeditor-Updater"
ZIP_MUSTER = re.compile(r"^Mapeditor-(?P<version>[0-9][0-9A-Za-z.\-]*)-win64\.zip$")
ABSCHNITT = re.compile(r"^##\s+New in\s+v?(?P<version>[0-9][0-9A-Za-z.\-]*)\s*$", re.M)


class UpdateFehler(RuntimeError):
    pass


# ------------------------------------------------------------------ Versionen
def version_tupel(text: str) -> tuple:
    """'v0.6.0' → (0, 6, 0); Vorabversionen ('0.6.0-beta1') zählen vor der Endversion."""
    text = str(text or "").strip().lstrip("vV")
    haupt, _, vorab = text.partition("-")
    zahlen = tuple(int(t) if t.isdigit() else 0 for t in re.split(r"[.]", haupt) if t != "")
    zahlen = zahlen + (0,) * (3 - len(zahlen)) if len(zahlen) < 3 else zahlen
    return zahlen + ((0, vorab) if vorab else (1, ""))


def ist_neuer(kandidat: str, laufend: str = __version__) -> bool:
    return version_tupel(kandidat) > version_tupel(laufend)


# ------------------------------------------------------------------ Release Notes
def abschnitte(notizen: str) -> list:
    """[(Version, Markdown-Text)] der Abschnitte ``## New in X`` in Reihenfolge des Textes."""
    notizen = notizen or ""
    treffer = list(ABSCHNITT.finditer(notizen))
    erg = []
    for i, m in enumerate(treffer):
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(notizen)
        rest = notizen[m.end():ende]
        andere = re.search(r"^#{1,2}\s+(?!New in)", rest, re.M)     # nächste Überschrift (Download, Lizenz …)
        if andere:
            rest = rest[:andere.start()]
        erg.append((m.group("version"), rest.strip()))
    return erg


def notizen_zwischen(notizen: str, ab_exklusiv: str | None, bis_inklusiv: str) -> str:
    """Markdown aller Abschnitte mit ``ab_exklusiv`` < Version ≤ ``bis_inklusiv``, neueste zuerst, je mit Überschrift.
    ``ab_exklusiv`` None = nur ``bis_inklusiv``."""
    teile = []
    for version, text in sorted(abschnitte(notizen), key=lambda vt: version_tupel(vt[0]), reverse=True):
        v = version_tupel(version)
        if v > version_tupel(bis_inklusiv):
            continue
        if ab_exklusiv is None:
            if v != version_tupel(bis_inklusiv):
                continue
        elif v <= version_tupel(ab_exklusiv):
            continue
        teile.append("## %s\n\n%s" % (version, text))
    return "\n\n".join(teile)


def mitgelieferte_notizen() -> str:
    """``RELEASE_NOTES.md`` der laufenden Installation (gepackt: neben dem Programm, sonst aus ``packaging/release``)."""
    kandidaten = []
    if getattr(sys, "frozen", False):
        basis = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        kandidaten += [basis / "mapeditor" / "RELEASE_NOTES.md", Path(sys.executable).parent / "RELEASE_NOTES.md"]
    quelle = Path(__file__).resolve().parents[2]
    # Arbeitsstand: packaging/release; öffentliches Repository: im Hauptordner neben Mapeditor/
    kandidaten += [quelle / "packaging" / "release" / "RELEASE_NOTES.md", quelle.parent / "RELEASE_NOTES.md"]
    for pfad in kandidaten:
        try:
            return pfad.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


# ------------------------------------------------------------------ Abfrage
def _oeffnen(url: str, timeout: float):
    anfrage = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(anfrage, timeout=timeout)


def abfragen(url: str = API_LATEST, timeout: float = 10.0) -> dict:
    """Neuestes Release von GitHub (``release_lesen``). Fehler als ``UpdateFehler``."""
    try:
        with _oeffnen(url, timeout) as antwort:
            daten = json.loads(antwort.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise UpdateFehler(str(e)) from e
    return release_lesen(daten)


def release_lesen(daten: dict) -> dict:
    """Antwort der GitHub-API → {version, tag, seite, notizen, zip_url, zip_name, zip_groesse, sha_url}."""
    tag = str(daten.get("tag_name") or "")
    version = tag.lstrip("vV")
    zip_asset = sha_asset = None
    for asset in daten.get("assets") or []:
        name = str(asset.get("name") or "")
        if ZIP_MUSTER.match(name):
            zip_asset = asset
        elif name.endswith(".zip.sha256"):
            sha_asset = asset
    if not version:
        raise UpdateFehler("release without tag")
    return {"version": version, "tag": tag, "seite": daten.get("html_url") or RELEASES_URL,
            "notizen": daten.get("body") or "", "entwurf": bool(daten.get("draft") or daten.get("prerelease")),
            "zip_url": zip_asset.get("browser_download_url") if zip_asset else None,
            "zip_name": zip_asset.get("name") if zip_asset else None,
            "zip_groesse": int(zip_asset.get("size") or 0) if zip_asset else 0,
            "sha_url": sha_asset.get("browser_download_url") if sha_asset else None}


# ------------------------------------------------------------------ Herunterladen und einspielen
def herunterladen(url: str, ziel: Path, fortschritt=None, abgebrochen=None, timeout: float = 30.0) -> Path:
    """Datei nach ``ziel`` laden (über ``ziel.part``). ``fortschritt(geladen, gesamt)``; ``abgebrochen()`` → True
    bricht ab (``UpdateFehler``)."""
    ziel = Path(ziel)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    teil = ziel.with_name(ziel.name + ".part")
    try:
        with _oeffnen(url, timeout) as antwort, open(teil, "wb") as f:
            gesamt = int(antwort.headers.get("Content-Length") or 0)
            geladen = 0
            while True:
                if abgebrochen is not None and abgebrochen():
                    raise UpdateFehler("cancelled")
                block = antwort.read(256 * 1024)
                if not block:
                    break
                f.write(block)
                geladen += len(block)
                if fortschritt is not None:
                    fortschritt(geladen, gesamt)
        os.replace(teil, ziel)
    except OSError as e:
        raise UpdateFehler(str(e)) from e
    finally:
        if teil.exists():
            try:
                teil.unlink()
            except OSError:
                pass
    return ziel


def sha256_pruefen(datei: Path, pruefsumme_text: str) -> bool:
    """``pruefsumme_text`` wie ``<hex> *Name`` (Release-Asset ``.sha256``)."""
    erwartet = (pruefsumme_text or "").strip().split()[0].lower() if (pruefsumme_text or "").strip() else ""
    h = hashlib.sha256()
    with open(datei, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return bool(erwartet) and h.hexdigest() == erwartet


def installation() -> Path | None:
    """Programmordner der laufenden, gepackten Installation (None beim Start aus dem Quelltext)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def ablage(version: str, installation_: Path | None = None) -> Path:
    """Ordner für die entpackte neue Version: neben der Installation, wenn beschreibbar, sonst
    ``%LOCALAPPDATA%\\Mapeditor\\updates``."""
    installation_ = installation_ or installation()
    if installation_ is not None and os.access(installation_.parent, os.W_OK):
        return installation_.parent / (".Mapeditor-update-%s" % version)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Mapeditor" / "updates" / version


def entpacken(zip_pfad: Path, ziel: Path) -> Path:
    """ZIP nach ``ziel`` entpacken; Rückgabe: Ordner mit ``Mapeditor.exe``. Prüft Pfade (keine Ausbrüche)."""
    ziel = Path(ziel)
    ziel.mkdir(parents=True, exist_ok=True)
    wurzel = ziel.resolve()
    with zipfile.ZipFile(zip_pfad) as z:
        for name in z.namelist():
            pfad = (ziel / name).resolve()
            if wurzel not in pfad.parents and pfad != wurzel:
                raise UpdateFehler("unsafe path in archive: %s" % name)
        z.extractall(ziel)
    exe = next((p for p in ziel.rglob("Mapeditor.exe")), None)
    if exe is None:
        raise UpdateFehler("Mapeditor.exe missing in the archive")
    return exe.parent


def austausch_skript(pid: int, alt: Path, neu: Path, skript: Path) -> Path:
    """CMD-Skript: wartet auf das Ende von ``pid``, tauscht ``alt`` gegen ``neu`` und startet die neue Version.
    Scheitert der Tausch, wird die neue Version aus ``neu`` gestartet und ``alt`` bleibt unverändert."""
    alt, neu = Path(alt), Path(neu)
    sicherung = alt.with_name(alt.name + ".alt")
    zeilen = [
        "@echo off",
        "chcp 65001 >nul",
        ":warten",
        'tasklist /FI "PID eq %d" 2>nul | find "%d" >nul && (timeout /t 1 /nobreak >nul & goto warten)' % (pid, pid),
        'if exist "%s" rmdir /s /q "%s"' % (sicherung, sicherung),
        'move "%s" "%s" >nul 2>&1 || goto ohne_tausch' % (alt, sicherung),
        'move "%s" "%s" >nul 2>&1 || (move "%s" "%s" >nul 2>&1 & goto ohne_tausch)' % (neu, alt, sicherung, alt),
        'start "" "%s"' % (alt / "Mapeditor.exe"),
        'rmdir /s /q "%s"' % sicherung,
        'rmdir /s /q "%s" >nul 2>&1' % neu.parent,
        "goto ende",
        ":ohne_tausch",
        'start "" "%s"' % (neu / "Mapeditor.exe"),
        ":ende",
        '(goto) 2>nul & del "%~f0"',
    ]
    skript = Path(skript)
    skript.parent.mkdir(parents=True, exist_ok=True)
    skript.write_text("\r\n".join(zeilen) + "\r\n", encoding="utf-8")
    return skript
