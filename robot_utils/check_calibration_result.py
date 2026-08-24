import rby1_sdk
import numpy as np
import sys
import time
import argparse
from rby1_sdk import *
import math

# ============================================================
# Robot initialization
# ============================================================
D2R = np.pi / 180  # Degree to Radian conversion factor
MINIMUM_TIME = 5
LINEAR_VELOCITY_LIMIT = 1.5
ANGULAR_VELOCITY_LIMIT = np.pi * 1.5
ACCELERATION_LIMIT = 1.0
STOP_ORIENTATION_TRACKING_ERROR = 1e-4
STOP_POSITION_TRACKING_ERROR = 1e-3
WEIGHT = 1
STOP_COST = WEIGHT * WEIGHT * 2e-3
MIN_DELTA_COST = WEIGHT * WEIGHT * 2e-3
PATIENCE = 10

robot = None

def make_transform(data):
        # data: [x, y, z, roll, pitch, yaw] (x,y,z in meters, r,p,y in degrees)
        # x, y, z = data[0]*1000, data[1]*1000, data[2]*1000 
        x, y, z = data[0], data[1], data[2]
        roll = data[3] * math.pi / 180
        pitch = data[4] * math.pi / 180
        yaw = data[5] * math.pi / 180
        
        cr = math.cos(roll); sr = math.sin(roll)
        cp = math.cos(pitch); sp = math.sin(pitch)
        cy = math.cos(yaw); sy = math.sin(yaw)
        
        m = np.eye(4, dtype=np.float32)
        m[0, 0] = cy * cp
        m[0, 1] = sr * sp * cy - cr * sy
        m[0, 2] = cr * sp * cy + sr * sy
        m[0, 3] = x
        
        m[1, 0] = sy * cp
        m[1, 1] = sr * sp * sy + cr * cy
        m[1, 2] = cr * sp * sy - sr * cy
        m[1, 3] = y
        
        m[2, 0] = -sp
        m[2, 1] = cp * sr
        m[2, 2] = cp * cr
        m[2, 3] = z

        # print(m)
        
        return m
    
def joint_position_command(robot, model_name, right_arm, left_arm):
    print("joint position command")

    q_joint_torso = np.array([0,0,0,0,0,0])* D2R
        
    q_joint_left_arm =  np.array(left_arm) * D2R
    q_joint_right_arm = np.array(right_arm) * D2R

    # Combine joint positions
    q = np.concatenate([q_joint_torso, q_joint_right_arm, q_joint_left_arm])

    # Build command
    rc = RobotCommandBuilder().set_command(
        ComponentBasedCommandBuilder().set_body_command(
            BodyCommandBuilder().set_command(
                JointPositionCommandBuilder()
                .set_position(q)
                .set_minimum_time(MINIMUM_TIME)
            )
        )
    )

    rv = robot.send_command(rc, 10).get()

    if rv.finish_code != RobotCommandFeedback.FinishCode.Ok:
        print("Error: Failed to conduct demo motion.")
        return 1

    return 0

def go_to_home_pose(robot, model_name):
    print("Go to home pose")

    if model_name =="a":
        target_joint = np.zeros(20)
    elif model_name == "m":
        target_joint = np.zeros(20)
        
    # Send command to go to home pose
    rv = robot.send_command(
        RobotCommandBuilder().set_command(
            ComponentBasedCommandBuilder().set_body_command(
                JointPositionCommandBuilder()
                .set_position(target_joint)
                .set_minimum_time(MINIMUM_TIME)
            )
        ),
        10,
    ).get()

    if rv.finish_code != RobotCommandFeedback.FinishCode.Ok:
        print("Error: Failed to conduct demo motion.")
        return 1

    return 0

def go_to_ready_pose(robot, model_name):
    print("Go to ready pose")

    # Define joint positions
    if model_name == "a":
        q_joint_torso = np.array([0, 30, -60, 30, 0, 0]) * D2R
    elif model_name == "m":
        q_joint_torso = np.array([0, 30, -60, 30, 0, 0]) * D2R
        
    q_joint_right_arm = np.array([-45, -30, 0, -90, 0, 45, 0]) * D2R
    q_joint_left_arm = np.array([-45, 30, 0, -90, 0, 45, 0]) * D2R

    # Combine joint positions
    q = np.concatenate([q_joint_torso, q_joint_right_arm, q_joint_left_arm])

    # Build command
    rc = RobotCommandBuilder().set_command(
        ComponentBasedCommandBuilder().set_body_command(
            BodyCommandBuilder().set_command(
                JointPositionCommandBuilder()
                .set_position(q)
                .set_minimum_time(MINIMUM_TIME)
            )
        )
    )

    rv = robot.send_command(rc, 10).get()

    if rv.finish_code != RobotCommandFeedback.FinishCode.Ok:
        print("Error: Failed to conduct demo motion.")
        return 1

    return 0

