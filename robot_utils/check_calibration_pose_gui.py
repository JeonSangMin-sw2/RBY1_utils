#!/usr/bin/env python3
"""
Check Calibration Pose Utility GUI
Target WS: utils_ws/robot_utils

Features:
- Reference Link Selection: t5 (link_torso_5) or base (base)
- Target Link Selection: Tool Flange (ee_right/ee_left) or Custom TCP (Euler ZYX x,y,z,roll,pitch,yaw)
- Init Pose Button: Moves to joint preparation pose
- Symmetrical Dual Arm Pose Editor:
  * Rotated Center Frame & Symmetric Y-axis Offset Position Calculations
  * X, Y, Z, Offset, Center RPY, Right RPY, Left RPY inputs for arms
  * Mirroring Checkbox for automatic symmetric Left arm pose calculations
  * Relative Jog Buttons (+X, -X, +Y, -Y, +Z, -Z, +Roll, -Roll, +Pitch, -Pitch, +Yaw, -Yaw) in Reference Frame
- MOVE Button: Moves target links with offset spacing (auto-executes Init Pose if not already done)
- STOP Button: Immediately cancels/stops robot motion safely without QThread destruction crashes
"""

import sys
import os
import time
import math
import warnings
import numpy as np
from scipy.spatial.transform import Rotation as R

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QGroupBox, QComboBox, QLineEdit, QGridLayout,
    QFormLayout, QMessageBox, QFrame, QCheckBox
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QColor

import rby1_sdk as rby

D2R = np.pi / 180.0
M_YZ = np.diag([1.0, -1.0, 1.0])


def rot_x(rad):
    c, s = np.cos(rad), np.sin(rad)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def rot_y(rad):
    c, s = np.cos(rad), np.sin(rad)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def rot_z(rad):
    c, s = np.cos(rad), np.sin(rad)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def reflect_rotation_yz(R_mat):
    """Symmetric reflection of 3D rotation matrix across Y-Z plane."""
    return M_YZ @ R_mat @ M_YZ


