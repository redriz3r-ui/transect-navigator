"""Minimal human review workspace; all assignments use exact selected IDs."""
from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout)
from qgis.core import (QgsCoordinateReferenceSystem, QgsFeature, QgsField,
    QgsGeometry, QgsPointXY, QgsVectorLayer, QgsWkbTypes, QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtCore import QMetaType
from qgis.gui import QgsMapCanvas, QgsMapToolPan

from ..integration.survey_review import load_review, save_review, start_review, validate_review_intake
from ..survey.review import ContinuityPolicy


def choose_review_policy(parent) -> ContinuityPolicy | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle('Review continuity policy')
    layout = QFormLayout(dialog)
    note = QLabel('Choose operational break limits. These are not scientific compliance thresholds. '
                  'Unknown chronology stays unknown. Existing snapshots restore their saved policy.')
    note.setWordWrap(True)
    layout.addRow(note)
    time, space = QDoubleSpinBox(dialog), QDoubleSpinBox(dialog)
    for spin, value in ((time, 60), (space, 100)):
        spin.setRange(.001, 1_000_000)
        spin.setDecimals(3)
        spin.setValue(value)
    layout.addRow('Break after time gap > seconds', time)
    layout.addRow('Break after spatial jump > metres', space)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return ContinuityPolicy(time.value(), space.value())
    return None


class SurveyReviewDialog(QDialog):
    """Owns isolated map layers; does not style, select or edit intake/source layers."""
    def __init__(self, intake, policy: ContinuityPolicy, parent=None):
        super().__init__(parent)
        self.intake = intake
        self.run = start_review(intake, policy)
        self._saved_revision = 0
        self._qa_window = None
        self.setWindowTitle(f'Survey Association Review — {intake.survey.survey_id}')
        self.resize(1200, 850)
        layout = QVBoxLayout(self)
        note = QLabel('Human review only. Selection is not confirmation. Direction refers to the official plan. '
                      'SOURCE_ORDER_ONLY does not establish chronology. Save edits to a new snapshot folder.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.recording = QComboBox()
        for key, rec in sorted(intake.model.recordings.items()):
            source = intake.model.sources[rec.source_id]
            self.recording.addItem(f'{source.source_file} / {rec.original_identity} / {rec.surveyor_id or "UNKNOWN"}', key)
        layout.addWidget(self.recording)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        self.pan = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.pan)
        splitter.addWidget(self.canvas)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(('Source order', 'Original time', 'Normalized UTC',
            'Continuity / flags', 'Disposition / role', 'Transect', 'Observation_ID', 'Decision_ID'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        splitter.addWidget(self.table)
        layout.addWidget(splitter, 1)
        self.unresolved_only = QCheckBox('Show unresolved observations only (excludes reviewed transitions)')
        layout.addWidget(self.unresolved_only)
        form = QHBoxLayout()
        self.plan = QComboBox()
        self.plan.addItem('Select official Transect_ID…', None)
        for plan in intake.survey.plans:
            if plan.transect_id:
                self.plan.addItem(plan.transect_id, plan.transect_id)
        self.direction = QComboBox()
        self.direction.addItems(['UNKNOWN', 'FORWARD', 'REVERSE', 'MIXED'])
        self.reviewer, self.reason = QLineEdit(), QLineEdit()
        self.reviewer.setPlaceholderText('Reviewer (required)')
        self.reason.setPlaceholderText('Reason / evidence (required)')
        for widget in (self.plan, self.direction, self.reviewer, self.reason):
            form.addWidget(widget)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.buttons = {}
        for label, state in (('ASSIGN / REVISE', 'ASSIGNED'), ('TRANSITION', 'TRANSITION'),
                             ('AMBIGUOUS', 'AMBIGUOUS'), ('UNASSIGNED', 'UNASSIGNED'),
                             ('SPLIT BEFORE SELECTED ROW', 'SPLIT')):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=state: self.apply_action(action))
            actions.addWidget(button)
            self.buttons[state] = button
        layout.addLayout(actions)
        self.visits = QTableWidget(0, 6)
        self.visits.setHorizontalHeaderLabels(('Visit_ID', 'Transect', 'Surveyor', 'Direction', 'Observations', 'Order basis'))
        self.visits.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.visits.setMaximumHeight(150)
        self.visits.cellClicked.connect(self._inspect_visit)
        layout.addWidget(self.visits)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        for label, callback in (('SAVE REVIEW…', self._save), ('OPEN REVIEW…', self._open), ('CLOSE', self.close)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        zoom = QPushButton('ZOOM TO SELECTED OBSERVATIONS')
        zoom.clicked.connect(lambda: self.canvas.zoomToSelected(self.points) if self.points.selectedFeatureCount() else None)
        controls.addWidget(zoom)
        qa = QPushButton('RUN / INSPECT VISIT QA…')
        qa.clicked.connect(self._open_qa)
        controls.addWidget(qa)
        self._map_layers = []
        self._build_map()
        self.recording.currentIndexChanged.connect(self.refresh)
        self.unresolved_only.toggled.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._highlight)
        self.refresh()

    def _build_map(self) -> None:
        for plan in self.intake.survey.plans:
            if plan.geometry_is_null:
                continue
            geometry = QgsGeometry()
            geometry.fromWkb(bytes.fromhex(plan.geometry_wkb_hex))
            if geometry.isNull() or geometry.isEmpty():
                continue
            layer = QgsVectorLayer(QgsWkbTypes.displayString(geometry.wkbType()), plan.transect_id or 'Unsupported plan', 'memory')
            layer.setCrs(QgsCoordinateReferenceSystem(plan.crs_wkt))
            layer.dataProvider().addAttributes([QgsField('Transect_ID', QMetaType.Type.QString)])
            layer.updateFields()
            feature = QgsFeature(layer.fields())
            feature.setAttributes([plan.transect_id or 'Unsupported plan'])
            feature.setGeometry(geometry)
            layer.dataProvider().addFeatures([feature])
            layer.updateExtents()
            labeling = QgsPalLayerSettings()
            labeling.fieldName = 'Transect_ID'
            layer.setLabeling(QgsVectorLayerSimpleLabeling(labeling))
            layer.setLabelsEnabled(True)
            self._map_layers.append(layer)
        self.points = QgsVectorLayer('Point?crs=EPSG:4326', 'Raw positions — review display copy', 'memory')
        self.points.dataProvider().addAttributes([QgsField('Observation_ID', QMetaType.Type.QString)])
        self.points.updateFields()
        features = []
        for key, point in self.intake.model.normalized_observations.items():
            if point.position_status == 'valid':
                feature = QgsFeature(self.points.fields())
                feature.setAttributes([key])
                feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(point.longitude, point.latitude)))
                features.append(feature)
        self.points.dataProvider().addFeatures(features)
        self.points.updateExtents()
        self._point_fids = {f['Observation_ID']: f.id() for f in self.points.getFeatures()}
        self.canvas.setLayers([self.points] + self._map_layers)
        if features:
            self.canvas.setExtent(self.points.extent())
            self.canvas.zoomOut()

    def selected_ids(self) -> list[str]:
        return [self.table.item(row, 6).text() for row in sorted({index.row() for index in self.table.selectedIndexes()})]

    def _highlight(self) -> None:
        self.points.selectByIds([self._point_fids[key] for key in self.selected_ids() if key in self._point_fids])
        self.canvas.refresh()

    def refresh(self, *_args) -> None:
        self.table.setRowCount(0)
        membership = self.run.membership
        rec = self.recording.currentData()
        for segment in self.run.segments:
            if segment.recording_id != rec:
                continue
            for key in segment.observation_ids:
                d = membership[key]
                if self.unresolved_only.isChecked() and (d.assignment_status == 'ASSIGNED' or d.activity_role == 'TRANSITION'):
                    continue
                raw, normal = self.intake.model.raw_observations[key], self.intake.model.normalized_observations[key]
                row = self.table.rowCount()
                self.table.insertRow(row)
                values = (str(raw.source_order), normal.original_timestamp or '', normal.timestamp_utc or '',
                    f'{segment.segment_id} | {segment.ordering_basis} | {", ".join(segment.flags + segment.boundary_reasons)}',
                    f'{d.assignment_status} / {d.activity_role}', d.transect_id or '', key, d.decision_id or '(unreviewed)')
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
        visits = self.run.visits
        self.visits.setRowCount(len(visits))
        for row, visit in enumerate(visits):
            for column, value in enumerate((visit.visit_id, visit.transect_id, visit.surveyor_id or 'UNKNOWN',
                                           visit.direction, str(len(visit.observation_ids)), visit.ordering_basis)):
                self.visits.setItem(row, column, QTableWidgetItem(value))
        self.status.setText(f'Revision {len(self.run.history)}: {len(membership)} retained observations; '
                            f'{self.run.unresolved_count} unresolved; {len(visits)} Visits. '
                            'Earlier decisions remain in saved history. Unsaved changes require SAVE REVIEW.')
        if self._qa_window is not None:
            self._qa_panel.refresh_validity()

    def _open_qa(self):
        if self._qa_window is None:
            from .survey_qa_panel import SurveyQaPanel
            self._qa_window = QDialog(self)
            self._qa_window.setWindowTitle('Reviewed Visit QA — no completion classification')
            self._qa_window.resize(1300, 950)
            layout = QVBoxLayout(self._qa_window)
            self._qa_panel = SurveyQaPanel(self.intake, lambda: self.run, self._qa_window)
            layout.addWidget(self._qa_panel)
        self._qa_panel.refresh_validity()
        self._qa_window.show()
        self._qa_window.raise_()

    def apply_action(self, action: str) -> None:
        try:
            validate_review_intake(self.intake)
            ids = self.selected_ids()
            kwargs = dict(reviewer=self.reviewer.text().strip(), reason=self.reason.text().strip())
            if action == 'SPLIT':
                if len(ids) != 1:
                    raise ValueError('Select exactly one row; the reviewed boundary goes immediately before it')
                kwargs['action'] = 'SPLIT_BEFORE'
            elif action == 'ASSIGNED':
                kwargs.update(assignment_status='ASSIGNED', activity_role='SURVEY',
                              transect_id=self.plan.currentData(), direction=self.direction.currentText())
            elif action == 'TRANSITION':
                kwargs['activity_role'] = 'TRANSITION'
            else:
                kwargs['assignment_status'] = action
            self.run.decide(ids, **kwargs)
            self.refresh()
        except (ValueError, KeyError) as error:
            self.status.setText(f'Review not changed: {error}')

    def _inspect_visit(self, row: int, _column: int) -> None:
        visit = self.run.visits[row]
        self.unresolved_only.setChecked(False)
        self.recording.setCurrentIndex(self.recording.findData(visit.recording_id))
        self.table.clearSelection()
        from qgis.PyQt.QtCore import QItemSelectionModel
        for index in range(self.table.rowCount()):
            if self.table.item(index, 6).text() in visit.observation_ids:
                self.table.selectionModel().select(self.table.model().index(index, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def _save(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, 'Parent folder for a new review snapshot')
        if not parent:
            return
        name, accepted = QInputDialog.getText(self, 'Save review', 'New package name', text=f'review_r{len(self.run.history)}')
        if accepted:
            try:
                result = save_review(self.run, self.intake, Path(parent), name)
                self._saved_revision = len(self.run.history)
                self.status.setText(f'Saved and verified: {result}')
            except (ValueError, OSError) as error:
                self.status.setText(f'Cannot save review: {error}')

    def _open(self) -> None:
        if not self._confirm_unsaved():
            return
        folder = QFileDialog.getExistingDirectory(self, 'Open review snapshot (replaces unsaved review in this window)')
        if folder:
            try:
                restored = load_review(Path(folder), self.intake)
                self.run = restored
                self._saved_revision = len(restored.history)
                self.refresh()
            except (ValueError, OSError) as error:
                self.status.setText(f'Cannot restore review: {error}')

    def closeEvent(self, event) -> None:
        if not self._confirm_unsaved():
            event.ignore()
            return
        self.canvas.stopRendering()
        super().closeEvent(event)

    def _confirm_unsaved(self) -> bool:
        if len(self.run.history) == self._saved_revision:
            return True
        return QMessageBox.question(self, 'Unsaved review',
            'Discard unsaved review decisions? Choose No to return and save a snapshot.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
