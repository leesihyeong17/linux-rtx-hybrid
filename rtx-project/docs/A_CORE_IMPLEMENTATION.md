# A 도메인(Core) 구현 기록

최초 작성: 2026-11-10 · 최종 갱신: 2026-11-11

A는 재전송 큐의 공통 API(`rtx_*`), 자료구조, 불변식 검증, 초기화·정리 경로와 통합 브랜치를 담당한다. 이 문서는 A의 구현 범위, 계약, 검증 결과와 한계를 B·C·D가 확인할 수 있도록 기록한다. 중요 작업이 끝날 때마다 3절에 항목을 추가하고 1절·6절의 상태를 갱신한다.

이 문서에 적힌 결과는 해당 절에 명시한 환경에서 실제로 실행한 것만 포함한다. 실행하지 않은 검증은 "미실행"으로 적고 통과로 해석하지 않는다.

## 1. 기준과 현재 상태

| 항목 | 기준·상태 |
| --- | --- |
| 커널 | Linux v5.4.214 |
| 팀 기준선 | `v0-baseline` = `991defcc56cad49cfb221f44f5d161c9ea920c4a` |
| upstream | gregkh stable `v5.4.214` = `9f02aa34e179d1b0185a27980dd5bafd0f2e6e59` |
| 현재 backend | RB (V1). 하이브리드 T+L은 미구현 |
| 설계 기준 | [design.md](design.md) 3·4·5·7절 |

