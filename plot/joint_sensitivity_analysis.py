import numpy as np
from scipy.spatial.transform import Rotation as R

def get_rotation_matrix(axis, angle_deg):
    angle_rad = np.radians(angle_deg)
    axis = axis / np.linalg.norm(axis)
    return R.from_rotvec(axis * angle_rad).as_matrix()

def rotation_matrix_to_euler_zyx(matrix):
    r = R.from_matrix(matrix)
    euler = r.as_euler('zyx', degrees=True)
    return euler[::-1] # [Roll, Pitch, Yaw]

def analyze_sensitivity(joint_axes, baseline_rpy):
    print(f"Baseline RPY (Observed): {baseline_rpy}")
    print("-" * 40)
    print(f"{'Joint':<15} | {'d_Roll':>7} | {'d_Pitch':>7} | {'d_Yaw':>7}")
    print("-" * 40)
    
    sensitivities = []
    
    for name, axis in joint_axes.items():
        # 1도 정도의 미세 오차가 발생했을 때의 회전 행렬 변화 계산
        dR = get_rotation_matrix(axis, 1.0)
        # 현재는 장착 상태(Pose)가 identity라 가정하지만, 
        # 실제로는 dR이 포즈에 따라 다른축으로 투영됨
        
        # 여기서는 단순히 전역 축(Global Axis) 기준으로 해당 관절이 틀어졌을 때의 RPY 변화량을 확인
        d_rpy = rotation_matrix_to_euler_zyx(dR)
        
        print(f"{name:<15} | {d_rpy[0]:>7.3f} | {d_rpy[1]:>7.3f} | {d_rpy[2]:>7.3f}")
        sensitivities.append((name, d_rpy))
        
    return sensitivities

def main():
    # URDF에서 추출한 right_arm 관절축 (단순화된 Forward pose 가정)
    # 실제로는 현재 포제에 따라 이 축들이 회전되어야 함.
    # 여기서는 '팔을 앞으로 뻗은 상태'에서 대략적인 방향을 가정
    
    # 0: Shoulder Tilt, 1: Shoulder Pitch, 2: Shoulder Roll, 
    # 3: Elbow Pitch, 4: Wrist Roll, 5: Wrist Pitch, 6: Wrist Roll
    
    # URDF Axis definitions (Local to joint):
    # j0: [0, 0.94, -0.34]
    # j1: [1, 0, 0] (X)
    # j2: [0, 0, 1] (Z)
    # j3: [0, 1, 0] (Y)
    # j4: [0, 0, 1] (Z)
    # j5: [0, 1, 0] (Y)
    # j6: [0, 0, 1] (Z)
    
    joint_axes = {
        "right_arm_0": np.array([0.0, 0.94, -0.34]),
        "right_arm_1": np.array([1.0, 0.0, 0.0]),
        "right_arm_2": np.array([0.0, 0.0, 1.0]),
        "right_arm_3": np.array([0.0, 1.0, 0.0]),
        "right_arm_4": np.array([0.0, 0.0, 1.0]),
        "right_arm_5": np.array([0.0, 1.0, 0.0]),
        "right_arm_6": np.array([0.0, 0.0, 1.0]),
    }
    
    # 관측된 데이터 (예: cam_xyz_1_1.xlsx | 1.21, -0.33, 1.00)
    observed_rpy = [1.21, -0.33, 1.00]
    
    analyze_sensitivity(joint_axes, observed_rpy)

if __name__ == "__main__":
    main()
