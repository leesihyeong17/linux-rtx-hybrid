# B split 위치·FIN·GSO·시퀀스 wrap 검증

검증일: 2026-11-11. 공통 환경과 재현 방법은 [실패 경계 검증](B_SPLIT_FAILURE_VALIDATION.md)을 따른다.

## 질문과 시험 설계

`case_boundaries.c`는 실제 RTX 큐에 연속한 4,000바이트 skb 세 개를 넣고 head/middle/tail 각각을 1,500 + 2,500바이트로 분할한다. 시작 시퀀스 1,000, `0xffffff00`, `0xfffff060`의 3종을 교차해 9개 사례를 실행한다. 뒤의 두 값은 각각 데이터가 wrap을 가로지르는 경우와 첫 노드의 끝이 정확히 0이 되는 경우다. 큐 전체 범위는 TCP serial 비교의 반공간보다 작다.

확인 항목은 prepare/commit 각 1회, 인접 시퀀스와 원래 끝 보존, FIN/PSH의 뒤 조각 이동, MSS 1,000 기준 GSO pcount 2+3, 전체 packets_out 12→13, 모든 데이터와 연결·회계 불변식이다. FIN은 tail skb에만 두어 TCP 종료 위치를 유지한다. 단일 skb의 wrap 분할 후 새 tail을 다시 나누는 별도 사례에서 총 prepare/commit 2회와 마지막 FIN을 확인한다.

## 대안 비교와 결정

| 대안 | 장점 | 한계 | 선택 이유 |
| --- | --- | --- | --- |
| 실제 네트워크에서 ISN·MTU·ACK를 조절 | 송수신 전체 경로를 포함 | 정확한 위치·wrap·FIN/GSO 조합을 반복 재현하는 제어 비용이 큼 | 기존 SACK/TLP/RTO 패킷 시험과 후속 wire 시험에 사용 |
| 실제 커널 함수에 제어된 skb fixture 입력 | 동일 커널의 skb_split, pcount, split API 실행; 경계 값 재현 가능 | 연결 상태를 구성하므로 실제 ACK 수신·NIC offload까지 입증하지 않음 | 이번 경계 회귀에 선택 |
| 사용자 공간 큐 모델 | 빠른 대량 조합 생성 | 실제 skb 공유·메모리 회계와 flags 코드가 빠짐 | 기존 모델 시험의 보완 근거로만 사용 |

분할 길이는 MSS 배수로만 고르지 않았다. 1,500은 pcount 합계가 4→5로 증가하므로 회계 조정을 실제로 요구한다. 반대로 재분할은 1,000 경계를 사용해 합계 4를 유지하는 경우를 함께 둔다. 9개 사례와 1개 재분할 사례는 선정 조합이며 가능한 모든 시퀀스·길이의 전수 증명은 아니다. 네트워크상의 FIN 수신과 32비트 wrap 송수신은 별도 범위다.

## 실행 결과

실행 `b-integration-kiJKsMWl`: **172 assertion 통과, 0 실패**. 공통 실행 전체는 285 assertion 통과, adapter 자체 테스트 495 통과, 기본 TCP smoke 4 사례 통과다. assertion 수에는 fixture 생성·정리 검사도 포함되며 서로 독립적인 네트워크 시나리오 수가 아니다.

- [원본 serial 로그](../.work/b-integration-kiJKsMWl/serial.log), [구조화 결과](../.work/b-integration-kiJKsMWl/summary.json)
- [결과 스크린샷](../.work/b-integration-kiJKsMWl/report/boundaries.png), [브라우저 보고서](../.work/b-integration-kiJKsMWl/report/boundaries.html)
- [빌드 로그](../.work/b-integration-kiJKsMWl/build.log), [소스·계측·바이너리 체크섬](../.work/b-integration-kiJKsMWl/SHA256SUMS)

스크린샷은 실제 실행 로그를 표시한 로컬 보고서 페이지를 Playwright로 캡처했다. 원본 로그의 행 번호와 hash를 함께 보존한다. `.work` 산출물은 Git에 포함하지 않으므로 팀 보고서 공유 시 별도로 첨부해야 한다.
