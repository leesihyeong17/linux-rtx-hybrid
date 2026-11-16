# C: ACK/SACK 호출부 카운터 연결과 SACK 검색 경계 sweep

PR: 생성 전 (만든 뒤 링크 갱신). 브랜치 `c/stats-v2`.
기준: main `73747d3f1` (#17 LIST 승격 통합 머지 직후).
관련: #19 C 리뷰 요청(카운터 연결 위치·부분 ACK trim 관측), #15 매트릭스의 "TREE→LIST를 가로지르는 SACK"·"경계·구멍·wrap" 항목 일부.

## 1. 문제와 범위

#12에서 A가 `CONFIG_TCP_RTX_STATS`와 C용 카운터 9개(`ack_*`, `sack_*`, `shift_*`)를 정의했다. 하지만 증가시키는 코드가 없어서 `/proc/rtx_stats`에서 이 값들은 항상 0이었다. D의 #19도 이 점을 리뷰 요청으로 남겼다. 또 SACK 검색(`rtx_find_cover`)은 A의 자체 테스트가 몇 개 지점만 확인하고 있었다. 하이브리드에서 TREE/LIST 경계를 포함한 모든 seq를 비교한 적은 없었다.

완료 조건:

- 9개 카운터가 의도한 사건에서만 증가한다. shift 3종은 리눅스 기존 MIB(`TCPSackShifted`·`TCPSackMerged`·`TCPSackShiftFallback`)와 실제 실행에서 같은 값이어야 한다.
- STATS를 끈 빌드에서는 기계어가 main과 같다.
- `rtx_find_cover`가 큐 앞뒤와 모든 경계·구멍·wrap에서 단순 순회 결과와 같다. 비교기가 틀린 답을 실제로 걸러 내는지도 보인다.

포함: `net/ipv4/tcp_input.c` 카운터 9곳, `net/ipv4/tcp_rtx_selftest.c` sweep 함수 1개.
제외: 성능 측정·K 튜닝(#16), #15의 split 관련 행, ARM64·gcc 9.4 실행(D 환경), `packet_peer.c` 수정(D의 #19 소관. 아래 6절의 제안만 전달).

## 2. 최종 변경

### 2.1 카운터 (`d1211ee77`, tcp_input.c +13/−1)

| 카운터 | 위치 (main `73747d3f1` + 이 PR 기준 행) | 세는 것 |
| --- | --- | --- |
| `shift_partial` | 1337, `tcp_shifted_skb` 부분 shift | `LINUX_MIB_SACKSHIFTED` 바로 옆 |
| `shift_merge` | 1369, `tcp_shifted_skb` 완전 병합 | `LINUX_MIB_SACKMERGED` 바로 옆 |
| `shift_fallback` | 1539, `tcp_shift_skb_data` fallback | `LINUX_MIB_SACKSHIFTFALLBACK` 바로 옆 |
| `sack_walk_skb` | 1560, `tcp_sacktag_walk` | 블록 범위 안에서 본 skb 1개 |
| `sack_skip_reuse` | 1621, `tcp_sacktag_skip` | 커서가 이미 목표 뒤라 검색을 건너뜀 |
| `sack_block` | 1768, `tcp_sacktag_write_queue` 블록 루프 | 검증을 통과한 SACK 블록 1개 |
| `ack_clean` | 3107, `tcp_clean_rtx_queue` | 호출 1회 |
| `ack_trim` | 3126, `tcp_tso_acked()`가 0보다 큰 값을 돌려줌 | 부분 누적 ACK로 머리 trim 1회 |
| `ack_unlink` | 3188, `tcp_rtx_queue_unlink_and_free` 다음 | 누적 ACK로 통째 제거된 skb 1개 |

`tcp_sacktag_skip`은 카운터를 넣으려고 `if (...) return skb;` 한 줄을 중괄호 블록으로 바꿨다. 이것이 유일한 삭제 행이다. 동작은 같다.

구조 이벤트(삽입·삭제·승격·`find_cover` 방문 노드)는 기존처럼 A의 어댑터 안에서 센다. C는 "ACK/SACK 처리가 왜 순회했는가"만 센다 (11/11 분담 합의).

### 2.2 sweep 자체 테스트 (`a4867812c`, tcp_rtx_selftest.c +89)

`st_find_cover_sweep(base)`를 base 3가지로 부른다: 1000, 큐 안에서 wrap(`0xfffffd00`), 첫 skb가 wrap을 가로지름(`0xffffffe0`).

- skb 12개, 길이 40/60/80/100 반복, 세 개마다 30바이트 구멍. K=4이면 TREE 8개 + LIST 4개.
- 3단계로 확인한다: 전체 큐 → 앞 2개 ACK 제거 → (하이브리드) TREE 마지막 노드 제거. 각 단계에서 첫 seq−3부터 끝 end_seq+3까지 모든 seq에 대해 `__rtx_find_cover()`와 단순 순회(`st_cover_ref`)를 비교한다.
- 음성 대조: 한 칸 어긋난 기준(`seq+1`)과는 반드시 어딘가에서 달라야 한다. 비교기가 항상 "같다"고만 하지 않는지 확인하는 장치다.
- 방문 노드 수(평균·최대)를 로그로 남긴다. 큐가 12개뿐이므로 성능 근거가 아니다.

`CONFIG_TCP_RTX_DEBUG`에서만 빌드되는 A의 파일에 함수를 추가한다. A 리뷰가 필요하다.

### 2.3 도메인 영향

공개 API, 구조, TCP 회계, 힌트는 바꾸지 않는다. B·A 코드 경로도 바뀌지 않는다. STATS off에서는 `rtx_stat_inc`가 빈 inline이다.

## 3. 대안과 트레이드오프

| 선택 | 고른 것과 이유 | 대가 |
| --- | --- | --- |
| `ack_trim`을 `tcp_trim_head()` 안에서 셀지, 호출부에서 셀지 | **호출부**(`tcp_tso_acked` 반환 뒤). `tcp_trim_head`는 B의 `tcp_output.c` 재전송 경로(2939행)에서도 불려서 안에서 세면 ACK와 재전송이 섞인다 | `tcp_tso_acked` 안의 `tcp_trim_head` 실패(ENOMEM)로 0을 돌려준 경우는 세지 않는다. 의도한 정의다 |
| shift 카운터를 새 위치에 둘지, MIB 옆에 둘지 | **MIB 바로 옆**. 리눅스가 이미 세는 사건과 1:1이므로 실행에서 MIB와 대조해 정확성을 확인할 수 있다 | MIB와 정보가 겹친다. 대신 `/proc/rtx_stats` 하나에서 구조 카운터와 같은 리셋 주기로 볼 수 있다 |
| `sack_block` 위치 | 검증·정렬 뒤 실제 처리 루프. 거부된 블록(DSACK·범위 밖)은 세지 않는다 | 수신 블록 수와 다를 수 있다. DSACK은 MIB `TCPDSACKRecv`로 본다 |
| sweep을 사용자 공간 모델로 할지, 커널 자체 테스트로 할지 | **커널 자체 테스트**. 실제 `__rtx_find_cover`, 실제 승격·캐시를 그대로 쓴다. 헤더를 사용자 공간으로 옮기는 별도 모델은 유지 비용이 크다 | DEBUG 빌드와 부팅이 필요하다 |
| ns/op 마이크로벤치 | **이번에는 안 함**. 이 환경은 KVM이 없어 QEMU TCG로만 돈다. 시간 값이 실제 CPU와 무관하다. 대신 환경과 무관한 방문 노드 수를 남겼다 | 시간 측정은 D의 실기 측정(#16)으로 넘긴다 |

## 4. 검증 방법과 결과

환경: x86_64 컨테이너, gcc 13.3.0, QEMU TCG(`-smp 1 -m 512`). 팀 표준 gcc 9.4·ARM64에서는 실행하지 않았다.

| 검증 | 설정 | 결과 |
| --- | --- | --- |
| `git diff --check`, `checkpatch.pl --no-tree` | 두 커밋 | 0 error, 0 warning |
| 기계어 비교 (`objdump -d`, 주소 정규화) | `rtx_defconfig`, STATS·DEBUG off. RB / HYBRID 각각 | `tcp_input.o`·`tcp_output.o`·`tcp.o` 모두 main과 **동일** |
| 부팅 커널 빌드 | x86_64_defconfig + STATS·DEBUG·KPROBES. RB / HYBRID(K=4) | 둘 다 성공. `net/ipv4`·`include/net` 경고 0 (gcc 13 + 5.4 커널의 기존 경고만 있음) |
| 자체 테스트 | RB / HYBRID K=4 | **556 passed, 0 failed** / **689 passed, 0 failed** |
| sweep | base 3종 × 2608 조회 | 불일치 0, 음성 대조 차이 44 (3종 모두). 방문 노드 RB 평균 2.97·최대 5, HYB 평균 2.59·최대 4 |
| 실제 패킷 시나리오 5종 | RB / HYBRID | 모두 exit 0, `WARNING`·`BUG`·`Oops` 0 |
| shift 카운터 = MIB | 5종 × 3쌍 × 2커널 = 30쌍 | **30/30 일치** |

### 4.1 시나리오별 카운터 (HYBRID K=4, 괄호는 같은 시나리오의 RB 값이 다를 때)

| 시나리오 | 확인한 경로 | sack_block / walk_skb | shift partial·merge·fallback | ack clean / unlink / trim | 구조 |
| --- | --- | --- | --- | --- | --- |
| SACK | 구멍 위 SACK → RACK 재전송 (`lost=1 retrans=1 ca_state=3`) | 1 / 2 | 0·0·2 | 4 / 5 / 0 | find_cover 1회 LIST 1단계 (RB: TREE 1단계) |
| PARTIAL | 4000바이트 중 2000 누적 ACK → 머리 trim | 0 / 0 | 0·0·0 | 4 / 4 / **1** | kprobe: `tcp_trim_head len=2000`, trim 전 범위 4000·segs=4 |
| DSACK | 중복 구간 DSACK | 0 / 0 | 0·0·0 | 3 / 3 / 0 | `TCPDSACKRecv +1`. C의 SACK 순회는 타지 않음 |
| RENEGING | SACK 후 철회 → 재전송 | 1 / 2 | 0·0·2 | 5 / 5 / 0 | `TCPSACKReneging +1` |
| SHIFT_BOUNDARY | 아래 4.2 | 3 / 5 | 0·**4**·1 | 6 / 6 / 0 | promote 4, unlink tree 5·list 5 (RB: promote 0, tree 10) |

PARTIAL의 trim은 TLP(`TCPLossProbes +1`)가 함께 일어난 실행이다. trim 자체는 kprobe 스택(`tcp_trim_head ← tcp_ack ← tcp_rcv_established`)으로 누적 ACK 경로임을 확인했다.

### 4.2 TREE 끝 + LIST 첫 노드 병합 (#17 사후 확인)

`c_stats_init.c`의 `c_shift_case`:

1. `TCP_NODELAY`와 autocork off 상태에서 1000바이트 skb 8개를 따로 보낸다 (각 패킷의 seq·길이를 확인).
2. SACK `[3000,4000)` → `[3000,5000)` → `[3000,8000)`을 차례로 보낸 뒤 전체 ACK, FIN으로 닫는다.

K=4이면 0~3번이 TREE, 4~7번이 LIST다. 두 번째 SACK에서 LIST 첫 노드(4번)가 TREE 마지막(3번)에 합쳐진다. #17이 `rtx_shift_prepare`로 막으려던 바로 그 경우다.

관찰:

- `promote=4`: 0~3번이 TREE로 갔다.
- `shift_merge=4` = `TCPSackMerged +4`. `unlink_list=5` = 병합으로 지운 LIST 노드 4개(4~7번) + FIN 1개. `unlink_tree=5` = SYN 1개 + 누적 ACK로 지운 0~3번 4개. SACK 시나리오의 `unlink_tree=1`(SYN)과도 맞는다.
- `sacked` 1 → 2 → 5.
- DEBUG 구조 검사 WARN 0, 이후 ACK·종료 정상.

병합이 4→3 순서였다는 것은 카운터와 seq 배치에서 추론한 것이다. 병합 순간을 직접 추적하지는 않았다 (`rtx_shift_commit`은 inline이라 kprobe를 걸 수 없음).

### 4.3 재현

```sh
# 기계어 비교: main과 이 브랜치를 같은 .config로 각각 빌드한 뒤
make O=<out> net/ipv4/tcp_input.o net/ipv4/tcp_output.o net/ipv4/tcp.o
# objdump -d 결과에서 주소를 정규화해 diff (결과 파일: .work/.../machine-code/)
# 부팅 (init·initramfs는 .work/src)
qemu-system-x86_64 -accel tcg -m 512 -smp 1 -kernel bzImage-<v> -initrd initramfs.cpio \
  -append 'console=ttyS0 panic=1 oops=panic rdinit=/init' -nographic -no-reboot -nic none
python3 summarize.py serial-<v>.log
```

## 5. 증거

로컬 전용(git 무시). PR에는 아래 스크린샷 두 장만 첨부할 예정이다.
위치: `rtx-project/.work/c-stats-sweep-20261116/` (C 담당자 작업 환경).

| 주장 | 파일 |
| --- | --- |
| 실행 원본 | `logs/serial-rb.log`, `logs/serial-hyb.log` (sha256 `7460fe7f…` = hyb) |
| 카운터 = MIB, 시나리오별 값 | `summary-rb.txt`, `summary-hyb.txt`, `src/summarize.py` |
| 실행 화면 (원본 로그 기반 보고서, 콘솔 캡처 아님. 행 번호·hash 표시) | `screenshots/rb-report.png`, `screenshots/hyb-report.png` |
| STATS off 기계어 동일 | `machine-code/*.dis` |
| 설정·빌드 | `configs/*.config`, `logs/build-*.log` |
| 시험 init·peer | `src/c_stats_init.c`, `src/packet_peer.c` (D의 파일 로컬 수정본), `src/initramfs.cpio` |
| 커널 이미지 | `bzImage-rb`, `bzImage-hyb` |
| 체크섬 | `SHA256SUMS` (36개 파일) |

## 6. 실패와 수정

| 문제 | 원인 | 처리 |
| --- | --- | --- |
| D의 RENEGING 시나리오가 실패 (`RTX_PACKET FAIL reneged retransmit range`, `logs/serial-reneging-original-fail.log` 444행. #17 이전 main + 카운터 6개, 11/13 빌드 커널) | 커널 문제가 아니다. SACK 직후 RACK이 보낸 offset 0 재전송(`C_SACK_POINT lost=1 retrans=1`)이 tun에 남아 있었다. peer는 reneging 뒤 처음 읽은 데이터 패킷을 그 재전송으로 판정해서 offset 0을 받고 실패했다 (수정 후 로그: `C_RENEG_DATA offset=0` 다음 `offset=1000`) | 로컬 사본에서 offset<1000 패킷을 건너뛰게 고쳐 재실행하니 통과했다 (`serial-reneging-fixed-6counter.log`, 이번 두 커널에서도 통과). `packet_peer.c`는 D 소유라 이 PR에 넣지 않고 #19에 수정 제안으로 전달한다 |
| 첫 카운터 커밋(`37f4f8c51`, 미push)의 메시지가 `[c] rtx:` 형식 | AGENTS.md 이전 관례 | 새 main 위에 `feat:` 형식으로 다시 만들었다. 옛 브랜치는 push하지 않는다 |
| sweep 첫 초안에서 checkpatch 경고 3건 | 블록 주석 형식, 80열 초과 | 고친 뒤 0건 |

이 PR의 커널 코드 자체에서 실패한 실행은 없다.

## 7. 남은 작업

- **A 리뷰**: A 소유 `tcp_rtx_selftest.c`에 함수 추가. 카운터 의미가 #12의 enum 주석과 맞는지 확인.
- **D 확인**: ARM64·gcc 9.4에서 같은 카운터 출력. #19의 native 시나리오에 카운터 대조(`shift_*` = MIB)를 판정기에 넣을지 결정.
- **#15 남은 행**: split과 SACK가 겹치는 경우, highest_sack·lost/retransmit hint 경계, DSACK+재정렬 조합. 이번에 확인한 것은 "TREE→LIST SACK 병합"과 "경계·구멍·wrap 검색"뿐이다.
- **ns/op 측정**: 실기(KVM 또는 ARM64 VM)에서 D와 함께 진행 (#16).
- partial shift가 LIST 첫 노드에서 일어나는 경우는 실행하지 못했다 (`shift_partial=0`). GSO 다중 세그먼트 skb가 필요하다.
