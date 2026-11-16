# TCP RTX 프로젝트 진행 정리

작성일: 2026-11-14 KST

범위: 11월 6일 준비 작업부터 11월 13일 ARM64 검증·C 리뷰 반영까지

후속 기록: 11월 16일 공유받은 B·C 리뷰 대조는 [리뷰 검토 기록](review-status-2026-11-16.md)에 추가했다. 원격 작업 브랜치에도 C 리뷰 반영 코드 `9cdfeee5b`까지 올라간 것을 확인했다. 아래 본문은 11월 14일의 로컬 브랜치·확인 범위를 유지한다. B가 별도 팀 브랜치에서 보고한 HYBRID K=16 실행과 최신 peer의 검증 근거 확인 사항은 후속 기록을 참조한다.

> **현재 상태:** RB 기반 V1의 공통 API 연결과 선정된 x86_64 QEMU 실행 검증을 마쳤고, ARM64 V0·V1 전체 커널 빌드도 성공했다. ARM64의 통신 시험 도구는 기존 커널에서 검증했다. **ARM64 V0·V1 설치·부팅, 해당 커널에서의 통합 회귀, 하이브리드 구현과 성능 비교는 아직 남아 있다.**

이 문서는 저장소의 작업 문서와 실행 결과를 종합한 기록이다. A·B·C의 기존 팀 작업과 D의 ARM64 검증을 구분한다. 이번 정리에서는 빌드나 테스트를 새로 실행하지 않았다.

## 1. 프로젝트 목표와 구현 단계

Linux 5.4.214의 TCP 재전송 큐를 공통 `rtx_*` API로 추상화하고, 이후 자료구조 변경에 따른 정확성과 비용을 비교한다.

| 단계 | 내용 | 현재 상태 |
| --- | --- | --- |
| V0 | 팀 기준 커널 | 기준 소스 고정, ARM64 전체 빌드 완료 |
| V1 | 기존 RB 트리를 유지하며 공통 API로 호출부 전환 | 통합 소스 확보, 선정된 QEMU 실행 검증 및 ARM64 전체 빌드 완료 |
| V2 | 큐의 head/tail 접근을 위한 캐시 최적화 | 후속 구현·검증 대상 |
| V3 | tail 삽입 위치 탐색 생략 | 후속 구현·검증 대상 |
| V4 | 오래된 구간의 RB 트리 + 최근 구간의 길이 제한 리스트 | 설계 단계, 현재 구현은 RB backend |

하이브리드의 목적은 최근 데이터가 ACK로 빠르게 제거되는 흐름에서 트리 연산을 줄이는 것이다. 실제 성능 향상 여부는 아직 측정하지 않았다.

## 2. 작업 환경과 기준 소스

| 항목 | 내용 |
| --- | --- |
| 저장소 | `linux-rtx-hybrid` |
| 현재 브랜치 | `test/d-arm64-v1-validation` |
| 정리 기준 로컬 HEAD | `9cdfeee5b5fcad8ce448c5624b95486035e94d00` |
| V0 빌드 기준 | `991defcc56cad49cfb221f44f5d161c9ea920c4a` (`v0-baseline`) |
| 통합 V1 빌드 기준 | `231db9ed6a204d6407d88954da60ce7db1c2dabd` |
| ARM64 환경 | UTM의 `ubuntu-tcp`, Ubuntu 20.04.5 LTS, aarch64 |
| VM 자원 | 4 vCPU, RAM 약 3.8 GiB, swap 약 3.8 GiB |
| ARM64 빌드 도구 | GCC 9.4.0, GNU ld 2.34, GNU Make 4.2.1 |
| 현재 실행 커널 | `5.4.214-kimseungbeom+` — 11월 14일에도 확인 |
| B의 별도 실행 환경 | Ubuntu 22.04 ARM64 컨테이너에서 x86_64 cross build, QEMU 6.2.0 |

