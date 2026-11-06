# D 담당 테스트·빌드 재현 절차 초안

작성 기준: 2026-11-06 KST. 이 문서는 실행 순서와 판정 기준이다. 실제 이번 작업의 완료 여부·로그 위치는 별도 작업 기록에 남긴다. 아래 명령이 있다는 이유로 빌드·부팅·성능 측정이 완료된 것은 아니다.

## 1. 기준선과 현재 구현

| 대상 | 기준·상태 |
| --- | --- |
| V0 소스 | 팀 `v0-baseline`, `991defcc56cad49cfb221f44f5d161c9ea920c4a` |
| upstream 기준 | `v5.4.214`, `9f02aa34e179d1b0185a27980dd5bafd0f2e6e59` — [MANIFEST.md](MANIFEST.md)의 기존 기록 |
| 확인한 개발 소스 | `7c09b8883eb2d72beea5eb5928de7241a13e14fa` |
| 어댑터 | `include/net/tcp_rtx.h`의 RB wrapper. `rtx_append()`도 `tcp_rbtree_insert()` 호출 |
| 호출 치환 | `tcp.h`의 head/tail/empty/unlink만 위임됨. 송신 삽입, SACK 검색, ACK 순회, init/purge 등에 직접 RB 접근이 남음 |
| 미구현 API | 설계의 `rtx_first_overlap()`, `rtx_validate()`, transaction token 및 hybrid 상태 필드는 확인한 구현에 없음 |

현재 코드는 V1 방향의 중간 상태다. 완성된 V1 동등성 검증이나 V2~V4 구현을 전제하지 않는다. V0는 **기존 과제용 실행 커널 `5.4.214-kimseungbeom+`와 별개**다. 같은 버전 번호는 같은 소스·설정을 뜻하지 않는다.

팀 태그와 upstream의 차이가 x86용 `rtx_defconfig` 하나라는 설명은 MANIFEST의 기존 검증 기록이다. upstream 객체가 로컬에 없으면 이번 로컬 실행에서 그 비교를 다시 확인했다고 쓰지 않는다. 현재 개발 브랜치를 V0로 빌드하지 않고 별도 detached worktree를 쓴다.

## 2. 실제 작업 위치와 환경 수집

이하 셸 명령은 **VS Code의 VM 원격 터미널**에서 실행한다. macOS 터미널에서 직접 실행하지 않는다. 커널 선택·SSH 복구는 **UTM 화면**에서 한다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
hostname
uname -m
uname -r
pwd
git status --short --branch
git rev-parse HEAD
git worktree list
df -h / /boot
```

2026-11-06 작업 시작 시 실제 출력은 `ubuntu-tcp`, `aarch64`, Ubuntu 20.04.5 LTS, `5.4.214-kimseungbeom+`, 브랜치 `test/d-benchmark`였다. 4 vCPU, RAM 약 3.8 GiB, GCC 9.4.0이며 `/` 여유 약 24 GiB, `/boot` 여유 약 1.3 GiB였다. 디스크 여유는 빌드·설치 직전에 다시 확인한다. VM 복제와 디스크 확장을 다시 할 필요는 없다.

```bash
rtx-project/scripts/capture_env.sh --label before-v0
```

기본 결과 위치는 `rtx-project/.work/results/env-<UTC>-<label>-<고유값>/`이다. 다른 위치는 `--output-root /원하는/경로`로 지정한다. 각 실행은 새 디렉터리를 만들고 그 절대 경로를 출력한다. 디렉터리 권한은 0700이다. 아래는 최종 빌드 설정도 함께 보존하는 예다. `RTX_V0_BUILD`는 다음 절에서 지정한다.

```bash
rtx-project/scripts/capture_env.sh \
  --label configured-v0 --build-config "$RTX_V0_BUILD/.config"
