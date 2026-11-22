# D의 ARM64 실제 TCP 및 큐 연산 성능 비교

[작업 이슈 #30](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/30) ·
[상위 #16](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/16) ·
[관련 큐 분포 #28](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/28) ·
[B 연산 fixture #25](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/25)

도구 기준 main `786037c6f`. 이 PR은 D의 실행 도구를 준비한다. 실제 비교군의
SHA·config와 판정 기준을 팀이 고정한 뒤 직접 부팅·반복 실행해 성능을 판단한다.

## 실제 TCP 실행

대상 ARM64 VM에서 비교할 **연산 fixture가 없는** 커널을 먼저 부팅한다.
실행기는 완료 빌드 기록과 설치 Image/config, 실행 커널의 GNU build ID와 빌드
vmlinux를 대조하고 RTX DEBUG/STATS 설정을 확인한다. 기존 V0 빌드 예시는 다음과 같다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
sudo python3 -B rtx-project/scripts/run_arm64_tcp_perf.py \
  --build-record rtx-project/.work/build-v0/validation-davvdlkt/status.json \
  --seconds 30 --warmup 10 --repeats 10 --rtt 20 --loss 1
```

각 반복에서 같은 전용 namespace/veth로 bulk 전송과 64바이트 요청·응답을 각각
실행한다. 워밍업과 측정은 별도 연결이며 qdisc 지연은 양방향 절반씩, 손실은
양방향 각각 지정한 백분율이다. `--reorder`는 지연이 있는 송신측에만 적용된다.
`--sender-cpu`와 `--receiver-cpu`로 endpoint affinity를 고정할 수 있다.
비교군 간 VM 자원·offload·CPU 설정·부하를 맞추고 실행 순서를 교차한다.

- `d-tcp-perf-*/`의 반복별 JSON에 양 끝의 payload 길이, 수신 시간, CPU tick,
  소켓 메모리를 기록한다. goodput은 요청/순방향 수신 bytes × 8 / 수신 구간 시간이다.
  수신기가 ready를 보내고 EOF를 받을 때까지의 시간이므로 요청·응답에는 echo 비용이 포함된다.
- CPU time/byte는 같은 구간의 **VM 전체** busy tick을 시간으로 환산한다. 송수신·softirq와
  배경 부하가 함께 포함된다. 짧은 표본은 tick 해상도로 0이 될 수 있다. CPU cycles는
  추정하지 않는다. Python endpoint와 가상 링크를 포함한 종단 측정이다.
- 송신 소켓 SO_MEMINFO를 약 100ms마다 읽은 관측 최댓값과 수신 소켓의 마지막 값을
  바이트로 기록한다. 버퍼 용량과 실제 할당량을 섞지 않는다. p50/p99는 측정 연결의 요청별
  왕복 ns를 nearest-rank로 계산한다. `latency-ns.u64`는 ARM64 little-endian uint64 원본이다.
- `summary.csv`에는 워밍업을 제외한 반복별 지표의 중앙값·사분위수를 기록한다.
  환경, 실행 인자, 로그, `status.json`, `SHA256SUMS`를 함께 보존한다. 실패한 실행은
  원본을 유지하고 전체 상태를 FAIL로 남긴다. 최종 비교에는 PASS 실행만 사용한다.

기존 정확성 endpoint는 매 바이트 패턴과 SHA를 검사한다. 성능 시험은 무결성 회귀를
별도 선행하고 시간 구간에는 수신 완료 길이 및 요청 echo를 확인해 해시 계산 비용을 줄였다.
생산 TCP·Core API를 변경하지 않는다. 커널 C checkpatch는 해당 없음이다.

## 개발 검증과 로컬 증거

ARM64 Ubuntu 20.04, GCC 9.4, 부팅된 V0에서 build ID와 기록을 대조했다.
loopback bulk/RR 전송, 바이트 불일치 거부, 지연 원본 개수, 잘린 커널 note 거부,
실패 실행 표시를 아래 명령으로 확인했다. 처음에는 실행 샌드박스가 TCP 소켓
생성을 거부했고, VM loopback에서 재실행해 3개 시험이 통과했다.
원본은 로컬 `rtx-project/.work/d-perf-development-7R7zB2jP/`의
`tcp-tests.initial.log`, `tcp-tests.log`, `boot-v0.log`에 있다.

```bash
python3 -B -m unittest discover -s rtx-project/tests -p test_arm64_tcp_perf.py -v
```

같은 커널 부팅에서 반복한 연결은 독립 부팅으로 세지 않는다. PMU probe와 부하의
cycles는 별도 항목이다. probe가 성공해도 이 실행은 workload cycles를 제공하지 않는다.
