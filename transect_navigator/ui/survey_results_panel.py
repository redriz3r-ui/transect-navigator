"""Operational overview over cached QA; filtering never runs geometry calculations."""
from dataclasses import asdict
import json
from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)
from qgis.gui import QgsMapCanvas, QgsMapToolPan

from ..integration.survey_results import assert_results_current, build_results, status_layer
from ..integration.survey_results_storage import load_results, save_results
from ..survey.model import utc_time
from ..survey.results import CompletionPolicy, STATUSES, scope_evidence


class SurveyResultsPanel(QWidget):
    """Owns one detached map layer; parent QA panel supplies the current batch/review."""
    def __init__(self, qa_panel, parent=None):
        super().__init__(parent)
        self.qa_panel = qa_panel
        self.result = self.policy = self.layer = None
        self.evidence_history = ()
        layout = QVBoxLayout(self)
        note = QLabel('Operational completion is separate from deviation QA and is not a scientific or regulatory '
            'determination. NOT_SURVEYED means no accepted work in imported data. Coverage is confirmed sampled support.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel('Select an explicit completion policy, then build results.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        overview, configuration = QWidget(), QWidget()
        tabs.addTab(overview, 'Survey RESULTS')
        tabs.addTab(configuration, 'Policy / unresolved evidence')
        config = QVBoxLayout(configuration)
        form = QFormLayout()
        self.policy_id, self.reviewer, self.reason = QLineEdit(), QLineEdit(), QLineEdit()
        self.policy_id.setPlaceholderText('Explicit project policy ID')
        self.support, self.endpoint, self.tolerance = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for control, minimum, maximum, value in ((self.support, .001, 100, 100),
                (self.endpoint, 0, 10000, 0), (self.tolerance, 0, .1, .02)):
            control.setDecimals(3)
            control.setRange(minimum, maximum)
            control.setValue(value)
            control.valueChanged.connect(self._policy_edited)
        for label, control in (('Policy ID', self.policy_id), ('Selected by / reviewer', self.reviewer),
                ('Selection reason', self.reason), ('Required progress support (%)', self.support),
                ('Endpoint support tolerance (m)', self.endpoint), ('Numerical tolerance (m)', self.tolerance)):
            form.addRow(label, control)
        for edit in (self.policy_id, self.reviewer, self.reason):
            edit.textChanged.connect(self._policy_edited)
        config.addLayout(form)
        self.apply_policy = QPushButton('SELECT THIS OPERATIONAL POLICY')
        self.apply_policy.clicked.connect(self._select_policy)
        config.addWidget(self.apply_policy)
        note = QLabel('Unscoped unresolved observations require REVIEW on every plan. Select exact rows and all '
            'relevant plans, then record relevance. This does not assign GPS observations. Empty plan selection '
            'means explicitly unrelated and is forbidden for AMBIGUOUS evidence. Reason and reviewer are required.')
        note.setWordWrap(True)
        config.addWidget(note)
        self.unresolved = qa_panel._table(('Observation_ID', 'Source', 'Order', 'Disposition', 'Original time'))
        self.unresolved.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.plans = QListWidget()
        self.plans.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for plan in qa_panel.intake.survey.plans:
            self.plans.addItem(plan.transect_id)
        split = QSplitter()
        split.addWidget(self.unresolved)
        split.addWidget(self.plans)
        config.addWidget(split)
        self.scope_reason = QLineEdit()
        self.scope_reason.setPlaceholderText('Reason for relevance / unrelated disposition')
        config.addWidget(self.scope_reason)
        self.scope_button = QPushButton('RECORD SELECTED EVIDENCE RELEVANCE')
        self.scope_button.clicked.connect(self._scope)
        config.addWidget(self.scope_button)
        body = QVBoxLayout(overview)
        buttons = QHBoxLayout()
        self.build_button = QPushButton('BUILD / REFRESH SURVEY RESULTS')
        self.build_button.clicked.connect(self.recompute)
        self.save_button = QPushButton('SAVE RESULTS SNAPSHOT…')
        self.save_button.clicked.connect(self._save)
        self.open_button = QPushButton('OPEN RESULTS SNAPSHOT…')
        self.open_button.clicked.connect(self._open)
        for button in (self.build_button, self.save_button, self.open_button):
            buttons.addWidget(button)
        body.addLayout(buttons)
        self.summary = QLabel('No current Survey totals.')
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        body.addWidget(QLabel('Operational map: blue COMPLETE · ochre INCOMPLETE · purple REVIEW · grey NOT_SURVEYED'))
        filters = QHBoxLayout()
        self.status_filter, self.surveyor_filter = QComboBox(), QComboBox()
        self.status_filter.addItems(('All statuses',) + STATUSES)
        self.surveyor_filter.addItem('All surveyors', '*')
        self.id_filter = QLineEdit()
        self.id_filter.setPlaceholderText('Filter Transect_ID')
        self.review_only = QCheckBox('Unresolved / REVIEW only')
        for control in (self.status_filter, self.id_filter, self.surveyor_filter, self.review_only):
            filters.addWidget(control)
        body.addLayout(filters)
        self.status_filter.currentIndexChanged.connect(self.filter_rows)
        self.surveyor_filter.currentIndexChanged.connect(self.filter_rows)
        self.id_filter.textChanged.connect(self.filter_rows)
        self.review_only.toggled.connect(self.filter_rows)
        self.table = qa_panel._table(('Transect', 'Status', 'Planned m', 'Visits', 'Observations',
            'Progress m', 'Corridor m', 'Track m', 'Unresolved', 'QA state', 'Basis'))
        self.table.cellClicked.connect(self.inspect)
        self.canvas = QgsMapCanvas()
        self.pan = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.pan)
        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(self.canvas)
        body.addWidget(split)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        body.addWidget(self.detail)
        self.refresh_validity()

    def _policy_edited(self, *_):
        self.policy = None
        self.refresh_validity()

    def _select_policy(self):
        try:
            self.policy = CompletionPolicy(self.policy_id.text(), self.support.value(), self.endpoint.value(),
                self.tolerance.value(), self.reviewer.text(), self.reason.text(), utc_time())
            self.refresh_validity()
        except ValueError as error:
            self.status.setText(str(error))

    def _scope(self):
        try:
            ids = [self.unresolved.item(i.row(), 0).text() for i in self.unresolved.selectionModel().selectedRows()]
            self.evidence_history = scope_evidence(self.evidence_history, self.qa_panel.batch,
                self.qa_panel.get_review().membership, ids, [item.text() for item in self.plans.selectedItems()],
                reviewer=self.reviewer.text(), reason=self.scope_reason.text())
            self.refresh_validity()
        except (ValueError, AttributeError) as error:
            self.status.setText(f'Cannot record relevance: {error}')

    def refresh_validity(self, *_):
        if not hasattr(self, 'save_button'):
            return
        self.save_button.setEnabled(False)
        if self.result is None:
            self.status.setText('Select an explicit policy and build results. No current Survey snapshot.')
            self._unresolved_rows()
            return
        try:
            if self.policy is None:
                raise ValueError('Select the edited completion policy explicitly')
            assert_results_current(self.result, self.qa_panel.batch, self.qa_panel.get_review(),
                self.qa_panel.intake, self.policy, self.evidence_history, options=self.qa_panel.options())
            self.status.setText('CURRENT — operational statuses, not compliance. Select a row for QA / Visit detail.')
            self.save_button.setEnabled(True)
        except (ValueError, AttributeError) as error:
            self.status.setText(f'STALE / REVIEW — rebuild after resolving dependencies: {error}')
            self.summary.setText('STALE — current project totals unavailable; historical snapshot is not mixed with current data.')
            self.table.setRowCount(0)
            self.canvas.setLayers([])
            self.layer = None
            self.detail.clear()
        self._unresolved_rows()

    def _unresolved_rows(self):
        if not hasattr(self, 'unresolved'):
            return
        run, intake = self.qa_panel.get_review(), self.qa_panel.intake
        rows = []
        for key, d in sorted(run.membership.items()):
            if d.assignment_status != 'ASSIGNED' and d.activity_role != 'TRANSITION':
                raw = intake.model.raw_observations[key]
                normal = intake.model.normalized_observations[key]
                rows.append((key, intake.model.sources[raw.source_id].source_file, raw.source_order,
                             d.assignment_status, normal.original_timestamp))
        self.qa_panel._fill(self.unresolved, rows)

    def recompute(self):
        try:
            if self.policy is None:
                raise ValueError('Select a completion policy explicitly in Policy / unresolved evidence')
            result = build_results(self.qa_panel.batch, self.qa_panel.get_review(), self.qa_panel.intake,
                self.policy, self.evidence_history, options=self.qa_panel.options())
            layer = status_layer(result, self.qa_panel.intake.survey)
            self.result, self.layer = result, layer
            self._populate()
            self.refresh_validity()
        except (ValueError, AttributeError) as error:
            self.refresh_validity()
            self.status.setText(f'Cannot build Survey results: {error}')

    def _populate(self):
        s = self.result.summary
        self.summary.setText(f'{s.planned_count} plans — ' + ', '.join(f'{k}: {v}' for k, v in s.status_counts) +
            f'\nPlanned: {s.total_planned_length_m} m (known subtotal {s.known_planned_length_m:.3f} m; '
            f'{s.unknown_length_count} unknown). Track: {s.accepted_track_length_m} m. '
            f'Progress: {s.confirmed_progress_m:.3f} m / {s.progress_lower_bound_percent}% confirmed lower bound. '
            f'QA corridor: {s.confirmed_corridor_m:.3f} m / {s.corridor_lower_bound_percent}% lower bound. '
            f'Unresolved: {s.unresolved_observation_count}; unscoped: {s.unscoped_observation_count}; '
            f'unknown support: {s.unknown_support_count}. {s.processing_state}')
        self.surveyor_filter.blockSignals(True)
        self.surveyor_filter.clear()
        self.surveyor_filter.addItem('All surveyors', '*')
        for value in sorted({s for r in self.result.transects for s in r.qa.surveyor_ids}, key=lambda x: x or ''):
            self.surveyor_filter.addItem(value or 'UNKNOWN', value)
        self.surveyor_filter.blockSignals(False)
        self.canvas.setDestinationCrs(self.layer.crs())
        self.canvas.setLayers([self.layer])
        self.canvas.setExtent(self.layer.extent())
        self.canvas.refresh()
        self.filter_rows()

    def filter_rows(self, *_):
        if self.result is None or self.layer is None:
            return
        state, surveyor = self.status_filter.currentText(), self.surveyor_filter.currentData()
        self.visible_rows = tuple(r for r in self.result.transects
            if (state == 'All statuses' or r.final_status == state)
            and self.id_filter.text().casefold() in r.transect_id.casefold()
            and (surveyor == '*' or surveyor in r.qa.surveyor_ids)
            and (not self.review_only.isChecked() or r.final_status == 'REVIEW' or r.unresolved_observation_ids))
        self.qa_panel._fill(self.table, [(r.transect_id, r.final_status, r.qa.planned_length_m, len(r.qa.visit_ids),
            r.qa.accepted_observation_count, r.confirmed_progress_m, r.confirmed_corridor_m,
            r.qa.summary.recorded_length_m if r.qa.summary else None, len(r.unresolved_observation_ids),
            r.qa_state, '; '.join(r.status_basis)) for r in self.visible_rows])

    def inspect(self, row, _column):
        result = self.visible_rows[row]
        batch = self.qa_panel.batch
        index = next(i for i, t in enumerate(batch.transects) if t.transect_id == result.transect_id)
        self.qa_panel._inspect_transect(index, 0)
        visits = [asdict(v) for v in batch.visits if v.visit.transect_id == result.transect_id]
        self.detail.setPlainText(json.dumps({'operational_result': asdict(result), 'visits_events_provenance': visits,
            'relevance_history': [asdict(s) for s in self.evidence_history if set(s.observation_ids) & set(result.unresolved_observation_ids)]}, indent=2))
        self.layer.selectByIds([f.id() for f in self.layer.getFeatures() if f['Transect_ID'] == result.transect_id])
        self.canvas.zoomToSelected(self.layer)

    def _save(self):
        self.refresh_validity()
        if not self.save_button.isEnabled():
            return
        parent = QFileDialog.getExistingDirectory(self, 'Parent folder for results snapshot')
        if not parent:
            return
        name, ok = QInputDialog.getText(self, 'Results snapshot', 'New folder name', text='survey_results')
        if ok:
            try:
                path = save_results(self.result, self.qa_panel.batch, self.qa_panel.get_review(), self.qa_panel.intake, Path(parent), name)
                self.status.setText(f'Saved and verified: {path}')
            except (ValueError, OSError) as error:
                self.status.setText(f'Cannot save results: {error}')

    def _open(self):
        folder = QFileDialog.getExistingDirectory(self, 'Results snapshot matching current review / QA settings')
        if folder:
            try:
                result, batch = load_results(Path(folder), self.qa_panel.get_review(), self.qa_panel.intake, self.qa_panel.options())
                self.qa_panel.batch = batch
                self.qa_panel._populate()
                policy = result.policy
                for control, value in ((self.policy_id, policy.policy_id), (self.reviewer, policy.selected_by), (self.reason, policy.reason)):
                    control.setText(value)
                for control, value in ((self.support, policy.required_support_percent), (self.endpoint, policy.endpoint_tolerance_m), (self.tolerance, policy.numerical_tolerance_m)):
                    control.setValue(value)
                self.result, self.policy, self.evidence_history = result, policy, result.evidence_history
                self.layer = status_layer(result, self.qa_panel.intake.survey)
                self._populate()
                self.refresh_validity()
            except (ValueError, OSError) as error:
                self.status.setText(f'Cannot open results: {error}')
