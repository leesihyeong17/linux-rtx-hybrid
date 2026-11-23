# C 리뷰 반영 — D ARM64 검증

2026-11-13 KST. 커널 기준 SHA는 [기존 검증 기록](pr/d-arm64-v1-validation.md)대로 고정한다.
이번 변경은 시험 도구·판정기·관측 준비이며 C 소유의 커널 카운터 증가는 추가하지 않는다.

## 반영한 시나리오

| 항목 | 변경 및 판정 |
| --- | --- |
| SACK | 중간 MSS SACK 뒤 `tcpi_unacked=4`, `tcpi_sacked=1` 확인. `tcpi_lost`, `tcpi_retrans`도 출력 |
| RENEGING | offset < 1000인 재전송은 범위·payload를 검증하고 로그에 남긴 후 건너뜀. 이후 offset=1000 데이터와 `tcpi_unacked=3`, `tcpi_sacked=0` 확인. `TCPSACKReneging` 증가량은 정확히 1 |
| PARTIAL | 이 시나리오의 전용 namespace에서 `tcp_early_retrans=0`으로 TLP 비활성화. unacked=2는 패킷 회계만 의미하며 단일 skb의 trim 증거로 사용하지 않음 |
| DSACK | 기존 최종 ACK 아래의 블록은 유지. `TCPDSACKRecv` 증가량 1 확인 |
| DSACK_OFO | 데이터 4000바이트가 outstanding인 상태에서 ACK=base, `[base+1000,base+2000)`와 `[base+1000,base+3000)` 두 블록 전송. unacked=4, sacked=2, `TCPDSACKOfoRecv` 증가량 1 확인 |

MIB before/after는 새로 만든 동일 network namespace에서 수집한다. 이 실행에는
위 DSACK 두 종류와 reneging을 각각 한 번만 보낸다. 누락·감소·예상 외 추가 증가는
성공으로 허용하지 않는다. shift MIB는 증가가 0이어도 유효한 관측이며 경로 성공을
뜻하지 않는다.

DSACK_OFO는 `packets_out=0` 조기 종료를 피하는 입력과 처리 후 회계를 검증한다.
`TCPDSACKOfoRecv` 자체는 순회 전 감지 지점의 카운터이므로, **이 값만으로 C의
`tcp_sacktag_walk` 내부 순회 검증까지 완료했다고 표시하지 않는다.** 내부 순회는
C 카운터 또는 해당 연결·시나리오에 대응하는 별도 추적 증거가 필요하다.

## 실제 실행 근거

사용자가 VM에서 `run_arm64_packets.py --smoke-current-kernel`을 실행했다.
결과 디렉터리는 `.work/d-packets-y9ch71ud`이고 실행 커널은
`5.4.214-kimseungbeom+`이다. V0/V1 부팅 검증 결과가 아니다.

- peer 종료 코드 0, 패킷 시나리오 5개 PASS, 새 커널 오류 0, cleanup 오류 0.
- SACK 시 `lost=1 retrans=1`을 실제 관측했다.
- RENEGING은 `skipped_offset=0 bytes=1000` 1건을 기록한 뒤
  offset=1000 재전송을 확인했다. 회복 시 `unacked=3 sacked=0 lost=3 retrans=1`.
- PARTIAL은 TLP off, `unacked=2 sacked=0 lost=0`, 최종 `total_retrans=0`.
- DSACK_OFO는 `unacked=4 sacked=2 lost=1 retrans=1`.
- `TCPDSACKRecv`, `TCPDSACKOfoRecv`, `TCPSACKReneging` 증가량은 각각 1.
- 원시 `netstat.json`을 새 판정기로 재검증한 shift 증가량:
  `TCPSackShifted=1`, `TCPSackMerged=0`, `TCPSackShiftFallback=6`.
  실행 당시 summary에는 없던 shift 요약은 별도 `review-validation.json`에 보존했다.

peer C 소스 SHA-256:
`7c900232a727c2365ed4fe7241795d69fc691532e16a36c5204952a61bab9ef6`.
native 진입점 SHA-256:
`eb67d075f778ae0fd89955a0c396092edfdc67a43ae468218762f35227b8753e`.

