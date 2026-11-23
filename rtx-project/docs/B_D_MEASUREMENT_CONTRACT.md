# B·D 도메인 성능 측정 계약 (#16)

- 작성일: 2026-11-16
- 문서 버전: 0.2 (2026-11-16)
- 상태: **RB 대조군, split 측정 경계, 재현 환경 식별, split 입력 조건의 네 항목을 확정. 최종 SHA·예비 측정 의존 수치 등은 별도 고정.**
- 관련 이슈: [#15 공동 정확성 회귀](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/15), [#16 성능 비교](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/16)
- 참고: [설계](design.md), [기존 시험 계획](testing.md), [환경 manifest](MANIFEST.md), [B #15 검증 결과](pr/issue-15-b-sack-regression.md)

## 1. 목적과 적용 상태

B의 연산 벤치마크와 D의 실행기·수집기가 같은 입력, 측정 경계, 단위와 증거 형식을 사용하도록 정리한다. 이 문서는 사용자가 전달한 D의 답장과 네 항목에 대한 확정 요청을 반영한다. 0.2에서 고정한 계약과 구현·예비 측정 후 정할 실행값을 구분한다. 코드 확인 기준은 `0ee4a1a716d5f5f5f4ea89e5eae948c5dd30fa49`다. 이 기준 커밋의 상태와 이번 #16 구현 이후의 상태는 구분한다. 최소 구현·실행 방법은 [벤치마크 README](../tests/benchmark/README.md), 변경·검증 결과는 [#16 대표 문서](pr/issue-16-b-benchmark-tail.md)에 연결한다.

| 구분 | 현재 상태 |
| --- | --- |
| 공통 작업 방향 | 정확성·관찰·성능 실행 분리, 동일 조건 비교, 원시값·실패 증거 보존, PMU 미지원 시 cycles 추정 금지 |
| 네 항목의 계약 | 3절·4절의 비교군, 측정 scope, 환경 필드, split 입력 필드를 고정. manifest에 contract_version=`0.2` 기록 |
| 구현·예비 측정 | 이번 #16에서 B 최소 진입점·수집기와 opt-in 공통 CACHE/TAIL 대조군을 제공. D 본 측정과 C 통합 회귀는 별도 |
| D의 수치·도구 제안 | ktime_get_ns(), 워밍업 10초, 본 측정 30/60초, 독립 실행 최소 10회 등은 예비 측정 후 고정 |
| 본 측정 전 고정 | 최종 비교 SHA/config, 실행 가능한 B 진입점·이벤트 구현, 주 workload·지표, 통계 계산 세부사항·수치 기준, 메모리 한계, 공유 위치 |
| 최종 성능 판단 | C 잔여 기본 회귀까지 확인한 통합 SHA로 수행. B #15의 선정 사례 통과만으로 전체 정확성을 확정하지 않음 |

## 2. 역할과 연결 순서

| 담당 | 제공할 내용 |
| --- | --- |
| B | 벤치마크 진입점, 측정하는 실제 함수, 입력 상태 복원 방식, 연산·성공·실패·승격 이벤트 정의, 연산별 정확성 검사 |
| D | 환경 재현, 비교 커널 빌드, 실행·수집 자동화, 타이머·PMU 검증, 반복 계획, 집계와 공유 증거 관리 |
| B·D | 비교군과 측정 계약 고정, 예비 측정 해석, 주 지표·허용 회귀·메모리 한계 확정 |
| C / A 협업 | C의 ACK/SACK 잔여 회귀 확인, A의 Core 계측·캐시·승격 비용 해석 |

```text
B: 진입점·연산 경계·출력 이벤트 초안 ─┐
                                    ├→ 실행기 연결 → 예비 측정 → 계약 고정
D: 환경·반복 실행·수집 형식 초안 ─────┘                         │
C: 기본 공동 회귀 확인 → 최종 통합 SHA 확정 ─────────────────────┤
                                                               ↓
                                                본 측정 → 최적화 후보 판단
```

## 3. 비교 대상과 설정

아래 세 고정 SHA는 로컬 Git에서 커밋 객체의 존재를 확인했다. V0·V1은 D가 제안한 비교 후보이며 최종 비교군 선정이나 해당 구성의 성능 재검증을 의미하지 않는다.

| 용도 | 기준 SHA | 설정·상태 |
| --- | --- | --- |
| RB 기준선 후보 | `991defcc56cad49cfb221f44f5d161c9ea920c4a` | 기존 V0 |
| API 전환본 후보 | `231db9ed6a204d6407d88954da60ce7db1c2dabd` | 기존 RB 기반 V1 후보. 성능 실행은 DEBUG/STATS off |
| 현재 통합본 off/on 예비 비교 | 동일 통합 SHA 사용, 예비 실행마다 SHA 고정 | `rb_integrated` / `hybrid_integrated`. 아래 공통 최적화 조건은 현재 미충족 |
| 최적화 RB 최종 대조군 V3 | 구현 및 공동 회귀 후 확정 | `rb_optimized_v3`. 명시적 head/tail 캐시와 검증된 tail 직접 연결 |
| 최종 하이브리드 비교군 V4 | V3와 공통 최적화를 맞춘 통합 SHA로 확정 | `hybrid_matched_v4`. bounded LIST 추가, K 명시, DEBUG/STATS off |
| B 정확성 재현 기준 | `0ee4a1a716d5f5f5f4ea89e5eae948c5dd30fa49` | [PR #20](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/20)의 K=16·K=1 시험 코드 |

각 비교군에는 다음 정보를 연결한다.

- 전체 소스 SHA, 추가 패치가 있으면 패치와 hash.
- 최종 `.config`, config의 SHA-256, 공통 설정 대비 diff.
- 컴파일러·빌드 명령, 커널 이미지 SHA-256.
- 벤치마크 코드·fixture/overlay의 버전과 hash.
- 캐시·tail 직접 연결 등 포함된 최적화 목록. 비교군 사이의 다른 기능 차이도 명시.

### 3.1 RB 대조군 확정

**최종 주 비교는 공통 최적화를 맞춘 V3 대 V4로 고정한다.** V0·기존 V1 후보는 보조 비교다. 같은 SHA의 HYBRID off/on이라는 사실만으로 V3/V4 조건을 충족했다고 판단하지 않는다.

현재 코드 확인 결과는 다음과 같다.

| 확인한 구현 | HYBRID off | HYBRID on |
| --- | --- | --- |
| `rtx_first()` / `rtx_last()` | `skb_rb_first()` / `skb_rb_last()` 탐색 | TREE 캐시와 LIST endpoint 사용 |
| `rtx_append()` | 일반 `__rtx_tree_insert()` | LIST 연결, K 초과 시 승격 |
| `__rtx_tree_insert()` | `tcp_rbtree_insert()`의 일반 삽입 | 같은 일반 삽입 + TREE 캐시 유지 |
| tail 직접 연결 | 없음 | 승격도 일반 삽입 사용. 직접 연결 없음 |

근거: [tcp_rtx.h](../../include/net/tcp_rtx.h)의 `rtx_first`, `rtx_last`, `rtx_append`, `__rtx_tree_insert`, `__rtx_limit`. 따라서 위 기준 SHA의 HYBRID off는 **V3가 아니며**, 해당 off/on 비교에는 LIST뿐 아니라 캐시 유무의 효과도 섞인다. 해당 기준 SHA의 하이브리드도 설계상의 V3 기반 V4를 완성한 것으로 부르지 않는다. 이번 변경의 CACHE/TAIL 조합은 [공통 RB 대조군 문서](B_TAIL_OPTIMIZATION.md)에 따르며, 최종 통합본의 성능 채택과는 별개다.

확정한 비교 운용은 다음과 같다.

1. **지금 가능한 예비 비교:** 동일 통합 SHA의 `rb_integrated` 대 `hybrid_integrated`. 결과는 캐시·LIST 등을 포함한 통합 구성 전체의 차이로 보고한다.
2. **최종 기여 검증:** B·A가 V3의 head/tail 캐시와 검증된 순차 tail 직접 연결을 구현·회귀 검증하고, 하이브리드에도 대응하는 공통 최적화를 맞춘다. RB 삽입·승격에서 tail 조건이 성립하는 경로를 명시하고, 임의 위치의 split/ordered insert에 tail 전제를 강제하지 않는다. RB 재균형 비용은 두 구성 모두 포함한다.
3. **최종 빌드 방식:** 두 backend와 공통 최적화를 같은 통합 SHA에 포함한다. 구조 선택은 HYBRID off/on으로 구분하고 K와 필수 종속 설정 외의 기능·컴파일러·DEBUG/STATS·계측 조건을 맞춘다. 캐시 유지와 tail 경로의 실제 실행 여부도 기능표와 시험으로 확인한다.
4. V3/V4 구현 또는 회귀가 미완료이면 예비 측정은 계속할 수 있지만, 하이브리드만의 성능 기여에 대한 최종 결론은 보류한다. V3를 생략하거나 현재 off 구성을 V3로 이름만 바꾸지 않는다.

V1 후보 `231db9ed6`에는 split 연결도 포함돼 있다. 기존 설계의 ‘어댑터만 있는 V1’과 동일하다고 가정하지 않고 실제 diff를 비교군 기능표에 적는다.

### 3.2 D 재현 단계와 환경 식별 확정

| 단계 | 실행 환경 | 시험 대상 | 표기·용도 |
| --- | --- | --- | --- |
| D-1 | ARM64 팀 VM에서 기존 B 실행기 실행 | x86_64 QEMU TCG 커널 | **ARM64 VM에서 B의 x86_64 시험 재현**. ARM64 커널 통과가 아님 |
| D-2 | ARM64 팀 VM에 대상 커널을 실제 부팅 | ARM64 커널 | **ARM64 대상 커널 검증**. ARM64용 config·빌드·부팅·fixture 절차 별도 보존 |

기존 [실행 스크립트](../scripts/hybrid_validation_container.sh)는 `ARCH=x86_64`, x86_64 cross compiler, `qemu-system-x86_64 -accel tcg`를 사용한다. ARM64 VM에서 스크립트를 실행한 사실만으로 D-2를 완료 처리하지 않는다. D-2에서 별도 ARM64 에뮬레이터를 사용하게 되면 `qemu_tcg`로 기록하고 VM native 실행이라고 표시하지 않는다.

다음 manifest 필드의 이름과 의미를 고정한다.

| 필드 | 자료형·의미 |
| --- | --- |
| `host_arch` | 문자열. D 실행기/에뮬레이터를 실행하는 OS의 아키텍처. 팀 ARM64 VM은 `aarch64` |
| `target_arch` | 문자열. 실제 시험 커널의 아키텍처. D-1은 `x86_64`, D-2는 `aarch64` |
| `execution_mode` | `qemu_tcg`, `vm_native`, `bare_metal_native` 중 실제 방식. `vm_native`는 VM 자체에 대상 커널을 부팅한 경우 |
| `environment_id` | 문자열. 고정한 환경 manifest를 식별. 서로 다른 단계·자원 설정은 구분 |
| `target_kernel_release` | 문자열. 실제 부팅된 시험 대상에서 수집한 release. build SHA/config/image hash와 연결 |
| `emulator_version`, `emulator_cpu_model` | 문자열 또는 null+사유. QEMU 실행 시 실제 버전과 CPU 모델 기록 |
| `virtualization` | 실제 VM/hypervisor 정보. 컨테이너·중첩 VM 등 실행 계층도 설명 |
| `physical_host_arch` | 문자열 또는 null+사유. VM 밖 물리 호스트 정보는 알 때만 기록하며 `host_arch`와 구분 |

빌드 머신이 실행 머신과 다르면 빌드 환경도 별도 식별한다. D-1과 D-2는 별도 run_id·environment_id를 사용한다. 서로 다른 아키텍처·실행 방식의 절대값을 묶어 자료구조 변경 효과로 계산하지 않고, 각 환경 안에서 동일 조건으로 비교한다.

## 4. 연산과 입력 상태 정의

### 4.1 공통 용어

| 항목 | 정의 |
| --- | --- |
| N | 전체 RTX 큐에 연결된 skb 수 |
| L | 하이브리드 LIST에 연결된 skb 수 |
| K | LIST의 최대 skb 수 |
| append 1회 | skb 하나를 삽입하는 호출 1회 |
| split 1회 시도 | 선택한 split 진입점의 호출 1회. 성공·실패 별도 기록 |
| 승격 1회 | skb 하나가 LIST에서 TREE로 이동한 사건 |

N/L/K는 TCP 세그먼트 수가 아니다. `packets_out`, `unacked`를 N의 대용값으로 쓰지 않는다. 승격은 구조 이동이므로 신규 skb 삽입이나 TCP 세그먼트 증가로 중복 계산하지 않는다.

### 4.2 측정 경계와 지표명 확정

B split 벤치마크의 함수 경계는 **`tcp_fragment(..., TCP_FRAG_IN_RTX_QUEUE, ...)`**로 고정한다. write queue 분할은 별도 workload이며 아래 RTX 구간 지표와 섞지 않는다. 다음 지표명은 raw sample의 ns 값이며 집계 시 해당 scope를 실제 완료한 연산 수로 나누어 ns/op를 계산한다.

| 지표명 | 측정 시작 → 종료 | 포함되는 비용 |
| --- | --- | --- |
| `rtx_append_total_ns` | `rtx_append()` 호출 직전 → 반환 직후 | 연결, 승격, 해당 RB 삽입·재균형과 캐시 유지 |
| `tcp_fragment_total_ns` | `tcp_fragment()` 호출 직전 → 반환 직후 | 조건 검사, `skb_unclone`, 할당, 메모리 회계, payload 분할, GSO·TCP 회계, Core 연결, tsorted 연결 등 실제 실행한 전체 경로 |
| `rtx_split_window_ns` | `rtx_split_prepare()` 호출 직전 → `rtx_split_commit()` 반환 직후 | 두 Core 함수 **및 그 사이의 seq/flags 수정, `skb_split()`, GSO·TCP 회계 등** |
| `rtx_split_prepare_ns` | prepare 호출 직전 → prepare 반환 직후 | prepare 함수 자체의 실행 구간 |
| `rtx_split_commit_ns` | commit 호출 직전 → commit 반환 직후 | commit 함수 자체의 실행 구간. 삽입·승격·재균형 포함 |
| `rtx_split_core_ns` | 같은 split 관측에서 `prepare_ns + commit_ns` | 두 비연속 Core 구간의 합. 사이의 payload·GSO·TCP 처리는 제외 |

prepare~commit 구간은 ‘순수 Core 비용’으로 부르지 않는다. `rtx_split_core_ns`는 파생 지표이며 별도의 연산 1회로 중복 집계하지 않는다. `[전체 시간 - 구간 시간]`처럼 다른 실행의 값을 빼서 미계측 비용을 추정하지 않는다.

현재 [tcp_fragment 구현](../../net/ipv4/tcp_output.c)에서 `skb_unclone()`과 새 skb 할당, 초기 wmem/truesize 처리는 prepare 이전에 있다. commit 뒤의 tsorted `list_add()`는 window와 Core 지표에 포함되지 않고 total에 포함된다.

```text
호출 직전 ─ 검사·unclone·할당·초기 메모리 회계 ─ [prepare]
              ─ seq/flags·skb_split·GSO·TCP 회계 ─ [commit] ─ tsorted ─ 반환 직후
|<----------------------- tcp_fragment_total_ns ----------------------->|
                                              ※ window는 prepare 직전부터 commit 직후
Core 합 = [prepare 함수 구간] + [commit 함수 구간]
```

- 시도·성공·실패 수와 함수 반환 코드를 기록한다. total은 정상 반환한 성공/실패 호출을 각각 분리 집계한다.
- 현재 prepare 이전에 실패한 호출은 window/prepare/commit/core 값을 null, 사유를 `not_reached`로 기록한다. 0ns 또는 성공 split 표본으로 넣지 않는다.
- 미래 변경이나 시험 중 prepare만 완료하고 commit이 끝나지 않았다면 관측한 구간만 보존한다. 나머지는 null+`incomplete`이며, transaction 실패 실행을 정상 성능 표본에 포함하지 않는다.
- **주 성능 실행은 total만 계측한다.** 내부 세부 구간의 타이머는 별도 observation 실행에 두고, 같은 입력·코드 조건의 run_id로 연결한다. Core 함수가 inline이어도 호출식 경계에 계측하며 noinline 강제로 production 특성을 바꾸지 않는다.
- observation의 window에는 내부 타이머가 주는 영향이 포함될 수 있다. 계측 오버헤드를 보존하고 세부 구간 합계가 비계측 total과 정확히 일치한다고 주장하지 않는다. 두 Core 값을 모두 얻은 같은 sample에서만 core 합을 만든다.
- prepare~commit 안에서 일반 `rtx_*` 조회, 상태 출력, 동적 메모리 할당을 추가하지 않는다. 시간값은 미리 준비한 로컬 저장소에 보존하고 출력·구조 검증은 commit 또는 함수 반환 후 수행한다.
- 상태 준비·복원·검증·로그 출력은 연산 시간 밖에서 수행한다. `ktime_get_ns()`는 경과 시간 측정이므로 순수 CPU 실행 시간으로 표기하지 않는다.

### 4.3 workload에 고정할 입력

기존 N/L/K·skb 크기·MSS·GSO·split 위치 외에 아래 **split 직전 입력 필드**를 고정한다. workload에는 요청 상태를, sample에는 `skb_unclone()` 전 실제 관찰한 상태를 남긴다. 서로 다르면 그 입력 조건의 정상 표본으로 집계하지 않는다. 상태 수집은 total 측정 시작 전에 수행한다.

| 필드 | 자료형·정의 |
| --- | --- |
| `skb_len_bytes`, `skb_data_len_bytes`, `skb_headlen_bytes` | 음이 아닌 정수. 전체 길이, 비선형 데이터 길이, 실제 `skb_headlen()` |
| `data_layout` | `linear`, `paged`, `mixed`, `frag_list`. frag_list가 있으면 `frag_list`; 그 외 비선형 데이터가 없으면 `linear`, headlen=0인 비선형이면 `paged`, 나머지는 `mixed` |
| `nr_frags`, `frag_sizes_bytes` | 음이 아닌 정수와 정수 배열. `skb_shinfo(skb)->nr_frags` 및 각 page fragment의 byte 길이 |
| `has_frag_list`, `head_frag` | boolean. frag_list 존재와 skb의 head_frag 상태. nr_frags만으로 두 상태를 추정하지 않음 |
| `skb_cloned`, `skb_shared`, `skb_header_cloned`, `nohdr` | boolean. 해당 helper 관찰값과 nohdr 상태. 데이터 공유·skb 객체 공유·헤더 공유를 서로 대체하지 않음 |
| `skb_users`, `dataref_raw` | 정수. 참조 상태 근거인 skb users 및 shared-info dataref의 관찰값. 코드 SHA와 함께 해석 |
| `gfp_mask`, `gfp_flags` | 16진 문자열과 symbolic flag 문자열 배열. 측정 호출에 실제 전달한 gfp_t 값과 의미. 예: GFP_KERNEL / GFP_ATOMIC |
| `call_context`, `socket_serialization` | 문자열. process/softirq 등 실행 문맥과 실제 socket 직렬화 방식 |

선형/page fragment 구성과 공유 상태는 같은 skb 길이에서도 복사·할당 비용을 바꾸므로 서로 다른 workload로 구분한다. 같은 workload를 비교군에 실행할 때 위 필드, MSS/GSO·split 위치를 맞춘다. GFP_KERNEL과 GFP_ATOMIC도 별도 조건이며 실행 문맥에 적합한 GFP만 사용한다. shared/cloned 상태를 만들었다는 이유로 TCP 호출자의 소유권·직렬화 전제를 깨뜨리지 않는다. 호출 전제를 만족하지 못하는 입력은 실행하지 않고 지원 범위 밖으로 기록한다.

초기 연산 벤치마크는 **frag_list 없는 입력**으로 범위를 고정한다. `has_frag_list=false`를 검증하고, frag_list 입력은 향후 별도 workload로 추가한다. 지원하지 않는 page 구성이나 상태를 임의로 선형화해서 같은 workload 이름으로 측정하지 않는다.

그 밖의 입력은 다음과 같다.

- 연산 종류, 목표 N/L, K, 연산 전후의 기대 N/L.
- skb 크기, GSO 사용 여부·MSS·세그먼트 조건, 필요 시 FIN/PSH와 시퀀스 조건.
- append의 LIST 여유/포화 여부. 포화 시 승격 비용을 포함하고 발생 여부를 구분.
- split 대상의 TREE/LIST 소속, 큐 위치, 분할 byte 위치와 MSS 정렬 여부.
- 실제 TCP의 연결 수·전송 크기·요청 크기, RTT·손실·재정렬·qdisc·offload·혼잡 제어 조건.

고정 상태 시험에서 복원 후 입력이 달라진 경우 정상 표본으로 포함하지 않는다. 묶음 측정은 같은 상태 보장 방법과 복원 비용의 제외 방법을 먼저 검증한다. 연속 append로 N/L이 변하는 묶음을 고정 상태 연산 시험으로 표시하지 않는다.

## 5. 실행 종류와 계측 분리

| 실행 종류 | 목적 | 기록할 사항 |
| --- | --- | --- |
| `correctness` | 불변식·경계·실패·payload 검증 | fixture/overlay, DEBUG/STATS, 실제 도달 경로, 성공·실패 |
| `observation` | N/L 분포·승격·연산 사건 관찰 | 사용한 계측, 계측 설정과 오버헤드, 연결할 성능 workload |
| `performance` | 같은 조건의 성능 비교 | 정확성 fixture/overlay 제외, DEBUG/STATS off 기본안, 필요한 측정 장치의 비용 |

성능 실행에서 직접 수집하지 않은 N/L 분포·승격 수는 null로 두고 같은 조건의 별도 observation run_id를 연결한다. 관찰 실행의 값을 성능 실행에서 직접 측정한 값으로 복사하지 않는다. 고정 상태 시험의 사전 입력 N/L과 실제 수집한 분포도 구분한다.

## 6. 타이머·PMU·반복 방식

### 6.1 시간과 CPU 측정

- 커널 연산은 `ktime_get_ns()` 기반 ns/op를 D의 기본안으로 둔다.
- 타이머 호출과 빈 측정 구간의 오버헤드 원시값을 함께 보존한다. 오버헤드 보정·차감 여부와 방법은 별도 합의한다.
- 연산이 너무 짧으면 동일 입력을 보장하는 묶음 측정을 검토한다. 오버헤드·잡음 때문에 차이를 구분할 수 없으면 판정 불가로 기록한다.
- PMU는 실제 대상 환경에서 카운터 실행, 종료 코드, 측정 범위를 확인한다. 미지원이면 cycles는 null과 사유를 남긴다.
- PMU 미지원 시 연산은 ns/op, TCP는 goodput·지연·CPU time/byte를 사용한다. CPU time/byte를 최종 주 지표로 삼을지는 본 측정 전에 확정한다.
- CPU 측정에는 sender/receiver 중 어느 범위인지, IRQ/softirq 포함 여부와 수집 방식을 명시한다. CPU time은 경과 시간과 구분한다.
- TCP 비용 지표의 분모는 수신 완료 payload byte다. wire byte나 재전송 byte를 같은 분모로 취급하지 않는다.

### 6.2 반복 계획 제안

| 항목 | D 제안 | 고정 시점 |
| --- | --- | --- |
| 워밍업 | 조건별 10초 | 예비 측정 후 |
| TCP 본 측정 | 우선 30초, 필요 시 60초로 비교군 전체 통일 | 본 측정 전 |
| 연산 반복 수·묶음 크기 | 예비 측정으로 결정, 비교군 전체 동일 | 본 측정 전 |
| 독립 실행 수 | 조건별 최소 10회 | 본 측정 전 |
| 실행 순서 | 비교군 교차 또는 무작위화, 순서·seed 보존 | 실행 계획 저장 시 |
| 실행 초기화 | 연결·큐 상태 초기화, CPU 배치·VM 자원 고정 | 매 실행 |

독립 실행 10회가 원하는 신뢰구간 폭을 보장하지는 않는다. 본 측정의 반복 수·종료 규칙을 정하고, 결과가 유리해질 때까지 선택적으로 반복하지 않는다. 한 실행 안의 개별 연산을 독립 실행 수에 포함하지 않는다.

## 7. 실행·수집 형식 초안

```text
<run_id>/
  manifest.json       # SHA, 설정, 도구, 환경, 실행 명령
  workload.json       # 입력 상태, 네트워크 조건, 반복 계획
  samples.jsonl       # 반복별 원시 측정값과 판정
  summary.csv         # 집계 결과
  configs/            # 최종 config와 비교 diff
  logs/               # stdout, stderr, 커널 로그, PMU 결과
  SHA256SUMS
```

아래는 공통 필수 정보 목록이다. 3.2절 환경 key와 4절 지표명·split 입력 key의 의미는 고정했다. JSON 내 중첩 위치·행 단위·나머지 key·schema_version 값은 B 이벤트와 D 수집기를 연결할 때 고정하며, manifest에는 contract_version=`0.2`를 남긴다.

| 구분 | 필수 정보 |
| --- | --- |
| 식별 | schema_version, run_id, 비교군, 전체 SHA, config hash, 벤치마크 코드 hash |
| 실행 | correctness / observation / performance, host_arch, target_arch, execution_mode, environment_id, 3.2절 환경 정보, 반복 번호·순서 |
| 입력 | workload_id, 연산, K, N/L 전후 상태, GSO 조건, split 위치, 4.3절의 실제 skb layout·길이·fragments·공유·GFP 조건 |
| 회계 | 시도·성공·실패 수, 실제 수행 연산 수, 승격 수 |
| 측정 | 타이머, 4.2절의 측정 scope와 고정 지표명, 경과 시간, 단위·값, scope별 실제 완료 수 |
| 판정 | valid 여부, 실패·제외 사유, 종료 코드, 원본 로그 상대 경로 |
| 연결 | 대응 observation run_id, 정확성 근거, 공유 위치와 SHA-256 |

null 값에는 사유가 필요하다. RB의 L/K는 적용 대상이 아니고, performance에서 수집하지 않은 승격 수는 미수집이며, 미지원 cycles는 장치 기능 제한이다. 세 상태를 구분한다. 실제 0회와 미수집을 구분하기 위해 null을 0으로 바꾸지 않는다. 최소 구현의 schema_version=1은 `null_reasons`에 `not_applicable`, `not_collected`, `not_reached`를 기록한다. cycles는 미요청 시 `not_measured`이며 PMU 미지원으로 추측하지 않는다.

B·D는 run_id가 독립 실행 하나를 나타내는지, 여러 반복을 포함한 묶음을 나타내는지 먼저 고정한다. 여러 반복을 묶는다면 각 독립 실행을 구분할 식별자를 추가해 집계 단위를 잃지 않는다. summary.csv는 samples.jsonl에서 재생성할 수 있어야 한다.

성공·실패·중단 실행 모두 기록을 보존한다. 공유 위치 확정 후 실행의 상대 경로와 SHA-256으로 B 원본 증거와 D 재현 결과를 연결한다. SHA256SUMS의 대상 파일 범위도 고정하며 체크섬 파일 자체를 재귀적으로 포함하지 않는다.

## 8. 집계와 판정 제안

### 8.1 통계 원칙

반복별 원시값, 중앙값, IQR, **독립 실행 단위** 95% 신뢰구간을 보고한다. 기존 설계는 bootstrap을 제안한다. 구체적인 재표집 방식·횟수·seed, 비교군을 짝지을지 여부와 변화율의 신뢰구간 계산법은 본 측정 전에 확정한다. p99는 실행별 요청 수와 변동도 보고한다.

회귀 실패·데이터 오류 실행은 성능 집계에서 제외하되 이유와 실패 개수를 보존한다. 단순히 느린 정상 실행은 제외하지 않는다. 정확성 실패가 있는 비교군의 성능 결론은 보류한다.

변화율은 `(modified - baseline) / baseline`으로 기록한다. 비용·지연은 음수가 개선이고 goodput은 양수가 개선이다. 잡음 때문에 허용 범위를 구분할 수 없으면 판정 불가다. 미측정, 판정 불가, 개선 근거 없음, 개선 또는 회귀 근거 있음을 구분한다.

### 8.2 주 지표와 허용 범위: 전부 합의 전 제안값

| 항목 | D 제안 | 남은 합의 |
| --- | --- | --- |
| 연산 | append total·tcp_fragment total ns/op, 승격 여부와 성공/실패별 비용. window/Core는 별도 관찰 지표 | 함수 경계는 4.2절에서 확정. 묶음 측정 방식은 예비 측정 후 고정 |
| TCP | goodput, CPU 비용/수신 완료 payload byte, 요청 응답 p50·p99 | 주 workload·주 지표·CPU 측정 범위 |
| 개선 목표 | V3 대비 V4의 사전 지정 주 workload에서 CPU/byte 중앙값 3% 이상 감소, 변화율 95% CI 상한 < 0 | 주 workload와 수치 채택, PMU 미지원 시 CPU time/byte 채택 |
| goodput 회귀 한계 | 저하 2% 이내를 신뢰구간까지 고려 | 판정 방법 확정. 위 부호 정의에서는 변화율 CI 하한이 -2% 이상인지 확인하는 안 |
| p99 회귀 한계 | 증가 5% 이내를 신뢰구간까지 고려 | 판정 방법 확정. 변화율 CI 상한이 +5% 이하인지 확인하는 안 |
| 메모리 | 소켓당·전체 사용량 보고 | 측정 대상 메모리의 범위와 허용 증가량 |

3%·2%·5%는 기존 문서와 D가 제안한 협의값이며 확정된 통과 기준이나 이미 얻은 개선 결과가 아니다. 메모리 허용 증가량도 아직 없다. K=16은 대표 정확성 시험값이며 최적 K로 선정된 값이 아니다. K 탐색에 사용한 조건과 최종 확인 조건의 분리도 본 측정 계획에 기록한다.

## 9. 증거 인계와 재현 기준

B는 D에게 소스 버전, 최종 config, 실행 명령, fixture/overlay 버전, 원본 로그와 SHA-256 목록을 전달한다. 기존 B 결과와 경로는 [#15 대표 문서](pr/issue-15-b-sack-regression.md)에 기록돼 있으며 현재 증거는 로컬 보존 상태다. 공유 위치와 업로드 방식은 미확정이다.

기존 성공 실행 `hybrid-8sXfJOmL`은 기준 HEAD `73747d3f1657c577fcd0f9a8179bcb7fbdb9e78e`의 archive에 보존된 `source.patch`와 별도 `harness/`를 적용해 실행했다. 최종 테스트 코드와 실행 사본의 일치를 확인한 뒤 커밋했다. 따라서 `0ee4a1a71`은 최종 시험 코드의 재현 기준이고, 기존 바이너리가 처음부터 해당 HEAD를 직접 빌드한 것처럼 기록하지 않는다. 원본 증거의 source-sha·patch·harness 정보를 함께 보존한다.

D-1과 D-2는 3.2절의 구분을 적용한다. D-2를 위해 변경한 ARM64용 config·빌드/부팅 절차·overlay와 대상 커널에서 수집한 식별 정보를 보존한다. 계약 문서 자체가 D의 단계 통과 증거는 아니다. 이번 B의 x86_64 실행은 #16 대표 문서에 별도로 기록한다.

## 10. 확정 사항과 본 측정 전 체크리스트

- [x] 기준 SHA의 HYBRID off는 V3가 아님을 확인하고, 예비 off/on과 최종 V3/V4 비교를 구분했다.
- [x] split total/window/Core 지표명·경계·실패 표본 처리를 고정했다.
- [x] D-1 x86_64 재현과 D-2 ARM64 대상 검증, 환경 식별 필드를 고정했다.
- [x] split의 선형/page·headlen·fragments·clone/shared·GFP 입력 필드를 고정했다.
- [ ] C 기본 공동 회귀 결과와 최종 통합 SHA를 연결한다.
- [ ] 비교군의 전체 SHA/config와 캐시·tail 등 기능 차이를 확정한다.
- [x] B가 고정한 함수 경계에 맞는 최소 실행 진입점·이벤트와 입력 복원 구현을 제공한다. 실제 TCP workload, 다양한 N/K 및 추가 실패 경로는 후속이다.
- [x] B가 공통 CACHE/TAIL 구현과 선택적 V3/V4 후보의 정확성 검증을 제공했다.
- [ ] A·D가 최종 통합 후보의 기능표와 config diff를 확인한다.
- [x] B 최소 이벤트의 단위·null 사유·schema_version=1을 README와 collector에 명시했다.
- [ ] D 수집기와 실제 연결해 해당 스키마의 소비를 검증한다.
- [x] run_id당 독립 부팅, JSONL 1행당 연산 1회, 동일 variant/repeat의 observation 연결을 최소 구현에 명시했다.
- [ ] 타이머 오버헤드·PMU 지원·CPU 측정 범위·CPU time/byte 채택 여부를 확인한다.
- [ ] workload·K·반복 수·묶음 크기·워밍업·측정 시간·순서·seed를 고정한다.
- [ ] V3/V4의 주 workload·주 지표·신뢰구간 계산법·회귀 한계·메모리 허용량을 합의한다.
- [ ] 증거 공유 위치와 필요한 원본·체크섬 범위를 정한다.
- [ ] 고정한 계약의 문서 버전과 Git SHA를 각 본 측정 manifest에 연결한다.

이번 B 산출물은 **확정한 함수 경계·지표·입력 필드를 따르는 최소 벤치마크 진입점과 출력 이벤트 구현**이다. D는 이를 실행기·수집기에 연결해 예비 측정으로 실행 가능성과 잡음을 확인한다. 예비 측정에서 계약을 변경하면 변경 이유와 버전을 남기고, 본 측정 결과를 본 뒤 유리하게 기준을 바꾸지 않는다.


## 11. 0.2 변경 기록과 검증 범위

최초 계약은 D가 요청한 네 쟁점을 기준 SHA의 코드와 대조한 문서였다. 해당 기준의 RB backend에는 공통 캐시·tail이 없었고 수집기도 미구현이었다. 이번 #16 변경은 이 계약을 유지하면서 최소 fixture·수집기와 공통 CACHE/TAIL 대조군을 추가한다. 따라서 최초 문서 확인과 실제 실행 검증을 구분한다.

실제 빌드·실행·소스 일치 점검은 [#16 대표 문서](pr/issue-16-b-benchmark-tail.md)에 기록한다. 3%·2%·5%, 최종 통합 SHA, PMU 지원, 최종 변화율 통계·메모리 한계·공유 위치는 이번 최소 구현의 완료와 별개로 기존 예비 측정/합의 절차를 따른다.
