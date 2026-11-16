# #16 B 최소 연산 벤치마크

[B·D 계약 0.2](../../docs/B_D_MEASUREMENT_CONTRACT.md)을 실행하는 fixed-state fixture다. 실제 `rtx_append()`와 `tcp_fragment()`를 호출한다. 성능용 TCP 본문과 Core에는 내부 hook을 넣지 않는다. 관찰용 사본에서만 prepare/commit 타이머, 승격/tail 이벤트, 할당 실패 hook을 넣는다. production 소스에는 벤치마크를 빌드하는 Kconfig 옵션을 추가하지 않는다.

## 실행

저장소 루트에서 실행한다. 호환되는 로컬 Ubuntu 22.04 이미지를 먼저 확인한다. runner는 이미지를 자동 pull하지 않는다. 컨테이너 안에서 현재 소스를 새로 빌드하며 Ubuntu 패키지 설치에는 네트워크가 필요하다.

```sh
docker image inspect ubuntu:22.04
docker ps -a
# 기능 확인용. 워밍업을 생략하므로 성능 판단에 사용하지 않는다.
RTX_BENCH_ITERATIONS=3 RTX_BENCH_WARMUP_MS=0 RTX_BENCH_REPEATS=2 \
  bash rtx-project/scripts/run_b_benchmark.sh
# 기본값: 연산 32회/조건, 조건별 워밍업 10초, 독립 부팅 10회/비교군·모드
bash rtx-project/scripts/run_b_benchmark.sh
python3 -B -m unittest discover -s rtx-project/tests/benchmark -p 'test_*.py' -v
```

`RTX_BUILD_IMAGE`, `RTX_BENCH_K`(기본 16), `RTX_BENCH_SEED`(기본 16)도 지정할 수 있다. K를 바꿔도 `append_k16`은 **N=16**인 고정 workload 이름이다. K 경계를 자동 탐색하는 이름이 아니다. 기본 실행은 100회 부팅에 각 9/10개 조건의 워밍업이 있어 수 시간이 걸릴 수 있다. 반복 수·워밍업은 예비 측정 후 D가 고정한다.

`run_b_benchmark.sh`는 `source.tar`(HEAD), 추적된 `source.patch`, 별도 `harness/`를 보존한다. 커밋 전 실행은 SHA 단독으로 재현할 수 없으므로 세 입력을 함께 사용한다. 새 커널 파일을 추가한다면 먼저 Git 추적 대상으로 등록해야 archive+diff에 포함된다. 벤치마크 C/H/Python은 별도 복사되므로 새 fixture도 포함된다.

x86_64 cross GCC, QEMU TCG 1 vCPU/512 MiB를 사용한다. 10개 커널을 모두 빌드한 다음 측정을 시작한다. 반복마다 5개 비교군 × 2개 모드의 순서를 seed로 섞어 `plan.json`에 저장한다. 한 부팅이 독립 실행 단위이며, 각 연산 앞에는 새 소켓·skb를 만든다. 로컬 runner의 호스트 CPU affinity는 고정하지 않으며 manifest에 명시한다. 이 runner의 결과는 기본 반복 수를 충족해도 **TCG 예비 결과**다. D의 고정 CPU·VM 자원 및 ARM64 대상 커널 시험을 대체하지 않는다.

컨테이너 이름은 작업별로 고유하다. EXIT/INT/TERM trap에서 해당 컨테이너만 제거하고 `remaining-containers.txt`로 확인한다. 기존 컨테이너·이미지·volume을 삭제하지 않는다.

## 비교군과 입력

| variant | HYBRID / CACHE / TAIL | 의미 |
| --- | --- | --- |
| rb-basic | n / n / n | 현재 API 전환 RB. upstream V0와 구분 |
| rb-cache | n / y / n | 캐시 효과를 분리 |
| rb-tail | n / y / y | V3 후보 |
| hybrid-ordered | y / y / n | 기존 하이브리드 |
| hybrid-tail | y / y / y | 공통 최적화를 맞춘 V4 후보 |

