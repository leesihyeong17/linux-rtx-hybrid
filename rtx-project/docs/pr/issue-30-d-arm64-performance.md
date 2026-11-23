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

## B 연산 벤치마크의 ARM64 실행

ARM64 실행기는 빌드할 때 고정한 커밋에서 `benchmark.c`, `hooks.h`,
`apply_overlay.py`, `collect.py`를 읽는다. #36 이후 기본값은
`0df8dcd98ac9a4affe7772a1a2b7c759da32d2ba`다. 기존 #25 빌드의
`4e204ffda288734f722674bb696d40efbb78e9d6`도 수집 시 그대로 사용한다.
B가 측정하는 append는 skb 하나의 `rtx_append()` 반환까지이며 승격과 RB
재균형을 포함한다. split은 `tcp_fragment()` 전체 호출 시간이다.
ARM64 실행기는 각 연산을 다시 구현하지 않고 빌드 입력, 부팅 신원, B 수집기
입출력을 연결한다. RTT·손실 설정은 실제 TCP 실행에만 적용한다.

| 모드 | 커널에 적용되는 변경 | 읽을 결과 |
| --- | --- | --- |
| `tcp` | 공통 config를 적용한 production 커널 | 위 TCP 도구의 `summary.csv` |
| `performance` | B의 부팅 시 연산 fixture | append/split의 총 ns/op |
| `observation` | B fixture와 내부 hook | 승격·tail 실행 횟수, split 내부 구간, 예상 할당 실패 |

비교군은 `rb-basic`(API 전환 RB), `rb-cache`, `rb-tail`, `hybrid-ordered`,
`hybrid-tail`이다. 위 V0 예제는 과거 기준선이다. 캐시·tail 조건을 맞춘 비교는
같은 production SHA·seed config에서 `rb-tail`과 `hybrid-tail` 등 합의한
비교군을 만든다. 기본 production SHA는 #36의 벤치마크 수정 커밋이다.
통합본을 비교할 때는 `--source-sha`로 최종 커밋 전체 SHA를 지정한다.
#25 커밋을 직접 지정하면 당시 벤치마크를 사용한다. 다른 과거 빌드를
재현할 때는 빌드 기록의 `harness_sha`를 `--harness-sha`에 전달한다.

저장소 루트에서 실행한다. 얕은 복제본에서는 사용할 소스와 벤치마크 커밋을 먼저 받는다. 각 빌드는 새 경로에 소스 사본·
커널 산출물·증거를 보존하므로 여유 있는 파일시스템을 지정한다. 동일한 seed를
모든 비교군에 사용한다. 아래는 한 조합의 예시다.

```bash
build="$PWD/rtx-project/.work/d30-rb-tail-performance"
python3 -B rtx-project/scripts/run_arm64_b_benchmark.py build \
  --directory "$build" --base-config /boot/config-5.4.214-rtx-v0 \
  --variant rb-tail --mode performance --k 16
cat "$build/evidence/status.json"
```

`status=PASS`, `stage=built`일 때만 설치한다. `--object-only`는 GCC가
`tcp_output.o`까지 빌드하는 개발 검사이며 설치용 Image가 없다. 전체 빌드는 Image,
modules, DTB, vmlinux를 확인한다. Ubuntu 팀 VM에서 최초 설치하는 명령은 아래와
같다. 설치 경로가 이미 있는 release는 보존한다. 설치 후 VM의 GRUB 고급 메뉴에서
해당 release를 선택한다.

```bash
(
  set -eu
  rel=$(python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert (r["status"],r["stage"])==("PASS","built"); print(r["kernelrelease"])' "$build/evidence/status.json")
  (cd "$build/evidence" && sha256sum -c SHA256SUMS)
  for existing in "/lib/modules/$rel" "/boot/vmlinuz-$rel" "/boot/initrd.img-$rel" "/boot/config-$rel" "/boot/System.map-$rel"; do
    if [ -e "$existing" ] || [ -L "$existing" ]; then echo "이미 설치된 경로: $existing"; exit 1; fi
  done
  sudo make -C "$build/source" O="$build/out" ARCH=arm64 LOCALVERSION= INSTALL_MOD_STRIP=1 modules_install
  sudo make -C "$build/source" O="$build/out" ARCH=arm64 LOCALVERSION= install
  if [ ! -s "/boot/initrd.img-$rel" ]; then sudo update-initramfs -c -k "$rel"; fi
  sudo update-grub
)
```

부팅할 때 fixture가 자동 실행된다. 기본은 workload당 워밍업 10초와 연산 32회다.
기능 확인용으로 줄일 때만 GRUB의 해당 부팅 항목 커널 인자에
`rtx_bench_iterations=3 rtx_bench_warmup_ms=0`을 추가한다. 수집기는 실제
`/proc/cmdline`과 CONFIG 레코드를 대조한다. 부팅 후 예시는 다음과 같다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
uname -r
sudo python3 -B rtx-project/scripts/run_arm64_b_benchmark.py collect \
  --build-record rtx-project/.work/d30-rb-tail-performance/evidence/status.json \
  --repeat 1 --order 1
