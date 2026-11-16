# B·C 리뷰 대조와 후속 검증

작성일: 2026-11-16 KST

## 결론

**C의 시나리오 수정 요구와 ARM64 필드 위치 확인은 현재 로컬 브랜치에 반영되어 있다. 남은 작업은 V0·V1 실제 부팅 후 trim·DSACK 내부 경로를 관측하고, C 계측 통합본을 검증하는 것이다.**

**B의 공유 packet peer QEMU 회귀 보고는 `65f68c267`까지의 변경을 검토했다. 이후 C 리뷰를 반영한 `03eea6691`의 shared helper 변경까지 실행 검증한 결과로 확대하지 않는다.**

이번 대조에서는 사용자에게 전달받은 B 보고서와 C 리뷰, 현재 소스·Git 차이·기존 실행 JSON을 확인했다. 새 커널 빌드, QEMU 부팅, namespace 패킷 시험, 자동 테스트 재실행은 하지 않았다.

## 1. 리뷰 대상과 현재 코드

| 구분 | 기준 |
| --- | --- |
| 현재 브랜치 | `test/d-arm64-v1-validation` |
| 검토 기준 코드 HEAD | `9cdfeee5b5fcad8ce448c5624b95486035e94d00` |
| B의 검토 대상 | `65f68c267d19180203c9e2169408be4b838c5f34` |
| B가 검토한 native 분기 도입 | `997a12637` |
| C 시나리오 리뷰 반영 | `03eea6691` |
| ARM64 trim 필드 위치 검사 추가 | `211e2a396` |
| C 리뷰 근거·관측 계약 문서화 | `9cdfeee5b` |

11월 16일 원격 브랜치를 조회하여 `9cdfeee5b5fcad8ce448c5624b95486035e94d00`까지 push된 것을 확인했다. 아래의 ‘반영 완료’는 해당 코드와 저장된 실행 근거를 뜻한다. PR #17 머지 여부는 이번에 조회하지 않았다.

## 2. C 리뷰 항목별 대조

| 요청 | 현재 반영·근거 | 판정 |
| --- | --- | --- |
| SACK에서 lost/retrans 출력 | peer가 `tcpi_lost`, `tcpi_retrans`를 출력. 기존 실행에서 `lost=1 retrans=1` 기록 | 반영 완료 |
| RENEGING에서 먼저 남은 offset<1000 패킷 처리 | 범위·payload를 검사해 로그에 남긴 뒤 건너뜀. 이후 offset=1000과 `unacked=3`, `sacked=0` 검사 | 반영 및 기존 커널 실행 완료 |
| `TCPSACKReneging` 증가량 1 | 판정기가 정확히 +1을 요구. 저장된 실행 결과도 +1 | 반영 및 기존 커널 실행 완료 |
| PARTIAL에서 TLP 끄기 | 시나리오 3에서 `tcp_early_retrans=0` 적용, 출력과 판정기에서 함께 검사 | 반영 및 기존 커널 실행 완료 |
| outstanding 데이터가 있는 DSACK 두 블록 | `DSACK_OFO` 추가. ACK=base, `[base+1000,base+2000)`, `[base+1000,base+3000)` 전송 | 반영 및 기존 커널 패킷 동작 확인 |
| `TCPDSACKOfoRecv` 확인 | 정확히 +1, `unacked=4`, `sacked=2` 검사 | 패킷·MIB 검증 완료, 내부 순회는 NOT RUN |
| ARM64 skb 필드 위치 확인 | V0·V1의 실제 설정으로 컴파일하여 cb=40, 내부 위치=0/4/8, 필드 크기=4/4/2 확인 | 두 빌드 모두 PASS |
| trim entry/return·호출자·잔여 상태 | [관측 계약](arm64-c-review.md)에 판정 기준을 기록. probe 등록·런타임 실행은 안 함 | NOT RUN |
| ACK/SACK/shift 카운터 증가 코드 | 현재 소스에는 해당 카운터의 선언·출력이 있고, `tcp_input.c`의 증가 지점은 없음 | C 후속 구현·통합 대상 |

시나리오 구현은 [packet_peer.c](../tests/runtime/packet_peer.c), 패킷·MIB 판정은 [run_arm64_packets.py](../scripts/run_arm64_packets.py)에 있다.

기존 native 실행 `.work/d-packets-y9ch71ud`는 **`5.4.214-kimseungbeom+`에서의 5개 시나리오 PASS**다. V0·V1 부팅 시험이 아니다. 이번에 확인한 현재 peer·native entry의 SHA-256도 이 실행에 기록된 소스 hash와 일치했다.

## 3. 내부 관측에서 유지할 구분

