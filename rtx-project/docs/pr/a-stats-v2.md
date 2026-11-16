# A — STATS v2: N 계측 정의 보강 (ACK 시점 N, 1 단위 버킷, 포트 필터)

브랜치 `a/stats-v2` · 기준 main `ffd6e62` (작성 당시 `ff2325b`, 11/23 rebase) · 작성 2026-11-16, 갱신 2026-11-23 · PR 번호: 생성 후 기입

## 1. 문제와 범위

N 분포는 "하이브리드 이득은 N ≤ K 근처에서만 생긴다"는 판단의 1순위 근거다. 측정 전에 B·D가 STATS v1(PR #12)의 세 가지 문제를 지적했고, 코드로 모두 맞음을 확인했다.

| 지적 | v1 코드 | 문제 |
| --- | --- | --- |
| N이 무엇의 분포인가 | `n_hist`는 skb 연결 직후 N (append·ordered·split 혼합) | ACK/SACK 경로가 실제로 다루는 큐 길이가 아니다. 시간 가중 점유율도 아니다 |
| K 경계 | 2의 거듭제곱 버킷 (`16-31`) | K=16에서 N ≤ K 비율을 계산할 수 없다 |
| 배경 트래픽 | `/proc/rtx_stats`는 전역 집계 | SSH 등 다른 연결이 섞인다 |
| L 승격 전 제거 비율 | 기존 카운터로 계산 가능 | 문서화만 필요 |

**완료 조건**: ① ACK 시점 N 히스토그램, ② N 0~64 정확 버킷, ③ 포트 필터와 측정 구간 초기화 절차, ④ STATS off일 때 기계어 변화 없음, ⑤ 측정 절차·계산식 문서, ⑥ D의 N 수집기(PR #29)가 v2 출력을 읽고 v1 커널도 계속 읽음.

**제외**: 실제 N 측정(D, ARM64 UTM), 수집기의 RB 빌드 지원·빌드 기록 형식, 시간 가중 점유율, 하이브리드 전용 카운터 추가(`unlink_purge`의 소속 구분 등).

## 2. 최종 변경

| 파일 | 변경 |
| --- | --- |
| `include/net/tcp_rtx.h` | `n_hist[16]` → `n_hist[2][74]` (`RTX_N_LINK`, `RTX_N_ACK`). `rtx_n_bucket()`, 포트 필터 `rtx_stats_port`·`rtx_stat_match()`, `rtx_stat_n()`. `rtx_stat_inc/add`에 `sk` 인자 |
| `net/ipv4/tcp_rtx_stats.c` | 출력: 첫 줄 `filter_port`, 히스토그램 `n_link`/`n_ack` 각 74줄(`이름 버킷 값` 세 필드). 쓰기: `0` = 초기화, `port N` = 필터, 그 외 `-EINVAL` |
| `net/ipv4/tcp_input.c` | `tcp_clean_rtx_queue()` 진입에 `rtx_stat_n(sk, RTX_N_ACK)` 1줄 추가. 기존 `rtx_stat_inc` 9곳에 `sk` 인자 (C 소유 줄, 기계적 변경) |
| `net/ipv4/tcp.c` | purge의 `rtx_stat_inc` 1곳에 `sk` 인자 |
| `net/ipv4/tcp_rtx_selftest.c` | `rtx_n_bucket()` 경계 12개 검사 |
| `net/ipv4/Kconfig` | 도움말 갱신 |
| `rtx-project/scripts/a_stats_qemu.sh` | QEMU 검증 스크립트 (신규) |
| `rtx-project/scripts/a_rtx_stats_summary.py` | 측정 덤프 요약·검사 (신규, D용) |
| `rtx-project/docs/A_N_HISTOGRAM.md` | 계측 정의·측정 절차·계산식·결과 칸 (신규). 2절은 D 수집기 기준 |
| `rtx-project/scripts/run_arm64_n_distribution.py` (D) | v1·v2 출력 모두 파싱(형식 자동 판별). v2면 측정 동안 포트 5001 필터를 걸고 끝나면 원래 값으로 복원(`port_filter`). 시도별 `n_link`=삽입·`n_ack`=`ack_clean` 검사, `le_share`(N≤K) 출력. `distribution.csv`에 `histogram` 열 추가 |
| `rtx-project/scripts/run_arm64_tcp.py` (D) | `Network.case()`에 `port` 인자(기본 0 = 기존 동작). 다른 호출부는 그대로 |
| `rtx-project/tests/test_arm64_n_distribution.py` (D) | 기존 5개를 새 반환 형식에 맞추고, v2 시험 5개 추가 |

**출력 형식 변경** (`/proc/rtx_stats`):

```
filter_port 5001
insert_append 975
...
ack_trim 0
n_link 0 0
n_link 1 124
...
n_link 64 0
n_link 65-127 0
...
n_link 16384- 0
n_ack 0 0
...
```

**도메인 간 영향**

- **C**: 호출부 카운터가 `rtx_stat_inc(sk, RTX_STAT_...)`로 바뀐다. 새 카운터를 추가할 때도 이 형식. PR #23의 `c_stress.c`는 `per_cpu(rtx_stats, cpu).cnt[id]`를 직접 읽는데 `cnt` 필드는 그대로라 영향 없다. `echo 0` 초기화도 그대로 동작한다. `CSTRESS_STAT` 파서 정규식(`(\w+) (\d+)$`)은 세 필드인 히스토그램 줄을 읽지 않고, `filter_port 0`이 한 항목 더 들어간다.
- **B (PR #25)**: `tests/benchmark/apply_overlay.py`가 `tcp_rtx.h`의 문자열 `rtx_stat_inc(RTX_STAT_PROMOTE);`를 기준점으로 쓴다. 이 PR 이후에는 `rtx_stat_inc(sk, RTX_STAT_PROMOTE);`로 바꿔야 한다(42·43행). 커널 코드 충돌은 #25가 바꾼 승격 줄 1곳뿐이다(4절). 팀 결정대로 #25 머지 후 이 PR을 그 위로 rebase하면서 A가 함께 고친다.
- **D**: PR #29의 수집기는 `n_hist` 16구간만 받아서 v2 커널에서는 모든 시도를 거부한다. 이 PR에서 v1·v2를 모두 읽도록 고쳤다(2절 표). v1 C23 커널의 예비 실행은 그대로 동작하고 결과 형식도 같다(`distribution.csv`에 `histogram` 열만 추가). v2 커널은 STATS v2를 포함한 SHA로 다시 빌드해야 한다. 측정 절차는 D 문서가 기준이고 [A_N_HISTOGRAM.md](../A_N_HISTOGRAM.md) 2절은 v2에서 달라지는 점만 적었다.
- **A 증거 도구(패치 0006, 미push)**: `a_qemu_test.sh`의 `n_hist` 합계 검사를 `n_link`로 바꿔야 한다 (작업 ④).

## 3. 대안과 트레이드오프

| 선택 | 고려한 대안 | 결정과 이유 |
| --- | --- | --- |
| N 샘플 시점 | (a) `tcp_clean_rtx_queue()` 진입, (b) 시간 가중(타이머 샘플링) | (a). 하이브리드 비용·이득은 ACK·SACK 처리마다 생긴다. 시간 가중은 타이머·소켓 순회가 필요하고 비교 목적에 맞지 않는다. 문서에 "이벤트 가중"임을 명시 |
| v1 `n_hist` | 삭제 / 유지 | `n_link`로 이름만 바꿔 유지. 송신 쪽 보조 지표이고 v1 결과와 연속성이 있다 |
| 버킷 | 전부 1 단위 / 1 단위 + 로그 | N 0~64 1 단위 + 9개 로그 버킷. K 후보(1~64)는 정확하고 메모리는 CPU당 약 1.3KB. K > 64는 설계 범위 밖 |
| 필터 판정에 필요한 `sk` | (a) `rtx_stat_inc(sk, id)`로 시그니처 변경, (b) 매크로가 호출부의 지역 변수 `sk`를 암묵적으로 사용 | (a). (b)는 호출부 변경이 없어 B의 기준 문자열이 안 깨지지만, 숨은 의존이라 리뷰·유지보수가 어렵다. 대가로 호출부 10곳과 B 도구 기준 문자열 1개가 바뀐다 |
| 필터 기준 | 로컬 포트만 / 로컬·원격 둘 다 | 둘 다. 송신 쪽(원격 5001)과 수신 쪽(로컬 5001)을 모두 잡는다. 수신 쪽은 보낸 데이터가 없어 재전송 큐 이벤트가 없다 |
| 필터 설정 시 초기화 | 자동 / 별도 | 별도(`echo 0`). 순서를 절차로 명시하는 편이 덜 놀랍다 |
| D 수집기 대응 | (a) v2만 지원, (b) v1·v2 둘 다 | (b). D가 v1 C23 커널로 예비 실행을 먼저 하므로 v1이 깨지면 안 된다. 판별은 `n_link`/`n_ack`/`filter_port` 유무 |
| 수집기의 시험 포트 | (a) 고정 5001, (b) 서버가 받은 임시 포트로 그때마다 필터 | (a). (b)는 서버 준비 뒤·전송 전에 필터를 써야 해서 `Network.case()` 안쪽을 크게 바꿔야 한다. netns가 매번 새로 생기므로 고정 포트 충돌이 없다 |
| 수집기에서 필터와 차분 | 필터+`echo 0` / 필터+전후 차분 | 전후 차분 유지. D 방식(전역 초기화 안 함, 기존 값 보존)과 맞춘다 |

## 4. 검증 방법과 결과

환경: Claude 작업 환경(x86_64 클라우드), gcc 13.3.0. 성능 수치는 없다.

| 검증 | 명령·설정 | 결과 |
| --- | --- | --- |
| STATS·DEBUG off 기계어 동일 (RB) | tcp*.o 7개 + `tcp_ipv6.o`, main `ff2325b` 대비, `x86_64_defconfig` | **8/8 SAME** |
| STATS·DEBUG off 기계어 동일 (하이브리드 K=16) | 같음 | **8/8 SAME** |
| STATS on 빌드 | RB+STATS, 하이브리드+DEBUG+STATS, tcp 오브젝트 + `tcp_rtx_stats.o`·`tcp_rtx_selftest.o` | 경고·오류 0 |
| 스타일 | `scripts/checkpatch.pl --no-tree` (커널 diff), `git diff --check` | 0 오류, 0 경고 |
| QEMU (하이브리드 K=16+DEBUG+STATS) | `rtx-project/scripts/a_stats_qemu.sh -o …` | **PASS**: 자체 테스트 761/0, 필터 5001에 5002 트래픽 20MB → 전부 0, 5001 트래픽 50MB → `n_link` 합 934 = 삽입 934, `n_ack` 합 189 = `ack_clean` 189, 잘못된 입력 거부, `port 0` 해제, WARNING 0 |
| QEMU (RB+DEBUG+STATS) | `a_stats_qemu.sh --rb -o …` | **PASS**: 자체 테스트 568/0, 합계 911 = 911, 170 = 170, 나머지 동일 |
| PR #25 위 적용 (시뮬레이션) | main + #25(`4e204ff`) 병합 후 4개 커밋 cherry-pick. #25 자체의 `tcp_rtx_selftest.c` 충돌(main #21과)은 양쪽 함수를 모두 남겨 임시 해결 | `tcp_rtx.h` 승격 줄 1곳 충돌 → `__rtx_tree_append(sk, skb); rtx_stat_inc(sk, RTX_STAT_PROMOTE);`로 해결. V3·V4 + DEBUG + STATS 빌드 경고 0. STATS off 기계어 V4·RB 각 8/8 SAME. `apply_overlay.py` 기준 문자열 수정 필요 확인 |
| D 수집기 단위 시험 | `cd rtx-project && python3 -m unittest discover -s tests -p 'test_*.py'` | **110개 통과** (D 기존 105 + v2 5). v2 거부 7가지가 각각 의도한 이유로 실패함을 따로 확인(필터 변경, 삽입 불일치, `ack_clean` 불일치, ACK 표본 0, 구간 누락, v1·v2 혼합, 형식 변경) |
| D 수집기에 실제 v2 출력 | QEMU 증거의 "필터 5001, 트래픽 없음" 덤프를 before, "포트 5001 전송" 덤프를 after로 `distribution()` | 하이브리드 표본 934·ACK 189, RB 911·170. `n_ack` N≤16 비율 0.931 / 0.900 (요약 스크립트와 같음) |
| 최신 main 적용 | 11/23 main `ffd6e62`(#27·#29·#31 포함) 위에 4개 커밋 `git am` | 충돌 없음 |
| 요약 스크립트 | 위 두 덤프에 `a_rtx_stats_summary.py` / `ack_clean` 값을 바꾼 덤프 | checks 모두 True, 종료 0 / 조작한 덤프는 False, 종료 1 |

미실행: VM(gcc 9.4) 빌드, ARM64 빌드·부팅, 손실·SACK 경로의 실제 카운터(루프백 무손실이라 `find_cover`·`sack_*`는 0), **ARM64 VM에서의 수집기 실제 실행**(netns·netem·포트 필터 쓰기는 모의 시험만).

## 5. 증거

| 주장 | 파일 |
| --- | --- |
| STATS off 기계어 동일, 빌드 경고 0 | [`evidence/A/1116_objdiff_stats_v2_ref.txt`](../../evidence/A/1116_objdiff_stats_v2_ref.txt) (`_ref`: 세션 임시 스크립트, 0006의 `a_objdiff.sh` 반영 후 재생성) |
| QEMU 하이브리드 | [`evidence/A/1116_stats_qemu_hyb16.txt`](../../evidence/A/1116_stats_qemu_hyb16.txt) |
| QEMU RB | [`evidence/A/1116_stats_qemu_rb.txt`](../../evidence/A/1116_stats_qemu_rb.txt) |

증거 파일의 `commit` 값은 Claude 작업 환경의 로컬 커밋이다. 패치를 `git am`으로 적용하면 해시가 바뀐다. 커널 변경의 동일성은 patch-id(`git show <커널 커밋> | git patch-id --stable`)로 확인한다: `337d47591e84`.

## 6. 실패와 수정

| 문제 | 원인 | 수정 |
| --- | --- | --- |
| QEMU에서 연결 실패 (`Network is unreachable`) | initramfs에서 `lo`를 올리지 않음 | `ifconfig lo 127.0.0.1 up` |
| busybox `nc` 전송 신뢰성 | 이전 작업(3.6)에서 5KB만 보내고 끝난 전례 | 스크립트 안에서 작은 정적 송수신 프로그램을 빌드해 사용, 수신 바이트 수 검사 |
| QEMU가 종료되지 않고 600초 대기 | tinyconfig에 ACPI가 없어 `poweroff -f`가 정지만 함 | `reboot -f` + `-no-reboot` |
| checkpatch 80자 경고 3개 | — | 줄 나눔. 이후 기계어 비교 재실행(8/8 SAME 유지) |

## 7. 남은 작업

- D: 이 PR 머지 후 STATS v2 포함 SHA로 C23 HYBRID 다시 빌드 → 수집기로 7조건 측정 → [A_N_HISTOGRAM.md](../A_N_HISTOGRAM.md) 4.2절 표. v1 커널 예비 실행은 머지 전에도 가능.
- D: 수집기 리뷰 — 포트 5001 고정, 필터 복원, CSV `histogram` 열.
- B: PR #25 벤치마크 도구의 기준 문자열 갱신(2절). 머지 순서에 따라 A가 rebase 시 함께 고칠 수도 있다.
- C: `rtx_stat_inc(sk, …)` 형식 확인 (리뷰 요청).
- A: 0006의 `a_qemu_test.sh`에서 `n_hist` → `n_link`, objdiff 증거 재생성 (작업 ④).
- `unlink_purge`는 소속 구분이 없어 L 승격 전 제거 비율에 purge 몫이 섞인다. 필터로 측정 연결만 세면 연결당 K개 이하라 무시 가능하다고 판단했지만, 필요하면 후속으로 구분 카운터를 추가한다.
