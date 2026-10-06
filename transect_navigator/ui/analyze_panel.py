"""ANALYZE input controls and per-observation comparison; no compliance scores."""
from pathlib import Path
from collections import Counter
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget)
from qgis.core import Qgis, QgsCsException, QgsProject
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from ..integration.analyze import ingest_recording
from ..integration.comparison import compare_ingested
from ..integration.statistics import summarize_comparison
from ..qaqc.statistics import StatisticsOptions
from ..qaqc.events import EventOptions
from ..integration.events import detect_comparison_events
from ..integration.visualization import publish_analysis, navigation_for_result
from ..integration.analysis_export import capture_analysis
from ..integration.recorded import (RecordingSource, file_sublayers, load_recorded_file)
from ..io.recorded import csv_headers


class AnalyzePanel(QWidget):
    """Choose one plan and recorded input; publish detached layers only after validation."""

    results_ready = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.loaded_input = None
        layout = QVBoxLayout(self)
        planned = QGroupBox('Planned transect',self)
        plan_form = QFormLayout(planned)
        self.planned_layer = QgsMapLayerComboBox(self)
        self.planned_layer.setFilters(Qgis.LayerFilter.LineLayer)
        self.planned_layer.setAllowEmptyLayer(True)
        self.planned_layer.setShowCrs(True)
        self.id_field = QgsFieldComboBox(self)
        self.feature = QComboBox(self)
        self.planned_layer.layerChanged.connect(self._plan_changed)
        self.id_field.fieldChanged.connect(self._features)
        plan_form.addRow('Planned Transect Layer',self.planned_layer)
        plan_form.addRow('Transect ID Field',self.id_field)
        plan_form.addRow('Transect feature',self.feature)
        self.result_spacing = QDoubleSpinBox(self)
        self.result_spacing.setDecimals(3)
        self.result_spacing.setRange(.001, 1_000_000)
        self.result_spacing.setValue(10)
        self.result_spacing.setSuffix(' m')
        self.result_spacing.setToolTip('Fresh navigation waypoints for the selected official plan, in its original direction.')
        plan_form.addRow('Result waypoint spacing', self.result_spacing)
        layout.addWidget(planned)
        recorded = QGroupBox('Recorded GPS observations / track',self)
        form = QFormLayout(recorded)
        self.mode = QComboBox(self)
        self.mode.addItems(['Loaded QGIS layer','File input'])
        self.recorded_layer = QgsMapLayerComboBox(self)
        self.recorded_layer.setFilters(Qgis.LayerFilter.PointLayer | Qgis.LayerFilter.LineLayer)
        self.recorded_layer.setAllowEmptyLayer(True)
        self.recorded_layer.setShowCrs(True)
        form.addRow('Input source',self.mode)
        form.addRow('Recorded layer',self.recorded_layer)
        self.file_box = QGroupBox('File input',self)
        file_form = QFormLayout(self.file_box)
        row = QHBoxLayout()
        self.path = QLineEdit(self)
        self.path.setPlaceholderText('GPX, GeoPackage, GeoJSON or CSV')
        browse = QPushButton('Browse…',self)
        browse.clicked.connect(self._browse)
        row.addWidget(self.path)
        row.addWidget(browse)
        file_form.addRow('File',row)
        self.sublayer = QComboBox(self)
        file_form.addRow('File layer',self.sublayer)
        self.csv_box = QGroupBox('CSV: explicit XY and CRS',self)
        csv_form = QFormLayout(self.csv_box)
        self.x_field, self.y_field = QComboBox(self), QComboBox(self)
        self.csv_crs = QLineEdit('EPSG:4326',self)
        csv_form.addRow('X / longitude column',self.x_field)
        csv_form.addRow('Y / latitude column',self.y_field)
        csv_form.addRow('Coordinate CRS',self.csv_crs)
        file_form.addRow(self.csv_box)
        self.open_file = QPushButton('OPEN INPUT / RELOAD',self)
        self.open_file.clicked.connect(self._open_file)
        file_form.addRow(self.open_file)
        form.addRow(self.file_box)
        self.timestamp, self.accuracy, self.group = (QgsFieldComboBox(self) for _ in range(3))
        for combo in (self.timestamp,self.accuracy,self.group):
            combo.setAllowEmptyFieldName(True)
        form.addRow('Timestamp field (optional)',self.timestamp)
        form.addRow('GNSS accuracy field (optional)',self.accuracy)
        form.addRow('Survey/group field (optional)',self.group)
        self.construct = QCheckBox('Construct a track only for complete, unique timestamp groups',self)
        self.construct.setChecked(True)
        form.addRow(self.construct)
        self.far_distance = QDoubleSpinBox(self)
        self.far_distance.setRange(1,1_000_000)
        self.far_distance.setValue(10_000)
        self.far_distance.setSuffix(' m')
        form.addRow('Very-far input warning',self.far_distance)
        layout.addWidget(recorded)
        note = QLabel('All recorded features are read (layer selection is ignored). Group independent surveys explicitly. '
            'Timestamps need a timezone; missing or duplicate times do not establish chronology. '
            'Raw geometry, attributes and available GNSS fields are retained. Accuracy units are not guessed. '
            'The far warning is a conservative input screen, not a QA/QC result.',self)
        note.setWordWrap(True)
        layout.addWidget(note)
        statistics = QGroupBox('User-configurable QA/QC thresholds — not compliance standards',self)
        statistics_form = QFormLayout(statistics)
        self.thresholds = []
        for label,value in zip('ABC',(3,5,10)):
            control = QDoubleSpinBox(self)
            control.setRange(0,1_000_000)
            control.setDecimals(3)
            control.setValue(value)
            control.setSuffix(' m')
            self.thresholds.append(control)
            statistics_form.addRow(f'Threshold {label} (inclusive)',control)
        self.coverage_station_gap = QDoubleSpinBox(self)
        self.coverage_station_gap.setRange(.001,1_000_000)
        self.coverage_station_gap.setDecimals(3)
        self.coverage_station_gap.setValue(25)
        self.coverage_station_gap.setSuffix(' m')
        self.coverage_time_gap = QDoubleSpinBox(self)
        self.coverage_time_gap.setRange(.001,1_000_000)
        self.coverage_time_gap.setDecimals(3)
        self.coverage_time_gap.setValue(60)
        self.coverage_time_gap.setSuffix(' s')
        statistics_form.addRow('Coverage: maximum station gap',self.coverage_station_gap)
        statistics_form.addRow('Coverage: maximum time gap',self.coverage_time_gap)
        coverage_note = QLabel('Coverage is sampled progress support within threshold C, not proof of survey compliance. '
                              'Deviation statistics weight every valid observation equally. GNSS uncertainty is summarized separately in source units.',self)
        coverage_note.setWordWrap(True)
        statistics_form.addRow(coverage_note)
        layout.addWidget(statistics)
        event_box = QGroupBox('Deviation events — descriptive criteria, not compliance standards',self)
        event_form = QFormLayout(event_box)
        self.event_threshold = QDoubleSpinBox(self)
        self.event_threshold.setRange(0,1_000_000)
        self.event_threshold.setDecimals(3)
        self.event_threshold.setValue(5)
        self.event_threshold.setSuffix(' m')
        event_form.addRow('Exceeds deviation threshold',self.event_threshold)
        self.event_min_duration_enabled = QCheckBox('Require minimum duration',self)
        self.event_min_duration_enabled.setChecked(True)
        self.event_min_duration = QDoubleSpinBox(self)
        self.event_min_duration.setRange(0,1_000_000)
        self.event_min_duration.setDecimals(3)
        self.event_min_duration.setValue(5)
        self.event_min_duration.setSuffix(' s')
        self.event_min_duration_enabled.toggled.connect(self.event_min_duration.setEnabled)
        event_form.addRow(self.event_min_duration_enabled,self.event_min_duration)
        self.event_min_distance_enabled = QCheckBox('Require minimum station span',self)
        self.event_min_distance = QDoubleSpinBox(self)
        self.event_min_distance.setRange(0,1_000_000)
        self.event_min_distance.setDecimals(3)
        self.event_min_distance.setValue(10)
        self.event_min_distance.setSuffix(' m')
        self.event_min_distance.setEnabled(False)
        self.event_min_distance_enabled.toggled.connect(self.event_min_distance.setEnabled)
        event_form.addRow(self.event_min_distance_enabled,self.event_min_distance)
        self.event_time_gap = QDoubleSpinBox(self)
        self.event_time_gap.setRange(.001,1_000_000)
        self.event_time_gap.setDecimals(3)
        self.event_time_gap.setValue(60)
        self.event_time_gap.setSuffix(' s')
        self.event_space_gap = QDoubleSpinBox(self)
        self.event_space_gap.setRange(.001,1_000_000)
        self.event_space_gap.setDecimals(3)
        self.event_space_gap.setValue(50)
        self.event_space_gap.setSuffix(' m')
        event_form.addRow('Maximum inter-fix time gap',self.event_time_gap)
        event_form.addRow('Maximum spatial or station jump',self.event_space_gap)
        event_note = QLabel('Requires at least two consecutive above-threshold fixes. All enabled minima must pass. '
                           'Unknown chronology is excluded; time/spatial gaps split events. Sustained GNSS errors can still create events.',self)
        event_note.setWordWrap(True)
        event_form.addRow(event_note)
        layout.addWidget(event_box)
        self.ingest = QPushButton('INGEST RECORDED GPS DATA',self)
        self.ingest.clicked.connect(self.ingest_data)
        layout.addWidget(self.ingest)
        self.compare = QPushButton('COMPARE OBSERVATIONS TO TRANSECT',self)
        self.compare.clicked.connect(self.compare_data)
        layout.addWidget(self.compare)
        self.status = QLabel('Choose a planned feature and recorded input. Comparison: positive = left, negative = right; ambiguous signs are NULL.',self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.mode.currentIndexChanged.connect(self._source_changed)
        self.recorded_layer.layerChanged.connect(self._source_changed)
        self.path.textChanged.connect(self._path_changed)
        self.sublayer.currentIndexChanged.connect(self._invalidate_file)
        self.x_field.currentIndexChanged.connect(self._invalidate_file)
        self.y_field.currentIndexChanged.connect(self._invalidate_file)
        self.csv_crs.textChanged.connect(self._invalidate_file)
        self._plan_changed()
        self._source_changed()
        self.csv_box.hide()

    def _plan_changed(self, *args) -> None:
        self.id_field.setLayer(self.planned_layer.currentLayer())
        self._features()

    def _features(self, *args) -> None:
        self.feature.clear()
        self.feature.addItem('Choose one planned feature…',None)
        layer = self.planned_layer.currentLayer()
        field = self.id_field.currentField()
        if layer is None or layer.fields().indexFromName(field) < 0:
            return
        for index, feature in enumerate(layer.getFeatures()):
            if index >= 10_000:
                self.feature.clear()
                self.feature.addItem('Filter the planned layer to 10,000 features or fewer',None)
                return
            self.feature.addItem(f'{feature[field]} — feature {feature.id()}',feature.id())

    def _source_changed(self, *args) -> None:
        is_file = self.mode.currentIndex() == 1
        self.file_box.setVisible(is_file)
        self.recorded_layer.setEnabled(not is_file)
        source = self.loaded_input if is_file else self.recorded_layer.currentLayer()
        layer = source.points if isinstance(source,RecordingSource) else source
        for combo in (self.timestamp,self.accuracy,self.group):
            combo.setLayer(layer)
            combo.setField('')
        if isinstance(source,RecordingSource):
            self.timestamp.setField(source.default_time_field)

    def _path_changed(self, *args) -> None:
        self.loaded_input = None
        self.sublayer.clear()
        self.x_field.clear()
        self.y_field.clear()
        self.csv_box.setVisible(Path(self.path.text()).suffix.lower() == '.csv')
        self._source_changed()

    def _invalidate_file(self, *args) -> None:
        self.loaded_input = None
        self._source_changed()

    def _browse(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self,'Recorded GPS input','',
            'Recorded data (*.gpx *.gpkg *.geojson *.json *.csv)')
        if filename:
            self.path.setText(filename)
            self._inspect_file()

    def _inspect_file(self) -> None:
        path = Path(self.path.text()).expanduser()
        self.sublayer.clear()
        is_csv = path.suffix.lower() == '.csv'
        self.csv_box.setVisible(is_csv)
        try:
            if is_csv:
                headers = csv_headers(path)
                for combo in (self.x_field,self.y_field):
                    combo.clear()
                    combo.addItem('Choose a column…','')
                    for header in headers:
                        combo.addItem(header,header)
                self.status.setText('Choose CSV X/Y columns and verify the declared CRS, then Open Input.')
            elif path.suffix.lower() == '.gpx':
                self.sublayer.addItem('All GPX tracks and trackpoints (segments preserved)','')
            else:
                for name,uri in file_sublayers(path):
                    self.sublayer.addItem(name,uri)
        except (ValueError,OSError) as error:
            self.status.setText(f'Cannot inspect input: {error}')

    def _open_file(self) -> None:
        try:
            path = Path(self.path.text().strip()).expanduser()
            if not path.is_file():
                raise ValueError('Choose an existing recorded data file')
            if path.suffix.lower() == '.csv' and not self.x_field.count():
                self._inspect_file()
                return
            if path.suffix.lower() in {'.gpkg','.geojson','.json'} and not self.sublayer.count():
                self._inspect_file()
                return
            self.loaded_input = load_recorded_file(path,uri=self.sublayer.currentData() or '',
                x_field=self.x_field.currentData() or '',y_field=self.y_field.currentData() or '',csv_crs=self.csv_crs.text().strip())
            self._source_changed()
            self.status.setText('Input opened privately. Choose timestamp/accuracy/group fields. File data are captured at Open Input; use Reload after changes.')
        except (ValueError,OSError,QgsCsException,RuntimeError) as error:
            self.loaded_input = None
            self._source_changed()
            self.status.setText(f'Cannot open recorded input: {error}')

    def ingest_data(self) -> None:
        self._run(False)

    def compare_data(self) -> None:
        """Always capture current inputs; do not analyze stale previously published layers."""
        self._run(True)

    def _run(self, compare: bool) -> None:
        self.ingest.setEnabled(False)
        self.compare.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            options = StatisticsOptions(tuple(control.value() for control in self.thresholds),
                self.coverage_station_gap.value(),self.coverage_time_gap.value()) if compare else None
            event_options = EventOptions(self.event_threshold.value(),
                self.event_min_duration.value() if self.event_min_duration_enabled.isChecked() else None,
                self.event_min_distance.value() if self.event_min_distance_enabled.isChecked() else None,
                self.event_time_gap.value(),self.event_space_gap.value()) if compare else None
            source = self.loaded_input if self.mode.currentIndex() == 1 else self.recorded_layer.currentLayer()
            if source is None:
                raise ValueError('Choose a recorded layer or Open Input first')
            result = ingest_recording(self.planned_layer.currentLayer(),self.id_field.currentField(),
                self.feature.currentData(),source,timestamp_field=self.timestamp.currentField(),
                accuracy_field=self.accuracy.currentField(),group_field=self.group.currentField(),
                create_track=self.construct.isChecked(),far_threshold_m=self.far_distance.value())
            project = QgsProject.instance()
            layers = result.layers()
            if compare:
                comparison = compare_ingested(result,project=project)
                summary = summarize_comparison(result,comparison,options=options,project=project)
                events = detect_comparison_events(result,comparison,options=event_options,project=project)
                waypoints = navigation_for_result(result.planned, self.id_field.currentField(),
                    self.result_spacing.value(), project)
                exported = capture_analysis(result, comparison, summary, events, waypoints, project=project)
                group = publish_analysis(result, comparison, summary, events,
                    id_field=self.id_field.currentField(), spacing_m=self.result_spacing.value(),
                    project=project, waypoints=waypoints)
                self.results_ready.emit({'summary': summary.summary, 'events': events.detection,
                    'rows': comparison.rows, 'spacing_m': self.result_spacing.value(),
                    'group': group.name(), 'has_track': result.raw_track is not None,
                    'time': exported.analysis_time, 'export': exported})
            else:
                group = project.layerTreeRoot().addGroup('Transect Navigator — ANALYZE inputs')
                try:
                    for layer in layers:
                        if project.addMapLayer(layer,False) is None:
                            raise ValueError(f'Could not add {layer.name()} to the project')
                        group.addLayer(layer)
                except Exception:
                    project.removeMapLayers([layer.id() for layer in layers])
                    project.layerTreeRoot().removeChildNode(group)
                    raise
            counts = Counter(issue.code for issue in result.issues)
            summary = '; '.join(f'{code.replace("_"," ").lower()}: {count}' for code,count in sorted(counts.items()))
            self.status.setText(f'Preserved {len(result.ordering.observations):,} raw observations. '
                f'{len(result.ordering.ordered_groups)} timestamp-orderable groups. '
                'Outputs are temporary, read-only snapshots; retain the original files. '
                + (f'Validation notices: {summary}. Open the ingestion validation table for details.' if summary else 'No ingestion warnings.')
                + (f' Detected {len(events.detection.events)} events; {len(events.detection.excluded_groups)} groups unevaluated (see Event Validation). Per-observation distances and QA Summary calculated. Positive = left, negative = right; inspect QA_Sign_Status for NULL signs. No compliance classifications.' if compare else ' No QA/QC metrics were calculated.'))
        except (ValueError,OSError,QgsCsException,RuntimeError) as error:
            self.status.setText(f'Cannot {"compare" if compare else "ingest"} recorded data: {error}\nSource data were not changed.')
        finally:
            QApplication.restoreOverrideCursor()
            self.ingest.setEnabled(True)
            self.compare.setEnabled(True)
