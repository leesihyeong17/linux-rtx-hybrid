# B tail 삽입 최적화와 공통 RB 대조군

## 범위

[#16](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/16)의 비교를 위해 `0ee4a1a716d5f5f5f4ea89e5eae948c5dd30fa49` 위에 캐시와 tail 직접 연결을 독립 설정으로 추가한다. 성능 개선은 아직 입증하지 않았으므로 `TCP_RTX_CACHE`, `TCP_RTX_TAIL`은 기본적으로 꺼져 있다. 기존 HYBRID 구성은 CACHE를 선택하며, TAIL은 선택하지 않는다.

| 구성 | HYBRID | CACHE | TAIL | 의미 |
| --- | --- | --- | --- | --- |
| RB 기본형 | n | n | n | 기존 API 전환 RB 경로 |
| RB 캐시 | n | y | n | head/tail 접근과 캐시 유지 비용을 분리 |
| RB tail | n | y | y | 공통 최적화 V3 후보 |
| 하이브리드 기본형 | y | y | n | 기존 하이브리드 경로 |
| 하이브리드 tail | y | y | y | V3와 최적화를 맞춘 V4 후보 |

같은 SHA의 RB tail과 하이브리드 tail을 비교해야 LIST의 추가 효과를 해석할 수 있다. 이 RB 기본형은 upstream V0 자체가 아니다. 기존 V0/V1 SHA 비교는 별도로 유지한다.

## 구현과 전제

`include/net/tcp_rtx.h`의 `__rtx_tree_append()`는 캐시된 TREE 최대 노드의 비어 있는 `rb_right`에 새 노드를 연결한 뒤 `rb_insert_color()`를 호출한다. 빈 TREE에서는 root에 연결한다. 키 탐색만 생략하며 RB 재균형 비용은 남는다. 삽입 전체가 O(1)이라는 의미가 아니다.

적용 위치는 RB backend의 `rtx_append()`와 하이브리드 LIST 맨 앞의 TREE 승격이다. 두 경로 모두 새 TREE 노드의 seq가 현재 TREE 최대 seq 뒤라는 계약이 있다. 비교에는 TCP `after()`를 사용하며 wraparound를 유지한다. DEBUG에서는 seq와 연결 위치 전제를 확인한다.

TREE split과 임의 위치 ordered insert는 기존 일반 삽입을 유지한다. 다음 위치가 알려진 split까지 최적화하면 중간 노드의 subtree를 다루는 별도 알고리즘·검증이 필요하므로 이번 범위에 넣지 않았다.

`struct tcp_sock`의 `rtx_tree_first/last`를 HYBRID 전용에서 CACHE 공통 상태로 옮겼다. init, child 초기화, 일반 삽입, append, unlink, replace, purge를 통한 최종 삭제에서 캐시를 유지한다. B/C 호출자는 Core 밖에서 필드를 변경할 필요가 없다. 공개 API, split token 계약, TCP 회계, skb 소유권, tsorted 책임은 그대로다.

## 선택과 비용

- 캐시와 tail을 독립 설정으로 둬 캐시만의 효과와 직접 연결 효과를 구분한다. 설정·검증 조합은 늘어난다.
- RB에 캐시를 추가하면 64비트에서 포인터 두 개의 명목 비용 16바이트와 변경 시 갱신 비용이 생긴다. 실제 `sizeof(tcp_sock)`와 slab 증가는 별도로 측정해야 한다.
- 양 backend에 같은 최적화를 제공한다. 기존 HYBRID off/on만 비교하면 섞였던 캐시 효과를 분리할 수 있다.
- 일반 삽입 fallback을 설정으로 유지한다. 올바른 append 호출은 순차 전제를 만족해야 하며 잘못된 호출을 숨기는 런타임 fallback은 넣지 않는다.
- K=16은 대표 시험값이다. 최적 K로 선택한 것이 아니다.

## 검증

실행 명령은 `bash rtx-project/scripts/run_hybrid_validation.sh`다. 로컬 Ubuntu 22.04 ARM64 Docker에서 x86_64 GCC 11.4로 Linux 5.4를 빌드하고 QEMU 6.2 TCG, 1 vCPU, 512 MiB의 x86_64 게스트를 부팅했다. ARM64 대상 커널 검증은 아니다.

증거 디렉터리는 `rtx-project/.work/hybrid-m7C55OHm/`이다. `source-sha.txt`, `source.patch`, 각 `kernel.config`, `environment.txt`, `SHA256SUMS`로 입력을 추적한다. 기준 SHA에 working-tree patch를 적용한 결과이며 기준 SHA 단독 실행으로 표현하지 않는다.

| 시험 | 결과 |
| --- | --- |
| IPv4/IPv6 빌드 | RB DEBUG/STATS off/on, RB cache on, RB tail off/on, hybrid tail off/on의 7개 설정 PASS |
| Hybrid tail K=16 Core | 1325 checks, 0 failed |
| Hybrid tail K=1 Core | 1190 checks, 0 failed |
| RB tail Core | 810 checks, 0 failed |
| B 통합 fixture K=16/K=1/RB tail | 각 summary JSON의 전체 항목 0 failed |
| 실제 패킷 SACK/LOSS/RTO | 4개 부팅 모두 재전송 진입과 반환 2/1/1 확인, runtime PASS |
| 스타일 | Core diff checkpatch 0 errors, 0 warnings; diff check PASS |

새 Core 테스트는 seq wrap, endpoint replace, 잘못된 캐시 검출, 마지막 TREE 노드 제거 후 append, child 캐시 초기화, 전체 drain, empty 재삽입을 확인한다. RB 부모 포인터·red 제약·black height를 삽입 후와 drain 중 검사한다. 기존 split·SACK·tsorted 시험도 다시 실행했다.

첫 Docker 실행은 로컬 이미지 조회에서 `No such image`로 종료했다. 재조회 시 기존 이미지가 확인되어 pull/build 없이 재실행했다. 실패 증거는 `rtx-project/.work/hybrid-IL5kS4bP/`에 남겼다. 성공 실행의 `remaining-containers.txt`는 비어 있다. 기존 컨테이너와 이미지는 보존했다.

## 남은 작업

본 측정 전 C의 잔여 기본 회귀와 D의 환경 고정을 마친 통합 SHA가 필요하다. 이 변경은 opt-in 비교 후보의 구현·정확성 검증이며 채택을 정당화하는 성능 수치는 아직 없다. ARM64 native, 실제 TCP CPU/byte·goodput·지연, 소켓/slab 메모리와 독립 반복 통계는 D와 이어서 검증한다.
