# #18 ARM64 V0·V1 설치 및 런타임 검증 결과

작성일: 2026-11-18 KST. 관련 [이슈 #18](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/18).
2026-11-22 UTC 기준 두 고정 커널의 부팅, TCP·패킷 결과, 부분 ACK trim과 DSACK 순회 관측을 완료했다.
작업 브랜치: `test/d-arm64-v0-v1-completion`.
도구·문서 작업 기준: main `9ce15674a906c557ca45bccb051a270b38d647cf`.
이번 후속 PR은 아직 생성하지 않았다.

## 문제와 완료 조건

기존에 전체 빌드를 완료한 V0·V1을 각각 설치·부팅했다. 두 커널 모두 실제 TCP
16개와 결정적 패킷 5개를 통과했다(총 42개). 두 부팅에서 추적 가능 함수 목록을
확인하고, 별도 진단 실행으로 같은 부분 ACK trim과 DSACK 순회를 직접 관측했다.
PR #26의 C23 RB·HYBRID 결과는 다른 production/harness
조합이므로 V0·V1의 런타임 결과로 대신하지 않는다.

이번 단계에서는 기존 빌드의 유효성을 재확인하고 설치 도우미 및 두 커널의 실행 증거를
남긴다. #18의 최종 완료에는 각 대상의 설치·부팅 신원 확인, 실제 TCP와
결정적 패킷 시험, ftrace 사용 가능 여부, GSO 부분 ACK/trim과 잔여 회계의
직접 관측이 필요하다. 패킷 시험의 PASS만으로 내부 trim 검증을 완료하지 않는다.
성능 비교와 #24의 N 분포 수집은 이번 범위에서 제외한다.

| 대상 | 커널 소스 SHA | release | 기존 전체 빌드 결과 디렉터리 |
| --- | --- | --- | --- |
| V0 | `991defcc56cad49cfb221f44f5d161c9ea920c4a` | `5.4.214-rtx-v0` | `.work/build-v0/validation-davvdlkt` |
| V1 | `231db9ed6a204d6407d88954da60ce7db1c2dabd` | `5.4.214-rtx-v1` | `.work/build-v1/validation-gszehhr3` |

`.work/`는 모두 `rtx-project/.work/` 기준이다. 빌드 환경·설정의 원본 기록은
[기존 V0·V1 문서](d-arm64-v1-validation.md), C23 결과는
[PR #26 관련 문서](issue-15-d-arm64-c-regression.md)를 참조한다.

## 최종 변경

- `scripts/install_arm64.py`: 기본 동작은 읽기 전용 사전 검사다. `--install`을
  명시하면 VM 터미널에서 sudo 인증 후 모듈·커널·initramfs·GRUB을 설치·검증한다.
  재부팅은 하지 않는다. 이 VM의 ARM64 UTM, LVM root, virtio_blk 조건을 대상으로 한다.
- `tests/test_install_arm64.py`: 미완성 빌드, 산출물 변경, 잘못된 모듈 버전,
  부분 설치, 필수 initramfs 항목 누락과 설치 명령 실패를 거부하는 시험을 추가한다.
- `scripts/inspect_arm64_trace.py`: 기존 TCP·패킷 도구처럼 sudo 실행 결과의
  새 디렉터리·파일을 호출 사용자에게 돌려준다. 기존에는 root의 0700 디렉터리에
  결과가 남아 일반 사용자로 검토할 수 없었다. 추적 설정·판정은 바꾸지 않는다.
- 이 문서: 실행 기준·증거·후속 절차를 기록한다.
- `scripts/run_arm64_packets.py --observe-acks LAYOUT_DIR`를 지정하면
  `arm64_ack_trace.py`가 부팅 신원·ELF·컴파일된 skb 레이아웃을 대조한 뒤
  전용 tracefs 인스턴스와 실행별 kprobe를 생성한다. 진단 빌드의
  `packet_peer.c`가 자신의 PID로 이벤트를 제한하고 PARTIAL과 DSACK_OFO의
  ACK 전송 직전부터 TCP_INFO 확인 직후까지 포트·시작 시퀀스를 marker로 남긴다.
  `run_arm64_tcp.py`의 프로세스 실행 함수가 해당 인스턴스의 FD를 peer에 전달한다.

`build_arm64.py`의 pin·설정·산출물 판정기를 재사용한다. C23에서 사용한 로컬
설치 절차의 모듈·initramfs 검사를 가져오되, 이번 입력은 V0/V1 완료 빌드로 제한한다.
기본 패킷 시험과 진단 시험은 같은 ACK 입력과 회계 판정기를 사용한다.

검사는 clean source와 정확한 SHA, 빌드 종료 코드·상태, 보존 config 및 주요
산출물 hash, AArch64 ELF, 모듈/DTB 개수, 모든 모듈의 vermagic을 확인한다.
모듈·DTB·builtin 메타데이터의 현재 hash도 추가 기록한다. 과거 빌드 기록에는
개별 모듈·DTB hash가 없으므로 이 추가 목록은 **현재 설치 입력의 식별자**이며,
개별 파일이 과거와 바이트 단위로 같았다는 증거는 아니다.

설치 시 모듈 누락·메타데이터·vermagic, 설치 Image/config/System.map hash,
initramfs의 해당 release용 virtio_blk·LVM·의존성 파일, GRUB 항목을 검사한다.
동일 release의 기존 파일이 하나라도 있으면 덮어쓰기를 거부한다. 도중 실패 시
이미 생성된 설치 파일은 자동 삭제하지 않으며, 로그를 보고 복구 방법을 결정한다.

## 대안과 트레이드오프

전체 재빌드 대신 기존 산출물을 검사해 재사용한다. 소스·config·주요 산출물이
완료 기록과 일치하므로 현 단계에서 재빌드할 근거가 없으며, VM의 시간·저장 공간을
절약한다. 개별 모듈 hash의 과거 비교 한계는 위와 같이 남긴다.
수동 설치 명령을 반복하는 대신 기존 C23 검사 절차를 도우미로 옮겨 명령·종료 코드와
설치 후 검사를 묶는다. 대신 이 도우미는 다른 하드웨어의 범용 설치를 보장하지 않는다.

## 검증 방법과 결과

환경은 기존 ARM64 UTM VM, GCC 9.4.0, Python 3.8.10이다. 사전 검사 시 실행
커널은 `5.4.214-rtx-c23-rb`였다. V0/V1의 RTX DEBUG/STATS는 off이며 V0에는
해당 옵션 자체가 없다. KASAN 빌드나 성능 측정을 이번에 추가 실행하지 않았다.
MANIFEST의 x86/VirtualBox PMU 기록을 이 UTM 실행 결과로 사용하지 않는다.

| 검사 | 실제 결과 | 범위·한계 |
| --- | --- | --- |
| V0 설치 입력 사전 검사 | PASS | 소스·설정·이미지 일치, 모듈 5,968개, DTB 264개 |
| V1 설치 입력 사전 검사 | PASS | 11월 22일 재확인, 모듈 5,968개, DTB 264개 |
| 설치 도우미 단위 시험 | 12개 PASS | 합성 파일과 모의 subprocess 사용, sudo/make 실제 실행 없음 |
| 정적 검사·로그 무결성 | PASS | Python 구문, CLI help, diff 공백 검사, 두 preflight의 SHA256SUMS 대조 |
| V0 설치·initramfs 실물 검사 | PASS | 모듈·Image/config/System.map hash, initramfs 필수 항목, GRUB 등록 확인 |
| V1 설치·initramfs 실물 검사 | PASS | 5,968개 모듈 검증, 이미지·config·System.map·initramfs hash 대조, GRUB 일반 부팅 항목 확인 |
| V0 부팅 신원 | PASS | 실제 release, 설치 Image/config와 고정 빌드의 hash 일치 |
| V0 전체 부팅 로그 검토 | 확인 완료 | BUG/WARNING/Oops/panic/KASAN/lockup/Call trace 진단 없음; 기존 환경 메시지는 아래 별도 기록 |
| V0 실제 TCP | 16개 PASS | 무손실·양쪽 egress 손실 1%, 양방향 전송 12개 길이/hash 및 reset 4개 검증 |
| V0 결정적 패킷 | 5개 PASS | SACK, PARTIAL, DSACK, DSACK_OFO, RENEGING |
| V0 시험 중 커널 오류·cleanup 오류 | 0개 | dmesg 연속성 확인, 생성한 namespace 3개 모두 제거 확인 |
| 추적 목록 도구 회귀 | 4개 PASS | 기존 판정 시험, Python 구문·CLI help 확인 |
| V0 추적 목록·소유권 | 수집 완료 | 함수 4개 AVAILABLE, 4개 UNAVAILABLE; 실제 sudo 결과의 UID/GID 1000 반환 확인 |
| V1 부팅·TCP·패킷 시험 | 부팅 확인, 16+5 PASS | 설치 Image/config 대조. 양방향 무손실·손실 1% 전송 12개와 reset 4개, 패킷 5개. 새 커널 오류·정리 실패 각 0건 |
| V1 ftrace 가용성 | 수집 완료 | V0와 동일한 4개 함수 AVAILABLE, 4개 UNAVAILABLE |
| V1 부분 ACK trim·잔여 skb | 관측 완료 | `tcp_ack`에서 2,000바이트 trim 1회, 반환 0, 같은 skb의 세그먼트 수 4→2, `tcpi_unacked=2`. C 카운터 직접 측정 범위는 아래 참조 |
| V1 DSACK_OFO 내부 순회 | 관측 완료 | 두 블록의 순회 범위와 같은 연결의 중복 플래그 대조 |
| V0 내부 trim·DSACK 순회 | 관측 완료 | 2,000바이트 trim 1회, 반환 0, 같은 skb의 세그먼트 수 4→2. DSACK의 두 순회 범위와 중복 플래그가 V1과 일치 |
| 진단용 peer C 패치 | PASS | 기본·진단 모드 컴파일, checkpatch 오류 0개·경고 0개. 기존 커널 이미지 사용 |

재현 명령:

아래 사전 검사는 설치 전에 실행한 기록이다. V0가 설치된 현재 상태에서 다시
실행하면 동일 release의 기존 파일 보호 검사로 거부되는 것이 정상이다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
python3 -B rtx-project/scripts/install_arm64.py \
  --variant v0 --build-run "$PWD/rtx-project/.work/build-v0/validation-davvdlkt"
python3 -B rtx-project/scripts/install_arm64.py \
  --variant v1 --build-run "$PWD/rtx-project/.work/build-v1/validation-gszehhr3"
python3 -B -m unittest discover -s rtx-project/tests -p test_install_arm64.py -v
```

## 증거와 실패 기록

| 증거 | 경로 | 뒷받침하는 주장 |
| --- | --- | --- |
| V0 preflight | `.work/d-install-v0-rlo6imr2/` | 실제 입력 검사 PASS, 설치·부팅 NOT RUN |
| V1 preflight | `.work/d-install-v1-tua6okqy/` | 실제 입력 검사 PASS, 설치·부팅 NOT RUN |
| 판정기 시험·검토 | `.work/d-issue18-20261118-b9gku30e/` | `unit-tests.log`에 12개 시험과 의도적인 실패 입력 거부 기록 |
| V0 실제 설치 | `.work/d-install-v0-6lj_fjg7/` | `status=PASS`, `stage=installed`, `installation=PASS` |
| V0 설치 결과 재검토 | `.work/d-v0-install-review-7soac5gi/` | 원본 증거와 설치 파일 hash 303건 대조, 모듈 5,968개·메타데이터 일치 |
| V0 부팅 신원 | `.work/d-v0-boot-3ng32brb/` | 실제 uname, /proc/version·cmdline·boot ID, source SHA 및 설치 config/Image 대조 |
| V0 실제 TCP | `.work/d-tcp-7i88bx53/` | 16개 결과, endpoint 원본 JSON, pcap, 명령·커널 로그 |
| V0 결정적 패킷 | `.work/d-packets-rcwqle06/` | peer 원본 로그·소스·바이너리·MIB, 5개 시나리오 |
| V0 실행 재검토 | `.work/d-v0-runtime-review-dsgqu59_/` | 원본 파일 100개 SHA-256, endpoint/MIB/pcap 재판정, 추적 목록 도구 회귀 로그 |
| 다른 커널에서 시도한 V0 추적 목록 | `.work/d-trace-x5n17ejy/` | 실행 커널 C23 RB, 모든 ftrace 항목 NOT RUN; V0 결과에서 제외 |
| V0 추적 목록 | `.work/d-trace-kpbg8wov/` | 실제 V0의 nm·ftrace 목록 및 기존 trace 설정 |
| V0 추적 목록 재검토 | `.work/d-v0-trace-review-tn05x3nv/` | raw 목록·빌드 심볼 대조, 파일 소유권 및 SHA-256, 로그 기반 캡처 |
| V1 설치 직전 사전 검사 | `.work/d-install-v1-8fm6wq49/` | 11월 22일 현재 입력·공간 확인 PASS; 설치·부팅 NOT RUN |
| V1 실제 설치 | `.work/d-install-v1-620yzg_3/` | 14:28:57 UTC에 `stage=installed`, `installation=PASS`. `kernel-install.log`에 initramfs 생성 기록. 원본 로그 기반 보고서는 `.work/d-v1-install-review-dpaj5t9q/`에 보존 |
| V1 TCP·패킷·추적 목록 | `.work/d-tcp-1e68vc59/`, `.work/d-packets-u24c03hh/`, `.work/d-trace-po4dphlg/` | 송수신 원본 JSON·pcap, peer.log·MIB 전후 값, 부팅 커널의 함수 목록 |
| V1 기본 시험 재검토 | `.work/d-v1-runtime-review-yg4hc1uk/` | `review.json`에 확인 결과, `SHA256SUMS`에 세 원본 폴더의 파일 체크섬 |
| V1 내부 ACK 진단 첫 시도 | `.work/d-packets-9w6sxtcp/` | `summary.json`과 `commands.log`에 peer 실행 전 중단 및 namespace 정리 기록. 위 V1 검토 폴더의 `report.png`에 원본 행·해시와 함께 캡처 |
| V1 내부 ACK 진단 재실행 | `.work/d-packets-1a9wiqxd/` | `ack-trace.log` 26–61행과 75–83행, `peer.log`, `netstat.json`, probe·버퍼 손실 기록 |
| V1 ACK 원본 검토 | `.work/d-v1-ack-review-z6ytnc5c/` | `review.json`, 원본 체크섬 `SHA256SUMS`, V1 ELF 호출 명령 `caller-disassembly.log`, 원본 행·해시를 표시한 로컬 보고서 `report.html`과 캡처 `report.png` |
| V0 내부 ACK 진단 | `.work/d-packets-7zdom5jk/` | `ack-trace.log` 26–61행(PARTIAL), 75–83행(DSACK_OFO), `peer.log`, `netstat.json`, probe 및 버퍼 손실 기록 |
| 두 커널의 추적 대조 | `.work/d-v0-ack-review-rke25q26/` | `comparison.json`, 커널별 호출 명령, 두 진단 폴더 원본 48개 파일의 `SHA256SUMS`, V0 원본 행·해시를 표시한 `report.html`과 캡처 `report.png` |

각 preflight의 `status.json`, `build-status.json`, `additional-artifacts.json`,
ELF/vermagic 명령·로그·종료 코드 및 `SHA256SUMS`를 보존한다. 검토 디렉터리에는
원본 로그의 경로·행·hash를 표시한 로컬 보고서와 캡처를 별도로 보존한다.
캡처는 **로그 기반 보고서 화면**이며 커널 콘솔이나 부팅 성공 화면이 아니다.
이 증거는 현재 로컬에만 있으며 GitHub 첨부·팀 공유 업로드는 아직 하지 않았다.
검토 폴더의 `report.html`과 `report.png`는 실제 로그에서 생성했다. Firefox는
PNG 생성 후에도 종료되지 않아 25초 뒤 해당 프로세스 그룹을 종료·회수했다.
캡처 파일은 직접 열어 확인했으며 정상 브라우저 종료로 보고하지 않는다.
`capture-status.json`과 `capture.log`에 이 상태를 남겼고 임시 서버는 사용하지 않았다.

이번 사전 검사와 단위 시험에 예상 밖 실패는 없었다. 단위 시험의
`Require a completed build`와 `modules-install failed` 출력은 합성 입력으로
실패 거부를 확인한 음성 대조다. 실제 커널 설치 실패 로그로 해석하지 않는다.
준비 단계에서는 sudo 인증이 필요해 설치를 실행하지 않았다. 이후 사용자가 VM
터미널에서 V0 설치를 실행했으며, 2026-11-18 17:59:01–17:59:56 KST에 완료했다.
`modules-install`, `kernel-install`, `grub`, `initramfs-list`와 설치 모듈 vermagic
47개 배치의 종료 코드는 모두 0이었다. 설치된 initramfs SHA-256은
`d4f7f2a0f9ba2b29bb7d1895a1c8a985deab2ab426cd7949e2ee4dbb6141ef98`이다.
이 설치 성공을 부팅 성공으로 해석하지 않는다.
이후 사용자가 UTM에서 V0로 재부팅했다. 2026-11-18 18:29 KST에 실제
`uname -r=5.4.214-rtx-v0`와 빌드/설치 config·Image 일치를 별도로 확인했다.
이후 사용자가 V0에서 TCP·패킷 시험을 실행했다. 원본 endpoint JSON을 재판정하고
peer/MIB 결과, source·실행 바이너리 hash, 전체 dmesg와 시험 전후 로그 연속성을
확인했다. TCP 캡처는 430개이며 SACK-permitted 패킷 32개, 실제 SACK 블록 패킷
1개가 있었다. 패킷 캡처만으로 내부 walker 실행을 입증하지 않는다.

`TCPDSACKRecv`, `TCPDSACKOfoRecv`, `TCPSACKReneging`은 각각 +1이다.
shift MIB는 shifted=1, merged=0, fallback=6이며 full merge 실행 증거는 아니다.
PARTIAL은 TLP off 상태에서 ACK 2000바이트 후 `unacked=2`를 관측했고 최종
재전송은 0이었다. RENEGING은 offset=0 선행 재전송 1개를 검증하고 건너뛴 뒤
offset=1000 재전송 및 `unacked=3, sacked=0`을 확인했다. 단일 GSO skb의
trim 및 내부 DSACK 순회는 여전히 별도 관측이 필요하다.

전체 부팅 로그에는 ACPI NUMA 초기화 실패/SRAT 부재와 audit 출력 제한
(`kauditd_printk_skb: 44 callbacks suppressed`) 메시지가 있다. 이 세 종류는
기존 C23 RB 부팅 로그에도 있었다. 새 TCP 오류로 분류하지 않지만 무경고 부팅으로
표시하지 않는다. 로그 위치·행 및 이전 기록과의 비교는 `review.json`에 남겼다.
보조 검토에서는 새 파일의 `git diff --no-index --check`가 진단 출력 없이
반환한 1을 처음에 실패로 처리했다. 차이 존재와 공백 오류를 구분하도록 검토
명령의 판정을 고쳤으며 `diff-check-diagnostic.json`과 재검증 기록을 보존했다.

11월 22일 VM 재시작 후 첫 추적 목록 수집은 C23 RB로 부팅된 상태에서 실행되어
V0의 ftrace 결과가 전부 NOT RUN이었다. 해당 기록은 보존하고, V0를 선택해 재부팅한
뒤 새 결과 `d-trace-kpbg8wov`를 수집했다. 현재 release와 설치 config/Image,
빌드 vmlinux hash가 고정 V0와 일치함을 다시 확인했다.

| V0 함수 | 실제 ftrace 가용성 | 후속 관측 |
| --- | --- | --- |
| `tcp_ack` | AVAILABLE | ACK 경로와 소켓 회계 연결 |
| `tcp_sacktag_write_queue` | AVAILABLE | SACK 처리 구간 |
| `tcp_sacktag_walk` | AVAILABLE | DSACK 대상 연결·구간의 순회 관측 준비 |
| `tcp_trim_head` | AVAILABLE | 부분 ACK trim의 entry/return/호출자 관측 준비 |
| `tcp_clean_rtx_queue`, `tcp_shift_skb_data`, `tcp_tso_acked` | UNAVAILABLE | 이 빌드에는 독립 심볼 없음; 위 호출부·대체 지점 사용 |
| `rtx_find_cover` | UNAVAILABLE | V0 원본에는 RTX 어댑터 API가 없음 |

전역 trace 상태는 `current_tracer=nop`, `function_profile_enabled=0`,
`events/enable=0`, kprobe/uprobe 목록 비어 있음이었다. `tracing_on=1`만으로
함수 추적이 실행됐다고 판단하지 않는다. 이번 도구는 설정을 읽기만 했으며
실제 trim·DSACK 순회 경로를 실행하거나 성능을 측정하지 않았다.

## 실행 절차와 V1 부팅

아래는 사용자가 VM 터미널에서 완료한 V0 설치 명령이다. 현재 VM에서 반복 실행할
필요는 없다. 새로운 설치에서는 비밀번호를 로컬 sudo 프롬프트에만 입력한다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
python3 -B rtx-project/scripts/install_arm64.py \
  --variant v0 \
  --build-run "$PWD/rtx-project/.work/build-v0/validation-davvdlkt" \
  --install
```

출력된 새 `d-install-v0-*` 폴더의 `status.json`에서 `status=PASS`,
`stage=installed`, `installation=PASS`를 확인한다. 사전 검사 단계의 PASS와
구별한다. 실패 시 반복 설치하거나 기존 파일을 삭제하지 말고 해당 로그부터 확인한다.

설치 확인 후 UTM 콘솔을 열고 재부팅하여 GRUB의 `Advanced options for Ubuntu`
아래 `Ubuntu, with Linux 5.4.214-rtx-v0`를 선택한다. recovery mode 항목은 쓰지 않는다.
SSH 재접속 후 `uname -r`가 정확히 그 값인지 확인한다. 기존 정상 커널은 보존한다.
현재 VM은 아래 V0 부팅 신원·TCP·패킷·추적 목록 수집 명령까지 완료했다.
다음 명령은 재현 기록이며 이미 통과한 시험을 반복할 필요는 없다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
uname -r
sudo python3 -B rtx-project/scripts/run_arm64_tcp.py \
  --variant v0 --build-output "$PWD/rtx-project/.work/build-v0"
sudo python3 -B rtx-project/scripts/run_arm64_packets.py \
  --variant v0 --build-output "$PWD/rtx-project/.work/build-v0"
sudo python3 -B rtx-project/scripts/inspect_arm64_trace.py \
  --variant v0 --build-output "$PWD/rtx-project/.work/build-v0"
```

TCP·패킷의 `summary.json`은 실행 결과를, ftrace의 `report.json`은 함수의 추적
가능 여부를 확인하는 자료다. ftrace 목록이 AVAILABLE이어도 함수 실행 증거는 아니다.

11월 22일 VM 터미널에서 다음 명령으로 V1을 설치했다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
python3 -B rtx-project/scripts/install_arm64.py \
  --variant v1 \
  --build-run "$PWD/rtx-project/.work/build-v1/validation-gszehhr3" \
  --install
```

UTM 화면을 열고 VM을 재부팅한다. GRUB의 `Advanced options for Ubuntu`에서
`Ubuntu, with Linux 5.4.214-rtx-v1`을 선택한 뒤 `uname -r`가
`5.4.214-rtx-v1`인지 확인한다. TCP·패킷·추적 목록은 위 V0 명령의
`--variant v1`, `build-v1`로 바꿔 같은 순서를 따른다.
두 버전 모두 이번에는 `--smoke-current-kernel` 대신 pin을 확인하는 옵션을 쓴다.
V1 기본 시험을 맞춘 뒤 두 버전의 내부 trim·잔여 회계·DSACK 경로 관측을 진행한다.

## V1 결과와 내부 ACK 진단

V1에서 전송한 12건 모두 송수신 길이와 SHA-256이 일치했고 reset 4건을 확인했다.
TCP 캡처 426개 중 SACK 블록을 포함한 패킷은 0개였다(V0는 430개 중 1개).
별도 패킷 시험에서 SACK, PARTIAL, DSACK, DSACK_OFO, RENEGING 모두 통과했고
DSACK 수신·OFO DSACK 수신·reneging 카운터는 V0와 같이 각각 1 증가했다.
두 버전 모두 2,000바이트 부분 ACK 후 unacked=2였고, reneging에서 offset
1,000의 1,000바이트 재전송을 확인했다. 두 시험이 만든 namespace는 정리됐다.
V1 부팅 로그(command-001.log)의 ACPI NUMA/SRAT 및 audit 출력 제한 메시지는
V0에서도 같은 종류가 관찰됐다.

부팅된 V1에서 다음 명령으로 내부 경로의 원본 기록을 수집했다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
sudo python3 -B rtx-project/scripts/run_arm64_packets.py --variant v1 \
  --build-output rtx-project/.work/build-v1 --observe-acks rtx-project/.work/d-trim-layout-1j6nles6
```

V0 실행에는 `--variant v0`, `build-v0`, `d-trim-layout-5sifmkix`를 사용한다.
새 폴더의 `ack-trace.log`, `event-pid.txt`, `kprobe-profile.txt`,
`trace-buffer.json`, `peer.log`를 함께 검토한다. ACK별로 같은 소켓의 이벤트를
연결한다. PARTIAL에서는 trim의 진입·호출자·반환과 ACK 회계 갱신 후
`tcp_rate_skb_delivered`의 skb 주소·시퀀스·남은 세그먼트 수를 대조한다.
DSACK_OFO에서는 순회 진입과 `tcp_sacktag_one`의 실제 범위·중복 표시를 대조한다.
probe missed-hit, 버퍼 손실, PID 필터 밖에서 처리된 ACK 또는 누락된 marker는
판정 보류 사유다. `ack_trace=COLLECTED`는 원본 수집 상태다.

도구 검증에서는 두 구성의 native peer가 GCC 경고 없이 컴파일됐다. 두 pin의
실제 레이아웃을 확인했고 잘못된 SHA를 거부했다. ARM64 도구 시험은 VM에서
49개 통과했다. 최초 sandbox 실행에서 loopback 소켓 생성이 차단되어 7개가
실패했고, VM 재실행 로그는 검토 폴더의 `arm64-tests.log`에 남겼다.

V1 내부 ACK 진단 첫 시도는 tracefs 설정 중 `[Errno 22] Invalid argument`로
종료됐다. peer 실행 전이라 ACK 추적 표본은 없으며 namespace 정리 오류는 0건이었다.
이 빌드의 `kprobe_events`가 사용하는 `seq_lseek`는 파일 끝으로의 이동을
거부한다. 같은 방식의 파일에서 Python append 스트림으로 오류를 재현했다.
도구를 수정해 seek 없이 각 probe 정의를 한 번의 write로 추가하고 실패 시
제어 파일과 명령을 기록한다. 레이아웃 일치 확인과 seek 없는 probe 추가 시험
2개가 통과했다(`ack-trace-tests.log`). `marker-test.log`에 사용자 공간의
시작·끝 marker 형식 및 잘못된 FD 입력 검사 결과를 보존했다.

수정 후 재실행에서 패킷 5개가 통과했고, 새 커널 오류와 정리 오류는 각각
0건이었다. 추적에는 62개 이벤트가 모두 보존됐다. CPU 4개의 overrun과 drop,
probe 6개의 missed-hit는 모두 0이었다. peer PID와 두 구간의 시작·끝 marker,
연결별 시작 시퀀스, 각 ACK의 소켓 식별자가 일치했다.

- **PARTIAL** — 해당 ACK에서 `tcp_trim_head`가 정확히 한 번 실행됐다.
  진입 시 구간은 `[base, base+4000)`, 세그먼트 수는 4, trim 길이는 2,000이었다.
  반환 이벤트는 `tcp_ack+0xb78 <- tcp_trim_head`와 `ret=0`을 기록했다.
  진입 시 stack 출력은 `tcp_trim_head` 다음에 `tcp_rcv_established`를 표시했다.
  직접 호출자는 반환 이벤트와 고정 V1 ELF의 `tcp_ack+0xb74` 호출 명령으로 확인했다.
  같은 ACK의 `tcp_rate_skb_delivered`에서 같은 skb가
  `[base+2000, base+4000)`·2세그먼트로 남았고, peer의 `tcpi_unacked`도 2였다.
  고정 V1에서 C 전용 `ack_trim`·`ack_unlink` 카운터 값은 직접 측정하지 않았다.
- **DSACK_OFO** — 같은 ACK에서 `tcp_sacktag_walk`가 base 기준
  `[1000,2000)`, `[1000,3000)`을 차례로 순회했다. `tcp_sacktag_one`은
  첫 범위에 `dup=1`, 새로 처리한 `[2000,3000)`에 `dup=0`을 기록했다.
  peer에서 `unacked=4, sacked=2`였고 namespace의 `TCPDSACKOfoRecv`가 1 증가했다.

재실행의 `summary.json`에서 내부 경로 두 항목은 패킷 판정기의 기본값인
`NOT RUN`으로 남는다. 위 결과는 별도로 수집한 추적 원본을 대조한 판정이다.

## V0와 V1의 최종 대조

V0 재부팅 후 동일한 peer 바이너리로 패킷 5개를 다시 실행했다. 아래 값은 각
실행에서 peer가 표시한 해당 ACK 구간 안의 원본 추적과 TCP 회계를 대조한 결과다.
시퀀스 범위는 연결마다 다른 시작 시퀀스에 대한 바이트 오프셋이다.

| 관측 항목 | V0 | V1 |
| --- | --- | --- |
| PARTIAL의 `tcp_ack`에서 호출한 trim | 2,000바이트 1회, 반환 0 | 2,000바이트 1회, 반환 0 |
| 같은 skb의 잔여 범위·세그먼트 및 `tcpi_unacked` | [2000,4000), 4→2, unacked=2 | [2000,4000), 4→2, unacked=2 |
| DSACK_OFO의 두 순회 범위 | [1000,2000), [1000,3000) | [1000,2000), [1000,3000) |
| 첫 범위와 새로 처리한 [2000,3000)의 중복 플래그 | 1, 0 | 1, 0 |
| `TCPDSACKOfoRecv` 증가량 | +1 | +1 |
| 추적 버퍼 보존·probe missed-hit | 62/62, 0건 | 62/62, 0건 |
| 시험 중 새 커널 오류·정리 실패 | 0건·0건 | 0건·0건 |

두 ELF에서 반환 이벤트의 직전 명령(`tcp_ack+0xb74`)이 `tcp_trim_head` 호출임을
확인했다. 원본 48개 파일의 체크섬을 검증했다. 체크섬 목록 자체의 SHA-256은
`6704da8f8cdab52c2f3880b540c3e6d73ff8ec44e5d8382678e1cce0ad2bcb5d`이다.

## 남은 작업과 #18 종료 판단

이번 V0·V1 런타임 검증에 예정한 VM 실행은 모두 완료했다. C 전용
`ack_trim`·`ack_unlink` 카운터의 직접 측정과 성능 측정은 이번 결과에 포함되지 않는다.
원본·체크섬·캡처는 현재 VM의 `.work/`에 보존돼 있다. 후속 PR에 결과를 올리고
팀이 접근할 수 있는 증거 위치를 연결한 뒤 #18의 종료 여부를 검토한다.
