from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from rby1_analyzer.core.security import SessionAuthority
from rby1_analyzer.dynamics.engine import get_dynamics_engine
from rby1_analyzer.jobs.manager import JobManager
from rby1_analyzer.main import RuntimeContext, create_app
from rby1_analyzer.storage.cases import CaseStore


def test_dynamics_engine_model_catalog() -> None:
    engine = get_dynamics_engine()
    models = engine.get_model_catalog()
    assert len(models) > 0
    keys = [m["key"] for m in models]
    assert "rby1a_v1.2" in keys or "rby1a_v1.0" in keys or "rby1m_v1.2" in keys


def test_dynamics_engine_calculate_single_pose() -> None:
    engine = get_dynamics_engine()
    models = engine.get_model_catalog()
    first_model = models[0]["key"]

    res = engine.calculate_single_pose(
        model_key=first_model,
        joint_angles={},
        is_deg=True,
    )
    assert res["model_key"] == first_model
    assert "kinematics" in res
    assert "position" in res["kinematics"]
    assert "matrix" in res["kinematics"]
    assert len(res["kinematics"]["matrix"]) == 4
    assert "dynamics" in res
    assert len(res["dynamics"]["joint_torques"]) > 0
    assert "max_gravity_ratio" in res["dynamics"]


def test_dynamics_api_routes(tmp_path: Path) -> None:
    authority = SessionAuthority()
    session_token = authority.exchange(authority.bootstrap_token)
    assert session_token is not None

    runtime = RuntimeContext(
        port=0,
        authority=authority,
        cases=CaseStore(tmp_path),
        jobs=JobManager(),
    )
    app = create_app(runtime)
    client = TestClient(app, headers={"Authorization": f"Bearer {session_token}"})

    # 1. GET models
    res_models = client.get("/api/v3/dynamics/models")
    assert res_models.status_code == 200
    data = res_models.json()
    assert "models" in data
    assert len(data["models"]) > 0

    # 2. POST calculate pose
    model_key = data["models"][0]["key"]
    res_calc = client.post(
        "/api/v3/dynamics/calculate",
        json={
            "model": model_key,
            "joint_angles": {"torso_0": 0.0},
            "is_deg": True,
        },
    )
    assert res_calc.status_code == 200
    calc_data = res_calc.json()
    assert "kinematics" in calc_data
    assert "dynamics" in calc_data
    assert len(calc_data["dynamics"]["joint_torques"]) > 0
