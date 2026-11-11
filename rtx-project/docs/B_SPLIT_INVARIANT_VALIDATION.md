# B split 후 회계·힌트·시간 큐와 RACK 검증

검증일: 2026-11-11. 공통 환경과 재현 방법은 [실패 경계 검증](B_SPLIT_FAILURE_VALIDATION.md)을 따른다.

## 검사 대상과 판단 기준

`common.h`의 fixture 전용 검사기는 RTX 앞뒤 연결·순서, 노드별 pcount의 packets/sacked/lost/retrans 합계, truesize의 queued memory 합계, transaction 종료, 세 힌트의 살아 있는 노드 소속, tsorted 이중 연결과 RTX 소속을 확인한다. 최대 32노드로 순회를 제한한다. 임의 메모리 손상에 안전한 범용 validator가 아니라, 이 시험에서 만든 소규모 상태의 검사기다.

`case_invariants.c`는 SACKED_ACKED와 SACKED_RETRANS가 설정된 skb를 1,500/2,500으로 나눠 packets_out/sacked_out/retrans_out 모두 4→5인지 검사한다. 세 힌트의 원래 노드 참조와 새 조각의 tsorted 인접 연결을 직접 확인한다. 이 flags 조합은 회계 분기 시험을 위한 상태이며 실제 ACK 이력의 재현을 주장하지 않는다.

검사기가 무조건 성공하는 것을 막기 위해 세 음성 대조군을 둔다. packets_out 불일치, 큐 밖 hint, 새 조각의 tsorted 누락을 각각 만들어 검사기가 거부하는지 확인하고 상태를 복원한다.

별도 소켓에서는 실제 분할 후 RACK timestamp를 설정하고 `tcp_rack_mark_lost()`를 호출한다. 두 조각의 합계 5 packets가 lost로 바뀌고 tsorted에서 제거되는지, 불필요한 추가 split이 없는지 확인한다. LOST이면서 재전송 중이 아닌 skb가 tsorted에 없는 상태는 정상 RACK 의미이므로 검사기가 이를 허용한다.

## 비교한 선택지

| 방식 | 강점 | 약점 | 결정 |
| --- | --- | --- | --- |
| WARN 부재·TCP_INFO만 확인 | 외부 관측에 가까움; 계측이 적음 | stale hint나 누락된 시간 큐 연결을 직접 판별하지 못함 | 기존 패킷 시험의 근거로 유지 |
| 시험 소켓의 직접 snapshot assertion | 필요한 포인터·회계 관계를 정확하게 확인; 음성 대조 가능 | fixture 범위 밖 상태·동시성까지 증명하지 않음 | 이번 누락 항목에 선택 |
| 모든 소켓에 production validator 추가 | 넓은 트래픽에서 이상 상태 탐지 가능 | hot path 비용과 A/C 계약 확장 필요; 범용 합법 상태 정의가 어려움 | 이번 B 검증 범위에서 제외 |

RACK를 분할 호출자로 취급하지 않고 **분할 결과를 소비하는 사용자**로 검사하는 편이 코드 책임과 맞는다. 다만 실제 ACK 타이밍, 재정렬, 다중 CPU, 타이머 경합은 이 fixture가 다루지 않는다. 기존 실제 SACK/TLP/RTO 패킷 시험과 함께 읽어야 하며 두 근거를 합쳐도 전체 TCP 정확성 증명은 아니다.

## 실행 결과

실행 `b-integration-kiJKsMWl`: **26 assertion 통과, 0 실패**. 공통 실행 전체는 285 assertion 통과, adapter 자체 테스트 495 통과, 기본 TCP smoke 4 사례 통과다. assertion 수에는 fixture 생성·정리 검사도 포함되며 서로 독립적인 네트워크 시나리오 수가 아니다.

- [원본 serial 로그](../.work/b-integration-kiJKsMWl/serial.log), [구조화 결과](../.work/b-integration-kiJKsMWl/summary.json)
- [결과 스크린샷](../.work/b-integration-kiJKsMWl/report/invariants.png), [브라우저 보고서](../.work/b-integration-kiJKsMWl/report/invariants.html)
- [빌드 로그](../.work/b-integration-kiJKsMWl/build.log), [소스·계측·바이너리 체크섬](../.work/b-integration-kiJKsMWl/SHA256SUMS)

스크린샷은 실제 실행 로그를 표시한 로컬 보고서 페이지를 Playwright로 캡처했다. 원본 로그의 행 번호와 hash를 함께 보존한다. `.work` 산출물은 Git에 포함하지 않으므로 팀 보고서 공유 시 별도로 첨부해야 한다.
