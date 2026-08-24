# Stream Command Switching Example
# This example demonstrates how to:
# 1. Initialize the robot (power on, servo on)
# 2. Move to Zero Pose using movej
# 3. Explicitly release control to clear priority holds
# 4. Stream Joint Positions to Ready Pose (static target, minimum_time = 4.0s)
# 5. In mid-movement (after 2.0s), switch to Cartesian command streaming on the same stream
#    under the exact same priority (1).
#
# Usage example:
#     python stream_command_switching.py --address localhost:50051 --model m

import sys
import argparse
import logging
import time
import signal
import numpy as np
import rby1_sdk as rby

# Initialize logger
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def initialize_robot(address, model, power=".*", servo=".*"):
    robot = rby.create_robot(address, model)
    if not robot.connect():
        logging.error(f"Failed to connect robot {address}")
        exit(1)
    if not robot.is_power_on(power):
        if not robot.power_on(power):
            logging.error(f"Failed to turn power ({power}) on")
            exit(1)
    if not robot.is_servo_on(servo):
        if not robot.servo_on(servo):
            logging.error(f"Failed to servo ({servo}) on")
            exit(1)
    if robot.get_control_manager_state().state in [
        rby.ControlManagerState.State.MajorFault,
        rby.ControlManagerState.State.MinorFault,
    ]:
        if not robot.reset_fault_control_manager():
            logging.error(f"Failed to reset control manager")
            exit(1)
    if not robot.enable_control_manager():
        logging.error(f"Failed to enable control manager")
        exit(1)
    return robot


def movej(robot, torso=None, right_arm=None, left_arm=None, minimum_time=0, priority=1):
    rc = rby.BodyComponentBasedCommandBuilder()
    if torso is not None:
        rc.set_torso_command(
            rby.JointPositionCommandBuilder()
            .set_minimum_time(minimum_time)
            .set_position(torso)
        )
    if right_arm is not None:
        rc.set_right_arm_command(
            rby.JointPositionCommandBuilder()
            .set_minimum_time(minimum_time)
            .set_position(right_arm)
        )
    if left_arm is not None:
        rc.set_left_arm_command(
            rby.JointPositionCommandBuilder()
            .set_minimum_time(minimum_time)
            .set_position(left_arm)
        )

    rv = robot.send_command(
        rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(rc)
        ),
        priority,
    ).get()

    if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
        logging.warning(f"movej did not finish with Ok status (FinishCode: {rv.finish_code}).")
        return False

    return True


