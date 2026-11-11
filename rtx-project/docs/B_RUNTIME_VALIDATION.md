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

설정은 x86_64_defconfig에 TCP_RTX_DEBUG, initramfs, serial console, TUN, tracing을 활성화하고 MODULES를 비활성화한 테스트 전용 설정이다. 기존 `rtx_defconfig`의 부분 빌드 검증과 구분한다. QEMU TCG 1 vCPU/512 MiB, 외부 NIC 없음. 성능 측정 환경이 아니다.

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

IPv6, 실제 NIC, 여러 CPU, ARM64·팀 VM, 할당/unclone 실패 주입, SACK/RACK/TLP 개별 경로, FIN/GSO/sequence wrap 조합, 내부 시간 큐·회계·힌트의 전수 검증은 기본 smoke 통과 범위에 포함하지 않는다. 실제 분할 결과는 ③의 패킷 시나리오와 추적 증거로 별도 판정한다.