| 작업 | 커밋·PR | 상태 |
| --- | --- | --- |
| 기준선 고정과 공용 설정 | `991defc`, `3dcdacf` | 완료 |
| RB 어댑터 | `23b99ca` (PR #3) | main 머지 |
| 순회 매크로 | `9ad40b1` (PR #4) | main 머지 |
| RB split API + 디버그 자체 테스트 | `0b15ec3` (PR #9) | main 머지 |
| 직접 접근 정리 (`tcp.c` init·purge) | `4fe3558` (PR #10) | main 머지 |
| 측정 카운터 + N 히스토그램 + `rtx_prefetch` | 브랜치 `a/rtx-stats` | PR 리뷰 대기 |
| merge·trim 계약(③) 초안 | — | 11/13 목표 |
| RB용 merge API | — | 11/15 목표 |
| 하이브리드 모델·`rtx_validate()` | — | 미착수 |
| 커널 하이브리드 backend | — | 미착수 |

## 2. 구조와 책임

```text
TCP 송신(B) · ACK/SACK(C) · 타이머·복구
                 |
                 v
   tcp.h wrapper (tcp_rtx_queue_head/tail/empty/unlink...)
                 |
                 v
        rtx_* 공통 API  (include/net/tcp_rtx.h, A 소유)
                 |
                 v
   backend: RB(V1, 현재)  →  T rbtree + L 리스트(V4, 예정)
```

| 파일 | 내용 | 소유 |
| --- | --- | --- |
| `include/net/tcp_rtx.h` | 공통 API, split 트랜잭션, backend 구현 | A |
| `include/net/tcp.h` | `tcp_rtx.h` 포함, 기존 wrapper가 어댑터를 경유 | A (B·C 리뷰) |
| `include/linux/tcp.h` | 디버그 빌드 전용 `rtx_txn_open` 필드. 추후 하이브리드 필드 | A |
| `net/ipv4/Kconfig`, `Makefile` | `CONFIG_TCP_RTX_DEBUG`, 자체 테스트 빌드 연결 | A |
| `net/ipv4/tcp_rtx_selftest.c` | 디버그 커널 부팅 시 자체 테스트 | A |
| `net/ipv4/tcp_rtx_stats.c` | 측정 카운터 저장소와 `/proc/rtx_stats` | A |

`tcp_rtx.h`는 `net/tcp.h`를 통해서만 사용한다. 직접 include하면 컴파일 오류가 나도록 막았다. `__rtx_`로 시작하는 함수는 backend 내부용이며 호출부에서 쓰지 않는다.

### 2.1 공개 API 계약

| API | 의미 | RB backend 구현 |
| --- | --- | --- |
| `rtx_init(sk)` | 빈 큐로 초기화 | `RB_ROOT` |
| `rtx_empty(sk)` | 비었는지 | `RB_EMPTY_ROOT` |
| `rtx_first(sk)` / `rtx_last(sk)` | 가장 낮은/높은 seq skb, 없으면 NULL | `skb_rb_first/last` |
| `rtx_next(sk, skb)` / `rtx_prev(sk, skb)` | 논리 순서상 다음/이전. 입력은 큐에 있는 skb | `skb_rb_next/prev` |
| `rtx_walk(skb, sk)` | 처음부터 순회 | first + next |
| `rtx_walk_from(skb, sk)` | skb부터 순회 | next |
| `rtx_walk_from_safe(skb, tmp, sk)` | 현재 skb 제거 허용. 다음 skb까지 제거하는 경우는 보호하지 않음 | next 선저장 |
| `rtx_find_cover(sk, seq)` | `seq <= q < end_seq`인 skb, 없으면 NULL | 기존 `tcp_sacktag_bsearch`와 동일 |
| `rtx_insert_ordered(sk, skb)` | seq 위치에 삽입 | `tcp_rbtree_insert` |
| `rtx_append(sk, skb)` | tail 뒤 신규 삽입. 하이브리드에서는 길이 제한·승격·캐시까지 내부 완료 | `tcp_rbtree_insert` |
| `rtx_unlink(sk, skb)` | 구조에서만 제거. free·회계·tsorted 정리 안 함 | `rb_erase` |
| `rtx_prefetch(sk)` | ACK 처리 전 캐시 힌트. 구조 접근 아님 | 트리 루트 prefetch. 하이브리드에서는 첫 skb |
| `rtx_stat_inc(id)` / `rtx_stat_add(id, v)` | 측정 카운터 증가 (2.4절) | `CONFIG_TCP_RTX_STATS` 꺼지면 빈 함수 |

공통 조건:

- 호출자가 기존 소켓 직렬화를 보장한다. 어댑터는 sleep·lock 획득을 하지 않는다.
- 조회는 참조 횟수를 늘리지 않는다(borrowed pointer).
- 삽입 대상은 시퀀스 큐에 연결되지 않은 skb다. 시간 큐(`tcp_tsorted_anchor`)는 기존 TCP 경로가 관리한다.
- 기존 `tcp_rtx_queue_unlink()`는 tsorted 정리 + `rtx_unlink`를 계속 수행한다. 구조 간 이동(승격)은 이 wrapper를 쓰지 않는다.

### 2.2 split 트랜잭션 계약

`tcp_fragment()`가 skb `[a,c)`를 skb `[a,b)` + buff `[b,c)`로 나눌 때 사용한다. B와 협의해 확정했으며 `tcp_rtx.h` 주석이 기준이다.

```c
struct rtx_split_token {
	struct sk_buff *skb;
	u8 domain;		/* enum rtx_domain: RTX_DOMAIN_TREE / RTX_DOMAIN_LIST */
	/* CONFIG_TCP_RTX_DEBUG: prepare 시점 seq, end_seq */
};
void rtx_split_prepare(struct sock *sk, struct sk_buff *skb, struct rtx_split_token *tok);
void rtx_split_commit(struct sock *sk, struct rtx_split_token *tok, struct sk_buff *buff);
```

| 단계 | 규칙 |
| --- | --- |
| prepare 위치 | RTX 큐일 때만. 마지막 실패 지점(`sk_stream_alloc_skb` 성공) 뒤, seq 수정 직전 |
| prepare 동작 | skb와 소속 저장. 실패하지 않고 할당하지 않음 → abort API 없음. prepare 뒤에 실패 경로가 추가되면 재검토 |
| 사이 구간 | 공개 `rtx_*`, `rtx_walk*`, 그 위의 wrapper(`tcp_rtx_queue_head/tail/empty/unlink/unlink_and_free` 등) 금지. 현재 사이 구간 함수 4개(`tcp_skb_fragment_eor`, `tcp_fragment_tstamp`, `tcp_set_skb_tso_segs`, `tcp_adjust_pcount`)는 어댑터를 호출하지 않음을 확인 |
| commit 위치 | 기존 `tcp_insert_write_queue_after()` 자리. 이후 tsorted 연결·회계는 `tcp_fragment()`가 그대로 수행 |
| commit 전제 | skb seq 불변, skb end_seq == buff seq, buff end_seq == 원래 end_seq, buff는 큐에 없음 |
| commit 동작 | TREE: 트리 전용 ordered insert, 중간 분할에 tail 최적화 없음. LIST: skb 바로 뒤 연결 후 길이 제한 복구(skb 자신이 승격될 수 있음) |
| commit 후 | token 무효. 호출자는 skb의 소속을 가정하지 않음 |
| buff 미연결 판정 | 공유 링크 필드(`list`/`rbnode`)를 보지 않음. 디버그 빌드에서 `__rtx_find_cover(buff seq) == NULL`로 확인 |
| 검증 시점 | commit은 구조만 국소 검사. 시간 큐까지 포함한 전체 검증(`rtx_validate`, 예정)은 `tcp_fragment()` 처리가 끝난 뒤 |

### 2.3 디버그 설정 `CONFIG_TCP_RTX_DEBUG`

| 항목 | 내용 |
| --- | --- |
| 기본값 | n. 성능 측정 빌드에는 켜지 않는다 |
| 소켓 크기 영향 | 켰을 때만 `tcp_sock`에 1바이트(`rtx_txn_open`) |
| 위반 처리 | `WARN_ON_ONCE`로 탐지만 한다. 연결을 건너뛰거나 되돌리지 않는다. commit은 항상 buff를 연결한다 |
| 자체 테스트 | 켜면 `tcp_rtx_selftest.c`가 빌드되어 부팅 중 실행. 결과는 `dmesg \| grep rtx_selftest` |

디버그 커널은 일반 rtx 커널을 덮어쓰지 않도록 이름을 바꿔 빌드한다.

```bash
scripts/config -e TCP_RTX_DEBUG --set-str LOCALVERSION "-rtx-dbg"
make olddefconfig
# 빌드·설치 후 5.4.214-rtx-dbg로 부팅 → dmesg | grep rtx_selftest
scripts/config -d TCP_RTX_DEBUG --set-str LOCALVERSION "-rtx"   # 복구
make olddefconfig
```

새 Kconfig 옵션이 생긴 뒤 처음 빌드할 때는 `make olddefconfig`를 먼저 실행한다. 생략하면 빌드 도중 설정 질문이 뜬다.

### 2.4 측정 카운터 `CONFIG_TCP_RTX_STATS`

| 항목 | 내용 |
| --- | --- |
| 기본값 | n. 원인 지표 측정용 빌드에서만 켠다. 최종 성능 측정은 STATS·DEBUG 모두 끈 빌드로 한다 (design 10.3) |
| DEBUG와 분리한 이유 | DEBUG의 WARN 검사와 추가 검색은 성능을 왜곡한다. STATS는 CPU별 카운터 증가만 한다 |
| 소켓 크기 영향 | 켰을 때만 `tcp_sock`에 4바이트(`rtx_count`, 큐 안의 skb 수) |
| 저장소 | CPU별 `struct rtx_stats` (이벤트 카운터 + N 히스토그램). 소켓 lock 안에서 `this_cpu_*`로 증가 |
| 읽기·초기화 | `cat /proc/rtx_stats` / `echo 0 > /proc/rtx_stats` (root, 트래픽 없을 때). netns와 무관하게 보이도록 `/proc/net`이 아닌 `/proc`에 둔다 |
| N 히스토그램 | skb를 큐에 연결할 때마다 그 직후의 N을 기록. 구간 `[2^b, 2^(b+1)-1]`, 마지막은 `32768-`. 삽입 빈도로 가중된 분포다 |
| 자체 테스트와의 관계 | DEBUG도 켜면 부팅 자체 테스트의 삽입이 카운터에 들어간다. 측정 전에 초기화한다 |

| 카운터 | 세는 위치 | 의미 |
| --- | --- | --- |
| `insert_append` / `insert_ordered` / `insert_split` | 어댑터 | 삽입 경로별 횟수 |
| `unlink_tree` / `unlink_list` | 어댑터 | 소속별 구조 제거 수 (RB에서는 모두 tree) |
| `unlink_purge` | `tcp_rtx_queue_purge` (A) | 위 제거 중 purge로 빠진 수 |
| `promote` | 어댑터 | 리스트→트리 승격 (하이브리드) |
| `find_cover`, `find_cover_tree_steps`, `find_cover_list_steps` | 어댑터 | 포함 검색 횟수와 비교한 노드 수 |
| `sack_skip_reuse` | `tcp_sacktag_skip` (C) | 커서 재사용으로 검색을 건너뛴 수 |
| `sack_block`, `sack_walk_skb` | SACK 처리 (C) | 처리한 SACK 블록 수, `tcp_sacktag_walk` 방문 skb 수 (블록당 평균 = 나눗셈) |
| `shift_partial` / `shift_merge` / `shift_fallback` | `tcp_shift_skb_data` (C) | shift 결과별 횟수 |
| `ack_clean`, `ack_unlink`, `ack_trim` | `tcp_clean_rtx_queue` (C) | ACK 처리 횟수, 제거 skb 수(ACK당 평균 = 나눗셈), 부분 ACK trim 수 |

호출부 카운터의 enum 항목은 미리 정의해 두었다. C는 `rtx_stat_inc(RTX_STAT_...)` 한 줄만 넣으면 된다. 새 항목이 필요하면 A에게 요청한다(enum과 `/proc` 이름표를 함께 바꿔야 한다).

## 3. 작업 기록

### 3.1 기준선 고정 (2026-11-02)

- kernel.org `linux-5.4.214.tar.xz`(sha256 `9abc79b4…5db`, [MANIFEST.md](MANIFEST.md))를 풀어 팀 레포를 만들고 `v0-baseline`을 붙였다.
- 처음 import할 때 커널 `.gitignore`에 걸려 7개 파일(`tools/perf/lib/include/perf/*.h` 6개, `tools/testing/selftests/tc-testing/plugins/__init__.py`)이 빠졌다. `git add -f`로 보충했다. perf를 소스에서 빌드하려면 필요하다.
- upstream 태그와 트리를 비교해 차이가 `arch/x86/configs/rtx_defconfig` 하나뿐임을 확인했다.
- `rtx_defconfig`: localmodconfig 기반, `LOCALVERSION=-rtx`, `DEBUG_INFO` off, veth·netem·e1000 포함. **x86 전용**이다. D의 ARM64 환경은 별도 설정을 사용한다.
- x86 VirtualBox VM에서 `perf stat -e cycles,instructions`가 `<not supported>`였다(2026-11-04).

### 3.2 RB 어댑터 (PR #3, 2026-11-04)

- `include/net/tcp_rtx.h`를 새로 만들고 2.1절 API를 기존 rbtree 연산으로 구현했다.
- `tcp.h`의 `tcp_rtx_queue_head/tail/empty/unlink`가 어댑터를 거치게 했다. 이름은 유지했으므로 기존 호출 약 25곳이 수정 없이 어댑터를 경유한다.
- `rtx_find_cover`는 `tcp_sacktag_bsearch`와 같은 코드다. C가 PR #6에서 원본을 이 함수로 대체했다.
- `rtx_append`와 `rtx_insert_ordered`는 현재 같은 구현이다. 의도(순차 신규 삽입 vs 일반 삽입)를 호출부에 남겨 V3·V4에서 `rtx_append`만 바꾸기 위해 나눴다.

### 3.3 순회 매크로 (PR #4, 2026-11-04)

- `skb_rbtree_walk*`의 재전송 큐용 대체로 `rtx_walk`, `rtx_walk_from`, `rtx_walk_from_safe`를 추가했다.
- `_safe`는 현재 skb 제거만 보호한다. SACK shift처럼 다음 skb까지 제거하는 경로는 기존 함수가 반환하는 cursor를 계속 사용해야 한다.

### 3.4 RB split API와 디버그 자체 테스트 (`a/split-api`, 2026-11-10)

- 2.2절 계약의 `rtx_split_token`, `rtx_split_prepare`, `rtx_split_commit`을 추가했다. RB backend에서 prepare는 skb와 `RTX_DOMAIN_TREE`를 저장하고, commit은 내부 `__rtx_tree_insert()`로 buff를 트리에 연결한다.
- 공개 API 전부에 `rtx_assert_no_txn()`을 넣었다. 디버그 빌드가 아니면 빈 함수다.
- 내부 함수 `__rtx_find_cover`, `__rtx_tree_insert`를 분리했다. commit은 공개 API 대신 이 함수를 사용하므로 트랜잭션 검사에 걸리지 않고, 소속을 다시 판별하지 않는다.
- `tcp_fragment()` 연결은 B 담당이다. A는 검증에 사용한 연결 예시를 `B-ref-tcp_fragment-wiring.patch`로 전달했고 커밋하지 않았다.
- 자체 테스트 항목: 8개 큐의 head·중간·tail 분할, 1개 큐 분할 후 재분할, seq wraparound(큐 내부, 첫 skb가 경계에 걸침). 각 분할마다 next/prev 연결, `rtx_find_cover` 결과, `rtx_last` 갱신, 전체 순서·연속성, 트랜잭션 플래그와 token 무효화를 확인한다.

### 3.5 `tcp.c` 초기화·purge 정리 (PR #10, 2026-11-10)

- `tcp_init_sock()`의 `sk->tcp_rtx_queue = RB_ROOT`를 `rtx_init(sk)`로, `tcp_rtx_queue_purge()`의 `rb_first`/`rb_next` 순회를 `rtx_first` + `rtx_walk_from_safe`로 바꿨다.
- purge는 현재 skb만 제거하므로 `_safe`로 충분하다. tsorted 정리와 해제는 기존 `tcp_rtx_queue_unlink()`, `sk_wmem_free_skb()`가 그대로 한다.
- 이후 재전송 큐 루트를 직접 쓰는 곳은 `tcp_input.c`의 prefetch 1곳만 남았다(3.6에서 정리).
- 하이브리드에서 할 일: `tcp_write_queue_purge()` 끝에서 리스트·길이·캐시 리셋 (disconnect 후 재사용 경로).

### 3.6 측정 카운터, N 히스토그램, `rtx_prefetch` (`a/rtx-stats`, 2026-11-11)

- 2.4절의 `CONFIG_TCP_RTX_STATS`, CPU별 카운터, `/proc/rtx_stats`, 소켓별 `rtx_count`를 추가했다.
- 삽입(`rtx_append`, `rtx_insert_ordered`, split commit)과 제거(`rtx_unlink`)에서 이벤트와 `rtx_count`를 갱신한다. 큐에 넣고 빼는 모든 경로가 이미 어댑터를 거치므로 송신·ACK 파일은 건드리지 않았다.
- `__rtx_find_cover()`가 비교한 노드 수를 돌려주도록 바꿨다. 공개 `rtx_find_cover()`만 카운터를 올리고, 디버그 검사용 호출은 세지 않는다.
- `rtx_prefetch(sk)`를 추가하고 `tcp_ack()`의 `prefetch(sk->tcp_rtx_queue.rb_node)`를 교체했다(C 승인). 이로써 재전송 큐 루트를 어댑터 밖에서 직접 쓰는 곳이 없다.
- 자체 테스트에 STATS가 켜졌을 때 `rtx_count`와 실제 순회 개수가 같은지 확인하는 검사를 추가했다.

### 3.7 C와의 결정 사항 (2026-11-11)

| 주제 | 결정 |
| --- | --- |
| `tcp_advance_highest_sack` → `rtx_next` | 승인 (PR #6에서 머지) |
| prefetch | `rtx_prefetch()`를 A가 제공, 호출부 교체도 A PR에 포함하고 C가 리뷰 |
| `rtx_first_overlap()` | **커널에 넣지 않는다.** 호출하는 경로가 없다. 사용자 공간 모델에서만 포함 검색과 의미 차이를 검증하는 테스트용으로 쓴다. 설계서 7절 API 초안과 다른 점이다 |
| SACK shift/merge transaction | `tcp_shifted_skb()`는 seq 수정(1310·1311행) 뒤에 `tcp_advance_highest_sack()`(→`rtx_next`, 1353행)와 `tcp_rtx_queue_unlink_and_free()`(→`rtx_unlink`, 1359행)를 부른다. merge API가 이 둘을 token 기반으로 대신한다. 11/13 계약 초안, 11/15 RB용 API PR |
| C 요청 명시 사항 | ① partial shift에서 skb가 first(L)일 때 commit의 경계 검사 조건, ② `rtx_merge_next` 뒤 `rtx_merge_unlink` 전까지 공개 API 허용 여부 → 계약 초안에 명시 |
| 측정 카운터 | 인프라와 구조 이벤트는 A, "왜 순회했는지"는 C가 호출부에서 `rtx_stat_inc`로. 우선순위는 N 히스토그램 |

## 4. 선택과 tradeoff

| 결정 | 이유 | 비용·한계 |
| --- | --- | --- |
| 기존 wrapper 이름 유지, 속만 위임 | 호출부 수정 없이 25곳을 어댑터로 전환 | 같은 기능의 이름이 두 개 남음 |
| 별도 헤더 `tcp_rtx.h` | B·C의 `tcp.h` 수정과 충돌을 줄이고 소유 범위를 분리 | `tcp.h` 중간에서 include. 직접 include는 오류로 차단 |
| 함수 포인터 대신 static inline | V1의 추가 비용 0, 기계어 동일성으로 검증 가능 | backend는 빌드 시에만 선택 가능 |
| `rtx_append` / `rtx_insert_ordered` 분리 | 순차 삽입 최적화 지점을 호출부에서 구분 | V1에서는 같은 동작 |
| split prepare를 마지막 실패 지점 뒤에 둠 | abort 경로가 필요 없음 | prepare 뒤에 실패 경로가 생기면 계약 재검토 |
| TREE 분할에 tail 최적화 미적용 | 논리적 다음 자리가 항상 현재 노드의 빈 오른쪽 자식은 아님 (B 제안) | 분할 삽입은 O(log T) 탐색 유지 |
| 위반 시 WARN만, 복구 안 함 | 부분 변경 상태를 남기지 않음 | 위반 후 동작은 backend에 따라 다를 수 있음 → 디버그로 원인 제거 |
| 미연결 판정을 seq 조회로 | union 링크 필드는 할당·이전 사용에 따라 값이 남을 수 있음 | 디버그 빌드에서 O(log N) 추가 |
| 디버그 필드를 `#ifdef`로 | 성능 빌드의 소켓 크기 불변 | 디버그·성능 커널을 따로 빌드해야 함 |
| STATS를 DEBUG와 별도 옵션으로 | 원인 지표 측정 중 WARN 검사·추가 검색 비용 배제 | 빌드 조합이 늘어남 (off / STATS / DEBUG+STATS) |
| 카운터를 CPU별 전역으로 | lock·원자 연산 없이 증가, 소켓 크기 증가 최소 | 연결별 분리 불가. 실험마다 초기화하고 한 조건씩 측정 |
| N을 삽입 시점에 샘플링 | 추가 타이머 없이 어댑터 안에서 완결 | 시간 가중 분포가 아니라 삽입 가중 분포 |
| `/proc/rtx_stats` (netns 무관 위치) | D의 veth·netns 실험에서도 같은 파일을 읽음 | 네임스페이스별 분리 없음 |
| `rtx_first_overlap` 커널 제외 | 호출 경로가 없어 코드·테스트 부담만 생김 | 범위 검색이 필요해지면 그때 추가 |

## 5. 검증 결과와 한계

| 작업 | 검증 | 환경 | 결과 |
| --- | --- | --- | --- |
| 기준선 | upstream 태그와 트리 비교 | — | 차이 `rtx_defconfig` 1개 |
| 3.2 어댑터 | `net/ipv4/tcp*.o` 7개 `objdump -d` 비교 (v0-baseline 대비) | x86 VirtualBox, gcc 9.4, `rtx_defconfig` | 7개 동일 |
| 3.2 어댑터 | 위 비교가 실제 재컴파일 결과인지 (`.tcp_input.o.cmd`에 `tcp_rtx.h`) | 같음 | 확인 |
| 3.3 매크로 | B 파일 순회 2곳 시험 교체 후 `tcp_output.o` 비교 (시험 수정은 되돌림) | gcc 13.3, `x86_64_defconfig` | 동일 |
| 3.4 split API | 디버그 off, tcp*.o 7개 비교 (main `9b79b09` 대비) | gcc 13.3, `rtx_defconfig` | 7개 동일 |
| 3.4 split API | 디버그 on, `net/ipv4`·`net/ipv6` 전체 빌드 | 같음 | 경고·오류 0 |
| 3.4 split API | B 연결 예시를 넣은 디버그 커널 QEMU 부팅, 자체 테스트 | gcc 13.3, QEMU(TCG) 1 vCPU·512 MB, root fs 없음 | **495 통과 / 0 실패**, WARNING 없음 |
| 3.4 split API | 디버그 off `net/ipv4` 빌드 | x86 VirtualBox, gcc 9.4 | 실행함, 오류 보고 없음 |
| 3.5 `tcp.c` | `net/ipv4/tcp.o` 비교 (main `d377a82` 대비) | gcc 13.3, `rtx_defconfig` | 동일 |
| 3.6 카운터 | STATS·DEBUG off, tcp*.o 7개 + `net/ipv6/tcp_ipv6.o` 비교 (main `4fe3558` 대비) | gcc 13.3, `rtx_defconfig` | 8개 동일 (prefetch 교체 포함) |
| 3.6 카운터 | DEBUG+STATS, STATS만 각각 `net/ipv4`·`net/ipv6` 전체 빌드 | 같음 | 경고·오류 0 |
| 3.6 카운터 | DEBUG+STATS 커널 QEMU 부팅 + 루프백 100MB 전송 후 `/proc/rtx_stats` | gcc 13.3, QEMU 1 vCPU, busybox initramfs | 자체 테스트 512 통과·0 실패. 200MB 송수신 일치. `insert_append` 6368 + `insert_ordered` 1(SYN) = `unlink_tree` 6369, `n_hist` 합계 6369. WARNING 0 |

한계:

- 호출부를 바꾸는 패치(B의 `tcp_fragment` 연결 등)는 코드 배치가 달라져 기계어 비교로 동등성을 보일 수 없다. 실제 송수신·분할 테스트로 검증해야 한다.
- 자체 테스트는 0으로 채운 `tcp_sock`과 빈 skb로 **구조만** 확인한다. 회계, 힌트, 시간 큐, 실제 TCP 동작은 확인하지 않는다.
- 디버그 위반(사이 구간 API 호출, commit 전제 위반)이 실제로 WARN을 내는지는 실행하지 않았다.
- split API의 VM(gcc 9.4)·ARM64 디버그 빌드, VM에서의 자체 테스트 부팅은 미실행.
- 실제 커널에서의 TCP 송수신·재전송·SACK 회귀와 성능 측정은 미실행.
- 카운터의 실제 트래픽 검증은 무손실 루프백 1개 조건뿐이다. 이때 N은 대부분 1~3(1-1: 3169, 2-3: 3170, 4-31: 30)이었지만, 루프백은 RTT가 거의 0이라 실제 조건의 N 분포로 해석하지 않는다. 테스트용 송수신 프로그램은 busybox `nc`가 5KB만 보내고 끝나서 직접 작성했다. 손실·SACK 경로의 카운터(`find_cover` 등)와 C의 호출부 카운터는 미검증이다.
- QEMU 부팅 커널은 root fs 없이 부팅해 자체 테스트 후 마운트 단계에서 멈춘다. 이는 의도한 동작이다.

## 6. 팀원 작업과의 연동

| 팀원 | 작업 | A 관련 상태 |
| --- | --- | --- |
| B | 송신 경로 V1 연결 (PR #7) | main 머지. 직접 접근 11곳 + `tcp_peek_sndq` 1곳 전환 확인 |
| B | `tcp_fragment` split 연결 | split API(PR #9) 머지됨 → B 연결 진행 가능 |
| B | PR #9, #10 리뷰·머지 | 완료 (2026-11-11) |
| C | ACK/SACK 경로 전환 (PR #6) | A 리뷰 후 머지 (`d377a82`). 12곳(`tcp_input.c` 11곳 + `tcp.h` 1곳) 전환 정확. 남은 prefetch는 3.6에서 `rtx_prefetch()`로 교체 |
| C | merge API 연결, 호출부 카운터 | 11/15 RB merge API 머지 후 `tcp_shifted_skb` 연결. 카운터는 2.4절 enum 사용 |
| D | 환경 수집·ARM64 V0 준비 (PR #8) | main 머지. D 환경은 ARM64(UTM). 최종 측정 기기와 cycles 지원 여부 결정 필요 |

3.6 이후 재전송 큐 루트(`sk->tcp_rtx_queue`)를 어댑터 밖에서 직접 쓰는 곳은 없다. `tcp_input.c`의 나머지 `skb_rb_*`는 수신 out-of-order 큐이며 변경 대상이 아니다.

## 7. 남은 작업과 완료 기준

| 순서 | 작업 | 의존성 | 완료 기준 |
| --- | --- | --- | --- |
| 1 | `a/rtx-stats` 머지 | C 리뷰 | main 반영 |
| 2 | N 분포 측정 | 1, D 시나리오·측정 기기 | 무손실·손실, RTT 조건별 `n_hist` 기록 |
| 3 | merge·trim 계약(③) 초안 | — | 11/13. C 요청 2가지 명시 |
| 4 | RB용 merge API | 3 | 11/15. C가 `tcp_shifted_skb`, B가 `tcp_collapse_retrans` 연결 |
| 5 | 사용자 공간 하이브리드 모델 + `rtx_validate()` + 랜덤 trace | — | 정렬 배열 기준 모델과 결과 일치, 실패 seed 저장. B 요청 split 5개 케이스 포함 |
| 6 | Go/No-Go 판단 | 4, 5 | N 분포와 모델 결과로 V4 계속 또는 V3 축소 결정·기록 |
| 7 | 커널 하이브리드 backend | 6 | `tcp_sock` 필드, 승격, 캐시. 자체 테스트·모델 동등성 통과 |
| 8 | init / child clone / purge | 7 | `tcp_init_sock`, `tcp_create_openreq_child`, purge, disconnect 재사용 경로 검사 |
| 9 | 계약 ③ merge·trim, ④ replace | B·C | 헤더 주석과 이 문서에 확정 |
| 10 | V2 패치, `sizeof(struct tcp_sock)` 비교 | 7 | 비교군 커밋과 크기 수치 기록 |

## 8. 갱신 체크리스트

- [ ] 작업이 끝나면 3절에 항목을 추가하고 1절 표의 상태를 바꾼다.
- [ ] 계약이 바뀌면 `tcp_rtx.h` 주석과 2절을 함께 고친다.
- [ ] 검증 결과는 환경(기기, 컴파일러, 설정)과 함께 5절에 적는다. 실행하지 않은 것은 "미실행"으로 남긴다.
- [ ] 팀원 PR을 리뷰하면 6절에 결과를 적는다.