- `unacked=2`만으로 skb 개수나 단일 GSO skb trim을 증명하지 않는다.
- DSACK_OFO 입력은 `packets_out=0` 조기 종료를 피하도록 구성했다. `TCPDSACKOfoRecv`는 내부 순회 이전 감지 지점의 카운터이므로, 증가량만으로 `tcp_sacktag_walk` 순회 완료로 표시하지 않는다.
- trim은 같은 PARTIAL 연결·ACK에서 entry 정확히 1회, `seq=base`, `end_seq=(base+4000) mod 2^32`, `segs=4`, `len=2000`, `ret=0`, `tcp_ack` 호출 경로를 확인한다.
- probe 활성·필터·구간·trace 손실 없음이 확인된 상태에서 진입이 없으면 `NOT_ENTERED`로 구분한다. 관측 자체가 불완전하면 `INCONCLUSIVE`로 남기며 단순히 trim 미진입으로 해석하지 않는다.
- 잔여 상태는 **같은 부분 ACK**의 `ack_trim=1`, `ack_unlink=0`, `tcpi_unacked=2`를 함께 본다. 마지막 전체 ACK까지 포함한 시나리오 전체 카운터를 사용하지 않는다.
- shift의 실제 MIB 이름은 `TCPSackShifted`, `TCPSackMerged`, `TCPSackShiftFallback`이다. 저장된 실행의 증가량 1/0/6은 해당 실행 값이며 V0·V1의 고정 기대값이 아니다.
- `/proc/rtx_stats`는 VM 전체 CPU의 전역 값이고 MIB는 network namespace 범위다. 두 값의 정확한 일치는 같은 이벤트·연결 범위를 확보한 뒤 검사한다. namespace 분리만으로 다른 연결의 전역 카운터 증가가 배제되지는 않는다.

## 4. B의 QEMU 회귀 검토 결과와 적용 범위

사용자가 공유한 「B 담당: 공유 packet peer native 분기 QEMU 회귀 검토」(2026-11-15)에 따르면 다음 결과를 얻었다. 원본 serial 로그·checksum 묶음은 이번 첨부에 포함되지 않아 아래 결과는 **B의 보고 내용**으로 기록한다.

| 실행 | 자체 테스트 | 기본 TCP | SACK/TLP/RTO |
| --- | --- | --- | --- |
| RB, peer 변경 전·후 | 각 495 passed / 0 failed | 각 4/4 PASS | 모두 PASS |
| HYBRID K=16, peer 변경 전·후 | 각 722 passed / 0 failed | 각 4/4 PASS | 모두 PASS |

B는 고정된 두 커널 이미지를 재사용하고 변경 전·후 peer를 새로 컴파일해 비교했다. 네 부팅에서 RTX queue=1, SACK 1000바이트 분할 2회, TLP 3000바이트 분할 1회, RTO 1000바이트 분할 1회 및 성공 반환을 보고했다. 검토 범위에서 guest 코드 수정 제안은 없었다.

B의 작업 체크아웃은 `a,b/list-impl`의 `aa4abd963eed6229602d3b004e8f1500c3998cea`다. 이 HYBRID 실행 보고는 별도 팀 브랜치의 진척이며, 현재 D 브랜치의 RB 기반 고정 V1이나 ARM64 실행 검증 결과와 구분한다.

### 현재 최신 peer까지의 검증 공백

B의 검토 대상 `65f68c267` 이후 `03eea6691`이 공유 `reply()`의 SACK 옵션 길이·두 블록 인코딩과 공통 로그 출력을 수정했다. native 쪽에는 DSACK_OFO, PARTIAL TLP 비활성화, RENEGING 보완이 추가됐다.

| peer Git blob | 값 |
| --- | --- |
| B 검토 대상의 peer | `1f82fa9cf137c1877d7696318a34482ca5fdb6de` |
| 현재 HEAD의 peer | `54fde1ddadac250dbd273f858825cfae718c3dbe` |

따라서 B의 상세 보고로 **native 분기 도입 당시 guest 호환성**은 확인할 수 있지만, **C 리뷰 반영 후 최신 peer의 QEMU 실행 회귀**까지 완료했다고 기록하지 않는다. 현재 guest 판정기는 추가된 lost/retrans 출력에 의존하지 않지만, 정적 검토나 컴파일 성공만으로 실행 회귀를 대체할 수는 없다.

이후 공유된 PR 화면에는 `Lidohyeon`의 승인과 “B 도메인 회귀 테스트 결과 이상 없음” 댓글이 표시되어 있다. 리뷰 승인은 확인했으며, 위 상세 보고와 별도로 최신 peer를 실행한 것인지는 실행 SHA·로그가 추가로 확인되어야 한다.

## 5. 다음 작업

1. B의 승인에 대응하는 guest 회귀 실행 SHA가 `03eea6691`을 포함하는지 확인한다. 포함한 실행이면 해당 근거를 연결하고, 아니라면 같은 고정 RB·HYBRID 이미지에서 최신 peer·initramfs로 추가 회귀를 진행한다.
2. D는 준비된 ARM64 V0·V1을 설치·부팅한 뒤 같은 통신·패킷 시나리오를 실행한다.
3. 해당 빌드용 probe 정의로 PARTIAL trim entry/return·ACK 호출 경로를 관측한다.
4. C의 ACK/SACK/shift 증가 코드가 통합되면 새 커널 SHA를 고정하고 잔여 skb·DSACK 순회·shift 계측을 검증한다. C가 밝힌 shift 작업의 #17 의존성을 확인한다.
5. 추적이 없는 별도 실행에서 성능을 비교한다.
