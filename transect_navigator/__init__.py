"""QGIS entry point; importing the package does not require QGIS."""
from __future__ import annotations
from typing import TYPE_CHECKING

__version__ = '0.1.13-rc1'

if TYPE_CHECKING:
    from qgis.gui import QgisInterface
    from .plugin import TransectNavigatorPlugin


def classFactory(iface: QgisInterface) -> TransectNavigatorPlugin:
    """Construct the plugin when QGIS requests it."""
    from .plugin import TransectNavigatorPlugin
    return TransectNavigatorPlugin(iface)
