# 재전송 큐 길이 N 측정 — 정의·절차·결과

작성: A · 2026-11-16 · 계측 코드: STATS v2 ([PR 문서](pr/a-stats-v2.md))

이 문서는 하이브리드가 이득을 볼 수 있는 구간(**N ≤ K 근처**)이 실제 트래픽에서 얼마나 자주 나오는지를 판단하는 근거다. 1~3절은 계측 정의와 절차(지금 확정), 4절은 D의 예비 관찰 결과(측정 후 채움)다. 구현 기록은 [A_CORE_IMPLEMENTATION.md](A_CORE_IMPLEMENTATION.md) 2.4절에 있다.

---

## 1. 무엇을 재는가

N = 소켓 하나의 재전송 큐에 있는 **skb 수**(`tcp_sock::rtx_count`). GSO skb 하나가 한 개로 세어지므로 TCP 세그먼트 수가 아니다.

| 히스토그램 | 기록 시점 | 가중치 | 쓰임 |
| --- | --- | --- | --- |
| `n_ack` | `tcp_clean_rtx_queue()` 진입 시, 제거 전 | 처리한 ACK 1개당 1 (보낸 데이터가 있는 ACK만) | **주 지표.** ACK·SACK 경로가 실제로 다루는 큐 길이 |
| `n_link` | skb를 큐에 연결한 직후 (append·ordered·split 모두) | 삽입 1회당 1 | 보조 지표. 송신 쪽 큐 길이. STATS v1의 `n_hist`와 같은 시점 |

- 둘 다 **이벤트 가중 분포**다. 시간 가중 점유율(어느 시점에 큐가 몇 개였는가)이 아니다. 하이브리드의 비용·이득은 연산마다 생기므로 이벤트 가중이 비교 목적에 맞다고 판단했다.
- 버킷: **N 0~64는 1 단위**, 그 위는 `65-127`, `128-255`, … `8192-16383`, `16384-`(열린 구간). 버킷 74개. K ≤ 64에서는 N ≤ K 비율을 정확히 계산할 수 있다.
- 백엔드와 무관한 값이다. N은 TCP 동작(cwnd, 손실, GSO)이 정하고 자료구조가 정하지 않는다. 다만 하이브리드에만 있는 승격·리스트 카운터는 하이브리드 커널에서만 나온다(3절).

## 2. 측정 절차 (D, ARM64 UTM)

