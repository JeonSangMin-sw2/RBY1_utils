from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np

try:
    import rby1_sdk.dynamics as rd
except ImportError:
    rd = None

from rby1_analyzer.storage.database import Database


def find_root_link(urdf_path: Path | str) -> str:
    """Parses URDF XML to find the root link (link with no parent joint)."""
    try:
        tree = ET.parse(str(urdf_path))
        root = tree.getroot()
        links = [link_elem.attrib["name"] for link_elem in root.findall("link") if "name" in link_elem.attrib]
        joints = root.findall("joint")
        child_links = set(
            j.find("child").attrib["link"]
            for j in joints
            if j.find("child") is not None and "link" in j.find("child").attrib
        )
        root_links = [link_name for link_name in links if link_name not in child_links]
        if root_links:
            return root_links[0]
    except Exception:
        pass
    return "base"


def _smooth_derivative(time_arr: np.ndarray, val_arr: np.ndarray) -> np.ndarray:
    """Computes a smoothed numerical derivative d(val)/d(time)."""
    n = len(time_arr)
    if n < 2:
        return np.zeros_like(val_arr)
    
    dt = np.gradient(time_arr)
    # Prevent division by 0
    dt = np.where(np.abs(dt) < 1e-6, 1e-6, dt)
    
    dval = np.gradient(val_arr, axis=0) / dt[:, None]
    
    # 5-point moving average smoothing along axis 0
    if n >= 5:
        kernel = np.array([1, 2, 3, 2, 1], dtype=float)
        kernel /= kernel.sum()
        padded = np.pad(dval, ((2, 2), (0, 0)), mode="edge")
        smoothed = np.zeros_like(dval)
        for i in range(dval.shape[1]):
            smoothed[:, i] = np.convolve(padded[:, i], kernel, mode="valid")
        return smoothed
    return dval


def matrix_to_zyx_euler(rot_matrix: np.ndarray, degrees: bool = False) -> tuple[float, float, float]:
    """Converts 3x3 rotation matrix to ZYX Euler angles (yaw, pitch, roll).
    Exact equivalent to scipy.spatial.transform.Rotation.from_matrix(rot_matrix).as_euler('zyx')
    without requiring the scipy dependency.
    """
    r02 = float(rot_matrix[0, 2])
    pitch = float(np.arcsin(np.clip(r02, -1.0, 1.0)))
    cos_pitch = np.cos(pitch)

    if abs(cos_pitch) > 1e-7:
        roll = float(np.arctan2(-rot_matrix[1, 2], rot_matrix[2, 2]))
        yaw = float(np.arctan2(-rot_matrix[0, 1], rot_matrix[0, 0]))
    else:
        # Gimbal lock (pitch = +- 90 deg)
        roll = 0.0
        yaw = float(np.arctan2(rot_matrix[1, 0], rot_matrix[1, 1]))

    if degrees:
        return float(np.degrees(yaw)), float(np.degrees(pitch)), float(np.degrees(roll))
    return float(yaw), float(pitch), float(roll)



@dataclass
class LoadedModel:
    key: str
    label: str
    urdf_path: Path
    base_link: str
    robot: Any
    joint_names: list[str]
    link_names: list[str]
    dof: int
    q_lower: list[float]
    q_upper: list[float]
    qdot_lower: list[float]
    qdot_upper: list[float]
    torque_limits: list[float]
    groups: dict[str, list[str]]


