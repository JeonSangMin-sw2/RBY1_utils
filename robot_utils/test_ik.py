import rby1_sdk as rby
import numpy as np

def se3_log(T):
    R = T[:3, :3]
    p = T[:3, 3]
    
    tr = np.trace(R)
    cos_theta = (tr - 1.0) / 2.0
    cos_theta = np.clip(cos_theta, -1.0, 1.0)
    theta = np.arccos(cos_theta)
    
    if np.abs(theta) < 1e-6:
        omega = np.zeros(3)
        v = p
    elif np.abs(theta - np.pi) < 1e-6:
        R_plus_I = R + np.eye(3)
        col_idx = np.argmax(np.linalg.norm(R_plus_I, axis=0))
        axis = R_plus_I[:, col_idx]
        axis = axis / np.linalg.norm(axis)
        omega = theta * axis
        
        skew_omega = np.array([
            [0.0, -omega[2], omega[1]],
            [omega[2], 0.0, -omega[0]],
            [-omega[1], omega[0], 0.0]
        ])
        
        v = p - 0.5 * skew_omega.dot(p) + (1.0 / (theta * theta)) * skew_omega.dot(skew_omega.dot(p))
    else:
        skew_omega_hat = (R - R.T) / (2.0 * np.sin(theta))
        omega_hat = np.array([skew_omega_hat[2, 1], skew_omega_hat[0, 2], skew_omega_hat[1, 0]])
        omega = theta * omega_hat
        
        skew_omega = np.array([
            [0.0, -omega[2], omega[1]],
            [omega[2], 0.0, -omega[0]],
            [-omega[1], omega[0], 0.0]
        ])
        
        cot_half_theta = 1.0 / np.tan(theta / 2.0)
        coeff = (1.0 - 0.5 * theta * cot_half_theta) / (theta * theta)
        v = p - 0.5 * skew_omega.dot(p) + coeff * skew_omega.dot(skew_omega.dot(p))
        
    return np.hstack((omega, v))

def debug_ik():
    robot = rby.create_robot("127.0.0.1:50051", "a")
    if not robot.connect():
        print("Failed to connect")
        return
        
    robot_model = robot.model()
    dyn_robot = robot.get_dynamics()
    
    q_curr = robot.get_state().position.copy()
    
    ref_link = "base"
    target_link = "ee_right"
    all_joint_names = robot_model.robot_joint_names
    
    dyn_state = dyn_robot.make_state([ref_link, target_link], all_joint_names)
    dyn_state.set_q(q_curr)
    
    dyn_robot.compute_forward_kinematics(dyn_state)
    T_curr = dyn_robot.compute_transformation(dyn_state, 0, 1)
    
    T_target = T_curr.copy()
    T_target[2, 3] += 0.05  # Shift 5cm in Z
    
    print("Initial target translation:", T_target[:3, 3])
    
    active_indices = robot_model.right_arm_idx
    damping = 1e-3
    alpha = 0.5
    
    for i in range(50):
        dyn_robot.compute_forward_kinematics(dyn_state)
        T_c = dyn_robot.compute_transformation(dyn_state, 0, 1)
        
        T_err = np.linalg.inv(T_c) @ T_target
        V_b = se3_log(T_err)
        
        err_rot = np.linalg.norm(V_b[:3])
        err_pos = np.linalg.norm(T_err[:3, 3])
        
        if err_pos < 1e-4 and err_rot < 1e-3:
            print(f"Converged at iter {i}: Pos Err = {err_pos:.6f}, Rot Err = {err_rot:.6f}")
            break
            
        if i % 5 == 0 or i < 5:
            print(f"Iter {i}: Pos Err = {err_pos:.6f}, Rot Err = {err_rot:.6f}")
        
        J = dyn_robot.compute_body_jacobian(dyn_state, 0, 1)
        J_active = J[:, active_indices]
        
        A = J_active.T @ J_active + damping * np.eye(len(active_indices))
        b = J_active.T @ V_b
        delta_q = np.linalg.solve(A, b)
        
        q_curr[active_indices] += alpha * delta_q
        
        q_lower = dyn_robot.get_limit_q_lower(dyn_state)
        q_upper = dyn_robot.get_limit_q_upper(dyn_state)
        q_curr = np.clip(q_curr, q_lower, q_upper)
        
        dyn_state.set_q(q_curr)
    else:
        print(f"Did not converge. Final Pos Err = {err_pos:.6f}, Rot Err = {err_rot:.6f}")

if __name__ == "__main__":
    debug_ik()
