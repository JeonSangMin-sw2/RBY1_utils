import rby1_sdk as rby
import time
import numpy as np
import logging
import argparse

def monitor_tool_flange(robot, duration_sec=60, rate_hz=10):
    print(f"\nMonitoring Tool Flange data (Press Ctrl+C to stop)")
    print("-" * 110)
    header = f"{'Side':<8} | {'SW':<4} | {'Vout':<6} | {'In':<3} | {'Out':<3} | {'Gyro (x,y,z)':<26} | {'Accel (x,y,z)':<26}"
    print(header)
    print("-" * 110)

    def callback(rs):
        def format_tf(tf):
            sw = "PUSH" if tf.switch_A else "REL"
            # Ensure voltage output doesn't break alignment
            v_out = f"{tf.output_voltage:4d}"[:4] + "V"
            di = f"{int(tf.digital_input_A)}{int(tf.digital_input_B)}"
            do = f"{int(tf.digital_output_A)}{int(tf.digital_output_B)}"
            
            # Format vectors compactly
            g = tf.gyro
            gyro_str = f"({g[0]:.2f},{g[1]:.2f},{g[2]:.2f})"
            a = tf.acceleration
            accel_str = f"({a[0]:.2f},{a[1]:.2f},{a[2]:.2f})"
            
            return f"{sw:<4} | {v_out:<6} | {di:<3} | {do:<3} | {gyro_str:<26} | {accel_str:<26}"

        r_str = format_tf(rs.tool_flange_right)
        l_str = format_tf(rs.tool_flange_left)

        output = f"\rRight: {r_str}\nLeft : {l_str}"
        print(output, end="")
        print("\033[A", end="") 

    robot.start_state_update(callback, rate=rate_hz)
    try:
        time.sleep(duration_sec)
    except KeyboardInterrupt:
        pass
    finally:
        robot.stop_state_update()
        print("\n" * 2 + "Tool Flange monitoring stopped.")

def main():
    parser = argparse.ArgumentParser(description="RBY1 Tool Flange Monitor")
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

    monitor_tool_flange(robot, duration_sec=args.duration)

if __name__ == "__main__":
    main()
