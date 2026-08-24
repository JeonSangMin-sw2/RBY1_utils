import rby1_sdk as rby
import numpy as np
import argparse
import logging
import time
import threading

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

def movej(robot, torso=None, right_arm=None, left_arm=None, minimum_time=0):
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
        1,
    ).get()

    if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
        logging.error("Failed to conduct movej.")
        return False

    return True

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def move_to_ready_pose(robot, robot_model):
    logging.info("Moving to ready pose using movej...")
    if not movej(
        robot,
        torso=None if robot_model.model_name == "UB" else np.deg2rad([0.0, 45.0, -90.0, 45.0, 0.0, 0.0]),
        right_arm=np.deg2rad([0.0, -5.0, 0.0, -120.0, 0.0, 40.0, 0.0]),
        left_arm=np.deg2rad([0.0, 5.0, 0.0, -120.0, 0.0, 40.0, 0.0]),
        minimum_time=4.0,
    ):
        logging.error("Failed to move to ready pose")
        exit(1)

def send_cartesian_command(robot, T_target, joint_targets=None, minimum_time=4.0):
    builder = rby.CartesianCommandBuilder() \
        .add_target(
            "base", 
            "ee_right", 
            T_target, 
            0.5, # linear_velocity_limit
            1.5, # angular_velocity_limit
            0.8  # acceleration_limit_scaling
        ) \
        .set_minimum_time(minimum_time) \
        .set_stop_position_tracking_error(1e-3) \
        .set_stop_orientation_tracking_error(1e-3)
    
    if joint_targets is not None:
        for jt in joint_targets:
            joint_name, position, vel_limit, acc_limit = jt
            builder.add_joint_position_target(joint_name, position, vel_limit, acc_limit)

    rc = rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_body_command(
            rby.BodyComponentBasedCommandBuilder().set_right_arm_command(builder)
        )
    )
    
    cancel_time = minimum_time + 0.5
    future = robot.send_command(rc, 10)
    
    if not future.wait_for(int(cancel_time * 1000)):
        logging.warning(f"Command did not finish within {cancel_time}s. Canceling to prevent infinite blocking.")
        robot.cancel_control()
        future.get()
        return True

    rv = future.get()
    if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
        return False
    return True

def send_optimal_control_command(robot, T_target, joint_targets=None, cartesian_weight=1.0, joint_weight=1.0):
    builder = rby.OptimalControlCommandBuilder() \
        .add_cartesian_target(
            "base", 
            "ee_right", 
            T_target, 
            cartesian_weight, # position weight
            cartesian_weight  # orientation weight
        ) \
        .set_velocity_limit_scaling(0.5) \
        .set_error_scaling(1.5) \
        .set_stop_cost(1e-3) \
        .set_min_delta_cost(1e-5) \
        .set_patience(10)
    
    if joint_targets is not None:
        for jt in joint_targets:
            joint_name, position = jt
            builder.add_joint_position_target(joint_name, position, joint_weight)
            logging.info(f"Added OC joint target: {joint_name} -> {np.rad2deg(position):.1f} deg (weight: {joint_weight})")

    rc = rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_body_command(builder)
    )
    
    rv = robot.send_command(rc, 10).get()
    if rv.finish_code != rby.RobotCommandFeedback.FinishCode.Ok:
        logging.error("Optimal Control command failed")
        return False
    return True

def monitor_q(robot, stop_event, joint_names):
    """
    Background thread to monitor ALL right arm joint positions every 0.1 seconds.
    """
    right_arm_indices = [i for i, name in enumerate(joint_names) if name.startswith("right_arm")]
    
    while not stop_event.is_set():
        state = robot.get_state()
        q = state.position
        
        q_right_arm = [np.rad2deg(q[i]) for i in right_arm_indices]
        q_str = ", ".join([f"{val:6.1f}" for val in q_right_arm])
        logging.info(f"Monitor Q (Right Arm) -> [{q_str}]")
        time.sleep(0.1)

def main(address, model, power, servo):
    robot = initialize_robot(address, model, power, servo)
    robot_model = robot.model()

    robot.set_parameter("cartesian_command.cutoff_frequency", "5")
    robot.set_parameter("default.acceleration_limit_scaling", "0.8")

    # --- Joint Monitoring Thread ---
    stop_event = threading.Event()
    monitor_thread = threading.Thread(target=monitor_q, args=(robot, stop_event, robot_model.robot_joint_names))
    monitor_thread.start()
    # -------------------------------------------------------------------------------

    # 1. Move to ready pose using joint command
    # === 1. Go to Ready Pose ===
    move_to_ready_pose(robot, robot_model)
    time.sleep(1)
    # Calculate target Cartesian position from current pose
    dyn_robot = robot.get_dynamics()
    dyn_state = dyn_robot.make_state(["base", "ee_right"], robot_model.robot_joint_names)
    dyn_state.set_q(robot.get_state().position)
    dyn_robot.compute_forward_kinematics(dyn_state)
    T_ready = dyn_robot.compute_transformation(dyn_state, 0, 1)

    # Create target: Move +20cm along Z-axis from current position (move up)
    T_up = T_ready.copy()
    T_up[2, 3] += 0.20

    # 2. Move right arm UP using Cartesian command
    # === 2. Move right arm UP (Cartesian only) ===
    if not send_cartesian_command(robot, T_up):
        exit(1)

    # 3. Return to ready pose
    # === 3. Return to Ready Pose ===
    time.sleep(1)
    move_to_ready_pose(robot, robot_model)
    time.sleep(1)
    # 4. Move to the same Cartesian position with an additional joint target for shoulder roll
    # === 4. Move right arm UP with shoulder roll Target ===
    # Induce right_arm_1 (typically Shoulder Roll) to lift to -45 degrees while moving up
    joint_target_params = ("right_arm_1", np.deg2rad(-45.0), 1.0, 100.0) 
    if not send_cartesian_command(robot, T_up, joint_targets=[joint_target_params]):
        exit(1)

    # 5. Return to ready pose
    # === 5. Return to Ready Pose ===
    time.sleep(1)
    move_to_ready_pose(robot, robot_model)
    time.sleep(1)

    # 6. Move right arm UP with multiple joint targets (conflict test)
    # === 6. Move right arm UP with multiple joint targets (Conflict Test) ===
    # Add targets for shoulder roll, elbow pitch, and wrist pitch
    multiple_targets = [
        ("right_arm_1", np.deg2rad(-45.0), 1.0, 100.0),
        ("right_arm_3", np.deg2rad(-100.0), 1.0, 100.0),
        ("right_arm_4", np.deg2rad(10.0), 1.0, 100.0)
    ]
    if not send_cartesian_command(robot, T_up, joint_targets=multiple_targets):
        exit(1)

    # 7. Return to ready pose
    # === 7. Return to Ready Pose ===
    time.sleep(1)
    move_to_ready_pose(robot, robot_model)
    time.sleep(1)

    # === All Scenarios Completed Successfully ===

    # --- Optional: Stop Joint Monitoring Thread ---
    stop_event.set()
    monitor_thread.join()
    # ----------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cartesian Example with Joint Target")
    parser.add_argument("--address", type=str, required=True, help="Robot address")
    parser.add_argument("--model", type=str, default="a", help="Robot Model Name (default: 'a')")
    parser.add_argument(
        "--power", type=str, default=".*", help="Power device name regex pattern"
    )
    parser.add_argument(
        "--servo", type=str, default=".*", help="Servo name regex pattern"
    )
    args = parser.parse_args()

    main(
        address=args.address,
        model=args.model,
        power=args.power,
        servo=args.servo,
    )