def matrix_to_euler_zyx_deg(R_mat):
    """Converts 3x3 rotation matrix to Euler ZYX degrees suppressing Gimbal lock warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yw, p, r = R.from_matrix(R_mat).as_euler('zyx', degrees=True)
    return r, p, yw


def euler_zyx_to_matrix(x_m, y_m, z_m, roll_deg, pitch_deg, yaw_deg):
    """Computes 4x4 matrix from Euler ZYX angles (Roll: X, Pitch: Y, Yaw: Z)."""
    roll_r = math.radians(roll_deg)
    pitch_r = math.radians(pitch_deg)
    yaw_r = math.radians(yaw_deg)

    cr, sr = math.cos(roll_r), math.sin(roll_r)
    cp, sp = math.cos(pitch_r), math.sin(pitch_r)
    cy, sy = math.cos(yaw_r), math.sin(yaw_r)

    T = np.eye(4, dtype=np.float64)
    T[0, 0] = cy * cp
    T[0, 1] = cy * sp * sr - sy * cr
    T[0, 2] = cy * sp * cr + sy * sr
    T[0, 3] = x_m

    T[1, 0] = sy * cp
    T[1, 1] = sy * sp * sr + cy * cr
    T[1, 2] = sy * sp * cr - cy * sr
    T[1, 3] = y_m

    T[2, 0] = -sp
    T[2, 1] = cp * sr
    T[2, 2] = cp * cr
    T[2, 3] = z_m

    return T


class RobotCommandWorker(QThread):
    finished_signal = Signal(bool, str)
    log_signal = Signal(str)

    def __init__(self, task_type, robot, model_name, params, parent=None):
        super().__init__(parent)
        self.task_type = task_type
        self.robot = robot
        self.model_name = model_name
        self.params = params

    def run(self):
        try:
            if not self.robot or not self.robot.is_connected():
                self.finished_signal.emit(False, "Robot is not connected.")
                return

            # Ensure Control Manager is enabled
            cm_state = self.robot.get_control_manager_state()
            if cm_state.state in [rby.ControlManagerState.State.MinorFault, rby.ControlManagerState.State.MajorFault]:
                self.log_signal.emit("[ControlManager] Control manager in fault state. Resetting...")
                self.robot.reset_fault_control_manager()
                time.sleep(1.0)

            cm_state = self.robot.get_control_manager_state()
            if cm_state.state != rby.ControlManagerState.State.Enabled:
                self.log_signal.emit("[ControlManager] Enabling control manager...")
                self.robot.enable_control_manager()
                time.sleep(1.0)

            if self.task_type == "INIT_POSE":
                self.execute_init_pose()
            elif self.task_type == "MOVE_CARTESIAN":
                if self.params.get("auto_init", False):
                    self.log_signal.emit("Executing Init Pose prior to Cartesian move...")
                    self.execute_init_pose()
                    time.sleep(1.0)
                self.execute_cartesian_move()
            elif self.task_type == "MOVE_TORSO":
                self.execute_torso_move()

        except Exception as e:
            self.finished_signal.emit(False, f"Command Error: {str(e)}")

    def execute_init_pose(self):
        self.log_signal.emit("Moving to Joint Preparation (Init) Pose...")

        q_torso = np.array([0, 30, -60, 30, 0, 0], dtype=np.float64) * D2R
        q_right = np.array([-45, -30, 0, -90, 0, 45, 0], dtype=np.float64) * D2R
        q_left = np.array([-45, 30, 0, -90, 0, 45, 0], dtype=np.float64) * D2R

        q_ready = np.concatenate([q_torso, q_right, q_left])
        min_time = self.params.get("min_time", 5.0)

        rc = rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(
                rby.JointPositionCommandBuilder()
                .set_position(q_ready)
                .set_minimum_time(min_time)
            )
        )

        rv = self.robot.send_command(rc, 10).get()
        if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            raise RuntimeError(f"Failed to move to Init Pose. Code: {rv.finish_code}")

        self.finished_signal.emit(True, "Init Pose successfully completed.")

    def execute_cartesian_move(self):
        impedance = self.params.get("impedance", False)
        mode_str = "IMPEDANCE (compliant)" if impedance else "POSITION (stiff)"
        self.log_signal.emit(f"Moving arms to Cartesian pose [{mode_str} mode]...")

        ref_link = self.params["ref_link"]  # "link_torso_5" or "base"
        T_right_target = self.params["T_right"]
        T_left_target = self.params["T_left"]
        min_time = self.params.get("min_time", 5.0)

        def build_arm_command(link_name, T_target):
            header = rby.CommandHeaderBuilder()
            if impedance:
                # Long control-hold keeps the arm compliant at the target until
                # the next command / STOP, so it can be physically pushed.
                header.set_control_hold_time(300.0)

                stiffness = np.full(7, self.params.get("imp_stiffness", 150.0), dtype=np.float64)
                damping_ratio = float(self.params.get("imp_damping", 1.0))
                torque_limit = np.full(7, self.params.get("imp_torque", 100.0), dtype=np.float64)

                arm = rby.CartesianImpedanceControlCommandBuilder()
                arm.add_target(
                    ref_link, link_name, T_target.astype(np.float64),
                    0.1, float(np.pi / 4), 0.5, float(np.pi)  # lin/ang vel & accel limits
                )
                arm.set_joint_stiffness(stiffness)
                arm.set_joint_damping_ratio(damping_ratio)
                arm.set_joint_torque_limit(torque_limit)
                arm.set_stop_position_tracking_error(1e-3)
                arm.set_stop_orientation_tracking_error(1e-4)
                arm.set_minimum_time(min_time)
                arm.set_command_header(header)
                return arm

            header.set_control_hold_time(0.5)
            arm = rby.CartesianCommandBuilder()
            arm.add_target(ref_link, link_name, T_target.astype(np.float32), 1.5, np.pi * 1.5, 1.0)
            arm.set_stop_position_tracking_error(1e-3)
            arm.set_stop_orientation_tracking_error(1e-4)
            arm.set_minimum_time(min_time)
            arm.set_command_header(header)
            return arm

        body = rby.BodyComponentBasedCommandBuilder()
        body.set_right_arm_command(build_arm_command("ee_right", T_right_target))
        body.set_left_arm_command(build_arm_command("ee_left", T_left_target))

        rc = rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(body)
        )

        rv = self.robot.send_command(rc, 10).get()
        if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            raise RuntimeError(f"Failed to move Cartesian Pose. Code: {rv.finish_code}")

        self.finished_signal.emit(True, f"Cartesian Move ({mode_str}) successfully completed.")

    def execute_torso_move(self):
        self.log_signal.emit("Jogging Torso (T5) in base frame...")

        ref_link = self.params["ref_link"]        # "base"
        target_link = self.params["target_link"]  # "link_torso_5"
        T_target = self.params["T_target"]
        min_time = self.params.get("min_time", 5.0)

        # Torso motions are kept slow for safety.
        linear_vel = 0.3
        angular_vel = np.pi * 0.5
        accel = 0.5

        header = rby.CommandHeaderBuilder()
        header.set_control_hold_time(0.5)

        torso_cart = rby.CartesianCommandBuilder()
        torso_cart.add_target(ref_link, target_link, T_target.astype(np.float32), linear_vel, angular_vel, accel)
        torso_cart.set_stop_position_tracking_error(1e-3)
        torso_cart.set_stop_orientation_tracking_error(1e-4)
        torso_cart.set_minimum_time(min_time)
        torso_cart.set_command_header(header)

        torso = rby.TorsoCommandBuilder().set_command(torso_cart)

        body = rby.BodyComponentBasedCommandBuilder()
        body.set_torso_command(torso)

        rc = rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(body)
        )

        rv = self.robot.send_command(rc, 10).get()
        if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            raise RuntimeError(f"Failed to jog Torso (T5). Code: {rv.finish_code}")

        self.finished_signal.emit(True, "Torso (T5) jog successfully completed.")


class CalibrationPoseCheckerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Calibration Pose Checker (utils_ws)")
        self.resize(1200, 680)

        self.robot = None
        self.init_pose_done = False
        self.worker = None
        self.active_workers = []

        self.init_ui()

        # Polling Timer for Live Robot State (10 Hz)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_robot_state)
        self.poll_timer.start(100)

    def init_ui(self):
        main_layout = QHBoxLayout()

        # Left Column: Controls
        left_panel = QVBoxLayout()

        # 1. Connection Group
        conn_box = QGroupBox("Robot Connection")
        conn_layout = QGridLayout()
        self.ip_input = QLineEdit("192.168.30.1:50051")
        self.model_input = QComboBox()
        self.model_input.addItems(["a", "m"])
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet("background-color: #ff9900; color: black; font-weight: bold;")
        self.btn_connect.clicked.connect(self.toggle_connection)

        conn_layout.addWidget(QLabel("IP Address:"), 0, 0)
        conn_layout.addWidget(self.ip_input, 0, 1)
        conn_layout.addWidget(QLabel("Model:"), 0, 2)
        conn_layout.addWidget(self.model_input, 0, 3)
        conn_layout.addWidget(self.btn_connect, 1, 0, 1, 4)
        conn_box.setLayout(conn_layout)
        left_panel.addWidget(conn_box)

        # 2. Reference Link & Target Link Configuration
        link_box = QGroupBox("Link & Frame Selection")
        link_layout = QGridLayout()

        # Reference Link Selection
        self.ref_link_sel = QComboBox()
        self.ref_link_sel.addItems(["t5 (link_torso_5)", "base (base)"])
        self.ref_link_sel.currentTextChanged.connect(self.on_ref_link_changed)

        # Target Link Mode
        self.target_mode_sel = QComboBox()
        self.target_mode_sel.addItems(["Tool Flange (ee_right / ee_left)", "Custom TCP"])
        self.target_mode_sel.currentTextChanged.connect(self.on_target_mode_changed)

        link_layout.addWidget(QLabel("Reference Link:"), 0, 0)
        link_layout.addWidget(self.ref_link_sel, 0, 1)
        link_layout.addWidget(QLabel("Target Link Mode:"), 0, 2)
        link_layout.addWidget(self.target_mode_sel, 0, 3)

        link_box.setLayout(link_layout)
        left_panel.addWidget(link_box)

        # 3. Custom TCP Input Group (Euler ZYX)
        self.tcp_box = QGroupBox("Custom TCP Offset wrt Flange (Euler ZYX)")
        tcp_layout = QGridLayout()

        self.tcp_x = QLineEdit("0.0")
        self.tcp_y = QLineEdit("0.0")
        self.tcp_z = QLineEdit("0.0")
        self.tcp_roll = QLineEdit("0.0")
        self.tcp_pitch = QLineEdit("0.0")
        self.tcp_yaw = QLineEdit("0.0")

        tcp_layout.addWidget(QLabel("X (mm):"), 0, 0)
        tcp_layout.addWidget(self.tcp_x, 0, 1)
        tcp_layout.addWidget(QLabel("Y (mm):"), 0, 2)
        tcp_layout.addWidget(self.tcp_y, 0, 3)
        tcp_layout.addWidget(QLabel("Z (mm):"), 0, 4)
        tcp_layout.addWidget(self.tcp_z, 0, 5)

        tcp_layout.addWidget(QLabel("Roll (deg):"), 1, 0)
        tcp_layout.addWidget(self.tcp_roll, 1, 1)
        tcp_layout.addWidget(QLabel("Pitch (deg):"), 1, 2)
        tcp_layout.addWidget(self.tcp_pitch, 1, 3)
        tcp_layout.addWidget(QLabel("Yaw (deg):"), 1, 4)
        tcp_layout.addWidget(self.tcp_yaw, 1, 5)

        self.tcp_box.setLayout(tcp_layout)
        self.tcp_box.setEnabled(False)
        left_panel.addWidget(self.tcp_box)

        # 4. Symmetrical Target Pose Parameters
        pose_box = QGroupBox("Symmetrical Pose Parameters")
        pose_layout = QGridLayout()

        self.chk_mirror = QCheckBox("Mirror Left Arm Angles from Right Arm")
        self.chk_mirror.setChecked(True)
        self.chk_mirror.toggled.connect(self.on_mirror_toggled)
        pose_layout.addWidget(self.chk_mirror, 0, 0, 1, 4)

        self.target_x = QLineEdit("350.0")
        self.target_y = QLineEdit("0.0")
        self.target_z = QLineEdit("300.0")
        self.target_offset = QLineEdit("160.0")

        pose_layout.addWidget(QLabel("Base Center X (mm):"), 1, 0)
        pose_layout.addWidget(self.target_x, 1, 1)
        pose_layout.addWidget(QLabel("Base Center Y (mm):"), 1, 2)
        pose_layout.addWidget(self.target_y, 1, 3)

        pose_layout.addWidget(QLabel("Base Center Z (mm):"), 2, 0)
        pose_layout.addWidget(self.target_z, 2, 1)
        pose_layout.addWidget(QLabel("Arm Spacing Offset (mm):"), 2, 2)
        pose_layout.addWidget(self.target_offset, 2, 3)

        # Target Center Frame Orientation (Euler ZYX)
        self.center_roll = QLineEdit("0.0")
        self.center_pitch = QLineEdit("0.0")
        self.center_yaw = QLineEdit("0.0")

        pose_layout.addWidget(QLabel("Center Roll (deg):"), 3, 0)
        pose_layout.addWidget(self.center_roll, 3, 1)
        pose_layout.addWidget(QLabel("Center Pitch (deg):"), 3, 2)
        pose_layout.addWidget(self.center_pitch, 3, 3)
        pose_layout.addWidget(QLabel("Center Yaw (deg):"), 3, 4)
        pose_layout.addWidget(self.center_yaw, 3, 5)

        # Right Arm Relative Orientation (Euler ZYX)
        self.r_roll = QLineEdit("90.0")
        self.r_pitch = QLineEdit("0.0")
        self.r_yaw = QLineEdit("90.0")

        self.r_roll.textChanged.connect(self.update_mirrored_left_pose)
        self.r_pitch.textChanged.connect(self.update_mirrored_left_pose)
        self.r_yaw.textChanged.connect(self.update_mirrored_left_pose)

        pose_layout.addWidget(QLabel("Right Roll (deg):"), 4, 0)
        pose_layout.addWidget(self.r_roll, 4, 1)
        pose_layout.addWidget(QLabel("Right Pitch (deg):"), 4, 2)
        pose_layout.addWidget(self.r_pitch, 4, 3)
        pose_layout.addWidget(QLabel("Right Yaw (deg):"), 4, 4)
        pose_layout.addWidget(self.r_yaw, 4, 5)

        # Left Arm Relative Orientation (Euler ZYX)
        self.l_roll = QLineEdit("-90.0")
        self.l_pitch = QLineEdit("0.0")
        self.l_yaw = QLineEdit("-90.0")

        pose_layout.addWidget(QLabel("Left Roll (deg):"), 5, 0)
        pose_layout.addWidget(self.l_roll, 5, 1)
        pose_layout.addWidget(QLabel("Left Pitch (deg):"), 5, 2)
        pose_layout.addWidget(self.l_pitch, 5, 3)
        pose_layout.addWidget(QLabel("Left Yaw (deg):"), 5, 4)
        pose_layout.addWidget(self.l_yaw, 5, 5)

        # Reflect Current Pose button directly under parameters
        self.btn_reflect = QPushButton("REFLECT CURRENT POSE")
        self.btn_reflect.setMinimumHeight(38)
        self.btn_reflect.setFont(QFont("Arial", 10, QFont.Bold))
        self.btn_reflect.setStyleSheet("background-color: #6f42c1; color: white;")
        self.btn_reflect.clicked.connect(self.reflect_current_pose)
        pose_layout.addWidget(self.btn_reflect, 6, 0, 1, 6)

        pose_box.setLayout(pose_layout)
        left_panel.addWidget(pose_box)

        # Initialize mirroring state
        self.on_mirror_toggled(True)

        # 5. Relative Jog Controls (Updates Symmetrical Pose Parameters & Robot)
        jog_box = QGroupBox("Relative Jog Center Target Frame")
        jog_layout = QGridLayout()

        # Step size inputs
        jog_layout.addWidget(QLabel("Pos Step (mm):"), 0, 0)
        self.pos_step_input = QLineEdit("10.0")
        jog_layout.addWidget(self.pos_step_input, 0, 1)

        jog_layout.addWidget(QLabel("Rot Step (deg):"), 0, 2)
        self.rot_step_input = QLineEdit("5.0")
        jog_layout.addWidget(self.rot_step_input, 0, 3)

        jog_btns = [
            ('+X', 1, 0), ('-X', 1, 1),
            ('+Y', 1, 2), ('-Y', 1, 3),
            ('+Z', 2, 0), ('-Z', 2, 1),
            ('+Offset', 2, 2), ('-Offset', 2, 3),
            ('+Roll', 3, 0), ('-Roll', 3, 1),
            ('+Pitch', 3, 2), ('-Pitch', 3, 3),
            ('+Yaw', 4, 0), ('-Yaw', 4, 1),
        ]

        for name, row, col in jog_btns:
            btn = QPushButton(name)
            btn.setMinimumHeight(32)
            btn.setFont(QFont("Arial", 10, QFont.Bold))
            if 'R' in name or 'Roll' in name or 'Pitch' in name or 'Yaw' in name:
                btn.setStyleSheet("background-color: #17a2b8; color: white;")
            else:
                btn.setStyleSheet("background-color: #007bff; color: white;")
            btn.clicked.connect(lambda *args, n=name: self.jog_relative_parameter(n))
            jog_layout.addWidget(btn, row, col)

        jog_box.setLayout(jog_layout)

        # 5b. T5 (link_torso_5) Jog in BASE Frame
        t5_box = QGroupBox("T5 Jog in BASE Frame")
        t5_layout = QGridLayout()

        t5_layout.addWidget(QLabel("Pos Step (mm):"), 0, 0)
        self.t5_pos_step_input = QLineEdit("5.0")
        t5_layout.addWidget(self.t5_pos_step_input, 0, 1)

        t5_layout.addWidget(QLabel("Rot Step (deg, 0-5):"), 0, 2)
        self.t5_rot_step_input = QLineEdit("1.0")
        t5_layout.addWidget(self.t5_rot_step_input, 0, 3)

        t5_btns = [
            ('+X', 1, 0), ('-X', 1, 1),
            ('+Y', 1, 2), ('-Y', 1, 3),
            ('+Z', 2, 0), ('-Z', 2, 1),
            ('+Roll', 3, 0), ('-Roll', 3, 1),
            ('+Pitch', 3, 2), ('-Pitch', 3, 3),
            ('+Yaw', 4, 0), ('-Yaw', 4, 1),
        ]

        for name, row, col in t5_btns:
            btn = QPushButton(name)
            btn.setMinimumHeight(32)
            btn.setFont(QFont("Arial", 10, QFont.Bold))
            if any(k in name for k in ('Roll', 'Pitch', 'Yaw')):
                btn.setStyleSheet("background-color: #e83e8c; color: white;")
            else:
                btn.setStyleSheet("background-color: #fd7e14; color: white;")
            btn.clicked.connect(lambda *args, n=name: self.jog_t5_base(n))
            t5_layout.addWidget(btn, row, col)

        t5_box.setLayout(t5_layout)

        # Layout relative jog boxes side-by-side (horizontally)
        jog_t5_layout = QHBoxLayout()
        jog_t5_layout.addWidget(jog_box)
        jog_t5_layout.addWidget(t5_box)
        left_panel.addLayout(jog_t5_layout)

        # 5c. Arm Control Mode (Position vs Impedance / Compliant) - arms only
        mode_box = QGroupBox("Arm Control Mode (arms only)")
        mode_layout = QGridLayout()

        self.chk_impedance = QCheckBox("Enable Arm Impedance (Compliant) Mode")
        self.chk_impedance.setChecked(False)
        self.chk_impedance.toggled.connect(self.on_impedance_toggled)
        mode_layout.addWidget(self.chk_impedance, 0, 0, 1, 6)

        mode_layout.addWidget(QLabel("Stiffness (Nm/rad):"), 1, 0)
        self.imp_stiffness_input = QLineEdit("150.0")
        mode_layout.addWidget(self.imp_stiffness_input, 1, 1)

        mode_layout.addWidget(QLabel("Damping Ratio:"), 1, 2)
        self.imp_damping_input = QLineEdit("1.0")
        mode_layout.addWidget(self.imp_damping_input, 1, 3)

        mode_layout.addWidget(QLabel("Torque Limit (Nm):"), 1, 4)
        self.imp_torque_input = QLineEdit("100.0")
        mode_layout.addWidget(self.imp_torque_input, 1, 5)

        mode_box.setLayout(mode_layout)
        left_panel.addWidget(mode_box)

        # Impedance parameter fields start disabled (position mode is default).
        self.imp_stiffness_input.setEnabled(False)
        self.imp_damping_input.setEnabled(False)
        self.imp_torque_input.setEnabled(False)

        # 6. Execution Controls (INIT POSE, MOVE, STOP)
        exec_box = QGroupBox("Motion Actions")
        exec_layout = QHBoxLayout()

        self.btn_init = QPushButton("INIT POSE")
        self.btn_init.setMinimumHeight(45)
        self.btn_init.setFont(QFont("Arial", 11, QFont.Bold))
        self.btn_init.setStyleSheet("background-color: #17a2b8; color: white;")
        self.btn_init.clicked.connect(self.run_init_pose)

        self.btn_move = QPushButton("MOVE")
        self.btn_move.setMinimumHeight(45)
        self.btn_move.setFont(QFont("Arial", 12, QFont.Bold))
        self.btn_move.setStyleSheet("background-color: #28a745; color: white;")
        self.btn_move.clicked.connect(self.run_move_pose)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setMinimumHeight(45)
        self.btn_stop.setFont(QFont("Arial", 12, QFont.Bold))
        self.btn_stop.setStyleSheet("background-color: #dc3545; color: white;")
        self.btn_stop.clicked.connect(self.run_stop)

        self.time_input = QLineEdit("5.0")
        self.time_input.setMaximumWidth(50)
        self.time_input.setMinimumHeight(35)
        self.time_input.setFont(QFont("Arial", 10))

        exec_layout.addWidget(self.btn_init)
        exec_layout.addWidget(self.btn_move)
        exec_layout.addWidget(self.btn_stop)
        exec_layout.addWidget(QLabel("Min Time (s):"))
        exec_layout.addWidget(self.time_input)
        exec_box.setLayout(exec_layout)
        left_panel.addWidget(exec_box)

        left_panel.addStretch()

        # Right Column: Live Status & Log Output
        right_panel = QVBoxLayout()

        status_box = QGroupBox("Live Status")
        status_layout = QVBoxLayout()

        self.lbl_init_status = QLabel("Init Pose Status: NOT EXECUTED")
        self.lbl_init_status.setFont(QFont("Arial", 10, QFont.Bold))
        self.lbl_init_status.setStyleSheet("color: #ffc107;")

        self.lbl_joint = QLabel("Joint Positions:\nN/A")
        self.lbl_joint.setFont(QFont("Consolas", 10))

        self.lbl_cart_right = QLabel("Right Pose (Ref -> Flange/TCP):\nN/A")
        self.lbl_cart_right.setFont(QFont("Consolas", 10))

        self.lbl_cart_left = QLabel("Left Pose (Ref -> Flange/TCP):\nN/A")
        self.lbl_cart_left.setFont(QFont("Consolas", 10))

        status_layout.addWidget(self.lbl_init_status)
        status_layout.addWidget(self.lbl_joint)
        status_layout.addWidget(self.lbl_cart_right)
        status_layout.addWidget(self.lbl_cart_left)
        status_layout.addStretch()
        status_box.setLayout(status_layout)
        right_panel.addWidget(status_box, 1)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setStyleSheet("background-color: #1e1e1e; color: #00ff00;")
        right_panel.addWidget(self.log_text, 3)

        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 1)
        self.setLayout(main_layout)

        self.log_msg("Calibration Pose Checker GUI initialized.")

    def log_msg(self, msg):
        self.log_text.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def on_target_mode_changed(self, text):
        is_custom = "Custom TCP" in text
        self.tcp_box.setEnabled(is_custom)
        self.log_msg(f"Target Link Mode changed to: {text}")

    def on_impedance_toggled(self, checked):
        """Enables the impedance gain fields and logs the active arm control mode."""
        self.imp_stiffness_input.setEnabled(checked)
        self.imp_damping_input.setEnabled(checked)
        self.imp_torque_input.setEnabled(checked)
        if checked:
            self.log_msg("[MODE] Arm IMPEDANCE (compliant) mode ENABLED — arms will yield to external force on next MOVE.")
        else:
            self.log_msg("[MODE] Arm POSITION (stiff) mode ENABLED.")

    def on_mirror_toggled(self, checked):
        """Toggles read-only state for Left arm angle text boxes based on mirroring checkbox."""
        self.l_roll.setReadOnly(checked)
        self.l_pitch.setReadOnly(checked)
        self.l_yaw.setReadOnly(checked)

        style = "background-color: #2b2b2b; color: #888888;" if checked else ""
        self.l_roll.setStyleSheet(style)
        self.l_pitch.setStyleSheet(style)
        self.l_yaw.setStyleSheet(style)

        if checked:
            self.update_mirrored_left_pose()

    def update_mirrored_left_pose(self):
        """Calculates and updates Left arm pose parameters symmetrically from Right arm inputs."""
        if not self.chk_mirror.isChecked():
            return

        try:
            r_r = float(self.r_roll.text().strip())
            r_p = float(self.r_pitch.text().strip())
            r_yw = float(self.r_yaw.text().strip())

            l_r = -r_r
            l_p = r_p
            l_yw = -r_yw

            self.l_roll.blockSignals(True)
            self.l_pitch.blockSignals(True)
            self.l_yaw.blockSignals(True)

            self.l_roll.setText(f"{l_r:.1f}")
            self.l_pitch.setText(f"{l_p:.1f}")
            self.l_yaw.setText(f"{l_yw:.1f}")

            self.l_roll.blockSignals(False)
            self.l_pitch.blockSignals(False)
            self.l_yaw.blockSignals(False)

        except ValueError:
            pass

    def jog_relative_parameter(self, direction):
        """Applies relative delta step in Reference Frame to Center Target Frame parameters and moves robot."""
        try:
            pos_step = float(self.pos_step_input.text().strip())
            rot_step = float(self.rot_step_input.text().strip())
        except ValueError:
            self.log_msg("[ERROR] Invalid Step values.")
            return

        try:
            if direction in ['+X', '-X']:
                val_mm = float(self.target_x.text().strip()) + (pos_step if direction == '+X' else -pos_step)
                self.target_x.setText(f"{val_mm:.1f}")

            elif direction in ['+Y', '-Y']:
                val_mm = float(self.target_y.text().strip()) + (pos_step if direction == '+Y' else -pos_step)
                self.target_y.setText(f"{val_mm:.1f}")

            elif direction in ['+Z', '-Z']:
                val_mm = float(self.target_z.text().strip()) + (pos_step if direction == '+Z' else -pos_step)
                self.target_z.setText(f"{val_mm:.1f}")

            elif direction in ['+Offset', '-Offset']:
                val_mm = float(self.target_offset.text().strip()) + (pos_step if direction == '+Offset' else -pos_step)
                self.target_offset.setText(f"{val_mm:.1f}")

            elif direction in ['+Roll', '-Roll', '+Pitch', '-Pitch', '+Yaw', '-Yaw']:
                c_r = float(self.center_roll.text().strip())
                c_p = float(self.center_pitch.text().strip())
                c_yw = float(self.center_yaw.text().strip())

                # Current Center Frame orientation in Reference Frame
                R_center = R.from_euler('zyx', [c_yw, c_p, c_r], degrees=True).as_matrix()

                # Delta rotation in Reference Frame (Left-multiply)
                d_deg = rot_step if '+' in direction else -rot_step
                d_rad = np.radians(d_deg)

                if 'Roll' in direction:
                    R_delta = rot_x(d_rad)
                elif 'Pitch' in direction:
                    R_delta = rot_y(d_rad)
                elif 'Yaw' in direction:
                    R_delta = rot_z(d_rad)

                R_center_new = R_delta @ R_center
                new_r, new_p, new_yw = matrix_to_euler_zyx_deg(R_center_new)

                self.center_roll.setText(f"{new_r:.1f}")
                self.center_pitch.setText(f"{new_p:.1f}")
                self.center_yaw.setText(f"{new_yw:.1f}")

            self.log_msg(f"Jogged Center Frame {direction} in Reference Frame. Moving robot...")
            if self.robot and self.robot.is_connected():
                self.run_move_pose()

        except ValueError as e:
            self.log_msg(f"[ERROR] Failed to jog parameter: {e}")

    def on_ref_link_changed(self, new_ref_text):
        """Auto-updates Symmetrical Pose parameters to live robot pose when Reference Frame switches."""
        self.log_msg(f"Reference Frame switched to: {new_ref_text}")

        if not self.robot or not self.robot.is_connected():
            return

        self.reflect_current_pose()

    def reflect_current_pose(self):
        """Reflects the live robot pose into the editable Symmetrical Pose parameters
        (in the currently selected Reference Frame)."""
        if not self.robot or not self.robot.is_connected():
            self.log_msg("[ERROR] Robot not connected. Cannot reflect current pose.")
            return

        try:
            state = self.robot.get_state()
            if state is None or getattr(state, 'position', None) is None:
                self.log_msg("[WARNING] No live robot state available to reflect.")
                return

            model = self.robot.model()
            dyn = self.robot.get_dynamics()
            q_full = np.array(state.position)

            ref_link_text = self.ref_link_sel.currentText()
            ref_link = "link_torso_5" if "t5" in ref_link_text else "base"
            dyn_state = dyn.make_state([ref_link, "ee_right", "ee_left"], model.robot_joint_names)
            dyn_state.set_q(q_full)
            dyn.compute_forward_kinematics(dyn_state)

            T_ref_ee_r = dyn.compute_transformation(dyn_state, 0, 1)
            T_ref_ee_l = dyn.compute_transformation(dyn_state, 0, 2)

            T_ee_tcp = self.get_tcp_offset_matrix()
            T_ref_tcp_r = T_ref_ee_r @ T_ee_tcp
            T_ref_tcp_l = T_ref_ee_l @ T_ee_tcp

            pr = T_ref_tcp_r[:3, 3] * 1000.0
            pl = T_ref_tcp_l[:3, 3] * 1000.0

            avg_x = (pr[0] + pl[0]) / 2.0
            avg_y = (pr[1] + pl[1]) / 2.0
            avg_z = (pr[2] + pl[2]) / 2.0
            offset = abs(pl[1] - pr[1]) / 2.0

            rr, rp, ry = matrix_to_euler_zyx_deg(T_ref_tcp_r[:3, :3])

            self.target_x.setText(f"{avg_x:.1f}")
            self.target_y.setText(f"{avg_y:.1f}")
            self.target_z.setText(f"{avg_z:.1f}")
            self.target_offset.setText(f"{offset:.1f}")

            self.center_roll.setText("0.0")
            self.center_pitch.setText("0.0")
            self.center_yaw.setText("0.0")

            self.r_roll.setText(f"{rr:.1f}")
            self.r_pitch.setText(f"{rp:.1f}")
            self.r_yaw.setText(f"{ry:.1f}")

            # When mirroring is disabled, reflect the actual Left arm orientation too.
            if not self.chk_mirror.isChecked():
                lr, lp, ly = matrix_to_euler_zyx_deg(T_ref_tcp_l[:3, :3])
                self.l_roll.setText(f"{lr:.1f}")
                self.l_pitch.setText(f"{lp:.1f}")
                self.l_yaw.setText(f"{ly:.1f}")

            self.log_msg(f"Reflected live robot pose into Symmetrical Pose parameters (frame '{ref_link}').")

        except Exception as e:
            self.log_msg(f"[WARNING] Could not reflect current pose: {e}")

    def jog_t5_base(self, direction):
        """Jogs T5 (link_torso_5) in the BASE frame by the configured step and commands the torso."""
        if not self.robot or not self.robot.is_connected():
            self.log_msg("[ERROR] Robot not connected.")
            return

        try:
            pos_step = float(self.t5_pos_step_input.text().strip())
            rot_step = float(self.t5_rot_step_input.text().strip())
        except ValueError:
            self.log_msg("[ERROR] Invalid T5 jog step values.")
            return

        # Rotation step is constrained to the 0~5 deg range per spec.
        if rot_step < 0.0:
            rot_step = 0.0
            self.t5_rot_step_input.setText("0.0")
        elif rot_step > 5.0:
            rot_step = 5.0
            self.t5_rot_step_input.setText("5.0")
            self.log_msg("[INFO] T5 rotation step clamped to max 5.0 deg.")

        try:
            state = self.robot.get_state()
            if state is None or getattr(state, 'position', None) is None:
                self.log_msg("[WARNING] No live robot state available for T5 jog.")
                return

            model = self.robot.model()
            dyn = self.robot.get_dynamics()
            q_full = np.array(state.position)

            dyn_state = dyn.make_state(["base", "link_torso_5"], model.robot_joint_names)
            dyn_state.set_q(q_full)
            dyn.compute_forward_kinematics(dyn_state)
            T_base_t5 = np.array(dyn.compute_transformation(dyn_state, 0, 1), dtype=np.float64)
        except Exception as e:
            self.log_msg(f"[ERROR] Failed to read current T5 pose: {e}")
            return

        T_target = T_base_t5.copy()

        if direction in ['+X', '-X', '+Y', '-Y', '+Z', '-Z']:
            d_m = (pos_step / 1000.0) * (1.0 if '+' in direction else -1.0)
            axis = {'X': 0, 'Y': 1, 'Z': 2}[direction[-1]]
            # Translation expressed directly in the BASE frame.
            T_target[axis, 3] += d_m
        elif direction in ['+Roll', '-Roll', '+Pitch', '-Pitch', '+Yaw', '-Yaw']:
            d_rad = math.radians(rot_step * (1.0 if '+' in direction else -1.0))
            if 'Roll' in direction:
                R_delta = rot_x(d_rad)
            elif 'Pitch' in direction:
                R_delta = rot_y(d_rad)
            else:
                R_delta = rot_z(d_rad)
            # Rotation about the BASE-frame axis (left-multiply), position preserved.
            T_target[:3, :3] = R_delta @ T_base_t5[:3, :3]
        else:
            return

        try:
            min_time = float(self.time_input.text().strip())
        except ValueError:
            min_time = 5.0

        params = {
            "ref_link": "base",
            "target_link": "link_torso_5",
            "T_target": T_target,
            "min_time": min_time,
        }
        self.log_msg(f"Jogging T5 {direction} in BASE frame. Moving torso...")
        self.start_worker("MOVE_TORSO", params)

    def toggle_connection(self):
        if self.robot:
            self.log_msg("Disconnecting from robot...")
            try:
                self.robot.disconnect()
            except Exception:
                pass
            self.robot = None
            self.btn_connect.setText("CONNECT")
            self.btn_connect.setStyleSheet("background-color: #ff9900; color: black; font-weight: bold;")
            self.lbl_joint.setText("Joint Positions:\nN/A")
            self.lbl_cart_right.setText("Right Pose (Ref -> Flange/TCP):\nN/A")
            self.lbl_cart_left.setText("Left Pose (Ref -> Flange/TCP):\nN/A")
            self.log_msg("Robot disconnected.")
            return

        try:
            addr = self.ip_input.text().strip()
            model = self.model_input.currentText().strip()
            self.log_msg(f"Connecting to robot at {addr} (model '{model}')...")

            self.robot = rby.create_robot(addr, model)
            if not self.robot.connect():
                raise Exception("Failed to connect.")

            if not self.robot.is_power_on(".*"):
                if not self.robot.power_on(".*"):
                    raise Exception("Failed to turn power on.")

            if not self.robot.is_servo_on(".*"):
                if not self.robot.servo_on(".*"):
                    raise Exception("Failed to turn servo on.")

            cm_state = self.robot.get_control_manager_state()
            if cm_state.state in [rby.ControlManagerState.State.MinorFault, rby.ControlManagerState.State.MajorFault]:
                self.robot.reset_fault_control_manager()

            self.robot.enable_control_manager()

            self.log_msg("Robot successfully connected and active.")
            self.btn_connect.setText("DISCONNECT")
            self.btn_connect.setStyleSheet("background-color: #6c757d; color: white; font-weight: bold;")

        except Exception as e:
            self.log_msg(f"[ERROR] Connection failed: {e}")

    def get_tcp_offset_matrix(self):
        """Returns T_EE_TCP matrix (4x4) from GUI inputs."""
        if "Custom TCP" not in self.target_mode_sel.currentText():
            return np.eye(4, dtype=np.float64)

        try:
            tx = float(self.tcp_x.text().strip()) / 1000.0
            ty = float(self.tcp_y.text().strip()) / 1000.0
            tz = float(self.tcp_z.text().strip()) / 1000.0
            r = float(self.tcp_roll.text().strip())
            p = float(self.tcp_pitch.text().strip())
            yw = float(self.tcp_yaw.text().strip())
            return euler_zyx_to_matrix(tx, ty, tz, r, p, yw)
        except ValueError:
            self.log_msg("[WARNING] Invalid TCP Offset inputs. Defaulting to identity.")
            return np.eye(4, dtype=np.float64)

    def compute_arm_flange_targets(self):
        """
        Computes T_Ref_EE_right and T_Ref_EE_left by:
        1. Rotating Target Center Frame by Center RPY.
        2. Applying symmetric Offset (+/-) along the rotated Center Frame Y-axis.
        3. Applying relative arm orientations and custom TCP offset.
        """
        try:
            bx = float(self.target_x.text().strip()) / 1000.0
            by = float(self.target_y.text().strip()) / 1000.0
            bz = float(self.target_z.text().strip()) / 1000.0
            offset = float(self.target_offset.text().strip()) / 1000.0

            c_r = float(self.center_roll.text().strip())
            c_p = float(self.center_pitch.text().strip())
            c_yw = float(self.center_yaw.text().strip())

            r_r = float(self.r_roll.text().strip())
            r_p = float(self.r_pitch.text().strip())
            r_yw = float(self.r_yaw.text().strip())

            l_r = float(self.l_roll.text().strip())
            l_p = float(self.l_pitch.text().strip())
            l_yw = float(self.l_yaw.text().strip())

            # 1. Target Center Frame orientation & position in Ref frame
            R_center = R.from_euler('zyx', [c_yw, c_p, c_r], degrees=True).as_matrix()
            P_center = np.array([bx, by, bz], dtype=np.float64)

            # 2. Y-axis direction of the rotated Center Frame in Ref frame
            y_axis_center = R_center[:, 1]

            # Symmetrical arm TCP positions along rotated Y-axis
            P_tcp_right = P_center - offset * y_axis_center
            P_tcp_left = P_center + offset * y_axis_center

            # Relative arm orientations combined with Center Frame orientation
            R_rel_right = R.from_euler('zyx', [r_yw, r_p, r_r], degrees=True).as_matrix()
            R_rel_left = R.from_euler('zyx', [l_yw, l_p, l_r], degrees=True).as_matrix()

            R_tcp_right = R_center @ R_rel_right
            R_tcp_left = R_center @ R_rel_left

            T_ref_tcp_right = np.eye(4, dtype=np.float64)
            T_ref_tcp_right[:3, :3] = R_tcp_right
            T_ref_tcp_right[:3, 3] = P_tcp_right

            T_ref_tcp_left = np.eye(4, dtype=np.float64)
            T_ref_tcp_left[:3, :3] = R_tcp_left
            T_ref_tcp_left[:3, 3] = P_tcp_left

            # 3. Apply Custom TCP transformation back to Tool Flange
            T_ee_tcp = self.get_tcp_offset_matrix()
            T_tcp_ee = np.linalg.inv(T_ee_tcp)

            T_ref_ee_right = T_ref_tcp_right @ T_tcp_ee
            T_ref_ee_left = T_ref_tcp_left @ T_tcp_ee

            return T_ref_ee_right, T_ref_ee_left
        except ValueError as e:
            raise RuntimeError(f"Invalid Pose Parameter input: {e}")

    def run_init_pose(self):
        if not self.robot:
            self.log_msg("[ERROR] Robot not connected.")
            return

        try:
            min_time = float(self.time_input.text().strip())
        except ValueError:
            min_time = 5.0

        params = {"min_time": min_time}
        self.start_worker("INIT_POSE", params)

    def run_move_pose(self):
        if not self.robot:
            self.log_msg("[ERROR] Robot not connected.")
            return

        try:
            T_right, T_left = self.compute_arm_flange_targets()
            ref_link_text = self.ref_link_sel.currentText()
            ref_link = "link_torso_5" if "t5" in ref_link_text else "base"
            min_time = float(self.time_input.text().strip())
        except Exception as e:
            self.log_msg(f"[ERROR] {e}")
            return

        impedance = self.chk_impedance.isChecked()
        try:
            imp_stiffness = float(self.imp_stiffness_input.text().strip())
            imp_damping = float(self.imp_damping_input.text().strip())
            imp_torque = float(self.imp_torque_input.text().strip())
        except ValueError:
            self.log_msg("[WARNING] Invalid impedance gains. Falling back to defaults (150 / 1.0 / 100).")
            imp_stiffness, imp_damping, imp_torque = 150.0, 1.0, 100.0

        auto_init = not self.init_pose_done
        params = {
            "ref_link": ref_link,
            "T_right": T_right,
            "T_left": T_left,
            "min_time": min_time,
            "auto_init": auto_init,
            "impedance": impedance,
            "imp_stiffness": imp_stiffness,
            "imp_damping": imp_damping,
            "imp_torque": imp_torque,
        }

        self.start_worker("MOVE_CARTESIAN", params)

    def run_stop(self):
        if not self.robot:
            self.log_msg("[ERROR] Robot not connected.")
            return

        self.log_msg("Sending IMMEDIATE EMERGENCY STOP signal to robot...")
        try:
            if hasattr(self.robot, "stop_command"):
                self.robot.stop_command()
            elif hasattr(self.robot, "cancel_control_manager"):
                self.robot.cancel_control_manager()
            else:
                rc = rby.RobotCommandBuilder().set_command(rby.ComponentBasedCommandBuilder())
                self.robot.send_command(rc, 1)
            self.log_msg("[SUCCESS] Stop / Cancel signal issued to robot.")
        except Exception as e:
            self.log_msg(f"[ERROR] Failed to send stop command: {e}")

        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(1000)

        self.set_buttons_enabled(True)

    def start_worker(self, task_type, params):
        if self.worker is not None and self.worker.isRunning():
            self.log_msg("[INFO] Active command detected. Interrupting previous task for new action...")
            try:
                if self.robot and hasattr(self.robot, "stop_command"):
                    self.robot.stop_command()
            except Exception:
                pass
            self.worker.wait(2000)

        self.set_buttons_enabled(False)
        worker = RobotCommandWorker(task_type, self.robot, self.model_input.currentText(), params, parent=self)
        self.worker = worker
        self.active_workers.append(worker)

        worker.finished.connect(lambda: self.cleanup_worker(worker))
        worker.log_signal.connect(self.log_msg)
        worker.finished_signal.connect(self.on_worker_finished)
        worker.start()

    def cleanup_worker(self, worker_obj):
        if worker_obj in self.active_workers:
            self.active_workers.remove(worker_obj)

    def on_worker_finished(self, success, msg):
        self.log_msg(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        if success:
            if self.worker and self.worker.task_type in ["INIT_POSE", "MOVE_CARTESIAN"]:
                self.init_pose_done = True
                self.lbl_init_status.setText("Init Pose Status: READY")
                self.lbl_init_status.setStyleSheet("color: #28a745;")
        self.set_buttons_enabled(True)

    def set_buttons_enabled(self, enabled):
        self.btn_init.setEnabled(enabled)
        self.btn_move.setEnabled(enabled)
        self.btn_stop.setEnabled(True)

    def poll_robot_state(self):
        if not self.robot:
            return

        try:
            state = self.robot.get_state()
            if state is None or getattr(state, 'position', None) is None:
                return

            model = self.robot.model()
            dyn = self.robot.get_dynamics()

            q_full = np.array(state.position)
            q_torso = np.degrees(q_full[model.torso_idx])
            q_right = np.degrees(q_full[model.right_arm_idx[:7]])
            q_left = np.degrees(q_full[model.left_arm_idx[:7]])

            joint_str = f"Torso: [{', '.join([f'{val:.1f}' for val in q_torso])}]\n"
            joint_str += f"Right: [{', '.join([f'{val:.1f}' for val in q_right])}]\n"
            joint_str += f"Left : [{', '.join([f'{val:.1f}' for val in q_left])}]"
            self.lbl_joint.setText(f"Joint Positions (deg):\n{joint_str}")

            ref_link_text = self.ref_link_sel.currentText()
            ref_link = "link_torso_5" if "t5" in ref_link_text else "base"

            dyn_state = dyn.make_state([ref_link, "ee_right", "ee_left"], model.robot_joint_names)
            dyn_state.set_q(q_full)
            dyn.compute_forward_kinematics(dyn_state)

            T_ref_ee_r = dyn.compute_transformation(dyn_state, 0, 1)
            T_ref_ee_l = dyn.compute_transformation(dyn_state, 0, 2)

            T_ee_tcp = self.get_tcp_offset_matrix()
            T_ref_tcp_r = T_ref_ee_r @ T_ee_tcp
            T_ref_tcp_l = T_ref_ee_l @ T_ee_tcp

            target_label = "TCP" if "Custom TCP" in self.target_mode_sel.currentText() else "Flange"

            # Right pose
            rx, ry, rz = T_ref_tcp_r[:3, 3] * 1000.0
            rr, rp, ryaw = matrix_to_euler_zyx_deg(T_ref_tcp_r[:3, :3])
            self.lbl_cart_right.setText(
                f"Right {target_label} Pose ({ref_link}):\n"
                f"X:{rx:6.1f} | Y:{ry:6.1f} | Z:{rz:6.1f} mm\n"
                f"Roll:{rr:6.1f} | Pitch:{rp:6.1f} | Yaw:{ryaw:6.1f} deg"
            )

            # Left pose
            lx, ly, lz = T_ref_tcp_l[:3, 3] * 1000.0
            lr, lp, lyaw = matrix_to_euler_zyx_deg(T_ref_tcp_l[:3, :3])
            self.lbl_cart_left.setText(
                f"Left {target_label} Pose ({ref_link}):\n"
                f"X:{lx:6.1f} | Y:{ly:6.1f} | Z:{lz:6.1f} mm\n"
                f"Roll:{lr:6.1f} | Pitch:{lp:6.1f} | Yaw:{lyaw:6.1f} deg"
            )

        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    ex = CalibrationPoseCheckerApp()
    ex.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
