# #14 LIST·승격 backend와 B 호출 경로 통합

이슈: https://github.com/leesihyeong17/linux-rtx-hybrid/issues/14
PR: 생성 후 연결한다.
기준: `231db9ed6` (B #13과 A stats 포함), 브랜치 `a,b/list-impl`.

## 목표와 범위

기존 `rtx_*` 호출부를 유지하면서 TREE + 제한 길이 LIST suffix를 구현한다. 기본 송신·분할·재전송까지 실제로 실행하고, 광범위한 공동 회귀 #15와 성능 판단 #16은 분리한다. 완료 여부는 아래 실행 기록으로 판정한다.

## 구현 선택과 트레이드오프

| 선택지 | 선택과 이유 | 비용·포기한 이점 |
| --- | --- | --- |
| 하이브리드를 항상 사용 / 빌드 옵션 | `TCP_RTX_HYBRID` 기본 n. RB 기준선 유지와 동일 코드 비교를 위해 선택 | 두 구성의 빌드·시험 유지 필요 |
| 동적 K / 빌드 시 K | `TCP_RTX_RECENT_MAX`, 1~1024, 초기값 16 | 실행 중 튜닝 불가. 16은 최적값이 아니며 #16에서 판단 |
| per-skb 태그 / 경계 seq 판별 | 기존 설계대로 안정된 상태에서 LIST 첫 seq 기준 판별 | 변이 중 판별 금지. 소속을 token에 저장해야 함 |
| 승격 tail 직접 연결 / 기존 ordered insert | 첫 통합은 기존 ordered insert 및 RB balancing 사용 | tail 탐색 생략은 후속. 검증 범위와 새 저수준 RB 코드를 줄임 |
| SYN clone 삭제 후 재삽입 / 같은 자리 replace | `rtx_replace`로 소속·위치·캐시 유지 | 작은 API 추가. 송신 호출부의 구조 의존성 제거 |
| SACK full shift 후 소속 재판별 / 사전 token | seq 변경 전 domain·next 저장 후 commit | 기존 C 경로 최소 수정 필요. TREE 끝이 LIST 경계 seq가 되는 오판 방지 |

Core 구조·연결·캐시를 A 영역에서 처리하고 TCP 회계·힌트·시간 큐·수명은 기존 TCP 호출부에 남긴다. 승격은 동일 skb의 구조 이동이며 TCP 카운터를 바꾸지 않는다. child 생성 시 복사된 LIST sentinel을 재초기화한다. partial trim은 이웃 범위를 넘지 않아 소속이 유지되고, 송신 collapse는 제거 대상 seq를 바꾸지 않으므로 기존 unlink 계약을 사용할 수 있다.

## 검증 계획과 현재 상태

구현 중. DEBUG off/on 및 RB/하이브리드 빌드, 실제 부팅·fixture·SACK/TLP/RTO 실행, raw log와 화면 캡처를 완료한 뒤 결과를 기록한다. 현재 이 문서는 성공을 주장하지 않는다.

## 작업 규칙

이번 PR에서 루트 AGENTS.md를 추가했다. 최소 변경, 도메인 계약, PR별 문서, 실행 증거와 Docker 자원 정리를 저장소 전체에 적용한다. 이 문서가 이번 PR의 대표 작업 문서다.
