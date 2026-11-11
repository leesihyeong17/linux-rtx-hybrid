# B write queue와 손실 표시 호출 경로 검증

검증일: 2026-11-11. 공통 환경과 재현 방법은 [실패 경계 검증](B_SPLIT_FAILURE_VALIDATION.md)을 따른다.

## 정확한 경로 분류

`tcp_recovery.c`에서 직접 `tcp_fragment()`를 호출하는 함수는 `tcp_newreno_mark_lost()`다. RACK의 `tcp_rack_mark_lost()`는 분할 호출자가 아니라 시간 큐를 소비하는 손실 판정 경로다. 기존 문서의 “RACK 분할 경로”라는 표현을 이 둘로 분리한다.

`case_callers.c`는 다음 네 경로를 실행한다.

1. 선형 skb를 가진 write queue에서 `tcp_fragment(TCP_FRAG_IN_WRITE_QUEUE)`.
2. 실제 page fragment만 가진 skb에서 `tso_fragment()`의 빠른 분할 경로.
3. Reno 상태에서 `tcp_mark_head_lost()`의 부분 손실 표시 분할.
4. Recovery 상태에서 ACK 전진 조건을 준 `tcp_newreno_mark_lost()` 분할.

write queue에서는 prepare/commit이 0회이고 RTX 큐·tsorted·packets_out이 비어 있어야 한다. write queue 두 노드, 메모리 합계, FIN 이동과 payload를 확인한다. 손실 경로에서는 4,000바이트 GSO skb의 앞 1,000바이트가 분리되어 lost_out=1, packets_out=4가 되고 prepare/commit이 각각 한 번 실행되어야 한다.

## 접근 방법의 트레이드오프

| 접근 | 얻는 것 | 비용·한계 | 판단 |
| --- | --- | --- | --- |
| production 함수를 export하거나 시험 API 추가 | 외부 테스트 모듈에서 접근 쉬움 | 공개 API와 production 표면을 시험 때문에 늘림 | 채택하지 않음 |
| 폐기 가능한 소스 사본에 wrapper/include 추가 | static 함수의 실제 본문을 호출; repository production 파일은 유지 | 계측 커널 결과이므로 배포 커널 동일 실행으로 주장할 수 없음 | 선택 |
| 패킷만으로 모든 분기 유도 | 실제 연결 상태와 타이머까지 검증 | 손실 알고리즘·GSO·시점 조합에 따라 원하는 분기 도달이 불확실 | 기존 SACK/TLP/RTO 시험으로 보완, 이 시험과 구분 |

`tcp_mark_head_lost`에만 disposable wrapper를 추가한다. `tso_fragment` 시험은 같은 번역 단위 뒤에 붙여 static 접근을 해결한다. 선형 skb로 tso_fragment를 부르면 일반 tcp_fragment로 우회할 수 있으므로 실제 page fragment를 구성했다. 이 선택이 write queue fast path 자체를 검증하는 데 중요하다.

## 실행 결과

실행 `b-integration-kiJKsMWl`: **49 assertion 통과, 0 실패**. 공통 실행 전체는 285 assertion 통과, adapter 자체 테스트 495 통과, 기본 TCP smoke 4 사례 통과다. assertion 수에는 fixture 생성·정리 검사도 포함되며 서로 독립적인 네트워크 시나리오 수가 아니다.

- [원본 serial 로그](../.work/b-integration-kiJKsMWl/serial.log), [구조화 결과](../.work/b-integration-kiJKsMWl/summary.json)
- [결과 스크린샷](../.work/b-integration-kiJKsMWl/report/callers.png), [브라우저 보고서](../.work/b-integration-kiJKsMWl/report/callers.html)
- [빌드 로그](../.work/b-integration-kiJKsMWl/build.log), [소스·계측·바이너리 체크섬](../.work/b-integration-kiJKsMWl/SHA256SUMS)

스크린샷은 실제 실행 로그를 표시한 로컬 보고서 페이지를 Playwright로 캡처했다. 원본 로그의 행 번호와 hash를 함께 보존한다. `.work` 산출물은 Git에 포함하지 않으므로 팀 보고서 공유 시 별도로 첨부해야 한다.
