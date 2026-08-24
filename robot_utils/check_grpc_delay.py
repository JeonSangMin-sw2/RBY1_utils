import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import rby1_sdk as rby

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def initialize_robot(address, model, power=".*", servo=".*"):
    logging.info(f"Connecting to robot at {address} (model: {model})...")
    robot = rby.create_robot(address, model)
    if not robot.connect():
        logging.error(f"Failed to connect robot {address}")
        exit(1)

    if not robot.is_power_on(power):
        logging.info("Powering on...")
        if not robot.power_on(power):
            logging.error(f"Failed to turn power ({power}) on")
            exit(1)

    if not robot.is_servo_on(servo):
        logging.info("Servo on...")
        if not robot.servo_on(servo):
            logging.error(f"Failed to servo ({servo}) on")
            exit(1)

    cm_state = robot.get_control_manager_state().state
    if cm_state in [
        rby.ControlManagerState.State.MajorFault,
        rby.ControlManagerState.State.MinorFault,
    ]:
        logging.info("Resetting control manager fault...")
        if not robot.reset_fault_control_manager():
            logging.error("Failed to reset control manager")
            exit(1)

    if not robot.enable_control_manager():
        logging.error("Failed to enable control manager")
        exit(1)

    logging.info("Robot initialized successfully.")
    return robot


def create_stream_builders(robot, robot_info, num_streams=2):
    """
    gRPC 서버의 리소스 충돌(선점 및 Stream Expire)을 방지하기 위해
    스트림 개수에 따라 상호 배타적인 컴포넌트 명령을 생성합니다.
    """
    state = robot.get_state()
    current_q = np.array(state.position)

    builders = []

    if num_streams == 1:
        # [Stream 1] 전체 상체 (Torso + Right Arm + Left Arm + Head)
        body_builder = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.torso_joint_idx) > 0:
            body_builder.set_torso_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.torso_joint_idx])
            )
        if len(robot_info.right_arm_joint_idx) > 0:
            body_builder.set_right_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.right_arm_joint_idx])
            )
        if len(robot_info.left_arm_joint_idx) > 0:
            body_builder.set_left_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.left_arm_joint_idx])
            )
        comp = rby.ComponentBasedCommandBuilder().set_body_command(body_builder)
        if len(robot_info.head_joint_idx) > 0:
            comp.set_head_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.head_joint_idx])
            )
        builders.append(("Upper Body (Torso+Arms+Head)", rby.RobotCommandBuilder().set_command(comp)))

    elif num_streams == 2:
        # C++ ROS 2 드라이버와 동일: [Stream 1: 상체 전체] + [Stream 2: 모빌리티 베이스]
        body_builder = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.torso_joint_idx) > 0:
            body_builder.set_torso_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.torso_joint_idx])
            )
        if len(robot_info.right_arm_joint_idx) > 0:
            body_builder.set_right_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.right_arm_joint_idx])
            )
        if len(robot_info.left_arm_joint_idx) > 0:
            body_builder.set_left_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.left_arm_joint_idx])
            )
        comp1 = rby.ComponentBasedCommandBuilder().set_body_command(body_builder)
        if len(robot_info.head_joint_idx) > 0:
            comp1.set_head_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.head_joint_idx])
            )
        builders.append(("Upper Body", rby.RobotCommandBuilder().set_command(comp1)))

        # Mobility Stream
        se2_cmd = (
            rby.SE2VelocityCommandBuilder()
            .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
            .set_minimum_time(0.01)
            .set_velocity(np.array([0.0, 0.0]), 0.0)
        )
        comp2 = rby.ComponentBasedCommandBuilder().set_mobility_command(
            rby.MobilityCommandBuilder().set_command(se2_cmd)
        )
        builders.append(("Mobility (Base)", rby.RobotCommandBuilder().set_command(comp2)))

    elif num_streams == 3:
        # [Stream 1: Torso + Head], [Stream 2: Right Arm], [Stream 3: Left Arm]
        # 1. Torso + Head
        b1 = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.torso_joint_idx) > 0:
            b1.set_torso_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.torso_joint_idx])
            )
        comp1 = rby.ComponentBasedCommandBuilder().set_body_command(b1)
        if len(robot_info.head_joint_idx) > 0:
            comp1.set_head_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.head_joint_idx])
            )
        builders.append(("Torso + Head", rby.RobotCommandBuilder().set_command(comp1)))

        # 2. Right Arm
        b2 = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.right_arm_joint_idx) > 0:
            b2.set_right_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.right_arm_joint_idx])
            )
        comp2 = rby.ComponentBasedCommandBuilder().set_body_command(b2)
        builders.append(("Right Arm", rby.RobotCommandBuilder().set_command(comp2)))

        # 3. Left Arm
        b3 = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.left_arm_joint_idx) > 0:
            b3.set_left_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.left_arm_joint_idx])
            )
        comp3 = rby.ComponentBasedCommandBuilder().set_body_command(b3)
        builders.append(("Left Arm", rby.RobotCommandBuilder().set_command(comp3)))

    elif num_streams >= 4:
        # [Stream 1: Torso], [Stream 2: Right Arm], [Stream 3: Left Arm], [Stream 4: Mobility (or Head)]
        # 1. Torso
        b1 = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.torso_joint_idx) > 0:
            b1.set_torso_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.torso_joint_idx])
            )
        comp1 = rby.ComponentBasedCommandBuilder().set_body_command(b1)
        builders.append(("Torso", rby.RobotCommandBuilder().set_command(comp1)))

        # 2. Right Arm
        b2 = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.right_arm_joint_idx) > 0:
            b2.set_right_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.right_arm_joint_idx])
            )
        comp2 = rby.ComponentBasedCommandBuilder().set_body_command(b2)
        builders.append(("Right Arm", rby.RobotCommandBuilder().set_command(comp2)))

        # 3. Left Arm
        b3 = rby.BodyComponentBasedCommandBuilder()
        if len(robot_info.left_arm_joint_idx) > 0:
            b3.set_left_arm_command(
                rby.JointPositionCommandBuilder()
                .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
                .set_minimum_time(0.01)
                .set_position(current_q[robot_info.left_arm_joint_idx])
            )
        comp3 = rby.ComponentBasedCommandBuilder().set_body_command(b3)
        builders.append(("Left Arm", rby.RobotCommandBuilder().set_command(comp3)))

        # 4. Mobility Base
        se2_cmd = (
            rby.SE2VelocityCommandBuilder()
            .set_command_header(rby.CommandHeaderBuilder().set_control_hold_time(1.0))
            .set_minimum_time(0.01)
            .set_velocity(np.array([0.0, 0.0]), 0.0)
        )
        comp4 = rby.ComponentBasedCommandBuilder().set_mobility_command(
            rby.MobilityCommandBuilder().set_command(se2_cmd)
        )
        builders.append(("Mobility (Base)", rby.RobotCommandBuilder().set_command(comp4)))

    return builders


