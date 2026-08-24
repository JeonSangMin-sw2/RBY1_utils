import sys
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import yaml

# Ensure the user site-packages are first in the list
user_site = os.path.expanduser('~/.local/lib/python3.10/site-packages')
sys.path.insert(0, user_site)

import mpl_toolkits
if hasattr(mpl_toolkits, '__path__'):
    mpl_toolkits.__path__.insert(0, os.path.join(user_site, 'mpl_toolkits'))

# 분석 파라미터 설정
USE_GROUP_AVG = True
EXPECTED_DIST = 100.0  # 목표 이동 거리 (mm)
TOLERANCE = 15.0       # 거리 허용 오차 (mm)

# 전역 데이터 저장소
DATA_STORAGE = {}

def read_file(file_path):
    """
    yaml, xlsx, ods 파일을 읽어서 데이터를 DATA_STORAGE에 저장하고 컬럼명 리스트를 반환함.
    """
    ext = os.path.splitext(file_path)[1].lower()
    file_name = os.path.basename(file_path)
    
    if ext == '.yaml' or ext == '.yml':
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
        df = pd.DataFrame(data)
    elif ext == '.xlsx':
        df = pd.read_excel(file_path)
    elif ext == '.ods':
        df = pd.read_excel(file_path, engine='odf')
    else:
        raise ValueError(f"지원하지 않는 확장자입니다: {ext}")
    
    DATA_STORAGE[file_name] = df
    return df.columns.tolist()

def get_data(file_name, item_list):
    """
    저장된 파일 데이터에서 특정 항목(컬럼) 리스트의 데이터를 반환함.
    """
    if file_name not in DATA_STORAGE:
        raise KeyError(f"파일 데이터가 로드되지 않았습니다: {file_name}")
    
    df = DATA_STORAGE[file_name]
    return df[item_list]

def cal_avg(data_list):
    """
    데이터 리스트의 평균을 반환함.
    """
    return np.mean(data_list, axis=0)

def cal_sum(data_list):
    """
    데이터 리스트의 합을 반환함.
    """
    return np.sum(data_list, axis=0)

def visualize(labels, datasets, title, y_label, save_path, width=0.25, rotation=30, annotate=True):
    """
    범용 시각화 함수. 여러 데이터 시리즈를 지원하며 오프셋을 자동으로 계산하여 막대 그래프를 생성함.
    """
    clean_labels = [os.path.splitext(l)[0] for l in labels]
    fig, ax = plt.subplots(figsize=(12, 7))
    x = np.arange(len(clean_labels))
    
    num_series = len(datasets)
    # 데이터 시리즈가 여러개인 경우 x축 오프셋 계산
    offsets = np.linspace(-(num_series-1)*width/2, (num_series-1)*width/2, num_series) if num_series > 1 else [0]
    
    for i, ds in enumerate(datasets):
        series_label = ds['label']
        data = ds['data']
        color = ds.get('color')
        
        rects = ax.bar(x + offsets[i], data, width, label=series_label, color=color, alpha=0.8)
        
        if annotate:
            for rect in rects:
                height = rect.get_height()
                ax.annotate(f'{height:.2f}',
                            xy=(rect.get_x() + rect.get_width() / 2, height),
                            xytext=(0, 2 if height >= 0 else -2),  # 2 points vertical offset
                            textcoords="offset points",
                            ha='center', va='bottom' if height >= 0 else 'top',
                            fontsize=8)
    
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(clean_labels, rotation=rotation, ha='right')
    ax.legend()
    ax.grid(axis='y', linestyle='--', alpha=0.6)
    ax.margins(y=0.05) # 상하단 여백 추가하여 라벨 겹침 방지
    
    plt.tight_layout()
    if save_path:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        plt.savefig(save_path)
        print(f"Graph saved to {save_path}")
    
    plt.close(fig)