현재 브랜치 HEAD에는 후속 시험 도구·문서 변경이 포함되어 있다. 이를 실제 커널 빌드 기준 SHA와 혼동하지 않는다. 원격 push·PR 게시 상태는 이번 정리에서 조회하지 않았다.

## 3. 완료한 작업

### 3.1 초기 환경 수집과 V0 준비 — D, 11월 6일

- 환경·도구·Git 상태·커널 설정을 실행별 디렉터리에 보존하는 `capture_env.sh` 작성.
- 고정 소스와 설정을 확인하고 ARM64 빌드를 준비하는 `prepare_v0_arm64.sh` 작성.
- V0의 `olddefconfig`, `prepare`, release 확인 완료.
- `tcp_input.o`, `tcp_output.o` 개별 컴파일 성공.
- 당시 보조 스크립트 단위 테스트 **17개 통과**.
- 빌드·설치·부팅·정확성 검사·성능 측정의 순서와 판정 기준 문서화.

초기 환경 수집은 58개 중 45개 정상, 13개 누락·오류로 **부분 완료**였다. 이후 11월 13일 수집도 58개 중 14개 누락·실패가 있는 부분 완료로 기록했다. 환경 수집의 누락을 TCP 시험 실패로 해석하지 않는다.

11월 6일 문서의 ‘전체 빌드 미실행’은 당시 상태다. 전체 빌드는 아래 11월 13일 작업에서 완료했다.

### 3.2 RB 기반 V1 연결 — A·B·C 팀 작업

| 담당 | 완료한 주요 작업 |
| --- | --- |
| A: Core | `rtx_*` 공통 API·순회 매크로, RB split prepare/commit 계약과 구현, 디버그 자체 테스트, init·purge 경로 정리 |
| B: 송신 | 신규 송신·조회·재전송 호출부를 공통 API로 전환하고, RTX `tcp_fragment()`에 split prepare/commit 연결 |
| C: ACK/SACK | ACK 정리·SACK 검색 및 순회 등 재전송 큐 직접 접근을 공통 API로 전환하고 호출 지점 감사표 작성 |

자료구조 연결과 TCP의 메모리 해제·회계·시간 큐 처리는 각 API 계약에 따라 구분했다. write queue와 수신 out-of-order 큐의 처리는 재전송 큐와 구분한다.

### 3.3 QEMU 부팅·실제 분할·회귀 검증 — B, 11월 11일

DEBUG on의 x86_64 시험 커널을 전체 빌드하고 QEMU에서 부팅했다. 외부 NIC 없는 TCG 1 vCPU·512 MiB 환경이며 성능 측정용 실행은 아니다.

| 검증 | 결과 | 확인 범위 |
| --- | --- | --- |
| A 자료구조 자체 테스트 | **495 passed, 0 failed** | 공통 API와 구조 동작 |
| 기본 TCP 통신 | **4/4 PASS** | 0·4,097·1,048,576바이트, 전체 바이트·길이 일치, half-close 및 reset |
| SACK | RTX 분할 **2회**, 모두 성공 반환 | `tcp_match_skb_to_sack`에서 len=1000 분할 |
| TLP | RTX 분할 **1회**, 성공 반환 | `tcp_send_loss_probe`에서 len=3000 분할, 재전송 수신 |
| RTO | RTX 분할 **1회**, 성공 반환 | `__tcp_retransmit_skb`에서 len=1000 분할, 재전송 수신 |
| 추가 실제 커널 fixture | **285 assertion 통과** | 실패 복구·경계·호출 경로·불변식 검사 |

SACK·TLP·RTO 각 시나리오에서 4,000바이트의 데이터 일치와 최종 ACK 후 회계 해소를 확인했다. Kprobe/kretprobe로 RTX 큐 분할과 성공 반환을 관찰했으며, 성공 실행에 새 WARN/OOPS/panic이나 trace 덮어쓰기가 없었다.

추가 fixture 285개의 구성은 다음과 같다.

