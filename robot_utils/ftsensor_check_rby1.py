import rby1_sdk as rby
import time
import numpy as np
import logging
import argparse

def monitor_ft_sensor(robot, duration_sec=60, rate_hz=10):
    print(f"\nMonitoring FT sensor data for {duration_sec} seconds...")
    print("-" * 80)
    print(f"{'Arm':<10} | {'Force (N)':<25} | {'Torque (Nm)':<25}")
    print("-" * 80)

    def callback(rs):
        rf = np.array2string(rs.ft_sensor_right.force, precision=2, separator=', ')
        rt = np.array2string(rs.ft_sensor_right.torque, precision=2, separator=', ')
        lf = np.array2string(rs.ft_sensor_left.force, precision=2, separator=', ')
        lt = np.array2string(rs.ft_sensor_left.torque, precision=2, separator=', ')

        print(f"\rRight FT: F {rf:<20} | T {rt:<20}\nLeft  FT: F {lf:<20} | T {lt:<20}", end="")
        print("\033[A", end="") 

    robot.start_state_update(callback, rate=rate_hz)
    try:
        time.sleep(duration_sec)
    except KeyboardInterrupt:
        pass
    finally:
        robot.stop_state_update()
        print("\n" * 2 + "FT sensor monitoring stopped.")

def main():
    parser = argparse.ArgumentParser(description="RBY1 FT Sensor Monitor")
    parser.add_argument("--address", type=str, default="127.0.0.1:50051", help="Robot address")
    parser.add_argument("--model", type=str, default="m", help="Robot model (a, m, etc.)")
    parser.add_argument("--duration", type=int, default=60, help="Monitoring duration in seconds")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    np.set_printoptions(suppress=True, precision=2)

    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        logging.error(f"Could not connect to robot at {args.address}")
        return

    if not robot.is_power_on(".*"):
        logging.info("Powering on...")
        if not robot.power_on(".*"):
            logging.error("Failed to power on")
            return

    monitor_ft_sensor(robot, duration_sec=args.duration)

if __name__ == "__main__":
    main()
