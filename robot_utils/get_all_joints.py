#!/usr/bin/env python3
"""
RBY1 Robot Joint State Viewer (All Parts: Torso, Right Arm, Left Arm, Head)

Prints joint angles in both degrees and radians for all parts of the robot.
Supports one-shot print and continuous live monitoring (--watch / -w).

Usage examples:
    python3 get_all_joints.py
    python3 get_all_joints.py --model m
    python3 get_all_joints.py --watch
    python3 get_all_joints.py --address 192.168.30.1:50051 --watch --hz 20
"""

import argparse
import sys
import time
import datetime
import numpy as np
import rby1_sdk as rby

DEFAULT_ROBOT_ADDRESS = "192.168.30.1:50051"
DEFAULT_ROBOT_MODEL = "a"


def format_part_block(name: str, q: np.ndarray, joint_names: list = None) -> str:
    deg = np.rad2deg(q)
    deg_str = ", ".join([f"{d:7.2f}" for d in deg])
    rad_str = ", ".join([f"{r:7.4f}" for r in q])
    code_deg = ", ".join([f"{d:.1f}" for d in deg])

    lines = [f"=== {name} ({len(q)}-DOF) ==="]
    if joint_names:
        name_str = ", ".join([f"{jn:>7s}" for jn in joint_names])
        lines.append(f"Joint:    [{name_str}]")
    lines.append(f"deg:      [{deg_str}]")
    lines.append(f"rad:      [{rad_str}]")
    lines.append(f"Python:   np.deg2rad([{code_deg}])")
    return "\n".join(lines)


def print_full_snapshot(state: rby.RobotState_A, model):
    q_full = np.array(state.position)

    torso_q = q_full[model.torso_idx]
    right_arm_q = q_full[model.right_arm_idx]
    left_arm_q = q_full[model.left_arm_idx]
    head_q = q_full[model.head_idx]

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    header = f"========================================================================\n" \
             f" RBY1 Robot Joint States (Model: {model.model_name.upper()}) | {now_str}\n" \
             f"========================================================================"

    torso_names = ["T0", "T1", "T2", "T3", "T4", "T5"]
    r_arm_names = [f"R{i}" for i in range(len(right_arm_q))]
    l_arm_names = [f"L{i}" for i in range(len(left_arm_q))]
    head_names = ["H0_Pan", "H1_Tilt"] if len(head_q) == 2 else [f"H{i}" for i in range(len(head_q))]

    block_torso = format_part_block("Torso", torso_q, torso_names)
    block_right = format_part_block("Right Arm", right_arm_q, r_arm_names)
    block_left = format_part_block("Left Arm", left_arm_q, l_arm_names)
    block_head = format_part_block("Head", head_q, head_names)

    # Copy-pasteable Pose definition for ready poses / trajectories
    deg_t = ", ".join([f"{d:.1f}" for d in np.rad2deg(torso_q)])
    deg_r = ", ".join([f"{d:.1f}" for d in np.rad2deg(right_arm_q)])
    deg_l = ", ".join([f"{d:.1f}" for d in np.rad2deg(left_arm_q)])

    pose_snippet = (
        "=== Python Pose Snippet (Ready for script paste) ===\n"
        "Pose(\n"
        f"    toros=np.deg2rad([{deg_t}]),\n"
        f"    right_arm=np.deg2rad([{deg_r}]),\n"
        f"    left_arm=np.deg2rad([{deg_l}]),\n"
        ")"
    )

    output = f"{header}\n\n{block_torso}\n\n{block_right}\n\n{block_left}\n\n{block_head}\n\n{pose_snippet}\n"
    return output


def run_watch_loop(robot, model, hz: float):
    interval = 1.0 / max(hz, 1.0)
    print(f"\nStarting live joint state monitor at {hz:.1f}Hz (Press Ctrl+C to stop)...")
    time.sleep(0.5)

    try:
        while True:
            state = robot.get_state()
            output = print_full_snapshot(state, model)
            # Clear screen and print
            sys.stdout.write("\033[H\033[J" + output)
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\nMonitor stopped by user.")


def main():
    parser = argparse.ArgumentParser(description="Read and print joint angles (deg/rad) for all RBY1 robot parts.")
    parser.add_argument("--address", type=str, default=DEFAULT_ROBOT_ADDRESS, help=f"Robot IP address (default: '{DEFAULT_ROBOT_ADDRESS}')")
    parser.add_argument("--model", type=str, default=DEFAULT_ROBOT_MODEL, help=f"Robot model name (default: '{DEFAULT_ROBOT_MODEL}')")
    parser.add_argument("--watch", "-w", action="store_true", help="Continuous live monitoring mode")
    parser.add_argument("--hz", type=float, default=10.0, help="Refresh rate (Hz) for watch mode (default: 10.0)")

    args = parser.parse_args()

    print(f"Connecting to robot at {args.address} (Model: {args.model})...")
    robot = rby.create_robot(args.address, args.model)

    try:
        if not robot.connect():
            print(f"Error: Failed to connect to robot at {args.address}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error connecting to robot: {e}", file=sys.stderr)
        sys.exit(1)

    print("Connected successfully.\n")

    try:
        model = robot.model()

        if args.watch:
            run_watch_loop(robot, model, args.hz)
        else:
            state = robot.get_state()
            output = print_full_snapshot(state, model)
            print(output)

    except Exception as e:
        print(f"Error reading robot state: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
