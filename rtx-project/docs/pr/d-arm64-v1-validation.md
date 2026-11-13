# D: ARM64 V0·통합 V1 검증

작성 기준: 2026-11-13 KST. 브랜치 `test/d-arm64-v1-validation`.
커널 코드의 기준은 아래 SHA로 고정한다. 이 브랜치의 후속 테스트·문서 커밋을
커널 빌드 SHA로 대체하지 않는다. 원격 push·PR 게시는 아직 하지 않았다.

## 목적과 구현

ARM64 VM에서 기준 커널과 통합 V1을 같은 설정으로 준비하고, 빌드 및 실제
TCP 검증의 결과를 구분해서 보존한다.

- `prepare_v0_arm64.sh`와 `prepare_v1_arm64.sh`: 고정 SHA, clean source,
  ARM64 및 기존 출력 보존을 검사한다. 공통 구현은 `scripts/lib/prepare_arm64.sh`.
- `build_arm64.py`: `Image modules dtbs`를 `-j2`로 빌드한다. 동시 전체 빌드는
  파일 잠금으로 거부한다. 고유 `validation-*` 디렉터리에 명령·로그·make 종료 코드,
  설정/산출물 hash, 모듈·DTB 개수, 관심 함수의 nm 심볼을 저장한다.
  종료 코드 0이어도 산출물이나 설정 검증이 실패하면 PASS로 기록하지 않는다.
- `run_arm64_tcp.py`: 일반 프로세스 endpoint, 전용 namespace/veth 두 개,
  양방향 정상망/양쪽 egress 손실 1% 전송, half-close, reset을 검사한다.
  길이와 SHA-256을 양쪽에서 대조하며 수신 측은 전 바이트 패턴도 검사한다.
  tcpdump와 커널 로그를 보존하고 자신이 생성한 프로세스·namespace만 정리한다.
- `inspect_arm64_trace.py`: 빌드한 vmlinux의 심볼과 실제 부팅 커널의
  `available_filter_functions`를 구분한다. 부팅이 다르면 ftrace는 NOT RUN이다.
  추적 설정을 변경하지 않고 읽기만 한다.
- `run_arm64_packets.py`: 기존 B의 TUN packet peer를 재사용하는 native 진입점.
  SACK, 부분 누적 ACK, DSACK, reneging을 독립적인 패킷 시나리오로 구성한다.
  `smoke.c`의 PID 1 보호는 유지하며, native peer에는 reboot/mount 경로가 없다.
  native 진입점은 PID 1 또는 init과 같은 network namespace에서 실행을 거부한다.

## 고정 환경과 소스

| 항목 | 값 |
| --- | --- |
| VM | `ubuntu-tcp`, aarch64, 4 vCPU, RAM/swap 각각 약 3.8 GiB |
| 도구 | GCC 9.4.0, GNU ld 2.34, GNU Make 4.2.1 |
| V0 | `991defcc56cad49cfb221f44f5d161c9ea920c4a` (`v0-baseline`) |
| 통합 V1 | `231db9ed6a204d6407d88954da60ce7db1c2dabd` |
| 시작 시 원격 main | fetch 후 위 V1 SHA와 일치 |
| 시작 시 실행 커널 | `5.4.214-kimseungbeom+` |
| V0 release | `5.4.214-rtx-v0` |
| V1 release | `5.4.214-rtx-v1` |

원본 설정 SHA-256: `37b8cd3b70afa3d896e7601c0fe7d5586b09ca2eefdc341c5c135a58d70c94b8`.

| 최종 설정 | SHA-256 |
| --- | --- |
| V0 | `244905ec793f52b799353aed7ce2fd32f3a48f9533ab5a9c3838c61d0db56e2f` |
| V1 | `999b4426e72b674735257199fa416bed835d46f9efcadb3b5e7bf662adfcaf5c` |

`scripts/diffconfig`로 확인한 차이는 LOCALVERSION과 V1에 추가된
`TCP_RTX_DEBUG=n`, `TCP_RTX_STATS=n`뿐이다. V0에는 해당 Kconfig 옵션이 없다.
공통 설정에 ftrace 기능이 포함되어 있으며 이는 추적 활성 또는 성능 측정 완료를 뜻하지 않는다.

## 이번 세션 결과

기존 11월 6일의 준비·부분 컴파일 기록과 아래 새 실행을 구분한다.