```

`statuses.tsv`에서 probe별 `ok`, `missing`, `permission_denied`, `timeout`, `error`와 종료 코드·로그를 확인한다. `summary.txt`를 먼저 읽고 실패한 probe의 로그를 확인한다. 종료 코드 0은 수집 완료, 2는 선택적 도구·접근 권한 등의 누락이 있는 부분 수집, 1은 잘못된 인자나 출력 생성 실패 등 치명적 오류다. **수집 성공은 커널·네트워크 테스트 통과가 아니다.** 명시한 `--build-config`가 읽히지 않으면 실행을 실패 처리한다.

실행 중 커널의 `running.config`와 지정한 `build.config`는 서로 다른 파일로 복사하고 SHA-256을 남긴다. 빌드 소스 SHA와 실행 커널이 동일하다는 연결은 부팅 검증에서 따로 확인한다. 호스트명·주소·라우팅 등 환경 자료는 팀 공유 전에 내용을 검토한다. 결과 전체나 커널 산출물은 Git에 넣지 않는다.

## 3. ARM64 V0 준비

팀의 `arch/x86/configs/rtx_defconfig`는 x86용이다. ARM64에서 `make rtx_defconfig`를 실행하지 않는다. 실제 동작 중인 ARM64 커널의 `/boot/config-$(uname -r)`를 설정 출발점으로 쓴다. 과제 소스 변경은 가져오지 않는다.

작업 디렉터리는 Git에서 제외하는 `rtx-project/.work/` 아래에 둔다. **처음 준비할 때만** 아래를 실행한다. 같은 경로가 이미 있으면 `git worktree list`, 해당 worktree의 HEAD·상태, 기존 준비 로그를 확인해 재사용한다. 기존 경로를 강제로 삭제하거나 reset하지 않는다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
RTX_V0_SOURCE="$PWD/rtx-project/.work/v0"
RTX_V0_BUILD="$PWD/rtx-project/.work/build-v0"
git rev-parse 'v0-baseline^{commit}'
mkdir -p rtx-project/.work
git worktree add --detach "$RTX_V0_SOURCE" v0-baseline
rtx-project/scripts/prepare_v0_arm64.sh \
  --source "$RTX_V0_SOURCE" --output "$RTX_V0_BUILD" \
  --config "/boot/config-$(uname -r)"
```

스크립트는 native ARM64 환경, 고정한 태그 SHA와 clean source, 도구·원본 설정을 검사하고 커널의 기존 `scripts/config`, `scripts/diffconfig`를 재사용한다. `olddefconfig`와 `prepare`를 수행하고 전체 Image·모듈 빌드나 설치·재부팅은 하지 않는다. `--output` 경로가 이미 있으면 덮어쓰지 않는다. 준비 중 실패하면 로그를 보존하고 원인을 고친 뒤 새 출력 경로로 재시도한다. 완료된 출력은 이후 전체 빌드에서 그대로 사용한다.

새 터미널에서 이어갈 때는 위 `cd`, `RTX_V0_SOURCE=...`, `RTX_V0_BUILD=...`만 다시 지정한다. worktree 생성과 준비 명령을 반복할 필요는 없다.

준비가 완료되었다고 판정하려면 다음 자료가 있어야 한다.

- 고정한 V0 commit, clean worktree 상태, 설정 원본과 SHA-256.
- `olddefconfig` 적용 후 최종 `.config`, 원본 대비 차이, 최종 SHA-256.
- `CONFIG_ARM64=y`, namespace/veth/netem 사용 가능 설정, 구분 가능한 `CONFIG_LOCALVERSION="-rtx-v0"`.
- compiler 버전과 실제 `make ... kernelrelease` 출력, prepare 단계 로그·종료 코드.

