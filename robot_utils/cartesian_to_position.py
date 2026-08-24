"""
Cartesian to Joint Position (IK Solver) Utility for rby1-sdk

This module provides a numerical inverse kinematics (IK) solver utilizing the robot dynamics
and forward kinematics features of the rby1-sdk. It allows computing the joint configurations
for a specified target Cartesian pose (4x4 homogeneous transformation matrix).

Usage:
    Import solve_ik or run this script directly to test.
    python3 cartesian_to_position.py --address 127.0.0.1:50051 --model a
"""

import rby1_sdk as rby
import numpy as np
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def se3_log(T):
    """
    Computes the matrix logarithm of an SE(3) homogeneous transformation matrix.
    
    Parameters
    ----------
    T : numpy.ndarray, shape (4, 4)
        Homogeneous transformation matrix in SE(3).
        
    Returns
    -------
    numpy.ndarray, shape (6,)
        6D twist vector [omega, v] where:
        - omega: 3D angular velocity vector (exponential coordinates of rotation)
        - v: 3D linear velocity vector (represented in the body twist format, v = theta * G_inv * p)
    """
    R = T[:3, :3]
    p = T[:3, 3]
    
    # Calculate trace of rotation matrix R
    tr = np.trace(R)
    cos_theta = (tr - 1.0) / 2.0
    
    # Clamp to prevent numerical out-of-bounds due to float precision
    cos_theta = np.clip(cos_theta, -1.0, 1.0)
    theta = np.arccos(cos_theta)
    
    if np.abs(theta) < 1e-6:
        # Rotation is close to zero
        omega = np.zeros(3)
        v = p
    elif np.abs(theta - np.pi) < 1e-6:
        # Rotation is close to pi radians (180 degrees)
        # Find column of R + I with the largest norm to determine rotation axis
        R_plus_I = R + np.eye(3)
        col_idx = np.argmax(np.linalg.norm(R_plus_I, axis=0))
        axis = R_plus_I[:, col_idx]
        axis = axis / np.linalg.norm(axis)
        
        omega = theta * axis
        
        # Calculate skew-symmetric matrix of the rotation axis
        skew_omega = np.array([
            [0.0, -omega[2], omega[1]],
            [omega[2], 0.0, -omega[0]],
            [-omega[1], omega[0], 0.0]
        ])
        
        # v_twist = p - 0.5 * skew_omega @ p + (1.0 / theta**2) * (skew_omega @ skew_omega) @ p
        v = p - 0.5 * skew_omega.dot(p) + (1.0 / (theta * theta)) * skew_omega.dot(skew_omega.dot(p))
    else:
        # General case
        skew_omega_hat = (R - R.T) / (2.0 * np.sin(theta))
        omega_hat = np.array([skew_omega_hat[2, 1], skew_omega_hat[0, 2], skew_omega_hat[1, 0]])
        omega = theta * omega_hat
        
        # Calculate skew-symmetric matrix of the rotation vector
        skew_omega = np.array([
            [0.0, -omega[2], omega[1]],
            [omega[2], 0.0, -omega[0]],
            [-omega[1], omega[0], 0.0]
        ])
        
        cot_half_theta = 1.0 / np.tan(theta / 2.0)
        coeff = (1.0 - 0.5 * theta * cot_half_theta) / (theta * theta)
        v = p - 0.5 * skew_omega.dot(p) + coeff * skew_omega.dot(skew_omega.dot(p))
        
    return np.hstack((omega, v))

