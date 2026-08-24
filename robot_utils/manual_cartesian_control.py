import sys
import os
import numpy as np
import logging
import argparse
import time
from scipy.spatial.transform import Rotation as R

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QTextEdit, QLabel, QGroupBox, QComboBox, QLineEdit, QGridLayout)
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QFont

import rby1_sdk as rby

# --- Helper Functions ---
def initialize_robot(address, model, power=".*", servo=".*"):
    robot = rby.create_robot(address, model)
    if not robot.connect():
        raise Exception(f"Failed to connect robot {address}")
    if not robot.is_power_on(power):
        if not robot.power_on(power):
            raise Exception(f"Failed to turn power ({power}) on")
    if not robot.is_servo_on(servo):
        if not robot.servo_on(servo):
            raise Exception(f"Failed to servo ({servo}) on")
    if robot.get_control_manager_state().state in [
        rby.ControlManagerState.State.MajorFault,
        rby.ControlManagerState.State.MinorFault,
    ]:
        if not robot.reset_fault_control_manager():
            raise Exception("Failed to reset control manager")
    if not robot.enable_control_manager():
        raise Exception("Failed to enable control manager")
    return robot

def terminate_robot(robot):
    if robot:
        try:
            robot.disconnect()
            return True
        except:
            pass
    return False

class ManualCartesianApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manual Cartesian Control")
        self.resize(850, 650)
        
        self.robot = None
        self.arm_side = "right"
        self._last_T_ref = np.eye(4)
        self.init_ui()
        
        # State Polling Timer
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_robot_state)
        self.poll_timer.start(100) # 10 Hz

    def init_ui(self):
        main_layout = QHBoxLayout()
        
        # --- Left Panel: Controls ---
        left_panel = QVBoxLayout()
        
        # Connection
        conn_box = QGroupBox("Robot Connection")
        conn_layout = QVBoxLayout()
        self.ip_input = QLineEdit("192.168.30.1:50051")
        self.model_input = QComboBox()
        self.model_input.addItems(["a", "m"])
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setStyleSheet("background-color: #ff9900; color: black; font-weight: bold;")
        self.btn_connect.clicked.connect(self.connect_robot)
        conn_layout.addWidget(QLabel("IP Address:"))
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(QLabel("Model:"))
        conn_layout.addWidget(self.model_input)
        conn_layout.addWidget(self.btn_connect)
        conn_box.setLayout(conn_layout)
        left_panel.addWidget(conn_box)
        
        # Settings
        settings_box = QGroupBox("Settings")
        settings_layout = QVBoxLayout()
        self.arm_sel = QComboBox()
        self.arm_sel.addItems(["Right Arm", "Left Arm"])
        self.arm_sel.currentTextChanged.connect(self.on_arm_side_changed)
        settings_layout.addWidget(QLabel("Target Arm:"))
        settings_layout.addWidget(self.arm_sel)
        
        # Reference Frame Selection
        settings_layout.addWidget(QLabel("Reference Frame:"))
        self.frame_sel = QComboBox()
        self.frame_sel.addItems(["Base Frame", "Tool (TCP) Frame"])
        self.frame_sel.currentTextChanged.connect(self.on_frame_changed)
        settings_layout.addWidget(self.frame_sel)
        
        # TCP Offset Input (mm)
        settings_layout.addWidget(QLabel("TCP Offset wrt Flange (mm):"))
        tcp_layout = QHBoxLayout()
        self.tcp_x = QLineEdit("0.0")
        self.tcp_y = QLineEdit("0.0")
        self.tcp_z = QLineEdit("0.0")
        
        tcp_layout.addWidget(QLabel("X:"))
        tcp_layout.addWidget(self.tcp_x)
        tcp_layout.addWidget(QLabel("Y:"))
        tcp_layout.addWidget(self.tcp_y)
        tcp_layout.addWidget(QLabel("Z:"))
        tcp_layout.addWidget(self.tcp_z)
        settings_layout.addLayout(tcp_layout)
        
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("Step (mm / deg):"))
        self.step_input = QLineEdit("10.0")
        h1.addWidget(self.step_input)
        settings_layout.addLayout(h1)
        
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Min Time (sec):"))
        self.time_input = QLineEdit("1.5")
        h2.addWidget(self.time_input)
        settings_layout.addLayout(h2)
        
        settings_box.setLayout(settings_layout)
        left_panel.addWidget(settings_box)
        
        # Jog Controls
        self.jog_box = QGroupBox("Cartesian Jog (Base Frame)")
        jog_layout = QGridLayout()
        
        btns = [
            ('+X', 0, 0), ('-X', 0, 1),
            ('+Y', 1, 0), ('-Y', 1, 1),
            ('+Z', 2, 0), ('-Z', 2, 1),
            ('+RX', 3, 0), ('-RX', 3, 1),
            ('+RY', 4, 0), ('-RY', 4, 1),
            ('+RZ', 5, 0), ('-RZ', 5, 1),
        ]
        
        for name, row, col in btns:
            btn = QPushButton(name)
            btn.setMinimumHeight(40)
            btn.setFont(QFont("Arial", 12, QFont.Bold))
            if 'R' in name:
                btn.setStyleSheet("background-color: #17a2b8; color: white;")
            else:
                btn.setStyleSheet("background-color: #007bff; color: white;")
            btn.clicked.connect(lambda *args, n=name: self.jog_robot(n))
            jog_layout.addWidget(btn, row, col)
            
        self.jog_box.setLayout(jog_layout)
        left_panel.addWidget(self.jog_box)
        left_panel.addStretch()
        
        # --- Right Panel: Status & Logs ---
        right_panel = QVBoxLayout()
        
        status_box = QGroupBox("Live Robot State")
        status_layout = QVBoxLayout()
        self.lbl_joint = QLabel("Joint Positions:\nN/A")
        self.lbl_joint.setFont(QFont("Consolas", 10))
        self.lbl_cart = QLabel("Cartesian Pose (Base to EE):\nN/A")
        self.lbl_cart.setFont(QFont("Consolas", 10))
        self.lbl_tcp_cart = QLabel("Cartesian Pose (Base to TCP):\nN/A")
        self.lbl_tcp_cart.setFont(QFont("Consolas", 10))
        status_layout.addWidget(self.lbl_joint)
        status_layout.addWidget(self.lbl_cart)
        status_layout.addWidget(self.lbl_tcp_cart)
        status_box.setLayout(status_layout)
        right_panel.addWidget(status_box)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setStyleSheet("background-color: #1e1e1e; color: #00ff00;")
        right_panel.addWidget(self.log_text)
        
        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 2)
        self.setLayout(main_layout)
        
        self.log_msg("Manual Cartesian Control UI Ready.")

    def log_msg(self, msg):
        self.log_text.append(msg)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def connect_robot(self):
        if self.robot:
            # Disconnect
            self.log_msg("[INFO] Disconnecting from robot...")
            terminate_robot(self.robot)
            self.robot = None
            self.btn_connect.setText("CONNECT")
            self.btn_connect.setStyleSheet("background-color: #ff9900; color: black; font-weight: bold;")
            self.lbl_joint.setText("Joint Positions:\nN/A")
            self.lbl_cart.setText("Cartesian Pose (Base to EE):\nN/A")
            self.lbl_tcp_cart.setText("Cartesian Pose (Base to TCP):\nN/A")
            self.log_msg("[INFO] Robot disconnected.")
            return

        try:
            addr = self.ip_input.text().strip()
            model = self.model_input.currentText().strip()
            self.log_msg(f"[INFO] Connecting to robot at {addr} ({model})...")
            self.robot = initialize_robot(addr, model)
            self.log_msg("[INFO] Robot successfully connected and activated.")
            self.btn_connect.setText("DISCONNECT")
            self.btn_connect.setStyleSheet("background-color: #6c757d; color: white; font-weight: bold;")
        except Exception as e:
            self.log_msg(f"[ERROR] Failed to connect: {e}")

    def on_arm_side_changed(self, text):
        if "Left" in text:
            self.arm_side = "left"
        else:
            self.arm_side = "right"
        self.log_msg(f"[INFO] Target arm set to {self.arm_side.upper()}.")

    def on_frame_changed(self, text):
        self.jog_box.setTitle(f"Cartesian Jog ({text})")
        self.log_msg(f"[INFO] Reference frame set to {text}.")

    def get_tcp_offset_transform(self):
        """Returns T_ee_tcp matrix (4x4) from UI inputs (in meters)."""
        try:
            tx = float(self.tcp_x.text().strip()) / 1000.0
            ty = float(self.tcp_y.text().strip()) / 1000.0
            tz = float(self.tcp_z.text().strip()) / 1000.0
        except ValueError:
            tx, ty, tz = 0.0, 0.0, 0.0
        
        T_ee_tcp = np.eye(4)
        T_ee_tcp[:3, 3] = [tx, ty, tz]
        return T_ee_tcp

    def poll_robot_state(self):
        if not self.robot:
            return
        
        try:
            state = self.robot.get_state()
            model = self.robot.model()
            arm_idx = model.left_arm_idx if self.arm_side == "left" else model.right_arm_idx
            joint_pos = np.degrees(state.position[arm_idx])
            
            joint_str = ", ".join([f"{j:.1f}" for j in joint_pos])
            self.lbl_joint.setText(f"Joint Positions ({self.arm_side}):\n[{joint_str}] deg")
            
            # Kinematics for Cartesian EE
            dyn_robot = self.robot.get_dynamics()
            ee_name = f"ee_{self.arm_side}"
            dyn_state = dyn_robot.make_state(["base", ee_name], model.robot_joint_names)
            dyn_state.set_q(state.position)
            dyn_robot.compute_forward_kinematics(dyn_state)
            T_base_ee = dyn_robot.compute_transformation(dyn_state, 0, 1)
            self._last_T_ref = T_base_ee.copy()
            
            # EE pose display
            x, y, z = T_base_ee[:3, 3] * 1000.0
            rpy = R.from_matrix(T_base_ee[:3, :3]).as_euler('xyz', degrees=True)
            rx, ry, rz = rpy
            cart_str = f"X:{x:6.1f} | Y:{y:6.1f} | Z:{z:6.1f} mm\n"
            cart_str += f"RX:{rx:6.1f} | RY:{ry:6.1f} | RZ:{rz:6.1f} deg"
            self.lbl_cart.setText(f"Cartesian Pose (Base to EE / Flange):\n{cart_str}")
            
            # TCP pose display (Base to TCP = Base_EE * EE_TCP)
            T_ee_tcp = self.get_tcp_offset_transform()
            T_base_tcp = T_base_ee @ T_ee_tcp
            tx, ty, tz = T_base_tcp[:3, 3] * 1000.0
            trpy = R.from_matrix(T_base_tcp[:3, :3]).as_euler('xyz', degrees=True)
            trx, try_, trz = trpy
            tcp_str = f"X:{tx:6.1f} | Y:{ty:6.1f} | Z:{tz:6.1f} mm\n"
            tcp_str += f"RX:{trx:6.1f} | RY:{try_:6.1f} | RZ:{trz:6.1f} deg"
            self.lbl_tcp_cart.setText(f"Cartesian Pose (Base to TCP):\n{tcp_str}")

        except Exception as e:
            pass

    def jog_robot(self, direction):
        if not self.robot:
            self.log_msg("[ERROR] Robot not connected.")
            return
            
        try:
            step = float(self.step_input.text().strip())
            min_time = float(self.time_input.text().strip())
        except ValueError:
            self.log_msg("[ERROR] Invalid Step or Min Time values.")
            return

        ref_frame = self.frame_sel.currentText()
        T_base_ee = self._last_T_ref.copy()
        T_ee_tcp = self.get_tcp_offset_transform()
        
        # Calculate Delta Transform depending on selected Frame
        if ref_frame == "Base Frame":
            # Relative movement in Base Frame
            T_target_ee = T_base_ee.copy()
            if 'R' not in direction: # Translation in Base Frame
                delta_m = step / 1000.0
                if direction == '+X': T_target_ee[0, 3] += delta_m
                elif direction == '-X': T_target_ee[0, 3] -= delta_m
                elif direction == '+Y': T_target_ee[1, 3] += delta_m
                elif direction == '-Y': T_target_ee[1, 3] -= delta_m
                elif direction == '+Z': T_target_ee[2, 3] += delta_m
                elif direction == '-Z': T_target_ee[2, 3] -= delta_m
            else: # Rotation around Base Axes (Pivot around TCP point if TCP offset set)
                delta_rad = np.radians(step)
                rx = delta_rad if direction == '+RX' else (-delta_rad if direction == '-RX' else 0)
                ry = delta_rad if direction == '+RY' else (-delta_rad if direction == '-RY' else 0)
                rz = delta_rad if direction == '+RZ' else (-delta_rad if direction == '-RZ' else 0)
                
                R_delta_base = R.from_euler('xyz', [rx, ry, rz]).as_matrix()
                
                # Rotate around TCP point in Base frame orientation
                T_base_tcp = T_base_ee @ T_ee_tcp
                p_tcp_base = T_base_tcp[:3, 3]
                
                # New TCP orientation
                R_tcp_new = R_delta_base @ T_base_tcp[:3, :3]
                T_base_tcp_new = np.eye(4)
                T_base_tcp_new[:3, :3] = R_tcp_new
                T_base_tcp_new[:3, 3] = p_tcp_base  # keep TCP point fixed
                
                # Convert target TCP back to target EE: T_base_ee_new = T_base_tcp_new * inv(T_ee_tcp)
                T_target_ee = T_base_tcp_new @ np.linalg.inv(T_ee_tcp)

        else: # Tool (TCP) Frame
            # Relative movement defined in Tool (TCP) Frame
            delta_T_tcp = np.eye(4)
            if 'R' not in direction: # Translation along TCP axes
                delta_m = step / 1000.0
                if direction == '+X': delta_T_tcp[0, 3] = delta_m
                elif direction == '-X': delta_T_tcp[0, 3] = -delta_m
                elif direction == '+Y': delta_T_tcp[1, 3] = delta_m
                elif direction == '-Y': delta_T_tcp[1, 3] = -delta_m
                elif direction == '+Z': delta_T_tcp[2, 3] = delta_m
                elif direction == '-Z': delta_T_tcp[2, 3] = -delta_m
            else: # Rotation around TCP axes
                delta_rad = np.radians(step)
                rx = delta_rad if direction == '+RX' else (-delta_rad if direction == '-RX' else 0)
                ry = delta_rad if direction == '+RY' else (-delta_rad if direction == '-RY' else 0)
                rz = delta_rad if direction == '+RZ' else (-delta_rad if direction == '-RZ' else 0)
                
                delta_T_tcp[:3, :3] = R.from_euler('xyz', [rx, ry, rz]).as_matrix()

            # Transformation chain: T_base_ee_new = T_base_ee * T_ee_tcp * delta_T_tcp * inv(T_ee_tcp)
            T_base_tcp = T_base_ee @ T_ee_tcp
            T_base_tcp_new = T_base_tcp @ delta_T_tcp
            T_target_ee = T_base_tcp_new @ np.linalg.inv(T_ee_tcp)

        self.log_msg(f"[CMD] Move {direction} by {step} ({ref_frame}, Time: {min_time}s)")
        
        ee_name = f"ee_{self.arm_side}"
        cb = rby.CartesianCommandBuilder().set_minimum_time(min_time)
        cb.add_target("base", ee_name, T_target_ee, 1.5, np.pi*1.5, 1.0)
        cb.set_stop_orientation_tracking_error(1e-4)
        cb.set_stop_position_tracking_error(1e-3)
        
        body_cmd = rby.BodyComponentBasedCommandBuilder()
        if self.arm_side == "right":
            body_cmd.set_right_arm_command(cb)
        else:
            body_cmd.set_left_arm_command(cb)
            
        rc = rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(body_cmd)
        )
        
        rv = self.robot.send_command(rc, 10).get()
        if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
            self.log_msg("[ERROR] Command failed or rejected.")
        else:
            self.log_msg("[SUCCESS] Move completed.")


def main():
    app = QApplication(sys.argv)
    ex = ManualCartesianApp()
    ex.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