측정은 D의 수집기 `scripts/run_arm64_n_distribution.py`(PR #29, #28)로 한다. 이 PR에서 수집기가 STATS v2 출력을 읽도록 고쳤다. 실행 방법과 조건표, 산출물은 [D 문서](pr/issue-28-arm64-n-distribution.md)가 기준이다. 아래는 v2 커널에서 달라지는 점만 정리한다.

### 2.1 커널

| 용도 | config | 비고 |
| --- | --- | --- |
| N 분포 + 승격·L 제거 비율 + SACK 검색 비용 | 하이브리드 K=16 + `CONFIG_TCP_RTX_STATS=y`. PR #25 머지 후에는 V4(`HYBRID`+`CACHE`+`TAIL`) | 수집기는 `5.4.214-rtx-c23-hybrid` 빌드 기록과 실행 커널을 대조한다. STATS v2를 포함한 SHA로 다시 빌드해야 한다 |
| (선택) RB 대조 | 통합 RB 기본형 + `STATS=y` | N은 TCP 동작이 정하므로 백엔드와 거의 무관하다. 차이가 크면 원인을 따로 본다. 현재 수집기는 HYBRID 빌드만 받는다 |

V0에는 계측 API가 없으므로 결과를 **"V0 실측"이라 부르지 않는다.** 계측 커널 SHA와 config를 기록한다. `DEBUG`를 켜도 수집기는 전후 차분을 쓰므로 부팅 자체 테스트 값은 빠진다.

### 2.2 수집기가 v2에서 하는 일

| 항목 | v1 커널 (현재 C23) | v2 커널 |
| --- | --- | --- |
| 히스토그램 | `n_hist` 16구간 (삽입 직후 N) | `n_link`(삽입 직후 N)와 `n_ack`(ACK 처리 시 N), 각 74구간 |
| 집계 범위 | VM 전체 (SSH 등 배경 TCP 포함) | **포트 5001만.** 시작할 때 `port 5001`을 쓰고, 끝나면(실패해도) 원래 필터로 되돌린다 |
| 시험 전송 포트 | 임시 포트 | 서버를 5001에서 띄운다 (netns마다 새로 만들므로 충돌 없음) |
| 시도별 검사 | 카운터 감소·빈 표본 거부 | 위에 더해 `n_link` 표본 = 삽입 수, `n_ack` 표본 = `ack_clean`, 필터·형식이 측정 중 바뀌지 않았는지 |
| 시도별 JSON | `histogram`, `percent`, 카운터 차이 | 위에 더해 `histograms`(둘 다), `ack_samples`, `le_share`(N≤1,4,8,16,32,64 비율), `filter_port` |
| `distribution.csv` | `histogram` 열 = `n_hist` | `histogram` 열로 `n_link`/`n_ack` 구분, 각 74행 |

전후 차분 방식은 그대로다(전역 초기화를 하지 않는다). 조건(손실은 양쪽 egress 각각, 추가 RTT는 방향마다 절반, 7조건 × 3회, 1 MiB)도 D 문서 그대로다.

### 2.3 손으로 확인할 때

수집기 없이 한 번 볼 때는 다음처럼 한다.

```sh
echo "port 5001" | sudo tee /proc/rtx_stats    # 필터 (해제: port 0)
cat /proc/rtx_stats > before.txt
# ... 포트 5001로 전송 ...
cat /proc/rtx_stats > after.txt
```

전후 차분을 [`a_rtx_stats_summary.py`](../scripts/a_rtx_stats_summary.py)로 보려면 `echo 0`으로 초기화한 뒤 한 번만 읽어 넘긴다. 이 스크립트는 단일 덤프를 받는다.

### 2.4 측정이 정상인지 확인

v2 수집기는 시도마다 다음을 검사하고, 어긋나면 그 시도를 FAIL로 남기고 멈춘다.

| 검사 | 의미 |
| --- | --- |
| `n_link` 표본 수 = `insert_append + insert_ordered + insert_split` 증가량 | 삽입마다 정확히 한 번 기록됐는지 |
| `n_ack` 표본 수 = `ack_clean` 증가량 (0이면 실패) | ACK 처리마다 정확히 한 번 기록됐는지 |
| `filter_port`와 출력 형식이 전후 같음 | 측정 중 누가 필터를 바꾸지 않았는지 |

## 3. 계산식

| 지표 | 식 | 커널 |
| --- | --- | --- |
| **N ≤ K 비율 (ACK 기준)** | `Σ_{N≤K} n_ack[N] / Σ n_ack` | 모두 |
| N ≤ K 비율 (삽입 기준) | 같은 식을 `n_link`로 | 모두 |
| (수집기 출력) | 위 두 비율은 시도별 `le_share`, 조건별 합계는 `distribution.csv`에서 계산 | — |
| N 분위수 | 누적 비율 50/90/99% 지점. N ≤ 64는 정확, 그 위는 버킷 하한 | 모두 |
| **L에서 승격 전 제거 비율** | `unlink_list / (unlink_list + promote)` | 하이브리드 |
| SACK 검색 평균 비교 노드 수 | `(find_cover_tree_steps + find_cover_list_steps) / find_cover` | 모두 (list는 하이브리드) |
| SACK 검색 중 LIST 비교 비중 | `find_cover_list_steps / (tree_steps + list_steps)` | 하이브리드 |
| ACK당 제거 skb 수 | `ack_unlink / ack_clean` | 모두 |
| SACK 블록당 방문 skb 수 | `sack_walk_skb / sack_block` | 모두 |

**L 승격 전 제거 비율 주의:** `unlink_list`에는 ACK 제거 외에 연결 종료 purge(`unlink_purge`), collapse 등의 제거도 들어간다. `unlink_purge`는 소속(TREE/LIST)을 구분하지 않으므로 purge 몫을 빼 줄 수 없다. 측정 연결이 정상 종료하면 purge 시점의 큐는 거의 비어 있고, 필터로 측정 연결만 세므로 영향은 연결당 최대 K개 이하다.

Go/No-Go 판단(진행 문서 3절)은 N 분포 + 승격 빈도 + L 승격 전 제거 비율 + SACK 검색 비용을 함께 본다. 이 문서는 수치를 제공하고, 결정 근거와 결론은 4절 아래에 기록한다.

## 4. 결과

### 4.1 정확성 확인 (QEMU, 성능 수치 아님)

`a_stats_qemu.sh`로 계측 자체가 맞는지만 확인했다. QEMU TCG + 무손실 루프백이라 N 분포로 해석하지 않는다.

| 커널 | 결과 | 증거 |
| --- | --- | --- |
| 하이브리드 K=16 + DEBUG + STATS | PASS (자체 테스트 0 실패, 필터 차단, 합계 일치) | [`1116_stats_qemu_hyb16.txt`](../evidence/A/1116_stats_qemu_hyb16.txt) |
| RB + DEBUG + STATS | PASS | [`1116_stats_qemu_rb.txt`](../evidence/A/1116_stats_qemu_rb.txt) |

### 4.2 예비 관찰 (D, ARM64 UTM) — 미실행

| 조건 | 설정 추가 RTT | 회차 | `n_ack` N≤4 / ≤8 / ≤16 | p50 / p90 / p99 | 원시 파일 |
| --- | --- | --- | --- | --- | --- |
| 예비 (무손실, v1 C23 커널) | | | (v1: N≤K 불가) | | |
| 무손실 · 추가 지연 없음 | | | | | |
| 무손실 · +20ms | | | | | |
| 무손실 · +50ms | | | | | |
| 손실 1% · 추가 지연 없음 | | | | | |
| 손실 1% · +20ms | | | | | |
| 손실 1% · +50ms | | | | | |
| 손실 1% · +20ms · 송신 재정렬 25% | | | | | |

### 4.3 결론 — 미작성