def solve_ik(
    robot,
    target_T,
    target_link="ee_right",
    ref_link="base",
    joint_names=None,
    q_init=None,
    max_iter=200,
    tol_pos=1e-4,
    tol_rot=1e-3,
    damping=1e-3,
    alpha=0.5
):
    """
    Computes numerical inverse kinematics (IK) utilizing robot dynamics.
    
    Parameters
    ----------
    robot : rby1_sdk.Robot or rby1_sdk.dynamics.Robot
        The robot object or robot dynamics object.
    target_T : numpy.ndarray, shape (4, 4)
        The desired 4x4 homogeneous transformation matrix of the target link relative to reference link.
    target_link : str, default: "ee_right"
        The target link name (end effector link).
    ref_link : str, default: "base"
        The reference link name.
    joint_names : list[str] or str, default: None
        The active joints to solve for.
        - If None, defaults to all joints if no robot model is present,
          or automatically resolves to right_arm / left_arm joints depending on target_link.
        - If "right_arm", solves for all right arm joints.
        - If "left_arm", solves for all left arm joints.
        - If a list of strings, uses the specific joints provided.
    q_init : numpy.ndarray, default: None
        Initial joint position configuration. If None, queries from robot state or defaults to zeros.
    max_iter : int, default: 200
        Maximum number of iterations.
    tol_pos : float, default: 1e-4
        Translation error tolerance [meters].
    tol_rot : float, default: 1e-3
        Rotation error tolerance [radians].
    damping : float, default: 1e-3
        Damping factor for Levenberg-Marquardt numerical updates to avoid singularities.
    alpha : float, default: 0.5
        Update step size (learning rate / scaling factor).
        
    Returns
    -------
    dict
        A dictionary containing:
        - "q": numpy.ndarray of the final joint values (full robot DOF).
        - "success": bool, whether the IK solver converged within tolerances.
        - "err_pos": float, final translation error.
        - "err_rot": float, final rotation error.
        - "iterations": int, number of iterations taken.
    """
    # 1. Retrieve dynamics robot and model specifications
    if hasattr(robot, "get_dynamics"):
        dyn_robot = robot.get_dynamics()
        robot_model = robot.model()
        all_joint_names = robot_model.robot_joint_names
    else:
        # Assuming the argument is already a dynamics robot object
        dyn_robot = robot
        all_joint_names = dyn_robot.get_joint_names()
        robot_model = None

    # 2. Determine active joints to solve for
    if joint_names is None:
        if robot_model is not None:
            if target_link == "ee_right":
                active_indices = robot_model.right_arm_idx
            elif target_link == "ee_left":
                active_indices = robot_model.left_arm_idx
            else:
                active_indices = list(range(len(all_joint_names)))
        else:
            active_indices = list(range(len(all_joint_names)))
    elif isinstance(joint_names, str):
        if robot_model is None:
            raise ValueError("String joint group names require passing the main robot object.")
        if joint_names == "right_arm":
            active_indices = robot_model.right_arm_idx
        elif joint_names == "left_arm":
            active_indices = robot_model.left_arm_idx
        elif joint_names == "torso":
            active_indices = robot_model.torso_idx
        else:
            raise ValueError(f"Unknown joint group name: {joint_names}")
    else:
        active_indices = [all_joint_names.index(name) for name in joint_names]

    # 3. Determine initial joint position configuration
    if q_init is None:
        if hasattr(robot, "get_state"):
            q_curr = robot.get_state().position.copy()
        else:
            q_curr = np.zeros(len(all_joint_names))
    else:
        q_curr = np.array(q_init, dtype=float).copy()

    # 4. Create dynamics state object
    # We specify [ref_link, target_link] so their indices will be 0 and 1 respectively in the state.
    dyn_state = dyn_robot.make_state([ref_link, target_link], all_joint_names)
    dyn_state.set_q(q_curr)

    success = False
    err_pos = float("inf")
    err_rot = float("inf")
    iteration = 0

    # 5. Iterative numerical inverse kinematics loop
    for iteration in range(max_iter):
        # Update forward kinematics
        dyn_robot.compute_forward_kinematics(dyn_state)
        
        # Compute current Cartesian pose from reference link (0) to target link (1)
        T_curr = dyn_robot.compute_transformation(dyn_state, 0, 1)
        
        # Calculate error transformation matrix: T_err = T_curr^-1 * target_T
        T_err = np.linalg.inv(T_curr) @ target_T
        
        # Calculate spatial body twist error
        V_b = se3_log(T_err)
        
        # Compute rotation and translation errors
        err_rot = np.linalg.norm(V_b[:3])
        err_pos = np.linalg.norm(T_err[:3, 3])
        
        if err_pos < tol_pos and err_rot < tol_rot:
            success = True
            break
            
        # Compute the full body Jacobian (6 x robot_dof)
        J = dyn_robot.compute_body_jacobian(dyn_state, 0, 1)
        
        # Extract the columns corresponding to our active joints
        J_active = J[:, active_indices]
        
        # Solve for joint update step using Levenberg-Marquardt method:
        # delta_q = (J^T * J + damping * I)^-1 * J^T * V_b
        A = J_active.T @ J_active + damping * np.eye(len(active_indices))
        b = J_active.T @ V_b
        delta_q = np.linalg.solve(A, b)
        
        # Apply scaling and update active joints
        q_curr[active_indices] += alpha * delta_q
        
        # Clamp to joint position limits (Projected Gradient Descent)
        q_lower = dyn_robot.get_limit_q_lower(dyn_state)
        q_upper = dyn_robot.get_limit_q_upper(dyn_state)
        q_curr = np.clip(q_curr, q_lower, q_upper)
        
        # Update the dynamics state configuration
        dyn_state.set_q(q_curr)

    return {
        "q": q_curr,
        "success": success,
        "err_pos": err_pos,
        "err_rot": err_rot,
        "iterations": iteration + 1
    }