class DynamicsEngine:
    _instance: DynamicsEngine | None = None

    def __init__(self) -> None:
        self.models: dict[str, LoadedModel] = {}
        self.model_search_paths = self._determine_search_paths()
        self.gravity_vector = np.array([0.0, 0.0, 0.0, 0.0, 0.0, -9.81])
        self._scan_and_init_models()

    def _determine_search_paths(self) -> list[Path]:
        paths = []
        root = Path(__file__).resolve().parents[3]
        
        # 1. frontend public models
        p1 = root / "frontend" / "public" / "models"
        if p1.is_dir():
            paths.append(p1)
            
        # 2. frontend dist models
        p2 = root / "frontend" / "dist" / "models"
        if p2.is_dir():
            paths.append(p2)
            
        # 3. PyInstaller sys._MEIPASS
        if hasattr(sys, "_MEIPASS"):
            p3 = Path(sys._MEIPASS) / "frontend" / "dist" / "models"
            if p3.is_dir():
                paths.append(p3)
                
        # 4. robot_utils models folder
        p4 = Path("/home/rainbow/utils_ws/robot_utils/models")
        if p4.is_dir() and p4 not in paths:
            paths.append(p4)
            
        return paths

    def _find_urdf_file(self, rel_path: str) -> Path | None:
        for base in self.model_search_paths:
            candidate = base / rel_path
            if candidate.is_file():
                return candidate
        return None

    def _scan_and_init_models(self) -> None:
        if rd is None:
            return

        candidates = [
            ("rby1a_v1.2", "RBY1-A (v1.2)", "rby1a/urdf/model_v1.2.urdf", "base"),
            ("rby1a_v1.1", "RBY1-A (v1.1)", "rby1a/urdf/model_v1.1.urdf", "base"),
            ("rby1a_v1.0", "RBY1-A (v1.0)", "rby1a/urdf/model_v1.0.urdf", "base"),
            ("rby1m_v1.3", "RBY1-M (v1.3)", "rby1m/urdf/model_v1.3.urdf", "base"),
            ("rby1m_v1.2", "RBY1-M (v1.2)", "rby1m/urdf/model_v1.2.urdf", "base"),
            ("rby1m_v1.1", "RBY1-M (v1.1)", "rby1m/urdf/model_v1.1.urdf", "base"),
            ("rby1m_v1.0", "RBY1-M (v1.0)", "rby1m/urdf/model_v1.0.urdf", "base"),
            ("rby1ub", "RBY1-UB", "rby1ub/urdf/model.urdf", "base"),
            ("leader_arm", "Leader Arm", "leader_arm/model.urdf", "Base"),
        ]

        for key, label, rel_path, default_base in candidates:
            urdf_path = self._find_urdf_file(rel_path)
            if not urdf_path:
                continue
            
            root_link = find_root_link(urdf_path) or default_base
            try:
                cfg = rd.load_robot_from_urdf(str(urdf_path), root_link)
                robot = rd.Robot(cfg)
                joint_names = robot.get_joint_names()
                link_names = robot.get_link_names()
                dof = robot.get_dof()
                
                temp_state = robot.make_state(link_names, joint_names)
                
                q_low = list(robot.get_limit_q_lower(temp_state))
                q_high = list(robot.get_limit_q_upper(temp_state))
                qdot_low = list(robot.get_limit_qdot_lower(temp_state))
                qdot_high = list(robot.get_limit_qdot_upper(temp_state))
                t_lim = list(robot.get_limit_torque(temp_state))
                
                # Categorize joints
                groups: dict[str, list[str]] = {
                    "Head": [],
                    "Right Arm": [],
                    "Left Arm": [],
                    "Torso": [],
                    "Wheel": [],
                    "Other": [],
                }
                for j_name in joint_names:
                    nl = j_name.lower()
                    if "wheel" in nl or "mobility" in nl or "steering" in nl:
                        groups["Wheel"].append(j_name)
                    elif "head" in nl:
                        groups["Head"].append(j_name)
                    elif "right_arm" in nl or "r_arm" in nl or nl.startswith("right_") or nl.endswith("_r"):
                        groups["Right Arm"].append(j_name)
                    elif "left_arm" in nl or "l_arm" in nl or nl.startswith("left_") or nl.endswith("_l"):
                        groups["Left Arm"].append(j_name)
                    elif "torso" in nl:
                        groups["Torso"].append(j_name)
                    else:
                        groups["Other"].append(j_name)

                self.models[key] = LoadedModel(
                    key=key,
                    label=label,
                    urdf_path=urdf_path,
                    base_link=root_link,
                    robot=robot,
                    joint_names=joint_names,
                    link_names=link_names,
                    dof=dof,
                    q_lower=q_low,
                    q_upper=q_high,
                    qdot_lower=qdot_low,
                    qdot_upper=qdot_high,
                    torque_limits=t_lim,
                    groups={k: sorted(v) for k, v in groups.items() if v},
                )
            except Exception as e:
                print(f"[DynamicsEngine] Warning: Failed to load model {key}: {e}", file=sys.stderr)

    def get_model(self, key_or_name: str) -> LoadedModel | None:
        if not key_or_name:
            return self.models.get("rby1a_v1.2") or next(iter(self.models.values()), None)
            
        norm = key_or_name.lower().replace("-", "").replace(" ", "_").replace("(", "").replace(")", "")
        if key_or_name in self.models:
            return self.models[key_or_name]
            
        for k, m in self.models.items():
            if k == norm or norm in k or norm in m.label.lower():
                return m
        return self.models.get("rby1a_v1.2") or next(iter(self.models.values()), None)

    def get_model_catalog(self) -> list[dict[str, Any]]:
        result = []
        for m in self.models.values():
            result.append({
                "key": m.key,
                "label": m.label,
                "dof": m.dof,
                "base_link": m.base_link,
                "joint_names": m.joint_names,
                "link_names": m.link_names,
                "groups": m.groups,
                "limits": {
                    "q_lower": m.q_lower,
                    "q_upper": m.q_upper,
                    "qdot_lower": m.qdot_lower,
                    "qdot_upper": m.qdot_upper,
                    "torque": m.torque_limits,
                }
            })
        return result

    def calculate_single_pose(
        self,
        model_key: str,
        joint_angles: dict[str, float],
        ref_link: str | None = None,
        target_link: str | None = None,
        is_deg: bool = False,
    ) -> dict[str, Any]:
        loaded = self.get_model(model_key)
        if not loaded:
            raise ValueError(f"Robot model not found: {model_key}")

        robot = loaded.robot
        state = robot.make_state(loaded.link_names, loaded.joint_names)
        state.set_gravity(self.gravity_vector)

        # Build q array in radians
        q = np.zeros(loaded.dof)
        for idx, j_name in enumerate(loaded.joint_names):
            val = joint_angles.get(j_name, 0.0)
            q[idx] = math.radians(val) if is_deg else val

        state.set_q(q)
        robot.compute_forward_kinematics(state)

        # Reference and target links
        actual_ref = ref_link if ref_link in loaded.link_names else loaded.base_link
        if actual_ref not in loaded.link_names:
            actual_ref = loaded.link_names[0]
            
        actual_target = target_link if target_link in loaded.link_names else loaded.link_names[-1]
        
        ref_idx = loaded.link_names.index(actual_ref)
        target_idx = loaded.link_names.index(actual_target)

        # Transformation matrix T (4x4)
        T = robot.compute_transformation(state, ref_idx, target_idx)
        x_m, y_m, z_m = float(T[0, 3]), float(T[1, 3]), float(T[2, 3])

        # Euler ZYX
        try:
            rot_matrix = T[:3, :3]
            yaw_d, pitch_d, roll_d = matrix_to_zyx_euler(rot_matrix, degrees=True)
            yaw_r, pitch_r, roll_r = matrix_to_zyx_euler(rot_matrix, degrees=False)
        except Exception:
            yaw_d = pitch_d = roll_d = 0.0
            yaw_r = pitch_r = roll_r = 0.0

        # Dynamics (Gravity Torques)
        gravity_torques = robot.compute_gravity_term(state)
        tau_limits = loaded.torque_limits

        joint_torques = []
        max_ratio = 0.0
        max_ratio_joint = "N/A"

        for idx, j_name in enumerate(loaded.joint_names):
            grav_t = float(gravity_torques[idx])
            lim_t = float(tau_limits[idx])
            pos_rad = float(q[idx])
            pos_deg = float(math.degrees(pos_rad))

            ratio = 0.0
            if 0 < lim_t < 1e10:
                ratio = abs(grav_t) / lim_t
                if ratio > max_ratio:
                    max_ratio = ratio
                    max_ratio_joint = j_name

            status_str = "OK"
            if ratio >= 1.0:
                status_str = "OVERLOAD"
            elif ratio >= 0.8:
                status_str = "WARNING"

            joint_torques.append({
                "joint": j_name,
                "position_rad": pos_rad,
                "position_deg": pos_deg,
                "gravity_torque": grav_t,
                "torque_limit": lim_t if lim_t < 1e10 else None,
                "load_ratio": ratio,
                "status": status_str,
            })

        # Center of Mass relative to reference link
        com_pos = robot.compute_center_of_mass(state, ref_idx)
        com_x, com_y, com_z = float(com_pos[0]), float(com_pos[1]), float(com_pos[2])

        return {
            "model_key": loaded.key,
            "model_label": loaded.label,
            "ref_link": actual_ref,
            "target_link": actual_target,
            "kinematics": {
                "position": {
                    "x_m": x_m, "y_m": y_m, "z_m": z_m,
                    "x_mm": x_m * 1000.0, "y_mm": y_m * 1000.0, "z_mm": z_m * 1000.0,
                },
                "rotation": {
                    "roll_deg": float(roll_d), "pitch_deg": float(pitch_d), "yaw_deg": float(yaw_d),
                    "roll_rad": float(roll_r), "pitch_rad": float(pitch_r), "yaw_rad": float(yaw_r),
                },
                "matrix": T.tolist(),
            },
            "center_of_mass": {"x_m": com_x, "y_m": com_y, "z_m": com_z},
            "dynamics": {
                "joint_torques": joint_torques,
                "max_gravity_ratio": max_ratio,
                "max_gravity_joint": max_ratio_joint,
            },
        }

    def calculate_trajectory_dynamics(
        self,
        db: Database,
        artifact_id: int,
        model_key: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        max_samples: int = 1500,
    ) -> dict[str, Any]:
        """Calculates trajectory-level inverse dynamics and compares actual vs theoretical values."""
        loaded = self.get_model(model_key or "")
        if not loaded:
            raise ValueError(f"Robot model not found: {model_key}")

        robot = loaded.robot
        joint_names = loaded.joint_names
        dof = loaded.dof

        # Query time range
        with db.connect() as conn:
            time_row = conn.execute(
                "SELECT MIN(sample_time) as min_t, MAX(sample_time) as max_t "
                "FROM chart_samples WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if not time_row or time_row["min_t"] is None:
                raise ValueError("No chart samples found for artifact")

            t_min = float(start_time if start_time is not None else time_row["min_t"])
            t_max = float(end_time if end_time is not None else time_row["max_t"])

            # Query all distinct sample times in window
            time_rows = conn.execute(
                "SELECT DISTINCT sample_time FROM chart_samples "
                "WHERE artifact_id=? AND sample_time>=? AND sample_time<=? "
                "ORDER BY sample_time",
                (artifact_id, t_min, t_max),
            ).fetchall()

        all_times = [float(r["sample_time"]) for r in time_rows]
        if not all_times:
            return {"times": [], "joints": {}, "anomalies": []}

        # Subsample if exceeds max_samples
        if len(all_times) > max_samples:
            step = len(all_times) / float(max_samples)
            sampled_times = [all_times[int(i * step)] for i in range(max_samples)]
            if sampled_times[-1] != all_times[-1]:
                sampled_times.append(all_times[-1])
        else:
            sampled_times = all_times

        t_arr = np.array(sampled_times, dtype=float)
        n_samples = len(t_arr)

        # Retrieve series data from database
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT sample_time, name, value FROM chart_samples "
                "WHERE artifact_id=? AND sample_time>=? AND sample_time<=?",
                (artifact_id, t_min, t_max),
            ).fetchall()

        signal_time_map: dict[str, dict[float, float]] = {}
        for r in rows:
            name = str(r["name"])
            t = float(r["sample_time"])
            v = float(r["value"])
            if name not in signal_time_map:
                signal_time_map[name] = {}
            signal_time_map[name][t] = v

        def get_interpolated_signal(name: str) -> np.ndarray:
            if name not in signal_time_map or not signal_time_map[name]:
                return np.zeros(n_samples, dtype=float)
            pts = sorted(signal_time_map[name].items())
            x_src = np.array([p[0] for p in pts], dtype=float)
            y_src = np.array([p[1] for p in pts], dtype=float)
            return np.interp(t_arr, x_src, y_src)

        # Build trajectory matrices Q, Qdot, Qddot, Tau_act, Tau_target_ff
        Q = np.zeros((n_samples, dof), dtype=float)
        Q_target = np.zeros((n_samples, dof), dtype=float)
        Qdot_measured = np.zeros((n_samples, dof), dtype=float)
        Qdot_target = np.zeros((n_samples, dof), dtype=float)
        Tau_act = np.zeros((n_samples, dof), dtype=float)
        Tau_target_ff = np.zeros((n_samples, dof), dtype=float)
        has_vel_measured = np.zeros(dof, dtype=bool)

        for j_idx, j_name in enumerate(joint_names):
            # Pos
            Q[:, j_idx] = get_interpolated_signal(f"{j_name}_pos")
            Q_target[:, j_idx] = get_interpolated_signal(f"{j_name}_target_pos")
            # Vel
            if f"{j_name}_vel" in signal_time_map:
                Qdot_measured[:, j_idx] = get_interpolated_signal(f"{j_name}_vel")
                has_vel_measured[j_idx] = True
            Qdot_target[:, j_idx] = get_interpolated_signal(f"{j_name}_target_vel")
            # Torque
            Tau_act[:, j_idx] = get_interpolated_signal(f"{j_name}_tq")
            Tau_target_ff[:, j_idx] = get_interpolated_signal(f"{j_name}_target_ff_tq")

        # Estimate velocities where not measured
        Qdot_computed = _smooth_derivative(t_arr, Q)
        Qdot = np.where(has_vel_measured[None, :], Qdot_measured, Qdot_computed)

        # Compute Acceleration Qddot
        Qddot = _smooth_derivative(t_arr, Qdot)

        # Run Inverse Dynamics & Gravity calculation along the trajectory
        Tau_model = np.zeros((n_samples, dof), dtype=float)
        Tau_grav = np.zeros((n_samples, dof), dtype=float)

        state = robot.make_state(loaded.link_names, loaded.joint_names)
        state.set_gravity(self.gravity_vector)

        for k in range(n_samples):
            state.set_q(Q[k])
            state.set_qdot(Qdot[k])
            state.set_qddot(Qddot[k])

            robot.compute_forward_kinematics(state)
            robot.compute_diff_forward_kinematics(state)
            robot.compute_2nd_diff_forward_kinematics(state)
            robot.compute_inverse_dynamics(state)

            Tau_model[k] = state.get_tau()
            Tau_grav[k] = robot.compute_gravity_term(state)

        # Residual / External Disturbance Torque: Tau_act - Tau_model
        Tau_ext = Tau_act - Tau_model

        # Tracking Errors: Target - Actual
        Pos_error = Q_target - Q
        Vel_error = Qdot_target - Qdot

        # Automated Anomaly Detection
        anomalies: list[dict[str, Any]] = []
        
        for j_idx, j_name in enumerate(joint_names):
            lim_t = float(loaded.torque_limits[j_idx])
            ext_t = Tau_ext[:, j_idx]
            pos_e = Pos_error[:, j_idx]
            act_t = Tau_act[:, j_idx]
            
            # 1. External Torque / Collision / Jam anomaly threshold
            tq_thresh = max(10.0, 0.25 * lim_t) if 0 < lim_t < 1e10 else 15.0
            ext_exceed = np.abs(ext_t) > tq_thresh
            
            # Find contiguous segments of anomaly
            in_seg = False
            seg_start_idx = 0
            for i, flag in enumerate(ext_exceed):
                if flag and not in_seg:
                    in_seg = True
                    seg_start_idx = i
                elif not flag and in_seg:
                    in_seg = False
                    seg_dur = t_arr[i - 1] - t_arr[seg_start_idx]
                    if seg_dur >= 0.02 or (i - seg_start_idx) >= 3:
                        peak_val = float(np.max(np.abs(ext_t[seg_start_idx:i])))
                        anomalies.append({
                            "id": f"jam_{j_name}_{int(t_arr[seg_start_idx]*1000)}",
                            "joint": j_name,
                            "type": "external_load_jam",
                            "severity": "major" if peak_val > tq_thresh * 1.5 else "minor",
                            "start_time": float(t_arr[seg_start_idx]),
                            "end_time": float(t_arr[i - 1]),
                            "peak_value": peak_val,
                            "unit": "Nm",
                            "summary": (
                                f"외란/잔차 토크 이상: {peak_val:.1f} Nm "
                                f"(정격/임계 {tq_thresh:.1f} Nm 초과, 충돌/기계적 걸림 의심)"
                            ),
                        })
            if in_seg:
                peak_val = float(np.max(np.abs(ext_t[seg_start_idx:])))
                anomalies.append({
                    "id": f"jam_{j_name}_{int(t_arr[seg_start_idx]*1000)}",
                    "joint": j_name,
                    "type": "external_load_jam",
                    "severity": "major" if peak_val > tq_thresh * 1.5 else "minor",
                    "start_time": float(t_arr[seg_start_idx]),
                    "end_time": float(t_arr[-1]),
                    "peak_value": peak_val,
                    "unit": "Nm",
                    "summary": (
                        f"외란/잔차 토크 지속 초과: {peak_val:.1f} Nm "
                        f"(충돌/걸림/과부하 의심)"
                    ),
                })

            # 2. Position tracking error spikes (> 0.087 rad ~ 5 deg)
            pos_thresh_rad = 0.087
            pos_exceed = np.abs(pos_e) > pos_thresh_rad
            in_seg = False
            for i, flag in enumerate(pos_exceed):
                if flag and not in_seg:
                    in_seg = True
                    seg_start_idx = i
                elif not flag and in_seg:
                    in_seg = False
                    peak_deg = float(math.degrees(np.max(np.abs(pos_e[seg_start_idx:i]))))
                    anomalies.append({
                        "id": f"pos_err_{j_name}_{int(t_arr[seg_start_idx]*1000)}",
                        "joint": j_name,
                        "type": "tracking_error",
                        "severity": "minor" if peak_deg < 15.0 else "major",
                        "start_time": float(t_arr[seg_start_idx]),
                        "end_time": float(t_arr[i - 1]),
                        "peak_value": peak_deg,
                        "unit": "deg",
                        "summary": f"목표 위치 추종 오차 급증: {peak_deg:.1f}° (Big Position Error 전조)",
                    })

            # 3. Torque Limit Overload (> 95% limit)
            if 0 < lim_t < 1e10:
                overload_flags = np.abs(act_t) >= 0.95 * lim_t
                if np.any(overload_flags):
                    max_overload = float(np.max(np.abs(act_t)))
                    anomalies.append({
                        "id": f"overload_{j_name}",
                        "joint": j_name,
                        "type": "torque_limit_exceeded",
                        "severity": "major",
                        "start_time": float(t_arr[np.argmax(overload_flags)]),
                        "end_time": float(t_arr[np.argmax(overload_flags)]),
                        "peak_value": max_overload,
                        "unit": "Nm",
                        "summary": f"관절 토크 정격 한계 도달/초과: {max_overload:.1f} Nm (한계치 {lim_t:.1f} Nm)",
                    })

        # Sort anomalies by start_time
        anomalies.sort(key=lambda a: a["start_time"])

        # Format joint series for frontend
        joints_payload: dict[str, dict[str, list[float]]] = {}
        for j_idx, j_name in enumerate(joint_names):
            lim_t = float(loaded.torque_limits[j_idx])
            joints_payload[j_name] = {
                "pos_deg": [float(math.degrees(v)) for v in Q[:, j_idx]],
                "target_pos_deg": [float(math.degrees(v)) for v in Q_target[:, j_idx]],
                "vel_deg_s": [float(math.degrees(v)) for v in Qdot[:, j_idx]],
                "target_vel_deg_s": [float(math.degrees(v)) for v in Qdot_target[:, j_idx]],
                "acc_deg_s2": [float(math.degrees(v)) for v in Qddot[:, j_idx]],
                "tau_actual": [float(v) for v in Tau_act[:, j_idx]],
                "tau_model": [float(v) for v in Tau_model[:, j_idx]],
                "tau_gravity": [float(v) for v in Tau_grav[:, j_idx]],
                "tau_target_ff": [float(v) for v in Tau_target_ff[:, j_idx]],
                "tau_ext": [float(v) for v in Tau_ext[:, j_idx]],
                "pos_error_deg": [float(math.degrees(v)) for v in Pos_error[:, j_idx]],
                "vel_error_deg_s": [float(math.degrees(v)) for v in Vel_error[:, j_idx]],
                "torque_limit": lim_t if lim_t < 1e10 else None,
            }

        return {
            "model_key": loaded.key,
            "model_label": loaded.label,
            "times": [float(t) for t in t_arr],
            "joints": joints_payload,
            "joint_names": joint_names,
            "link_names": loaded.link_names,
            "base_link": loaded.base_link,
            "groups": loaded.groups,
            "anomalies": anomalies,
        }


_engine_singleton: DynamicsEngine | None = None


def get_dynamics_engine() -> DynamicsEngine:
    global _engine_singleton
    if _engine_singleton is None:
        _engine_singleton = DynamicsEngine()
    return _engine_singleton
