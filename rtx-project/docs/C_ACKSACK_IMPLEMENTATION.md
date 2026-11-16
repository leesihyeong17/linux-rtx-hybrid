# C 도메인(ACK/SACK) 상태

갱신: 2026-11-16, 기준 main `73747d3f1`.

C는 ACK·SACK 수신 경로(`net/ipv4/tcp_input.c`)가 재전송 큐를 `rtx_*` API로만 다루게 하고, 그 경로의 정확성과 측정 지점을 맡는다. 호출 지점별 상세는 [c-callsite-audit.md](c-callsite-audit.md), 각 PR의 근거·증거는 `docs/pr/` 문서에 있다. 이 문서는 현재 상태와 남은 일만 적는다.

## 1. 작업 상태

| 작업 | PR·커밋 | 상태 |
| --- | --- | --- |
| V1 호출부 전환 (12곳, `tcp_sacktag_bsearch` 제거) | #6 (`7b84f6b`) | main 머지 |
| `tcp_ack()` prefetch → `rtx_prefetch` | #12 (A) | main 머지, C 확인 |
| SACK shift/merge 트랜잭션 (`tcp_shifted_skb`) | #17 (A·B 작성) | main 머지. C 사후 리뷰·하이브리드 실행 확인 완료 |
| 호출부 카운터 9개 | `d1211ee77` | PR 예정 ([문서](pr/c-ack-sack-stats-find-cover.md)) |
| SACK 검색 경계 sweep | `a4867812c` | 같은 PR |
| D 시나리오(#19) 리뷰 | — | 실행 결과와 RENEGING peer 수정 제안 전달 예정 |
| #15 공동 경계 회귀 | — | SACK 병합 경계·검색 경계만 확인. split 결합·힌트·DSACK 조합 남음 |

## 2. 설계상 C가 지키는 규칙

- SACK 순회는 `tcp_shift_skb_data`가 돌려준 cursor로 계속한다. shift가 현재·다음 skb를 지울 수 있으므로 safe iterator로 바꾸지 않는다.
- `tcp_clean_rtx_queue`는 삭제 전에 `next`를 저장한다.
- seq를 바꾸는 SACK 병합은 `rtx_shift_prepare`/`commit` 사이에서만 한다. 소속은 변이 전에 저장한 값을 쓴다.
- 힌트(`highest_sack`, `lost_skb_hint`, `retransmit_skb_hint`), tsorted 큐, TCP 회계는 기존 TCP 코드가 맡는다. 어댑터로 옮기지 않는다.
- 수신 out-of-order 큐는 재전송 큐가 아니다. 건드리지 않는다.
- 카운터는 "ACK/SACK가 왜 순회했나"만 센다. 구조 이벤트는 A의 어댑터가 센다. 최종 성능은 STATS·DEBUG를 끈 빌드로 잰다.

자료구조 선택 근거(리스트·트리 외 대안 비교)는 design.md §6에 있다.

## 3. 남은 일

| 순서 | 작업 | 의존 | 완료 기준 |
| --- | --- | --- | --- |
| 1 | 카운터·sweep PR 리뷰 반영 | A·D 리뷰 | 리뷰 의견 반영, 재실행 결과 갱신 |
| 2 | LIST 첫 노드 부분 shift 실행 | GSO skb를 만드는 peer | `shift_partial>0`이면서 WARN 0, 경계 seq 변화 뒤 검색 정상 |
| 3 | #15 SACK·split 결합, 힌트 경계, DSACK+재정렬 | B와 시나리오 합의 | 매트릭스 행별 실행/실패/미실행 기록 |
| 4 | ARM64·gcc 9.4 재실행 | D 환경 | 같은 카운터 = MIB 대조 |
| 5 | 성능 비교 기여 | #16 | `find_cover_list_steps`, `sack_walk_skb`/`sack_block` 해석을 측정 문서에 제공 |
