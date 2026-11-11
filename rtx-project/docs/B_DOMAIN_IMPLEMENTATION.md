# B 도메인 V1 구현과 후속 작업

작성일: 2026-11-06 · 갱신일: 2026-11-11

B 도메인은 TCP 송신 경로를 공통 재전송 큐 API에 연결한다. V1 호출부 전환에 이어 RB backend의 split prepare/commit을 `tcp_fragment()`에 연결했다. 최근 리스트, 승격, 캐시와 tail 직접 연결 최적화는 아직 구현하지 않았다. split 연결 커널의 QEMU 부팅·기본 TCP 송수신 검증을 완료했다. SACK·TLP·RTO의 선정된 실제 분할 경로도 검증했다. 전체 회귀 매트릭스 완료는 아니다.

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
| `tcp_insert_write_queue_after()` | RTX 분기 직접 트리 삽입 | V1에서는 `rtx_insert_ordered`; 후속 split 연결에서는 write queue 전용으로 축소 |
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

V1에서는 `tcp_insert_write_queue_after()`의 RTX 분기만 ordered insert로 연결했다. 후속 split 연결에서는 이 helper를 write queue 전용으로 축소하고, RTX 분할은 `tcp_fragment()`에서 prepare/commit으로 직접 처리한다. `tso_fragment()`도 단순화한 helper를 사용한다.

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

후속 TREE split에는 우선 트리 전용 일반 ordered insert를 사용하는 방안을 B가 권고했다. 논리적 다음 항목의 자리가 항상 현재 노드의 빈 오른쪽 자식은 아니다. tail 직접 연결 최적화를 중간 분할에 그대로 적용하면 안 된다. 이 선택은 A의 PR #9에서 내부 `__rtx_tree_insert()`로 반영됐다.

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

## 5. RB split 연결과 확정 계약