def main(address, model, power, servo):
    logging.info("===== Starting Stream Command Switching Example =====")

    # 1. Initialize Robot (Power On, Servo On)
    robot = initialize_robot(address, model, power, servo)

    # Set parameters for cartesian command control
    robot.set_parameter("cartesian_command.cutoff_frequency", "5")

    robot_model = robot.model()
    torso_dof = len(robot_model.torso_idx)
    right_arm_dof = len(robot_model.right_arm_idx)
    left_arm_dof = len(robot_model.left_arm_idx)

    # 2. Move to Zero Pose (blocking, priority=1)
    logging.info("Moving to Zero Pose...")
    if not movej(
        robot,
        torso=np.zeros(torso_dof),
        right_arm=np.zeros(right_arm_dof),
        left_arm=np.zeros(left_arm_dof),
        minimum_time=5.0,
        priority=1,
    ):
        logging.error("Failed to move to Zero Pose.")
        exit(1)
    logging.info("Reached Zero Pose.")

    # 3. Explicitly cancel active control to clear any priority holds before creating stream
    logging.info("Releasing active control locks...")
    robot.cancel_control()
    time.sleep(0.5)

    # Ready pose configuration
    torso_ready = np.array([0.0, 0.1, -0.2, 0.1, 0.0, 0.0])
    right_arm_ready = np.array([0.2, -0.2, 0.0, -1.0, 0.0, 0.7, 0.0])
    left_arm_ready = np.array([0.2, 0.2, 0.0, -1.0, 0.0, 0.7, 0.0])

    dt = 0.01

    # Compute Cartesian Position and Build Command BEFORE starting/switching the stream to minimize delay
    dyn_robot = robot.get_dynamics()
    dyn_state = dyn_robot.make_state(
        ["base", "ee_right"], robot_model.robot_joint_names
    )
    BASE_LINK_IDX = 0
    EE_RIGHT_LINK_IDX = 1

    dyn_state.set_q(robot.get_state().position)
    dyn_robot.compute_forward_kinematics(dyn_state)
    T_ref = dyn_robot.compute_transformation(
        dyn_state, BASE_LINK_IDX, EE_RIGHT_LINK_IDX
    )

    # Define a relative target (Z offset of -0.05m from the captured transient frame)
    target = T_ref.copy()
    target[2, 3] -= 0.05

    def build_cartesian_command(T: np.typing.NDArray):
        rc = rby.RobotCommandBuilder().set_command(
            rby.ComponentBasedCommandBuilder().set_body_command(
                rby.BodyComponentBasedCommandBuilder().set_right_arm_command(
                    rby.CartesianCommandBuilder()
                    .set_command_header(
                        rby.CommandHeaderBuilder().set_control_hold_time(1e6)
                    )
                    .add_joint_position_target("right_arm_2", 0.5, 1, 100)
                    .add_target("base", "ee_right", T, 0.3, 100.0, 0.8)
                    .set_minimum_time(2)
                )
            )
        )
        return rc

    cartesian_rc = build_cartesian_command(target)

    # 4. Initialize Command Stream with priority=1
    logging.info("Activating Command Stream with priority=1...")
    stream = robot.create_command_stream(priority=1)

    def sigint_handler(signum, frame):
        logging.info("SIGINT received, cancelling stream...")
        stream.cancel()
        exit(1)

    signal.signal(signal.SIGINT, sigint_handler)

    # Map ready pose into robot joint configuration using ComponentBased command to avoid target position length errors
    body_command = rby.BodyComponentBasedCommandBuilder()
    body_command.set_torso_command(
        rby.JointPositionCommandBuilder()
        .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1e6))
        .set_minimum_time(4.0)
        .set_position(list(torso_ready))
    )
    body_command.set_right_arm_command(
        rby.JointPositionCommandBuilder()
        .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1e6))
        .set_minimum_time(4.0)
        .set_position(list(right_arm_ready))
    )
    body_command.set_left_arm_command(
        rby.JointPositionCommandBuilder()
        .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1e6))
        .set_minimum_time(4.0)
        .set_position(list(left_arm_ready))
    )

    joint_rc = rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_body_command(body_command)
    )

    # Stream the joint command repeatedly for 2.0s to maintain the stream alive
    logging.info("Streaming Joint Position command for 2.0s to keep stream alive...")
    switch_time = 2.0
    switch_steps = int(switch_time / dt)
    for _ in range(switch_steps):
        stream.send_command(joint_rc)
        time.sleep(dt)

    logging.info("Switching to Cartesian stream command on the same stream (priority=1)...")

    # 5. Move to Cartesian Position while in motion
    log_count = 0
    while True:
        # Keep sending Cartesian target commands down the stream to maintain control hold
        stream.send_command(cartesian_rc)

        feedback = stream.request_feedback()

        def extract_cartesian_command_feedback(f):
            return (
                f.component_based_command.body_command
                .body_component_based_command.right_arm_command
                .cartesian_command
            )

        cartesian_feedback = extract_cartesian_command_feedback(feedback)

        # Avoid index error when feedback list is momentarily empty
        if len(cartesian_feedback.se3_pose_tracking_errors) == 0:
            time.sleep(dt)
            continue

        if log_count % 100 == 0:
            logging.info(
                f"Position error: {cartesian_feedback.se3_pose_tracking_errors[0].position_error:.4f}, "
                f"Manipulability: {cartesian_feedback.manipulability:.4f}"
            )
        log_count += 1

        # Check if the position error is within threshold (1cm)
        if cartesian_feedback.se3_pose_tracking_errors[0].position_error < 1e-2:
            logging.info("Target reached via Cartesian Stream switching.")
            break
        time.sleep(dt)

    # Clean up stream
    stream.cancel()
    logging.info("===== Stream Command Switching Example Finished =====")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="stream_command_switching")
    parser.add_argument("--address", type=str, required=True, help="Robot address")
    parser.add_argument(
        "--model", type=str, default="a", help="Robot Model Name (default: 'a')"
    )
    parser.add_argument(
        "--power",
        type=str,
        default=".*",
        help="Power device name regex pattern (default: '.*')",
    )
    parser.add_argument(
        "--servo",
        type=str,
        default=".*",
        help="Servo name regex pattern (default: '.*')",
    )
    args = parser.parse_args()

    main(
        address=args.address,
        model=args.model,
        power=args.power,
        servo=args.servo,
    )