def test_multi_stream_delay(robot, num_streams=2, target_hz=100.0, duration_sec=5.0, parallel=False):
    robot_info = robot.get_robot_info()
    stream_builders = create_stream_builders(robot, robot_info, num_streams=num_streams)
    actual_num_streams = len(stream_builders)

    print(f"\n==================================================================")
    send_mode_str = "Parallel (Concurrent Threads)" if parallel else "Sequential (Synchronous in Loop)"
    print(f" [Multi-Stream Test] Streams: {actual_num_streams}개 | Rate: {target_hz}Hz | Mode: {send_mode_str}")
    print(f" Assigned Components:")
    for idx, (name, _) in enumerate(stream_builders):
        print(f"   Stream #{idx+1}: {name}")
    print(f"==================================================================")

    # N개의 독립 스트림 핸들러 생성
    streams = []
    for idx in range(actual_num_streams):
        stream = robot.create_command_stream(priority=1)
        streams.append(stream)

    target_period = 1.0 / target_hz  # 10ms
    total_iterations = int(target_hz * duration_sec)

    per_stream_latencies = [[] for _ in range(actual_num_streams)]
    total_cycle_latencies = []
    loop_periods = []
    overrun_count = 0

    def send_to_single_stream(s_idx, stream_handler, cmd_builder):
        t0 = time.perf_counter()
        feedback = stream_handler.send_command(cmd_builder, timeout_ms=100)
        t1 = time.perf_counter()
        return s_idx, (t1 - t0) * 1000.0

    executor = ThreadPoolExecutor(max_workers=actual_num_streams) if parallel else None

    print(f"\nBenchmarking {total_iterations} cycles (~{duration_sec:.1f}s)...")
    last_loop_time = time.perf_counter()

    for i in range(total_iterations):
        loop_start = time.perf_counter()

        if i > 0:
            loop_periods.append((loop_start - last_loop_time) * 1000.0)
        last_loop_time = loop_start

        # 최신 관절 상태를 반영한 커맨드 생성
        stream_builders = create_stream_builders(robot, robot_info, num_streams=num_streams)

        cycle_send_start = time.perf_counter()

        try:
            if parallel:
                futures = [
                    executor.submit(send_to_single_stream, s_idx, streams[s_idx], stream_builders[s_idx][1])
                    for s_idx in range(actual_num_streams)
                ]
                for future in futures:
                    s_idx, lat = future.result()
                    per_stream_latencies[s_idx].append(lat)
            else:
                for s_idx in range(actual_num_streams):
                    t0 = time.perf_counter()
                    feedback = streams[s_idx].send_command(stream_builders[s_idx][1], timeout_ms=100)
                    t1 = time.perf_counter()
                    per_stream_latencies[s_idx].append((t1 - t0) * 1000.0)

            cycle_send_end = time.perf_counter()
            total_cycle_latencies.append((cycle_send_end - cycle_send_start) * 1000.0)

        except Exception as e:
            print(f"Error at cycle {i}: {e}")
            break

        # 10ms 주기 맞추기
        elapsed = time.perf_counter() - loop_start
        sleep_time = target_period - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            overrun_count += 1

    if executor:
        executor.shutdown(wait=False)

    for stream in streams:
        try:
            stream.cancel()
        except Exception:
            pass

    if len(total_cycle_latencies) > 0:
        total_cycle_latencies = np.array(total_cycle_latencies)
        loop_periods = np.array(loop_periods) if len(loop_periods) > 0 else np.array([0.0])

        print(f"\n====================== BENCHMARK RESULTS ======================")
        print(f"Completed Cycles: {len(total_cycle_latencies)} / {total_iterations}")
        print(f"Overruns (> {target_period*1000:.1f}ms limit): {overrun_count} times ({overrun_count/len(total_cycle_latencies)*100:.1f}%)")
        
        print(f"\n[1] Individual Stream RTT Latencies:")
        for s_idx in range(actual_num_streams):
            s_name = stream_builders[s_idx][0]
            s_lat = np.array(per_stream_latencies[s_idx])
            print(f"  - Stream #{s_idx + 1} ({s_name}):")
            print(f"      Avg: {np.mean(s_lat):.3f} ms | Min: {np.min(s_lat):.3f} ms | Max: {np.max(s_lat):.3f} ms | Std: {np.std(s_lat):.3f} ms")

        print(f"\n[2] Total Cycle Send Time (Time to finish all {actual_num_streams} streams):")
        print(f"  Avg:  {np.mean(total_cycle_latencies):.3f} ms")
        print(f"  Min:  {np.min(total_cycle_latencies):.3f} ms")
        print(f"  Max:  {np.max(total_cycle_latencies):.3f} ms")
        print(f"  Std:  {np.std(total_cycle_latencies):.3f} ms")
        print(f"  95%:  {np.percentile(total_cycle_latencies, 95):.3f} ms")
        print(f"  99%:  {np.percentile(total_cycle_latencies, 99):.3f} ms")

        if len(loop_periods) > 1:
            print(f"\n[3] 100Hz Loop Period (Target: {target_period*1000:.1f} ms):")
            print(f"  Avg:  {np.mean(loop_periods):.3f} ms")
            print(f"  Std (Jitter): {np.std(loop_periods):.3f} ms")
        print(f"===============================================================\n")
    else:
        print("No valid samples recorded.")


def main():
    parser = argparse.ArgumentParser(description="RBY1 gRPC Multi-Stream 100Hz Latency Benchmark")
    parser.add_argument("--address", type=str, default="127.0.0.1:50051", help="Robot address (default: 127.0.0.1:50051)")
    parser.add_argument("--model", type=str, default="m", help="Robot model ('a' or 'm')")
    parser.add_argument("--power", type=str, default=".*", help="Power device name regex pattern")
    parser.add_argument("--servo", type=str, default=".*", help="Servo name regex pattern")
    parser.add_argument("--streams", type=int, default=2, choices=[1, 2, 3, 4], help="Number of streams to open (1~4, default: 2)")
    parser.add_argument("--hz", type=float, default=100.0, help="Stream target frequency in Hz (default: 100)")
    parser.add_argument("--duration", type=float, default=5.0, help="Test duration in seconds (default: 5.0)")
    parser.add_argument("--parallel", action="store_true", help="Send commands to streams in parallel via threads")
    args = parser.parse_args()

    robot = initialize_robot(args.address, args.model, args.power, args.servo)
    test_multi_stream_delay(robot, num_streams=args.streams, target_hz=args.hz, duration_sec=args.duration, parallel=args.parallel)


if __name__ == "__main__":
    main()