A의 [PR #9](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/9)에서 split API와 계약이 제공됐다. B는 이를 `tcp_fragment()`에 연결했다. 계약의 기준은 `include/net/tcp_rtx.h`이며, 아래 서명은 DEBUG 필드를 생략한 요약이다.

### 5.1 제공된 인터페이스

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

`domain`은 prepare 당시 TREE 또는 LIST 소속을 나타낸다. DEBUG 빌드에서는 원래 `seq`, `end_seq`를 추가 저장한다. 소속 상수는 `RTX_DOMAIN_TREE`/`RTX_DOMAIN_LIST`, 트랜잭션 상태는 `tcp_sock.rtx_txn_open`이다. 현재 backend는 TREE만 사용한다. token은 소켓 직렬화 구간 안의 임시 상태이며 skb 참조 횟수를 늘리지 않는다.

### 5.2 tcp_fragment의 연결 위치

1. 기존 할당·unclone 실패 처리를 완료한다.
2. `Correct the sequence numbers` 직전에 RTX 큐일 때만 prepare를 호출한다.
3. 기존 데이터 분할, 시퀀스·플래그·pcount·wmem 처리를 수행한다.
4. 현재 `tcp_insert_write_queue_after()` 호출 위치에서 RTX 큐이면 commit으로 연결한다. Write queue 분기는 기존 연결을 유지한다.
5. 기존 `tcp_tsorted_anchor` 연결을 수행한 뒤 전체 상태가 안정된 지점에서 검증한다.

현재 소스에는 prepare 이후 오류 반환 경로가 없다. 따라서 이 경로를 유지한다면 abort API가 필요하지 않다는 판단이다. 후속 변경이 실패 가능한 연산이나 조기 반환을 추가하면 이 전제를 다시 검토해야 한다.

RTX 분할은 `tcp_fragment()`의 공통 경로를 사용한다. 직접 호출 지점은 `tcp_output.c` 2곳, `tcp_input.c` 2곳, `tcp_recovery.c` 1곳이다. B의 함수 연결이 C의 SACK·RACK 동작에도 영향을 주므로 통합 검증이 필요하다.

### 5.3 commit 전제와 동작

확정 계약의 전제는 원래 skb의 `seq`가 유지되고, `skb.end_seq == buff.seq`이며, `buff.end_seq`가 원래 끝값과 같다는 것이다. 새 `buff`는 시퀀스 큐에 아직 연결되지 않아야 한다.

| prepare 당시 소속 | commit 동작 |
| --- | --- |
| TREE | 현재 RB 구현은 내부 tree-only ordered insert로 삽입한다. tree_last 캐시 갱신은 향후 캐시 구현 시 적용한다. |
| LIST (미구현) | 향후 원래 skb 바로 뒤에 buff를 연결하고, K 초과 시 리스트 head를 승격한다. |

K개 상태의 리스트 head를 분할하면 원래 skb 자신이 트리로 승격될 수 있다. commit 이후 호출자는 이전 소속을 가정하지 않는다. token은 commit 후 무효가 된다.

### 5.4 요청사항 반영과 호출부 검토

- prepare~commit 사이에는 공개 `rtx_*`, 순회 매크로와 이를 사용하는 wrapper를 호출하지 않는다.
- commit은 공개 ordered insert 대신 내부 트리 전용 삽입을 사용한다.
- DEBUG 검사는 WARN으로 탐지만 한다. commit은 경고 때문에 연결을 생략하지 않는다.
- buff 미연결 검사는 공유 링크 필드가 아니라 내부 시퀀스 포함 검색으로 수행한다.
- 전체 TCP 상태 검증은 시간 큐 연결 이후에 해야 한다. 아직 없는 `rtx_validate()`는 호출하지 않는다.
- B는 RTX 분기에서만 prepare/commit을 호출하고 기존 RTX 삽입을 대체했다. commit 뒤에 token을 재사용하지 않는다.
- write queue와 `tso_fragment()`는 token 없이 기존 `__skb_queue_after()` 연결을 유지한다.
- `tcp_skb_fragment_eor`, `skb_split`과 하위 분할·zerocopy 처리, `tcp_fragment_tstamp`, `tcp_set_skb_tso_segs`, `tcp_adjust_pcount`와 회계 검사, `__skb_header_release`를 검토했다. 해당 구간에 공개 RTX API 호출이나 `tcp_fragment()`에서의 오류 반환은 없다. 새 buff에 대한 기존 zerocopy 처리 의미도 변경하지 않는다.

### 5.5 B 연결 검증 (2026-11-11)

기준 HEAD는 `4fe3558d5e232aecc1c362f1c0457bc2c4c5516c`이며, 비교 소스는 해당 HEAD와 B의 `tcp_output.c` 패치만으로 준비했다. 작업 디렉터리의 무관한 변경은 비교 소스에서 제외했다.

| 검증 | 결과 |
| --- | --- |
| `git diff --check` | 통과 |
| 코드 패치 `checkpatch.pl --no-tree` | 오류 0, 경고 0, check 0 |
| prepare/commit 경로·하위 함수 정적 검토 | 중복 삽입·금지 RTX 호출·중간 오류 반환 없음. write queue 분기와 기존 TCP 처리 순서 보존 |
| 변경 전 DEBUG off/on `net/ipv4/`, `net/ipv6/` 빌드 | 두 설정 모두 성공 |
| 변경 후 DEBUG off/on `net/ipv4/`, `net/ipv6/` 빌드 | 두 설정 모두 성공 |
| 빌드 진단 비교 | 각 빌드 오류 0, 기존 호스트 인증서 도구의 OpenSSL 3 deprecated 경고 14개. 소스 경로 정규화 후 전후 경고 동일, 새 경고 없음 |
| DEBUG 자체 테스트 포함 | on에서 `tcp_rtx_selftest.o` 빌드 및 비어 있지 않은 파일 확인. off에서는 빌드 로그에 없음 |
| 컨테이너 정리 | 작업용 `rtx-b-split-20261111` 제거, 동일 이름 잔존 컨테이너 없음 |

환경은 기존 `ubuntu:22.04` arm64 이미지(`962f6cadeae0`), Docker Linux, host GCC와 x86_64 cross GCC 모두 11.4.0이다. 커널 대상은 x86_64이며 `rtx_defconfig`에서 DEBUG만 off/on으로 설정하고 각각 `olddefconfig`를 실행했다. 네 빌드는 별도 출력 디렉터리에서 실행했다.

```sh
make O="$out" ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- rtx_defconfig
scripts/config --file "$out/.config" -e TCP_RTX_DEBUG # off 비교군은 -d
make O="$out" ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig
make O="$out" ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 net/ipv4/ net/ipv6/
```

로컬 증거는 Git 제외 경로 `rtx-project/.work/b-split-validation/`에 보존했다. `build.sh`/`run.sh`, 기준 SHA와 tar, 코드 패치, 네 설정 및 SHA-256, 설정·빌드 로그, `diagnostics.txt`, `static-review.txt`, 정리 로그를 포함한다. 컨테이너 내부 빌드 오브젝트는 정리 시 제거했다. 전체 커널 링크나 바이너리 동등성 검증은 수행하지 않았다.

이 부분 빌드 시점에는 실제 커널 부팅, 자체 테스트 실행, TCP 송수신·분할·재전송과 성능 측정은 미실행이었다. 후속 실행 결과는 5.6절을 참조한다. 빌드 성공만으로 런타임 정확성을 판단하지 않는다.

### 5.6 ② 부팅·기본 송수신 검증

2026-11-11, 커널 `7a7b4f9ac`의 DEBUG on 전체 빌드와 QEMU 부팅을 완료했다. A의 자체 테스트 495/0, IPv4 loopback payload·half-close 3건 및 reset 1건이 통과했고 새 WARN/OOPS/panic은 없었다. 전체 바이트 비교로 데이터와 길이를 검증했다. 실패한 테스트 harness와 의존성 수정 이력, 환경, 증거 및 재현 명령은 [B_RUNTIME_VALIDATION.md](B_RUNTIME_VALIDATION.md)에 기록했다. 작업용 컨테이너는 모두 제거했다.

이 결과는 기본 통신 smoke이며 실제 RTX split 경로 실행이나 성능 향상의 근거로 사용하지 않는다.

### 5.7 ③ 실제 분할·재전송 검증

게스트 TUN 패킷 peer와 Kprobe/kretprobe를 추가했다. SACK 중간 구간 분할 2회, TLP tail 분할 1회, TLP를 끈 RTO 재전송 분할 1회를 실제로 관찰했고 모두 성공 반환했다. 각 사례의 4,000바이트 데이터, 최종 ACK 이후 TCP_INFO의 unacked/sacked/lost=0, FIN 종료를 확인했다. trace 덮어쓰기·호출 누락·오류 반환을 거부하는 판정기도 추가했다.

재현 스크립트, 커널 이미지 체크섬, 실패 실행과 성공 실행의 로그 위치는 [실행 검증 문서](B_RUNTIME_VALIDATION.md)에 기록했다. 커널의 프로덕션 코드는 이번 단계에서 변경하지 않았다. 이 결과는 RACK, 모든 head/middle/tail 조합, FIN을 포함한 skb의 분할, wraparound, 할당 실패 주입 또는 시간 큐·힌트 전수 검증까지 포함하지 않는다.

## 6. 남은 작업과 완료 기준

| 순서 | 작업 | 담당·의존성 | 완료 기준 |
| --- | --- | --- | --- |
| 1 | V1 실제 커널 부팅·송수신 검증 | B·D, 지금 진행 가능 | 대상 커널 부팅 확인, payload 길이·해시와 종료 동작 확인 |
| 2 | 분할·재전송 시나리오 준비 | B, C·D 협업 | 각 시나리오가 의도한 분할 경로를 실행한다는 관찰 방법 확보 |
| 3 | RB용 split API 제공 완료 (#9) | A | 계약·RB 구현 제공. A 보고 자체 테스트 결과는 A 문서 참조 |
| 4 | B split 연결 구현, 실행 회귀 대기 | B·C·D | 정적·빌드 검증은 5.5절. 실제 분할과 TCP 회귀 통과는 별도 필요 |
| 5 | 리스트와 승격 구현 통합 | A·B·C | K 경계, 원래 skb 승격, TREE/LIST 경계 분할에서 순서·캐시·힌트 검증 |
| 6 | merge·trim·replace 계약과 연결 | A·B·C | token, 구조 제거, 시간 큐·해제 순서와 순회 재개 위치 확정 |
| 7 | 캐시 및 tail 직접 연결 | A의 캐시 계약, B의 삽입 최적화 | 빈 트리·일반 삽입·삭제·교체에서 캐시 일치, RB 균형 유지 |
| 8 | 비용 및 회귀 비교 | B·D, 통합 정확성 선행 | V1·캐시·tail·하이브리드의 효과를 분리해 측정 |

1번과 2번은 A의 하이브리드 완성을 기다리지 않는다. 4번의 API 의존성은 #9로 해소됐으며, 호출부 연결 이후의 실행 검증이 남아 있다.

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
