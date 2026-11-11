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

2026-11-11 최종 실행 `hybrid-aQKg0YD5`에서 아래 기본 검증을 완료했다. 전체 TCP 회귀나 성능 개선을 주장하지 않는다.

## 작업 규칙

이번 PR에서 루트 AGENTS.md를 추가했다. 최소 변경, 도메인 계약, PR별 문서, 실행 증거와 Docker 자원 정리를 저장소 전체에 적용한다. 이 문서가 이번 PR의 대표 작업 문서다.

## 호출부 감사와 호환성

| 경로 | 처리와 이유 |
| --- | --- |
| 신규 송신·TLP·RTO·RTX split | 기존 rtx_* 연결 재사용. split token의 실제 domain에 따라 연결하고 제한 복구 |
| ACK purge·일반 삭제 | rtx_next를 삭제 전에 확보하고 소속에 맞게 제거. 끝 노드 삭제 시 캐시 NULL |
| child 생성 | tcp_create_openreq_child에서 rtx_init. LIST sentinel을 부모 주소로 공유하지 않음 |
| partial ACK trim | 시작 seq만 증가하고 다음 노드 전까지이므로 구조 위치·캐시 포인터 유지 |
| 송신 collapse | 제거 대상 skb의 시작 seq는 유지. 확장된 앞 노드의 end만으로 소속을 판별하지 않으므로 기존 unlink 사용 가능 |
| C full/partial shift | rtx_shift_token에 domain과 next를 seq 수정 전에 보존. full shift의 빈 TREE 노드가 LIST 첫 seq와 같아져도 잘못된 링크를 읽지 않음 |
| SYN skb 교체 | rtx_replace로 같은 seq 범위·소속·위치를 유지. old skb의 tsorted·수명 정리는 기존 호출부 책임 |
| SACK highest_sack | full shift에서는 token의 사전 next를 사용. transaction 중 일반 rtx_next 호출 제거 |

shift transaction은 skb_shift의 payload 이동 이후이지만 **시퀀스 변경 이전**에 시작한다. 뒤 구간에는 실패 반환이나 공개 RTX 구조 API가 없고 partial/full 경로가 각각 commit한다. 전체 실패 rollback을 제공하지 않는다. replacement는 queue N과 LIST 길이를 바꾸지 않으며 stats의 새 삽입/삭제 이벤트로 중복 계산하지 않는다.

## 재현 방법

```sh
RTX_BUILD_IMAGE=ubuntu:22.04 rtx-project/scripts/run_hybrid_validation.sh
python3 -B -m unittest discover -s rtx-project/tests/integration -p 'test_*.py' -v
python3 -B -m unittest discover -s rtx-project/tests/runtime -p 'test_*.py' -v
```

runner는 HEAD archive에 추적 파일의 작업 diff를 적용하고 둘 다 보존한다. 커밋 전 실행의 코드 정체성은 HEAD SHA만이 아니라 `source.patch`와 함께 확인해야 한다. 별도로 복사한 harness와 runtime 시험 소스도 보존한다. Linux 파일시스템 내부에서 RB/하이브리드 × DEBUG·STATS off/on 네 구성을 빌드한다. DEBUG와 STATS를 각각 독립적으로 교차한 8개 구성 전수 시험은 아니다.

하이브리드 DEBUG on K=16의 원본 커널을 부팅해 기본 통신과 SACK/TLP/RTO를 검사하고, 이후 disposable overlay를 적용한 커널에서 fixture와 같은 패킷 시험을 실행한다. 마지막으로 K=1로 재빌드해 경계를 반복한다. 기존 fixture의 회계 검사기는 최대 32 skb이므로 새 통합 fixture는 K<=24에서만 실행하며, 이 제한은 production K의 지원 범위(1~1024)와 다르다.

## 첫 실행에서 발견한 경고

초기 실행 `hybrid-Np42Mkcb`는 네 빌드와 두 번의 부팅·패킷 시험 및 fixture를 통과했지만 LIST 첫 노드 조회에서 새 const 제거 경고가 발생했다. Linux 5.4의 `list_first_entry_or_null` 매크로가 내부에 mutable head 포인터를 선언하기 때문이었다. const를 강제로 제거하지 않고 `list_empty`와 명시적인 `list_entry` 조회로 변경했다. 초기 로그를 보존하고 수정본 전체를 다시 실행했다. 최종 결과는 두 번째 실행을 기준으로 한다.

## 한계와 후속 작업

- #15: 실제 C shift 경로의 폭넓은 패킷 조합, DSACK, 연속 분할의 두 번째 실패 등. 이번 구조 selftest의 shift 재현을 전체 C 패킷 경로 검증으로 표현하지 않는다.
- #16: K 선정·tail 직접 연결·성능/메모리 비용 측정. 승격은 ordered insert이므로 O(log N)이며 성능 향상을 주장하지 않는다.
- 구조 validator는 캐시·순서·범위·LIST 연결·길이를 검사한다. 임의 메모리 손상에 안전한 탐색기, RB 균형의 독립적인 형식 증명 또는 전체 TCP validator가 아니다.
- IPv6는 빌드만 검사한다. 실제 IPv6 통신, NIC offload, 다중 CPU·경합, ARM64 guest, 실제 allocator 고갈은 미검증이다.
- 일반 하이브리드 부팅과 계측 fixture 결과를 분리한다. kprobe가 켜진 시험 환경은 성능 측정 환경이 아니다.
- skb별 새 필드는 없고 HYBRID 설정일 때 tcp_sock에 LIST head·캐시 2개·길이를 추가한다. 64비트 명목 필드 합계 36바이트이며 정렬/slab을 포함한 연결당 실제 증가량은 별도 측정 대상이다.