def main():
    parser = argparse.ArgumentParser(description="rby1-sdk Cartesian to Joint Position (IK Solver) Test")
    parser.add_argument("--address", type=str, required=True, help="Robot gRPC IP address (e.g. 192.168.30.1:50051)")
    parser.add_argument("--model", type=str, default="a", help="Robot Model Name (default: 'a')")
    parser.add_argument("--target-link", type=str, default="ee_right", help="Target link name (default: ee_right)")
    parser.add_argument("--ref-link", type=str, default="base", help="Reference link name (default: base)")
    args = parser.parse_args()

    logging.info(f"Connecting to robot at {args.address}...")
    robot = rby.create_robot(args.address, args.model)
    if not robot.connect():
        logging.error("Failed to connect to the robot.")
        return
        
    logging.info("Robot connected successfully. Getting dynamics and model configurations...")
    robot_model = robot.model()
    dyn_robot = robot.get_dynamics()
    
    # 1. Get current robot joint configuration
    q_curr = robot.get_state().position
    logging.info(f"Current joint configuration (q) queried successfully. Size: {len(q_curr)}")

    # 2. Compute current Cartesian position of target link via FK
    dyn_state = dyn_robot.make_state([args.ref_link, args.target_link], robot_model.robot_joint_names)
    dyn_state.set_q(q_curr)
    dyn_robot.compute_forward_kinematics(dyn_state)
    T_curr = dyn_robot.compute_transformation(dyn_state, 0, 1)
    
    logging.info("Current Cartesian Transform (FK result):")
    print(np.round(T_curr, 4))
    
    # 3. Create a target Cartesian pose by shifting the current pose by +10cm in Z-axis
    T_target = T_curr.copy()
    T_target[2, 3] += 0.10  # Shift 10cm up
    
    logging.info(f"Target Cartesian Transform (Shifted +10cm along Z-axis):")
    print(np.round(T_target, 4))
    
    # 4. Compute Joint IK using our solver
    logging.info(f"Solving Inverse Kinematics for {args.target_link}...")
    result = solve_ik(
        robot=robot,
        target_T=T_target,
        target_link=args.target_link,
        ref_link=args.ref_link,
        joint_names="right_arm" if args.target_link == "ee_right" else "left_arm",
        q_init=q_curr,
        max_iter=300,
        tol_pos=1e-4,
        tol_rot=1e-3
    )
    
    logging.info(f"IK solver finished in {result['iterations']} iterations.")
    logging.info(f"Convergence Success: {result['success']}")
    logging.info(f"Final Translation Error: {result['err_pos']:.6f} m")
    logging.info(f"Final Rotation Error: {result['err_rot']:.6f} rad")
    
    if result["success"]:
        logging.info("IK calculation succeeded! Verifying solved joint angles via Forward Kinematics...")
        # Verify result via FK
        dyn_state.set_q(result["q"])
        dyn_robot.compute_forward_kinematics(dyn_state)
        T_verify = dyn_robot.compute_transformation(dyn_state, 0, 1)
        
        logging.info("Verified Cartesian Transform (FK of solved q):")
        print(np.round(T_verify, 4))
        
        diff = np.linalg.norm(T_verify - T_target)
        logging.info(f"Matrix norm difference between target and verified transform: {diff:.6f}")
    else:
        logging.error("IK calculation did not converge.")

if __name__ == "__main__":
    main()
