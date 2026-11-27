"""Dialoge der automatischen Aktualisierung (26.11.2026, Logik in ``aktualisierung``).

* ``beim_start(fenster)``: nach dem ersten Start einer neuen Version deren Release Notes zeigen („What's new“), dann –
  höchstens einmal am Tag und nur, wenn unter Ansicht → Automatik eingeschaltet – im Hintergrund nach einem neuen
  Release suchen.
* ``pruefen(fenster, manuell=True)``: Hilfe → *Check for updates …* (meldet auch „aktuell“ und Fehler).
* ``UpdateDialog``: neue Version mit den Release Notes aller neueren Versionen; *Download and install* (gepackte
  Installation), *Open release page* (Start aus dem Quelltext oder Release ohne ZIP), *Skip this version*, *Later*.
* ``DownloadDialog``: Laden mit Fortschritt, Prüfsumme, Entpacken; danach Neustart sofort oder beim Beenden.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressBar, QPushButton,
                               QTextBrowser, QVBoxLayout)

from . import __version__, aktualisierung as akt
from .ansicht import settings
from .i18n import tr

#: höchstens so oft automatisch suchen (Sekunden)
PRUEF_ABSTAND_S = 24 * 3600


def _notiz_browser(markdown: str) -> QTextBrowser:
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setMarkdown(markdown or tr("No release notes."))
    browser.setMinimumSize(620, 360)
    return browser


# ------------------------------------------------------------------ Was ist neu
def notizen_nach_update(laufend: str = __version__) -> str | None:
    """Markdown für den „What's new“-Dialog oder None (schon gesehen bzw. erste Installation). Merkt sich die Version."""
    store = settings()
    gesehen = store.value("update/gesehen")
    store.setValue("update/gesehen", laufend)
    if gesehen and not akt.ist_neuer(laufend, str(gesehen)):
        return None
    if not gesehen:
        # vor 0.6.0 gab es den Schlüssel nicht: andere Einstellungen = Update, keine = erste Installation
        frueher = [k for k in store.allKeys() if not k.startswith("update/")]
        if not frueher:
            return None
    text = akt.notizen_zwischen(akt.mitgelieferte_notizen(), str(gesehen) if gesehen else None, laufend)
    return text or None


class NeuDialog(QDialog):
    """Release Notes nach dem ersten Start einer neuen Version (auch Hilfe → What's new …)."""

    def __init__(self, markdown: str, version: str = __version__, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("What's new in Mapeditor {version}", version=version))
        layout = QVBoxLayout(self)
        layout.addWidget(_notiz_browser(markdown))
        knoepfe = QDialogButtonBox(QDialogButtonBox.Ok)
        knoepfe.accepted.connect(self.accept)
        layout.addWidget(knoepfe)
        self.resize(720, 520)


def neu_zeigen(fenster, alle: bool = False):
    """Hilfe → What's new …: Notizen der laufenden Version (``alle``: ganze Datei)."""
    notizen = akt.mitgelieferte_notizen()
    text = notizen if alle else akt.notizen_zwischen(notizen, None, __version__)
    NeuDialog(text or notizen, parent=fenster).exec()


# ------------------------------------------------------------------ Suchen
class _Suche(QObject):
    fertig = Signal(object, str)          # Release-Info oder None, Fehlertext

    def starten(self, url: str = akt.API_LATEST):
        def lauf():
            try:
                self.fertig.emit(akt.abfragen(url), "")
            except akt.UpdateFehler as e:
                self.fertig.emit(None, str(e))
        threading.Thread(target=lauf, daemon=True).start()


def beim_start(fenster):
    """Nach dem Start: „What's new“ (einmal je neuer Version), dann automatische Suche (einmal am Tag)."""
    if os.environ.get("MAPEDITOR_KEIN_UPDATE") or os.environ.get("MAPEDITOR_RAUCHTEST"):
        return
    text = notizen_nach_update()
    if text:
        NeuDialog(text, parent=fenster).exec()
    if not fenster.view_state.get("update_pruefen", True):
        return
    store = settings()
    try:
        zuletzt = float(store.value("update/letzte_pruefung", 0) or 0)
    except (TypeError, ValueError):
        zuletzt = 0.0
    if time.time() - zuletzt < PRUEF_ABSTAND_S:
        return
    pruefen(fenster, manuell=False)