```

- 실행별 `d-b-native-*/status.json`이 PASS이고 `manifest.json`의 `valid`가
  true인지 확인한다. 커널 식별, 실제 부팅 조건, native TCP 전송·reset 확인,
  B의 프로토콜 검사가 모두 성공해야 집계한다. 실패 실행은 원본과 함께 남는다.
- `logs/dmesg.raw`는 커널 원본이다. `logs/serial.log`는 그 원본에 D의
  userspace TCP smoke 완료 줄을 붙인 B 수집기 입력이다. smoke는 같은 커널에서
  namespace/veth로 길이·내용·half-close와 reset을 확인한다. 개별 결과는
  `smoke/`에 보존한다. 입력·종료 경계가 유실되거나 손상된 부팅은 제외한다.
- `samples.jsonl`은 시도마다 N/L, K, skb 입력, 실제 연산 수와 단위를 제공한다.
  `operation-counts.csv`는 workload별 시도·성공·실패 합계, `summary.csv`는 한
  부팅 안의 중앙값·Q1·Q3다. 관찰 모드의 예상 split 실패는 시도 1, 실패 1로 기록한다.
  정상 성능 표본은 `performance`의 성공 workload만 사용한다.
- `configs/`에 config·차이와 계측 패치가 있고 `env-*/`에는 VM 환경이 있다.
  결과 디렉터리에서 `sha256sum -c SHA256SUMS`로 대조한다. 동일 boot ID의 재수집은
  한 부팅으로 센다. 비교군·K·반복 번호·실행 순서를 사전에 고정하고 대응 조건의
  observation과 performance를 연결한다. 연산 fixture의 고정 N과 실제 TCP의 #28
  삽입 히스토그램은 각각 해석한다.

## 확인 범위와 남은 일

| 확인 | 결과 | 로컬 증거 |
| --- | --- | --- |
| V0 부팅 신원과 loopback TCP | build ID 일치, bulk/RR 송수신·오류 거부 통과 | `boot-v0.log`, `tcp-tests.log` |
| ARM64 GCC 9.4 부분 빌드 | rb-basic/performance와 hybrid-tail/observation, K=16에서 `tcp_output.o` 생성 | `final-object-*/build.log`, `status.json`, `SHA256SUMS` |
| B 수집 규약 | B의 시험용 입력에서 예상 실패 계수 확인, 누락 입력·틀린 config·smoke 미완료·실행 실패 거부 | `b-tests.log` |
| 비교 커널 전체 링크·설치·부팅 및 성능 본 측정 | 팀의 최종 SHA·판정 기준 확정 후 직접 실행 | 후속 실행에서 생성 |

부분 빌드 두 개에서 B fixture의 `bm_valid` 스택 프레임이 1040바이트로
설정된 경고 기준 1024바이트를 넘었다. 측정용 원본과 경고 설정을 그대로
유지했다. B와 검토할 지점이다. 전체 커널 링크·부팅 시 동작은 이번 부분 빌드의
검증 범위를 벗어난다.

```bash
python3 -B -m unittest discover -s rtx-project/tests -p test_arm64_b_benchmark.py -v
python3 -B -m unittest discover -s rtx-project/tests -p 'test_*.py' -v
```

개발 증거는 위 로컬 폴더에 보존하며 팀 저장소에 원본을 업로드하면 그 경로를
추가한다. 보고서의 성능 결론은 같은 ARM64 VM과 비교 커널에서 직접 수집한
반복별 원시값과 변동을 사용한다.

전체 개발 회귀는 105개 통과했다. 현재 V0의 유효한 빌드 기록을 B 수집기에 넘긴
음성 대조도 예상대로 FAIL을 기록했고 표본 파일을 생성하지 않았다. 환경 수집에서
커널·CPU·메모리·부팅 인자를 확인했고 이 VM에는 `perf` 실행 파일이 없어 PMU
지원 여부는 미확정이다. 로컬 증거의 `report.png`는 로그의 줄 번호와 SHA-256을
표시한 HTML 보고서 캡처다. 전체 시험 로그 `all-tests.log`의 SHA-256은
`dd5d969b2dc1180cde875668a383dc51166e1028010887e96b25fa059e887829`,
캡처의 SHA-256은
`d3ee1f4cb4148f8252314418d1bebe401722d474fd47c1e4ac71c03dd5181697`이다.

B 원본 실행기와 같은 C/H/Python 파일 집합의 bundle hash를 확인하고 최종본으로
두 object 빌드와 회귀 105개를 다시 실행했다. 증거는 `bundle-parity.log`,
`final-object-*/`, `regression-final.log` 및 최상위 `SHA256SUMS`에 있다.
