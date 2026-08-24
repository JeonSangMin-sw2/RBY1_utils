# Real Time Velocity Control Demo
# This example demonstrates how to control the robot using real time VELOCITY control.
#
# Usage example:
#     python realtime_example.py --address 127.0.0.1:50051 --model a
#
# Scenario:
# 1. Move to a ready position using the standard position builder.
# 2. Start a real-time control thread.
# 3. In the control loop, set mode=True for all joints to enable Velocity Mode.
# 4. Generate and send a sine-wave velocity profile (rad/s) to the mobile base and upper body joints.
# 5. Press Ctrl+C to gracefully stop the real-time loop.
#
# Copyright (c) 2025 Rainbow Robotics. All rights reserved.

import rby1_sdk as rby
import numpy as np
import argparse
import logging
import threading
import time
import gc

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

def move_to_ready_pose(robot, model_name):
    """ Move to Ready Position Before Starting the Motion """
    if model_name == "a":
        torso = np.deg2rad([0.0, 45.0, -90.0, 45.0, 0.0, 0.0])
        right_arm = np.deg2rad([0.0, -5.0, 0.0, -120.0, 0.0, 70.0, 0.0])
        left_arm = np.deg2rad([0.0, 5.0, 0.0, -120.0, 0.0, 70.0, 0.0])
    else:
        torso = np.deg2rad([0.0, 45.0, -90.0, 45.0, 0.0, 0.0])
        right_arm = np.deg2rad([0.0, -5.0, 0.0, -120.0, 0.0, 50.0, 0.0])
        left_arm = np.deg2rad([0.0, 5.0, 0.0, -120.0, 0.0, 50.0, 0.0])
        
    logging.info("Moving to ready pose...")
    if not movej(robot, torso=torso, right_arm=right_arm, left_arm=left_arm, minimum_time=5.0):
        exit(1)
    logging.info("Ready pose reached.")

class RealTimeVelocityControl:
    def __init__(self, address, model, power, servo):
        self.robot = initialize_robot(address, model, power, servo)
        self.model_name = model
        self.model = self.robot.model()
        
        self.local_t = 0.0
        self.is_running = True

        # Go to ready position using standard builder command before RT control
        move_to_ready_pose(self.robot, self.model_name)
        
        if model == "a":
            self.rt_thread = threading.Thread(
                target=self.robot.control,
                args=(self.control_a,),
            )
        elif model == "m":
            self.rt_thread = threading.Thread(
                target=self.robot.control,
                args=(self.control_m,),
            )

    def control_a(self, state: rby.Robot_A_ControlState):
        i = rby.Robot_A_ControlInput()
        
        # 1. Set mode to Velocity Control (True)
        i.mode.fill(True)
        
        # 2. Initialize target velocity array (Default 0 rad/s -> maintain current position)
        target_v = np.zeros_like(state.position)
        
        # 3. Create a sine wave trajectory (Amplitude: 0.2 rad/s, Frequency: 0.5 Hz)
        amp = 0.2
        freq = 0.5
        sine_vel = amp * np.sin(2.0 * np.pi * freq * self.local_t)
        
        # Keep the mobile base (2 wheels) moving at a constant 0.5 rad/s
        target_v[0:2] = 0.5
        
        # Apply the sine wave velocity ONLY to the right arm (indices 8 to 14)
        target_v[8:15] = sine_vel
        
        # 4. Input target velocity
        i.target = target_v
        
        # Set feedback gain appropriate for velocity control (Depends on low-level controller)
        i.feedback_gain.fill(5)
        
        # Optional: Feedforward torque
        i.feedforward_torque.fill(0)
        
        # Automatically stop after 3 seconds
        if self.local_t >= 3.0:
            self.is_running = False
            
        i.finish = not self.is_running
        
        # Increase the virtual local time by 0.002 seconds
        self.local_t += 0.002
        return i

    def control_m(self, state: rby.Robot_M_ControlState):
        i = rby.Robot_M_ControlInput()
        
        # 1. Set mode to Velocity Control (True)
        i.mode.fill(True)
        
        # 2. Initialize target velocity array (Default 0 rad/s -> maintain current position)
        target_v = np.zeros_like(state.position)
        
        # 3. Create a sine wave trajectory (Amplitude: 0.2 rad/s, Frequency: 0.5 Hz)
        amp = 0.2
        freq = 0.5
        sine_vel = amp * np.sin(2.0 * np.pi * freq * self.local_t)
        
        # Keep the mobile base (4 wheels) moving at a constant 0.5 rad/s
        target_v[0:4] = 0.5
        
        # Apply the sine wave velocity ONLY to the right arm (indices 10 to 16)
        target_v[10:17] = sine_vel
        
        # 4. Input target velocity
        i.target = target_v
        
        i.feedback_gain.fill(5)
        i.feedforward_torque.fill(0)
        
        # Automatically stop after 3 seconds
        if self.local_t >= 3.0:
            self.is_running = False
            
        i.finish = not self.is_running
        
        # Increase the virtual local time by 0.002 seconds
        self.local_t += 0.002
        return i

    def start(self):
        gc.disable()
        logging.info("Starting real-time velocity control... (It will automatically stop after 3 seconds)")
        self.rt_thread.start()

    def wait_for_done(self):
        try:
            while self.rt_thread.is_alive():
                self.rt_thread.join(0.1)
        except KeyboardInterrupt:
            print("\nInterrupted! Stopping control stream gracefully...")
            self.is_running = False
            self.rt_thread.join()
        finally:
            gc.enable()

def main(address, model, power, servo):
    rt_control = RealTimeVelocityControl(address, model, power, servo)
    rt_control.start()
    rt_control.wait_for_done()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real Time Velocity Control Example")
    parser.add_argument("--address", type=str, required=True, help="Robot address")
    parser.add_argument(
        "--model", 
        type=str, 
        default="a", 
        help="Robot Model Name (default: 'a')"
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