def rotation_matrix_to_euler_zyx(R):
    """
    Extract ZYX Euler angles (Yaw, Pitch, Roll) from a rotation matrix.
    R = Rz(yaw) * Ry(pitch) * Rx(roll)
    """
    pitch = np.arctan2(-R[2,0], np.sqrt(R[0,0]**2 + R[1,0]**2))
    
    if np.abs(np.cos(pitch)) > 1e-6:
        roll = np.arctan2(R[2,1], R[2,2])
        yaw = np.arctan2(R[1,0], R[0,0])
    else:
        # Gimbal lock
        yaw = 0
        roll = np.arctan2(R[0,1], R[1,1])
        
    return np.degrees(np.array([roll, pitch, yaw]))

def analyze_file(file_path):
    file_name = os.path.basename(file_path)
    columns = read_file(file_path)
    
    # 'x', 'y', 'z' 항목 추출
    df = get_data(file_name, ['x', 'y', 'z'])
    # 수치형 데이터로 변환
    df = df.apply(pd.to_numeric, errors='coerce').dropna()
    
    # 1. 이전 포인트와의 상대적 이동량을 계산 (Step-by-Step)
    prev_point = df.iloc[0].values
    valid_diffs = {'x': [], 'y': [], 'z': []}
    
    for i in range(1, len(df)):
        point = df.iloc[i].values
        diff = point - prev_point
        dist = np.linalg.norm(diff)
        
        # 2. 이동 거리가 약 100mm인 경우에만 수집
        if np.abs(dist - EXPECTED_DIST) < TOLERANCE:
            # 3. 가장 큰 변화가 있는 축을 기준으로 분류
            abs_diff = np.abs(diff)
            axis_idx = np.argmax(abs_diff)
            axis_name = ['x', 'y', 'z'][axis_idx]
            
            # 4. 방향성 일치를 위해 부호 보정 (로봇의 +축 방향 벡터로 통일)
            # 만약 -100mm 이동했다면 방향만 반전시켜 +100mm 벡터로 간주
            if diff[axis_idx] < 0:
                diff = -diff
                
            valid_diffs[axis_name].append(diff)
            # print(f"  [Match] Row {i}: {axis_name} axis, dist={dist:.1f}mm")
            
        # 다음 비교를 위해 이전 포인트 갱신
        prev_point = point

    # 결과 요약
    counts = {ax: len(valid_diffs[ax]) for ax in ['x', 'y', 'z']}
    print(f"INFO: {os.path.basename(file_path)} - 발견 포인트: {counts}")

    if any(c == 0 for c in counts.values()):
        missing = [ax for ax, c in counts.items() if c == 0]
        raise ValueError(f"{', '.join(missing)}축 데이터 부족 (각 축별 약 {EXPECTED_DIST}mm 이동이 필요함)")

    # 5. 각 축별 평균 벡터 및 MAE(Mean Absolute Error) 계산
    vectors = {ax: cal_avg(valid_diffs[ax]) for ax in ['x', 'y', 'z']}
    
    ideals = {
        'x': np.array([EXPECTED_DIST, 0, 0]),
        'y': np.array([0, EXPECTED_DIST, 0]),
        'z': np.array([0, 0, EXPECTED_DIST])
    }
    
    # 각 포인트에서의 절대 오차 (|dx|, |dy|, |dz|) 의 평균
    mae = {}
    for ax in ['x', 'y', 'z']:
        diff_array = np.array(valid_diffs[ax])
        errors = np.abs(diff_array - ideals[ax])
        # cal_avg로 전체 성분의 절대 오차 평균 계산 (1D로 평탄화하여 scalar 확보)
        mae[ax] = cal_avg(errors.flatten()) 
        
    nx = vectors['x'] / np.linalg.norm(vectors['x'])
    ny = vectors['y'] / np.linalg.norm(vectors['y'])
    nz = vectors['z'] / np.linalg.norm(vectors['z'])
    
    R = np.column_stack((nx, ny, nz))
    U, S, Vt = np.linalg.svd(R)
    R_ortho = U @ Vt
    if np.linalg.det(R_ortho) < 0:
        Vt[2,:] *= -1
        R_ortho = U @ Vt
        
    return rotation_matrix_to_euler_zyx(R_ortho), vectors, mae


