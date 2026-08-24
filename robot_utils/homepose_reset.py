import rby1_sdk as rby
import argparse
import time

def main(address, model_name):
    print(f"\n[INFO] Connecting to robot at {address} (model: {model_name})...")
    try:
        robot = rby.create_robot(address, model_name)
        if not robot.connect():
            print(f"[ERROR] Failed to connect to robot at {address}")
            return
        print("[INFO] Connected successfully.")
    except Exception as e:
        print(f"[ERROR] Exception during robot connection: {e}")
        return

    try:
        model = robot.model()
        right_dof = len(model.right_arm_idx)
        left_dof = len(model.left_arm_idx)
        
        # 1. Disable control manager
        print("\n--- STEP 1: Disabling Control Manager ---")
        try:
            robot.disable_control_manager()
            time.sleep(1.0)
            print("[SUCCESS] Control manager disabled.")
        except Exception as e:
            print(f"[ERROR] Failed to disable control manager: {e}")
            return

        # 2. Reset torso and arm joint offsets
        print("\n--- STEP 2: Resetting Torso and Arm Joint Offsets ---")
        all_success = True
        
        # Torso joints (0 to 5)
        for i in range(6):
            joint_name = f"torso_{i}"
            try:
                success = robot.home_offset_reset(joint_name)
                if not success:
                    print(f"[ERROR] Failed to reset joint offset for {joint_name}")
                    all_success = False
                else:
                    print(f"[OK] {joint_name} offset reset")
            except Exception as e:
                print(f"[ERROR] Exception resetting joint {joint_name}: {e}")
                all_success = False

        # Right arm joints
        for i in range(right_dof):
            joint_name = f"right_arm_{i}"
            try:
                success = robot.home_offset_reset(joint_name)
                if not success:
                    print(f"[ERROR] Failed to reset joint offset for {joint_name}")
                    all_success = False
                else:
                    print(f"[OK] {joint_name} offset reset")
            except Exception as e:
                print(f"[ERROR] Exception resetting joint {joint_name}: {e}")
                all_success = False

        # Left arm joints
        for i in range(left_dof):
            joint_name = f"left_arm_{i}"
            try:
                success = robot.home_offset_reset(joint_name)
                if not success:
                    print(f"[ERROR] Failed to reset joint offset for {joint_name}")
                    all_success = False
                else:
                    print(f"[OK] {joint_name} offset reset")
            except Exception as e:
                print(f"[ERROR] Exception resetting joint {joint_name}: {e}")
                all_success = False

        if not all_success:
            print("[WARNING] Some joints failed to reset. Proceeding with power cycle anyway...")
        else:
            print("[SUCCESS] All torso and arm joint offsets reset successfully.")

        # 3. Power Off (48V)
        print("\n--- STEP 3: Powering Off (48V) ---")
        try:
            success = robot.power_off("48v")
            time.sleep(2.0)
            if not success:
                print("[WARNING] power_off returned False, but proceeding...")
            else:
                print("[SUCCESS] Power off complete.")
        except Exception as e:
            print(f"[ERROR] Failed to power off: {e}")
            return

        # 4. Power On
        print("\n--- STEP 4: Powering On (.*) ---")
        try:
            success = robot.power_on(".*")
            time.sleep(2.0)
            if not success:
                print("[ERROR] power_on returned False")
                return
            print("[SUCCESS] Power on complete.")
        except Exception as e:
            print(f"[ERROR] Failed to power on: {e}")
            return

        # 5. Servo On
        print("\n--- STEP 5: Turning Servos On (.*) ---")
        try:
            success = robot.servo_on(".*")
            time.sleep(2.0)
            if not success:
                print("[ERROR] servo_on returned False")
                return
            print("[SUCCESS] Servos on complete.")
        except Exception as e:
            print(f"[ERROR] Failed to turn servos on: {e}")
            return

        # 6. Enable Control Manager
        print("\n--- STEP 6: Enabling Control Manager ---")
        try:
            # Reset fault if any
            cm_state = robot.get_control_manager_state().state
            if cm_state in [rby.ControlManagerState.State.MajorFault, rby.ControlManagerState.State.MinorFault]:
                print("[INFO] Control manager is in fault state. Resetting fault...")
                robot.reset_fault_control_manager()
                time.sleep(1.0)
            
            success = robot.enable_control_manager(unlimited_mode_enabled=True)
            if not success:
                print("[ERROR] enable_control_manager returned False")
                return
            print("[SUCCESS] Control manager enabled in unlimited mode.")
        except Exception as e:
            print(f"[ERROR] Failed to enable control manager: {e}")
            return

        print("\n==================================================")
        print("   HOMEPOSE RESET SEQUENCE COMPLETED SUCCESSFULLY")
        print("==================================================")

    except Exception as e:
        print(f"[ERROR] Unexpected exception: {e}")
    finally:
        try:
            robot.disconnect()
            print("[INFO] Disconnected from robot.")
        except Exception:
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Torso and Arm Homepose Offset Reset Sequence")
    parser.add_argument("--address", type=str, required=True, help="Robot IP address (e.g. 127.0.0.1)")
    parser.add_argument("--model", type=str, required=True, help="Robot model name (e.g. m, a)")
    args = parser.parse_args()

    main(
        address=args.address,
        model_name=args.model,
    )