이번 VM의 설정 원본은 `NET_NS=y`, `VETH=m`, `NET_SCHED=y`, `NET_SCH_NETEM=m`을 이미 포함한다. 모듈 설정은 해당 V0 모듈을 함께 빌드·설치해야 쓸 수 있다. `SYSTEM_TRUSTED_KEYS=""`, `MODULE_SIG_KEY="certs/signing_key.pem"`이다. 다른 설정을 가져오면 배포판 인증서의 존재하지 않는 파일 경로가 없는지 다시 확인한다. 키나 보안 설정을 근거 없이 일괄 변경하지 않는다.

원본은 `DEBUG_INFO=y`, `DEBUG_INFO_BTF=y`이다. 준비 스크립트는 이 성능용 설정의 `DEBUG_INFO`, `DEBUG_INFO_BTF`, `GDB_SCRIPTS`를 끄고, `NAMESPACES`, `NET_NS`, `VETH`, `NET_SCHED`, `NET_SCH_NETEM`을 builtin으로 설정한다. 차이를 보존하고 향후 비교군 전체에 동일하게 적용한다. debug 정보 비활성화는 빌드 공간·BTF 도구 의존성을 줄이지만 원본에 있는 모든 런타임 debug 기능을 끄는 것은 아니다. KASAN·lockdep 등 정확성 검증용 커널은 별도 설정·결과로 관리한다. V0의 부팅에 필요한 virtio·스토리지·파일시스템·EFI·console 설정을 임의로 지우지 않는다.

`LOCALVERSION_AUTO=n`이어도 이 커널의 `scripts/setlocalversion`은 조건에 따라 `+`를 붙인다. 아래처럼 모든 make 호출에 **`LOCALVERSION=`을 명시**하고 최종 release를 확인한다. 최종 `.config`가 바뀌면 이전 체크섬·준비 완료 기록을 재사용하지 않는다.

이 초안의 재현성은 같은 소스·설정·도구·절차로 실험을 재현하는 범위다. 빌드 시각·사용자명과 자동 생성되는 모듈 서명 키까지 통제한 bit-for-bit 동일 이미지 재현을 검증한 것은 아니다. 생성된 서명 개인 키는 공유하거나 커밋하지 않는다.

## 4. 전체 빌드, 설치, 부팅을 별도 단계로 기록

아래는 준비 완료 후 사람이 진행할 절차다. `RTX_V0_SOURCE`, `RTX_V0_BUILD`는 준비 결과의 절대 경로로 지정한다. 예시는 동일한 native GCC 빌드를 전제로 한다.

```bash
df -h / /boot
free -h
mkdir -p "$RTX_V0_BUILD/validation"
set -o pipefail
make -C "$RTX_V0_SOURCE" O="$RTX_V0_BUILD" ARCH=arm64 \
  CC=gcc HOSTCC=gcc CROSS_COMPILE= KCONFIG_CONFIG=.config LOCALVERSION= \
  -j2 Image modules dtbs \
  2>&1 | tee "$RTX_V0_BUILD/validation/build.log"
build_rc=$?
printf '%s\n' "$build_rc" > "$RTX_V0_BUILD/validation/build.exitcode"
test "$build_rc" -eq 0
```

4 vCPU·RAM 3.8 GiB의 이 VM에서는 먼저 `-j2`로 시작한다. `-j$(nproc)`는 필수가 아니다. 원본 설정은 많은 모듈을 포함하므로 전체 빌드가 오래 걸리고 큰 공간을 쓸 수 있다. 실패한 경우 로그 끝과 메모리·공간을 확인하고, 성공으로 기록하지 않는다. 위 마지막 `test`가 실패하면 다음 설치 단계로 넘어가지 않는다.

빌드 성공 후 같은 VM 원격 터미널에서 확인한다.

```bash
RTX_V0_RELEASE=$(make -s -C "$RTX_V0_SOURCE" O="$RTX_V0_BUILD" \
  ARCH=arm64 CC=gcc HOSTCC=gcc CROSS_COMPILE= KCONFIG_CONFIG=.config \
  LOCALVERSION= kernelrelease)
printf '%s\n' "$RTX_V0_RELEASE"
test "$RTX_V0_RELEASE" = '5.4.214-rtx-v0'
sha256sum "$RTX_V0_BUILD/.config" "$RTX_V0_BUILD/arch/arm64/boot/Image" \
  "$RTX_V0_BUILD/System.map"
df -h / /boot
```

