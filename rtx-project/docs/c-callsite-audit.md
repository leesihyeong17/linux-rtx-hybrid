# C 담당 호출 지점 감사표 (ACK/SACK 경로)

기준: main `73747d3f1` (#17 LIST 승격 통합) + C 카운터 PR(`c/stats-v2`). 행 번호는 이 기준의 `net/ipv4/tcp_input.c`이다.
처음 작성: `v5.4.214` + A의 RB 어댑터(PR #3·#4), 브랜치 `c/ack-sack-adapter`(PR #6).
분류: **변경** = rtx 큐 직접 접근을 어댑터로 교체 / **유지** = 이미 어댑터 경유 또는 다른 자료구조 / **트랜잭션** = seq 변이 전후 계약 / **카운터** = C 측정 지점.

## 1. 변경한 지점 (PR #6)

| 행 | 함수 | 이전 | 이후 | 비고 |
| --- | --- | --- | --- | --- |
| 1429 | `tcp_shift_skb_data` | `skb_rb_prev(skb)` | `rtx_prev(sk, skb)` | SACK shift 대상 이전 노드 |
| 1516 | `tcp_shift_skb_data` | `skb_rb_next(prev)` | `rtx_next(sk, prev)` | 병합 후 다음 노드 |
| 1552 | `tcp_sacktag_walk` | `skb_rbtree_walk_from` | `rtx_walk_from` | `continue` 시 반환 cursor 의미 동일 |
| 1625 | `tcp_sacktag_skip` | `tcp_sacktag_bsearch()` | `rtx_find_cover()` | `tcp_sacktag_bsearch` 삭제 (어댑터와 동일 구현) |
| 1973 | `tcp_timeout_mark_lost` | `skb_rbtree_walk_from` | `rtx_walk_from` | RTO 시 손실 표시 |
| 2211 | `tcp_mark_head_lost` | `skb_rbtree_walk_from` | `rtx_walk_from` | `lost_skb_hint`에서 시작 |
| 2354 | `tcp_undo_cwnd_reduction` | `skb_rbtree_walk(..., &sk->tcp_rtx_queue)` | `rtx_walk` | |
| 2620 | `tcp_simple_retransmit` | `skb_rbtree_walk(..., &sk->tcp_rtx_queue)` | `rtx_walk` | |
| 3109 | `tcp_clean_rtx_queue` | `skb_rb_first(&sk->tcp_rtx_queue)` | `rtx_first(sk)` | 누적 ACK 시작점 |
| 3181 | `tcp_clean_rtx_queue` | `skb_rb_next(skb)` | `rtx_next(sk, skb)` | 삭제 전 next 저장 |
| 5881 | `tcp_rcv_fastopen_synack` | `skb_rbtree_walk_from` | `rtx_walk_from` | TFO SYN 데이터 재전송 |
| tcp.h:1873 | `tcp_advance_highest_sack` | `skb_rb_next(skb)` | `rtx_next(sk, skb)` | A 소유 헤더, A 승인 |

## 2. 트랜잭션 지점 (PR #17, A·B 작성, C 사후 리뷰 완료)

| 행 | 함수 | 내용 |
| --- | --- | --- |
| 1311 | `tcp_shifted_skb` | `rtx_shift_prepare(sk, skb, &tok)`. seq 변경 전, 마지막 실패 지점(`tcp_skb_shift`) 뒤 |
| 1338 | 부분 shift | `rtx_shift_commit(sk, &tok, false)` 후 반환 |
| 1357 | 완전 병합 | `tp->highest_sack = tok.next` (기존 `tcp_advance_highest_sack` 대체. 소속을 변이 전에 저장) |
| 1363 | 완전 병합 | `rtx_shift_commit(sk, &tok, true)` → tsorted 제거 → `sk_wmem_free_skb` |

리뷰 결론:

- prepare와 commit 사이에는 필드 읽기만 있다.
- TREE 마지막 skb의 seq가 LIST 첫 seq까지 올라가는 경우도 저장된 domain으로 처리된다.
- tsorted 제거와 구조 제거의 순서가 바뀌었지만 둘은 서로 다른 union 필드라 영향이 없다.

실행 확인: [pr/c-ack-sack-stats-find-cover.md](pr/c-ack-sack-stats-find-cover.md) 4.2절.

## 3. 카운터 지점 (C 카운터 PR)

| 행 | 카운터 | 행 | 카운터 |
| --- | --- | --- | --- |
| 1337 | `shift_partial` (MIB SACKShifted 옆) | 1768 | `sack_block` |
| 1369 | `shift_merge` (MIB SACKMerged 옆) | 3107 | `ack_clean` |
| 1539 | `shift_fallback` (MIB SACKShiftFallback 옆) | 3126 | `ack_trim` (`tcp_tso_acked` > 0) |
| 1560 | `sack_walk_skb` | 3188 | `ack_unlink` |
| 1621 | `sack_skip_reuse` | | |

정의와 근거: 위 PR 문서 2.1·3절.

## 4. 유지한 지점

| 행 | 내용 | 이유 |
| --- | --- | --- |
| 1611 | `tcp_advance_highest_sack` (`tcp_sacktag_walk` 일반 경로) | 내부에서 `rtx_next`. 구조 변이 없음 |
| 3187 | `tcp_rtx_queue_unlink_and_free` | 이미 `rtx_unlink` 경유 |
| 3631 | `rtx_prefetch(sk)` | #12에서 A가 교체. RB는 기존 루트 prefetch와 같음 |
| `tcp_rtx_queue_head`·`empty` 사용처 | | 이미 어댑터 경유 |
| `skb_rb_*`, `rb_erase` (ofo 처리부) | | 수신 out-of-order 큐. 재전송 큐 아님 (설계 §8) |
| `tcp_rbtree_insert` 정의 | | ofo와 공유. 송신 쪽 호출은 B·A 범위 |

`tcp_input.c`에 재전송 큐 직접 접근(`sk->tcp_rtx_queue`, `skb_rb_first(&sk->tcp_rtx_queue)` 등)은 남아 있지 않다 (11/16 grep).

## 5. 검증 이력

| 날짜 | 기준 | 검증 | 결과 |
| --- | --- | --- | --- |
| 11/06 | `7c09b88` → `7b84f6b` (PR #6) | 기계어 비교 | `tcp_output.o` 동일. `tcp_input.o`는 `tcp_sacktag_walk` 안 2개 명령만 차이 (레지스터↔스택 1곳, 정렬 NOP 1곳) |
| 11/13 | #17 이전 main + 카운터 6개 | QEMU 부팅, D 시나리오 4종 | 자체 테스트 통과, RENEGING은 peer 문제로 실패 → 로컬 수정 후 통과 |
| 11/16 | `73747d3f1` + 카운터 9개 | STATS off 기계어 비교 (RB·HYBRID) | `tcp_input.o`·`tcp_output.o`·`tcp.o` 동일 |
| 11/16 | 같음 + sweep | QEMU 부팅 RB·HYBRID(K=4), 시나리오 5종 | 자체 테스트 556·689 통과, shift 카운터 = MIB 30/30, WARN 0 |

컴파일러는 gcc 13.3이다. 팀 표준 gcc 9.4·ARM64 실행은 D 환경에서 확인이 필요하다.

## 6. 하이브리드에서 C가 계속 볼 것

1. 부분 shift가 LIST 첫 노드에서 일어나 경계 seq가 바뀌는 경우는 아직 실행하지 못했다 (GSO 다중 세그먼트 skb 필요).
2. `tcp_sacktag_walk`의 반환 cursor가 T→L 경계를 넘는 경우: SHIFT_BOUNDARY 실행에서 정상. split이 섞인 경우는 #15.
3. `rtx_find_cover`의 LIST 선형 탐색 비용: 실제 트래픽의 `find_cover_list_steps / find_cover`로 본다 (#16).
