# #16 B 최소 벤치마크와 공통 tail 최적화

- 이슈: [#16 B·D 성능 비교](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/16)
- PR: [#25](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/25)
- 구현 커밋: Core `00d5aafd6`, 벤치마크·계약 `852d1486a`
- 브랜치: `feat/b-rtx-benchmark-tail`
- 기준: `0ee4a1a716d5f5f5f4ea89e5eae948c5dd30fa49`, 선행 [PR #20](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/20)
- 대상: 선행 PR이 열려 있는 동안 `test/b-sack-boundary-regression`에 쌓는 후속 PR. 선행 PR 병합 후 main 기준으로 재검토한다.
- 2026-11-23: #20 병합 후 base를 `main`으로 변경하고 main `0f2987d`를 merge했다 (아래 "main 통합 merge" 절).

## 문제와 완료 범위

B와 D는 함수 경계·입력·대조군·증거 형식을 합의했지만 실행 가능한 최소 연산 벤치마크가 없었다. 기존 RB backend에는 하이브리드와 동일한 endpoint 캐시와 tail 직접 연결이 없어 HYBRID off/on만으로 LIST 효과를 분리할 수도 없었다.

이번 PR은 실제 append/split을 실행하고 계약에 맞는 원시 로그·정규화 표본을 만드는 최소 fixture와 runner를 제공한다. 공통 CACHE/TAIL을 opt-in으로 구현하고 정확성을 검증한다. #16 전체 완료나 성능 개선 채택을 선언하지 않으며 이슈를 닫지 않는다. 최종 C 회귀, D native 환경 및 실제 TCP 측정은 남아 있다.

## 구현

| 영역 | 변경 |
| --- | --- |
| Core | `TCP_RTX_CACHE`로 양 backend의 TREE endpoint 캐시 공유. `TCP_RTX_TAIL`로 순차 삽입·승격에서 cached maximum에 직접 연결 후 기존 RB 재균형 수행 |
| 캐시 생명주기 | init/child 초기화, insert, unlink, replace, purge를 통한 마지막 삭제·재삽입 처리. 공개 API와 accounting·tsorted 책임 유지 |
| Core 회귀 | wraparound, endpoint 교체·손상 검출, 마지막 삭제 후 append, child reset, 전체 drain, RB 부모/red/black-height 검사 |
| 벤치마크 | 매 연산 새 소켓/큐, 실제 `rtx_append`·`tcp_fragment`, payload/GSO/seq/wmem/tsorted/cache 사후 검증 |
| split 입력 | 선형·page fragment·혼합·clone·GFP_ATOMIC·관찰용 할당 경계 실패 |
| 계측 분리 | performance는 total만. observation만 prepare/commit/window/Core 시간과 승격/tail 횟수 수집. 정확성 overlay와 DEBUG/STATS는 벤치마크에서 제외 |
| 수집 | schema 1 / contract 0.2. hash, host/target/execution, run_id, config, seed/순서, 실제 연산 수, null 사유·예상 실패 구분 |
| 통계 | 개별 연산은 run 내부 기술 통계. 독립 부팅 중앙값으로 별도 집계. 10회 미만 CI 생략 |

세부 알고리즘은 [tail 구현 문서](../B_TAIL_OPTIMIZATION.md), 실행·이벤트·모든 한계는 [벤치마크 README](../../tests/benchmark/README.md), 합의는 [B·D 계약](../B_D_MEASUREMENT_CONTRACT.md)에 있다.

A 영역의 캐시 저장·유지 코드를 확장했지만 B/C 호출부에 새 필드 조작을 요구하지 않는다. TREE split의 일반 삽입은 유지한다. 기존 RB 기본형, RB cache, RB tail, hybrid ordered, hybrid tail의 5개 구성을 제공한다. 최종 V3/V4 후보는 rb-tail/hybrid-tail이다.

## 선택과 트레이드오프

| 선택 | 이유와 비용 |
| --- | --- |
| 캐시와 tail을 각각 설정으로 분리 | 효과를 분리해 비교 가능. 빌드 조합과 유지 상태 증가 |
| production 기본값은 tail off | 이번 증거는 정확성과 실행 가능성이다. 측정 전에 성능 우위를 가정해 기본 동작을 바꾸지 않음 |
| 순차 삽입·승격만 직접 연결 | 증명 가능한 최대 seq 전제 사용. split의 중간 위치 삽입 최적화는 포기 |
| disposable 부팅 fixture | static TCP 구현과 실제 skb/accounting을 실행하고 구간 밖에 입력을 복원. 커널 빌드·부팅 비용, cache warming·합성 입력 한계 |
| 내부 관찰을 별도 커널로 분리 | performance 함수 본문/Core hook 오버헤드 제거. 관찰용 비용은 performance 비용과 동일시할 수 없음 |
| 할당 호출 경계 실패 | prepare 미진입과 보존 상태를 재현. allocator 내부 실패·실제 메모리 압박의 대체가 아님 |
| `ktime_get_ns`·빈 구간 동시 기록 | PMU 없는 환경에서도 실행 가능. 타이머 오버헤드와 TCG 잡음보다 작은 차이는 판정 불가 |
| 실패 run에서 전체 집계 중단 | 부정확한 비교군으로 결과를 내지 않음. 대규모 반복의 부분 재개·실패율 집계는 D 후속 |

RB 재균형은 남으므로 append 전체 O(1)을 주장하지 않는다. 이번 x86_64 config의 `sizeof(tcp_sock)` 원시값은 RB 기본 2184, RB cache/tail 2200, hybrid 2224 bytes였다. 구조체 크기이며 소켓당 총 할당량·slab 증가로 해석하지 않는다.

## 실제 검증

Ubuntu 22.04 ARM64 Docker의 로컬 기존 이미지 `962f6cadeae0`를 재사용했다. x86_64 cross GCC 11.4.0, QEMU 6.2.0 TCG, x86_64 게스트 1 vCPU/512 MiB다. ARM64 대상 커널 native 실행이 아니다. 호스트 affinity 미고정과 PMU 미요청을 기록했다.

```sh
bash rtx-project/scripts/run_hybrid_validation.sh
RTX_BENCH_ITERATIONS=3 RTX_BENCH_WARMUP_MS=0 RTX_BENCH_REPEATS=2 \
  bash rtx-project/scripts/run_b_benchmark.sh
python3 -B -m unittest discover -s rtx-project/tests/benchmark -p 'test_*.py' -v
python3 -B -m unittest discover -s rtx-project/tests/integration -p 'test_*.py' -v
python3 -B -m unittest discover -s rtx-project/tests/runtime -p 'test_*.py' -v
```

| 검증 | 결과 |
| --- | --- |
| Core IPv4/IPv6 설정 행렬 | 7개 빌드 PASS. DEBUG/STATS off/on 및 cache/tail 선택 포함 |
| Hybrid tail K=16 | Core 1325/0, B fixture 908/0 |
| Hybrid tail K=1 | Core 1190/0, B fixture 743/0 |
| RB tail | Core 810/0, B fixture 798/0 |
| 실제 패킷 회귀 | 4개 부팅 모두 SACK/LOSS/RTO 재전송 진입·성공 반환 2/1/1, runtime PASS |
| 벤치마크 전체 링크 | 5 variant × 2 mode = 10개 커널 빌드 PASS |
| 벤치마크 실행 | 각 구성 2회 독립 부팅 = 20 runs. 570개 연산, 판정 실패 0 |
| 연산 구성 | append 240, split 성공 300, 기대한 split 할당 실패 30. 실패 주입도 정상 판정 표본으로 구분 |
| 최종 수집기 재검증 | 동일 raw 로그 20개를 복사본에서 재수집해 570개 표본 PASS. 원본 증거 불변 |
| Python 판정기 | benchmark 9 + integration 2 + runtime 7 test methods PASS. 손상·누락·중복·미실행·잘못된 variant/hash 거부 포함 |
| 스타일 | Core patch 및 benchmark C/H checkpatch 0 errors/0 warnings, diff check·Python/shell 구문 PASS |

`/0`은 실패 검사 수 0이며 assertion을 독립 실험 수로 세지 않는다. 0초 워밍업·2회 반복은 **기능 확인용**이다. CI가 비어 있는 결과를 유의한 개선이나 동등성으로 보고하지 않는다. 총 연산 수 570을 반복 수 570으로 해석하지 않는다. 본 측정 기본 워밍업과 반복 수는 README에 별도 명시했다.

벤치마크 기본 K=16에서는 N=16 split의 대상이 LIST에 있다. TREE split의 성능 sweep은 미실행이다. 정확성용 K=1과 RB tail 회귀에서 TREE split을 실행한 사실과 구분한다. 실제 TCP smoke는 정확성 확인이며 처리량·지연 결과가 아니다. DEBUG와 STATS를 각각 독립 교차하는 모든 조합, 모든 K, ARM64 타깃은 검증하지 않았다.

## 증거와 재현 식별

아래는 로컬 보존 증거이며 GitHub에 업로드하지 않았다. GitHub의 상대 링크로 원본을 읽을 수 있다고 주장하지 않는다. D와 공유 저장소를 확정하면 SHA-256과 함께 옮긴다.

| 위치 (`rtx-project/.work/` 아래) | 의미 |
| --- | --- |
| `hybrid-m7C55OHm/` | Core·B·패킷 회귀, source/config/overlay, serial 원본·summary·SHA256SUMS |
| `b-bench-fgioMKuo/` | 10개 이미지, 20개 run, 원시 event·환경·명령·hash·독립 실행 요약 |
| `issue16-final-checks/` | 최종 Python tests·스타일·소스 일치 점검, 재수집 결과·캡처·최종 체크섬 |
| `issue16-final-checks/report/benchmark.png` | 실제 benchmark raw 행·hash 및 20-run 결과 HTML 캡처 |
| `issue16-final-checks/report/regression.png` | 실제 Core/B 회귀 raw 행·hash HTML 캡처 |
| `hybrid-IL5kS4bP/` | 첫 이미지 조회 실패와 정리 기록 |

검증 소스는 기준 SHA의 archive + `source.patch` + harness 사본이다. 최종 Core C/H·Kconfig, 벤치마크 C/H와 빌드 스크립트가 실행 입력과 일치하는지 확인한다. 첫 실행 뒤 collector에 variant label 검증·scope containment·환경 필드 전달을 보강했다. 커널 코드는 그대로이며 최종 collector로 원시 20개를 별도 복사본에 다시 검증했다. 최종 overlay 생성 결과도 실제 빌드 patch/hash와 비교했다. PR #25는 생성 시 충돌 없음으로 확인했고, 원격 Actions workflow와 head check는 등록되지 않아 원격 CI 통과로 표현하지 않는다. 후속 renderer와 단위 테스트의 추가는 커널 입력을 바꾸지 않는다.

스크린샷은 QEMU 콘솔 자체가 아니라 원본 로그를 행 번호·hash와 함께 렌더링한 화면이다. 임시 서버는 캡처 후 종료했다. Docker 실행의 `remaining-containers.txt`는 모두 비어 있고 작업 컨테이너를 제거했다. 기존 자원은 보존했다.

## 실패·경고와 수정

첫 correctness 실행은 로컬 Ubuntu 이미지 조회에서 `No such image`로 종료했다. 재조회로 기존 이미지를 확인하고 같은 이미지를 재사용해 성공했다. 이미지 pull이나 기존 이미지 삭제는 하지 않았다. checkpatch 초기에 지적된 Kconfig 도움말 길이와 C 줄 길이는 수정 후 다시 확인했다.

실제 커널 fixture/benchmark 실패는 없었다. 수집기 음성 대조는 손상된 입력을 의도대로 거부했다. 기존 커널의 OpenSSL deprecated 경고와 compressed kernel relocation/DT_TEXTREL 링크 경고는 남는다. 새 C 컴파일 경고를 숨기는 옵션은 추가하지 않았다. HTML 캡처 서버의 favicon 404는 보고서 로드·캡처에 영향이 없었다.

## main 통합 merge (2026-11-23, A)

- 배경: #20 병합 뒤 base 브랜치 삭제로 #25가 자동으로 닫혀 다시 열고 base를 `main`으로 바꿨다. 그 사이 main에 #21(C 카운터·find_cover sweep)과 #33(A STATS v2, `rtx_stat_inc(sk, id)`)이 들어와 충돌이 생겼다.
- 방식: rebase 대신 main `0f2987d`를 이 브랜치에 merge(`78ddfe8`). 기존 커밋 `00d5aafd6`·`852d1486a`·`4e204ffda`와 위 기준 SHA는 그대로이며 force-push가 없다.
- 충돌 해결
  - `include/net/tcp_rtx.h` 승격: `__rtx_tree_append(sk, skb); rtx_stat_inc(sk, RTX_STAT_PROMOTE);`
  - `net/ipv4/tcp_rtx_selftest.c`: 이 PR의 cache/tail 검사와 #21 sweep·#33 `rtx_n_bucket` 검사를 모두 유지, init에서 셋 다 호출
  - `tests/benchmark/apply_overlay.py`: 승격 anchor를 `rtx_stat_inc(sk, RTX_STAT_PROMOTE);`로 갱신 (텍스트 충돌은 없었지만 #33 API 변경으로 anchor가 사라짐)

| 검증 (Claude 클라우드 x86_64, gcc 13.3.0) | 결과 | 증거 (`rtx-project/evidence/A/1123_merge25/`) |
| --- | --- | --- |
| 모두 off RB 기본형, main vs merge 기계어 (TCP 오브젝트 8개) | 8/8 SAME | `objdiff_rb_main_vs_merge_ref.txt` |
| HYBRID K=16·TAIL off·DEBUG/STATS off, #25 head vs merge | 8/8 SAME → merge가 만든 기계어 변화 없음 | `objdiff_hyb16_pr25_vs_merge_ref.txt` |
| 같은 설정 main vs merge | 1/8 SAME. 차이는 `tcp_sock` 필드 오프셋(0x898~0x8a8)뿐, 오프셋을 가리면 8/8 동일 → 이 PR의 필드 배치 변경 | `objdiff_hyb16_main_vs_merge_ref.txt` |
| 빌드: V4·V3·V2 DEBUG+STATS on, V4·V3 off (`net/ipv4/` 전체 + `tcp_ipv6.o`) | 5/5 성공, tcp/rtx 경고 0 (호스트 도구 OpenSSL 경고만) | `buildmatrix_ref.txt` |
| CACHE 없이 TAIL만 켬 | `olddefconfig`가 TAIL을 조용히 제거 (A 리뷰 제안 1 재현, 미반영 상태) | `buildmatrix_ref.txt` |
| 사용자 공간 모델(#35 도구, `a_model_test.sh 78ddfe8`): 14개 빌드(rb·rb-cache·rb-tail·hyb K=1/2/4/16 ±tail 등) | 시나리오 17개, 랜덤 2,000 seed·명령 532만, 불일치 0, 변이 17/17 검출 | `model_78ddfe8.txt` |
| QEMU tinyconfig V4(HYBRID K=16+CACHE+TAIL)+DEBUG+STATS | 자체 테스트 1364/0 (cache/tail·sweep·hybrid 검사 모두 실행 로그 확인), STATS v2 포트 필터·`n_link`=삽입·`n_ack`=`ack_clean` PASS, WARNING/BUG 0 | `stats_qemu_v4_ref.txt` |
| `git diff --check`, checkpatch(merge 결과의 Core·selftest·Kconfig·tcp.h, main 대비) | 문제 없음 | — |
| `rtx-project` Python 시험 (tests 110, benchmark 9, integration 2), 두 overlay를 merge 소스 사본에 적용 | 모두 통과 | — |

`_ref` 파일은 아직 레포에 없는 도구(A 증거 도구 0006의 `a_objdiff.sh`에 설정 추가 옵션, `a_stats_qemu.sh`에 CACHE/TAIL 추가)로 만들었다. 미실행: ARM64·GCC 9.4, 이 PR의 Docker 회귀·벤치마크 재실행, 실제 TCP 시험. B 확인 필요: 리뷰 제안 2개(TAIL `select`/`.config` 검사, `__rtx_tree_append` 호출자 주석), D가 보고한 `bm_valid` 스택 1,040B.

## 남은 작업과 인계

1. **C**: shift/merge, 누적 ACK 정리, DSACK 등 잔여 기본 회귀를 마치고 최종 통합 SHA를 고정한다.
2. **D**: 공유 증거 저장소, 고정 CPU/VM 조건, ARM64 대상 native 빌드·실행, PMU 실제 지원 여부를 확인한다. 현재 ARM64 호스트의 x86_64 QEMU 재현과 구분한다.
3. **B·D**: TREE/LIST별 split, N/K·MSS·split 위치·fragment 수의 sweep과 반복/묶음 크기를 예비 측정으로 고정한다. 현재 준비/검증의 cache warming·타이머 오버헤드를 평가한다.
4. **D**: 실제 TCP goodput, CPU time/byte, 요청 지연 p50/p99, 큐 분포·승격 빈도와 소켓/slab 메모리를 별도 측정한다. 부팅 fixture 결과로 대체하지 않는다.
5. **B·D·A**: 같은 최종 SHA의 V3/V4 비교에서 공통 기능/config diff를 검토하고 변화율 CI·주 workload·메모리 허용량·회귀 기준을 확정한다. 현재 tail은 측정 가능한 후보이며 채택 판단은 후속이다.
6. **통합**: PR #20 병합 후 후속 PR의 base와 변경 범위를 재확인한다. #16 전체를 닫지 않는다.
