import sys
import os
user_site = os.path.expanduser('~/.local/lib/python3.10/site-packages')
sys.path.insert(0, user_site)

import mpl_toolkits
if hasattr(mpl_toolkits, '__path__'):
    mpl_toolkits.__path__.insert(0, os.path.join(user_site, 'mpl_toolkits'))

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

def visualize_3d_excel(file_paths, x_col, y_col, z_col):
    # 1. 3차원 그래프 생성
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection='3d')

    # 색상 팔레트 설정 (파일마다 다른 색상 적용)
    colors = plt.colormaps['tab10'].colors

    # 파일이 하나만 들어온 경우 리스트로 변환
    if isinstance(file_paths, str):
        file_paths = [file_paths]

    # 2. 각 파일에 대해 데이터 추출 및 시각화
    for i, file_path in enumerate(file_paths):
        try:
            df = pd.read_excel(file_path)
            # NaN(결측치)이 있으면 그래프가 끊길 수 있으므로 제거합니다.
            df_cleaned = df[[x_col, y_col, z_col]].dropna()
            
            x = df_cleaned[x_col]
            y = df_cleaned[y_col]
            z = df_cleaned[z_col]

            # 파일명에서 라벨 추출
            label = os.path.basename(file_path)
            color = colors[i % len(colors)]

            # 4. 산점도(Scatter plot) 그리기
            ax.scatter(x, y, z, color=color, label=label, marker='o', alpha=0.6)
            
        except Exception as e:
            print(f"파일 {file_path}을 읽는 중 오류가 발생했습니다: {e}")

    # 축 레이블 설정
    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    ax.set_zlabel(z_col)
    ax.set_title(f'3D Visualization of Multiple Files')

    # 범례 추가
    ax.legend()
    
    # Save as image
    os.makedirs('result', exist_ok=True)
    plt.savefig('result/3d_position_v4.png')
    print("3D plot saved to result/3d_position_v4.png")

# 사용 예시:
# visualize_3d_excel(['data1.xlsx', 'data2.xlsx'], 'x', 'y', 'z')


def main():
    file_list = [
        'data/cam_xyz_1_1.xlsx',
        'data/cam_xyz_2_1.xlsx',
        'data/cam_xyz_3_1.xlsx',
        'data/cam_xyz_4_1.xlsx',
        'data/cam_xyz_5_1.xlsx',
        'data/jig_xyz.xlsx'
    ]
    visualize_3d_excel(file_list, 'x', 'y', 'z')

if __name__ == "__main__":
    main()