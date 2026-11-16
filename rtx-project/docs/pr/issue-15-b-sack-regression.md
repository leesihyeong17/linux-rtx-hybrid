# #15 B 도메인 SACK·split 경계 회귀

이슈: https://github.com/leesihyeong17/linux-rtx-hybrid/issues/15

PR: https://github.com/leesihyeong17/linux-rtx-hybrid/pull/20
구현·검증 문서 커밋: `432026cf5080532f2358b5edcbfd92d81994b9a9`.

기준 커밋: `73747d3f1657c577fcd0f9a8179bcb7fbdb9e78e`.
작업 브랜치: `test/b-sack-boundary-regression`. PR 대상은 저장소의 통합 브랜치 `main`이다.

## 문제와 범위

#14에서 LIST·승격과 B의 split API 연결은 구현됐다. 이번 작업은 새 큐 구현이 아니라 SACK 순회가 그 구현을 소비할 때의 회귀 검증이다. 기존 `case_hybrid.c`는 독립적인 한 번의 분할을 검사했다. SACK에 의한 연속 두 번 분할과 두 번째 할당 실패 후 상태는 검사하지 않았다. 기존 B 검사기는 SACK 처리로 시간 큐에서 정상 제거된 skb도 손상으로 판정했다.

B 범위의 완료 기준은 실제 SACK 함수에서 split 경로 도달을 확인하고, 분할 후 순서·회계·payload·플래그·시간 큐·transaction·highest_sack을 검증하는 것이다. #15 전체 종료를 뜻하지 않는다. C의 shift/merge, 실제 누적 ACK 정리, DSACK 조합은 아래 남은 작업으로 유지한다.

## 구현

production의 `net/ipv4/`와 `include/net/` 파일 및 공개 Core API는 변경하지 않는다. 테스트 파일은 disposable source overlay를 통해 시험 커널에만 포함된다.

| 파일 | 구현과 의미 |
| --- | --- |
| `tests/integration/case_sack.c` | 실제 `tcp_sacktag_skip()` → `tcp_sacktag_walk()` → `tcp_fragment()`를 실행하는 9개 시나리오. 단일 skb, K-full head/middle/tail, 두 번째 할당 실패·재시도, MSS 비정렬, wrap, TREE/LIST 교차와 gap |
| `tests/integration/apply_overlay.py` | `tcp_input.c`의 static SACK 함수에 접근하는 시험 wrapper 추가. 정상 SACK의 state·highest_sack 초기화만 제공하며 split/승격/회계 로직은 재구현하지 않음 |
| `tests/integration/hooks.h` | 관찰 소켓의 tcp_fragment 내부 할당 호출을 세고 지정한 N번째 호출에서 NULL 반환. 기존 unclone/즉시 할당 실패 시험 유지 |
| `tests/integration/common.h` | ACKED skb의 정상 tsorted 이탈 허용. 비ACKED·비LOST 또는 미ACKED 재전송 skb의 누락은 계속 거부. 큐 밖의 비어 있지 않은 anchor도 거부 |
| `tests/integration/case_invariants.c`, `case_tsorted.c` | 기존 음성 대조를 새 상태 정의에 맞춰 수정. ACKED 유무·LOST·RETRANS 6가지 상태에서 누락 허용 여부 검사 |
| `tests/integration/render_report.py` | 새 사례의 원본 serial 행·hash를 보고서로 출력 |

`case_sack.c`는 데이터가 선형 영역에 있는 skb를 만든다. 이 형태는 shift 최적화 대신 실제 split 경로로 진행하도록 제어한다. 각 정상 시나리오에서 prepare/commit 각각 2회를 검사하므로 split을 건너뛰고 우연히 같은 최종 상태에 도달한 경우는 통과하지 않는다. gap은 포함 검색 계약을 검사하려고 만든 인위적인 큐이며, 정상 송신 스트림에 그런 구멍이 생긴다고 가정하지 않는다.

두 번째 할당 실패에서는 첫 분할의 앞·뒤 조각과 바뀐 경계를 유지한다. prepare/commit은 각각 1회, 할당 시도는 2회, 주입 실패는 1회여야 한다. 이어 같은 SACK을 재시도하면 총 prepare/commit은 각각 2회, 할당 시도는 3회가 된다. 실패 뒤에도 열린 transaction, 중복 회계, 손상된 구조가 없어야 한다. 첫 분할을 롤백하는 코드는 추가하지 않는다.