| 대상 | 상태 | 근거·범위 |
| --- | --- | --- |
| V0 기존 설정 재사용 점검 | PASS | 고정 HEAD, clean source, `.config`와 보존 설정 hash 일치 |
| V1 설정·prepare | PASS | 고정 SHA, `5.4.214-rtx-v1`, 종료 코드 0 |
| V0 전체 빌드 | PASS | `validation-davvdlkt`, make 종료 코드 0, 모듈 5,968개, DTB 264개 |
| V1 전체 빌드 | RUNNING | `validation-gszehhr3`, 종료 코드와 산출물 확인 전 |
| V0/V1 설치·실제 부팅 | NOT RUN | 기존 커널에서 작업 중, 별도 sudo·UTM 단계 필요 |
| 기존 커널의 namespace 하네스 | PASS | `d-tcp-kspa5nnt`, 통신 16건, 커널 새 오류 0, cleanup 성공 |
| 보조 도구·판정기 테스트 | PASS | 최상위 54개 + runtime 15개 + integration 2개 = 71개. 실제 커널 검증과 구분 |
| 기존 커널의 SACK 협상 | PASS | TCP_INFO 및 SACK-permitted 패킷 확인 |
| 위 손실 실행의 SACK 블록 | NOT OBSERVED | 캡처 411개 중 SACK 블록 0개. 손실 설정만으로 경로 통과를 주장하지 않음 |
| deterministic native packet peer | NOT RUN | 컴파일 및 판정기 테스트 완료, sudo 실행 결과 대기 |
| 단일 GSO skb 내부 부분 ACK/trim 불변식 | NOT RUN | 추가 내부 관측 필요; 패킷 회계만으로 PASS 처리하지 않음 |
| 성능 비교 | NOT RUN | V0/V1 실제 부팅 및 정확성 검증 이후 진행 |

V0 전체 빌드는 14:12:10–14:46:58 KST, 약 34분 48초였다. 이는 이 실행의
실측 소요 시간이며 V1이나 다른 VM의 예상 시간으로 일반화하지 않는다.
V0 빌드에는 `-Wframe-larger-than` 경고 6개가 있었다:
`fs/jffs2/xattr.c`, `drivers/iommu/arm-smmu-v3.c`,
`drivers/net/ethernet/neterion/vxge/vxge-main.c`, `drivers/vhost/scsi.c`,
`drivers/vhost/vhost.c`, `drivers/usb/misc/usbtest.c`.
TCP 관련 빌드 오류는 없었지만 전체 빌드를 무경고로 표시하지 않는다.

V0 Image SHA-256: `0629ef40521e4ae34b873e2e8de173cd648d81328013efd2a019cf36e4aa7da5`.
V0 vmlinux SHA-256: `a5f84523d79c7a3c81e22c76cac613f2d4ba3d1731027a11622e19be3ae565f6`.

최초 namespace 실행 `d-tcp-6cy4rvwt`는 통신 16건과 cleanup은 성공했으나,
tcpdump의 kernel packet buffer가 전달되기 전에 종료되어 저장 패킷이 0개였다.
전체 결과를 FAIL로 보존했다. `-U`만으로 kernel buffer timeout을 없앨 수 없어
`--immediate-mode`를 추가했다. 재실행 `d-tcp-kspa5nnt`에서는
411 captured / 411 received / 0 dropped를 확인했다.
두 실행은 사용자가 VM 터미널에서 sudo로 실행했고 에이전트가 원시 결과를 읽어 검증했다.

환경 수집 `env-20261113T050909Z-d-v1-start-oJsaMu`는 58개 중 14개 항목 누락/실패,
종료 코드 2인 PARTIAL이다. 이 결과로 네트워크 실패 또는 전체 수집 성공을 주장하지 않는다.

## ARM64 함수 관측

V0 전체 빌드의 `nm --defined-only vmlinux` 결과다. 심볼 부재의 원인을
개별 분석 없이 모두 인라인이라고 단정하지 않는다.

| 함수 | V0 nm | 실제 ftrace | 대안 |
| --- | --- | --- | --- |
| tcp_ack | 있음 | NOT RUN | 호출 1회를 ACK 1회로 집계, inclusive 시간 해석 |
| tcp_sacktag_walk | 있음 | NOT RUN | 상위 SACK 함수와 방문 노드 수를 별도 기록 |
| tcp_sacktag_write_queue | 있음 | NOT RUN | SACK 패킷 관측과 함께 해석 |
| tcp_clean_rtx_queue | 없음 | NOT RUN | tcp_ack 및 ACK 처리 후 큐·회계 snapshot |
| tcp_shift_skb_data | 없음 | NOT RUN | 상위 함수와 별도 shift/merge 진단 |
| tcp_tso_acked | 없음 | NOT RUN | tcp_trim_head와 호출 경로·GSO/잔여 skb 관측 |
| tcp_trim_head | 있음 | NOT RUN | 재전송 측 호출과 ACK 측 호출을 구분 |
| rtx_find_cover | 없음 | NOT RUN | V0에 어댑터 없음. V1은 별도 nm 확인 |

현재 V1 소스의 `ACK_TRIM`, `ACK_CLEAN`, `SACK_WALK_SKB`, `SHIFT_*`는
선언·출력만 있고 증가 지점이 확인되지 않는다. `/proc/rtx_stats`의 해당 0값은
경로 미실행의 근거가 아니다. 계측을 추가한다면 C와 증가 위치·의미를 먼저 맞춘다.

