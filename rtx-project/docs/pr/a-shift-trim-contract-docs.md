# A: shift·trim·collapse 계약 문서화 (PR #17 반영)

작성: 2026-11-16 · 담당: A · PR: https://github.com/leesihyeong17/linux-rtx-hybrid/pull/22 · 관련 이슈: 없음 (PR #17, #14 후속)

## 1. 문제와 범위

- **기준 SHA**: main `167a868` (PR #19 머지 시점, PR #17 `73747d3` 포함)
- **문제**: `A_CORE_IMPLEMENTATION.md`는 merge·trim 계약 초안(11/13)과 RB용 merge API(11/15)를 A의 남은 작업으로 적고 있다. B가 PR #17에서 같은 문제를 `rtx_shift_token`으로 구현해 main에 머지했으므로 문서가 실제 코드와 다르다. C가 요청한 두 질문(3.7절)의 답도 문서에 없다.
- **목표**: A 문서를 PR #17 기준으로 맞추고 shift·trim·collapse 계약과 C 질문의 답을 기록한다.
- **완료 조건**: A 문서 1·3.7·6·7절의 상태가 실제 main과 일치하고, 2.5절에 계약과 C 질문 답이 있다.
- **포함**: `rtx-project/docs/A_CORE_IMPLEMENTATION.md`, 이 문서.
- **제외**: 커널 코드 변경, 새 merge API, `tcp_rtx.h` 주석 변경, 7절의 다른 항목(7·8번 등) 재정리, 증거 도구 패치(0006, 별도 PR).

## 2. 최종 변경

| 파일 | 변경 |
| --- | --- |
| `A_CORE_IMPLEMENTATION.md` 1절 | `a/rtx-stats`를 PR #12 머지로, merge·trim 계약과 RB merge API를 "PR #17로 대체"로, 하이브리드 backend를 PR #17 머지로 갱신 |
| 같은 문서 2.5절 (신규) | shift·trim·collapse·replace 경로별 구조 API와 소속 유지 이유, `rtx_shift_token` 단계별 규칙, C 질문 ①·② 답, 검증 범위 |
| 같은 문서 3.7·6·7절 | merge API 계획을 대체됨으로 표시, C의 사후 확인 필요 명시 |

API·도메인 영향: 없음 (문서만 변경). 문서에 적은 계약은 PR #17 코드 그대로이며 새 약속을 추가하지 않았다.

## 3. 대안과 트레이드오프

| 선택지 | 판단 |
| --- | --- |
| 계획대로 RB용 `rtx_merge_next`/`rtx_merge_unlink` 추가 | 기각. `rtx_shift_token`과 기능이 겹친다(`AGENTS.md` 2절: 같은 기능 중복 구현 금지). 호출부도 이미 token을 쓴다 |
| `rtx_shift_commit(remove=false)`에 DEBUG 경계 검사 추가 | 이번 범위 밖. 2.5절 근거상 부분 shift는 소속을 바꿀 수 없다. 검사를 넣을지는 C 사후 확인 뒤 후속으로 논의 |
| 계약을 issue-14 문서에만 두고 A 문서는 링크만 | 기각. C 질문의 답과 경로별 표는 A 문서 2절(공개 API 계약)에 있어야 찾을 수 있다. 설계 근거와 B 검증 결과는 복제하지 않고 링크로 둔다 |

## 4. 검증 방법과 결과

| 항목 | 방법 | 결과 |
| --- | --- | --- |
| 계약 내용과 코드 일치 | `include/net/tcp_rtx.h` 172–193, 470–533행, `net/ipv4/tcp_input.c` 1285–1370행, `net/ipv4/tcp_output.c` `tcp_trim_head`·`tcp_collapse_retrans`·`tcp_send_synack`를 읽고 대조 | 일치 (코드 읽기) |
| `rtx_shift_*` 호출부 | `grep -rn "rtx_shift_" net/ include/` | 커널은 `tcp_shifted_skb` 1곳, 나머지는 selftest |
| 공백 오류 | `git diff --check` | 오류 없음 |
| 커널 빌드·부팅·selftest | — | 해당 없음. 문서만 변경했으므로 재빌드하지 않음 (`AGENTS.md` 4절) |
| first(L)에서 부분 shift, 사이 구간 API 호출 시 WARN | — | **미실행** |

## 5. 증거

해당 없음. 실행 결과가 아닌 코드 읽기 결과다. 근거는 위 표의 파일과 행 번호(main `167a868`)다. B의 실행 증거는 [issue-14 대표 문서](issue-14-list-promotion-integration.md)를 따른다.

## 6. 실패와 수정

없음.

## 7. 남은 작업

- C: `tcp_input.c`의 shift 변경(PR #17)과 2.5절 계약 사후 확인.
- A: first(L) 부분 shift와 사이 구간 API 호출 WARN을 selftest 또는 하이브리드 모델로 실행 검증 (모델 작업에서).
- A: 7절 7·8번(하이브리드 backend, init/child/purge)도 PR #17로 상당 부분 완료됐다. A의 사후 리뷰와 독립 검증 결과와 함께 갱신한다.
- 별도 PR: 증거 도구·기존 작업 증거(패치 0006) 재정리.