| 항목 | assertion 수 |
| --- | ---: |
| unclone·할당 호출 경계의 실패 주입 및 재시도 | 38 |
| head/middle/tail, 재분할, FIN/GSO/sequence wrap | 172 |
| write queue, TSO fast path, Reno 손실 표시 | 49 |
| 회계·힌트·시간 큐, 잘못된 상태 거부, RACK 소비 | 26 |

fixture는 특정 커널 상태를 구성해 실제 함수를 실행한 시험이다. allocator 내부 고갈이나 네트워크상의 sequence wrap 송수신까지 검증한 것은 아니다. 이 QEMU 결과를 ARM64 V0·V1 부팅 결과로 대체하지 않는다.

### 3.4 ARM64 V0·V1 전체 빌드 — D, 11월 13일

`build_arm64.py`로 `Image modules dtbs` 전체 빌드를 수행했다. 동시 빌드를 막는 잠금과 산출물·설정 검증을 추가하고, 실행별 명령·로그·종료 코드·체크섬을 보존했다.

| 항목 | V0 | 통합 V1 |
| --- | --- | --- |
| 결과 | **PASS** | **PASS** |
| release | `5.4.214-rtx-v0` | `5.4.214-rtx-v1` |
| 실행 시간(KST) | 14:12:10–14:46:58 | 15:23:23–15:55:54 |
| 실측 소요 시간 | 34분 48초 | 32분 31초 |
| make 종료 코드 | 0 | 0 |
| 모듈 수 | 5,968 | 5,968 |
| DTB 수 | 264 | 264 |
| 설치·부팅 | **NOT RUN** | **NOT RUN** |

설정 차이는 LOCALVERSION과 V1에 추가된 `TCP_RTX_DEBUG=n`, `TCP_RTX_STATS=n`이다. V0에는 해당 Kconfig 옵션이 없다.

두 빌드 모두 같은 6개 소스에서 `-Wframe-larger-than` 경고가 있었다. 전체 빌드는 성공했고 TCP 관련 빌드 오류는 없었지만, 무경고 빌드로 기록하지 않는다.

### 3.5 ARM64 통신 시험 도구 실행 — D, 11월 13일

실행 커널은 **기존 `5.4.214-kimseungbeom+`**이다. 사용자가 VM 터미널에서 sudo로 실행했고, 저장된 원시 결과를 검토했다.

| 검증 | 결과 |
| --- | --- |
| 전용 namespace/veth 기반 TCP 통신 | 정상망·양쪽 egress 손실 1%, 양방향 합계 **16건 PASS** |
| 데이터 무결성 | 길이·SHA-256 대조 및 수신 전체 바이트 패턴 검사 통과 |
| 연결 종료 | half-close·reset 검사 통과 |
| 패킷 캡처 | **411 captured / 411 received / 0 dropped** |
| 시나리오를 직접 구성한 packet peer | **SACK·PARTIAL·DSACK·DSACK_OFO·RENEGING 5건 PASS** |
| MIB 증가량 | `TCPDSACKRecv`, `TCPDSACKOfoRecv`, `TCPSACKReneging` 각각 **+1** |
| 커널 새 오류·정리 오류 | 없음 |

일반 손실 시험의 캡처에는 SACK-permitted 협상은 있었지만 실제 SACK 블록은 0개였다. 따라서 해당 실행만으로 SACK 처리 경로를 검증했다고 표시하지 않고, 별도의 packet peer로 관련 입력을 구성했다.

PARTIAL의 `unacked=2`는 패킷 회계 근거다. 단일 GSO skb의 `tcp_trim_head` 진입·잔여 skb 상태는 아직 별도 관측이 필요하다. DSACK_OFO의 MIB 증가 역시 내부 `tcp_sacktag_walk` 순회 완료를 뜻하지 않는다.

### 3.6 C 리뷰 반영과 ARM64 관측 준비 — D, 11월 13일

