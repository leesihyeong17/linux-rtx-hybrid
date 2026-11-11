# B split 연결 실행 검증

## 범위와 재현

②는 split 연결 커널의 부팅과 기본 TCP 송수신, ③은 실제 RTX 분할·재전송 관찰을 담당한다. 각 단계의 실행 결과는 아래에 별도로 기록한다. A의 자료구조 자체 테스트만으로 실제 `tcp_fragment()` 실행을 입증하지 않는다.

```sh
rtx-project/scripts/run_b_runtime.sh
# 다른 호환 Ubuntu 22.04 이미지 사용 시:
RTX_BUILD_IMAGE=962f6cadeae0 rtx-project/scripts/run_b_runtime.sh
```

Docker가 필요하다. 실행 전에 기존 이미지·컨테이너를 확인하며, runner는 지정 이미지가 로컬에 없으면 실패한다. 컨테이너 내부에 cross GCC와 QEMU 의존성을 설치하고 Linux 파일시스템에서 빌드한다. 호스트 커널 설치·재부팅이나 호스트 네트워크 변경은 하지 않는다.

커널 소스는 실행 시점 **커밋된 HEAD**의 `git archive`다. 미커밋 커널 수정은 포함되지 않는다. 게스트 테스트·runner는 실행 시점 파일을 복사하고 체크섬으로 식별한다. 출력은 실행마다 새로운 `rtx-project/.work/b-runtime-*`에 보존한다. 원시 결과와 커널 산출물은 Git에서 제외한다.

②의 설정은 x86_64_defconfig에 TCP_RTX_DEBUG, initramfs, serial console, TUN, tracing을 활성화하고 MODULES를 비활성화한 테스트 전용 설정이다. 기존 `rtx_defconfig`의 부분 빌드 검증과 구분한다. QEMU TCG 1 vCPU/512 MiB, 외부 NIC 없음. 성능 측정 환경이 아니다.

## ② 부팅·기본 송수신

정적으로 링크한 테스트 프로그램을 게스트 PID 1로 실행한다. PID 1이 아니면 즉시 종료하므로 호스트에서 실행하지 않는다.

- 커널 release와 A의 `rtx_selftest` 성공 요약을 확인한다.
- IPv4 loopback에서 0 / 4,097 / 1,048,576바이트를 전송한다.
- 수신 바이트를 결정적 패턴과 모두 비교하고 총 길이를 확인한다. 해시 비교 대신 전체 바이트를 직접 검증한다.
- 송신 측 `SHUT_WR` 이후 수신 측의 응답과 EOF를 확인해 half-close를 검사한다.
- `SO_LINGER={1,0}` 종료를 상대가 ECONNRESET으로 관찰하는지 확인한다.
- 각 연결은 새로운 소켓을 사용한다. 게스트 60초, QEMU 180초 제한과 WARN/OOPS/panic 검사로 실패·정지를 탐지한다.

성공은 네 통신 사례, 자체 테스트의 0 failures, 예상 release, 최종 PASS, QEMU 정상 종료가 모두 있어야 한다. 실패 시 로그를 남기고 작업용 컨테이너만 제거한다. 성공 후에도 잔존 컨테이너가 있으면 실패 처리한다.

### 실행 기록

2026-11-11, kernel source `7a7b4f9acb9a35f14ba030b33f0644adebc4bd63`, Ubuntu 22.04 arm64 / cross GCC 11.4.0 / QEMU 6.2.0에서 실행했다.

| 항목 | 결과 |
| --- | --- |
| 전체 bzImage 빌드·부팅 | 성공, `5.4.214-rtx-b-runtime` |
| A 구조 자체 테스트 | 495 passed, 0 failed |
| 세 payload·half-close 및 reset | 4/4 PASS, 전체 바이트·길이 일치 |
| WARN/OOPS/panic | 발견 없음 |
| QEMU/runner 종료 | 0, 작업용 컨테이너 잔존 없음 |

증거: `.work/b-runtime-wneGcWqa/` (경로는 `rtx-project/` 기준). `serial.log`, `SHA256SUMS`, `environment.txt`, `exit-code.txt`, cleanup 기록을 보존했다. 최초 전체 빌드 로그·설정은 `.work/b-runtime-LHpOzppm/`에 있다. 커널 이미지 SHA-256은 `d88dfbe8e9f6e6c6c85ef7e0de42da9c8f5d0c4d9d9039239fbffbb56959c267`이다.

실패 기록도 보존했다. `LHpOzppm`에서는 reset 테스트의 연결 완료 동기화가 없어서 실패했고, `KUbMe9VK`에서는 최소 패키지 설치 시 cross libc 헤더가 누락되어 게스트 컴파일이 실패했다. reset 확인 바이트 교환과 명시적인 `libc6-dev-amd64-cross` 의존성으로 보완한 뒤 `wneGcWqa`에서 통과했다. 커널 코드는 이 과정에서 변경하지 않았다.

동일 커널을 재사용할 때는 `--kernel-results <이전 결과 디렉터리>`를 지정한다. runner는 이전 산출물 체크섬과 이전 커밋 대비 커널 소스 차이를 검사한다. `rtx-project`의 문서·검증 도구 변경만 허용하며, 게스트 프로그램은 매번 다시 컴파일한다. 이번 실행 검증은 DEBUG on이며 DEBUG off는 기존 부분 빌드까지만 검증됐다.

## 남은 범위