def pruefen(fenster, manuell: bool = True, url: str = akt.API_LATEST):
    """Im Hintergrund nach einem neuen Release suchen; ``manuell`` meldet auch „aktuell“ und Fehler."""
    suche = _Suche(fenster)
    fenster._update_suche = suche                  # am Leben halten

    def fertig(info, fehler):
        settings().setValue("update/letzte_pruefung", time.time())
        if info is None:
            if manuell:
                QMessageBox.warning(fenster, tr("Check for updates"),
                                    tr("Could not reach GitHub: {fehler}", fehler=fehler))
            return
        if not akt.ist_neuer(info["version"]) or info["entwurf"]:
            if manuell:
                QMessageBox.information(fenster, tr("Check for updates"),
                                        tr("You have the latest version ({version}).", version=__version__))
            return
        if not manuell and str(settings().value("update/uebersprungen") or "") == info["version"]:
            return
        UpdateDialog(info, fenster).exec()
    suche.fertig.connect(fertig)
    suche.starten(url)
    return suche


# ------------------------------------------------------------------ Abfrage-Dialog
class UpdateDialog(QDialog):
    """Neue Version mit Release Notes; Ergebnis in ``aktion``: installieren, seite, ueberspringen, spaeter."""

    def __init__(self, info: dict, fenster=None):
        super().__init__(fenster)
        self.info, self.fenster, self.aktion = info, fenster, "spaeter"
        self.setWindowTitle(tr("Update available"))
        layout = QVBoxLayout(self)
        kopf = QLabel(tr("Mapeditor {neu} is available, you have {alt}.", neu=info["version"], alt=__version__))
        kopf.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(kopf)
        notizen = akt.notizen_zwischen(info["notizen"], __version__, info["version"]) or info["notizen"]
        layout.addWidget(_notiz_browser(notizen))
        self.herunterladbar = bool(info.get("zip_url")) and akt.installation() is not None
        hinweis = QLabel(tr("The download (about {mb} MB) replaces this installation. Your projects, settings and the "
                            "carrier package are kept.", mb=max(1, round(info.get("zip_groesse", 0) / 1e6)))
                         if self.herunterladbar else
                         tr("This copy runs from the source code: get the new version from the release page."))
        hinweis.setWordWrap(True)
        layout.addWidget(hinweis)
        knoepfe = QDialogButtonBox()
        self.installieren = knoepfe.addButton(tr("Download and install") if self.herunterladbar
                                              else tr("Open release page"), QDialogButtonBox.AcceptRole)
        self.ueberspringen = knoepfe.addButton(tr("Skip this version"), QDialogButtonBox.DestructiveRole)
        self.spaeter = knoepfe.addButton(tr("Later"), QDialogButtonBox.RejectRole)
        self.installieren.setDefault(True)
        self.installieren.clicked.connect(self._installieren)
        self.ueberspringen.clicked.connect(self._ueberspringen)
        self.spaeter.clicked.connect(self.reject)
        layout.addWidget(knoepfe)
        self.resize(760, 600)

    def _installieren(self):
        if not self.herunterladbar:
            self.aktion = "seite"
            QDesktopServices.openUrl(QUrl(self.info["seite"]))
            self.accept()
            return
        self.aktion = "installieren"
        self.accept()
        DownloadDialog(self.info, self.fenster).exec()

    def _ueberspringen(self):
        self.aktion = "ueberspringen"
        settings().setValue("update/uebersprungen", self.info["version"])
        self.reject()


