# TCP 재전송 큐 프로젝트 — 구체 설계 및 역할 분담

작성일: 2026-10-26 · 리눅스응용설계 · 4인 · 4~6주 · 대상 Linux **v5.4.214**

> 문서 상태: **소스 검증을 바탕으로 한 구현·실험 계획**. 하이브리드 구현, 커널 빌드·부팅, 성능 측정을 완료했다는 뜻이 아니다. 아래에서 **확인 사실**, **제안 설계**, **검증할 가설**을 구분한다. 성능 개선 수치는 아직 없다.

## 1. 결정과 프로젝트 목표

기존의 “어댑터 + B·C 독립 기여” 분담을 유지한다. 구체적인 본 실험 구조는 **오래된 시퀀스 구간의 rbtree(T) + 최근 구간의 길이 제한 이중 연결 리스트(L)**로 정한다. 한 skb는 T 또는 L 중 **한 곳에만** 속한다. 전체 논리 큐의 순서는 `T의 중위 순회 → L의 순서 순회`다.

리스트 길이 상한 K의 최초 후보는 **8 skb**, 탐색 후보는 4·8·16이다. 이는 최적값이라는 사실이 아니라 사전에 고정할 실험 변수다. 데이터 패킷 수나 바이트 수가 아니라 **skb 노드 수**를 센다. GSO skb 하나는 여러 TCP 세그먼트를 담을 수 있다.

핵심 가설은 “K개 이내의 최근 skb가 트리로 옮겨지기 전에 ACK되어 제거되는 흐름에서는 트리 삽입·삭제를 피할 수 있다”이다. **항상 작은 큐에서 이기거나 큰 큐에서 손해가 없다고 보장하지 않는다.** 큰 큐가 지속되면 새 삽입마다 오래된 리스트 노드를 트리로 옮기므로 비용을 뒤로 미룬 것에 그칠 수 있다. 이 경우에는 하이브리드를 중단하고 비교군의 결과를 주 결과로 제출한다.

본 프로젝트의 산출물은 패치, 어댑터 계약, 불변식 검증기, 재현 가능한 실험, 결과 보고서다. “개선 없음”도 원인을 측정으로 설명하면 유효한 결과다. 성능 향상 자체를 사전에 확정하지 않는다.

## 2. 실제 소스로 확인한 출발점

### 2.1 검증 버전과 범위