설치는 `/lib/modules`와 `/boot`를 바꾸는 다음 단계다. 기존 부팅 가능한 커널을 보존하고, 같은 release가 이미 설치되어 있으면 그 출처를 확인한 뒤 진행한다. 현재 VM에서 `/sbin/installkernel`과 `/etc/kernel/postinst.d`의 initramfs·GRUB hook이 확인되었다. 설치 명령마다 로그와 종료 코드를 따로 보존한다.

```bash
set -o pipefail
sudo make -C "$RTX_V0_SOURCE" O="$RTX_V0_BUILD" ARCH=arm64 \
  CC=gcc HOSTCC=gcc CROSS_COMPILE= KCONFIG_CONFIG=.config \
  LOCALVERSION= INSTALL_MOD_STRIP=1 modules_install \
  2>&1 | tee "$RTX_V0_BUILD/validation/modules-install.log"
modules_rc=$?
printf '%s\n' "$modules_rc" > "$RTX_V0_BUILD/validation/modules-install.exitcode"
test "$modules_rc" -eq 0
```

`modules_rc=0`을 확인한 뒤에만 다음 명령을 실행한다.

```bash
sudo make -C "$RTX_V0_SOURCE" O="$RTX_V0_BUILD" ARCH=arm64 \
  CC=gcc HOSTCC=gcc CROSS_COMPILE= KCONFIG_CONFIG=.config LOCALVERSION= install \
  2>&1 | tee "$RTX_V0_BUILD/validation/kernel-install.log"
install_rc=$?
printf '%s\n' "$install_rc" > "$RTX_V0_BUILD/validation/kernel-install.exitcode"
test "$install_rc" -eq 0
```

설치 hook의 종료와 실제 파일을 확인한다. 필요한 initramfs가 없으면 `sudo update-initramfs -c -k "$RTX_V0_RELEASE"`, 이미 있으면 `sudo update-initramfs -u -k "$RTX_V0_RELEASE"`로 생성·갱신하고 종료 코드와 로그를 저장한다. 이어 `sudo update-grub` 결과와 `/boot/grub/grub.cfg`의 새 release 항목을 확인한다. 커널 이미지, `config`, `System.map`, `initrd.img`, `/lib/modules/$RTX_V0_RELEASE`가 모두 있어야 한다. 이 VM의 installkernel 경로에서는 빌드한 `Image`를 `/boot/vmlinuz-$RTX_V0_RELEASE`로 복사하므로 두 파일의 SHA-256도 대조한다.

이 단계까지도 **실행 중인 커널은 바뀌지 않는다.** UTM 화면을 열어 복구 가능한 상태에서 재부팅하고 GRUB에서 V0를 선택한다. SSH가 다시 연결되면 **VM 원격 터미널**에서 확인한다.

```bash
uname -r
test "$(uname -r)" = '5.4.214-rtx-v0'
rtx-project/scripts/capture_env.sh --label booted-v0 \
  --build-config "$RTX_V0_BUILD/.config"
```

`uname -r`, 설치 config와 최종 빌드 config의 SHA-256 일치, 부팅 로그의 새 오류 여부를 저장하고 그때 V0 부팅 완료로 표시한다. 부팅 실패 시 UTM에서 기존 커널을 선택하고 실패 release·증상을 남긴다. SSH가 끊기는 재부팅은 이 준비 스크립트가 자동 실행하지 않는다.

## 5. 기존 테스트 재사용과 다음 구현 순서

