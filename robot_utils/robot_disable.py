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
    time.sleep(1.0)
    if not robot.disable_control_manager():
        logging.error(f"Failed to enable control manager")
        exit(1)
    return robot



def main(address, model, power, servo):
    robot = initialize_robot(address, model, power, servo)

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