- RENEGING 시험에서 먼저 도착하는 RACK 재전송을 검증·기록한 뒤 목표 구간 재전송을 확인하도록 보완.
- PARTIAL 시나리오에서 TLP를 끄고, DSACK_OFO의 outstanding 데이터·두 SACK 블록·회계 조건을 검사.
- 저장된 로그를 재검증하여 shift MIB 증가량 **1 / 0 / 6**을 별도 기록. 순서는 `TCPSackShifted` / `TCPSackMerged` / `TCPSackShiftFallback`이며, 기존 커널 1회 실행 값이다.
- 빌드 산출물의 심볼 존재 여부와 실제 부팅 커널의 ftrace 가능 여부를 구분하는 검사 도구 작성.
- V0·V1의 실제 빌드 설정으로 skb 필드 위치를 컴파일해 ARM64 trim probe 정의 생성.
- C와 ACK·SACK·shift 카운터의 증가 기준 및 D의 후속 관측 판정 기준 문서화.

두 빌드 모두 `sk_buff.cb` 위치 40바이트, cb 내부 `seq/end_seq/tcp_gso_segs` 위치 0/4/8바이트, 필드 크기 4/4/2바이트를 확인했다. **필드 위치 검증은 PASS이고, 런타임 probe 등록·관측은 NOT RUN이다.**

리뷰 반영 후 자동 회귀 테스트는 **최상위 66개 + runtime 15개 + integration 2개 = 83개 PASS**로 기록되어 있다. native/guest peer의 `-O2 -Wall -Wextra -Werror` 컴파일도 통과했다. 이 수치는 앞선 17개 테스트와 별도로 더해 총합을 만들지 않는다.

## 4. 발견한 문제와 수정

| 문제 | 조치·결과 |
| --- | --- |
| 짧은 ARM64 TCP 시험에서 통신은 성공했지만 pcap에 0개 저장 | 첫 실행을 FAIL로 보존하고 tcpdump에 `--immediate-mode` 추가. 재실행에서 411개 캡처 |
| RENEGING 중 목표 구간보다 앞선 재전송이 먼저 도착 | 범위·payload를 검증해 로그에 남기고 목표 재전송까지 확인하도록 수정 |
| SACK 협상이나 TCP_INFO만으로 내부 처리 경로를 판정할 위험 | 패킷 동작과 내부 경로 관측을 별도 판정하며 미관측은 NOT RUN으로 유지 |
| QEMU 초기 reset 시험의 동기화 누락·cross libc 헤더 누락 | 연결 확인 절차와 의존성을 보완한 뒤 통과 |
| QEMU 패킷 peer의 비-IPv4 패킷 처리 실패 | 시험용 TUN의 IPv6 자동 설정을 끄고 재실행하여 통과 |

실패 실행도 성공 실행과 함께 보존했다. 시험 도구의 수정과 production TCP 코드의 변경은 구분해 기록했다.

## 5. 주요 산출물

경로는 `rtx-project/` 기준이다.

| 파일·디렉터리 | 역할 |
| --- | --- |
| `scripts/capture_env.sh` | 실행 환경과 커널 설정 수집 |
| `scripts/prepare_v0_arm64.sh`, `prepare_v1_arm64.sh`, `lib/prepare_arm64.sh` | 고정 V0·V1 소스와 설정 검증·준비 |
| `scripts/build_arm64.py` | 전체 빌드·산출물 검증·증거 보존 |
| `scripts/run_arm64_tcp.py` | namespace/veth 기반 무결성·종료·손실 시험 |
| `scripts/run_arm64_packets.py` | SACK·부분 ACK·DSACK·reneging packet peer 실행 |
| `scripts/inspect_arm64_trace.py`, `verify_arm64_trim_layout.py` | 심볼·추적 가능 여부와 ARM64 필드 위치 확인 |
| `scripts/run_b_runtime.sh`, `run_b_integration.sh` | QEMU 기본 통신·분할 및 실제 커널 fixture 실행 |
| `tests/`, `tests/runtime/`, `tests/integration/` | 시험 도구·판정기·패킷 peer·커널 fixture |
| `docs/pr/d-arm64-v1-validation.md`, `docs/arm64-c-review.md` | ARM64 실행 근거와 C 리뷰 반영·관측 계약 |
| `docs/B_RUNTIME_VALIDATION.md`, `docs/B_SPLIT_*_VALIDATION.md` | B의 부팅·분할·실패 복구·경계·불변식 검증 기록 |

