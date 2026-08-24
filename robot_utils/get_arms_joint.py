#!/usr/bin/env python3
import argparse
import sys
import numpy as np
import rby1_sdk as rby

def main():
    parser = argparse.ArgumentParser(description="Connect to robot and print joint angles for both arms.")
    parser.add_argument("--address", type=str, required=True, help="Robot IP address (e.g., 192.168.30.1:50051)")
    parser.add_argument("--model", type=str, default="a", help="Robot model name (default: 'a')")
    parser.add_argument("--degree", action="store_true", help="Print joint values in degrees instead of radians")

    args = parser.parse_args()

    # Create robot instance and connect
    print(f"Connecting to robot at {args.address} (Model: {args.model})...")
    robot = rby.create_robot(args.address, args.model)
    
    if not robot.connect():
        print(f"Error: Failed to connect to robot at {args.address}", file=sys.stderr)
        sys.exit(1)

    try:
        # Retrieve robot model indices and current state
        model = robot.model()
        state = robot.get_state()
        
        q_full = np.array(state.position)
        
        # Slice joint values using arm index arrays
        right_arm_q = q_full[model.right_arm_idx]
        left_arm_q = q_full[model.left_arm_idx]
        
        unit = "deg" if args.degree else "rad"
        
        # Convert to degrees if requested
        if args.degree:
            right_arm_q = np.rad2deg(right_arm_q)
            left_arm_q = np.rad2deg(left_arm_q)

        # Output formatting
        print("\n==================================================")
        print(f" Robot Arm Joints (Unit: {unit})")
        print("==================================================")
        
        print("\n[Right Arm Joints]")
        print("["+ str(right_arm_q[0]) + ", " + str(right_arm_q[1]) + ", " + str(right_arm_q[2]) + ", " + str(right_arm_q[3]) + ", " + str(right_arm_q[4]) + ", " + str(right_arm_q[5]) + ", " + str(right_arm_q[6]) + "]")
        print("\n[Left Arm Joints]")
        print("["+ str(left_arm_q[0]) + ", " + str(left_arm_q[1]) + ", " + str(left_arm_q[2]) + ", " + str(left_arm_q[3]) + ", " + str(left_arm_q[4]) + ", " + str(left_arm_q[5]) + ", " + str(left_arm_q[6]) + "]")
        print("==================================================")
        
    except Exception as e:
        print(f"Error reading robot state: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