IPv6, 실제 NIC, 여러 CPU, ARM64·팀 VM, 할당/unclone 실패 주입, FIN/GSO/sequence wrap 조합, 내부 시간 큐·회계·힌트의 전수 검증은 기본 smoke 통과 범위에 포함하지 않는다. SACK/TLP/RTO의 선정 경로는 아래 ③에서 추가 검증했다. RACK 개별 경로는 아직 미검증이다. 실제 분할 결과는 ③의 패킷 시나리오와 추적 증거로 별도 판정한다.

## ③ 실제 SACK·TLP·RTO 분할 및 재전송

```sh
rtx-project/scripts/run_b_runtime.sh --packets
python3 -m unittest discover -s rtx-project/tests/runtime -p 'test_*.py'
```

이 모드는 MODULES/KPROBES/KPROBE_EVENTS를 활성화한 커널을 사용한다. Linux 5.4의 KPROBES가 MODULES에 의존하기 때문이다. 기존 ② 이미지는 이 조건을 만족하지 않아 재사용할 수 없다. 이후 같은 추적 설정의 이미지는 `--packets --kernel-results <결과>`로 재사용할 수 있다.

게스트 전용 TUN에 `198.18.0.1/24`를 설정하고 사용자 공간 패킷 peer가 `198.18.0.2:8080`으로 응답한다. SYN/SYNACK으로 MSS=1000과 SACK을 협상하고 실제 TCP 소켓에서 4,000바이트를 송신한다. 수신 데이터는 전 바이트를 검사한다.

- SACK: 누적 ACK는 진행하지 않고 중간 MSS `[base+1000, base+2000)`만 SACK한다. TCP_INFO의 sacked=1, unacked=4를 확인한다.
- LOSS: tcp_early_retrans=3으로 ACK를 보류하고 실제 중복 데이터 패킷을 수신한 뒤 payload를 검사한다. 이번 추적에서는 tcp_send_loss_probe의 호출을 확인했다.
- RTO: tcp_early_retrans=0으로 TLP를 끄고 ACK를 보류한다. __tcp_retransmit_skb에서의 분할과 중복 데이터 수신을 확인한다.
- 세 경우 모두 최종 ACK 후 unacked/sacked/lost=0과 FIN 종료를 확인한다.
- x86_64 진입 레지스터에서 `tcp_fragment`의 queue/len을, kretprobe에서 반환값을 기록한다. 스택 추적으로 호출 경로도 남긴다. 프로덕션 TCP 코드는 변경하지 않는다.
- 시나리오별 RTX(queue=1) 분할 호출이 적어도 한 번 있고, 모든 반환이 0이며, trace 덮어쓰기가 없어야 성공이다. 단순히 최종 PASS 문자열이 있는 것만으로 통과시키지 않는다.
- 추적 판정기의 단위 테스트는 분할 누락, write queue 오인, -ENOMEM 반환, trace 덮어쓰기, 재전송 미발생, 시나리오 누락을 거부하는지 확인한다.

### 실행 기록

2026-11-11, kernel source `038f8b180` (②의 테스트·문서 커밋이며 TCP 코드는 `7a7b4f9ac`와 동일), cross GCC 11.4.0 / QEMU 6.2.0에서 실행했다.

| 시나리오 | 관찰한 tcp_fragment 호출자 | RTX 분할·성공 반환 | 데이터 및 회계 |
| --- | --- | --- | --- |
| SACK | `tcp_match_skb_to_sack` | len=1000 두 번, 모두 0 | 4000바이트 일치, sacked=1/unacked=4 확인, 최종 ACK 후 해소 |
| LOSS (TLP) | `tcp_send_loss_probe` | len=3000 한 번, 0 | 4000바이트 일치, 1000바이트 재전송 수신, 최종 ACK 후 해소 |
| RTO | `__tcp_retransmit_skb` | len=1000 한 번, 0 | 4000바이트 일치, 1000바이트 재전송 수신, 최종 ACK 후 해소 |

모든 추적의 queue=1(RTX)이며 buffer 기록 수는 15/15로 덮어쓰기가 없었다. 기본 통신 4건 및 A 자체 테스트 495/0도 함께 통과했다. 성공 실행에 WARN/OOPS/panic이 없고 runner 종료 코드는 0이다. SACK 사례의 `total_retrans=1`도 원시 로그에 남겼으며, 이 시나리오를 무재전송 검사로 해석하지 않는다.

증거: `.work/b-runtime-TeNAlMRK/`의 `serial.log`, `trace-summary.txt`, `SHA256SUMS`, 환경·설정·정리 로그. 추적 커널의 전체 빌드 로그는 `.work/b-runtime-nFfGZmCz/`에 있다. 커널 SHA-256은 `c4819318f877e2e893e2d819de5a433e4d720cd20080db362f1e1e47bd11ff48`이다. 테스트 전용 컨테이너는 제거했고 잔존이 없음을 확인했다.

최초 `nFfGZmCz` 실행은 IPv4 전용 peer의 비-IPv4/TCP 패킷 처리에서 실패했다. 게스트 TUN의 IPv6 자동 설정을 끄고 `TeNAlMRK`에서 재검증했다. 당시 테스트 PID 1의 오류 종료가 kernel panic으로 표시된 실패 로그도 보존했다. 이후 테스트 오류는 실패 마커를 출력하고 종료하도록 보완했다.

새 추적 판정기 단위 테스트 7건이 통과했다. 기존 환경 수집·V0 준비 테스트 17건은 macOS의 GNU 도구 누락으로 직접 실행에 실패했으나 Linux 컨테이너에 필요한 git/openssl/rsync를 설치한 뒤 17/17 통과했다 (`nFfGZmCz/existing-tests.log`). 이 단위 테스트 결과는 TCP 실행 검증과 별개다.