SACK된 중간 조각만 ACKED인지, highest_sack이 그 뒤 조각인지 검사한다. MSS 정렬 경계에서는 반환 cursor도 뒤 조각이다. 비정렬 끝점은 뒤 조각의 한 MSS 내부에 있으므로 walker가 그 조각을 SACK 표시 없이 방문하고 다음 노드를 반환한다. 이 시험의 tail 사례에서는 반환값이 NULL이어도 highest_sack은 뒤 조각이다. 같은 SACK 반복 시 새 split이나 delivered/sacked_out 중복 증가가 없어야 한다. 전체 범위를 추가 SACK하면 highest_sack은 NULL이고 tsorted는 비어야 한다. 마지막에는 기존 purge 검증으로 큐·캐시·힌트·wmem·packets_out 정리를 검사한다. fixture teardown의 sacked/lost/retrans 카운터 정규화는 실제 누적 ACK 회계 검증을 대체하지 않는다.

## 선택과 트레이드오프

| 검토한 선택지 | 선택 이유 | 한계 |
| --- | --- | --- |
| 수동으로 ACKED 표시 / 실제 SACK walker 호출 | 실제 C 순회가 split 이후 반환 cursor와 accounting을 소비하도록 후자를 선택 | 전체 ACK 옵션 파싱·캐시·DSACK 처리는 통과하지 않으므로 별도 패킷 검증 필요 |
| 패킷만으로 모든 경계 생성 / 제어된 fixture와 기존 패킷 시험 병행 | K, 분할 위치와 N번째 실패를 결정적으로 만들 수 있는 fixture 선택 | 실제 NIC/GSO 환경의 shift 성공이나 타이머 경합은 재현하지 않음 |
| allocator 내부 실패 / tcp_fragment 할당 호출 경계 실패 | 첫 split 성공·두 번째 실패를 정확하게 지정 | 메모리 고갈·allocator 내부 정리의 검증은 아님 |
| tsorted 검사 제거 / 정상 상태만 확장 | ACKED의 정상 이탈을 허용하면서 비ACKED 누락 거부를 유지 | 검사기는 bounded fixture용이며 임의 메모리 손상에 안전한 일반 검증기가 아님 |
| 모든 K 실행 / K=16과 K=1 | 대표 크기와 최소 용량의 자기 승격 경계에 집중 | fixture는 최대 32노드, K<=24. production의 K=1~1024 전수 검증 아님 |

새 production 상태·메모리 비용은 없다. 계측과 DEBUG/STATS를 켠 실행은 성능 측정이 아니며 처리량 또는 cycles 개선을 주장하지 않는다.

## 재현과 실행 결과

```sh
bash rtx-project/scripts/run_hybrid_validation.sh
python3 -B -m unittest discover -s rtx-project/tests/integration -p 'test_*.py' -v
python3 -B -m unittest discover -s rtx-project/tests/runtime -p 'test_*.py' -v
python3 -B rtx-project/tests/integration/render_report.py rtx-project/.work/<run>
```

runner는 기준 HEAD archive + 추적된 working diff와 별도 harness 복사본을 보존한다. 새 fixture 파일도 복사본에 포함된다. 따라서 커밋 전 검증은 SHA만으로 재현하지 않고 `source.patch`, `harness/`, `instrumentation.patch`를 함께 사용해야 한다. 실행 사본과 최종 커밋 대상 파일의 일치를 별도로 확인한다.

2026-11-16 최종 실행 `hybrid-8sXfJOmL`에서 runner exit code 0과 PASS를 확인했다. 기존 Ubuntu 22.04 arm64 이미지 `962f6cadeae0`를 재사용했다. x86_64 cross GCC 11.4.0, QEMU 6.2.0 TCG, x86_64 guest 1 CPU / 512 MiB 환경이다.

| 실제 실행 | 결과 |
| --- | --- |
| RB/HYBRID × DEBUG·STATS off/on의 네 구성 | fixture overlay 적용 전 IPv4·IPv6 빌드 통과 |
| K=16 원본 커널 부팅 | Core 722/0, TCP smoke 4개, SACK/TLP/RTO 패킷 시험 통과 |
| K=16 fixture 커널 | Core 722/0, B 908/0. 새 SACK 451/0, tsorted 60/0 포함 |
| K=1 fixture 커널 | Core 647/0, B 743/0. 새 SACK 331/0, tsorted 60/0 포함 |
| 세 부팅의 패킷 추적 | 각 부팅에서 SACK split 2회, TLP 1회, RTO 1회와 성공 반환 확인 |
| 로그 판정기 단위 테스트 | integration 2개 + packet trace 7개 통과 |
| 정적 검사 | C 패치 checkpatch 오류 0, 새 파일 추가에 따른 MAINTAINERS 확인 경고 1. 새 C 파일별 검사는 오류/경고 0. diff whitespace·Python 구문·runner shell 구문 통과 |

