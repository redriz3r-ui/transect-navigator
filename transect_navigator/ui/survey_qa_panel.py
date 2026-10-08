"""Small reviewed-QA runner/inspector; no project status or map redesign."""
from pathlib import Path
import json

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..integration.survey_qa import assert_batch_current, run_batch_qa
from ..integration.survey_qa_storage import load_batch_qa, save_batch_qa
from ..qaqc.events import EventOptions
from ..qaqc.statistics import StatisticsOptions
from ..survey.qa import QaOptions


class SurveyQaPanel(QWidget):
    """Get current review dynamically so edits/open-review cannot leave a hidden cache."""
    def __init__(self, intake, get_review, parent=None):
        super().__init__(parent)
        self.intake, self.get_review = intake, get_review
        self.batch = None
        layout = QVBoxLayout(self)
        note = QLabel('Only confirmed SURVEY Visits enter QA. Thresholds are configurable QA settings, '
                      'not compliance standards. Coverage is sampled support, not operational completion. '
                      'GNSS values retain source units and are not subtracted from deviation.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.controls = {}
        for key, label, default, minimum in (
            ('a', 'Threshold A (m)', 3, 0), ('b', 'Threshold B (m)', 5, 0), ('c', 'Threshold C (m)', 10, 0),
            ('station_gap', 'Coverage maximum station gap (m)', 25, .001),
            ('time_gap', 'Coverage maximum time gap (s)', 60, .001),
            ('event_threshold', 'Event exceedance threshold (m)', 5, 0),
            ('event_duration', 'Event minimum duration (s)', 5, 0),
            ('event_span', 'Event minimum station span (m)', 0, 0),
            ('event_time_gap', 'Event maximum time gap (s)', 60, .001),
            ('event_space_gap', 'Event maximum spatial gap (m)', 50, .001)):
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(minimum, 1_000_000)
            spin.setValue(default)
            spin.valueChanged.connect(self.refresh_validity)
            self.controls[key] = spin
            form.addRow(label, spin)
        self.duration_enabled = QCheckBox('Apply minimum event duration')
        self.duration_enabled.setChecked(True)
        self.span_enabled = QCheckBox('Apply minimum event station span')
        for checkbox in (self.duration_enabled, self.span_enabled):
            checkbox.toggled.connect(self.refresh_validity)
            form.addRow(checkbox)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.run_button = QPushButton('RUN VISIT QA')
        self.run_button.clicked.connect(self.run_qa)
        self.save_button = QPushButton('SAVE QA SNAPSHOT…')
        self.save_button.clicked.connect(self._save)
        open_button = QPushButton('OPEN QA SNAPSHOT…')
        open_button.clicked.connect(self._open)
        for button in (self.run_button, self.save_button, open_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel('No QA run yet.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.transects = self._table(('Transect', 'Visits', 'Accepted / evaluated', 'Planned m',
            'Track m', 'Mean m', 'Median m', 'RMSE m', 'P95 m', 'Maximum m',
            'Progress m', 'Corridor m', 'Events', 'Flags / errors'))
        self.visits = self._table(('Visit_ID', 'Transect', 'Direction', 'State', 'Count',
            'Track m', 'Mean m', 'Progress m', 'Corridor m', 'Events', 'Flags / errors'))
        layout.addWidget(self.transects)
        layout.addWidget(self.visits)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail)
        self.transects.cellClicked.connect(self._inspect_transect)
        self.visits.cellClicked.connect(self._inspect_visit)
        self.refresh_validity()

    @staticmethod
    def _table(columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        return table

    def options(self):
        value = {key: spin.value() for key, spin in self.controls.items()}
        return QaOptions(StatisticsOptions((value['a'], value['b'], value['c']), value['station_gap'], value['time_gap']),
            EventOptions(value['event_threshold'], value['event_duration'] if self.duration_enabled.isChecked() else None,
                value['event_span'] if self.span_enabled.isChecked() else None,
                value['event_time_gap'], value['event_space_gap']))

    def refresh_validity(self, *_args):
        if not hasattr(self, 'status'):
            return
        self.save_button.setEnabled(False)
        if self.batch is None:
            self.status.setText('No QA run yet.')
            return
        try:
            assert_batch_current(self.batch, self.get_review(), self.intake, self.options())
            failures = sum(v.processing_state == 'FAILED' for v in self.batch.visits)
            errors = sum(bool(t.errors) for t in self.batch.transects)
            self.status.setText(f'CURRENT dependency snapshot: {len(self.batch.visits)} Visits; '
                f'{failures} failed Visits; {errors} transects with processing errors. '
                'Inspect flags for unknown chronology and partial evaluated results.')
            self.save_button.setEnabled(True)
        except ValueError as error:
            self.status.setText(f'STALE / INVALID — displayed results are historical only: {error}')

    def run_qa(self):
        try:
            result = run_batch_qa(self.get_review(), self.intake, self.options())
            self.batch = result
            self._populate()
            self.refresh_validity()
        except (ValueError, OSError) as error:
            self.refresh_validity()
            self.status.setText(f'QA not run: {error}. Previous results were not overwritten.')

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                text = 'unknown' if value is None else f'{value:.6f}' if isinstance(value, float) else str(value)
                table.setItem(r, c, QTableWidgetItem(text))

    def _populate(self):
        rows = []
        for t in self.batch.transects:
            s = t.summary
            d = s.observed_deviation if s else None
            rows.append((t.transect_id, len(t.visit_ids), f'{t.accepted_observation_count} / {t.evaluated_observation_count}',
                t.planned_length_m, s.recorded_length_m if s else None,
                *([d.mean, d.median, d.rmse, d.p95, d.maximum] if d else [None] * 5),
                t.progress_support.supported_length_m if t.progress_support else None,
                s.coverage.supported_length_m if s else None, len(t.event_uids), '; '.join(t.flags + t.errors)))
        self._fill(self.transects, rows)
        self._visit_rows = self.batch.visits
        self._show_visits()
        self.detail.setPlainText('Select a transect or Visit to inspect full statistics, thresholds, events and exact provenance.')

    def _show_visits(self):
        self._fill(self.visits, [(v.visit.visit_id, v.visit.transect_id, v.visit.direction,
            v.processing_state, len(v.visit.observation_ids), v.summary.recorded_length_m if v.summary else None,
            v.summary.observed_deviation.mean if v.summary else None,
            v.progress_support.supported_length_m if v.progress_support else None,
            v.summary.coverage.supported_length_m if v.summary else None, len(v.events), '; '.join(v.flags + (v.error,)))
            for v in self._visit_rows])

    def _inspect_transect(self, row, _column):
        from dataclasses import asdict
        t = self.batch.transects[row]
        self._visit_rows = tuple(v for v in self.batch.visits if v.visit.transect_id == t.transect_id)
        self._show_visits()
        self.detail.setPlainText(json.dumps(asdict(t), indent=2))

    def _inspect_visit(self, row, _column):
        from dataclasses import asdict
        self.detail.setPlainText(json.dumps(asdict(self._visit_rows[row]), indent=2))

    def _save(self):
        if self.batch is None:
            return
        parent = QFileDialog.getExistingDirectory(self, 'Parent folder for new QA snapshot')
        if not parent:
            return
        name, accepted = QInputDialog.getText(self, 'QA snapshot', 'New package name', text='reviewed_qa')
        if accepted:
            try:
                path = save_batch_qa(self.batch, self.get_review(), self.intake, Path(parent), name, options=self.options())
                self.status.setText(f'Saved and verified: {path}')
            except (ValueError, OSError) as error:
                self.status.setText(f'Cannot save QA: {error}')

    def _open(self):
        folder = QFileDialog.getExistingDirectory(self, 'Open QA snapshot matching current review and QA settings')
        if folder:
            try:
                result = load_batch_qa(Path(folder), self.get_review(), self.intake, self.options())
                self.batch = result
                self._populate()
                self.refresh_validity()
            except (ValueError, OSError) as error:
                self.status.setText(f'Cannot load QA: {error}')
