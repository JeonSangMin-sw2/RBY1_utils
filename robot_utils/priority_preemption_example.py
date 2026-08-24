import rby1_sdk as rby
import numpy as np
import argparse
import logging
import time
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

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

def movej(robot, right_arm, minimum_time=4.0, priority=1):
    rc = rby.BodyComponentBasedCommandBuilder()
    rc.set_right_arm_command(
        rby.JointPositionCommandBuilder()
        .set_minimum_time(minimum_time)
        .set_position(right_arm)
    )
    cmd = rby.RobotCommandBuilder().set_command(
        rby.ComponentBasedCommandBuilder().set_body_command(rc)
    )
    future = robot.send_command(cmd, priority)
    return future

def monitor_joint(robot, stop_event, joint_names):
    """
    Background thread to monitor right_arm_3 joint position.
    """
    try:
        idx = joint_names.index("right_arm_3")
    except ValueError:
        logging.error("Could not find 'right_arm_3' in joint names.")
        return
        
    while not stop_event.is_set():
        state = robot.get_state()
        q_val = np.rad2deg(state.position[idx])
        logging.info(f"[MONITOR] right_arm_3: {q_val:6.1f} deg")
        time.sleep(0.1)

def main(address, model, power, servo):
    robot = initialize_robot(address, model, power, servo)
    robot_model = robot.model()

    # --- Joint Monitoring Thread ---
    stop_event = threading.Event()
    # monitor_thread = threading.Thread(target=monitor_joint, args=(robot, stop_event, robot_model.robot_joint_names))
    # monitor_thread.start()

    # Define Poses (right_arm_3 is at index 3 in right_arm joints)
    # Joint configuration: [right_arm_0, right_arm_1, right_arm_2, right_arm_3, right_arm_4, right_arm_5, right_arm_6]
    # Standard ready pose from cartesian_example.py: [0.0, -5.0, 0.0, -120.0, 0.0, 40.0, 0.0]
    # We will modify the 4th element (right_arm_3)
    pose_ready = np.deg2rad([0.0, -5.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    pose_90 = np.deg2rad([0.0, -5.0, 0.0, -90.0, 0.0, 0.0, 0.0])
    pose_0 = np.deg2rad([0.0, -5.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    try:
        # === 0. Move to Ready Pose (right_arm_3 = 0 deg) ===
        logging.info("=========================================")
        logging.info("0. Moving right_arm_3 to 0 deg (Ready Pose)...")
        future = movej(robot, pose_ready, minimum_time=4.0, priority=1)
        rv = future.get()
        logging.info(f"Ready pose move finished with code: {rv.finish_code}")
        time.sleep(1.0)

        # === Scenario 1: Same Priority Preemption Test ===
        # Test: If a command with the SAME priority comes in while one is running,
        # is the new command ignored (preempted), or does it cancel the old one?
        logging.info("=========================================")
        logging.info("Scenario 1: Same Priority Preemption Test (Priority 10 vs 10)")
        
        logging.info("Sending command 1: Move right_arm_3 to 90 deg (Priority 10, Duration 4s)")
        future1 = movej(robot, pose_90, minimum_time=4.0, priority=10)
        
        time.sleep(1.0) # Wait 1 second so command 1 is active and moving
        
        logging.info("Sending command 2: Move right_arm_3 to 0 deg (Priority 10, Duration 4s)")
        future2 = movej(robot, pose_0, minimum_time=4.0, priority=10)
        
        # Check command 2 status
        logging.info("Waiting for command 2 result...")
        rv2 = future2.get()
        logging.info(f"Command 2 finished with code: {rv2.finish_code}")
        if rv2.finish_code == rby.RobotCommandFeedback.FinishCode.Preempted:
            logging.info("-> RESULT: Command 2 was PREEMPTED (ignored) because its priority (10) was not higher than Command 1 (10).")
        else:
            logging.info(f"-> RESULT: Command 2 executed or ended with code: {rv2.finish_code}")

        # Wait for command 1 to finish
        logging.info("Waiting for command 1 to complete...")
        rv1 = future1.get()
        logging.info(f"Command 1 finished with code: {rv1.finish_code}")
        time.sleep(1.0)

        # === Reset: Return to 0 deg if it successfully reached 90 deg ===
        logging.info("=========================================")
        logging.info("Resetting right_arm_3 to 0 deg...")
        future = movej(robot, pose_ready, minimum_time=4.0, priority=1)
        future.get()
        time.sleep(1.0)

        # === Scenario 2: Higher Priority Preemption & Priority > 100 Test ===
        # Test: Can priority exceed 100?
        # Test: If priority 101 comes after priority 99, is priority 99 canceled and priority 101 executed?
        logging.info("=========================================")
        logging.info("Scenario 2: Higher Priority Preemption Test (Priority 99 vs 101)")
        
        logging.info("Sending command 3: Move right_arm_3 to 90 deg (Priority 99, Duration 4s)")
        future3 = movej(robot, pose_90, minimum_time=4.0, priority=99)
        
        time.sleep(1.0) # Wait 1 second so command 3 is active and moving
        
        logging.info("Sending command 4: Move right_arm_3 to 0 deg (Priority 101, Duration 4s)")
        future4 = movej(robot, pose_0, minimum_time=4.0, priority=101)
        
        # We expect command 3 (priority 99) to be preempted (canceled) by command 4 (priority 101)
        logging.info("Waiting for command 3 result...")
        rv3 = future3.get()
        logging.info(f"Command 3 finished with code: {rv3.finish_code}")
        if rv3.finish_code == rby.RobotCommandFeedback.FinishCode.Preempted:
            logging.info("-> RESULT: Command 3 (Priority 99) was PREEMPTED (canceled) by higher priority command.")
        else:
            logging.info(f"-> RESULT: Command 3 ended with code: {rv3.finish_code}")

        # Check command 4 status
        logging.info("Waiting for command 4 to complete...")
        rv4 = future4.get()
        logging.info(f"Command 4 finished with code: {rv4.finish_code}")
        if rv4.finish_code == rby.RobotCommandFeedback.FinishCode.Ok:
            logging.info("-> RESULT: Command 4 (Priority 101) successfully completed! Priority > 100 is supported.")
        else:
            logging.info(f"-> RESULT: Command 4 ended with code: {rv4.finish_code}")

    finally:
        stop_event.set()
        # monitor_thread.join()
        logging.info("Example finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Priority and Preemption Example")
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
