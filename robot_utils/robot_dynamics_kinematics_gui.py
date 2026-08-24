#!/usr/bin/env python3
"""
Robot Dynamics & Kinematics Calculator GUI
Target WS: utils_ws/robot_utils

Features:
- Offline URDF model loading (RBY1-A, RBY1-M, RBY1-UB, Leader Arm) with automatic root link discovery
- Degree/Radian unit switching for inputs with automatic conversion and limit scaling
- Subsystem grouping for joint controls (Torso, Left Arm, Right Arm, Head, Wheels/Base)
- DoubleSpinBoxes and QSliders for interactive joint position control
- Kinematics panel: Reference/Target Link selection, 4x4 matrix display, position (m/mm), and Roll-Pitch-Yaw decomposition
- Dynamics panel: Gravity compensation and Inverse Dynamics calculation, comparing computed torques against physical joint limits with color-coded warning system
- Premium dark mode style sheet
"""

import sys
import os
import math
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation as R

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QGroupBox, QComboBox, QLineEdit, QGridLayout,
    QFormLayout, QMessageBox, QFrame, QRadioButton, QButtonGroup,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView, QSlider,
    QDoubleSpinBox
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QColor, QPalette

import rby1_sdk as rby
import rby1_sdk.dynamics as rd

# Predefined model paths based on the repository structure (loaded from local models folder)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SDK_MODELS_DIR = os.path.join(SCRIPT_DIR, "models")

def find_root_link(urdf_path):
    """Parses URDF XML to find the root link (link with no parent joint)."""
    try:
        tree = ET.parse(urdf_path)
        root = tree.getroot()
        links = [l.attrib['name'] for l in root.findall('link')]
        joints = root.findall('joint')
        child_links = set(j.find('child').attrib['link'] for j in joints if j.find('child') is not None)
        root_links = [l for l in links if l not in child_links]
        if root_links:
            return root_links[0]
    except Exception:
        pass
    return "base"

class RobotDynamicsKinematicsApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Robot Dynamics & Kinematics Calculator")
        self.resize(1300, 850)
        
        self.robot = None
        self.state = None
        self.joint_names = []
        self.link_names = []
        self.joint_widgets = {}  # joint_name -> {spinbox, slider, limit_lbl, lower, upper}
        self.updating_ui = False
        
        # Default gravity vector (Z-axis negative)
        self.gravity_vector = np.array([0.0, 0.0, 0.0, 0.0, 0.0, -9.81])
        
        # Setup UI
        self.init_ui()
        self.apply_dark_theme()
        
        # Scan and populate robot models
        self.scan_robot_models()

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # 1. Header Title
        title_lbl = QLabel("ROBOT KINEMATICS & DYNAMICS CALCULATOR")
        title_lbl.setFont(QFont("Arial", 16, QFont.Bold))
        title_lbl.setStyleSheet("color: #00ADB5; letter-spacing: 2px;")
        title_lbl.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_lbl)
        
        # 2. Top Bar Controls (Model Selector & Unit Switcher)
        top_bar = QHBoxLayout()
        
        model_group = QGroupBox("Model Configuration")
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Robot Model/Version:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(250)
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        model_layout.addWidget(self.model_combo)
        model_group.setLayout(model_layout)
        top_bar.addWidget(model_group, 2)
        
        unit_group = QGroupBox("Angle Units")
        unit_layout = QHBoxLayout()
        self.unit_button_group = QButtonGroup(self)
        
        self.rb_deg = QRadioButton("Degrees (deg)")
        self.rb_deg.setChecked(True)
        self.rb_deg.toggled.connect(self.on_unit_changed)
        self.unit_button_group.addButton(self.rb_deg)
        unit_layout.addWidget(self.rb_deg)
        
        self.rb_rad = QRadioButton("Radians (rad)")
        self.rb_rad.toggled.connect(self.on_unit_changed)
        self.unit_button_group.addButton(self.rb_rad)
        unit_layout.addWidget(self.rb_rad)
        
        unit_group.setLayout(unit_layout)
        top_bar.addWidget(unit_group, 1)
        
        main_layout.addLayout(top_bar)
        
        # 3. Main Workspace Split (Left: Joint Inputs Scroll Area, Right: Results)
        workspace = QHBoxLayout()
        workspace.setSpacing(15)
        
        # --- Left Column: Joint Inputs ---
        left_panel = QVBoxLayout()
        left_title = QLabel("JOINT CONFIGURATION")
        left_title.setFont(QFont("Arial", 11, QFont.Bold))
        left_title.setStyleSheet("color: #EEEEEE;")
        left_panel.addWidget(left_title)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setSpacing(12)
        self.scroll_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_widget)
        left_panel.addWidget(self.scroll_area)
        
        # Reset to zero button
        self.btn_reset_zero = QPushButton("RESET ALL JOINTS TO ZERO")
        self.btn_reset_zero.setFont(QFont("Arial", 10, QFont.Bold))
        self.btn_reset_zero.setStyleSheet("background-color: #393E46; color: #EEEEEE; padding: 8px;")
        self.btn_reset_zero.clicked.connect(self.reset_all_joints_to_zero)
        left_panel.addWidget(self.btn_reset_zero)
        
        workspace.addLayout(left_panel, 4)
        
        # --- Right Column: Results & Computations ---
        right_panel = QVBoxLayout()
        right_panel.setSpacing(15)
        
        # 3a. Kinematics Panel
        kin_box = QGroupBox("Kinematics (Forward Kinematics & Coordinates)")
        kin_layout = QVBoxLayout()
        
        # Link selectors
        links_sel_layout = QHBoxLayout()
        links_sel_layout.addWidget(QLabel("Reference Link:"))
        self.ref_link_combo = QComboBox()
        self.ref_link_combo.currentTextChanged.connect(self.trigger_calculation)
        links_sel_layout.addWidget(self.ref_link_combo)
        
        links_sel_layout.addWidget(QLabel("Target Link:"))
        self.target_link_combo = QComboBox()
        self.target_link_combo.currentTextChanged.connect(self.trigger_calculation)
        links_sel_layout.addWidget(self.target_link_combo)
        kin_layout.addLayout(links_sel_layout)
        
        # Coordinate Results
        coords_layout = QGridLayout()
        self.lbl_pos_x = QLabel("X: - mm (- m)")
        self.lbl_pos_y = QLabel("Y: - mm (- m)")
        self.lbl_pos_z = QLabel("Z: - mm (- m)")
        self.lbl_rot_r = QLabel("Roll: - deg (- rad)")
        self.lbl_rot_p = QLabel("Pitch: - deg (- rad)")
        self.lbl_rot_y = QLabel("Yaw: - deg (- rad)")
        
        for lbl in [self.lbl_pos_x, self.lbl_pos_y, self.lbl_pos_z, self.lbl_rot_r, self.lbl_rot_p, self.lbl_rot_y]:
            lbl.setFont(QFont("Consolas", 10, QFont.Bold))
            lbl.setStyleSheet("color: #00ADB5;")
            
        coords_layout.addWidget(QLabel("Position:"), 0, 0)
        coords_layout.addWidget(self.lbl_pos_x, 0, 1)
        coords_layout.addWidget(self.lbl_pos_y, 0, 2)
        coords_layout.addWidget(self.lbl_pos_z, 0, 3)
        
        coords_layout.addWidget(QLabel("Rotation (Euler ZYX):"), 1, 0)
        coords_layout.addWidget(self.lbl_rot_r, 1, 1)
        coords_layout.addWidget(self.lbl_rot_p, 1, 2)
        coords_layout.addWidget(self.lbl_rot_y, 1, 3)
        kin_layout.addLayout(coords_layout)
        
        # 4x4 Matrix display
        kin_layout.addWidget(QLabel("Homogeneous Transformation Matrix T:"))
        self.matrix_txt = QTextEdit()
        self.matrix_txt.setReadOnly(True)
        self.matrix_txt.setFont(QFont("Consolas", 10))
        self.matrix_txt.setStyleSheet("background-color: #222831; color: #00FF66; border: 1px solid #393E46;")
        self.matrix_txt.setMaximumHeight(110)
        kin_layout.addWidget(self.matrix_txt)
        
        kin_box.setLayout(kin_layout)
        right_panel.addWidget(kin_box, 3)
        
        # 3b. Dynamics & Torque Table Panel
        dyn_box = QGroupBox("Dynamics & Joint Torques")
        dyn_layout = QVBoxLayout()
        
        # Table of joint torques & limits
        self.torque_table = QTableWidget()
        self.torque_table.setColumnCount(5)
        self.torque_table.setHorizontalHeaderLabels(["Joint Name", "Current Pos", "Gravity Torque", "Torque Limit", "Status"])
        self.torque_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.torque_table.setStyleSheet("""
            QTableWidget {
                background-color: #222831;
                gridline-color: #393E46;
                color: #EEEEEE;
            }
            QHeaderView::section {
                background-color: #393E46;
                color: #EEEEEE;
                padding: 4px;
                font-weight: bold;
                border: 1px solid #222831;
            }
        """)
        dyn_layout.addWidget(self.torque_table)
        
        # Info labels
        self.lbl_max_gravity_torque = QLabel("Max Gravity Torque: - Nm (-)")
        self.lbl_max_gravity_torque.setFont(QFont("Arial", 10, QFont.Bold))
        self.lbl_max_gravity_torque.setStyleSheet("color: #EEEEEE;")
        dyn_layout.addWidget(self.lbl_max_gravity_torque)
        
        dyn_box.setLayout(dyn_layout)
        right_panel.addWidget(dyn_box, 5)
        
        workspace.addLayout(right_panel, 5)
        main_layout.addLayout(workspace)
        
        self.setLayout(main_layout)

    def scan_robot_models(self):
        """Scans the standard models folder or hardcodes URDF models to populate the combo box."""
        models = {}
        
        # Hardcoded dictionary matching typical models structure if folder exists (Default names removed)
        candidate_paths = [
            ("RBY1-A (v1.2)", os.path.join(SDK_MODELS_DIR, "rby1a/urdf/model_v1.2.urdf"), "base"),
            ("RBY1-A (v1.1)", os.path.join(SDK_MODELS_DIR, "rby1a/urdf/model_v1.1.urdf"), "base"),
            ("RBY1-A (v1.0)", os.path.join(SDK_MODELS_DIR, "rby1a/urdf/model_v1.0.urdf"), "base"),
            
            ("RBY1-M (v1.3)", os.path.join(SDK_MODELS_DIR, "rby1m/urdf/model_v1.3.urdf"), "base"),
            ("RBY1-M (v1.2)", os.path.join(SDK_MODELS_DIR, "rby1m/urdf/model_v1.2.urdf"), "base"),
            ("RBY1-M (v1.1)", os.path.join(SDK_MODELS_DIR, "rby1m/urdf/model_v1.1.urdf"), "base"),
            ("RBY1-M (v1.0)", os.path.join(SDK_MODELS_DIR, "rby1m/urdf/model_v1.0.urdf"), "base"),
            
            ("RBY1-UB", os.path.join(SDK_MODELS_DIR, "rby1ub/urdf/model.urdf"), "base"),
            ("Leader Arm", os.path.join(SDK_MODELS_DIR, "leader_arm/model.urdf"), "Base")
        ]
        
        # Add files that exist on disk
        for name, path, base in candidate_paths:
            if os.path.exists(path):
                models[name] = (path, base)
                
        # Fallback to search recursively if no pre-defined models are found
        if not models and os.path.exists(SDK_MODELS_DIR):
            for root, dirs, files in os.walk(SDK_MODELS_DIR):
                for f in files:
                    if f.endswith(".urdf"):
                        full_path = os.path.join(root, f)
                        rel = os.path.relpath(full_path, SDK_MODELS_DIR)
                        name = rel.replace("/urdf", "").replace(".urdf", "").upper()
                        # Guess base link name
                        base_link = "Base" if "leader" in name.lower() else "base"
                        models[name] = (full_path, base_link)
                        
        self.models_dict = models
        self.model_combo.blockSignals(True)
        self.model_combo.addItems(sorted(models.keys()))
        self.model_combo.blockSignals(False)
        
        if models:
            # Select RBY1-A (v1.2) or first model
            default_model = "RBY1-A (v1.2)" if "RBY1-A (v1.2)" in models else list(models.keys())[0]
            self.model_combo.setCurrentText(default_model)
            self.load_robot_model(default_model)

    def load_robot_model(self, model_name):
        """Loads the selected robot model's URDF, recreates the dynamics Robot and populates joint controls."""
        if model_name not in self.models_dict:
            return
            
        urdf_path, base_link_name = self.models_dict[model_name]
        
        # If base link name fails, parse the root link dynamically
        discovered_base = find_root_link(urdf_path)
        if discovered_base != base_link_name:
            base_link_name = discovered_base
            
        try:
            config = rd.load_robot_from_urdf(urdf_path, base_link_name)
            self.robot = rd.Robot(config)
            self.joint_names = self.robot.get_joint_names()
            self.link_names = self.robot.get_link_names()
            self.state = self.robot.make_state(self.link_names, self.joint_names)
            
            # Setup base link in dynamics state gravity
            self.state.set_gravity(self.gravity_vector)
            
            # Clear old widgets
            self.clear_joint_inputs()
            
            # Recreate dynamic joint widgets
            self.create_joint_inputs()
            
            # Populate link selection dropdowns
            self.ref_link_combo.blockSignals(True)
            self.target_link_combo.blockSignals(True)
            
            self.ref_link_combo.clear()
            self.ref_link_combo.addItems(self.link_names)
            if base_link_name in self.link_names:
                self.ref_link_combo.setCurrentText(base_link_name)
            else:
                self.ref_link_combo.setCurrentIndex(0)
                
            self.target_link_combo.clear()
            self.target_link_combo.addItems(self.link_names)
            # Find a suitable target link
            candidates = ["ee_right", "ee_left", "Link_6R", "Link_6L", "FT_sensor_R", "FT_sensor_L"]
            target_found = False
            for cand in candidates:
                if cand in self.link_names:
                    self.target_link_combo.setCurrentText(cand)
                    target_found = True
                    break
            if not target_found:
                # Default to last link in URDF
                self.target_link_combo.setCurrentIndex(len(self.link_names) - 1)
                
            self.ref_link_combo.blockSignals(False)
            self.target_link_combo.blockSignals(False)
            
            # Recalculate kinematics/dynamics
            self.trigger_calculation()
            
        except Exception as e:
            QMessageBox.critical(self, "Load Model Error", f"Failed to load robot URDF: {e}")

    def clear_joint_inputs(self):
        """Clears all dynamic joint controls from the scroll layout."""
        # Find all QGroupBox children in the scroll widget
        for child in self.scroll_widget.findChildren(QGroupBox):
            child.setParent(None)
            child.deleteLater()
        self.joint_widgets.clear()

    def create_joint_inputs(self):
        """Dynamically parses and groups robot joints to build input controls (spinbox + slider)."""
        if not self.robot:
            return
            
        # Group definitions
        groups = {
            "Torso": [],
            "Right Arm": [],
            "Left Arm": [],
            "Head": [],
            "Wheels & Base": [],
            "Other Joints": []
        }
        
        # Categorize joints by name
        for j_name in self.joint_names:
            name_lower = j_name.lower()
            if "torso" in name_lower:
                groups["Torso"].append(j_name)
            elif "right_arm" in name_lower or "r_arm" in name_lower or name_lower.endswith("r") or "right" in name_lower:
                groups["Right Arm"].append(j_name)
            elif "left_arm" in name_lower or "l_arm" in name_lower or name_lower.endswith("l") or "left" in name_lower:
                groups["Left Arm"].append(j_name)
            elif "head" in name_lower:
                groups["Head"].append(j_name)
            elif "wheel" in name_lower or "mobility" in name_lower or "steering" in name_lower:
                groups["Wheels & Base"].append(j_name)
            else:
                groups["Other Joints"].append(j_name)
                
        # Retrieve limits
        q_lower_all = self.robot.get_limit_q_lower(self.state)
        q_upper_all = self.robot.get_limit_q_upper(self.state)
        
        # Determine the current units
        is_deg = self.rb_deg.isChecked()
        
        # Create UI Group Boxes
        for group_name, j_list in groups.items():
            if not j_list:
                continue
                
            group_box = QGroupBox(group_name)
            group_box.setStyleSheet("""
                QGroupBox {
                    font-weight: bold;
                    border: 1px solid #393E46;
                    border-radius: 6px;
                    margin-top: 10px;
                    padding-top: 15px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    subcontrol-position: top left;
                    left: 10px;
                    padding: 0px 5px;
                    color: #00ADB5;
                }
            """)
            grid = QGridLayout()
            grid.setSpacing(8)
            
            for i, j_name in enumerate(sorted(j_list)):
                idx = self.joint_names.index(j_name)
                lower = q_lower_all[idx]
                upper = q_upper_all[idx]
                
                # Check for continuous / infinite joints (like wheels)
                is_continuous = False
                if lower < -1000 or upper > 1000:
                    is_continuous = True
                    lower = -2.0 * math.pi
                    upper = 2.0 * math.pi
                
                # Create Spinbox
                spin = QDoubleSpinBox()
                spin.setDecimals(3)
                spin.setSingleStep(0.1 if is_deg else 0.001)
                
                # Create Slider
                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, 1000)
                
                # Limits Label
                limit_lbl = QLabel()
                limit_lbl.setFont(QFont("Consolas", 9))
                limit_lbl.setStyleSheet("color: #888888;")
                
                # Store widget metadata
                self.joint_widgets[j_name] = {
                    "spin": spin,
                    "slider": slider,
                    "limit_lbl": limit_lbl,
                    "lower": lower,  # always stored in radians
                    "upper": upper,  # always stored in radians
                    "is_continuous": is_continuous
                }
                
                # Set dynamic limits and values based on units
                self.update_joint_widget_limits(j_name, is_deg)
                
                # Initialize values
                if is_deg:
                    spin.setValue(0.0)
                else:
                    spin.setValue(0.0)
                self.sync_slider_from_spinbox(j_name, 0.0, is_deg)
                
                # Connect signals
                spin.valueChanged.connect(lambda val, j=j_name: self.on_spin_changed(j, val))
                slider.valueChanged.connect(lambda val, j=j_name: self.on_slider_changed(j, val))
                
                # Add to grid layout
                lbl_name = QLabel(j_name)
                lbl_name.setFont(QFont("Arial", 9, QFont.Bold))
                grid.addWidget(lbl_name, i, 0)
                grid.addWidget(spin, i, 1)
                grid.addWidget(slider, i, 2)
                grid.addWidget(limit_lbl, i, 3)
                
            group_box.setLayout(grid)
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, group_box)

    def update_joint_widget_limits(self, j_name, is_deg):
        """Sets limits, ranges, suffixes, and labels on joint widgets based on target unit."""
        meta = self.joint_widgets[j_name]
        spin = meta["spin"]
        limit_lbl = meta["limit_lbl"]
        
        # Convert bounds
        lower_val = meta["lower"] if not is_deg else math.degrees(meta["lower"])
        upper_val = meta["upper"] if not is_deg else math.degrees(meta["upper"])
        
        spin.blockSignals(True)
        spin.setRange(lower_val, upper_val)
        spin.setSuffix(" deg" if is_deg else " rad")
        spin.setDecimals(3)
        spin.setSingleStep(0.1 if is_deg else 0.001)
        spin.blockSignals(False)
        
        if meta["is_continuous"]:
            limit_lbl.setText("[Continuous]")
        else:
            limit_lbl.setText(f"[{lower_val:.3f}, {upper_val:.3f}]")

    def sync_slider_from_spinbox(self, j_name, value, is_deg):
        """Updates the QSlider position mapping the float spinbox value to the [0, 1000] range."""
        meta = self.joint_widgets[j_name]
        slider = meta["slider"]
        
        lower_val = meta["lower"] if not is_deg else math.degrees(meta["lower"])
        upper_val = meta["upper"] if not is_deg else math.degrees(meta["upper"])
        
        span = upper_val - lower_val
        if span <= 0:
            ratio = 0.5
        else:
            ratio = (value - lower_val) / span
            ratio = min(max(ratio, 0.0), 1.0)
            
        slider.blockSignals(True)
        slider.setValue(int(ratio * 1000))
        slider.blockSignals(False)

    def sync_spinbox_from_slider(self, j_name, slider_val, is_deg):
        """Updates the QDoubleSpinBox value mapping the [0, 1000] slider position to a float."""
        meta = self.joint_widgets[j_name]
        spin = meta["spin"]
        
        lower_val = meta["lower"] if not is_deg else math.degrees(meta["lower"])
        upper_val = meta["upper"] if not is_deg else math.degrees(meta["upper"])
        
        ratio = slider_val / 1000.0
        float_val = lower_val + ratio * (upper_val - lower_val)
        
        spin.blockSignals(True)
        spin.setValue(float_val)
        spin.blockSignals(False)
        
        return float_val

    def on_spin_changed(self, j_name, val):
        """Called when a joint spinbox is changed by the user."""
        if self.updating_ui:
            return
        is_deg = self.rb_deg.isChecked()
        self.sync_slider_from_spinbox(j_name, val, is_deg)
        self.trigger_calculation()

    def on_slider_changed(self, j_name, slider_val):
        """Called when a joint slider is dragged by the user."""
        if self.updating_ui:
            return
        is_deg = self.rb_deg.isChecked()
        val = self.sync_spinbox_from_slider(j_name, slider_val, is_deg)
        self.trigger_calculation()

    def on_model_changed(self, text):
        """Fires when the selected robot model changes."""
        self.load_robot_model(text)

    def on_unit_changed(self, checked):
        """Converts values and bounds when toggling between Deg and Rad."""
        if not checked or not self.robot:
            return
            
        self.updating_ui = True
        is_deg = self.rb_deg.isChecked()
        
        for j_name, meta in self.joint_widgets.items():
            spin = meta["spin"]
            old_val = spin.value()
            
            # Convert value
            new_val = math.degrees(old_val) if is_deg else math.radians(old_val)
            
            # Update limits and suffix
            self.update_joint_widget_limits(j_name, is_deg)
            
            # Set value
            spin.blockSignals(True)
            spin.setValue(new_val)
            spin.blockSignals(False)
            
            # Sync slider
            self.sync_slider_from_spinbox(j_name, new_val, is_deg)
            
        self.updating_ui = False
        self.trigger_calculation()

    def reset_all_joints_to_zero(self):
        """Resets all joint values to 0.0."""
        self.updating_ui = True
        is_deg = self.rb_deg.isChecked()
        for j_name, meta in self.joint_widgets.items():
            spin = meta["spin"]
            
            # Keep within limits if 0.0 is out of bounds
            lower = meta["lower"] if not is_deg else math.degrees(meta["lower"])
            upper = meta["upper"] if not is_deg else math.degrees(meta["upper"])
            target = 0.0
            if target < lower:
                target = lower
            elif target > upper:
                target = upper
                
            spin.setValue(target)
            self.sync_slider_from_spinbox(j_name, target, is_deg)
            
        self.updating_ui = False
        self.trigger_calculation()

    def get_joint_angles_rad(self):
        """Gathers current joint angles from UI in radians."""
        q = np.zeros(len(self.joint_names))
        is_deg = self.rb_deg.isChecked()
        for idx, j_name in enumerate(self.joint_names):
            if j_name in self.joint_widgets:
                val = self.joint_widgets[j_name]["spin"].value()
                q[idx] = math.radians(val) if is_deg else val
        return q

    def trigger_calculation(self):
        """Performs kinematics and dynamics calculations based on current joint inputs."""
        if not self.robot or not self.state:
            return
            
        try:
            # 1. Fetch current q
            q = self.get_joint_angles_rad()
            self.state.set_q(q)
            
            # 2. Update Kinematics
            self.robot.compute_forward_kinematics(self.state)
            
            ref_link = self.ref_link_combo.currentText()
            target_link = self.target_link_combo.currentText()
            
            if ref_link and target_link:
                # Find indices in link list
                ref_idx = self.link_names.index(ref_link)
                target_idx = self.link_names.index(target_link)
                
                # Compute T_ref_target
                T = self.robot.compute_transformation(self.state, ref_idx, target_idx)
                
                # Position
                x_m, y_m, z_m = T[0, 3], T[1, 3], T[2, 3]
                x_mm, y_mm, z_mm = x_m * 1000.0, y_m * 1000.0, z_m * 1000.0
                
                self.lbl_pos_x.setText(f"X: {x_mm:7.2f} mm ({x_m:6.3f} m)")
                self.lbl_pos_y.setText(f"Y: {y_mm:7.2f} mm ({y_m:6.3f} m)")
                self.lbl_pos_z.setText(f"Z: {z_mm:7.2f} mm ({z_m:6.3f} m)")
                
                # Rotation ZYX decomposition
                with np.printoptions(precision=4, suppress=True):
                    # We suppress Gimbal Lock warnings
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        yaw_d, pitch_d, roll_d = R.from_matrix(T[:3, :3]).as_euler('zyx', degrees=True)
                        yaw_r, pitch_r, roll_r = R.from_matrix(T[:3, :3]).as_euler('zyx', degrees=False)
                        
                self.lbl_rot_r.setText(f"Roll:  {roll_d:6.2f} deg ({roll_r:5.3f} rad)")
                self.lbl_rot_p.setText(f"Pitch: {pitch_d:6.2f} deg ({pitch_r:5.3f} rad)")
                self.lbl_rot_y.setText(f"Yaw:   {yaw_d:6.2f} deg ({yaw_r:5.3f} rad)")
                
                # Matrix display
                matrix_str = ""
                for row in T:
                    matrix_str += f"[ {row[0]:7.4f}  {row[1]:7.4f}  {row[2]:7.4f}  {row[3]:7.4f} ]\n"
                self.matrix_txt.setPlainText(matrix_str.strip())
                
            # 3. Update Dynamics (Gravity Torques)
            gravity_torques = self.robot.compute_gravity_term(self.state)
            tau_limits = self.robot.get_limit_torque(self.state)
            
            # Populate Table
            self.torque_table.setRowCount(len(self.joint_names))
            
            is_deg = self.rb_deg.isChecked()
            max_torque_ratio = 0.0
            max_torque_joint = "N/A"
            
            for idx, j_name in enumerate(self.joint_names):
                pos_val = self.joint_widgets[j_name]["spin"].value() if j_name in self.joint_widgets else 0.0
                unit_str = " deg" if is_deg else " rad"
                
                grav_t = gravity_torques[idx]
                lim_t = tau_limits[idx]
                
                # Calculate loading percentage
                ratio = 0.0
                if lim_t > 0 and lim_t < 1e10:  # Valid non-infinite limit
                    ratio = abs(grav_t) / lim_t
                    if ratio > max_torque_ratio:
                        max_torque_ratio = ratio
                        max_torque_joint = j_name
                        
                # Create table items
                item_name = QTableWidgetItem(j_name)
                item_pos = QTableWidgetItem(f"{pos_val:.2f}{unit_str}")
                item_grav = QTableWidgetItem(f"{grav_t:7.3f} Nm")
                
                # Limit limit string representation
                if lim_t > 1e10:
                    item_lim = QTableWidgetItem("Continuous / Inf")
                    item_status = QTableWidgetItem("OK")
                    item_status.setForeground(QColor("#00FF66"))
                else:
                    item_lim = QTableWidgetItem(f"{lim_t:.1f} Nm")
                    percentage = ratio * 100.0
                    
                    if ratio >= 1.0:
                        item_status = QTableWidgetItem(f"OVERLOAD ({percentage:.1f}%)")
                        item_status.setForeground(QColor("#FF2E63"))
                        item_grav.setForeground(QColor("#FF2E63"))
                    elif ratio >= 0.8:
                        item_status = QTableWidgetItem(f"Warning ({percentage:.1f}%)")
                        item_status.setForeground(QColor("#FF9F43"))
                        item_grav.setForeground(QColor("#FF9F43"))
                    else:
                        item_status = QTableWidgetItem(f"OK ({percentage:.1f}%)")
                        item_status.setForeground(QColor("#00FF66"))
                        
                # Read-only items
                for item in [item_name, item_pos, item_grav, item_lim, item_status]:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignCenter)
                    
                self.torque_table.setItem(idx, 0, item_name)
                self.torque_table.setItem(idx, 1, item_pos)
                self.torque_table.setItem(idx, 2, item_grav)
                self.torque_table.setItem(idx, 3, item_lim)
                self.torque_table.setItem(idx, 4, item_status)
                
            # Update summary label
            if max_torque_joint != "N/A":
                self.lbl_max_gravity_torque.setText(
                    f"Max Gravity Torque Loading: {max_torque_ratio * 100.0:.1f}% on joint '{max_torque_joint}'"
                )
                if max_torque_ratio >= 1.0:
                    self.lbl_max_gravity_torque.setStyleSheet("color: #FF2E63; font-weight: bold;")
                elif max_torque_ratio >= 0.8:
                    self.lbl_max_gravity_torque.setStyleSheet("color: #FF9F43; font-weight: bold;")
                else:
                    self.lbl_max_gravity_torque.setStyleSheet("color: #00FF66; font-weight: bold;")
            else:
                self.lbl_max_gravity_torque.setText("Max Gravity Torque Loading: N/A")
                self.lbl_max_gravity_torque.setStyleSheet("color: #EEEEEE;")
                
        except Exception as e:
            # Silently catch or display error during updates
            self.matrix_txt.setPlainText(f"Error calculating kinematics/dynamics: {e}")

    def apply_dark_theme(self):
        """Applies a premium styling dark mode stylesheet (QSS) for the application."""
        self.setStyleSheet("""
            QWidget {
                background-color: #121212;
                color: #EEEEEE;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QGroupBox {
                border: 2px solid #222831;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 18px;
                font-size: 11pt;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 15px;
                padding: 0 5px;
                color: #00ADB5;
            }
            QLabel {
                color: #B5B5B5;
            }
            QComboBox {
                background-color: #222831;
                border: 1px solid #393E46;
                border-radius: 4px;
                padding: 5px 8px;
                color: #EEEEEE;
                min-height: 25px;
            }
            QComboBox:hover {
                border-color: #00ADB5;
            }
            QComboBox QAbstractItemView {
                background-color: #222831;
                color: #EEEEEE;
                selection-background-color: #00ADB5;
            }
            QDoubleSpinBox {
                background-color: #222831;
                border: 1px solid #393E46;
                border-radius: 4px;
                padding: 4px;
                color: #EEEEEE;
                min-width: 90px;
            }
            QDoubleSpinBox:focus {
                border-color: #00ADB5;
            }
            QSlider::groove:horizontal {
                border: 1px solid #393E46;
                height: 6px;
                background: #222831;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #00ADB5;
                border: 1px solid #00ADB5;
                width: 14px;
                height: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #00FFF6;
                border-color: #00FFF6;
            }
            QPushButton {
                background-color: #393E46;
                border: none;
                border-radius: 5px;
                color: #EEEEEE;
                padding: 8px 15px;
            }
            QPushButton:hover {
                background-color: #00ADB5;
                color: #121212;
                font-weight: bold;
            }
            QPushButton:pressed {
                background-color: #008085;
            }
            QRadioButton {
                color: #EEEEEE;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
            QRadioButton::indicator::unchecked {
                border: 2px solid #393E46;
                border-radius: 8px;
                background-color: #222831;
            }
            QRadioButton::indicator::checked {
                border: 2px solid #00ADB5;
                border-radius: 8px;
                background-color: #00ADB5;
            }
            QScrollArea {
                border: 1px solid #222831;
                background-color: #121212;
            }
            QScrollBar:vertical {
                border: none;
                background: #121212;
                width: 10px;
                margin: 0px 0 0px 0;
            }
            QScrollBar::handle:vertical {
                background: #393E46;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #00ADB5;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

def main():
    app = QApplication(sys.argv)
    ex = RobotDynamicsKinematicsApp()
    ex.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