Linux stable의 `v5.4.214` 태그는 커밋 **`9f02aa34e179d1b0185a27980dd5bafd0f2e6e59`**로 확인했다. 본 문서의 기준선은 이 커밋이다. 배포판이 별도 패치를 적용한 “5.4.214”라면 같은 기준선으로 취급하지 않는다. [태그 커밋](https://github.com/gregkh/linux/commit/9f02aa34e179d1b0185a27980dd5bafd0f2e6e59)

아래 파일의 해당 태그 원문을 직접 열어 확인했다. 다만 **전체 저장소의 모든 호출 지점과 모든 설정 조합을 감사한 것은 아니다**. 1주차에 전체 checkout 검색으로 호출 지점 목록을 완성해야 한다.

| 확인 사실 | 소스 및 의미 |
| --- | --- |
| 재전송 큐의 루트는 `struct sock`의 `struct rb_root tcp_rtx_queue`다. `sk_send_head`와 union을 이룬다. | [include/net/sock.h, 411행](https://github.com/gregkh/linux/blob/v5.4.214/include/net/sock.h#L411). `tcp_sock`에 이미 리스트+트리가 있다는 전제는 틀리다. |
| `tcp_rtx_queue_head()`와 `tail()`은 각각 `skb_rb_first()`·`skb_rb_last()`를 호출한다. | [include/net/tcp.h, 1758행](https://github.com/gregkh/linux/blob/v5.4.214/include/net/tcp.h#L1758). 이 태그의 helper 자체는 head/tail 캐시가 아니다. |
| 새로 보낸 데이터는 write queue에서 빠져 rtx tree에 들어간다. | [tcp_output.c, 72행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_output.c#L72). 두 큐의 의미를 혼동하면 안 된다. |
| `tcp_rbtree_insert()`는 루트부터 `before()`로 탐색한 뒤 `rb_link_node()`와 `rb_insert_color()`를 호출한다. | [tcp_input.c, 4933행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L4933). 새 데이터가 논리적으로 tail에 온다고 해서 기존 구현이 O(1) 삽입인 것은 아니다. |
| SACK의 `tcp_sacktag_bsearch()`는 `seq <= q < end_seq`에 해당하는 skb를 찾고 없으면 NULL을 반환한다. | [tcp_input.c, 1606행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L1606). 일반적인 lower_bound와 의미가 다르다. |
| SACK 검색 뒤에는 순회·분할·shift·병합·힌트 갱신이 이어진다. | [tcp_sacktag_walk(), 1534행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L1534). 검색 함수 하나만 바꾸면 끝나지 않는다. |
| 누적 ACK 처리는 첫 노드에서 출발하고, 부분 ACK이면 TSO skb의 앞부분을 trim할 수 있다. | [tcp_clean_rtx_queue(), 3093행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L3093), [tcp_tso_acked(), 3051행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L3051). |
| `skb->next/prev`, `skb->list`, `skb->rbnode`는 같은 union에 있다. | [include/linux/skbuff.h, 685행](https://github.com/gregkh/linux/blob/v5.4.214/include/linux/skbuff.h#L685). 같은 skb를 이 필드들로 리스트와 트리에 동시에 연결하면 메모리가 덮어써진다. |
| `tcp_tsorted_anchor`는 별도 union이며 destructor·refdst 저장 공간과 겹친다. | [skbuff.h, 722행](https://github.com/gregkh/linux/blob/v5.4.214/include/linux/skbuff.h#L722), [tcp.h, 1739행](https://github.com/gregkh/linux/blob/v5.4.214/include/net/tcp.h#L1739). 시퀀스 리스트용 여유 필드가 아니다. |
| RACK은 `tsorted_sent_queue`를 사용한다. | [tcp_recovery.c, 85행](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_recovery.c#L85). 전송 시간 순서와 시퀀스 순서는 별개다. |
| `rb_root_cached` 및 cached insert/erase API는 이미 제공된다. | [include/linux/rbtree.h, 125행](https://github.com/gregkh/linux/blob/v5.4.214/include/linux/rbtree.h#L125). 이것은 **왼쪽 끝** 캐시이며 tail 캐시까지 자동 제공하지 않는다. |

### 2.2 원안에서 바로잡는 설명

- `tcp_transmit_skb()` 전체 지연을 재전송 큐 삽입 비용으로 해석하지 않는다. 기준 소스에서 새 데이터의 큐 이동은 `tcp_event_new_data_sent()`에 있고, 분할·SYN/TFO 등에도 별도 삽입이 있다.
- SACK은 보통 해당 skb를 “SACK되었다”고 표시한다. **SACK 수신을 임의 중간 노드 삭제와 동일시하지 않는다.** 실제 제거는 누적 ACK, 병합/shift, 정리 등 각 경로의 기존 의미를 따른다. 수신자가 SACK을 철회하는 reneging도 고려한다.
- `ss -ti`의 unacked 또는 `packets_out`은 실제 rtx **skb 개수**를 대체하지 못한다. “unacked 수천”을 큐 길이 게이트로 사용하지 않는다.
- inline helper에는 호출 가능한 독립 심볼이 없을 수 있다. `bpftrace`가 내부 rb 삽입 구간을 자동으로 정확히 분리해 준다고 쓰지 않는다.
- head 캐시, tail 위치 탐색 생략, 하이브리드는 서로 다른 변경이다. 각각의 효과를 분리한다.

### 2.3 최신 upstream 중복 여부: 확인한 것과 남은 것

작성 시점에 조회한 torvalds/linux `master`는 **`6b4b543fa18757297ac756a6fe6e64f506b7844a`**였다. 움직이는 master 대신 이 SHA를 비교 기록에 남긴다. 이 커밋의 `include/net/sock.h`, `include/net/tcp.h`, `include/linux/tcp.h`, `tcp_output.c`, `tcp_input.c`를 열어 다음을 확인했다.

- rtx 루트는 여전히 일반 `rb_root`이며 head/tail helper는 first/last 접근이다.
- `tcp_rbtree_insert()`는 현재 `tcp_output.c`에 있고 루트부터 탐색한다. 새 데이터 삽입도 이 함수를 호출한다.
- 확인한 코드에는 본 문서의 “bounded 최근 리스트 + 오래된 트리” 구현이 보이지 않았다.

근거: [upstream tcp.h](https://github.com/torvalds/linux/blob/6b4b543fa18757297ac756a6fe6e64f506b7844a/include/net/tcp.h#L2277), [upstream sock.h](https://github.com/torvalds/linux/blob/6b4b543fa18757297ac756a6fe6e64f506b7844a/include/net/sock.h#L488), [upstream 삽입 구현](https://github.com/torvalds/linux/blob/6b4b543fa18757297ac756a6fe6e64f506b7844a/net/ipv4/tcp_output.c#L63).

이것은 **독창성의 증명이나 미병합 패치·논문 전체 조사 결과가 아니다**. 2주차까지 upstream 이력, stable 백포트, 공개 제안에서 같은 설계를 조사한다. 동일 변경을 찾으면 출처와 차이를 명시하고, 독립 개선 주장을 철회하거나 재현·비교 연구로 과제 방향을 조교와 조정한다.

## 3. 선택한 하이브리드 자료구조

### 3.1 논리 배치

```text
낮은 시퀀스/오래된 데이터                           높은 시퀀스/최근 데이터
T: rbtree [a, b, c, ...]                  L: [x] <-> [y] <-> [z]
           tree_first / tree_last              list head / tail

전체 큐 = inorder(T) 이어서 L
len(L) <= K, 각 skb는 T 또는 L 한 곳에만 존재
L이 K를 초과하면 L의 첫 노드를 T의 가장 오른쪽으로 이동
```

T와 L은 겹치지 않는 **시퀀스 구간**이다. 트리 노드와 리스트 노드를 임의로 교차 배치하지 않는다. 큐가 작다고 트리의 노드를 다시 리스트로 끌어오지 않는다. ACK로 트리가 자연스럽게 비워지면 리스트만 남는다. 따라서 `N <= K`여도 이전 이력에 따라 트리 노드가 남을 수 있다.

### 3.2 물리 저장과 메모리 비용

**제안 필드**는 `struct tcp_sock`에 추가한다. 기존 `sk->tcp_rtx_queue`는 T의 루트로 유지한다. `struct sock`의 union을 확대하지 않아 다른 프로토콜의 공통 구조체 크기 변경을 피한다.

```c
/* 제안 예시: 기존 커널에 존재하는 필드가 아님 */
struct list_head rtx_recent;       /* skb->list를 연결, 시퀀스 순서 */
struct sk_buff *rtx_tree_first;    /* T의 최소 노드, 비면 NULL */
struct sk_buff *rtx_tree_last;     /* T의 최대 노드, 비면 NULL */
u32 rtx_recent_len;               /* skb 수, TCP segment 수 아님 */
```

- 전체 head = `rtx_tree_first`가 있으면 그것, 없으면 리스트 첫 skb. 전체 tail = 리스트가 비지 않았으면 리스트 마지막 skb, 없으면 `rtx_tree_last`. 조회는 O(1)이다.
- 별도의 전체 head/tail 포인터를 중복 저장하지 않는다. 리스트 sentinel 자체가 양 끝을 가진다.
- 64비트 환경에서 위 필드의 명목 크기는 36바이트이고 정렬을 포함하면 흔히 40바이트가 필요하지만, 실제 `sizeof(struct tcp_sock)` 및 slab 크기 변화는 빌드 설정과 배치로 측정한다. 연결 수에 따른 총 비용도 제시한다.
- 새 per-skb 필드, wrapper 할당, `skb->cb` 확장은 하지 않는다. `cb[48]`은 `TCP_SKB_CB`가 쓰며 공짜 공간으로 가정하지 않는다. 소켓 필드 증가도 비용이므로 숨기지 않는다.
- L에서는 `skb->list`만 사용하고 T에서는 `skb->rbnode`만 사용한다. `tcp_tsorted_anchor`와 `tsorted_sent_queue`의 기존 관리는 별도로 유지한다.
- 전송용 clone/copy가 네트워크 계층으로 내려갈 때의 기존 `dev = NULL` 및 tsorted save/restore 처리를 보존한다. [기준 clone 처리](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_output.c#L1045)

### 3.3 소속 판별과 변이 중 규칙

안정된 큐에 **현재 포함된 skb**만 입력으로 받는 `rtx_domain(sk, skb)`를 둔다.

1. L이 비었으면 TREE다.
2. `before(seq(skb), seq(first(L)))`이면 TREE, 아니면 LIST다.
3. debug 빌드에서는 실제 포인터 순회 결과와 대조한다.

이 O(1) 판별은 시퀀스 구간 분리와 고유 시작 시퀀스 불변식에 의존한다. `RB_EMPTY_NODE()`로 소속을 추정하지 않는다. 리스트 포인터를 rbnode로 읽는 것도 금지한다.

분할·shift·병합 도중에는 길이가 0이 되거나 이웃과 시작 시퀀스가 잠시 같아질 수 있다. **변경 전에 각 skb의 소속과 필요한 이웃을 저장한 지역 transaction token**을 사용한다. 변이 중에는 일반 소속 판별·검색·검증기를 호출하지 않고 token을 받는 내부 연결 연산을 쓴다. commit 뒤에만 공개 API로 돌아온다. 이 규칙을 호출 경로에 적용하기 어렵다면 2주차 No-Go로 판단한다.

token은 구조와 이웃의 임시 정보일 뿐 새 참조 카운트나 장기 iterator가 아니다. 소켓 lock 밖으로 가져가지 않는다. 이미 제거되었거나 해제된 skb로 소속을 물어보는 것은 계약 위반이다.

## 4. 불변식

아래 조건을 **공개 연산 완료 시점**에 만족시킨다. TCP의 다단계 변경 내부에서는 transaction 규칙을 적용한다.

| 번호 | 불변식 |
| --- | --- |
| I1 소유권 | 각 재전송 skb는 T 또는 L에 정확히 한 번 존재한다. 미전송 write queue와 rtx 큐에 동시에 존재하지 않는다. 링크 이동은 skb 복제·해제가 아니다. |
| I2 순서 | 각 구간과 전체 순서가 TCP 직렬번호 순서로 증가한다. T의 모든 노드는 L의 모든 노드보다 앞선다. 정상 데이터·SYN·FIN 구간의 시작값은 고유하다. |
| I3 범위 | 각 노드는 `[seq, end_seq)`이며, 이웃 범위가 겹치지 않는다. 연속 데이터에서는 이전 end와 다음 seq가 일치한다. 자료구조 검색은 구멍도 안전하게 처리한다. SYN/FIN은 payload가 0이어도 시퀀스를 소비하므로 `skb->len == 0`만으로 빈 구간이라 판단하지 않는다. |
| I4 용량 | 완료 시 `0 <= rtx_recent_len <= K`. append나 split 내부의 K+1은 lock을 유지한 채 즉시 승격으로 해소한다. |
| I5 캐시 | T가 비면 tree_first/tree_last 둘 다 NULL, 아니면 실제 rb_first/rb_last와 같다. 전체 head/tail 정의가 실제 논리 순서와 같다. 마지막 삭제 후 리스트와 캐시가 모두 빈 상태다. |
| I6 수명·힌트 | highest_sack, lost_skb_hint, retransmit_skb_hint가 기존 의미를 유지하며 해제된 skb를 가리키지 않는다. highest_sack은 단순히 “마지막 SACK skb”가 아니라 그 다음 skb를 나타내는 기존 계약을 보존한다. |
| I7 회계 | packets_out, sacked_out, lost_out, retrans_out, GSO pcount, wmem/truesize 관련 처리는 기존 TCP 의미를 따른다. 구조 간 이동만으로 이 값들을 바꾸지 않는다. |
| I8 시간 순서 | 시퀀스 큐의 이동 때문에 tsorted anchor를 제거·초기화하지 않는다. SACK/재전송/해제에서 요구하는 기존 시간 큐 처리는 유지한다. |
| I9 동시성 | 기존 TCP socket 직렬화 아래서만 구조를 읽고 쓴다. softirq의 기존 lock/backlog 규칙을 보존한다. 독자적인 lock-free 구조나 새 중첩 lock을 도입하지 않는다. |
| I10 직렬번호 | 비교 대상의 유효 순서 범위는 2^31 미만의 공통 직렬번호 구간에 있어야 한다. raw unsigned `<`, `min`, `max`로 순서를 비교하지 않는다. |

`before(a,b)`는 기준 태그에서 `(__s32)(a-b) < 0`으로 구현되어 있다. modulo 2^32 wrap은 지원하지만, 거리 2^31 이상인 임의 숫자 집합에 대해 전역 순서를 만들어 주지는 않는다. 유효 ACK/SACK 검사와 송신 윈도 제한을 유지하고, 단위 모델은 이 전제를 명시한다. 현재 `snd_una` 기준 숫자로 key를 다시 매기며 트리를 재구성하는 방식은 사용하지 않는다. [직렬번호 비교 소스](https://github.com/gregkh/linux/blob/v5.4.214/include/net/tcp.h#L268)

## 5. 알고리즘과 복잡도

N은 전체 skb 수, T는 트리 노드 수, L은 리스트 노드 수(≤K), m은 검색 결과로 순회하는 노드 수다. 아래는 자료구조 비용이며 payload 복사, skb 할당, TCP 회계, 패킷 전송 비용은 별도다. 로그는 빈 트리도 표현하도록 `log(T+1)`로 쓴다.

### 5.1 새 데이터 append와 승격

1. 기존 write queue에서 분리된 skb 또는 적법하게 새로 준비한 skb를 받는다.
2. 새 시작 시퀀스가 전체 tail 뒤이고 범위가 겹치지 않는지 확인한다. 일반 새 데이터는 연속성도 확인한다.
3. L의 tail에 연결하고 L 길이를 증가시킨다.
4. K를 초과하면 **L의 head 한 개**를 리스트에서 분리한 뒤 T로 승격한다.
5. 승격 노드는 T의 모든 기존 노드 뒤이므로 tree_last의 비어 있는 right child 자리에 `rb_link_node()`한다. 빈 트리이면 루트에 연결한다. 반드시 `rb_insert_color()`를 실행하고 캐시를 갱신한다.

승격할 때는 리스트 링크를 제거한 **뒤** rbnode를 초기화·연결한다. 일반 TCP unlink helper가 수행하는 tsorted cleanup이나 free를 호출하면 안 된다. skb 주소와 데이터는 그대로다.

승격 없는 append는 O(1), 승격이 있으면 최악 O(log(T+1))이다. tail 위치 탐색을 생략해도 RB 균형 복구 비용은 남는다. 긴 steady-state 큐에서는 거의 모든 append에 승격이 발생하므로 “삽입 전체가 상각 O(1)”이라는 주장을 하지 않는다.

새 노드가 tail 뒤가 아니면 append로 처리하지 않는다. 분할·교체용 API 또는 일반 ordered insert를 사용한다. 일반 ordered insert는 L이 비어 있으면 트리에, L이 있으면 경계 이전은 트리에 O(log(T+1)), 이후는 최대 K개를 찾아 리스트에 O(K)로 삽입한 뒤 상한을 복구한다. 중복 시작 시퀀스·겹침은 허용하지 않는다. 모든 트리 삽입은 최초 노드 여부와 최소·최대 갱신 여부를 처리한다. 같은 위치의 skb 교체도 캐시가 old를 가리키면 new로 갱신한다.

### 5.2 순서 순회

- `first`: tree_first 또는 first(L).
- `last`: last(L) 또는 tree_last.
- `next`: TREE이면 `rb_next`; 트리 끝이면 first(L). LIST이면 리스트 다음 노드; sentinel이면 NULL.
- `prev`: LIST의 첫 노드이면 tree_last, 그 외에는 리스트 이전 노드. TREE이면 `rb_prev`.
- 전환 시 저장했던 rb_node 포인터를 계속 사용하지 않는다. iterator의 기준은 살아 있는 skb 포인터와 현재 구조다.

트리의 next/prev 한 번은 최악 O(log(T+1)), 리스트는 O(1)이다. 변이가 없는 전체 중위 순회는 O(N), 연속된 m개 구간 순회는 시작 위치 비용을 포함해 O(log(T+1)+m)으로 볼 수 있다. 매 노드마다 루트 검색을 다시 하지 않는다.

### 5.3 SACK: 점 포함 검색과 범위 검색을 구분

기존 `tcp_sacktag_bsearch(q)`를 대신하는 **`rtx_find_cover(q)`**는 `!before(q, seq) && before(q, end_seq)`를 만족하는 skb만 반환하며 없으면 NULL이다. 단순 lower_bound로 바꾸지 않는다.

```text
find_cover(q):
  L이 있고 q가 first(L).seq보다 앞서지 않으면:
      L을 최대 K개 순회해 q를 포함하는 노드를 찾는다
      만나지 못하면 NULL
  그 외:
      기존 tcp_sacktag_bsearch와 동일한 분기로 T를 검색한다
```

T와 L의 경계보다 작은 q는 L 내부에 포함될 수 없고, 경계 이상인 q는 T에 포함될 수 없다. 따라서 한 쪽 검색만으로 충분하다. 비용은 O(log(T+1)+K) 상한이며 실제로는 선택한 한 쪽의 비용이다.

별도 **`rtx_first_overlap(start,end)`**는 임의 `[start,end)`와 겹치는 첫 노드를 찾는 검증·일반 범위 API다. `end_seq > start`인 첫 노드를 구하고 그 노드의 `seq < end`도 확인한다. T에서는 겹치지 않는 정렬 구간의 end도 증가한다는 성질을 이용해 후보를 보존하는 lower-bound 탐색을 한다. T에서 후보가 없으면 L을 최대 K개 조사한다. 반환 이후 `next()`로 `seq < end`인 동안 진행한다. 범위가 T와 L을 가로질러도 한 번씩 방문한다. 탐색·순회 비용은 **O(log(T+1)+K+m)**이다.

예: `[100,200)`을 가진 skb에서 q=200은 포함되지 않는다. 다음 skb가 `[200,300)`이면 그것을 반환한다. 다음 시작이 250이면 `find_cover(220)=NULL`이지만 `first_overlap(220,280)`은 `[250,300)`을 반환한다. 이 차이를 테스트한다.

실제 SACK 통합에서는 기존 `tcp_sacktag_skip()`의 cursor 재사용 조건, SACK block 정렬·검증·캐시·DSACK 처리를 유지하고 포함 검색만 어댑터로 바꾼다. 범위 API를 기존 포함 검색 자리에 무조건 끼우지 않는다. 부분 겹침은 기존 `tcp_match_skb_to_sack()` 및 분할/shift 정책이 처리한다. `find_cover`가 NULL이라고 리스트를 트리로 모두 옮기는 우회책은 사용하지 않는다.

**검색 비용과 SACK 전체 처리 비용은 다르다.** 범위 내에서 s번 split/merge 또는 승격이 발생하면 그 변이 비용과 payload 처리 비용이 추가된다. 전체 SACK 처리를 무조건 O(log N+m)이라고 쓰지 않는다.

### 5.4 삭제와 ACK

- LIST 삭제: 소속을 이미 확인한 skb의 `list_del`, L 길이 감소. O(1).
- TREE 삭제: 필요하면 삭제 전에 rb_next/rb_prev로 tree_first/tree_last의 대체값을 구하고 `rb_erase`. 최악 O(log(T+1)). head 캐시는 **조회**를 O(1)로 만들 뿐 트리 삭제를 O(1)로 만들지 않는다.
- 누적 ACK: 전체 head부터 기존 ACK 판정으로 진행한다. 완전히 ACK된 skb는 힌트·시간 큐·회계 처리를 거쳐 제거한다. 부분 ACK는 기존 trim 조건을 따른다. 리스트 노드가 ACK되면 승격 없이 바로 제거할 수 있다.
- SACK된 중간 skb는 원칙적으로 유지한다. 합법적인 shift/병합으로 불필요해진 노드만 기존 정책대로 제거한다.
- safe iterator는 “현재 노드만 삭제”할 때만 충분하다. SACK shift는 현재와 다음 skb까지 없앨 수 있으므로 기존 함수의 반환 cursor를 존중한다. 이 위험은 기준 소스에도 주석으로 명시되어 있다. [SACK 순회 주석](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L1560)

### 5.5 분할, trim, shift, 병합, 교체

| 연산 | 구조 변경 계약 |
| --- | --- |
| split `[a,c) → [a,b), [b,c)` | 기존 skb는 앞부분, 새 skb는 뒷부분이다. 기존 소속을 먼저 저장한다. TREE에서 split하면 새 노드도 TREE로 ordered insert한다. LIST에서 split하면 바로 뒤에 붙이고 K 초과 시 가장 앞 노드를 승격한다. 끝값·FIN/PSH·pcount·wmem은 기존 `tcp_fragment()` 로직을 보존한다. |
| head trim `[a,c) → [b,c)` | 시작값만 증가하고 이웃 순서를 넘지 않으면 구조상의 위치를 유지할 수 있다. 저장한 소속에 따라 처리하고 종료 때 순서·경계 검증을 한다. TCP 카운터와 GSO 크기는 원래 코드가 담당한다. |
| partial shift | 이전 skb의 end와 현재 skb의 seq를 함께 증가시킨다. 경계를 사이에 둔 TREE→LIST 쌍도 허용한다. 소속을 먼저 저장하고 transaction 내에서는 일반 검색을 하지 않는다. 살아남는 두 노드의 순서는 그대로다. |
| full merge/shift | 이전 노드가 범위를 흡수하고 뒤 노드는 제거된다. 이전 노드의 소속을 유지한다. TREE 마지막 + LIST 첫 노드 병합이면 L 길이가 감소하고 경계가 다음 리스트 노드로 이동한다. FIN, timestamp, SACK 상태, rate 정보, 회계, 힌트 갱신 순서를 보존한다. |
| clone 교체 | SYN 재전송 등에서 skb 주소가 교체될 수 있다. 기존 위치·소속을 저장하고 새 skb를 같은 논리 위치에 연결한 뒤 힌트를 기존 규칙으로 바꾼다. 교체 중 NULL 또는 freed 포인터가 남지 않도록 한다. |
| 일반 rekey | 이웃 순서를 바꾸는 key 변경은 노드를 분리하고 다시 삽입한다. 정상 trim/shift와 구분한다. 1차 범위는 기존 TCP 호출 경로가 요구하는 변이만 지원한다. |

split의 새 skb 할당이나 기존 copy/unclone 실패는 **기존 커널의 오류 처리를 보존**한다. 자료구조의 연결을 먼저 끊고 할당에 실패하는 순서를 만들지 않는다. 성공 전 구조는 기존 상태를 유지하고, 성공 후 커밋되는 링크 변경 자체에는 추가 메모리 할당이 없다. payload 변경의 롤백 가능성을 근거 없이 가정하지 않는다.

이 부분의 기준은 [tcp_fragment()](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_output.c#L1306), [tcp_shifted_skb()](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c#L1284), [tcp_collapse_retrans()](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_output.c#L2805)다. 어댑터가 이 함수들의 TCP 의미를 새로 구현하지 않는다.

### 5.6 wraparound 예

`[0xfffffff0, 0x00000010)` 다음에 `[0x00000010, 0x00000030)`이 올 수 있다. 첫 범위의 길이는 u32 차로 32이고, q=`0x00000005`는 첫 노드에 포함된다. q=`0x00000010`은 두 번째 노드에 속한다. split과 승격 전후 결과가 같아야 한다. 경계 판별도 `before/after`로 수행한다.

유효하지 않은 SACK 범위는 기존 validator에서 거른다. 자료구조 API가 네트워크에서 받은 임의 숫자를 먼저 신뢰해서는 안 된다. 정확히 2^31 차이가 나는 입력은 순서 정의가 모호하므로 모델 테스트에서 정상 입력으로 인정하지 않는다.

### 5.7 비용 요약과 이득이 날 조건

| 연산 | 기존 일반 rbtree | 제안 하이브리드 |
| --- | --- | --- |
| head / tail 조회 | 최악 O(log N) | O(1) |
| 순차 신규 append | O(log N) | 승격 없으면 O(1), 있으면 O(log(T+1)) |
| 알려진 노드 삭제 | O(log N) | LIST O(1), TREE O(log(T+1)) |
| 포함 검색 | O(log N) | O(log(T+1)+K) 상한 |
| 범위 검색+변이 없는 순회 | O(log N+m) | O(log(T+1)+K+m) |
| 전체 변이 없는 순회 | O(N) | O(N) |
| split의 링크 처리 | O(log N) | TREE O(log(T+1)), LIST 승격 없으면 O(1), 있으면 O(log(T+1)) |
| 추가 저장 공간 | 기존 skb의 rbnode | 소켓당 O(1), 새 per-skb 할당 없음 |

K를 고정하면 검색의 점근 복잡도는 유지되지만, 선형 비교 K번·분기·캐시 미스·승격 비용 때문에 실제로 느려질 수 있다. 긴 지연·고속 링크는 큐를 키우기 쉽지만, 동시에 최근 skb가 빨리 ACK될 가능성을 낮춰 하이브리드의 이점을 줄일 수 있다. “고 BDP이면 무조건 유리”라고 가정하지 않는다.

## 6. 대안과 선택 이유

| 대안 | 장점 | 비용·결정 |
| --- | --- | --- |
| 일반 rbtree + head/tail 캐시 | 변경 범위가 비교적 작고 검색 의미 유지 | 필수 비교군 및 범위 축소안. 조회 개선만으로 메인 하이브리드 기여를 주장하지 않는다. |
| `rb_root_cached` | 표준 leftmost 캐시 API 재사용 | tail은 별도 구현이 필요하고 `struct sock` 루트 타입 변경이 파급된다. 직접 캐시 비교군과 동일한 실험에 섞지 않고 별도 선택안으로 둔다. |
| rbtree + tail 직접 연결 | 순차 삽입의 위치 탐색 생략 | RB fixup은 남는다. 하이브리드보다 작고 유효한 대조군이다. |
| 전체 리스트와 전체 트리 동시 유지 | O(1) next/prev와 검색 인덱스 공존 | 현재 skb union으로 불가능. 새 링크 필드 또는 wrapper·할당·수명 관리가 필요하고 모든 노드에 트리 삽입 비용도 남는다. 학기 범위에서 제외. |
| 작은 큐는 리스트, 임계값 넘으면 전체 트리로 전환 | 구현 개념이 단순하고 per-skb 추가 필드 불필요 | 전환 순간 O(N log N), 반복 전환 시 진동. 택한다면 hysteresis가 필요하다. 본안은 한 노드씩 승격해 큰 일괄 전환을 피한다. |
| 제한 없는 리스트 suffix | append가 단순 | SACK 검색 O(N) 회귀를 막지 못해 제외. |
| 배열·ring·청크 인덱스 | 순회 지역성이 좋아질 가능성 | 중간 split/merge, 포인터 힌트 안정성, 확장/할당이 추가된다. 후속 연구로 남긴다. |

## 7. 어댑터 계약: A가 끝나기 전에 B·C가 일할 수 있게

다음 `rtx_*`는 **제안 API**이며 Linux 5.4.214의 기존 API가 아니다. 실제 기존 이름과 혼동하지 않도록 프로젝트 내부 접두사를 붙여도 된다. 함수 포인터 vtable 대신 빌드 시 선택하는 정적 helper로 시작한다. 런타임 backend 변경은 범위 밖이다.

```c
/* 인터페이스 초안: 자료형 세부는 1주차 공동 리뷰에서 고정 */
void rtx_init(struct sock *sk);
bool rtx_empty(const struct sock *sk);
struct sk_buff *rtx_first(const struct sock *sk);
struct sk_buff *rtx_last(const struct sock *sk);
struct sk_buff *rtx_next(const struct sock *sk, const struct sk_buff *skb);
struct sk_buff *rtx_prev(const struct sock *sk, const struct sk_buff *skb);
struct sk_buff *rtx_find_cover(struct sock *sk, u32 seq);
struct sk_buff *rtx_first_overlap(struct sock *sk, u32 start, u32 end);
void rtx_append(struct sock *sk, struct sk_buff *skb);
void rtx_insert_ordered(struct sock *sk, struct sk_buff *skb);
void rtx_unlink(struct sock *sk, struct sk_buff *skb);
void rtx_validate(struct sock *sk); /* debug/test 전용 */
/* split/shift/replace는 begin/commit 및 domain token 내부 helper 추가 */
```

| 계약 항목 | 확정할 의미 |
| --- | --- |
| lock | 호출자가 해당 경로의 기존 socket 직렬화를 확보한다. 어댑터는 sleep·lock 획득을 하지 않는다. |
| 반환·소유권 | 조회는 borrowed skb 포인터 또는 NULL. 추가 ref를 얻지 않는다. unlink는 구조에서만 빼고 free·TCP 회계·시간 큐 제거를 하지 않는다. |
| 기존 unlink와 차이 | 기존 `tcp_rtx_queue_unlink()`는 tsorted anchor cleanup도 수행한다. 기존 wrapper는 이 의미를 유지하되 내부 구조 제거만 `rtx_unlink`로 위임한다. 구조 간 승격은 wrapper를 호출하지 않는다. |
| cursor | 비변이 순회만 안정적으로 지원한다. 삭제 전 next 저장 또는 변이 함수가 반환한 cursor를 사용한다. split 후 새 노드 방문 여부를 호출자별로 명시한다. |
| NULL | first/last/find는 없으면 NULL. next/prev의 입력 skb는 유효한 큐 노드여야 한다. 임의 NULL 입력 허용 여부에 의존하지 않는다. |
| 삽입 | 미연결 skb가 전제다. append는 tail 뒤, ordered insert는 고유 시퀀스·비중첩이 전제다. TCP payload 할당 오류는 상위 함수가 처리한다. |
| 구조 변경 | transaction token은 변이 전 domain과 필요한 이웃을 보존한다. 실패 가능한 작업 전후의 commit 위치를 각 호출 지점별로 리뷰한다. |
| reset/purge | first/unlink를 반복하는 기준 구현으로 시작한다. 회계·free·tsorted 전체 초기화는 기존 purge의 책임이다. 마지막에 루트·리스트·캐시가 모두 비어야 한다. |
| backend 동등성 | RB backend와 hybrid backend의 조회·순회 결과, TCP 상태 전이가 동일해야 한다. 내부 T/L 분할은 호출자에게 노출하지 않는다. |

**1주차 RB backend**는 기존 head/tail, rb next/prev, insert/erase, SACK 포함 검색으로 구현한다. B·C는 이 버전으로 경로 변경과 테스트를 진행한다. 구조에 직접 접근하는 호출은 허용 목록으로 관리하고 최종에는 어댑터 내부와 명확한 디버그 코드만 남긴다. 어댑터만 도입한 빌드의 동작·성능을 먼저 확인해 추상화 자체의 비용을 분리한다.

## 8. 파일별 수정 범위와 빠뜨리기 쉬운 경로

| 파일 | 예상 변경·감사 범위 | 주 담당 |
| --- | --- | --- |
| `include/linux/tcp.h` | TCP 전용 리스트·캐시 필드. 크기·정렬·clone 후 초기화 검토 | A |
| `include/net/tcp.h` | 공통 어댑터, head/tail/empty/unlink wrapper, highest_sack advance의 next 교체, 선언 | A, C 리뷰 |
| `include/net/sock.h` | 기존 root의 의미 확인. 본안은 타입 변경 없이 유지. cached-root 대안을 선택할 때만 별도 수정 | A |
| `include/linux/skbuff.h` | union·cb·clone 제약 감사. 본안에서 레이아웃 변경 없음 | A+B |
| `net/ipv4/tcp_output.c` | 새 데이터, 재전송 순회, split, collapse, TLP의 직접 last/next, SYN/SYNACK 교체, TFO 삽입 경로 | B |
| `net/ipv4/tcp_input.c` | SACK bsearch/skip/walk, ACK clean, trim, shift/merge, 손실 순회, root prefetch, 힌트 및 직접 rb 접근 | C |
| `net/ipv4/tcp.c` | init, purge, disconnect 후 재사용, TCP repair 송신 큐 순회 | A+B |
| `net/ipv4/tcp_minisocks.c` | passive-open child 생성: 복제된 list sentinel과 캐시를 새 소켓 기준으로 초기화 | A |
| `net/ipv4/tcp_recovery.c`, `tcp_timer.c` | RACK 시간 큐 의미 유지, RTO/TLP가 논리 head와 empty를 사용하는지 검사 | C |
| `net/ipv4/tcp_ipv4.c`, `net/ipv6/` 관련 코드 | destroy/purge 및 IPv6 공유 경로, 소켓 생성/clone 관련 누락 검사. 필요한 곳만 수정 | B+C |
| `tools/testing/selftests/net/`, 별도 실험 디렉터리 | 사용자 공간 회귀·네트워크 조건·결과 수집. 내부 연산 테스트용 debug 코드의 빌드 연결은 별도 관리 | D |

`tcp_rbtree_insert()`는 수신 out-of-order 경로에도 쓰인다. **이 함수 전체를 송신 전용 하이브리드로 바꾸지 않는다.** 기존 함수는 유지하고 rtx 호출만 어댑터로 옮긴다. receive ofo tree와 write queue는 범위 밖이다.

전체 checkout에서 다음을 검색하고 각 결과를 “RTX 변경 / 다른 자료구조 유지 / 디버그”로 분류한 표를 제출한다.

```sh
git grep -n -E 'tcp_rtx_queue|tcp_rbtree_insert|skb_rb_(first|last|next|prev)|skb_rbtree_walk' -- include net
git grep -n -E 'highest_sack|lost_skb_hint|retransmit_skb_hint|tcp_tsorted_anchor' -- include net
git grep -n -E 'TCP_SKB_CB.*(seq|end_seq)' -- net/ipv4/tcp* net/ipv6/tcp*
```

필수 누락 방지 항목은 `tcp_advance_highest_sack()`의 직접 next, TLP의 직접 last, ACK의 직접 first, purge의 rb 순회, repair 순회, 초기화/child clone이다. 리스트 노드에 rb 연산이 적용되는 지점이 한 곳이라도 남으면 통합 No-Go다.

## 9. A/B/C/D 역할과 독립 기여

| 담당 | 구현·분석 책임 | 독립 산출물과 완료 기준 |
| --- | --- | --- |
| A — Core·통합 | 자료구조, 어댑터 RB/hybrid backend, 캐시·승격·불변식, init/clone/purge, 공통 헤더 관리 | 두 backend의 모델 동등성; split/merge 경계 검증; 소켓 크기 비용; 캐시 단독 비교 패치 |
| B — 송신·삽입 | 새 데이터·SYN/TFO·재전송·분할·collapse 경로, cached tail의 직접 연결 최적화 | 삽입 위치 탐색과 RB fixup 비용 분리; 순차/분할 삽입 microbenchmark; 실패 경로 및 전송 회귀 검증 |
| C — ACK/SACK·삭제/검색 | 포함/범위 검색, 두 구간 순회, ACK 제거, SACK shift/merge, 힌트 수명·wraparound | 검색 방문 노드 수·cycles, LIST 삭제/승격 전 ACK 비율, SACK/DSACK 및 부분 ACK 테스트. 트리 삭제 O(1) 주장은 금지 |
| D — 실험·재현성 | 빌드/부팅 자동화 유지, baseline, netem 실험, 반복·통계, 시스템 검증 | 동일 조건 반복 실행 도구, raw 결과·환경 manifest, CI/오류 로그, 노이즈 한계와 회귀 판정 |

설계는 네 명의 공동 결정이고 A 혼자 책임지지 않는다. B·C는 어댑터 호출 치환만 맡지 않고 각 연산의 가설·비교 패치·수치 해석을 책임진다. 성능이 안 나와도 회귀 원인과 한계를 분석한다. 각 구현자는 자기 코드의 테스트를 작성하고 D가 검증 방법을 리뷰한다.

**빌드 담당은 D, 백업은 A**로 명시한다. 첫 주에는 전원이 한 번씩 기준 커널을 빌드·부팅하고, 가장 먼저 성공한 절차를 D가 스크립트화한다. A는 공통 헤더와 통합 브랜치를 관리한다. B·C가 같은 파일을 수정하면 함수 단위 소유 범위를 정하고 공통 helper 변경은 A 리뷰를 받는다.

## 10. 비교군과 벤치마크

### 10.1 비교군

| 이름 | 구성 | 비교 목적 |
| --- | --- | --- |
| V0 | 무수정 v5.4.214 | 최종 기준선 |
| V1 | 어댑터만, 기존 RB backend | 호출 치환·추상화 비용 확인 |
| V2 | V1 + 명시적 head/tail 캐시 | 캐시만의 효과 |
| V3 | V2 + 검증된 tail 직접 연결 | 삽입 탐색 생략 효과, 하이브리드의 강한 비교 대상 |
| V4 | V3 기반 bounded 최근 리스트 + 트리 | 최근 노드의 트리 연산 생략 효과 |

V4가 V0보다 빠르더라도 V3보다 이점이 없으면 “하이브리드의 기여가 입증됐다”고 결론 내리지 않는다. `rb_root_cached`를 시험한다면 V2의 별도 대안으로 표기하고 동시에 다른 최적화를 섞지 않는다. 각 비교군은 재현 가능한 commit과 동일 `.config`·compiler 설정으로 고정한다. 측정 instrumentation을 사용하는 경우 모든 비교군에 같은 조건으로 넣는다.

### 10.2 환경과 네트워크 조건

실제 **Linux 테스트 VM 또는 전용 장비**에서 커널을 부팅한다. 개발 호스트에서 소스 문서만 읽은 것을 실행 검증으로 표시하지 않는다. 빠른 회귀는 network namespace + veth, 최종 성능은 가능하면 별도 송신/수신 CPU 또는 두 호스트로 확인한다. 동일 호스트 veth 결과에는 양쪽 처리와 netem 비용이 섞인다는 한계를 기록한다.

환경 manifest에는 커밋, patch, kernel config, compiler, CPU/메모리, VM vCPU·host 부하, governor, CPU affinity, IRQ/softirq 배치, NIC/MTU, offload, congestion control, buffer/window, qdisc 방향·limit을 기록한다. netem의 지연을 편도/왕복 중 무엇으로 설정했는지 명시하고 실제 RTT를 확인한다. qdisc queue limit 부족으로 생긴 손실을 의도한 랜덤 손실과 구분한다.

| 축 | 최소 시나리오 |
| --- | --- |
| 흐름 | 단일 장시간 bulk, 다중 연결 bulk, 작은 메시지 request/response 또는 짧은 연결 |
| 관찰한 큐 크기 | 1~K, K 경계 부근, K보다 훨씬 큰 경우. synthetic 모델은 1·8·64·1024·4096 skb 포함 |
| 네트워크 | 무손실·낮은 RTT, 높은 RTT·충분한 BDP, 랜덤/버스트 손실, 재정렬, ACK 지연/손실 |
| 기능 | SACK on/off, GSO/TSO/GRO 기본 설정과 통제된 off 설정, IPv4 및 IPv6 smoke |

시작 후보는 RTT 1/20/100ms, 손실 0/0.1/1%, 연결 1/8개로 한다. 모든 조합을 다 돌리지 않고 2주차에 대표 조건을 사전 선택한다. 이 숫자들은 환경에 맞춰 보정할 실험 설정이지 실제 큐 길이 보장이 아니다. offload off 결과만 골라 성능 개선을 일반화하지 않는다.

### 10.3 측정 항목

**주 지표**는 sender CPU cycles/수신 완료 payload byte, 수신 goodput, request/response p50·p99 지연이다. cycles의 CPU 범위와 softirq 포함 여부를 기록한다. 프로세스 PID만 측정하면 네트워크 softirq 비용이 빠질 수 있다. VM에서 PMU가 제공되지 않으면 cycles를 꾸며내지 않고 CPU time/byte 또는 ns/op와 한계를 보고한다.

**원인 지표**는 insert/delete/find/next 비용, key 비교 횟수, 승격 횟수, L에서 ACK되어 제거된 수, 실제 N/T/L 히스토그램, split/merge 횟수, SACK 범위당 방문 노드 수, retransmission·timeout, 메모리 및 캐시 관련 지표다. 신호가 약하면 연산 카운터와 microbenchmark로 설명하되 실제 처리량 개선과 동일시하지 않는다.

microbenchmark는 미리 준비한 skb/노드를 사용해 할당 비용과 순수 구조 연산을 분리하고, 별도로 할당 포함 경로도 측정한다. 큐를 채우기만 하는 경우뿐 아니라 append→ACK, append→승격, 중간 split/merge, 손실 SACK 검색의 혼합 trace를 실행한다. 출력이 사용되도록 하고 계측 오버헤드를 보정한다. 노드 수와 GSO pcount를 분리한다.

커널 내부 연산은 test/debug harness 또는 별도 tracepoint로 관찰한다. helper가 inline인지 `nm`/disassembly와 실제 trace 가능 여부를 확인한다. 기능 지연 추적은 경로 전체 원인 분석용이고 rb 연산만의 시간과 구분한다. instrumentation을 끈 커널로 최종 성능을 다시 확인한다.

### 10.4 반복과 통계

2주차에 D가 측정 프로토콜을 고정한다. 기본은 조건당 warm-up 10초, 본 측정 30~60초, 독립 실행 최소 10회다. 짧은 연결은 충분한 요청 수를 확보한다. 실행 순서는 V0~V4를 무작위 또는 교차 순서로 배치하고, 각 실행 간 큐와 연결 상태를 재설정한다.

중앙값, 실행 간 분산/IQR, 95% bootstrap 신뢰구간과 모든 원시값을 보존한다. 패킷 하나하나를 독립 실험 표본처럼 계산하지 않는다. p99 지연에는 요청 수와 실행별 p99 변동을 함께 제시한다. 노이즈가 허용 회귀폭보다 크면 “통과”가 아니라 **판정 불가**다.

임계값 K는 훈련 조건으로 선택한 뒤 고정하고 다른 확인 조건에서 재측정한다. 조건마다 가장 잘 나온 K만 골라 한 구현의 성능처럼 보고하지 않는다.

## 11. 정확성 테스트

### 11.1 자료구조·모델 동등성

기준 모델은 64비트 확장 시퀀스를 사용하는 정렬 벡터로 만들고, 커널 쪽은 유효 범위의 u32 직렬번호를 사용한다. 동일 연산 trace를 RB backend와 hybrid에 적용해 노드 ID·순서·포함 검색·범위 검색 결과를 비교한다. 랜덤 seed와 실패 trace를 저장한다. debug 검증기에서 트리 균형/부모 링크, 리스트 연결, 중복·누락, 캐시, L 길이, 구간 순서를 확인한다.

필수 사례:

- 빈 큐, 1개, K−1/K/K+1, 트리만/리스트만/양쪽 존재, 마지막 노드 삭제 후 재사용.
- append와 승격, 리스트 전체 ACK, 트리 head/중간/tail 삭제, 경계 노드 제거.
- tree/list 각각의 split, K개 리스트 split에 따른 승격, 경계 양쪽 partial shift·full merge.
- 검색 q가 seq/end 바로 앞·같음·바로 뒤, 큐 밖, 구멍, 전체 범위, T/L 경계를 넘는 SACK.
- wraparound가 노드 내부·두 노드 사이·T/L 경계에서 발생하는 경우.
- 0 payload SYN/FIN, 여러 세그먼트를 담은 GSO skb, partial ACK trim, 교체 후 힌트.
- 할당 실패, clone/unclone 실패, purge와 반복 connect/disconnect, passive child 초기화.

### 11.2 실제 TCP 회귀

송수신 payload의 길이와 해시를 비교하고 정상 종료·half-close·reset을 검사한다. 손실/재정렬, 빠른 재전송, RTO, TLP, RACK, SACK/DSACK, 중복 ACK, SACK reneging, 부분 ACK, 작은 MSS, TFO/SYN 재전송, socket abort를 포함한다. netem만으로 정확한 SACK 경계를 보장할 수 없으므로 scripted packet peer 또는 packetdrill 같은 결정적 패킷 시나리오를 사용하고 도구 버전·실제 지원을 확인한다.

debug 커널은 가능한 설정에서 KASAN, lockdep, DEBUG_LIST 등을 켜서 UAF·이중 삭제·락 문제를 확인한다. 성능용 커널은 디버그 오버헤드와 분리한다. TCP 통계와 힌트는 기존 코드의 의미에 맞춰 비교하며 payload 해시 통과만으로 회계 정확성을 판정하지 않는다.

### 11.3 회귀 통과 기준

모델 불일치, 새 WARN/OOPS, UAF/leak, 큐 누락, 데이터 오류, 회계 음수/불일치, 타이머 진행 정지는 **즉시 실패**다. 테스트 실패를 “드문 기능”이라며 무시하지 않는다. 선택적 TFO/TCP repair 시험을 기간 안에 못 끝내면 미검증 범위를 명시하고, 해당 기능을 지원한다고 주장하지 않는다. 공유 코드를 변경한 경우 기능을 끄는 것만으로 링크 안전성 검토를 생략할 수는 없다.

## 12. Go / No-Go 게이트

다음 수치는 팀이 합의할 **제안 판정 기준**이다. 실험 결과를 본 뒤 유리하게 바꾸지 않는다.

| 시점 | Go 조건 | No-Go 시 조치 |
| --- | --- | --- |
| 1주차 말 — 실행 환경 | 전원 baseline 부팅 확인, D의 재현 스크립트, source SHA/config 보존 | 환경 정상화 우선. 4주 일정이면 하이브리드 범위를 즉시 재검토 |
| 1~1.5주 — 설계 | union 충돌 없는 설계, SACK 포함/범위 구분, wrap·split·merge·힌트 token 계약, 호출 지점 목록 | 계약이 닫히지 않으면 V2/V3로 축소 |
| 2주차 — 기존 연구 | 최신 고정 SHA와 관련 이력 비교, 동일 기법 여부와 과제 기여 설명 가능 | 동일 구현 발견 시 출처 명시 후 재현/변형 비교로 전환 |
| 2주차 — 실현 가능성 | 모델 테스트 통과, B/C가 RB 어댑터로 독립 개발, 실제 N/T/L 관찰 가능, 작은/큰 큐 조건 재현 | 큐 길이 확보 실패 시 microbenchmark 중심으로 범위 제한; 시스템 개선 주장은 보류 |
| 2주차 — 성능 잠재력 | V0 profiling에서 대상 비용 확인, L에서 승격 전 제거되는 노드가 실제로 관찰되고 절감이 계측 노이즈보다 큼 | 승격이 거의 전 노드에 발생하거나 리스트 검색비가 절감을 상쇄하면 V4 중단 |
| 3~4주 — 통합 정확성 | 필수 correctness suite, raw rb 접근 감사, purge·timer·child 경로 통과 | V4 병합/제출 대상에서 제외하고 마지막 정상 비교군 유지 |
| 최종 — 성능 | 사전 선택 주 워크로드에서 V4의 cycles/byte가 V3보다 중앙값 ≥3% 감소하고 개선 신뢰구간이 0을 넘지 않음. V0 대비도 별도 보고 | micro 개선만 있으면 그 범위로 결론 제한 |
| 최종 — 회귀 방지 | 주요 다른 조건의 goodput 저하 ≤2%, p99 지연 증가 ≤5% 범위라는 근거 확보; 메모리 증가 수치 공개 | 경계 넘는 회귀 또는 신뢰구간이 넓어 회귀를 배제 못하면 범용 개선 주장 금지 |

성능 비율은 `(modified - baseline) / baseline`으로 정의해 부호를 일관되게 표시한다. cycles/byte 감소의 95% 신뢰구간 상한이 0보다 작아야 “개선 근거 있음”으로 판정한다. 회귀 없는 것으로 판단하려면 해당 허용 한계를 신뢰구간까지 고려한다. 통계적으로 구분되지 않는 결과를 “같거나 빠름”으로 바꾸지 않는다.

## 13. 4~6주 일정

| 주차 | A | B | C | D / 공동 게이트 |
| --- | --- | --- | --- | --- |
| 1주 | 필드·어댑터 RB 초안, init/purge 감사 | 송신·SYN·split 호출 지도 | ACK/SACK·hint·변이 지도 | baseline 빌드·부팅·측정 smoke. 전원 설계·소스 합의 |
| 2주 | hybrid 모델·불변식·캐시 | V3 삽입 최적화·microbench | 포함/범위 검색·변이 테스트 | 측정 프로토콜 고정, 큐 히스토그램, 최신 이력 비교, Go/No-Go |
| 3주 | backend 통합·child 초기화 | 송신/재전송/분할 통합 | ACK/SACK/shift/merge 통합 | 회귀 자동화와 V0~V3 결과; 매일 작은 통합 |
| 4주 | 캐시/수명 오류 수정·코드 고정 | 삽입·실패 경로 검증 | SACK·wrap·timer 검증 | 4주형 최종 실험·보고서 또는 6주형 통합 게이트 |
| 5주(있을 때) | 메모리 비용·검증 보완 | workload별 삽입 원인 분석 | SACK-heavy 회귀 분석 | V0~V4 반복 실험, K 고정 후 확인 실험 |
| 6주(있을 때) | 최종 코드·설계 문서 | 재현 절차 리뷰 | 정확성·한계 리뷰 | 통계·그래프·발표·재현 패키지, 새 기능 추가 금지 |

**4주형은 3주차 말 코드 동결**을 목표로 한다. 2주차에 닫힌 설계와 자동 테스트가 없으면 V4를 강행하지 않는다. 4주차에는 V0~V3와 동작 확인된 V4만 측정하고 부족한 확장 실험은 명시한다. 6주형은 남는 시간을 기능 확대보다 반복·회귀 검증에 쓴다.

## 14. 주요 위험과 범위 축소 순서

| 위험 | 대응 및 축소 |
| --- | --- |
| 최근 리스트의 이득이 없음 | 승격률·ACK 전 제거율을 근거로 V4 중단. V3/V2를 정식 결과로 제출 |
| skb union 오용·변이 중 잘못된 소속 판별 | transaction token과 debug 검증, 각 변이의 호출 순서 리뷰. 해결되지 않으면 hybrid 통합 중단 |
| SACK 병합·힌트 UAF | deterministic 시나리오와 KASAN. 일반 safe iterator에 의존하지 않음 |
| 큰 큐 검색 회귀 | K를 작게 고정해 확인 실험. 회귀가 남으면 하이브리드 범용 적용 주장 철회 |
| VM 잡음·PMU 미제공 | CPU 격리, 실행 교차, 반복. cycles 불가 시 CPU time/byte와 micro 결과로 제한 |
| GSO로 실제 노드 수가 작음 | skb histogram으로 확인. offload on을 기본 결과로 두고 off는 진단용으로 분리 |
| 소켓 크기·cacheline 비용 | 구조체/slab 크기와 다중 연결 메모리를 보고. 성능 이득보다 크면 캐시 필드 축소 또는 V1 복귀 |
| 초기화·purge 누락 | active/passive open, clone, disconnect 재사용, abort, repair 호출 지도 검사 |
| 프로젝트 종료 임박 | V4 → V3 → V2 → V1+분석 순으로 축소. 마지막 정상 버전을 항상 유지 |

축소할 때 **TCP 정확성을 없애는 방식으로 범위를 줄이지 않는다**. SACK을 끄고 “SACK 검색 문제 해결”이라고 보고하거나, merge를 무조건 비활성화한 뒤 원래 동작과 같다고 주장하지 않는다. 그런 변형이 필요하면 별도 제한 실험으로 명시하고 원래 기능을 보존한 비교군과 구분한다.

## 15. 최종 제출 체크리스트

- [ ] source SHA, config, compiler, patch series와 빌드/부팅 재현 절차
- [ ] V0~V4 구현 여부와 제외 이유, 동작 확인된 최종 commit
- [ ] 공통 API 계약·transaction 경계·필드 배치·불변식
- [ ] 직접 rb 접근 및 seq/end_seq 변경 호출 지점 감사표
- [ ] 모델 seed·실패 trace·correctness 결과·미검증 기능 목록
- [ ] N/T/L 분포, 승격률, 승격 전 제거율, SACK 방문 수
- [ ] goodput·CPU/byte·지연·메모리, 반복 원시값·신뢰구간·노이즈
- [ ] V4 대 V3 및 V0 비교, 작은/큰 큐와 손실/재정렬의 회귀 분석
- [ ] A/B/C/D별 패치·측정·리뷰 기여와 기존 연구 출처
- [ ] 개선이 없거나 조건부일 때도 결과·한계를 그대로 기록

## 부록. 소스 검증 기록과 구현 전 확인 항목

### A. 직접 읽은 기준 태그 파일

모든 링크는 `v5.4.214`를 명시한다. 문서의 알고리즘과 API 초안은 이 파일에 원래 존재하는 기능으로 인용하지 않는다.

- [include/net/tcp.h](https://github.com/gregkh/linux/blob/v5.4.214/include/net/tcp.h): 직렬번호 비교, 큐 helper, tsorted cleanup, highest_sack.
- [include/net/sock.h](https://github.com/gregkh/linux/blob/v5.4.214/include/net/sock.h): rtx root 위치와 union.
- [include/linux/tcp.h](https://github.com/gregkh/linux/blob/v5.4.214/include/linux/tcp.h): tcp_sock, 시간 큐 및 힌트.
- [include/linux/skbuff.h](https://github.com/gregkh/linux/blob/v5.4.214/include/linux/skbuff.h): 링크 union, cb, tsorted anchor.
- [include/linux/rbtree.h](https://github.com/gregkh/linux/blob/v5.4.214/include/linux/rbtree.h): cached-root 지원 API.
- [net/ipv4/tcp_output.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_output.c): 송신, 분할, 재전송, collapse, TLP, SYN/TFO.
- [net/ipv4/tcp_input.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_input.c): SACK 검색/순회/shift, ACK clean, 일반 rb insert와 ofo 호출.
- [net/ipv4/tcp.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp.c): init, purge, repair.
- [net/ipv4/tcp_minisocks.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_minisocks.c): child clone 이후 초기화.
- [net/ipv4/tcp_recovery.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_recovery.c): RACK 시간 순서 순회.
- [net/ipv4/tcp_timer.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_timer.c): rtx head/empty를 사용하는 타이머 경로.
- [net/ipv4/tcp_ipv4.c](https://github.com/gregkh/linux/blob/v5.4.214/net/ipv4/tcp_ipv4.c): destroy와 purge 연결.

### B. 실행으로 확인해야 하는 사항

현재 문서는 source inspection 결과다. 실제 구조체/slab 크기, socket lock 전제의 모든 호출 지점, 전체 설정별 빌드, IPv6/선택 기능의 회귀, test harness의 사용 가능성, 성능 수치와 통계 판정은 아직 실행 검증하지 않았다. 전체 checkout 감사와 위 게이트를 통해 확정한다.

이 프로젝트가 답해야 할 최종 질문은 “리스트를 붙였는가”가 아니라, **시퀀스 검색·순회·수명을 보존하면서 실제로 몇 개의 트리 연산을 생략했고, 그 절감이 추가 비용보다 컸는가**이다.