모든 구성은 DEBUG/STATS off이며 나머지 config는 공통이다. `configs/common.diff`와 원본 `.config`로 확인한다. TAIL은 순차 삽입과 승격의 탐색을 생략하지만 RB 재균형은 실행한다. [구현과 회귀](../../docs/B_TAIL_OPTIMIZATION.md)를 참고한다.

| workload_id | N | 실제 입력·연산 |
| --- | --- | --- |
| append_empty / append_short / append_k16 / append_long | 0 / 4 / 16 / 64 | 4000-byte 선형 skb 하나를 append |
| split_linear | 16 | 선형 4000 bytes |
| split_paged | 16 | headlen 0, page fragment 4000 bytes 하나 |
| split_mixed | 16 | 선형 2000 + page fragment 2000 bytes |
| split_clone | 16 | 선형 skb를 실제 clone해 data 공유 상태를 생성 |
| split_atomic | 16 | `GFP_ATOMIC`으로 split. 호출 문맥은 process/lock_sock |
| split_alloc_failure | 16 | 관찰용만. `sk_stream_alloc_skb` 호출 경계에서 NULL 반환 |

MSS=1000, 초기 gso_segs=4, split 대상은 큐 index=8, byte offset=1500이다. K=16에서는 하이브리드 split 대상이 LIST에 있다. TREE split, 더 많은 N/K·분할 위치, 다중 page fragment, frag_list, 실제 메모리 압박 실패는 후속 workload다. K=1로 실행하면 해당 대상은 TREE에 있지만 이번 최소 벤치마크의 실행 증거는 K=16이다. K=1의 별도 통합 정확성 회귀와 혼동하지 않는다.

`skb_shared`인 객체에 대한 잘못된 변이는 지원하지 않는다. clone은 별도 skb 객체의 data 공유이며 `skb_shared`와 다르다. 입력의 len/data_len/headlen/nr_frags/frag_sizes, frag_list/head_frag, cloned/shared/header_cloned/nohdr, users/dataref, GFP 값·이름을 실제 상태에서 출력한다. `GFP_ATOMIC`은 할당 플래그만 바꾸며 interrupt/softirq 문맥 검증을 뜻하지 않는다.

## 측정 경계

| 지표 | 측정 범위 |
| --- | --- |
| rtx_append_total_ns | `rtx_append()` 호출 전후. 승격 시 연결·RB 재균형·캐시 갱신 포함 |
| tcp_fragment_total_ns | 전체 `tcp_fragment()`. unclone/할당부터 최종 tsorted·회계까지 포함 |
| rtx_split_prepare_ns | 관찰용의 실제 prepare 전후 |
| rtx_split_commit_ns | 관찰용의 실제 commit 전후 |
| rtx_split_core_ns | 같은 관측의 prepare+commit 합. 연속 구간이 아님 |
| rtx_split_window_ns | prepare 시작~commit 종료. 중간 skb_split/GSO/TCP 회계 포함 |

빈 타이머 구간을 각 시도에 함께 기록하며 임의 차감하지 않는다. 바깥 total은 분기·호출 envelope를 포함한다. prepare~commit 구간에는 다른 `rtx_*` 조회, 할당, 로그 출력을 넣지 않는다. 내부 계측 시간이 total에 영향을 주므로 관찰용 total을 성능용 total과 합치지 않는다. 내부 Core에 추가한 이벤트 hook의 오버헤드도 관찰 비용에 포함된다.

할당 실패는 prepare 전에 일어나므로 세부 시간은 `null/not_reached`다. 기대한 실패의 유효한 표본과 검증 실패로 제외되는 실행을 구분한다. 주입 실패는 allocator 고갈을 재현한 결과가 아니다. 실행 중의 예상하지 못한 할당 실패는 정상 측정 표본으로 처리하지 않는다.

## 이벤트와 D 수집

커널은 JSON 레코드 `RTXBENCH_CONFIG`, `INPUT`, `SAMPLE`, 관찰용 `DETAIL`, `DONE`을 출력한다. 프로토콜 1, contract_version 0.2다. 원시 transport의 -1 sentinel은 수집기에서 null과 사유로 변환한다. D는 원시 로그를 보존하면서 정규화된 `samples.jsonl`을 소비할 수 있다.