## 최종 실행 결과

환경: 기존 Ubuntu 22.04 arm64 이미지 `962f6cadeae0`, x86_64 cross GCC 11.4.0, QEMU 6.2.0 TCG, guest x86_64 1 CPU / 512 MiB. 소스 기준은 `231db9ed6` 위 문서 커밋 `f11c33716` + 보존된 `source.patch`다. archive와 patch에서 소스를 복원하여 최종 커밋 대상 파일과 바이트 단위 일치를 확인했다. 실행한 harness·runner 사본도 최종 파일과 일치한다.

| 구성 | 검증 | 결과 |
| --- | --- | --- |
| RB, DEBUG/STATS off | IPv4·IPv6 빌드 | 통과 |
| RB, DEBUG/STATS on | IPv4·IPv6 빌드 | 통과 |
| HYBRID K=16, DEBUG/STATS off | IPv4·IPv6 빌드 | 통과 |
| HYBRID K=16, DEBUG/STATS on | IPv4·IPv6 및 전체 커널 빌드 | 통과 |
| K=16, fixture overlay 없는 커널 | Core selftest + TCP smoke + 패킷 | 722/0 + 4 사례 + SACK/TLP/RTO 통과 |
| K=16, fixture overlay 커널 | Core selftest + B assertion + smoke·패킷 | 722/0 + 396/0, 나머지 통과 |
| K=1, fixture overlay 재빌드 | Core selftest + B assertion + smoke·패킷 | 647/0 + 351/0, 나머지 통과 |
| 판정기 unittest | integration 2개 + packet trace 7개 | 9개 통과 |
| 정적 검사 | kernel patch checkpatch, 새 fixture checkpatch, diff whitespace, shell syntax | 통과. kernel patch 오류·경고·check 각 0 |

세 부팅 모두 SACK 2회, TLP 1회, RTO 1회의 RTX 분할과 성공 반환을 추적으로 확인했다. K=16과 K=1의 실제 tcp_fragment fixture에서 각각 LIST head/middle/tail 선택을 실행하고 승격·pcount·wmem·payload·tsorted를 검사했다. K=1에서는 head/middle/tail 선택이 동일 노드라는 점을 고려해야 한다.

Core selftest는 K까지의 append, K+1 승격, 자기 승격, wrap, 구멍 검색·ordered insert, TREE/LIST replacement, 경계에 도달한 full shift token, partial trim, 복사본 재초기화·부모 보존, purge 후 빈 상태를 검사했다. 길이와 캐시를 고의로 어긋나게 해 구조 validator가 거부하는 음성 대조도 포함한다. 실제 TCP smoke의 accept 경로는 child 생성 구현을 실행한다. SYN clone 교체의 구조 API는 selftest로 확인했지만 simultaneous-open 패킷 경로 전체는 별도 미검증이다.

커널 WARN/BUG/Oops/panic은 0건이다. 네 부분 빌드는 동일한 기존 OpenSSL deprecated 경고 7건만 남았으며 새 경고는 없다. 전체 커널 최초 링크에는 기존 compressed kernel의 relocation/DT_TEXTREL 경고 2건이 추가된다. 경고를 숨기는 컴파일 옵션은 추가하지 않았다.

### 보고서용 증거

모든 링크는 **로컬 보존 산출물**이며 GitHub에 바이너리·로그·이미지를 업로드한 것이 아니다. GitHub에서 아래 로컬 링크는 열리지 않으므로 팀 보고서 공유 시 별도로 첨부한다.

- [빌드·실행 요약 화면](../../.work/hybrid-aQKg0YD5/report/index.png)
- [K=16/K=1 LIST split·승격 실행 화면](../../.work/hybrid-aQKg0YD5/report/fixtures.png)
- [원본 커널 패킷 회귀 화면](../../.work/hybrid-aQKg0YD5/report/packets.png)
- [첫 const 경고와 수정 근거 화면](../../.work/hybrid-aQKg0YD5/report/initial-warning.png)
- [원본 커널 serial](../../.work/hybrid-aQKg0YD5/serial-packets.log), [K=16 fixture serial](../../.work/hybrid-aQKg0YD5/serial.log), [K=1 serial](../../.work/hybrid-aQKg0YD5/serial-k1.log)
- [실행 환경](../../.work/hybrid-aQKg0YD5/environment.txt), [빌드 경고 비교](../../.work/hybrid-aQKg0YD5/warning-comparison.json), [실행 소스 일치 확인](../../.work/hybrid-aQKg0YD5/verified-source-hashes.json)
- [바이너리·입력·로그 체크섬](../../.work/hybrid-aQKg0YD5/SHA256SUMS), [추가 증거 체크섬](../../.work/hybrid-aQKg0YD5/EVIDENCE_SHA256SUMS)

스크린샷은 검증된 실제 로그를 행 번호·SHA-256과 함께 보여주는 로컬 HTML 페이지의 Playwright 캡처다. QEMU 콘솔 자체의 화면 촬영이라고 표현하지 않는다.

| 커널 산출물 | SHA-256 |
| --- | --- |
| K=16 원본 | `b8ec37947bad6860370650d27a371429caa49141f582d4fa242f585fb85b5ec1` |
| K=16 fixture | `5e97efdc4c5e6a698aced3d64d0a3795b4a1c767de4066bcb3985969a3b0e411` |
| K=1 fixture | `f03d4c3238ca1c2a0cb09d47e577ca24fc07943e4932070e1b1bf04820027319` |

두 실행 모두 task 컨테이너 제거와 빈 `remaining-containers.txt`를 확인했다. 기존 컨테이너·이미지·볼륨은 보존했고 스크린샷용 HTTP 서버도 종료했다.