이 로그는 실제 RACK 선행 재전송을 처리한 근거다. 단일 GSO skb trim, 내부 DSACK
순회 및 V0/V1 성능 비교는 아직 NOT RUN이다.

수정 후 자동 회귀 테스트는 최상위 66개 + runtime 15개 + integration 2개,
총 **83개 PASS**다. 실제 loopback 송수신도 포함한다. native/guest peer 모두
GCC의 `-O2 -Wall -Wextra -Werror` 컴파일을 통과했다. 신규 wire 검사는 실제 C
인코더가 생성한 두 SACK 블록, TCP 옵션 길이, IP/TCP 체크섬 및 seq wrap-around를
검사한다. 이 자동 테스트 통과를 커널 trim 경로 검증으로 대체하지 않는다.

## ARM64 trim 필드 위치 검증

`verify_arm64_trim_layout.py`는 완성된 고정 소스/설정/vmlinux의 일치 여부를 확인하고,
해당 Kbuild 설정으로 `arm64_trim_offsets.c`를 어셈블리까지만 컴파일한다.
`offsetof`와 `sizeof`를 컴파일러가 내보낸 결과로 probe 정의를 생성한다.
프로브 등록·모듈 로드·커널 설치·재부팅은 수행하지 않는다.

```bash
python3 rtx-project/scripts/verify_arm64_trim_layout.py \
  --variant v0 --source rtx-project/.work/v0 --build-output rtx-project/.work/build-v0
python3 rtx-project/scripts/verify_arm64_trim_layout.py \
  --variant v1 --source rtx-project/.work/v1 --build-output rtx-project/.work/build-v1
```

| 값 | V0 | V1 |
| --- | --- | --- |
| `offsetof(struct sk_buff, cb)` | 40 | 40 |
| cb 내부 `seq / end_seq / tcp_gso_segs` | 0 / 4 / 8 | 0 / 4 / 8 |
| 각 필드 크기(byte) | 4 / 4 / 2 | 4 / 4 / 2 |
| 결과 디렉터리 | `d-trim-layout-5sifmkix` | `d-trim-layout-1j6nles6` |
| 어셈블리 SHA-256 | `0c424a1c132371013496480e08f50dc2a489a274ee5c0609e3d597b5847fc7a4` | `0e85dac6db859776e4dc09a3da09a69af2f5b21cebfe95a2e59f760be285c8df` |

두 빌드 모두 PASS, 검사 전후 설정과 vmlinux hash가 동일하다. 생성한 정의는 다음과 같다.
이는 **해당 빌드에 대한 정의**이며 다른 실행 커널에 그대로 적용하지 않는다.

```text
p:rtx_trim tcp_trim_head len=%x2:u32 seq=+40(%x1):u32 end_seq=+44(%x1):u32 segs=+48(%x1):u16
r:rtx_trim_ret tcp_trim_head ret=$retval:s32
```

## 실제 trim 관측 시 판정 계약

아래는 V0/V1 부팅 후 수행할 진단 절차다. 필드 위치 확인을 런타임 관측 완료로
취급하지 않는다. 현재 자동 packet runner는 trim을 NOT RUN으로 기록한다.

1. 실행 커널 release 및 설치 이미지/config를 해당 빌드와 대조한다. 전용 trace
   instance와 고유 probe 이름을 사용한다. 기존 전역 probe 정의를 덮어쓰지 않는다.
2. entry에 stacktrace를 함께 수집한다. PARTIAL ACK 직전/직후 구간을 trace marker로
   명시하고 `RTX_FLOW PARTIAL base=...`와 연결을 대조한다. 같은 구간의 다른 소켓
   이벤트를 포함하지 않도록 연결 식별 정보도 보존한다.
3. 해당 PARTIAL 연결에서 entry **정확히 1회**,
   `seq=base`, `end_seq=(base+4000) mod 2^32`, `segs=4`, `len=2000`을 확인한다.
   대응하는 return은 `ret=0`, stacktrace는 `tcp_ack` 경로여야 한다.
   `tcp_output.c`의 재전송 trim은 제외한다. entry/return 대응이 불명확하면 통과시키지 않는다.
