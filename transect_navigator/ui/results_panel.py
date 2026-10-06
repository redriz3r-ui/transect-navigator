"""Read-only results and an unsmoothed Qt scatter profile; no plotting dependency."""
from qgis.PyQt.QtCore import Qt, QPointF, QRectF
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import (QWidget, QVBoxLayout, QLabel, QComboBox, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView)
from ..presentation.results import (COLORS, NOTICE, profile_points, profile_bounds,
                                    summary_rows, class_rules)
from .analysis_export_panel import AnalysisExportPanel


class DeviationProfile(QWidget):
    """Plot individual measurements by station; never infer a path through the samples."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.points = ()
        self.length_m = 0.0
        self.signed = False
        self.setMinimumSize(400, 200)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('white'))
        plot = QRectF(75, 25, max(1, self.width()-100), max(1, self.height()-75))
        xmax, ymin, ymax = profile_bounds(self.points, self.length_m)

        def position(x, y):
            return QPointF(plot.left() + plot.width()*x/xmax,
                           plot.bottom() - plot.height()*(y-ymin)/(ymax-ymin))

        painter.setPen(QPen(QColor('#555555'), 1))
        painter.drawRect(plot)
        painter.drawLine(position(0, 0), position(xmax, 0))
        for fraction in (0, .5, 1):
            x, y = xmax*fraction, ymin+(ymax-ymin)*fraction
            painter.drawText(QPointF(position(x, 0).x()-10, plot.bottom()+18), f'{x:.3g}')
            painter.drawText(QPointF(5, position(0, y).y()+4), f'{y:.3g}')
        painter.drawText(QPointF(75, 17), ('Signed' if self.signed else 'Absolute') + ' deviation (m)')
        painter.drawText(QPointF(75, self.height()-8), 'Distance along planned transect (m)')
        painter.save()
        painter.setClipRect(plot.adjusted(-3, -3, 3, 3))
        for category, color in enumerate(COLORS):
            painter.setPen(QPen(QColor(color), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            points = QPolygonF([position(p.station_m, p.deviation_m)
                               for p in self.points if p.class_index == category])
            if points:
                painter.drawPoints(points)
        painter.restore()
        if not self.points:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, 'No defined measurements to plot')
        painter.end()


class ResultsPanel(QWidget):
    """Latest successful analysis snapshot, independent of project-layer lifetime."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.snapshot = None
        layout = QVBoxLayout(self)
        self.title = QLabel('Run an observation comparison to see results.', self)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(['Measurement', 'Result'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 2)
        self.mode = QComboBox(self)
        self.mode.addItems(['Absolute deviation', 'Signed deviation (+ left / − right)'])
        self.mode.currentIndexChanged.connect(self._profile)
        layout.addWidget(self.mode)
        self.profile = DeviationProfile(self)
        layout.addWidget(self.profile, 1)
        self.legend = QLabel(self)
        self.legend.setTextFormat(Qt.TextFormat.PlainText)
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.export_panel = AnalysisExportPanel(self)
        layout.addWidget(self.export_panel)

    def set_result(self, snapshot: dict) -> None:
        """Accept calculation records and private export copies, never project-layer references."""
        self.snapshot = snapshot
        self.export_panel.set_analysis(snapshot.get('export'))
        summary = snapshot['summary']
        rows = summary_rows(summary, snapshot['events'], snapshot['spacing_m'])
        self.table.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(label))
            self.table.setItem(row, 1, QTableWidgetItem(value))
        self.title.setText(f"{snapshot['group']}\nAnalysis completed {snapshot['time']}")
        self.legend.setText('Profile colors: ' + '; '.join(
            f'{rule.color}: {rule.label}' for rule in class_rules('deviation', summary.options.thresholds_m)))
        self._profile()

    def _profile(self) -> None:
        if self.snapshot is None:
            return
        snapshot = self.snapshot
        summary = snapshot['summary']
        signed = self.mode.currentIndex() == 1
        self.profile.points = profile_points(snapshot['rows'], summary.options.thresholds_m, signed)
        self.profile.length_m = summary.planned_length_m
        self.profile.signed = signed
        self.profile.update()
        omitted = len(snapshot['rows'])-len(self.profile.points)
        self.note.setText(f'{NOTICE} {omitted} undefined measurements omitted from this profile. '
            'Dots are individual observations, not a connected or smoothed track. '
            'Statistics are observation-weighted. Coverage is sampled progress support, not protocol compliance. '
            'Reported GNSS uncertainty remains separate from observed deviation. '
            + ('Raw track available. ' if snapshot['has_track'] else 'Raw track unavailable; chronology was not invented. ')
            + 'Stationary events may have no line geometry; inspect their attribute table. '
            'Map outputs are temporary: export the analysis package below and retain raw files.')