현재 `tools/testing/selftests/net/`를 먼저 검토했다. `in_netns.sh`는 임시 namespace와 종료 시 정리 동작을 제공하지만 한 namespace의 loopback 실행용이라 veth 두 끝의 전송 토폴로지는 별도 준비가 필요하다. `tcp_inq.c`는 TCP_INQ 동작, `tcp_fastopen_backup_key.sh`는 TFO 키 갱신, `fcnal-test.sh`는 IPv4/IPv6·VRF 통신 회귀에 재사용할 수 있다. 전체 selftests의 통과를 RTX 자료구조 동등성으로 해석하지 않는다.

해당 테스트의 Makefile·`config`를 참고해 필요한 헤더·라이브러리·커널 기능을 확인한다. 범용 selftests 전체에는 NUMA·BPF·VRF 등 추가 의존성이 있다. 먼저 선택한 테스트만 준비하고 결과에 대상 이름·소스 SHA·skip/실패 이유를 남긴다. 기존 스크립트의 고정 namespace 이름과 정리 범위도 실행 전에 확인한다.

이후 D 구현 순서는 다음과 같다. 아직 없는 스크립트를 실행했다고 기록하지 않는다.

1. V0 전체 빌드·설치·부팅과 최소 네트워크 기능 확인.
2. 실행별 고유 namespace 두 개와 veth를 만드는 setup/cleanup 도구. 생성 자원만 정리하고 중단 후 재실행 가능하게 한다.
3. 손실 없는 양방향 payload 길이·SHA-256 검사, 정상 종료·half-close·reset·반복 연결 검증.
4. iperf3 JSON 원시 출력·명령·종료 코드를 보존하는 실행 파이프라인 smoke test.
5. 테스트용 namespace/veth에만 netem 적용·복구, 반복 실행과 집계.
6. A/B/C와 계측 계약을 고정한 뒤 실제 구현된 V1~V4 비교로 확장.

**SSH 관리 인터페이스에는 netem, offload 변경, qdisc 삭제를 적용하지 않는다.** cleanup은 이름 패턴 전체가 아니라 이번 실행에서 실제 생성한 자원 목록만 대상으로 한다. 전역 네트워크 초기화나 다른 namespace 삭제를 복구 방법으로 쓰지 않는다.

## 6. 정확성 우선 게이트

| 단계 | 필요한 관찰·통과 조건 |
| --- | --- |
| 무손실 연결 | 송수신 payload 길이·해시 일치, timeout 없음, 정상 종료·half-close·reset의 예상 결과 일치 |
| 손실·지연·재정렬 | 정한 제한 시간 안에 완료, 의도한 조건·실제 RTT·qdisc drop 기록, 새 데이터 오류 없음 |
| 커널 회귀 | 실행 전후 로그 비교에서 새 WARN/OOPS·메모리 오류 없음; 읽기 권한이 없으면 미검증으로 남김 |
| 자료구조 모델 | 같은 seed/연산 trace의 노드 ID·순서·검색 결과가 기준 모델과 동일; seed·실패 trace 보존 |
| 실제 TCP 경계 | ACK/partial ACK, SACK/DSACK, split/shift/merge, 힌트 수명, purge, sequence wraparound, GSO, SYN/FIN 확인 |

모델에는 빈 큐, 1개, K−1/K/K+1, 마지막 삭제 후 재사용, T/L 각각·양쪽, 승격과 경계 삭제를 포함한다. `rtx_unlink()`는 구조만 제거하며 free·TCP 회계·tsorted 정리는 별도라는 실제 계약을 지킨다. skb의 list/rb 링크 union 동시 사용을 허용하지 않는다. SACK 수신이 임의 중간 skb 삭제와 같다고 전제하지 않는다.

자료구조 모델 통과는 실제 커널의 TCP 검증을 대체하지 않는다. netem은 정확한 SACK 경계나 reneging을 보장하지 않는다. 그런 사례는 ARM64·커널 호환성을 확인한 packetdrill 또는 결정적 패킷 시나리오로 별도 검증한다. 미설치·권한 부족·호환성 문제는 `SKIP` 또는 `NOT RUN`으로 남기며 `PASS`로 세지 않는다. 데이터 불일치, 새 WARN/OOPS, 진행 정지, 회계 불일치는 실패다.

