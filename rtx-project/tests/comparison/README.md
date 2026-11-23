# #16 ARM64 비교 재현

Apple Silicon에서 커널 이미지를 직접 부팅한다. 게스트에 디스크나 외부 NIC를 연결하지 않는다. 각 부팅에서 생성한 두 네트워크 namespace 사이에 veth를 연결하고, D의 `run_arm64_tcp_perf.trial()`과 endpoint로 수신 바이트, RR 응답, VM busy CPU, 소켓 메모리를 수집한다. C fixture와 LIST 길이 계측은 빌드 컨테이너의 일회용 소스에만 적용한다. [측정 조건](plan.md)

## 실행

Docker Desktop, Python 3, Git, cpio, gzip, patch와 macOS용 공식 vfkit 0.6.4 바이너리가 필요하다. 저장소는 V0, V1, #25 병합 커밋과 #36 벤치마크 커밋 `0df8dcd98`을 포함한 전체 복제본을 사용한다. [vfkit 0.6.4 릴리스](https://github.com/crc-org/vfkit/releases/tag/v0.6.4)

저장소 루트에서 새 출력 경로와 vfkit 파일을 지정한다. 실행기는 vfkit 체크섬을 확인하고 ARM64 Ubuntu 20.04에서 커널을 빌드한다. 원본 체크아웃은 읽기만 한다. 컨테이너를 제거한 뒤 게스트를 한 대씩 부팅한다. 아래 예에서 `/path/to/vfkit`은 내려받은 실행 파일이다.

```sh
bash rtx-project/tests/comparison/reproduce.sh \
  "$PWD/rtx-project/.work/issue16-repeat" /path/to/vfkit
```

`builds/`에는 각 구성의 `Image`, 전체 `.config`, 소스 커밋과 빌드 로그가 있다. `runs/`, `long-runs/`, `batch-runs/`는 각각 사전 고정한 기본 비교, 긴 큐 조건, 묶음 append 조건이다. 부팅별 `serial.log`에는 커널 출력이 있고, 직렬 포트로 전송한 게스트 자료는 `results.tar.gz`에 분리했다. 여기에는 양쪽 endpoint의 원본 JSON, RR의 64비트 ns 배열, 계측 부팅의 카운터와 히스토그램, 네트워크 설정 명령과 종료 상태가 있다. `manifest.json`에 이미지·initramfs·콘솔·측정 자료의 해시와 boot ID가 있다. 가상화 호스트가 물리 CPU 코어를 선택한다. 측정 중에는 다른 빌드를 돌리지 않는다.

## 결과 재계산

`analyze.py`는 계획한 부팅이 모두 있고 식별자와 해시가 맞는지 확인한다. 양 끝점의 바이트 수, RR 표본 수, 연산 결과, 히스토그램 총합을 검사한 뒤 표를 쓴다. 통계 계산에는 Python 표준 라이브러리를 사용한다. 그림을 다시 그릴 때만 Matplotlib가 필요하다.

```sh
run="$PWD/rtx-project/.work/issue16-repeat"
python3 -B rtx-project/tests/comparison/analyze.py "$run" "$run/tables"
python3 rtx-project/tests/comparison/figure.py "$run/tables" "$run/tables/comparison.png"
```

`tcp.csv`의 busy ns/byte는 게스트 전체의 CPU 시간이다. `peer_user_s`와 `peer_system_s`는 두 endpoint 프로세스의 시작·종료 비용까지 포함한다. `queue-counters.csv`의 `n_le16`은 ACK 시점에 N≤16이었던 횟수이고 `l_sum / ack_clean`은 같은 시점의 평균 LIST 길이다. `operations.csv`의 연산 시간은 각 부팅의 중앙값이다. 묶음 unlink는 N부터 빈 큐까지 가장 오래된 skb부터 제거하는 구간을 평균낸다.
