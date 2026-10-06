"""Native PREPARE form with one inline status area and explicit export options."""
from pathlib import Path
from qgis.core import Qgis, QgsCsException, QgsProject, QgsVectorLayer
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QDialog, QDoubleSpinBox,
                                QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                                QLineEdit, QPushButton, QVBoxLayout, QWidget, QTabWidget, QScrollArea)
from ..integration.prepare import PreparedNavigation, prepare_navigation
from ..integration.export import export_navigation_package
from ..io.package import package_destination
from .analyze_panel import AnalyzePanel
from .results_panel import ResultsPanel


class MainDialog(QDialog):
    """Collect options; services own validation, calculations and file creation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('Transect Navigator')
        self.setObjectName('transect_navigator_dialog')
        outer = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs)
        prepare_panel = QWidget(self)
        layout = QVBoxLayout(prepare_panel)
        self.tabs.addTab(prepare_panel,'PREPARE')
        self.analyze_panel = AnalyzePanel(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.analyze_panel)
        self.tabs.addTab(scroll,'ANALYZE — inputs')
        self.results_panel = ResultsPanel(self)
        self.results_scroll = QScrollArea(self)
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setWidget(self.results_panel)
        self.tabs.addTab(self.results_scroll, 'RESULTS')
        self.analyze_panel.results_ready.connect(self._show_results)
        intro = QLabel('Create navigation waypoints and a portable package from your planned transects.', self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        inputs = QGroupBox('Transect inputs', self)
        form = QFormLayout(inputs)
        self.layer_combo = QgsMapLayerComboBox(self)
        self.layer_combo.setFilters(Qgis.LayerFilter.LineLayer)
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setShowCrs(True)
        self.field_combo = QgsFieldComboBox(self)
        self.layer_combo.layerChanged.connect(self.field_combo.setLayer)
        self.field_combo.setLayer(self.layer_combo.currentLayer())
        self.spacing = QDoubleSpinBox(self)
        self.spacing.setDecimals(3)
        self.spacing.setRange(0.001, 1_000_000.0)
        self.spacing.setValue(10.0)
        self.spacing.setSuffix(' m')
        form.addRow('Input Transect Layer', self.layer_combo)
        form.addRow('Transect ID Field', self.field_combo)
        form.addRow('Waypoint Spacing', self.spacing)
        self.selected_only = QCheckBox('Use selected features only', self)
        self.selected_only.setToolTip('Unchecked: use all features allowed by the layer filter. Checked: a selection is required.')
        self.reverse = QCheckBox('Reverse direction for this run', self)
        form.addRow(self.selected_only)
        form.addRow(self.reverse)
        layout.addWidget(inputs)
        note = QLabel('Spacing follows WGS84 geodesic legs between input vertices. Both endpoints are '
                      'included. Bearings point toward the next waypoint relative to true north. '
                      'Original transects remain unchanged.', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        outputs = QGroupBox('Navigation package', self)
        output_form = QFormLayout(outputs)
        destination_row = QHBoxLayout()
        self.destination = QLineEdit(self)
        self.destination.setPlaceholderText('Choose an existing output folder…')
        browse = QPushButton('Browse…', self)
        browse.clicked.connect(self.browse_destination)
        destination_row.addWidget(self.destination)
        destination_row.addWidget(browse)
        output_form.addRow('Output folder', destination_row)
        self.package_name = QLineEdit('navigation_package', self)
        output_form.addRow('New package name', self.package_name)
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel('GeoPackage (always included)', self))
        self.formats = {}
        for name in ('GeoJSON','GPX','CSV'):
            check = QCheckBox(name, self)
            check.setChecked(True)
            self.formats[name] = check
            format_row.addWidget(check)
        output_form.addRow('Also export', format_row)
        self.load_exported = QCheckBox('Add exported waypoints to the project', self)
        output_form.addRow(self.load_exported)
        explanation = QLabel('Creates a new folder; existing packages are never overwritten. '
                             'Export regenerates from the current inputs and settings.', self)
        explanation.setWordWrap(True)
        output_form.addRow(explanation)
        layout.addWidget(outputs)
        actions = QHBoxLayout()
        self.generate = QPushButton('GENERATE WAYPOINTS', self)
        self.generate.clicked.connect(self.generate_waypoints)
        self.export = QPushButton('EXPORT NAVIGATION PACKAGE', self)
        self.export.clicked.connect(self.export_package)
        actions.addWidget(self.generate)
        actions.addWidget(self.export)
        layout.addLayout(actions)
        self.status = QLabel('Ready. Generate adds a temporary waypoint layer; export saves a complete package.', self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setMinimumHeight(65)
        layout.addWidget(self.status)
        close = QPushButton('Close', self)
        close.clicked.connect(self.close)
        outer.addWidget(close)
        self.resize(760, 750)

    def _show_results(self, snapshot: dict) -> None:
        self.results_panel.set_result(snapshot)
        self.tabs.setCurrentWidget(self.results_scroll)

    def browse_destination(self) -> None:
        """Use a folder chooser; no write occurs here."""
        folder = QFileDialog.getExistingDirectory(self, 'Choose output folder', self.destination.text())
        if folder:
            self.destination.setText(folder)

    def _prepare(self) -> PreparedNavigation:
        """Read the current controls for each operation; do not reuse stale results."""
        return prepare_navigation(self.layer_combo.currentLayer(), self.field_combo.currentField(),
                                  self.spacing.value(), selected_only=self.selected_only.isChecked(),
                                  reverse=self.reverse.isChecked())

    def _busy(self, active: bool) -> None:
        self.generate.setEnabled(not active)
        self.export.setEnabled(not active)
        if active:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def generate_waypoints(self) -> None:
        """Add only a completed generation result; errors stay in one inline area."""
        self._busy(True)
        self.status.setText('Generating waypoints…')
        try:
            prepared = self._prepare()
            if QgsProject.instance().addMapLayer(prepared.waypoints) is None:
                raise ValueError('QGIS could not add the generated waypoint layer')
            self.status.setText(f'Added {len(prepared.rows):,} waypoints for {len(prepared.transects):,} transects. '
                                'The layer is temporary. Export a navigation package to save the plan and waypoints.')
        except (ValueError, QgsCsException, OSError) as error:
            self.status.setText(f'Cannot generate waypoints: {error}\nNo new layer was added.')
        finally:
            self._busy(False)

    def export_package(self) -> None:
        """Export a fresh coherent run, never stale cached points or modified output layers."""
        self._busy(True)
        self.status.setText('Preparing navigation package…')
        try:
            if not self.destination.text().strip():
                raise ValueError('Choose an output folder')
            parent = Path(self.destination.text().strip()).expanduser()
            name = self.package_name.text().strip()
            package_destination(parent, name)  # Fail before expensive geometry work.
            prepared = self._prepare()
            formats = {name for name,check in self.formats.items() if check.isChecked()}
            folder = export_navigation_package(prepared, parent, name, formats)
            message = f'Exported {len(prepared.rows):,} waypoints and {len(prepared.transects):,} planned transects to:\n{folder}'
            if self.load_exported.isChecked():
                layer = QgsVectorLayer(f'{folder / "navigation.gpkg"}|layername=navigation_waypoints',
                                       f'{name} — navigation waypoints', 'ogr')
                if not layer.isValid() or QgsProject.instance().addMapLayer(layer) is None:
                    message += '\nFiles are saved, but QGIS could not load the exported layer. Open navigation.gpkg manually.'
            self.status.setText(message)
        except (ValueError, QgsCsException, OSError) as error:
            self.status.setText(f'Cannot export navigation package: {error}\nExisting files and source layers were not changed.')
        finally:
            self._busy(False)
