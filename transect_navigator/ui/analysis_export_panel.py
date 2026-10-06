"""Export the latest completed run, without rereading source or displayed layers."""
from pathlib import Path
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QApplication, QWidget, QFormLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QFileDialog)
from qgis.core import QgsCsException
from ..integration.analysis_export import AnalysisExport, export_analysis_package


class AnalysisExportPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.analysis: AnalysisExport | None = None
        layout = QFormLayout(self)
        row = QHBoxLayout()
        self.folder = QLineEdit(self)
        self.folder.setPlaceholderText('Existing output folder…')
        browse = QPushButton('Browse…', self)
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder)
        row.addWidget(browse)
        layout.addRow('Analysis output folder', row)
        self.name = QLineEdit('analysis_package', self)
        layout.addRow('New package name', self.name)
        self.export = QPushButton('EXPORT ANALYSIS PACKAGE (GeoPackage + CSV)', self)
        self.export.setEnabled(False)
        self.export.clicked.connect(self.export_results)
        layout.addRow(self.export)
        self.status = QLabel('Run a comparison first. Export uses the completed run shown above.', self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addRow(self.status)

    def set_analysis(self, analysis: AnalysisExport | None) -> None:
        self.analysis = analysis
        self.export.setEnabled(analysis is not None)
        self.status.setText('Ready to export the displayed completed run. Creates a new folder; existing files are never overwritten.'
                            if analysis is not None else 'Run a comparison first.')

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Choose analysis output folder', self.folder.text())
        if folder:
            self.folder.setText(folder)

    def export_results(self) -> None:
        if self.analysis is None:
            self.status.setText('Run a comparison before exporting analysis.')
            return
        self.export.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if not self.folder.text().strip():
                raise ValueError('Choose an existing output folder')
            destination = export_analysis_package(self.analysis, Path(self.folder.text()), self.name.text())
            self.status.setText(f'Exported and verified: {destination}\nRetain the original GPS/device files.')
        except (ValueError, OSError, RuntimeError, QgsCsException) as error:
            self.status.setText(f'Cannot export analysis: {error}\nThe completed run and source data remain unchanged.')
        finally:
            QApplication.restoreOverrideCursor()
            self.export.setEnabled(True)