`/0`은 실패 수 0을 뜻한다. assertion 수는 입력·상태 검사의 개수이며 독립적인 네트워크 시나리오 수가 아니다. K=1에서 head/middle/tail이 같은 노드인 점도 고려해야 한다. 새 fixture 자체는 DEBUG/STATS on에서 실행했다. off 구성의 production 빌드는 off fixture 실행과 구분한다. DEBUG와 STATS를 각각 독립적으로 교차한 8개 구성은 실행하지 않았다.

최종 세 부팅에서 kernel WARNING/BUG/Oops/panic과 B 실패는 없다. 네 부분 빌드에는 모두 동일한 기존 OpenSSL deprecated 경고 7개가 남았다. 각 전체 커널 링크에는 기존 compressed kernel relocation/DT_TEXTREL 경고 2개가 있으며 새 C 경고는 없다. 경고 억제 옵션은 추가하지 않았다. checkpatch의 MAINTAINERS 경고는 `rtx-project/tests`에 fixture 두 파일을 추가해서 발생한 일반 안내다. 기존 팀 테스트 영역에 속하며 커널 production 유지보수 범위를 바꾸지 않아 MAINTAINERS는 수정하지 않았다.

시험 harness 14개 파일, runner·컨테이너 빌드 스크립트·runtime 입력을 최종 작업 파일과 비교해 일치를 확인했다. overlay 적용 전 tcp_input/tcp_output hash도 저장소의 production 코드와 같다. 저장소에 GitHub Actions workflow는 등록되어 있지 않아 원격 CI 성공을 이 로컬 실행 결과로 표현하지 않는다.

### 증거와 자원 정리

아래 증거는 로컬에 보존했으며 GitHub에 업로드하지 않았다. 상대 링크는 로컬 작업 공간에서만 열린다. 캡처는 실제 로그의 행 번호·SHA-256을 표시하는 HTML 화면이며 QEMU 콘솔 자체의 스크린샷이 아니다.

- [K=16·K=1 최종 결과 캡처](../../.work/hybrid-8sXfJOmL/report/summary.png)
- [tsorted 상태·음성 대조 캡처](../../.work/hybrid-8sXfJOmL/report/tsorted.png)
- [첫 실패 캡처](../../.work/hybrid-9UKLCp51/report/failure.png), [첫 실패 원본 serial](../../.work/hybrid-9UKLCp51/serial.log)
- [K=16 serial](../../.work/hybrid-8sXfJOmL/serial.log), [K=1 serial](../../.work/hybrid-8sXfJOmL/serial-k1.log), [원본 커널 패킷 serial](../../.work/hybrid-8sXfJOmL/serial-packets.log)
- [환경](../../.work/hybrid-8sXfJOmL/environment.txt), [K=16 설정](../../.work/hybrid-8sXfJOmL/hybrid-on/kernel.config), [K=1 설정](../../.work/hybrid-8sXfJOmL/k1.config)
- [실행 harness 일치 확인](../../.work/hybrid-8sXfJOmL/verified-harness-hashes.json), [빌드 경고 비교](../../.work/hybrid-8sXfJOmL/build-warnings.json)
- [빌드 입력·바이너리·로그 SHA-256](../../.work/hybrid-8sXfJOmL/SHA256SUMS), [설정·캡처·추가 증거 SHA-256](../../.work/hybrid-8sXfJOmL/EVIDENCE_SHA256SUMS)

| 커널 산출물 | SHA-256 |
| --- | --- |
| K=16 원본 | `183361d00e316a7f02055270a210b031edaff398e0e965fb0dc349bc356a5438` |
| K=16 fixture | `98fd1135fe3d4d78e238b3942e73d471045234150975531999a8033548915640` |
| K=1 fixture | `87c05cac62ac18f4923c6d47cf225c1ae1af759927ce468bd4019fc50cdfd3ce` |

첫 환경 실패·fixture 실패·최종 성공 실행의 task 컨테이너는 모두 제거했고 `remaining-containers.txt`가 비어 있음을 확인했다. 기존 컨테이너·이미지·볼륨은 보존했다. 캡처용 localhost 서버도 종료했다.

## 첫 실행 실패와 수정