# ------------------------------------------------------------------ Laden und einspielen
class _Laden(QObject):
    fortschritt = Signal(int, int)
    fertig = Signal(str, str)              # Ordner der neuen Version, Fehlertext

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info, self.abbruch = info, False

    def starten(self):
        def lauf():
            try:
                ablage = akt.ablage(self.info["version"])
                zip_pfad = ablage.parent / (ablage.name + ".zip") if ablage.parent.exists() else None
                zip_pfad = zip_pfad or Path(tempfile.gettempdir()) / self.info["zip_name"]
                akt.herunterladen(self.info["zip_url"], zip_pfad, lambda g, t: self.fortschritt.emit(g, t),
                                  lambda: self.abbruch)
                if self.info.get("sha_url"):
                    sha = akt.herunterladen(self.info["sha_url"], zip_pfad.with_name(zip_pfad.name + ".sha256"))
                    ok = akt.sha256_pruefen(zip_pfad, sha.read_text(encoding="ascii", errors="replace"))
                    sha.unlink()
                    if not ok:
                        zip_pfad.unlink()
                        raise akt.UpdateFehler("checksum does not match")
                ordner = akt.entpacken(zip_pfad, ablage)
                zip_pfad.unlink()
                self.fertig.emit(str(ordner), "")
            except (akt.UpdateFehler, OSError) as e:
                self.fertig.emit("", str(e))
        threading.Thread(target=lauf, daemon=True).start()


class DownloadDialog(QDialog):
    """Lädt das Release, prüft es, entpackt es und bietet den Neustart an."""

    def __init__(self, info: dict, fenster=None):
        super().__init__(fenster)
        self.info, self.fenster, self.ordner, self.fehler = info, fenster, None, None
        self.setWindowTitle(tr("Downloading Mapeditor {version}", version=info["version"]))
        layout = QVBoxLayout(self)
        self.text = QLabel(tr("Downloading …"))
        layout.addWidget(self.text)
        self.balken = QProgressBar()
        self.balken.setRange(0, max(1, int(info.get("zip_groesse") or 0)))
        layout.addWidget(self.balken)
        self.abbrechen = QPushButton(tr("Cancel"))
        self.abbrechen.clicked.connect(self._abbrechen)
        layout.addWidget(self.abbrechen, 0, Qt.AlignRight)
        self.laden = _Laden(info, self)
        self.laden.fortschritt.connect(self._fortschritt)
        self.laden.fertig.connect(self._fertig)
        self.resize(460, 140)
        QTimer.singleShot(0, self.laden.starten)

    def _fortschritt(self, geladen, gesamt):
        if gesamt and self.balken.maximum() != gesamt:
            self.balken.setRange(0, gesamt)
        self.balken.setValue(min(geladen, self.balken.maximum()))
        self.text.setText(tr("Downloading … {mb} MB", mb=round(geladen / 1e6)))

    def _abbrechen(self):
        self.laden.abbruch = True
        self.reject()

    def _fertig(self, ordner, fehler):
        if fehler:
            self.fehler = fehler
            if not self.laden.abbruch:
                QMessageBox.warning(self, tr("Update"), tr("The update failed: {fehler}", fehler=fehler))
            self.reject()
            return
        self.ordner = Path(ordner)
        frage = QMessageBox(self)
        frage.setWindowTitle(tr("Update ready"))
        frage.setText(tr("Mapeditor {version} is ready. Restart now to install it?", version=self.info["version"]))
        jetzt = frage.addButton(tr("Restart now"), QMessageBox.AcceptRole)
        frage.addButton(tr("When I close Mapeditor"), QMessageBox.RejectRole)
        frage.exec()
        self.accept()
        einspielen(self.ordner, self.fenster, sofort=frage.clickedButton() is jetzt)


def _skript_starten(skript: Path):
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(["cmd.exe", "/c", str(skript)], creationflags=flags, close_fds=True)


def einspielen(neu: Path, fenster, sofort: bool):
    """Austauschskript schreiben; ``sofort``: Editor schließen (fragt nach ungespeicherten Änderungen), sonst beim
    Beenden. Bricht der Nutzer das Schließen ab, bleibt die neue Version bis zum Beenden liegen."""
    alt = akt.installation()
    if alt is None:
        return None
    skript = akt.austausch_skript(os.getpid(), alt, Path(neu), Path(tempfile.gettempdir()) / "mapeditor_update.cmd")
    app = QApplication.instance()
    if getattr(app, "_update_skript", None) is None:
        app._update_skript = skript
        app.aboutToQuit.connect(lambda: _skript_starten(skript))
    if sofort and fenster is not None and fenster.close():
        app.quit()
    return skript
