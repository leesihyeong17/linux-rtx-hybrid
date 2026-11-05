# C 담당 호출 지점 감사표 (ACK/SACK 경로)

기준: `v5.4.214` + A의 RB 어댑터(`include/net/tcp_rtx.h`, PR #3·#4). 브랜치 `c/ack-sack-adapter`.
분류: **변경** = rtx 큐 직접 접근을 어댑터로 교체 / **유지** = 이미 어댑터 경유 또는 다른 자료구조 / **보류** = hybrid 단계에서 결정.

## 1. 변경한 지점

| 파일:행 | 함수 | 이전 | 이후 | 비고 |
| --- | --- | --- | --- | --- |
| tcp_input.c:1421 | `tcp_shift_skb_data` | `skb_rb_prev(skb)` | `rtx_prev(sk, skb)` | SACK shift 대상 이전 노드 |
| tcp_input.c:1508 | `tcp_shift_skb_data` | `skb_rb_next(prev)` | `rtx_next(sk, prev)` | 병합 후 다음 노드 |
| tcp_input.c:1543 | `tcp_sacktag_walk` | `skb_rbtree_walk_from` | `rtx_walk_from` | `continue` 시 반환 cursor 의미 동일 |
| tcp_input.c:1612 | `tcp_sacktag_skip` | `tcp_sacktag_bsearch()` | `rtx_find_cover()` | `tcp_sacktag_bsearch` 삭제 (어댑터와 동일 구현) |
| tcp_input.c:1958 | `tcp_timeout_mark_lost` | `skb_rbtree_walk_from` | `rtx_walk_from` | RTO 시 손실 표시 |
| tcp_input.c:2196 | `tcp_mark_head_lost` | `skb_rbtree_walk_from` | `rtx_walk_from` | `lost_skb_hint`에서 시작 |
| tcp_input.c:2339 | `tcp_undo_cwnd_reduction` | `skb_rbtree_walk(..., &sk->tcp_rtx_queue)` | `rtx_walk` | |
| tcp_input.c:2605 | `tcp_simple_retransmit` | `skb_rbtree_walk(..., &sk->tcp_rtx_queue)` | `rtx_walk` | |
| tcp_input.c:3093 | `tcp_clean_rtx_queue` | `skb_rb_first(&sk->tcp_rtx_queue)` | `rtx_first(sk)` | 누적 ACK 시작점 |
| tcp_input.c:3164 | `tcp_clean_rtx_queue` | `skb_rb_next(skb)` | `rtx_next(sk, skb)` | 삭제 전 next 저장 |
| tcp_input.c:5863 | `tcp_rcv_fastopen_synack` | `skb_rbtree_walk_from` | `rtx_walk_from` | TFO SYN 데이터 재전송 |
| tcp.h:1873 | `tcp_advance_highest_sack` | `skb_rb_next(skb)` | `rtx_next(sk, skb)` | **A 소유 헤더** — A 리뷰 필요 |

## 2. 유지한 지점

| 파일:행 | 내용 | 이유 |
| --- | --- | --- |
| tcp_input.c:1359, 3170 | `tcp_rtx_queue_unlink_and_free` | 이미 `rtx_unlink` 경유 |
| tcp_input.c:1946, 2192, 2298, 3018, 5828 / tcp_recovery.c:241 / tcp_timer.c:467·501·534 / tcp.h:1883·2063 | `tcp_rtx_queue_head`·`empty` | 이미 어댑터 경유 |
| tcp_input.c:4674, 4892, 5050~5061 등 | `skb_rb_*`, `rb_erase` | 수신 out-of-order 큐. 재전송 큐 아님 (설계 §8) |
| `tcp_rbtree_insert` (tcp_input.c) | 정의 | ofo와 공유. 송신 쪽 호출은 B 범위 |

## 3. 보류

| 파일:행 | 내용 | 결정 필요 사항 |
| --- | --- | --- |
| tcp_input.c:3613 | `prefetch(sk->tcp_rtx_queue.rb_node)` | hybrid에서도 `sk->tcp_rtx_queue`가 T 루트로 남으므로 그대로 동작. 다만 ACK가 L(리스트)에서 끝나는 경우가 많으면 `rtx_first()` 쪽을 prefetch하는 게 맞을 수 있음 → A와 `rtx_prefetch(sk)` helper 여부 결정 후 측정 |

## 4. 검증

- `rtx_defconfig`, gcc 13.3으로 `tcp_input.o`·`tcp_output.o`·`tcp.o`·`tcp_recovery.o`·`tcp_timer.o`·`tcp_ipv6.o` 경고 없이 빌드.
- 변경 전(main `7c09b88`)과 후의 `objdump -d` 비교(주소 정규화):
  - `tcp_output.o`: 동일
  - `tcp_input.o`: `tcp_sacktag_walk` 안 2개 명령만 다름 (레지스터↔스택 슬롯 1곳, 정렬용 NOP 1곳). 함수 목록·제어 흐름 동일.
- 커널 부팅·실제 TCP 회귀 테스트는 **아직 안 함** (D의 환경에서 V1 smoke로 확인 필요).
- 팀 표준 컴파일러(gcc 9.4)로는 재빌드하지 않았음.

## 5. hybrid backend로 갈 때 C 쪽에서 확인할 것

1. `tcp_shift_skb_data` → `tcp_shifted_skb`의 full merge는 `skb`를 unlink·free한다. T 마지막 + L 첫 노드 병합이면 `rtx_unlink`가 L 길이와 경계를 갱신해야 한다 (설계 §5.5 full merge).
2. `tcp_sacktag_walk`는 shift가 현재·다음 skb를 모두 없앨 수 있어 반환 cursor를 쓴다. `rtx_walk_from`이 매 반복 `rtx_next`를 새로 호출하므로 의미는 유지되지만, T→L 경계를 넘는 `rtx_next`가 맞는지 테스트 필요.
3. `rtx_find_cover`는 L 구간이면 최대 K개 선형 탐색. SACK이 L을 자주 가리키는지(방문 노드 수)를 C의 측정 항목으로 둔다.