`hybrid-9UKLCp51`은 네 구성 빌드, 원본 커널 부팅·패킷 시험을 통과했으나 K=16 fixture에서 901 assertion 중 4개가 실패했다. 실패는 새 테스트 기대값에 있었다. production TCP 구현을 수정하거나 경고를 무시하지 않았다.

- `find_cover()`를 lower_bound로 잘못 기대한 2개 검사를 수정했다. `design.md`의 포함 검색 계약과 `tcp_rtx.h`의 구현은 `[seq, end_seq)`를 포함하는 노드가 없으면 NULL이다. gap의 첫 바이트와 마지막 바이트는 NULL, 실제 다음 노드 시작점은 그 노드인지 검사한다.
- 비정렬 SACK 끝점에서 walker 반환 cursor를 highest_sack과 동일하게 기대한 2개 검사를 수정했다. 마지막 1-MSS 조각은 부분 SACK으로 표시되지 않고 순회만 지나간다. 따라서 반환 cursor는 그 다음 항목(NULL)이고 highest_sack은 미SACK 뒤 조각이다. 두 포인터의 기대값을 분리했다. 중간 SACK 표시·분할 횟수·전체 회계 검사는 유지했다.

원본 실패는 `serial.log`의 `BVERIFY FAIL` 4행과 `BVERIFY_TOTAL checks=901 failed=4`로 보존된다. checker가 이를 거부해 runner exit code는 1이며 K=1 단계는 이 첫 실행에서 수행되지 않았다.

그보다 앞선 `hybrid-frgwuEGQ`는 image inspect에서 `No such image: ubuntu:22.04`로 중단됐다. 독립 재조회에서 같은 로컬 이미지가 확인돼 이를 재사용했다. 원인은 확정하지 않았으며 새 이미지 pull은 하지 않았다. 빌드 이전 환경 조회 실패를 커널 회귀 실패로 계산하지 않는다. 해당 종료 코드와 cleanup 기록은 보존했으나 터미널 오류 자체의 스크린샷은 없다.

## #15 매트릭스와 남은 작업

| #15 조건 | 이번 B 실행 결과 | 남은 범위 |
| --- | --- | --- |
| LIST 여유 split | 통과. K=16 단일 skb에서 연속 분할, 세 조각 payload·순서 | 모든 길이 조합 전수 시험 |
| K-full head/middle/tail | 통과. 실제 SACK 분할, 자기/다른 head 승격, 소속·길이·회계 | K=1에서는 세 위치가 같은 노드임 |
| TREE→LIST SACK | 통과. 초기 TREE 첫 노드와 LIST 다음 노드 확인, 경계·gap 검색, 2회 split | 더 복잡한 다중 SACK 블록 조합 |
| 두 번째 split 실패 | 통과. 첫 split 유지, 닫힌 token, 실패 횟수·재시도 결과 | allocator 내부 실패 |
| split 후 partial shift/full merge | shift/merge 미실행. 이번 fixture는 선형 skb로 split 경로를 강제 | C와 page-fragment skb, 반환 cursor, 현재·다음 동시 제거 시험 |
| highest_sack·힌트 | 선정 범위 통과. 중간 조각 다음과 끝 NULL 검사, lost/retransmit 포인터 생존 확인 | C의 lost_cnt_hint·retransmit cursor 의미 및 변이 조합 |
| 경계·gap·wrap·FIN·비정렬 | 통과. 커널 skb fixture에서 full-MSS 반올림, FIN/PSH 이동, payload 보존 | wire wrap과 FIN·재전송 조합 확대 |
| SACK 이후 누적 ACK/purge | SACK 후 시간 큐 이탈·purge 통과. 누적 ACK fixture 미실행 | C의 누적 ACK·partial ACK cleanup fixture |
| DSACK·재정렬·재전송 | 일반 SACK 반복·기존 SACK/TLP/RTO 통과. DSACK 조합 미실행 | 실제 DSACK·reordering·retrans 조합; 반복 SACK을 DSACK으로 부르지 않음 |

B의 다음 작업은 C의 shift/ACK fixture에 같은 회계 검사기를 적용하고 실패 시 sender 재진입까지 공동 확인하는 것이다. A에는 구조 변경 요구가 없으며 회귀에서 불변식 위반이 나올 때 최소 재현을 전달한다. #16에서는 D와 기준선·계측 비용·K·승격 비용을 비교한 뒤 tail 직접 연결 여부를 판단한다. IPv6 runtime, 실제 NIC offload, 다중 CPU·타이머 경합, ARM64·팀 VM 실행은 이번 x86_64 QEMU 결과로 대체하지 않는다.
