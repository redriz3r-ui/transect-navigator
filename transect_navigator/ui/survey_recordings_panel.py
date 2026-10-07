"""Utilitarian Stage 3 Survey recordings workspace component."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                                QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QTableWidget, QTableWidgetItem, QInputDialog,
                                QVBoxLayout, QWidget)
from qgis.gui import QgsMapLayerComboBox

from ..integration.survey_intake import ImportOptions, SurveyIntake
from ..integration.survey_intake_storage import load_survey_intake, save_survey_intake
from ..io.recorded import csv_headers
from ..survey.intake import FieldMappings


class _CsvOptionsDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f'CSV recording mapping — {path.name}')
        layout = QFormLayout(self)
        headers = csv_headers(path)
        self.x, self.y = QComboBox(self), QComboBox(self)
        self.timestamp, self.accuracy = QComboBox(self), QComboBox(self)
        for combo in (self.x, self.y):
            combo.addItems(headers)
        if self.y.count() > 1:
            self.y.setCurrentIndex(1)
        for combo in (self.timestamp, self.accuracy):
            combo.addItem('(unknown / not mapped)', '')
            for header in headers:
                combo.addItem(header, header)
        self.crs = QLineEdit('EPSG:4326', self)
        self.surveyor = QLineEdit(self)
        self.timezone = QLineEdit(self)
        self.timezone.setPlaceholderText('Optional IANA zone, e.g. America/Los_Angeles')
        layout.addRow('X / longitude field', self.x)
        layout.addRow('Y / latitude field', self.y)
        layout.addRow('Coordinate CRS', self.crs)
        layout.addRow('Timestamp field', self.timestamp)
        layout.addRow('Timestamp timezone if source is naive', self.timezone)
        layout.addRow('Reported accuracy field', self.accuracy)
        layout.addRow('Surveyor_ID (optional)', self.surveyor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def options(self) -> ImportOptions:
        return ImportOptions(
            self.x.currentText(), self.y.currentText(), self.crs.text().strip(),
            FieldMappings(timestamp=self.timestamp.currentData() or '',
                          timezone=self.timezone.text().strip(),
                          accuracy=self.accuracy.currentData() or ''),
            self.surveyor.text().strip() or None)


class SurveyRecordingsPanel(QWidget):
    """Add/inspect recordings and edit explicit Surveyor_ID without assignment."""

    COLUMNS = ('Source file', 'Format', 'Status', 'Recording', 'Observations',
               'Time range (UTC)', 'CRS', 'Surveyor_ID', 'Warnings / errors')

    def __init__(self, intake: SurveyIntake, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.intake = intake
        self._updating = False
        layout = QVBoxLayout(self)
        note = QLabel(
            'Raw sources are captured independently before any transect assignment. '
            'Surveyor_ID is user supplied; blank means unknown. Duplicate content is '
            'recorded but is not imported twice.', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.layer = QgsMapLayerComboBox(self)
        self.layer.setAllowEmptyLayer(True)
        row.addWidget(self.layer)
        add_layer = QPushButton('ADD LOADED LAYER', self)
        add_layer.clicked.connect(self._add_layer)
        row.addWidget(add_layer)
        add_files = QPushButton('ADD RECORDING FILES…', self)
        add_files.clicked.connect(self._choose_files)
        row.addWidget(add_files)
        layout.addLayout(row)
        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.itemChanged.connect(self._surveyor_edited)
        layout.addWidget(self.table)
        save_row = QHBoxLayout()
        self.status = QLabel('No recording sources imported.', self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        save_row.addWidget(self.status, 1)
        save = QPushButton('SAVE INTAKE SNAPSHOT…', self)
        save.clicked.connect(self._save)
        save_row.addWidget(save)
        open_button = QPushButton('OPEN INTAKE SNAPSHOT…', self)
        open_button.clicked.connect(self._open)
        save_row.addWidget(open_button)
        review = QPushButton('REVIEW ASSIGNMENTS…', self)
        review.clicked.connect(self._review)
        save_row.addWidget(review)
        layout.addLayout(save_row)
        self.refresh()

    def _review(self) -> None:
        from .survey_review_dialog import SurveyReviewDialog, choose_review_policy
        policy = choose_review_policy(self)
        if policy is None:
            return
        try:
            dialog = SurveyReviewDialog(self.intake, policy, self)
            dialog.exec()
        except ValueError as error:
            self.status.setText(f'Cannot open review: {error}')

    def add_files(self, paths: list[Path],
                  options: Mapping[Path, ImportOptions] | None = None) -> None:
        options = options or {}
        for path in paths:
            selected = options.get(path)
            if selected is None and path.suffix.lower() == '.csv':
                dialog = _CsvOptionsDialog(path, self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    continue
                selected = dialog.options()
            if selected is None and path.suffix.lower() == '.gpx':
                selected = ImportOptions(mappings=FieldMappings(
                    timestamp='time', hdop='hdop', pdop='pdop', satellites='sat',
                    fix='fix', elevation='ele'))
            self.intake.import_file(path, selected or ImportOptions())
        self.refresh()

    def refresh(self) -> None:
        self._updating = True
        self.table.setRowCount(0)
        recordings_by_source = {}
        for recording in self.intake.model.recordings.values():
            recordings_by_source.setdefault(recording.source_id, []).append(recording)
        for source in self.intake.model.sources.values():
            recordings = recordings_by_source.get(source.source_id) or [None]
            for recording in recordings:
                row = self.table.rowCount()
                self.table.insertRow(row)
                time_range = ''
                if recording and (recording.start_time_utc or recording.end_time_utc):
                    time_range = f'{recording.start_time_utc or "?"} — {recording.end_time_utc or "?"}'
                values = (
                    source.source_file, source.source_format,
                    recording.import_status if recording else source.import_status,
                    recording.original_identity if recording else '',
                    str(recording.observation_count if recording else 0), time_range,
                    (recording.original_crs_authid or '(custom/unknown)') if recording else '',
                    (recording.surveyor_id or '') if recording else '',
                    '; '.join(source.warnings) or source.validation_error
                    or (recording.validation_error if recording else ''),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column != 7 or recording is None:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if recording is not None:
                        item.setData(Qt.ItemDataRole.UserRole, recording.recording_id)
                    self.table.setItem(row, column, item)
        for attempt in self.intake.model.attempts:
            if attempt.status == 'SUCCESS' or (attempt.status == 'FAILED' and attempt.source_id):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (attempt.requested_name, Path(attempt.requested_name).suffix.lstrip('.').upper(),
                      attempt.status, '', '0', '', '', '', attempt.message)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        totals = self.intake.model.totals()
        self.status.setText(
            f'{totals.state}: {totals.source_count} sources, {totals.recording_count} '
            f'recordings, {totals.observation_count} observations; '
            f'{totals.failed_attempt_count} failed and {totals.duplicate_attempt_count} '
            'duplicate import attempts. No transect assignment has been performed.')
        self._updating = False

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, 'Add survey recording sources', '',
            'Recorded data (*.gpx *.csv *.gpkg *.geojson *.json)')
        if paths:
            self.add_files([Path(path) for path in paths])

    def _add_layer(self) -> None:
        layer = self.layer.currentLayer()
        self.intake.import_layer(layer)
        self.refresh()

    def _surveyor_edited(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 7:
            return
        recording_identifier = item.data(Qt.ItemDataRole.UserRole)
        if recording_identifier:
            try:
                self.intake.set_surveyor(recording_identifier, item.text() or None)
                self.refresh()
            except (ValueError, KeyError) as error:
                self.status.setText(f'Cannot update Surveyor_ID: {error}')
                self.refresh()

    def _save(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, 'Choose parent folder for intake snapshot')
        if not parent:
            return
        name, accepted = QInputDialog.getText(
            self, 'New intake snapshot name', 'Package name',
            text=f'{self.intake.survey.survey_id}_recordings')
        if not accepted:
            return
        try:
            output = save_survey_intake(self.intake, Path(parent), name)
            self.status.setText(f'Saved verified intake snapshot: {output}')
        except (ValueError, OSError) as error:
            self.status.setText(f'Cannot save intake snapshot: {error}')

    def _open(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Open Survey intake snapshot')
        if not folder:
            return
        try:
            self.intake = load_survey_intake(Path(folder), self.intake.survey,
                                              project=self.intake.project)
            self.refresh()
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self.status.setText(f'Cannot open intake snapshot: {error}')
