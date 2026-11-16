# #15 C 도메인 SACK shift·누적 ACK·힌트·DSACK 공동 회귀

이슈: https://github.com/leesihyeong17/linux-rtx-hybrid/issues/15
PR: 생성 전. 브랜치 `c/sack-ack-regression`.
기반: B의 `test/b-sack-boundary-regression`(#20, `0ee4a1a71`). 이 PR은 B의 overlay·검사기를 그대로 쓰므로 #20 위에 쌓는다.
실행 기준: 위 브랜치에 main `cc843d480`과 C 카운터 PR #21(`8bab2171d`까지)을 합친 트리. fixture가 #21의 shift·ACK 카운터를 읽기 때문이다.

## 1. 문제와 범위

B는 #20에서 SACK 분할 경로를 검증했지만, 선형 skb fixture라 shift·merge 성공 경로를 탈 수 없었다. 실제 누적 ACK 정리, 힌트의 의미, DSACK 조합도 C에 남겼다(#20 대표 문서 매트릭스, B 요청 4항목).

완료 조건:

1. page fragment skb로 실제 partial shift·full merge를 실행한다. TREE 마지막/LIST 첫 노드 경계와 "현재+다음 skb 동시 병합"을 포함한다. 반환 cursor로 순회가 이어지고, 누락·중복 방문이나 해제된 노드 접근이 없어야 한다.
2. 실제 `tcp_ack()`로 부분 trim과 완전 제거를 실행한다. `packets_out`·`sacked_out`·`lost_out`·`retrans_out`·wmem·tsorted·큐 캐시를 검사한다.
3. `highest_sack`·`lost_skb_hint`·`lost_cnt_hint`·`retransmit_skb_hint`가 split·shift·삭제 뒤에도 의미대로 유지되는지 확인한다.
4. DSACK·재정렬·재전송 조합과 두 번째 split 실패 뒤의 ACK 처리를, 실제 ACK/SACK 옵션 처리를 거쳐 시험한다.
5. 실제 경로 도달을 계측으로 보이고, K=16·K=1 결과, 음성 대조, 원본 로그, 남은 미검증 범위를 남긴다. 결함이 나오면 재현 테스트와 최소 수정을 남긴다.

포함: `rtx-project/tests/integration/`의 C fixture·검사기와 overlay 확장. production 코드(`net/ipv4/`, `include/net/`)는 바꾸지 않는다.
제외: 송신·재전송 재진입 의미 판정(B와 공동으로 확인 예정), 성능, IPv6, 다중 CPU·타이머 경합, ARM64·gcc 9.4.

## 2. 최종 변경

| 파일 | 내용 |
| --- | --- |
| `c_common.h` (신규) | page fragment skb 생성(`c_add`). 실제 TCP 헤더와 SACK 옵션을 가진 ACK skb를 만들어 `tcp_ack()`에 전달(`c_ack`). 힌트 의미 오라클(`c_hints`), payload 패턴 검사(`c_payload`), 상태 덤프(`CSTATE`/`CDOMAIN`). shift 카운터와 MIB 동시 대조(`c_expect_shift`) |
| `case_cshift.c` (신규) | 항목 1. 경계 partial→full(`c_shift_boundary`), 현재+다음 동시 병합 뒤 순회 계속(`c_shift_pair`, cur=1~4) |
| `case_cack.c` (신규) | 항목 2~4. 부분·누적 ACK(`cleanup`), 손실 표시·재전송·DSACK(`recovery`), 재정렬(`reorder`), 두 번째 split 실패 뒤 ACK(`split_failure`), 오라클 음성 대조(`negative`) |
| `c_diff_states.py`, `test_c_diff_states.py` (신규) | 하이브리드 로그와 RB 참조 로그의 `CSTATE` 비교와 그 단위 테스트(불일치·누락 거부 포함) |
| `apply_overlay.py` | `case_c*`가 있으면 `c_common.h`를 함께 복사하고, `tcp_input.c`에 `c_test_ack()` wrapper를 붙인다 (`tcp_mstamp_refresh` 뒤 `tcp_ack(sk, skb, FLAG_SLOWPATH)`) |

fixture 소켓은 B의 `b_socket()`으로 만든다. 그 위에 연결된 소켓이 갖는 최소 상태를 채운다.

- MSS 1000, SACK 허용, wscale 2, cwnd 256
- `icsk_ack.rcv_mss`와 수신 창
- `sk_gso_max_segs`/`size`
- 혼잡 제어 초기화
- 송신은 `queue_xmit`·`rebuild_header` stub이 clone을 해제하고 성공을 돌려준다. 그래서 재전송 경로(`tcp_xmit_retransmit_queue` → `__tcp_retransmit_skb` → 필요 시 `tcp_fragment`)가 실제로 돈다.

모드는 두 가지다.

- `C_NOLOSS`: RACK on, reordering 127. 손실 표시 없이 SACK·shift·ACK만 본다.
- `C_CLASSIC`: RACK off, reordering 3. dupthresh로 `tcp_mark_head_lost()`가 돌아 lost 힌트를 만든다.

RB 커널은 같은 시나리오를 k16·k1 두 모양으로 모두 실행한다. 그래서 하이브리드 K=16·K=1 결과와 TCP 상태를 한 줄씩 비교할 수 있다.

## 3. 대안과 트레이드오프

| 선택지 | 선택과 이유 | 대가 |
| --- | --- | --- |
| SACK walker 직접 호출 / `tcp_ack()` 호출 | **`tcp_ack()`**. 옵션 파싱 위치(`TCP_SKB_CB->sacked`), DSACK 판정, 블록 검증·캐시, `tcp_clean_rtx_queue`, `tcp_fastretrans_alert`, 재전송까지 실제 코드로 지나간다 | 연결 상태 일부를 fixture가 채워야 한다(아래 6절 실패 2건이 그 비용) |
| tun 패킷 시험 / 커널 fixture | **커널 fixture**. page fragment 배치, K 경계 위치, N번째 할당 실패를 결정적으로 만들 수 있다. 큐·힌트를 직접 검사할 수 있다 | 실제 NIC·GSO·타이머 경합은 재현하지 않는다. 패킷 수준 시험은 별도(기존 D·C 시나리오) |
| 기대값만 작성 / RB 대조 추가 | **둘 다**. 핵심 단계는 절대 기대값(카운터·seq·pcount·sacked_out)으로 검사한다. 복잡한 회복 흐름은 RB 참조 실행과 TCP 상태 전체를 비교해 "하이브리드가 의미를 바꾸지 않았다"를 판정한다 | RB와 같다는 것은 mainline 의미 보존이다. mainline 자체의 이상은 잡지 못해서 따로 관찰로 적는다 |
| 해제된 노드 접근 판정 | **KASAN(outline)**. 같은 fixture에 일부러 use-after-free 변이를 넣어 실제로 잡히는지 확인했다 | 빌드·부팅이 느리다. 성능 측정 빌드가 아니다 |
| 힌트 의미 정의 | mainline 코드에서 유도한 불변식을 쓴다. `highest_sack`은 가장 높은 SACKed skb의 다음, `lost_cnt_hint`는 hint 앞 SACKed pcount 합, `retransmit_skb_hint` 앞에는 재전송 대기(LOST·미SACK·미재전송) skb가 없음 | sacked_out=0일 때의 stale `highest_sack`은 mainline이 무시하므로 검사하지 않는다 |

## 4. 검증 방법과 결과

환경: x86_64 컨테이너, gcc 13.3.0, QEMU 8.2.2 TCG `-smp 1 -m 1024`. x86_64_defconfig + B 러너와 같은 옵션, `TCP_RTX_DEBUG`·`TCP_RTX_STATS`·`KASAN`(outline) on. init은 B 러너와 같은 `smoke.c`+`packet_peer.c`(`RTX_PACKET_CASES`). 팀 표준 gcc 9.4·ARM64는 실행하지 않았다.

| 실행 | 결과 |
| --- | --- |
| HYBRID K=16 | fixture 9개 case 2142 checks, 실패 0 (C: cack 548, cshift 686). KASAN 0, WARNING 0. selftest 749/0. `RTX_RUNTIME PASS` |
| HYBRID K=1 | 1459 checks, 실패 0 (cack 368, cshift 348). KASAN 0. selftest 674/0 |
| RB (k16·k1 모양 모두) | 2674 checks, 실패 0 (cack 888, cshift 988). KASAN 0. selftest 556/0 |
| RB 대조 (`c_diff_states.py`) | k16 63단계, k1 55단계 TCP 상태 **차이 0** |
| 음성 대조 1: 오라클 | `lost_cnt_hint`+1, `highest_sack` 한 칸 이동, hint 앞 LOST skb, payload 1바이트 반전을 모두 거부 (`CHINT`/`B_EXPECT` 4건) |
| 음성 대조 2: 변이 커널 | hole-fill에서 `rtx_next(sk, prev)` → `rtx_next(sk, skb)`(이미 해제된 skb). KASAN `use-after-free in tcp_sacktag_walk` 1건, `tcp_rtx.h:190` DEBUG WARNING, fixture 실패 24건으로 거부 |
| 음성 대조 3: 대조기 | `test_c_diff_states.py` 5개(상태 차이·단계 누락·빈 로그 거부) 통과 |
| 개발 실행 (K=4, KASAN 없음) | 1698 checks, 실패 0 |
| 정적 검사 | `git diff --check` 통과. 새 C 파일 checkpatch 0/0 |

### 4.1 항목별 실행 내용

**1. split → partial shift / full merge** (`case_cshift`, 모두 `NOLOSS`)

- `c_shift_boundary`: skb n=K+3개(0~2 TREE, 3~ LIST).
  1. SACK `[s2+1000, s3)`: skb 2를 split한다 (prepare/commit 1).
  2. SACK `[.., s3+2000)`: LIST 첫 노드 2 MSS가 TREE 마지막에 **partial shift**된다. `shift_partial` = `TCPSackShifted` = 1, 새 split 없음. first(L).seq가 +2000 되어도 LIST에 남는다.
  3. SACK `[.., s4)`: 나머지가 **full merge**된다 (`shift_merge` = `TCPSackMerged` = 1). K=1은 LIST가 빈다. K=16은 LIST 길이 15, `rtx_tree_last`는 병합 대상이다.
  4. 전체 ACK 뒤 큐·wmem·tsorted가 0이다.
  - base 1000과 wrap(`-6000`) 두 가지로 실행했다.
- `c_shift_pair(cur)`: prev(SACKed) / cur(미SACK) / next(SACKed). cur를 채우는 SACK 한 번으로 cur와 next가 같은 호출에서 병합된다(merge 2). 반환된 prev 다음부터 순회가 이어져 cur+2가 partial shift된다(partial 1). `highest_sack` = cur+2이다.
  - 위치 조합(K=16): T/T/T(cur=1), **T/T/L**(cur=2, TREE 끝과 LIST 첫 노드를 함께 제거), T/L/L, L/L/L. 여기에 wrap을 더했다.
  - K=1: T/T/T, T/T/L. K=1의 cur=2는 뒤에 skb가 없어 partial이 0이다.
- 누락·중복 방문은 `sacked_out`·`delivered`·플래그 재계산(`b_valid`)과 절대 기대값으로, 해제 접근은 KASAN으로 판정했다.

**2. SACK 뒤 부분·누적 ACK** (`cack/cleanup`)

1. SACK: skb 1 전체와 first(L) 중간(split 2회)
2. `ack=s0+1000`: frag skb 머리 trim. `ack_trim`+1, packets_out −1, wmem −1000
3. `ack=s1+2000`: skb 0 제거와 **SACKed skb 1의 부분 ACK**. sacked_out 6→4
4. `ack=s3+2000`: 옛 TREE/LIST 경계를 넘어 SACKed 중간 조각까지. unlink 3, trim 1, sacked_out 1
5. 전체 ACK: 모든 카운터 0, tsorted 비어 있음, `rtx_tree_first/last`·`rtx_recent_len` 0

매 단계 `b_valid`(packets·sacked·lost·retrans 재계산, wmem=Σtruesize, tsorted 정상 상태)를 검사했다.

**3. 힌트 의미**: 모든 시나리오의 매 ACK 뒤 `c_hints`를 검사했다. `recovery`에서는 실제 `tcp_mark_head_lost()`가 `lost_skb_hint`/`lost_cnt_hint`를 만들었다. 이어서 shift·부분 ACK·DSACK가 일어나는 동안 의미가 유지되는 것을 확인했다(K=16 예: `lh=4/0` → 부분 ACK 뒤 `lh=3/0`). 경계 병합에서 `highest_sack`은 `tok.next`와 일치했고, 끝이면 NULL이었다.

**4. DSACK·재정렬·재전송·split 실패** (`cack/recovery`, `reorder`, `split_failure`)

- recovery (`CLASSIC`):
  1. SACK으로 Recovery에 진입하고, 머리를 손실 표시한 뒤 실제 재전송한다. 재전송 경로 안에서 `tcp_fragment`가 돈다. 결과는 xmits 4, total_retrans 5.
  2. 재전송된 머리를 부분 ACK한다.
  3. 첫 블록 `[s0, s0+1000)` DSACK: `TCPDSACKRecv`+1, `undo_retrans` 5→4.
  4. 같은 DSACK을 한 번 더 보낸다(관찰 1 참고).
  5. 경계 누적 ACK, 이어서 전체 ACK.
- reorder: SACK 뒤 재전송 없는 앞부분을 누적 ACK한다. `TCPSACKReorder`+1, `reord_seen`+1, 손실·재전송 없음.
- split_failure: 한 SACK 블록에서 두 번째 split 할당을 실패시킨다(alloc 2, fault 1, prepare/commit 1, sacked 0, transaction 닫힘). 이어서 재시도 SACK(prepare 2, sacked 2), 부분 ACK, 전체 ACK가 모두 정상이었다.

B가 공유한 계약과 이번 결과는 서로 맞다.

- `rtx_find_cover`는 포함 검색이다. #21의 sweep과 B의 gap 검사가 같은 결과다.
- 두 번째 split이 실패해도 첫 split은 유지된다.
- SACKed skb가 tsorted에서 빠지는 것은 정상 상태다.

### 4.2 관찰 (하이브리드 결함 아님, RB와 동일)

1. 같은 DSACK을 두 번 받으면 `undo_retrans`가 두 번 줄고(4→3) `delivered`도 두 번 늘었다. mainline `tcp_check_dsack()`은 어떤 구간을 이미 DSACK으로 셌는지 기억하지 않는다. RB 참조와 상태가 같아 hybrid 문제는 아니다. 정상 수신자는 같은 DSACK을 반복하지 않는다.
2. HYBRID K=16의 find_cover는 LIST 구간에서 최대 16노드를 선형 탐색한다(sweep 평균 7.72, RB 2.97). #16 측정에서 참고할 값이다.

## 5. 증거

로컬 전용(git 무시). 위치는 `rtx-project/.work/c15-20261116-1506/`(C 작업 환경)이다. PR에는 캡처 4장을 첨부할 예정이다.

| 주장 | 파일 |
| --- | --- |
| K=16·K=1·RB 실행 원본 | `serial-hyb16.log`, `serial-hyb1.log`, `serial-rb.log` |
| case별 판정 | `summary-*.json`(B의 `check_results.py`), `kasan-*.txt` |
| RB 대조 | `diff-k16.json`, `diff-k1.json` |
| 변이 음성 대조 | `mutant.patch`, `serial-mutant.log`, `kasan-mutant.txt` |
| 실행 화면(원본 로그 기반 보고서, 행 번호·sha256 표시, 콘솔 캡처 아님) | `report/hyb16.png`, `report/hyb1.png`, `report/rb.png`, `report/mutant.png` |
| 실행한 harness·overlay | `harness/`, `instrumentation.patch`, `source-sha.txt`(`6c98193d1`), `source.patch` |
| 설정·빌드·커널 | `*.config`, `build-*.log`, `bzImage-*`, `initramfs.cpio`, `environment.txt` |
| 개발 중 실패 재현 | `dev-failures/` |
| 체크섬 | `SHA256SUMS` |

실행한 harness와 이 PR 파일은 `c_common.h` 20행 주석 한 줄만 다르다(checkpatch 80열 정리). 실행 코드는 같다.

## 6. 실패와 수정

모두 fixture 준비 문제였다. production 코드는 바꾸지 않았다.

| 실패 | 원인 | 수정·재실행 |
| --- | --- | --- |
| 재전송 단계에서 `divide error` in `__tcp_select_window` (`dev-failures/serial-k4-no-rcvmss.log` 317행) | 연결되지 않은 fixture 소켓의 `icsk_ack.rcv_mss`가 0이다. 재전송 헤더를 만들 때 0으로 나눈다 | `rcv_mss`·수신 창을 설정. 재실행 통과 |
| recovery 단계에서 `b_valid`·구조 검사 실패, 길이 0 skb 3개(`serial-k4-no-gsosegs.log` 320·321행, 24 failures) | `sk_gso_max_segs`=0이라 `tcp_tso_segs()`가 0을 돌려준다. 그래서 `tcp_retransmit_skb(segs=0)` → `tcp_fragment(len=0)`가 된다. 연결 소켓은 route가 1 이상으로 설정하므로 실제로는 생기지 않는다 | `sk_gso_max_segs`/`size` 설정. 재실행 통과 |

두 로그는 수정 전 harness(`dev-failures/*.patch`)로 다시 실행해 보존했다. 처음 발견했을 때의 로그는 개발 중 덮어써서 남아 있지 않다.

결함은 찾지 못했으므로 production 수정 패치는 없다.

## 7. 남은 작업

- **B와 공동 확인**: 재전송 재진입(`__tcp_retransmit_skb` 안의 `tcp_fragment`)이 이번 recovery에서 실제로 돌았다(머리 `0+2000` 조각). 회계는 RB와 같았다. split 의미 판정은 B 기준으로 함께 보기로 했다.
- 일부러 겨냥하지 않은 경로:
  - `tcp_shifted_skb`에서 병합 대상이 `lost_skb_hint`인 경우
  - `retransmit_skb_hint`가 병합·삭제되는 경우
  - OFO DSACK
  - RACK 시간 기반 손실(`C_NOLOSS`는 모든 skb의 송신 시각이 같다)
  - TLP·RTO 타이머 발화
- 환경 확대: gcc 9.4·ARM64(D), 다중 CPU, IPv6, 실제 NIC는 미실행이다.
- #20 머지 뒤 이 PR의 base를 main으로 바꾼다. #21 머지 전에는 이 fixture의 shift·ACK 카운터 검사가 실패한다(카운터 증가 코드가 #21에 있음).
- #15 매트릭스에서 C 담당 행은 위 미검증을 빼고 채웠다. 이슈 종료는 B·A와 합의해 판단한다.
