"""Own QGIS menu registration and dialog lifetime only."""
from __future__ import annotations
from qgis.gui import QgisInterface
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 places QAction in QtWidgets.
    from qgis.PyQt.QtWidgets import QAction
from .ui.main_dialog import MainDialog


class TransectNavigatorPlugin:
    """Small lifecycle controller with symmetric load/unload behavior."""

    MENU = "&Transect Navigator"

    def __init__(self, iface: QgisInterface) -> None:
        self.iface = iface
        self.action: QAction | None = None
        self.dialog: MainDialog | None = None

    def initGui(self) -> None:
        """Register one action; repeated initialization is harmless."""
        if self.action is not None:
            return
        self.action = QAction("Transect Navigator…", self.iface.mainWindow())
        self.action.setObjectName("transect_navigator_open")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.MENU, self.action)

    def run(self) -> None:
        """Show or raise the single nonmodal dialog."""
        if self.dialog is None:
            self.dialog = MainDialog(self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def unload(self) -> None:
        """Release owned UI and disconnect the registered action."""
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if self.action is not None:
            self.iface.removePluginMenu(self.MENU, self.action)
            self.action.triggered.disconnect(self.run)
            self.action.deleteLater()
            self.action = None