```text
b-bench-<id>/
  source-sha.txt, source.tar, source.patch, harness/, harness-hashes.json
  environment.txt, host-environment.txt, image.json, plan.json
  builds/<variant>-<mode>/  # bzImage, config, diff, build log, overlay patch/hash
  runs/<run_id>/
    manifest.json
    workload.json
    samples.jsonl
    summary.csv
    configs/
    logs/serial.log
    result.txt
    SHA256SUMS
  independent-runs.csv
  SHA256SUMS
  result.txt, cleanup.log, remaining-containers.txt, exit-code.txt
```

manifest는 전체 SHA와 patch hash, config/kernel/initramfs/벤치마크 bundle hash, compiler·환경 원본 경로, host_arch/target_arch/execution_mode, 실행 명령·순서·repeat·seed와 종료 코드를 연결한다. `benchmark_sha256`은 파일명→SHA-256 사전을 정렬 JSON으로 직렬화한 hash다. `source_sha`가 같아도 patch가 다르면 같은 소스로 보지 않는다. 실제 CPU/GSO와 VM 제한은 manifest와 환경 로그를 같이 읽는다.

표본은 연산당 1행이다. `attempts`, `successes`, `failures`, `actual_ops`, N/L 전후, 실제 입력, total metric/value/unit, 타이머 빈 구간, `detail`의 4개 시간, `promotions`, `tail_links`, 판정과 원본 경로를 담는다. N은 skb 수이며 packets_out을 대신 쓰지 않는다. 성능용의 N/L은 측정 구간 밖에서 검사한 고정 입력/결과이고 분포 계측은 아니다. 승격·tail 횟수는 performance에서 `null/not_collected`이며 동일 variant/repeat의 `observation_run_id`를 연결한다. 각 run의 상대 경로는 root의 `plan.json`에서 찾는다. 연결은 같은 조건이라는 뜻이지 같은 연산의 별칭이 아니다.

RB의 L/K는 `null/not_applicable`, 실행하지 않은 split scope는 `null/not_reached`다. PMU를 요청하지 않았으므로 cycles는 `null/not_measured`이며 지원 여부를 추정하지 않는다. raw record가 누락·중복·손상되거나 실행 경로, config hash, variant label, 실제 작업 수가 맞지 않으면 수집을 실패시킨다. 원시 실패 로그와 `valid=false` manifest는 보존한다. 실패 run이 있으면 현재 최소 runner는 전체 집계를 중단한다. 느리지만 정상인 run을 제거하는 기능은 없다.

각 run의 `SHA256SUMS`는 자체 체크섬 파일을 제외한 모든 run 파일을 포함한다. root 목록은 생성 시점의 공유 입력·빌드·run 파일을 포함하며 checksum 자체와 Python cache를 제외한다. 이후 만들어지는 root result·호스트 cleanup/exit 파일과 추가 캡처는 별도 최종 점검 체크섬으로 보존한다. 원본 목록을 새 산출물 때문에 덮어쓰지 않는다.

## 집계와 판정의 한계

`summary.csv`는 한 실행 안의 연산별 기술 통계다. `independent-runs.csv`는 독립 부팅별 중앙값의 중앙값·Q1/Q3를 집계한다. 10개 미만의 독립 실행에서는 CI를 비워 두고 insufficient_runs를 기록한다. 10개 이상이면 실행 중앙값을 2000회 재표집하는 percentile bootstrap 95% CI를 seed와 함께 제공한다. 이것은 비교군 간 변화율 CI·우열 판정이 아니며 최종 통계 선택은 D와 고정해야 한다.

실제 TCP goodput/CPU time per byte/p50/p99, N/L 분포, 소켓·slab 메모리 증가와 검색/삭제 비용은 이 최소 fixture에서 측정하지 않는다. `sizeof(tcp_sock)`는 CONFIG 원시값으로만 기록하며 동적 메모리 사용량으로 바꾸지 않는다. socket reset·준비·payload 검증·purge는 타이머 밖이며 이 준비 과정의 cache warming도 한계다. 오버헤드나 실행 잡음보다 작은 차이는 판정 불가로 둔다.
