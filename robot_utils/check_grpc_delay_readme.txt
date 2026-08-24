cd /home/rainbow/utils_ws/robot_utils
# 1. 스트림 2개 (상체 + 모빌리티) 100Hz로 5초간 측정
python3 check_grpc_delay.py --address 127.0.0.1:50051 --model m --streams 2 --duration 5.0
# 2. 스트림 4개 (Torso, 오른팔, 왼팔, 베이스) 100Hz로 10초간 측정
python3 check_grpc_delay.py --address 127.0.0.1:50051 --model m --streams 4 --duration 10.0
# 3. 스트림 4개를 멀티스레드 병렬(Parallel)로 동시 전송할 때의 딜레이 비교
python3 check_grpc_delay.py --address 127.0.0.1:50051 --model m --streams 4 --duration 10.0 --parallel

