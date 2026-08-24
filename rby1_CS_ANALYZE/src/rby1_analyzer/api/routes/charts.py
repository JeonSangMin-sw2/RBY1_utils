from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from rby1_analyzer.charts import ChartSeries, DenseWindowError, window_series


class ChartRepository(Protocol):
    def load_series(
        self,
        case_id: str,
        names: set[str] | None,
        start: float,
        end: float,
    ) -> list[ChartSeries]: ...


def get_chart_repository(request: Request) -> ChartRepository:
    repository = getattr(request.app.state, "chart_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail={"code": "chart_store_unavailable"})
    return repository


router = APIRouter(prefix="/api/cases/{case_id}/charts", tags=["charts"])


@router.get("/window")
def chart_window(
    case_id: str,
    start: float,
    end: float,
    series: Annotated[list[str] | None, Query()] = None,
    max_points: Annotated[int, Query(ge=4, le=2_000)] = 2_000,
    repository: ChartRepository = Depends(get_chart_repository),
) -> dict[str, object]:
    selected = set(series) if series else None
    try:
        window = window_series(
            repository.load_series(case_id, selected, start, end),
            start=start,
            end=end,
            selected=selected,
            max_points=max_points,
        )
    except DenseWindowError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "too_dense_requires_zoom",
                "required_points": error.required_points,
                "suggested_window_seconds": error.suggested_window_seconds,
            },
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "invalid_chart_window", "message": str(error)}) from error
    return {
        "start": start,
        "end": end,
        "series": [
            {
                "name": item.name,
                "kind": item.kind,
                "nan_count": item.nan_count,
                "points": [[point.time, point.value] for point in item.points],
            }
            for item in window
        ],
    }