4. trace buffer overrun/drop과 `kprobe_profile` missed-hit를 확인한다.
   PARTIAL 구간·필터·프로브 활성 상태·손실 없음이 확인된 상태에서 entry가 0이면
   **NOT_ENTERED: trim 미진입(skb 분리 전송)**으로 별도 표시한다. 시험 실패나 trim
   검증 PASS로 계산하지 않는다. 기록 누락·프로브 미활성·trace 손실은 INCONCLUSIVE이며
   이를 trim 미진입으로 바꾸지 않는다. 관측한 필드·횟수·반환값이 틀리면 FAIL이다.
5. entry/return/호출자만 충족하면 trim 진입·성공의 근거다. 잔여 skb까지 PASS로
   올리려면 **같은 부분 ACK**의 `ack_trim=1`, `ack_unlink=0` 및
   `tcpi_unacked=2`를 함께 확인해야 한다. 시나리오 전체 구간은 마지막 전체 ACK를
   포함하므로 이 카운터 판정에 사용할 수 없다.
6. 자신이 만든 probe/instance만 해제·제거하고 정리 결과를 기록한다.
   추적이 없는 별도 실행에서 성능을 측정한다.

## C 카운터와 후속 작업 분담

| 카운터 | C가 넣을 증가 기준 | D가 검증할 내용 |
| --- | --- | --- |
| `ack_clean` | `tcp_clean_rtx_queue` 호출 1회 | ACK별 호출 수 |
| `ack_unlink` | 누적 ACK로 skb 통째 제거 1개 | PARTIAL 대상 ACK에서 0 |
| `ack_trim` | 호출부에서 `tcp_tso_acked` 반환값 > 0 | PARTIAL 대상 ACK에서 1; 재전송 trim 제외 |
| `sack_block` | 검증 통과 후 처리한 SACK 블록 1개 | 블록 입력 및 처리 수 대조 |
| `sack_skip_reuse` | `tcp_sacktag_skip`의 커서 재사용 | 재사용 경로의 별도 증거 |
| `sack_walk_skb` | 범위 검사 통과 후 살펴본 skb 1개 | DSACK_OFO 구간 순회 관측; 단순 함수 진입 수와 구분 |
| `shift_partial` | 기존 `LINUX_MIB_SACKSHIFTED` 자리 | `TCPSackShifted`와 같은 증가량 |
| `shift_merge` | 기존 `LINUX_MIB_SACKMERGED` 자리 | `TCPSackMerged`와 같은 증가량 |
| `shift_fallback` | 기존 `LINUX_MIB_SACKSHIFTFALLBACK` 자리 | `TCPSackShiftFallback`와 같은 증가량 |

`/proc/net/netstat`의 실제 열 이름은 `TCPSackShifted`, `TCPSackMerged`,
`TCPSackShiftFallback`이다. V0/V1에서 STATS를 켜지 않고도 수집·비교할 수 있다.
단, 현재 1/0/6은 기존 커널의 1회 실행 값이며 V0/V1의 고정 기대값이 아니다.

C의 shift 계측은 #17 머지 후 진행한다. 해당 변경을 포함한 커널을 검증할 때는
새 기준 SHA를 별도로 기록해야 하며 기존 고정 V1 결과에 합치지 않는다.

현재 `/proc/rtx_stats`는 **VM 전체 CPU의 전역 카운터**다. network namespace만
분리해도 SSH 등 다른 연결의 증가는 섞인다. 따라서 전용 namespace의 MIB와
전역 STATS의 원시 증가량이 항상 같다고 단정할 수 없다. C의 증가 위치는 같은
이벤트 기준으로 맞추되, D의 정확한 일치 검증에는 해당 ACK/연결로 좁힌 진단
관측 또는 다른 TCP 트래픽이 배제된 VM 실행이 필요하다. 전역 카운터를 임의로
초기화하거나 아직 증가 코드가 없는 0을 경로 미실행으로 판정하지 않는다.

진행 순서는 **수정 하네스 확인(완료) → V0/V1 부팅·동일 시나리오 실행 → trim
entry/return/호출자 관측 → C 계측 통합 후 잔여 skb·DSACK 순회·shift 일치 검증**이다.
