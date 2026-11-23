# A: 사용자 공간 하이브리드 모델 + 랜덤 trace 동등성 테스트 (설계 11.1)

작성: 2026-11-23 · 담당: A · 브랜치: `a/hybrid-model` · PR: (생성 후 링크) → PR: https://github.com/leesihyeong17/linux-rtx-hybrid/pull/35 · 관련: 이슈 #34, 설계 11.1, A 문서 3.9절·7절 5번, B 문서 6.1절, 이슈 #24, PR #25·#33

## 1. 문제와 범위

- **기준 SHA**: 작업은 main `ffd6e62`(PR #31 머지 시점, 하이브리드 backend PR #17 포함)에서 했고, PR #33 머지 후 main `0f2987d`로 rebase했다. `0f2987d`의 `tcp_rtx.h`·`rbtree`·`list`·`tcp_input.c`는 PR #33 head `5a75c90`과 같으므로 아래 `5a75c90` 결과가 현재 main 헤더의 결과다
- **문제**: 하이브리드 backend(PR #17)는 커널 자체 테스트(구조 검사)와 B·C·D의 실제 TCP 회귀로 검증됐지만, 설계 11.1이 요구한 **독립 기준 모델과의 동등성 테스트**(같은 trace를 RB·하이브리드에 적용해 순서·검색 결과 비교, 랜덤 seed·실패 trace 저장)는 없었다. A 문서 1절에도 "미착수"로 남아 있다.
- **목표**: 실제 `include/net/tcp_rtx.h`를 사용자 공간에서 그대로 컴파일해, 커널 코드와 무관하게 작성한 기준 모델과 수백만 개 연산에서 결과가 같은지 확인한다. 판정기가 실제 버그를 거부하는지도 확인한다.
- **완료 조건**: ① 설계 11.1 필수 사례와 랜덤 trace에서 RB·하이브리드(K 여러 값, DEBUG/STATS on·off) 모두 불일치 0 ② 일부러 넣은 버그(변이)를 모두 검출 ③ 실패 trace·seed 저장 ④ 재현 스크립트와 증거 로그 커밋.
- **포함**: `rtx-project/tools/rtx_model/`(하네스·기준 모델·변이 검사), `scripts/a_model_test.sh`, `tests/test_a_rtx_model.py`, `evidence/A/1123_model_*.txt`, 이 문서, A 문서 3.9절 등 갱신(1절 표의 STATS v2 행을 "PR #33 머지"로 고침 포함).
- **제외**: 커널 코드 변경(없음). TCP 회계·힌트·tsorted·pcount·할당 실패(어댑터 밖, 7절). PR #25 코드 수정(검증만 함).

## 2. 최종 변경

| 파일 | 내용 |
| --- | --- |
| `tools/rtx_model/build.sh` | 대상 리비전의 `tcp_rtx.h`, `include/linux/{rbtree,rbtree_augmented,list}.h`, `lib/rbtree.c`를 **수정 없이 복사**하고 `tcp_input.c`에서 `tcp_rbtree_insert()`를 그대로 잘라 와서 설정별 바이너리를 만든다. 입력 파일 SHA-256을 남긴다 |
| `tools/rtx_model/shim/` | 커널 밖 의존성만 채우는 최소 헤더. `struct sk_buff`는 커널과 같이 `next/prev`·`rbnode`·`list`를 **한 union**에 둔다(한 쪽을 쓰면 다른 쪽이 깨지는 실제 구조). `tcp_sock` 필드와 `#ifdef`는 `include/linux/tcp.h`와 같다. `WARN_ON_ONCE`는 즉시 실패, `CONFIG_DEBUG_LIST` 검사 켬, `LIST_POISON`은 접근 시 segfault 나는 주소 |
| `tools/rtx_model/harness.c` | trace 명령을 공개 `rtx_*` API로 실행하고 결과를 한 줄씩 출력. 매 명령 뒤 **하네스 자체 검사**: 레드-블랙 속성·부모 링크·트리 순서, 리스트 링크·순서·`rtx_recent_len`·K 상한, `rtx_tree_first/last` 캐시, STATS면 `rtx_count`, DEBUG면 `rtx_validate_structure()`. 해제된 skb는 링크 영역을 독으로 채워 재사용 시 바로 깨지게 한다 |
| `tools/rtx_model/rtx_model.py` | **기준 모델**: 64비트 확장 시퀀스로 정렬한 Python 리스트(직렬번호 연산·rbtree·list 없음) + 설계 3.1/5.1/5.5의 T/L 소속 규칙을 K별로 추적. 필수 사례 시나리오 17개, 랜덤 trace 생성(크기 3종), 비교, 실패 trace 저장(`--fail-dir`), 경로 도달 집계 |
| `tools/rtx_model/mutants.py` | `tcp_rtx.h`에 한 줄짜리 버그를 하나씩 넣어 빌드·실행하고 검출 여부 확인 (17종, 해당 코드가 없는 헤더는 N/A) |
| `scripts/a_model_test.sh` | 증거 재생성: 빌드 → 시나리오 → 랜덤 → 변이. 맨 위에 날짜·기기·컴파일러·대상 SHA·도구 SHA 기록 |
| `tests/test_a_rtx_model.py` | 기존 `unittest discover`에 들어가는 빠른 검사(시나리오 + 10 seed + 비교기 자체 음성 시험). 커널 소스가 없는 sparse clone·컴파일러 없음이면 skip |

trace 명령과 대응 API:

| 명령 | 실행 | 해당 커널 경로 |
| --- | --- | --- |
| `APPEND` | `rtx_append` | `tcp_event_new_data_sent` |
| `ORD` | `rtx_insert_ordered` | SYN·TFO `syn_data` |
| `UNLINK` | `rtx_unlink` (중간 노드 포함 → 구멍 생성) | `tcp_rtx_queue_unlink` |
| `ACK p` | `rtx_first` + `rtx_unlink` 반복 후 head seq 증가(trim) | `tcp_clean_rtx_queue`, `tcp_trim_head` |
| `SPLIT` | `rtx_split_prepare` → seq 변경 → `rtx_split_commit` | `tcp_fragment` |
| `SHIFT` | `rtx_shift_prepare` → prev.end·cur.seq 증가 → `rtx_shift_commit(remove)` | `tcp_shifted_skb` 부분·전체 |
| `REPLACE` | `rtx_replace` | SYN 재전송 clone 교체 |
| `PURGE` | `rtx_walk_from_safe` + `rtx_unlink` | `tcp_rtx_queue_purge` |
| `COVER q` | `rtx_find_cover` | `tcp_sacktag_bsearch` 대체 |
| `OVERLAP s e` | 범위 검색(하네스 안 구현, 커널에 없음 — A 문서 3.7) | 설계 5.3 `rtx_first_overlap` |
| `DUMP` | `rtx_first/last`, `rtx_walk`, `rtx_prev` 역순회, `rtx_domain`, `rtx_empty` | 순회·소속 판별 전반 |
| `NBUCKET n` | `rtx_n_bucket` (PR #33에만 있음) | STATS v2 히스토그램 |

STATS 빌드는 끝에 카운터(`insert_append/ordered/split`, `unlink_tree/list`, `promote`, `find_cover`)와 N 히스토그램(`n_hist`/`n_link`)을 출력하고, 모델이 계산한 값과 **정확히** 비교한다. 히스토그램 구간 기대값은 헤더 주석에 적힌 구간 정의(v1: `[2^b, 2^(b+1)-1]`, v2: 0~64 1 단위 후 `[65,127]`…`[16384,)`)로 계산한다.

빌드 설정 (헤더에 `CONFIG_TCP_RTX_CACHE`가 있으면 Kconfig 관계대로 V2·V3·V4도 추가):

| 이름 | 설정 | 비교군 |
| --- | --- | --- |
| `rb` / `rb-plain` | RB, DEBUG+STATS / 둘 다 off | V1 |
| `rb-cache` / `rb-tail` | RB + CACHE / + TAIL (PR #25) | V2 / V3 |
| `hyb-kN` (N = 1, 2, 4, 16) / `hyb-kN-plain` (4, 16) | HYBRID, DEBUG+STATS / off | 기존 하이브리드 |
| `hyb-kN-tail` | HYBRID + CACHE + TAIL (PR #25) | V4 |

API·도메인 영향: 없음. 커널 코드를 바꾸지 않았다.

## 3. 대안과 트레이드오프

| 선택지 | 판단 |
| --- | --- |
| 하이브리드를 Python으로 다시 구현해 모델과 비교 | 기각. 재구현을 검사할 뿐 실제 `tcp_rtx.h`를 검사하지 않는다(`AGENTS.md` 4절 "구현을 그대로 반복하는 테스트" 금지) |
| 커널 안 자체 테스트(`tcp_rtx_selftest.c`) 확장 | 보완 관계. 실제 커널 빌드·부팅이 필요해 반복이 느리고(약 30분), 수백만 연산 랜덤 trace와 실패 trace 저장이 어렵다. 모델은 같은 헤더를 수 초 만에 빌드해 수백만 연산을 돌린다 |
| 커널 헤더 전체를 include해서 사용자 공간 빌드 | 기각. `linux/kernel.h` 등이 끌고 오는 의존성이 너무 크다. 대신 **검사 대상 파일 5개는 원본 그대로**, 나머지만 최소 shim으로 채웠다. shim에는 rbtree·list·rtx 로직이 없다 |
| 기준 모델에 소속(T/L)까지 넣기 vs 순서·검색만 비교 | 소속까지 넣음. 순서·검색만 비교하면 "L이 K-1개로 유지" 같은 버그(변이 `limit_k_minus_1`)가 결과에 드러나지 않는다. 대가로 모델이 설계의 소속 규칙을 따르므로, **설계 규칙 자체의 오류는 잡지 못한다** |
| 매 명령 뒤 전체 상태 출력·검사 | 선택. trace가 길어져도(최대 N≈300) 2코어에서 5,000 seed 약 6분. 첫 불일치 명령 번호를 바로 알 수 있다 |
| `rtx_first_overlap()`을 커널에 추가 | 기각(A 문서 3.7 결정 유지). 하네스에만 두고 모델과 비교한다. 이 결과는 하네스 구현의 정확성일 뿐 커널 코드 검증이 아니다 |

## 4. 검증 방법과 결과

재현: `rtx-project/scripts/a_model_test.sh [-s 0:5000] [-m 0:200] [-o LOG] REV`
환경: Claude 클라우드 작업 환경, x86_64 Linux 6.18, gcc 13.3.0 (`-O2 -Wall -Wextra -Werror`), Python 3.13. **정확성 증거로만 사용**(가이드 2절).

### 4.1 결과

| 대상 | 빌드 | 시나리오 | 랜덤 | 변이 |
| --- | --- | --- | --- | --- |
| main `ffd6e62` | 8개 | 17개 / 명령 10,055 · 불일치 0 | seed 0~4999 / 명령 13,308,809 · 불일치 0 | 14/14 검출 (TAIL·v2 전용 3개 N/A) |
| PR #25 `4e204ff` (B, CACHE·TAIL) | 14개 (V2·V3·V4 포함) | 17개 / 명령 10,055 · 불일치 0 | seed 0~1999 / 명령 5,323,293 · 불일치 0 | 16/16 검출 (v2 전용 1개 N/A) |
| PR #33 `5a75c90` (A, STATS v2) = 현재 main `0f2987d` 헤더 | 8개 | 17개 / 명령 10,055 · 불일치 0 | seed 0~1999 / 명령 5,323,293 · 불일치 0 | 15/15 검출 (TAIL 전용 2개 N/A) |

"명령 수"는 trace 하나의 명령 수 합계이며, 각 빌드가 같은 trace를 모두 실행한다(main 랜덤은 1,330만 × 8개 빌드). STATS 빌드의 카운터·히스토그램 비교는 trace마다 끝에서 한 번 한다.

### 4.2 목표 경로 도달 (main, 랜덤 5,000 seed)

PASS만으로 경로가 실행됐다고 보지 않기 위해 모델이 경로별 횟수를 센다(`AGENTS.md` 4절). 전체 값은 증거 로그 `coverage` 줄.

| 경로 | K=1 | K=2 | K=4 | K=16 |
| --- | --- | --- | --- | --- |
| 승격 (L → T) | 890,140 | 816,617 | 646,194 | 161,553 |
| L이 K개로 꽉 찬 상태 | 2,093,707 | 1,871,353 | 1,425,996 | 312,384 |
| L 안 split | 71,039 | 125,196 | 192,419 | 286,675 |
| split 직후 원래 skb 자신이 승격 (B 6.1 "K개 상태의 head 분할") | 71,039 | 47,561 | 24,647 | 1,978 |
| 경계를 넘는 부분 shift (prev=T 끝, cur=first(L)) | 40,176 | 30,214 | 18,499 | 8,229 |
| 경계를 넘는 전체 병합 (T 끝이 first(L)을 흡수) | 34,123 | 25,563 | 16,185 | 6,985 |
| first(L) 직접 제거 | 24,724 | 27,034 | 27,113 | 27,056 |
| L로 가는 ordered insert | 78,523 | 87,329 | 96,962 | 119,572 |
| L 쪽에서 끝난 `find_cover` | 653,553 | 1,150,651 | 1,740,016 | 2,565,677 |
| T 없이 first(L) head trim | 19,675 | 36,036 | 60,761 | 109,932 |
| T/L 경계가 u32 wrap에 걸린 상태 | 3,910 | 3,762 | 2,611 | 790 |

skb 내부에서 wrap(2,463회), wrap 지점을 가르는 split(938회)은 K와 무관하다. 시나리오는 위에 더해 빈 큐, 1개, K−1/K/K+1, 트리만/리스트만/양쪽, 마지막 삭제 후 재사용, purge 후 재사용, 구멍 안·구멍을 넘는 검색, `q` = seq−1/seq/seq+1/end−1/end/end+1, wrap 위치 6가지, 모든 위치의 replace를 명시적으로 만든다.

### 4.3 변이 검사 — 판정기가 실제로 거부하는가

| 변이 | 넣은 버그 | 처음 잡힌 곳 |
| --- | --- | --- |
| `domain_first_l_is_tree` | `rtx_domain`이 first(L)을 TREE로 판정 | DEBUG 소속 교차 검사 WARN |
| `unlink_keeps_tree_last` | T 끝 제거 시 `rtx_tree_last` 미갱신 | 하네스 캐시 검사 |
| `limit_k_minus_1` | L 상한 K−1 | 모델 소속 비교 |
| `prev_of_first_l_null` / `next_of_tree_last_null` | T↔L 경계에서 순회 끊김 | 역순회·정순회 비교 |
| `cover_list_end_inclusive` / `cover_list_from_boundary_minus` | L 포함 검색 경계 오류 | `COVER` 결과 |
| `ordered_list_position` / `split_list_before_skb` / `split_ignores_token_domain` | L 안 삽입 위치·split 소속 오류 | 하네스 리스트 순서 검사 |
| **`shift_recomputes_domain`** | shift commit에서 token 대신 **변이 후** seq로 소속 재판정 | T 끝이 first(L)을 흡수하는 병합에서 큐 손상 |
| `replace_keeps_tree_first` / `tree_insert_first_cache` / `empty_ignores_list` | 캐시·빈 큐 판정 오류 | 캐시 검사·segfault·`DUMP` |
| `tail_append_*` (PR #25만) | TAIL 직접 연결의 first 캐시·왼쪽 자식 오류 | TAIL 빌드에서만 검출 |
| `nbucket_exact_edge` (PR #33만) | N=64가 로그 구간으로 감 | `NBUCKET` 시나리오 |

TAIL 전용 2개를 뺀 모든 변이가 **DEBUG·STATS off 빌드(`hyb-k4-plain`)에서도** 잡혔다. 즉 검출이 `rtx_validate_structure()`나 WARN에만 의존하지 않는다. TAIL 변이는 TAIL을 켠 빌드(모두 DEBUG on)에서만 실행되며 그 빌드에서 잡혔다. PR #25에서는 캐시 관련 변이가 `rb-cache`·`rb-tail`(RB + CACHE)에서도 잡혀, 캐시가 RB backend에서도 검증된다. `shift_recomputes_domain`은 설계 3.3의 "변이 전에 소속을 token에 저장" 규칙이 실제로 필요하다는 근거다.

### 4.4 기타 확인

| 항목 | 결과 |
| --- | --- |
| 비교기 음성 시험 (기대값 한 줄을 틀리게) | 모든 빌드에서 실패로 판정 (`test_rejects_wrong_output`) |
| 히스토그램 구간 함수(모델 쪽) | v1·v2 경계값 단위 시험 통과 |
| `python3 -m unittest tests.test_a_rtx_model` | 4개 통과 |
| `pyflakes`, `bash -n`, `git diff --check` | 경고·오류 없음 |
| 커널 빌드·부팅 | 해당 없음 (커널 코드 변경 없음) |

## 5. 증거

| 파일 | 주장 |
| --- | --- |
| [`evidence/A/1123_model_ffd6e62.txt`](../../evidence/A/1123_model_ffd6e62.txt) | main 하이브리드·RB 동등성, 카운터·`n_hist` v1 정확성, 변이 검출 |
| [`evidence/A/1123_model_4e204ff.txt`](../../evidence/A/1123_model_4e204ff.txt) | PR #25 V2·V3·V4 설정 동등성, TAIL 변이 검출 |
| [`evidence/A/1123_model_5a75c90.txt`](../../evidence/A/1123_model_5a75c90.txt) | PR #33 STATS v2 카운터·`n_link`·`rtx_n_bucket` 정확성 |

각 로그 맨 위에 대상 SHA, 도구 커밋, 복사한 커널 입력 파일 SHA-256, 바이너리별 `CONF`(K·DEBUG·STATS·CACHE·TAIL) 줄이 있다. 도구 커밋 해시는 패치를 `git am`하면 바뀐다. 도구 내용 동일성은 이 PR의 커밋으로 확인한다.

## 6. 실패와 수정

- 첫 수동 trace에서 인접하지 않은 노드로 `SHIFT`를 보내자 하네스가 즉시 순서 위반으로 거부했다. 커널 문제가 아니라 잘못된 입력이었고, 생성기는 인접·연속 쌍만 고르도록 만들었다.
- 모델 `ordered()`가 새 노드를 넣은 **뒤** first(L)을 찾아 KeyError → 넣기 전에 찾도록 수정. 모델 쪽 버그였다.
- PR #33은 `rtx_n_bucket()`을 STATS 밖에 정의해 STATS off 빌드에도 있다. 처음에는 STATS 여부로 기대값을 정해 3개 빌드가 불일치 → 하네스가 함수 존재 여부(`nbucket=1`)를 알려 주도록 수정.
- B 문서 6.1의 "TREE tail 분할"이 시나리오에 명시적으로 없어(랜덤에만 있음) 추가했다.
- **커널 코드(main·#25·#33)에서 발견한 결함: 없음.**

## 7. 남은 작업과 한계

- **어댑터 구조만 검증한다.** TCP 회계(`packets_out`, `sacked_out` 등), 힌트(`highest_sack`, `lost_skb_hint`), tsorted 큐, GSO pcount, FIN 플래그, wmem, 할당·clone·unclone 실패는 모델에 없다. 이 부분은 커널 자체 테스트와 B·C·D의 실제 TCP 회귀(PR #13, #20, #26, #27)가 근거다.
- 단일 스레드 사용자 공간 실행이다. 소켓 lock·softirq 동시성, per-CPU 합산은 검증하지 않는다.
- STATS v2 포트 필터는 `port 0`(필터 없음)만 실행했다. 필터 동작은 PR #33의 QEMU 검증이 근거다.
- 기준 모델은 설계 문서의 소속 규칙을 따르므로, 규칙 자체가 성능상 나쁜 선택인지는 판단하지 않는다(그건 N 측정·Go/No-Go 몫).
- x86_64 gcc 13.3에서만 실행. ARM64·gcc 9.4는 미실행(정확성 증거라 환경 차이는 허용, 가이드 2절).
- 모델은 `rtx_count`·N 히스토그램의 **계산이 맞는지**만 보인다. 실제 트래픽의 N 분포는 D의 #28 측정 몫이다.
- 후속: PR #25가 main에 머지되면 통합 SHA(#25 + #33)로 `a_model_test.sh`를 다시 돌려 작업채팅 ④(PR #17·#25 독립 검증)의 증거로 쓴다.
