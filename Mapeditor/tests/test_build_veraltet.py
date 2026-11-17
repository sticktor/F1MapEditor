"""Bauziel EGO-Editor / Track Installer ist veraltet: Warnbalken, Bauwarnung, Standardziel Swofty."""

from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from mapeditor.i18n import tr
from mapeditor.build import EGO_DEPRECATED, BuildDialog
from mapeditor.project import Project
from mapeditor.window import MainWindow
from conftest import EXAMPLES
from test_build import builder, click_build  # noqa: F401  (Fixture builder)


def test_default_target_is_swofty_and_saved_choice_kept(app, tmp_path, example_preview):
    project = Project.open(EXAMPLES / "teststrecke.json")
    project.manifest.pop("bauen", None)
    project.save(tmp_path / "p1")
    w = MainWindow(project, auto_preview=False)
    w.install_preview(example_preview)
    d = BuildDialog(w)
    assert d.target.currentData() == "swofty"
    d.close()
    w.document.undo.setClean()
    w.close()
    project2 = Project.open(EXAMPLES / "teststrecke.json")
    project2.manifest["bauen"] = {"ziel": "ego"}
    project2.save(tmp_path / "p2")
    w2 = MainWindow(project2, auto_preview=False)
    w2.install_preview(example_preview)
    d2 = BuildDialog(w2)
    assert d2.target.currentData() == "ego"
    d2.close()
    w2.document.undo.setClean()
    w2.close()


def test_banner_follows_target_and_switch_button(builder):
    w, d, mode = builder
    ego, swofty = d.target.findData("ego"), d.target.findData("swofty")
    d.target.setCurrentIndex(ego)
    assert not d.deprecation_banner.isHidden()
    assert d.deprecation_banner.toolTip()
    d.target.setCurrentIndex(swofty)
    assert d.deprecation_banner.isHidden()
    d.target.setCurrentIndex(ego)
    QTest.mouseClick(d.deprecation_button, Qt.LeftButton)
    assert d.target.currentData() == "swofty" and d.deprecation_banner.isHidden()


def test_ego_build_logs_warning_swofty_does_not(builder):
    w, d, mode = builder
    d.target.setCurrentIndex(d.target.findData("ego"))
    click_build(d)
    assert any(m["id"] == "EGO_VERALTET" and m["level"] == "Warnung" for m in d.messages)
    assert any(m["text"] == tr(EGO_DEPRECATED) for m in d.messages if m["id"] == "EGO_VERALTET")
    assert "deprecated" in EGO_DEPRECATED
    d.job._kill_if_running()
