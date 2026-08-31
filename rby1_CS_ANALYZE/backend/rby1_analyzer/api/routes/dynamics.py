from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from rby1_analyzer.api.deps import bearer_token
from rby1_analyzer.api.routes.incidents import _case_db
from rby1_analyzer.dynamics.engine import get_dynamics_engine

router = APIRouter(
    prefix="/api/v3",
    tags=["v3-dynamics"],
    dependencies=[Depends(bearer_token)],
)


class PoseCalculateRequest(BaseModel):
    model: str = Field(default="rby1a_v1.2")
    joint_angles: dict[str, float] = Field(default_factory=dict)
    ref_link: str | None = None
    target_link: str | None = None
    is_deg: bool = True


@router.get("/dynamics/models")
def get_dynamics_models() -> dict[str, Any]:
    engine = get_dynamics_engine()
    return {"models": engine.get_model_catalog()}


@router.post("/dynamics/calculate")
def calculate_single_pose(body: PoseCalculateRequest) -> dict[str, Any]:
    engine = get_dynamics_engine()
    try:
        return engine.calculate_single_pose(
            model_key=body.model,
            joint_angles=body.joint_angles,
            ref_link=body.ref_link,
            target_link=body.target_link,
            is_deg=body.is_deg,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dynamics calculation failed: {e}",
        )


@router.get("/cases/{case_id}/csvs/{artifact_id}/dynamics")
def get_trajectory_dynamics(
    case_id: str,
    artifact_id: int,
    request: Request,
    model: Annotated[str | None, Query(description="Robot model key, e.g. rby1a_v1.2")] = None,
    start: Annotated[float | None, Query(description="Start time")] = None,
    end: Annotated[float | None, Query(description="End time")] = None,
    max_samples: Annotated[int, Query(description="Maximum samples to return", ge=50, le=5000)] = 1500,
) -> dict[str, Any]:
    db = _case_db(request, case_id)
    engine = get_dynamics_engine()

    target_model = model
    if not target_model:
        # Try to infer model from CSV metadata or fallback
        with db.connect() as conn:
            evidence_rows = conn.execute(
                "SELECT signature FROM events WHERE artifact_id IN ("
                "  SELECT id FROM artifacts WHERE case_id=? AND kind='log'"
                ") LIMIT 100",
                (case_id,),
            ).fetchall()
            evidence = [str(r["signature"]) for r in evidence_rows]

            series_rows = conn.execute(
                "SELECT DISTINCT name FROM chart_samples WHERE artifact_id=?",
                (artifact_id,),
            ).fetchall()
            series_names = [str(r["name"]) for r in series_rows]

        from rby1_analyzer.api.routes.csv_analysis import infer_robot_model
        inferred = infer_robot_model(evidence, series_names)
        m = inferred.get("model", "a")
        v = inferred.get("version", "v1.2")
        target_model = f"rby1{m}_{v}"

    try:
        return engine.calculate_trajectory_dynamics(
            db=db,
            artifact_id=artifact_id,
            model_key=target_model,
            start_time=start,
            end_time=end,
            max_samples=max_samples,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Trajectory dynamics calculation failed: {e}",
        )
