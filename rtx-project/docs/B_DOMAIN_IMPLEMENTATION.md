# B 도메인 V1 구현과 후속 작업

작성일: 2026-11-06

B 도메인은 TCP 송신 경로를 공통 재전송 큐 API에 연결한다. 현재 완료한 작업은 기존 RB backend를 유지하는 V1 호출부 전환이다. 최근 리스트, 승격, 캐시, tail 직접 연결 최적화와 split token은 아직 구현하지 않았다.

이 문서는 B 담당자와 A·C·D 협업자가 현재 구현 범위, 검증 한계, 다음 패치의 선행 조건을 확인하기 위한 기록이다.

## 1. 기준과 현재 상태

| 항목 | 기준 |
| --- | --- |
| 커널 | Linux v5.4.214 |
| 브랜치 | `feat/b-domain-packet-insert` |
| V1 변경 전 커밋 | `7c09b8883eb2d72beea5eb5928de7241a13e14fa` |
| V1 구현 커밋 | `c0fbf4eae4d11243587e0a723f5c259772c8ddf0` |
| PR | [#7](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/7) |
| PR 대상 | `main`. 원격에 `dev`가 없어 사용자가 이번 PR의 대상으로 승인했다. |
| 설계 기준 | [design.md](design.md)의 rbtree와 길이 제한 이중 연결 리스트 설계 |

V1 구현 커밋은 `tcp_output.c` 11곳과 `tcp.c` 1곳을 변경했다. 추가 12행, 삭제 12행이며 Core API 자체는 변경하지 않았다. V1 연결 완료는 전체 하이브리드 통합 완료를 뜻하지 않는다.

이전 논의에서 원형 버퍼도 비교 후보로 다뤘지만, 이 문서의 후속 구현 계획은 현재 `design.md`의 이중 연결 리스트를 전제로 한다.

## 2. 구현 구조와 책임

```text
송신·분할·재전송·송신 큐 조회
                |
                v
       rtx_* 공통 큐 API
                |
                v
       기존 rbtree backend
```

데이터 구조는 기존 `struct sock::tcp_rtx_queue`와 `struct sk_buff`를 유지한다. 송신 코드는 큐의 구조 연산을 API에 맡기고, 기존 TCP 처리 순서를 유지한다.

| 책임 | 소유 영역 |
| --- | --- |
| 큐의 연결·순회·검색, 향후 캐시와 승격 | A의 Core 구현 |
| 신규 송신, 분할·병합·교체의 송신 호출부 | B |
| ACK/SACK 처리와 관련 힌트 규칙 | C, 공유 함수는 B와 협업 |
| 빌드·부팅 환경, 통합 회귀와 반복 측정 | D, 각 구현자는 자신의 테스트 책임 유지 |

관련 파일은 다음과 같다.

- [tcp_rtx.h](../../include/net/tcp_rtx.h)는 기존 RB 연산을 감싸는 Core API를 정의한다.
- [tcp.h](../../include/net/tcp.h)는 Core 헤더를 포함하고, 기존 head/tail/empty 및 제거 wrapper를 제공한다.
- [tcp_output.c](../../net/ipv4/tcp_output.c)는 송신·분할·재전송 경로를 구현한다.
- [tcp.c](../../net/ipv4/tcp.c)의 `tcp_peek_sndq()`는 재전송 큐와 write queue의 데이터를 조회한다.

### 2.1 사용 중인 API 계약

| API | V1 동작과 전제 |
| --- | --- |
| `rtx_append(sk, skb)` | tail 뒤에 오는 미연결 skb를 삽입한다. 현재 내부 구현은 일반 `tcp_rbtree_insert()`다. |
| `rtx_insert_ordered(sk, skb)` | 시퀀스 위치에 삽입한다. 분할과 SYN 관련 삽입에 사용한다. |
| `rtx_last(sk)` | 마지막 skb를 반환한다. 빈 큐면 NULL이다. 현재는 캐시 조회가 아니다. |
| `rtx_next(sk, skb)` | 현재 큐에 있는 살아 있는 skb의 다음 항목을 반환한다. |
| `rtx_walk(skb, sk)` | 큐의 첫 항목부터 순회한다. |
| `rtx_walk_from(skb, sk)` | 현재 항목부터 순회한다. |
| `rtx_walk_from_safe(skb, tmp, sk)` | 본문 전에 다음 항목을 저장한다. 저장한 다음 항목까지 제거하는 경우는 보호하지 않는다. |
| `rtx_unlink(sk, skb)` | 구조에서만 제거한다. free, TCP 회계와 tsorted 정리는 하지 않는다. |

호출자는 기존 소켓 직렬화 조건을 만족해야 한다. 조회는 참조 횟수를 증가시키지 않는다. Core는 `net/tcp.h`를 통해 사용하며 `net/tcp_rtx.h`를 직접 include하지 않는다.

삽입의 미연결 조건은 시퀀스 큐 연결에 관한 조건이다. 별도 시간 순서 큐인 `tcp_tsorted_anchor`의 관리는 기존 TCP 경로에 남긴다.

### 2.2 호출부 변경 목록

| 파일·함수 | 기존 연산 | V1 연결 |
| --- | --- | --- |
| `tcp_output.c`: `tcp_event_new_data_sent()` | RTX 직접 트리 삽입 | `rtx_append(sk, skb)` |
| `tcp_insert_write_queue_after()` | RTX 분기 직접 트리 삽입 | `rtx_insert_ordered(sk, buff)` |
| `tcp_send_loss_probe()` | 트리 마지막 항목 조회 | `rtx_last(sk)` |
| `tcp_send_loss_probe()` | 분할 후 다음 항목 조회 | `rtx_next(sk, skb)` |
| `tcp_collapse_retrans()` | 다음 항목 조회 | `rtx_next(sk, skb)` |
| `tcp_retrans_try_collapse()` | 현재 항목 제거를 허용하는 순회 | `rtx_walk_from_safe(skb, tmp, sk)` |
| `tcp_xmit_retransmit_queue()` | 현재 위치부터 재전송 순회 | `rtx_walk_from(skb, sk)` |
| `tcp_send_fin()` | 메모리 압박 시 RTX 마지막 항목 조회 | `rtx_last(sk)` |
| `tcp_send_synack()` | 복사본 삽입 | `rtx_insert_ordered(sk, nskb)` |
| `tcp_send_syn_data()` | TFO 데이터 삽입 | `rtx_insert_ordered(sk, syn_data)` |
| `tcp_connect()` | SYN 삽입 | `rtx_insert_ordered(sk, buff)` |
| `tcp.c`: `tcp_peek_sndq()` | RTX 전체 순회 | `rtx_walk(skb, sk)` |

### 2.3 기존 TCP 동작을 보존한 지점

신규 송신은 write queue에서 skb를 분리한 뒤 `rtx_append()`를 호출한다. 이후 `highest_sack`, `packets_out`, 타이머와 통계 처리는 원래 순서를 유지한다.

`tcp_insert_write_queue_after()`의 write queue 분기는 그대로다. RTX 분기만 ordered insert로 연결했다. 이 단계는 RB 연결 패치이며, 하이브리드 분할에 필요한 변경 전 소속 저장을 구현하지 않는다.

`tcp_collapse_retrans()`의 `tcp_rtx_queue_unlink_and_free()`는 유지했다. 이 wrapper는 시간 큐 제거, 구조 제거와 메모리 해제를 수행하므로 `rtx_unlink()`만으로 대체할 수 없다.

Collapse 순회는 본문 실행 전에 `tmp`를 저장한다. 첫 반복은 병합 대상 `to`를 건너뛰며, 이후 병합으로 제거하는 `next(to)`는 현재 반복의 `skb`다. 따라서 현재 경로에서는 저장한 `tmp`가 유지된다. 이 판단을 다른 SACK 순회의 안전성으로 일반화하지 않는다.

SYNACK·TFO·SYN은 ordered insert를 사용한다. 특히 `tcp_send_syn_data()`는 `space == 0`인 경우에도 성공 삽입 경로에 도달할 수 있다. V1은 새 비어 있지 않은 구간 조건을 추가하지 않는다. 하이브리드의 구간 불변식과의 정합성은 후속 검토 대상이다.

## 3. 선택과 tradeoff

| 결정 | 선택 이유 | 비용·한계 또는 후속 조건 |
| --- | --- | --- |
| 기존 API로 직접 접근만 치환 | 변경 범위를 제한하고 Core가 저장 구조를 소유하게 한다. | V1만으로 성능 향상을 주장할 수 없다. |
| 기존 head/tail/empty wrapper 유지 | 이미 Core에 위임하므로 추가 이름 변경이 불필요하다. | 기존 wrapper와 Core의 두 이름이 남지만 책임은 같다. |
| 기존 unlink/free wrapper 유지 | 시간 큐와 skb 수명 관리를 보존한다. | 향후 변이 token 경로에서 구조 제거를 중복 수행하지 않도록 설계해야 한다. |
| 신규 송신만 append로 구분 | 순차 신규 삽입이라는 의도를 후속 tail 최적화에 전달한다. | 실제 빠른 삽입은 캐시 구현 이후에 가능하다. |
| 분할·SYN 관련 삽입에 ordered insert 사용 | 임의의 tail 전제를 새로 추가하지 않는다. | 해당 경로의 빠른 삽입 가능성은 이번에 최적화하지 않는다. |
| 수신용 일반 RB helper 유지 | `tcp_rbtree_insert()`는 수신 OFO 경로도 사용한다. | 저장소 전체에서 이 helper가 사라지는 것을 완료 기준으로 삼지 않는다. |
| 하이브리드와 split을 후속 패치로 분리 | 현재 RB에서 검증 가능한 작업 단위를 확보한다. | 하이브리드 활성화 전 추가 계약과 검증이 필요하다. |

후속 TREE split에는 우선 트리 전용 일반 ordered insert를 사용하는 방안을 B가 권고했다. 논리적 다음 항목의 자리가 항상 현재 노드의 빈 오른쪽 자식은 아니다. tail 직접 연결 최적화를 중간 분할에 그대로 적용하면 안 된다. 이 선택은 A의 최종 수락 및 구현 확인이 남아 있다.

## 4. V1 검증 결과와 한계

다음 결과는 V1 구현 커밋과 변경 전 커밋의 비교 결과다. 이번 문서 추가를 위해 커널 빌드를 다시 실행한 것은 아니다.

| 검증 | 결과 |
| --- | --- |
| `git diff --check` | 통과 |
| `scripts/checkpatch.pl --no-tree` | 오류·경고·check 항목 각 0개 |
| 독립 소스 검토 | API 대응, collapse 순회, 기존 정리 처리에서 수정할 문제를 발견하지 못함 |
| 변경 전·후 `tcp.o`, `tcp_output.o` 빌드 | 모두 통과 |
| `tcp.o` 바이트 비교 | 동일 |
| `tcp_output.o` 바이트 비교 | 다름. TLP와 FIN 함수의 명령·분기 배치 차이를 관찰 |
| `tcp_output.o` 미정의 심볼 목록 | 동일 |
| 실제 커널 부팅·송수신 회귀 | 미실행 |
| 성능 측정 | 미실행 |

객체 파일 빌드는 전체 커널 링크나 부팅 검증이 아니다. `tcp_output.o`의 바이너리 동등성을 입증하지 못했으며, 소스 검토와 부분 빌드 결과를 전체 TCP 회귀 통과로 해석하지 않는다.

빌드 환경은 Ubuntu 22.04 컨테이너와 x86_64 cross GCC 11.4.0이었다. 설정은 `rtx_defconfig`를 사용했다. [MANIFEST.md](MANIFEST.md)의 Ubuntu 20.04·GCC 9.4 실행 환경과 동일한 검증 환경은 아니다. 원본 빌드의 호스트 인증서 도구에서는 OpenSSL 3 deprecated 경고가 발생했다. 테스트용 컨테이너는 제거했고 기존 컨테이너는 보존했다.

각 소스를 준비한 별도 Linux 빌드 환경에서 사용한 명령은 다음과 같다. `/work/src`는 해당 비교 소스, `/work/out`은 빌드 출력 디렉터리다.

```sh
cd /work/src
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- rtx_defconfig
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 net/ipv4/tcp.o net/ipv4/tcp_output.o
```

관찰한 SHA-256은 다음과 같다.

| 대상 | SHA-256 |
| --- | --- |
| 변경 전·후 `tcp.o` | `8a72abf23964dd357c133df5095b4b58cf1fda9755d990205d564a6585be70f8` |
| 변경 전 `tcp_output.o` | `8f8fc95098af2090ad3a92df4a2bd1b1e514c5b4a3aedcb826b0a03be81ce594` |
| 변경 후 `tcp_output.o` | `c82fbd52106a04843fcb8a8bef4a456605d289ae3176485d433dee86935c9cbe` |
| 빌드 `.config` | `abadc082049bdb37ab9e3d673eb33b7e7b23aa7b9d29a44d6a3949a570ac2016` |

V1 커밋의 PR 생성 당시 GitHub Actions workflow와 등록된 commit check/status는 0개였다. 자동 검증이 없다는 뜻이며 CI 성공을 뜻하지 않는다. 환경·컴파일러·경로가 달라지면 객체 해시도 달라질 수 있다.

## 5. 후속 split 패치의 계약 초안

이 절은 A가 전달한 초안과 B 측 검토 내용을 정리한다. 아래 함수와 token은 현재 Core 헤더에 없는 제안 인터페이스다. A의 최종 보완 답변과 구현은 아직 확인하지 않았다.

### 5.1 A가 제안한 인터페이스

```c
struct rtx_split_token {
	struct sk_buff *skb;
	u8 domain;
};

void rtx_split_prepare(struct sock *sk, struct sk_buff *skb,
		       struct rtx_split_token *tok);
void rtx_split_commit(struct sock *sk, struct rtx_split_token *tok,
		      struct sk_buff *buff);
```

`domain`은 prepare 당시 TREE 또는 LIST 소속을 나타낸다. debug 빌드에서는 원래 `seq`, `end_seq`를 추가 저장하는 제안이다. 상수와 debug 상태의 실제 정의는 A가 확정해야 한다. token은 소켓 직렬화 구간 안의 임시 상태이며 skb 참조 횟수를 늘리지 않는다.

### 5.2 tcp_fragment의 연결 위치

1. 기존 할당·unclone 실패 처리를 완료한다.
2. `Correct the sequence numbers` 직전에 RTX 큐일 때만 prepare를 호출한다.
3. 기존 데이터 분할, 시퀀스·플래그·pcount·wmem 처리를 수행한다.
4. 현재 `tcp_insert_write_queue_after()` 호출 위치에서 RTX 큐이면 commit으로 연결한다. Write queue 분기는 기존 연결을 유지한다.
5. 기존 `tcp_tsorted_anchor` 연결을 수행한 뒤 전체 상태가 안정된 지점에서 검증한다.

현재 소스에는 prepare 제안 지점 이후 오류 반환 경로가 없다. 따라서 이 경로를 유지한다면 abort API가 필요하지 않다는 판단이다. 후속 변경이 실패 가능한 연산이나 조기 반환을 추가하면 이 전제를 다시 검토해야 한다.

RTX 분할은 `tcp_fragment()`의 공통 경로를 사용한다. 직접 호출 지점은 `tcp_output.c` 2곳, `tcp_input.c` 2곳, `tcp_recovery.c` 1곳이다. B의 함수 연결이 C의 SACK·RACK 동작에도 영향을 주므로 통합 검증이 필요하다.

### 5.3 commit 전제와 동작

A 초안의 전제는 원래 skb의 `seq`가 유지되고, `skb.end_seq == buff.seq`이며, `buff.end_seq`가 원래 끝값과 같다는 것이다. 새 `buff`는 시퀀스 큐에 아직 연결되지 않아야 한다.

| prepare 당시 소속 | commit 동작 |
| --- | --- |
| TREE | 새 뒷부분을 트리에 삽입한다. 원래 skb가 tree_last였다면 캐시를 buff로 갱신한다. |
| LIST | 원래 skb 바로 뒤에 buff를 연결한다. 길이를 증가시키고 K 초과 시 리스트 head를 승격한다. |

K개 상태의 리스트 head를 분할하면 원래 skb 자신이 트리로 승격될 수 있다. commit 이후 호출자는 이전 소속을 가정하지 않는다. token은 commit 후 무효가 된다.

### 5.4 B가 요청한 보완사항

- 변이 중 일반 공개 RTX API와 이를 호출하는 wrapper를 금지하는 범위를 확정한다. commit 자체와 token을 받는 내부 연결 연산은 허용해야 한다.
- TREE commit은 저장된 소속을 이용하는 트리 전용 삽입으로 시작한다. 공개 ordered insert를 호출해 변이 중 소속을 재판별하지 않는다.
- `WARN_ON_ONCE`는 위반 탐지이며 상태 복구가 아니다. 경고 후 연결을 생략해서 부분 변경 상태를 남기지 않도록 처리 방침을 정한다.
- `RB_EMPTY_NODE()`나 `list_empty()`만으로 buff의 미연결 여부를 판정하지 않는다. 공유 union과 할당 초기화 규칙을 고려하고, 필요하면 debug 순회로 실제 중복 포함 여부를 확인한다.
- 구조 검증과 전체 TCP 검증 시점을 구분한다. commit 직후에는 새 buff의 tsorted 연결이 아직 끝나지 않는다.
- RB backend에도 동일한 split API를 먼저 제공한다. B가 호출부를 연결한 뒤 하이브리드 구현과 독립적으로 검증할 수 있어야 한다.

## 6. 남은 작업과 완료 기준

| 순서 | 작업 | 담당·의존성 | 완료 기준 |
| --- | --- | --- | --- |
| 1 | V1 실제 커널 부팅·송수신 검증 | B·D, 지금 진행 가능 | 대상 커널 부팅 확인, payload 길이·해시와 종료 동작 확인 |
| 2 | 분할·재전송 시나리오 준비 | B, C·D 협업 | 각 시나리오가 의도한 분할 경로를 실행한다는 관찰 방법 확보 |
| 3 | RB용 split API 제공 | A, 위 초안 보완 | prepare/commit 계약과 RB 구현 제공, Core 테스트 통과 |
| 4 | B split 연결 패치 | B, 3번 선행 | RTX에만 prepare/commit 적용, write queue와 기존 TCP 처리 보존, 빌드·회귀 통과 |
| 5 | 리스트와 승격 구현 통합 | A·B·C | K 경계, 원래 skb 승격, TREE/LIST 경계 분할에서 순서·캐시·힌트 검증 |
| 6 | merge·trim·replace 계약과 연결 | A·B·C | token, 구조 제거, 시간 큐·해제 순서와 순회 재개 위치 확정 |
| 7 | 캐시 및 tail 직접 연결 | A의 캐시 계약, B의 삽입 최적화 | 빈 트리·일반 삽입·삭제·교체에서 캐시 일치, RB 균형 유지 |
| 8 | 비용 및 회귀 비교 | B·D, 통합 정확성 선행 | V1·캐시·tail·하이브리드의 효과를 분리해 측정 |

1번과 2번은 A의 하이브리드 완성을 기다리지 않는다. 4번의 병합 가능한 패치에는 실제 split API가 필요하다. 존재하지 않는 API를 호출하는 코드만 커밋해서 빌드를 깨뜨리지 않는다.

### 6.1 split 공동 테스트

| 테스트 | 주 담당 | 확인할 결과 |
| --- | --- | --- |
| TREE의 head·중간·tail 분할 | A 모델, B 연결 | 구간·순서 보존과 tree_last 갱신 |
| LIST의 head·중간·tail 분할 | A 모델 | 바로 뒤 연결과 길이 관리 |
| K개 상태의 head 분할 | A 모델, B 통합 | 원래 skb 승격 후에도 주소·순서와 후속 접근 유지 |
| TREE/LIST 경계 인접 항목 분할 | A 모델, B·C 통합 | 영역 분리, next/prev와 검색 결과 보존 |
| 할당·unclone 실패 | B | prepare 이전에 오류 반환, 큐 연결 누락·중복 없음 |
| GSO, FIN 포함 분할, wraparound | A·B·C | 플래그·시퀀스·pcount·힌트의 기존 의미 보존 |
| SACK·RACK·TLP·재전송의 실제 분할 | B·C·D | 5개 호출 경로의 동작과 시간 큐·회계 확인 |

### 6.2 후속 최적화의 주의점

현재 `rtx_last()`는 `skb_rb_last()`를 호출하므로 매번 이것으로 tail을 구하면 탐색 비용이 남는다. tail 직접 연결에는 캐시된 tree_last가 필요하다. 전체 논리 큐의 tail과 트리 tail을 구분하고, 캐시를 갱신하는 모든 삽입·삭제·교체·초기화 경로를 A와 확정해야 한다.

직접 연결 후에도 `rb_insert_color()`는 실행해야 한다. 따라서 목표는 삽입 위치 탐색 생략이며, 삽입 전체의 O(1) 보장이 아니다.

MANIFEST에는 VirtualBox PMU 미지원이 기록되어 있다. 해당 환경에서는 cycles 값을 추정해 보고하지 않는다. D와 사용 가능한 시간 지표 및 비교 횟수를 정하고, microbenchmark와 실제 통신 성능 결과를 구분한다.

## 7. 이후 변경 시 확인할 항목

- [ ] 이 문서의 완료·미완료 상태를 실제 구현과 맞춘다.
- [ ] split API가 추가되면 초안 대신 실제 헤더의 계약과 테스트 결과를 연결한다.
- [ ] 실제 커널 실행 후 환경, 시나리오, 결과와 미검증 범위를 기록한다.
- [ ] 하이브리드 활성화 전 A의 init·purge·child 경로와 C의 직접 RB 접근 감사 결과를 확인한다.
- [ ] 검증 결과가 없는 성능 향상이나 전체 TCP 정확성을 주장하지 않는다.
