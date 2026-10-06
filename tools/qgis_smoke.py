"""Execute from QGIS Desktop's Python console; requires a real iface."""
from qgis.core import QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QEvent
from qgis.utils import iface
from transect_navigator import classFactory


def run() -> None:
    """Check repeated lifecycle and dialog reuse without reading source features."""
    if iface is None:
        raise RuntimeError('Run this smoke check inside QGIS Desktop')
    project = QgsProject.instance()
    before = (set(project.mapLayers()), project.isDirty())
    plugin = classFactory(iface)
    try:
        for _ in range(2):
            plugin.initGui()
            action = plugin.action
            plugin.initGui()
            assert plugin.action is action
            plugin.run()
            dialog = plugin.dialog
            assert dialog.isVisible()
            dialog.close()
            plugin.run()
            assert plugin.dialog is dialog and dialog.isVisible()
            plugin.unload()
            plugin.unload()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert plugin.action is None and plugin.dialog is None
        assert before == (set(project.mapLayers()), project.isDirty())
    finally:
        plugin.unload()
    print('Transect Navigator desktop lifecycle smoke check passed')


run()
