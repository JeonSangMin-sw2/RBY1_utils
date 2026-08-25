from __future__ import annotations

from collections import defaultdict

from rby1_analyzer.storage.cases import CaseStore

from .service import ChartPoint, ChartSeries


DEFAULT_SERIES = (
    "power_5v",
    "power_12v",
    "power_24v",
    "power_48v",
    "control_manager_state",
    "control_state",
    "right_wheel_pos",
    "right_wheel_target_pos",
    "left_wheel_pos",
    "left_wheel_target_pos",
    "right_wheel_cur",
    "left_wheel_cur",
)


class SQLiteChartRepository:
    def __init__(self, cases: CaseStore):
        self.cases = cases

    def load_series(
        self,
        case_id: str,
        names: set[str] | None,
        start: float,
        end: float,
    ) -> list[ChartSeries]:
        db = self.cases.open(case_id)
        with db.connect() as connection:
            available_rows = connection.execute(
                "SELECT name,kind,COUNT(*) AS count FROM chart_samples "
                "GROUP BY name,kind ORDER BY name"
            ).fetchall()
            available = {row["name"]: row["kind"] for row in available_rows}
            if names is None:
                selected = [name for name in DEFAULT_SERIES if name in available]
                if not selected:
                    selected = list(available)[:12]
            else:
                selected = sorted(name for name in names if name in available)
            grouped: dict[str, list[ChartPoint]] = defaultdict(list)
            for name in selected:
                rows = connection.execute(
                    "SELECT sample_time,value FROM chart_samples "
                    "WHERE name=? AND sample_time BETWEEN ? AND ? ORDER BY sample_time",
                    (name, start, end),
                ).fetchall()
                grouped[name].extend(
                    ChartPoint(float(row["sample_time"]), float(row["value"])) for row in rows
                )
        return [
            ChartSeries(name, available[name], tuple(grouped[name])) for name in selected
        ]