## 7. 성능 프로토콜 초안

비교군은 V0(기준), V1(RB 어댑터), V2(캐시), V3(tail 직접 삽입), V4(최근 리스트+트리)다. 실제 구현·정확성 게이트를 통과한 버전만 측정한다. V4는 V0뿐 아니라 V3와 비교한다.

| 항목 | 기록·원칙 |
| --- | --- |
| 조건 후보 | RTT 1/20/100 ms, 손실 0/0.1/1%, 연결 1/8개에서 대표 조건 사전 선택 |
| 큐 크기 | 실제 skb 기준 N/T/L과 K. `ss -ti`의 unacked나 packets_out을 skb 수로 쓰지 않음 |
| 네트워크 | netem 편도 지연·양쪽 적용 여부·limit·실제 RTT, MTU, 혼잡 제어, offload 상태 |
| 주 지표 | 수신 완료 payload 기반 goodput, sender CPU cycles/byte 또는 CPU time/byte, 요청 응답 p50/p99·요청 수 |
| 원인 지표 | 비교·삽입·삭제·검색 비용, 승격 수, 승격 전 ACK 제거 수, N/T/L 분포, 재전송·timeout·메모리 |
| 반복 | 조건당 warm-up 10초, 본 측정 30~60초, 독립 실행 최소 10회. 교차 또는 무작위 버전 순서 |
| 통제 | 같은 VM 자원·compiler·기능 설정·계측 설정·CPU 배치. 호스트 부하와 IRQ/softirq 범위 기록 |
| 통계 | 모든 원시값, 중앙값, IQR/분산, 실행 단위 95% 신뢰구간. p99는 요청 수·실행별 변동도 기록 |

현재 UTM ARM64 VM의 PMU 접근은 별도 실행 검증 대상이다. MANIFEST의 VirtualBox “PMU 미지원” 기록을 복사하지 않는다. `perf --version`이나 `perf list`만으로 cycles 사용 가능을 확정하지 않고 실제 카운터 실행과 종료 코드를 확인한다. cycles가 불가능하면 추정치를 채우지 않는다. PID만 측정하면 softirq 비용이 빠질 수 있고, 단일 VM veth에는 송수신·netem 비용이 섞인다. 결과의 측정 범위를 명시한다.

계측용/debug 결과와 최종 성능 결과를 나눈다. K=4/8/16은 탐색 조건에서 선택한 뒤 고정하고 별도 확인 조건에서 평가한다. 잡음이 예상 개선폭보다 크면 판정 불가다. 제안 기준인 V3 대비 CPU/byte 3% 감소, goodput 회귀 2% 이내, p99 회귀 5% 이내는 **팀과 측정 전에 합의할 값**이며 확정된 채점 기준이 아니다. 변화율은 `(modified - baseline) / baseline`으로 기록하고 비용 지표는 음수가 개선이다.

## 8. 결과에 반드시 남길 것

각 단계에 실행 시각·환경 수집 경로·source SHA·config SHA-256·명령·종료 코드·원시 로그 위치·판정·미검증 이유를 남긴다. 준비/전체 빌드/모듈 설치/커널 설치/initramfs·GRUB/부팅/회귀/성능을 각각 독립 상태로 관리한다. 실패 실행을 지우거나 성공 실행만 골라 통계에 넣지 않는다.

팀에 커밋하는 것은 재현 스크립트, 조건, 필요한 작은 요약, 원시 결과의 위치·체크섬이다. 커널 이미지·빌드 디렉터리·대용량 raw 데이터·인증 키는 커밋하지 않는다. 설치·부팅·네트워크 기능·성능은 실제 확인한 범위만 완료로 표시한다.