## 6. 남은 작업과 다음 순서

- [ ] **ARM64 V0·V1 설치 및 실제 부팅:** 모듈·커널 이미지·initramfs·GRUB와 기존 커널 복구 항목을 확인하고 UTM에서 부팅한다.
- [ ] **부팅한 각 커널의 정체성 확인:** release, 설치 이미지·설정 hash, 부팅 로그를 해당 빌드와 대조한다.
- [ ] **동일 통신 시나리오 실행:** V0·V1 각각에서 16건 통신 시험과 5개 packet 시나리오를 실행한다.
- [ ] **부분 ACK의 실제 trim 관측:** entry/return·ACK 호출 경로·대상 연결·GSO 및 잔여 skb 상태를 확인한다.
- [ ] **C 계측 통합 후 내부 검증:** DSACK 순회와 ACK/shift 카운터를 확인한다. 새 커널 기준 SHA를 별도로 고정한다.
- [ ] **확장 회귀:** IPv6, 실제 NIC/offload, 다중 CPU·타이머 경합, allocator 내부 실패, 실제 네트워크 wrap 등 미검증 조합을 다룬다.
- [ ] **후속 자료구조 구현:** 캐시, tail 삽입 최적화, LIST·승격 및 merge·trim·replace 계약을 구현·검증한다.
- [ ] **성능 측정:** 정확성 검증을 통과한 비교군에서 DEBUG/STATS 및 추적을 끈 별도 실행으로 goodput·CPU time/byte·지연을 반복 측정한다.

현재 VM은 기존 커널로 실행 중이며, 확인한 `/boot`에는 V0·V1 release의 `vmlinuz`, `config`, `initrd.img`가 없다. 현재 결과로 ARM64 통합 커널의 실행 정확성이나 성능 향상을 주장하지 않는다.

## 7. 근거와 보존 위치

다음 D 결과는 이번 정리에서 로컬 상태 파일·요약 JSON을 직접 읽어 대조했다. B의 QEMU 실행 결과와 83개 자동 테스트 통과는 저장소의 기존 검증 문서에 기록된 결과를 인용했다.

| 근거 | 로컬 경로 (`rtx-project/` 기준) |
| --- | --- |
| V0 전체 빌드 | `.work/build-v0/validation-davvdlkt/status.json` |
| V1 전체 빌드 | `.work/build-v1/validation-gszehhr3/status.json` |
| ARM64 TCP 16건 | `.work/d-tcp-kspa5nnt/summary.json` |
| 패킷 시나리오 5건 | `.work/d-packets-y9ch71ud/summary.json` |
| 저장 로그의 shift MIB 재검증 | `.work/d-packets-y9ch71ud/review-validation.json` |
| V0 trim 필드 위치 | `.work/d-trim-layout-5sifmkix/layout.json` |
| V1 trim 필드 위치 | `.work/d-trim-layout-1j6nles6/layout.json` |

상세 설명은 `docs/work-summary-2026-11-06.md`, `docs/A_CORE_IMPLEMENTATION.md`, `docs/B_DOMAIN_IMPLEMENTATION.md`, `docs/B_RUNTIME_VALIDATION.md`, `docs/c-callsite-audit.md`, `docs/pr/d-arm64-v1-validation.md`, `docs/arm64-c-review.md`를 참조한다.

`.work/`의 원시 로그와 커널 산출물은 Git 제외 대상이다. 이 Markdown을 Notion으로 옮겨도 원본 파일은 함께 전달되지 않으므로, 팀 공유 시 필요한 요약 JSON·로그·체크섬을 별도로 첨부한다.