## 재현과 다음 단계

저장소 루트에서 실행한다. 이미 존재하는 worktree/출력 디렉터리를 다시 만들지 않는다.

```bash
# 준비된 고정 소스의 전체 빌드. V0/V1을 동시에 실행하지 않는다.
python3 rtx-project/scripts/build_arm64.py \
  --source rtx-project/.work/v1 --output rtx-project/.work/build-v1 --variant v1

# 전체 빌드 성공 후 심볼/추적 가능 여부 기록
python3 rtx-project/scripts/inspect_arm64_trace.py \
  --build-output rtx-project/.work/build-v1 --variant v1

# 기존 커널에서는 하네스 검증 범위로만 실행
sudo python3 rtx-project/scripts/run_arm64_tcp.py --smoke-current-kernel
sudo python3 rtx-project/scripts/run_arm64_packets.py --smoke-current-kernel

# V1 설치·실제 부팅 후 실행. 다른 release나 설치 이미지/config hash 불일치 시 거부한다.
sudo python3 rtx-project/scripts/run_arm64_tcp.py \
  --variant v1 --build-output rtx-project/.work/build-v1
sudo python3 rtx-project/scripts/run_arm64_packets.py \
  --variant v1 --build-output rtx-project/.work/build-v1
```

설치·부팅은 [기존 절차](../testing.md)의 4절을 따른다. 출력 경로와 release를
각 비교군으로 지정하고 `/boot` 공간, 동일 release 충돌, 모듈·initramfs·GRUB,
기존 정상 커널의 복구 항목을 확인한다. 전체 빌드가 실행 중일 때 재부팅하지 않는다.
UTM에서 커널을 선택하고 재연결한 뒤 실행 커널과 설정/이미지 hash를 확인한다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s rtx-project/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s rtx-project/tests/runtime -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s rtx-project/tests/integration -v
```

최상위 테스트 중 loopback 검사는 실제 소켓 권한이 필요하다. 네트워크를 제한하는
샌드박스에서는 해당 실행이 FAIL로 끝나므로 VM에서 실행한다. native peer는
GCC 9.4로 `-O2 -Wall -Wextra -Werror` 컴파일했고 기존 guest 모드도 객체 컴파일했다.
guest QEMU 실행 검증을 이번 ARM64 실행으로 대체했다고 기록하지 않는다.

## 부분 ACK 및 측정 계약

- native PARTIAL 시나리오는 MSS 1000, 전송 4000바이트, 누적 ACK 2000바이트,
  TCP_INFO의 unacked 2, 최종 큐 회계 해소와 payload 일치를 검사한다.
  **단일 다중 세그먼트 skb, trim 진입, 잔여 skb seq/len/pcount는 별도 관측 전까지 미검증이다.**
- 기존 B peer를 재사용해 Linux 5.4에서 이미 사용한 handshake/payload/FIN 처리를
  유지했다. packetdrill 신규 의존성과 중복 peer 구현을 추가하지 않았다.
  시험 도구 버전은 Git 커밋과 실행 디렉터리에 복사한 소스 hash로 고정한다.
- GSO/TSO를 일괄 비활성화하지 않는다. ARM64 추적을 추가할 때 x86 `%si/%cx`
  fetch 인자를 복사하지 않고 빌드와 아키텍처의 인자/구조체 배치를 검증한다.
- DSACK는 누적 ACK 아래의 중복 블록 및 TCPDSACKRecv 증가, reneging은 앞서 SACK한
  구간을 철회한 뒤 해당 데이터의 재전송 및 TCPSACKReneging 증가로 확인한다.
- ftrace는 진단용이다. 최종 성능은 STATS/DEBUG off와 실제 추적 비활성 상태를 확인한
  별도 실행으로 측정한다. 중첩 함수의 inclusive 시간을 단순 합산하지 않는다.
- CPU time/byte는 성공적으로 수신한 application byte가 분모다. softirq 실행 CPU를
  확인하고, 송신 측만 분리하지 못하면 VM 전체 CPU 비용으로 표기한다.
  `/proc/softirqs` 횟수를 시간으로 바꾸거나 프로세스 시간만으로 ACK 전체 비용을 주장하지 않는다.

## 증거 공유

원시 로그와 커널 산출물은 `rtx-project/.work/`에 보존되며 Git에 포함되지 않는다.
이 문서의 수치·hash·실패 기록이 공유 가능한 요약이다. PR에 근거를 첨부할 때는
해당 실행의 `summary.json`/`status.json`, 종료 코드, 필요한 작은 로그와 체크섬을
함께 제공한다. `.work` 로컬 링크만으로 팀원이 원본을 열 수 있다고 가정하지 않는다.