def main():
    # 플래그: True이면 그룹별 평균, False이면 개별 파일별 출력
    
    files = [
        'data/cam1.xlsx',
        'data/cam2.xlsx',
        'data/cam3.xlsx',
        'data/jig.xlsx'
    ]
    
    # 그룹별 데이터 저장을 위한 딕셔너리
    groups = {
        'cam': {'rpy': [], 'raw': [], 'mae': []},
        'jig': {'rpy': [], 'raw': [], 'mae': []}
    }
    print("-" * 55)
    
    for f_path in files:
        if not os.path.exists(f_path):
            print(f"{os.path.basename(f_path):<20} | 파일 없음")
            continue
            
        group_key = 'cam' if 'cam' in f_path else 'jig'
        try:
            rpy, raw_vectors, mae = analyze_file(f_path)
            groups[group_key]['rpy'].append(rpy)
            groups[group_key]['raw'].append(raw_vectors)
            groups[group_key]['mae'].append(mae)
            print(f"{os.path.basename(f_path):<20} | 분석 완료")
        except Exception as e:
            print(f"{os.path.basename(f_path):<20} | 분석 실패: {e}")

    final_labels = []
    final_results_rpy = []
    final_results_raw = []
    final_results_mae = []

    if USE_GROUP_AVG:
        # 그룹별 평균 계산
        for key in ['cam', 'jig']:
            if not groups[key]['rpy']:
                continue
                
            final_labels.append(key)
            
            # RPY 평균 (단순 평균이지만 미세 오차이므로 큰 문제 없음)
            mean_rpy = cal_avg(groups[key]['rpy'])
            final_results_rpy.append(mean_rpy)
            
            # MAE 평균
            mean_mae = {
                'x': cal_avg([m['x'] for m in groups[key]['mae']]),
                'y': cal_avg([m['y'] for m in groups[key]['mae']]),
                'z': cal_avg([m['z'] for m in groups[key]['mae']])
            }
            final_results_mae.append(mean_mae)
            
            # Raw Vector 평균
            mean_raw = {
                'x': cal_avg([v['x'] for v in groups[key]['raw']]),
                'y': cal_avg([v['y'] for v in groups[key]['raw']]),
                'z': cal_avg([v['z'] for v in groups[key]['raw']]),
            }
            final_results_raw.append(mean_raw)
    else:
        # 개별 파일별 결과 사용
        for f_path in files:
            group_key = 'cam' if 'cam' in f_path else 'jig'
            # analyze_file이 실패했을 수도 있으므로 길이 체크가 필요할 수 있으나 
            # 여기서는 분석에 성공한 데이터만 순서대로 담겨있다고 가정하거나
            # groups 구조를 활용해서 다시 뽑음
            idx_in_group = 0
            # 위 loop에서 이미 groups에 다 들어갔으므로 이를 순서대로 labels와 함께 구성
        
        # 다시 루프를 돌며 성공한 파일들만 수집
        for f_path in files:
            group_key = 'cam' if 'cam' in f_path else 'jig'
            # 현재 groups[group_key] 에 순차적으로 저장되어 있음.
            # 하지만 어떤 파일이 실패했는지 알기 위해 groups 대신 직접 수집 로직을 main에 태우는게 깔끔할수도.
            # 일단 기존 groups에 쌓인걸 차례대로 꺼내옴 (순서 보장됨)
            pass

        # 간단히 하기 위해 groups tracking logic을 위 loop와 통합하거나 여기서 재생성
        # 위 loop에서 이미 groups에 넣어두었으니, labels를 파일명으로 해서 다시 구성
        idx_cam = 0
        idx_jig = 0
        for f_path in files:
            group_key = 'cam' if 'cam' in f_path else 'jig'
            if group_key == 'cam' and idx_cam < len(groups['cam']['rpy']):
                final_labels.append(os.path.basename(f_path))
                final_results_rpy.append(groups['cam']['rpy'][idx_cam])
                final_results_raw.append(groups['cam']['raw'][idx_cam])
                final_results_mae.append(groups['cam']['mae'][idx_cam])
                idx_cam += 1
            elif group_key == 'jig' and idx_jig < len(groups['jig']['rpy']):
                final_labels.append(os.path.basename(f_path))
                final_results_rpy.append(groups['jig']['rpy'][idx_jig])
                final_results_raw.append(groups['jig']['raw'][idx_jig])
                final_results_mae.append(groups['jig']['mae'][idx_jig])
                idx_jig += 1

    if not final_labels:
        print("분석할 수 있는 데이터가 없습니다.")
        return

    if USE_GROUP_AVG:
        print("\n[그룹별 평균 결과]")
    else:
        print("\n[개별 파일 분석 결과]")

    for i, label in enumerate(final_labels):
        rpy = final_results_rpy[i]
        print(f"{label:<20} | Roll:{rpy[0]:>8.3f} | Pitch:{rpy[1]:>8.3f} | Yaw:{rpy[2]:>8.3f}")

    # 1. Orientation Deviation (RPY)
    rpy_results = np.array(final_results_rpy)
    visualize(final_labels, [
        {'label': 'Roll (X axis)', 'data': rpy_results[:, 0], 'color': 'lightcoral'},
        {'label': 'Pitch (Y axis)', 'data': rpy_results[:, 1], 'color': 'lightgreen'},
        {'label': 'Yaw (Z axis)', 'data': rpy_results[:, 2], 'color': 'lightskyblue'}
    ], 'Calculated Orientation Deviation (100mm Moves)', 'Deviation (Degrees)', 'result/deviation_analysis_v3.png')

    # 2. Displacement Errors (dx, dy, dz) for X, Y, and Z moves
    axes_data = {
        'X': {'key': 'x', 'ideal': np.array([EXPECTED_DIST, 0, 0])},
        'Y': {'key': 'y', 'ideal': np.array([0, EXPECTED_DIST, 0])},
        'Z': {'key': 'z', 'ideal': np.array([0, 0, EXPECTED_DIST])}
    }
    for ax_name, cfg in axes_data.items():
        errs = np.array([r[cfg['key']] - cfg['ideal'] for r in final_results_raw])
        visualize(final_labels, [
            {'label': 'dx', 'data': errs[:, 0], 'color': 'indianred'},
            {'label': 'dy', 'data': errs[:, 1], 'color': 'seagreen'},
            {'label': 'dz', 'data': errs[:, 2], 'color': 'royalblue'}
        ], f'Displacement Errors during {ax_name}-axis 100mm Move', 'Error (mm)', f'result/deviation_analysis_mm_{ax_name}.png', rotation=15)

    # 3. Linear Displacement MAE
    visualize(final_labels, [
        {'label': 'During X-Move', 'data': [r['x'] for r in final_results_mae], 'color': 'indianred'},
        {'label': 'During Y-Move', 'data': [r['y'] for r in final_results_mae], 'color': 'seagreen'},
        {'label': 'During Z-Move', 'data': [r['z'] for r in final_results_mae], 'color': 'royalblue'}
    ], 'Linear Displacement MAE (Avg of |dx|,|dy|,|dz| per move)', 'Mean Absolute Error (mm)', 'result/deviation_analysis_mae.png')

    # 4. Linear Displacement Error Norm
    norms = {ax_name: [] for ax_name in ['x', 'y', 'z']}
    axes_cfg = {
        'x': {'ideal': np.array([EXPECTED_DIST, 0, 0]), 'label': 'During X-Move', 'color': 'indianred'},
        'y': {'ideal': np.array([0, EXPECTED_DIST, 0]), 'label': 'During Y-Move', 'color': 'seagreen'},
        'z': {'ideal': np.array([0, 0, EXPECTED_DIST]), 'label': 'During Z-Move', 'color': 'royalblue'}
    }
    for res in final_results_raw:
        for ax_name, cfg in axes_cfg.items():
            error_vec = res[ax_name] - cfg['ideal']
            norms[ax_name].append(np.linalg.norm(error_vec))
            
    visualize(final_labels, [
        {'label': 'During X-Move', 'data': norms['x'], 'color': 'indianred'},
        {'label': 'During Y-Move', 'data': norms['y'], 'color': 'seagreen'},
        {'label': 'During Z-Move', 'data': norms['z'], 'color': 'royalblue'}
    ], 'Linear Displacement Error Norm (||actual - ideal||)', 'Error Norm (mm)', 'result/deviation_analysis_norm.png')

if __name__ == "__main__":
    main()