def go_to_calibration_checking_pose(robot, model_name, data, offset):

    # Initialize transformation matrices
    T_torso = np.eye(4)
    P_right = [data[0], data[1] - offset, data[2],90,-90,0.0]
    P_left = [data[0], data[1] + offset, data[2],-90,-90,0.0]
    T_right = make_transform(P_right)
    T_left = make_transform(P_left)

    # Build command
    rc = RobotCommandBuilder().set_command(
        ComponentBasedCommandBuilder().set_body_command(
            BodyComponentBasedCommandBuilder()
            # .set_torso_command(
            #     CartesianCommandBuilder()
            #     .add_target(
            #         "base",
            #         target_link,
            #         T_torso,
            #         LINEAR_VELOCITY_LIMIT,
            #         ANGULAR_VELOCITY_LIMIT,
            #         ACCELERATION_LIMIT,
            #     )
            #     .set_minimum_time(MINIMUM_TIME)
            #     .set_stop_orientation_tracking_error(STOP_ORIENTATION_TRACKING_ERROR)
            #     .set_stop_position_tracking_error(STOP_POSITION_TRACKING_ERROR)
            # )
            .set_right_arm_command(
                CartesianCommandBuilder()
                .add_target(
                    "link_torso_5",
                    "ee_right",
                    T_right,
                    LINEAR_VELOCITY_LIMIT,
                    ANGULAR_VELOCITY_LIMIT,
                    ACCELERATION_LIMIT,
                )
                .set_minimum_time(MINIMUM_TIME)
                .set_command_header(CommandHeaderBuilder().set_control_hold_time(1))
                .set_stop_orientation_tracking_error(STOP_ORIENTATION_TRACKING_ERROR)
                .set_stop_position_tracking_error(STOP_POSITION_TRACKING_ERROR)
            )
            .set_left_arm_command(
                CartesianCommandBuilder()
                .add_target(
                    "link_torso_5",
                    "ee_left",
                    T_left,
                    LINEAR_VELOCITY_LIMIT,
                    ANGULAR_VELOCITY_LIMIT,
                    ACCELERATION_LIMIT,
                )
                .set_minimum_time(MINIMUM_TIME)
                .set_command_header(CommandHeaderBuilder().set_control_hold_time(1))
                .set_stop_orientation_tracking_error(STOP_ORIENTATION_TRACKING_ERROR)
                .set_stop_position_tracking_error(STOP_POSITION_TRACKING_ERROR)
            )
        )
    )

    rv = robot.send_command(rc, 10).get()

    if rv.finish_code != RobotCommandFeedback.FinishCode.Ok:
        print("Error: Failed to conduct motion.")
        return 1

    return 0

def initialize_robot(address, model_name, power, servo):
    robot = rby1_sdk.create_robot(address, model_name)

    if not robot.connect():
        print("Error: Unable to establish connection to the robot at")
        sys.exit(1)

    print("Successfully connected to the robot")

    print("Starting state update...")
    robot.set_parameter("default.acceleration_limit_scaling", "1.0")
    robot.set_parameter("joint_position_command.cutoff_frequency", "5")
    robot.set_parameter("cartesian_command.cutoff_frequency", "5")
    robot.set_parameter("default.linear_acceleration_limit", "20")
    robot.set_parameter("default.angular_acceleration_limit", "10")
    robot.set_parameter("manipulability_threshold", "1e4")

    print("parameters setting is done")

    if not robot.is_connected():
        print("Robot is not connected")
        exit(1)

    if not robot.is_power_on(power):
        rv = robot.power_on(power)
        if not rv:
            print("Failed to power on")
            exit(1)

    print(servo)
    if not robot.is_servo_on(servo):
        rv = robot.servo_on(servo)
        if not rv:
            print("Fail to servo on")
            exit(1)

    control_manager_state = robot.get_control_manager_state()

    if (
        control_manager_state.state == rby1_sdk.ControlManagerState.State.MinorFault
        or control_manager_state.state == rby1_sdk.ControlManagerState.State.MajorFault
    ):

        if control_manager_state.state == rby1_sdk.ControlManagerState.State.MajorFault:
            print(
                "Warning: Detected a Major Fault in the Control Manager!!!!!!!!!!!!!!!."
            )
        else:
            print(
                "Warning: Detected a Minor Fault in the Control Manager@@@@@@@@@@@@@@@@."
            )

        print("Attempting to reset the fault...")
        if not robot.reset_fault_control_manager():
            print("Error: Unable to reset the fault in the Control Manager.")
            sys.exit(1)
        print("Fault reset successfully.")

    print("Control Manager state is normal. No faults detected.")

    print("Enabling the Control Manager...")
    if not robot.enable_control_manager(unlimited_mode_enabled=True):
        print("Error: Failed to enable the Control Manager.")
        sys.exit(1)
    print("Control Manager enabled successfully.")
    return robot

def cal_offset(ref_T, cur_T):
    error = np.linalg.inv(ref_T) @ cur_T
    sy = math.sqrt(error[0][0]**2 + error[1][0]**2)
    singular = sy < 1e-6
    if not singular:
        roll = math.atan2(error[2][1], error[2][2])
        pitch = math.atan2(-error[2][0], sy)
        yaw = math.atan2(error[1][0], error[0][0])
    else:
        roll = math.atan2(-error[1][2], error[1][1])
        pitch = math.atan2(-error[2][0], sy)
        yaw = 0
    return [error[0][3], error[1][3], error[2][3], roll, pitch, yaw]

def main(address, model_name, power, servo):
    print("Attempting to connect to the robot...")
    

    global robot
    
    robot = initialize_robot(address, model_name, power, servo)
    # BASE, EE = 0, 1
    go_to_ready_pose(robot, model_name)
    time.sleep(1)
    go_to_calibration_checking_pose(robot, model_name, [0.35,-0.0,-0.0], 0.16)
    time.sleep(2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="09_demo_motion")
    parser.add_argument("--address", type=str, required=True, help="Robot address")
    parser.add_argument("--model", type=str, default='a', help="Robot Model Name (default: 'a')")
    parser.add_argument(
        "--power",
        type=str,
        default=".*",
        help="Power device name regex pattern (default: '.*')",
    )
    parser.add_argument(
        "--servo",
        type=str,
        default= "^(?!.*head).*",
        help="Servo name regex pattern (default: '.*')",
    )
    args = parser.parse_args()

    main(address=args.address, model_name = args.model, power=args.power, servo=args.servo)