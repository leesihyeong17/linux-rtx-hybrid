# A: DSACK_OFO 직후 lost/retrans RB·HYBRID 차이 조사

- 대상: D의 [#26 ARM64 회귀 문서](issue-15-d-arm64-c-regression.md) 10절 "DSACK_OFO 직후 lost/retrans는 HYBRID 1/1, RB 0/0, 원인 미확정"
- 기준 SHA: main `f78f562` (= PR #25 통합 merge `78ddfe8`와 같은 트리)
- PR: (생성 후 링크)
- 결론: **정확성 차이 아님.** 두 커널 모두 같은 RACK 판정을 하고 같은 최종 상태에 도달한다. 10ms 시점 값은 RACK reorder 타이머가 시험 프로그램의 `TCP_INFO` 읽기보다 먼저 울렸는지에 따라 달라지는 **타이밍 경합**이다.

## 1. 문제와 범위

D의 패킷 시험 `DSACK_OFO`(`tests/runtime/packet_peer.c` 시나리오 6)는 4,000바이트(MSS 1000 × 4)를 받은 뒤 `ACK=base` + 블록 `[base+1000,base+2000)`(DSACK) + `[base+1000,base+3000)`를 보내고, **10ms 뒤** `TCP_INFO`의 `lost/retrans`를 출력한다. 판정기는 `unacked=4 sacked=2`만 검사하고 `lost/retrans`는 값을 가리지 않는다. 같은 시험이 HYBRID 로그에서 1/1, RB 로그에서 0/0으로 달라 원인을 확인했다.

- 완료 조건: 차이가 하이브리드 큐 구조 때문인지(정확성) 타이밍 때문인지 판정하고 근거를 남긴다.
- 포함: 코드 경로 분석, QEMU 반복 재현(RB·HYBRID K=16·V4, DEBUG/STATS on·off), 시점별 상태 추적, RACK 판정값 추적.
- 제외: ARM64·D의 UTM 환경 재실행, 커널 코드 변경(이번 PR은 문서·증거만), 손실 복구 지연의 성능 측정.

## 2. 원인

1. **`lost`를 만드는 것은 RACK이고, RACK은 재전송 큐 구조를 보지 않는다.** `tcp_rack_detect_loss()`는 `tp->tsorted_sent_queue`(송신 시각 순 리스트)와 skb 송신 시각, `tp->rack` 상태만 쓴다(`net/ipv4/tcp_recovery.c`). 하이브리드가 바꾸는 `tcp_rtx_queue`(rbtree T + 리스트 L)는 SACK 표시(`tcp_sacktag_walk`)에만 관여하며, 그 결과(어떤 skb가 SACK됐는지)는 모델 동등성 시험(PR #35)과 커널 자체 테스트로 RB와 같음을 확인했다.
2. **seg1을 잃었다고 판정하는 시점이 두 갈래다.** `remaining = rack.rtt_us + reo_wnd - age(seg1)`이다.
   - seg1과 seg2~4의 송신 시각이 같으면 `remaining = reo_wnd`(이번 실행 약 120~190µs) > 0 → 즉시 판정하지 않고 **reorder 타이머**를 건다. 타이머 길이는 `usecs_to_jiffies(remaining) + TCP_TIMEOUT_MIN(2 jiffies)`이라 HZ=250에서 이론상 약 8~12ms이고, 이번 관찰값은 폴링·계측 영향을 포함해 약 12~17ms였다(E5 폴링 로그의 `lost_after_us`, E7의 `age`).
   - seg1이 seg2~4보다 `reo_wnd` 이상 먼저 나갔으면 ACK 처리 중에 **즉시** lost로 표시한다. `tcp_sendmsg_locked()`는 첫 skb를 `tcp_push_one()`으로 먼저 보내고 나머지는 복사 후 보내므로, 이 간격은 실행 타이밍에 따라 생기기도 하고 안 생기기도 한다(코드 사실 + 아래 실험으로 두 경우 모두 관찰, µs 간격 자체는 직접 측정하지 못함).
3. **시험은 10ms에 상태를 읽는다.** 타이머 경로일 때 8~12ms 타이머와 10ms 읽기가 겹쳐 0/0(Disorder, 타이머 대기)과 1/1(Recovery, seg1 재전송) 중 어느 쪽이든 나온다.

## 3. 검증 방법과 결과

환경: Claude 클라우드 x86_64, gcc 13.3.0, QEMU TCG 1 vCPU/512MiB, tinyconfig + TUN·NET_NS·IPv6(HZ=250). 커널 소스 `78ddfe8`(= main `f78f562` 트리). peer는 D의 `packet_peer.c` + `native_packet_main.c`를 정적 빌드해 `unshare -n`으로 매번 새 네트워크 네임스페이스에서 실행(D의 native 방식과 같은 진입점). 재현: `rtx-project/evidence/A/1123_dsack_ofo/tools/run.sh`, 요약: `tools/summarize.py logs/*.txt`(→ `summary.txt`).

| 실험 | peer 변형 | 커널 | 10ms 시점 0/0 비율 | 이후 상태 | 비고 |
|---|---|---|---|---|---|
| E1 | 상태 출력 추가 | RB / HYB16 / V4, DEBUG·STATS on | 2/30 · 5/30 · 1/30 | — | 세 커널 모두 0/0·1/1 둘 다 나옴 |
| E2 | +300ms 재확인 | RB / HYB16, on | 4/40 · 3/40 | 40/40 모두 lost=2 retrans=1 Loss(RTO 후) | 최종 상태 동일 |
| E3 | +6ms 재확인 | RB / HYB16, on | 0/40 · 8/40 | 40/40 모두 lost=1 retrans=1 Recovery | 0/0이던 경우도 6ms 뒤 1/1 |
| E4 | +6ms 재확인 | RB / HYB16, **off** | 3/80 · 12/80 | 80/80 모두 1/1 Recovery | DEBUG와 무관하게 재현 |
| E5 | `TCP_INFO` 100µs 간격 폴링 | RB / HYB16, off | — | 모두 1/1 | ACK 직후 첫 폴링에 이미 Recovery: RB **80/80**, HYB **0/80** |
| E6 | E5 + 첫 폴링 lost 기록 | RB / HYB16, off | — | 모두 1/1 | 즉시 판정 RB 19/20, HYB 0/20 |
| E7 | 상태 출력 + RACK `pr_info`(디버그 전용 커널) | RB / HYB16, off | 0/25 · 3/25 | 모두 1/1 | **첫 RACK 판정: 둘 다 25/25 타이머**, `remaining = reo_wnd`, seg1 `age = rack_rtt` |
| E8 | E6 + 수신 시각 기록 | RB / HYB16, off | — | 모두 1/1 | 즉시 판정 **RB 20/20, HYB 20/20** |

- 모든 실행: peer 완료 100%, 실패·타임아웃 0, 커널 WARNING/BUG 0. `drained` 후 `total_retrans`는 같은 peer 변형 안에서 두 커널이 항상 같다(E1·E2 = 2, E3~E8 = 1).
- **해석:** 같은 peer에서 커널에 따라 즉시/타이머가 한쪽으로 갈린 경우(E5·E6)가 있지만, peer를 조금 바꾸면 두 커널이 함께 타이머 쪽(E7)이나 함께 즉시 쪽(E8)으로 바뀐다. 즉 갈림은 판정 로직이 아니라 송신·처리 타이밍(µs)이 `reo_wnd` 경계 어느 쪽에 떨어지는지에서 온다. 판정에 쓰이는 값(`reo_wnd`, `rack_rtt`, `age`)과 판정 순서는 E7에서 두 커널이 같은 형태였다.
- E4에서 10ms 0/0 비율이 HYB16 쪽이 높았다(12/80 vs 3/80). 이는 HYBRID 커널의 실행 시간 차이가 위 경계를 넘는 빈도에 영향을 준 것으로 보며, **잃은 패킷 판정이 늦어지는 시간은 최대 reorder 타이머 1회(이번 환경 관찰 약 12~17ms)** 다. 정확성 문제는 아니지만 손실 복구 지연에 관한 관찰이라 성능 측정(#16)에서 참고할 수 있다. QEMU TCG 결과이므로 실제 기기 수치로 쓰지 않는다.

## 4. 대안과 트레이드오프

| 선택 | 이유 |
|---|---|
| 커널 printk 추적은 디버그 전용 빌드에서만 | 콘솔 출력이 타이밍을 바꿔(E7에서 RB도 타이머 쪽으로 이동) 원인 확인용으로만 쓰고 결론의 주 근거는 출력 없는 빌드(E4~E6, E8)로 삼음 |
| D의 peer 판정 기준은 그대로 둠 | `lost/retrans`는 타이밍 의존 값이라 판정에 넣지 않는 기존 설계가 맞다. 값을 비교하려면 `+6ms` 같은 재확인 시점을 두거나 `ca_state`를 함께 기록하는 것이 낫다(제안만, 이번 PR에서 변경하지 않음) |
| seg1~seg2 송신 간격 직접 측정은 생략 | peer 수신 시각으로는 TUN 큐에 쌓인 뒤 한 번에 읽혀 간격이 0으로 보임(E8). 커널 계측은 타이밍을 바꿈. 대신 판정값 추적(E7)과 결과 분포로 판단 |

## 5. 증거

`rtx-project/evidence/A/1123_dsack_ofo/`

| 파일 | 내용 |
|---|---|
| `summary.txt` | `logs/` 전체 요약 (위 표의 근거) |
| `logs/e1-*` ~ `logs/e8-*` | 실험별 QEMU 콘솔에서 DSACK_OFO 관련 줄만 추린 로그 (E7은 DSACK_OFO 구간의 `RACKDBG` 포함) |
| `tools/run.sh` | 커널 빌드 + initramfs + QEMU 반복 실행. `SRC=<커널 트리> tools/run.sh NAME ITER [scripts/config 인자]`, DEBUG/STATS off는 `RTX_DBG="-d TCP_RTX_DEBUG -d TCP_RTX_STATS"` |
| `tools/packet_peer_*.c` | D의 `packet_peer.c`에 계측을 더한 사본: `snapshots`(상태 출력 + 재확인, E3·E4), `snapshots_kmsg`(+ `/dev/kmsg` 표식, E7), `poll_arrival`(+ 폴링·수신 시각, E8). E1은 재확인 없이 상태 출력만, E2는 재확인 300ms, E5·E6은 `poll_arrival`에서 수신 시각 기록을 뺀 형태. 빌드: `gcc -static -O2 -DRTX_NATIVE_PEER <사본> native_packet_main.c -o peer` |
| `tools/rack_printk_debug_only.py` | E7 전용: 커널 트리의 `tcp_recovery.c`에 RACK 추적 출력을 넣는 스크립트. **디버그 빌드 전용, 커밋 대상 아님** |
| `tools/summarize.py` | 로그 요약 |

## 6. 실패와 수정

- 반복 실행 중 이전 QEMU가 종료되지 않은 상태에서 다음 실행이 같은 로그 파일에 쓴 경우가 있었다(poweroff가 halt로 끝나 QEMU가 남음). 해당 실행 결과는 버리고, `run.sh`를 "halt 문구가 보이면 QEMU를 직접 종료"하도록 고친 뒤 실험별 실행 수를 로그의 `DS iter` 수와 대조해 단일 실행만 남겼다.
- 처음 init의 출력 필터가 새 출력 줄(`RTX_LATER`)을 거르지 않아 한 번 다시 실행했다.

## 7. 남은 작업

- D 문서 10절의 "원인 미확정"에 이 문서를 연결해 달라고 D에게 요청.
- (선택) D의 peer에 `ca_state`와 재확인 시점 출력 추가 — 다음 회귀 로그에서 같은 질문이 생기지 않도록.
- (성능) 손실 판정이 reorder 타이머로 넘어가는 빈도·지연을 실제 기기에서 볼지 #16에서 판단. 이번 결과는 QEMU TCG라 수치로 쓰지 않는다.
