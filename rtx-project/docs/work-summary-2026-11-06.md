# TCP RTX 프로젝트 작업 정리 — 2026-11-06

작성 기준: 2026년 11월 6일, 한국 시간(KST, UTC+9).

이 문서는 현재 저장소의 파일·Git 이력·실행 로그와 이번 대화에서 확인한 작업을 정리한다. 범위는 D 담당 테스트·빌드 재현 환경 준비와 검증이다. 실행 절차는 [testing.md](testing.md), 실제 진행 결과는 이 문서를 참고한다.

## 1. 현재까지의 결과

**테스트용 스크립트 작성, 단위 테스트 17개 통과, V0 커널 설정·빌드 준비, TCP 소스 두 개의 개별 컴파일까지 완료했다. 전체 커널 빌드·설치·부팅과 실제 TCP 동작·성능 검증은 수행 완료하지 않았다.**

| 작업 | 상태 | 확인 근거 |
| --- | --- | --- |
| 작업 브랜치와 V0 기준 소스 확보 | 완료 | `test/d-benchmark`, 별도 V0 detached worktree |
| 환경 수집 스크립트 작성 | 완료 | `scripts/capture_env.sh` |
| 실제 환경 정보 수집 | 부분 완료 | 58개 항목 중 45개 정상, 13개 누락·오류, 종료 코드 2 |
| ARM64 V0 준비 스크립트 작성 | 완료 | `scripts/prepare_v0_arm64.sh` |
| V0 설정 및 빌드 준비 실행 | 완료 | `stage=prepared`, `exit_code=0` |
| V0 TCP 입력·출력 소스 개별 컴파일 | 완료 | `tcp_input.o`, `tcp_output.o`, 종료 코드 0 |
| 보조 스크립트 단위 테스트 | 통과 | 환경 수집 4개 + V0 준비 13개 = 17개 |
| 재현·검증·성능 측정 절차 문서화 | 초안 작성 완료 | `docs/testing.md` |
| 전체 커널 Image·modules·dtbs 빌드 | 미실행 | 전체 빌드 시작 전 이번 작업 종료 |
| 커널 설치·initramfs·GRUB 갱신·V0 부팅 | 미실행 | 실행 커널은 기존 `5.4.214-kimseungbeom+` |
| 실제 TCP 정확성·재전송·성능 검증 | 미실행 | 실행 결과 없음 |

장시간 빌드 예상 시간을 확인한 뒤, 사용자의 요청에 따라 이번 작업은 현재 결과까지로 마무리했다. 이후에는 작업 정리 문서만 작성했다.

## 2. 작업 환경과 소스 기준

| 항목 | 확인 내용 |
| --- | --- |
| 작업 경로 | `/home/seungbeom/tcp-rtx/linux-rtx-hybrid` |
| VM 호스트명 | `ubuntu-tcp` |
| OS / 아키텍처 | Ubuntu 20.04.5 LTS / `aarch64` |
| VM 자원 | 4 vCPU, RAM 약 3.8 GiB, swap 약 3.8 GiB |
| 실행 중인 커널 | `5.4.214-kimseungbeom+` |
| 컴파일러 | GCC 9.4.0, native ARM64 |
| 링커 / 빌드 도구 | GNU ld 2.34 / GNU Make 4.2.1 |
| 작업 브랜치 | `test/d-benchmark` |
| 개발 소스 HEAD | `7c09b8883eb2d72beea5eb5928de7241a13e14fa` |
| V0 기준 태그 | `v0-baseline` |
| V0 기준 커밋 | `991defcc56cad49cfb221f44f5d161c9ea920c4a` |
| V0 소스 경로 | `rtx-project/.work/v0` — detached HEAD, 변경 없는 상태 확인 |
| V0 빌드 출력 경로 | `rtx-project/.work/build-v0` |
| 준비한 V0 release | `5.4.214-rtx-v0` |
| 작업 종료 전 여유 공간 | `/` 약 22 GiB, `/boot` 약 1.3 GiB — 이후 작업 전 재확인 필요 |

기존 실행 커널과 이번에 준비한 V0는 구분한다. 현재 실행 커널의 설정을 출발점으로 사용했지만, V0 소스는 팀 기준 태그의 별도 worktree를 사용했다.

저장소에 이미 있던 RB 어댑터·순회 매크로와 `MANIFEST.md`, `design.md`는 작업의 기준 자료다. 이번 작업에서 새로 구현한 성과로 포함하지 않는다. [testing.md](testing.md)에 기록한 개발 소스는 V1 방향의 중간 상태이며, V2~V4의 구현 완료나 성능 향상을 확인한 상태가 아니다.

## 3. 작성한 파일과 역할

경로는 `rtx-project/` 기준이다.

| 파일 | 수행한 작업 |
| --- | --- |
| [.gitignore](../.gitignore) | `.work/`, `results/`, `__pycache__/`를 제외하여 로컬 worktree·빌드 결과·원시 로그를 소스와 구분 |
| [scripts/capture_env.sh](../scripts/capture_env.sh) | 실행 환경, 도구, Git 상태, 커널 설정, 네트워크 조회 결과를 실행별 디렉터리에 수집 |
| [scripts/prepare_v0_arm64.sh](../scripts/prepare_v0_arm64.sh) | 고정 V0 소스와 ARM64 설정을 검증하고 설정 생성·빌드 준비·release 확인을 자동화 |
| [tests/test_capture_env.py](../tests/test_capture_env.py) | 환경 수집의 실패 분류·결과 보존·입력 검증 테스트 4개 작성 |
| [tests/test_prepare_v0_arm64.py](../tests/test_prepare_v0_arm64.py) | V0 준비의 입력 검증·실패 처리·산출물 기록 테스트 13개 작성 |
| [docs/testing.md](testing.md) | 빌드·설치·부팅·정확성 검사·벤치마크의 실행 순서와 판정 기준 작성 |
| [docs/work-summary-2026-11-06.md](work-summary-2026-11-06.md) | 실제 완료 범위, 증거, 제한 사항, 후속 작업 정리 |

IDE에 열려 있던 저장소 루트의 `scripts/bin2c.c`는 HEAD와 동일하며, 이번 작업의 수정 대상이 아니었다.

### 환경 수집 스크립트

- 실행마다 고유한 결과 디렉터리를 만들고 기존 결과를 덮어쓰지 않도록 했다. 결과 디렉터리 권한은 `0700`이다.
- 개별 조회의 명령·출력·종료 코드를 저장하고, `ok`, `missing`, `permission_denied`, `timeout`, `error`로 분류한다.
- 실행 중 커널 설정과 명시적으로 지정한 빌드 설정을 별도 파일로 복사하고 SHA-256을 남긴다.
- 수집 완료는 종료 코드 0, 부분 수집은 2, 인자·출력 생성 등 치명적 오류는 1로 구분한다.
- 환경을 조회하는 스크립트이며, 네트워크 설정 변경이나 테스트 트래픽 발생은 수행하지 않는다.

### ARM64 V0 준비 스크립트

- ARM64 호스트·설정, 고정 태그와 HEAD, 소스 변경 여부, 필수 도구와 인증서 경로를 검사한다.
- 기존 출력 경로를 덮어쓰지 않고, 외부 빌드 플래그가 설정에 섞이는 경우를 거부한다.
- 커널의 기존 설정 도구와 빌드 시스템으로 `olddefconfig`, `prepare`, `kernelrelease`를 실행한다.
- 원본·최종 설정, 설정 차이, 체크섬, 명령, 단계별 로그와 종료 상태를 보존한다.
- 전체 Image·모듈 빌드, 설치와 재부팅은 이 스크립트의 실행 범위에 포함하지 않는다.

## 4. 실제 실행 및 확인 결과

### 4.1 환경 수집: 부분 완료

결과 경로: [env-20261105T165556Z-d-initial-K7Y6SP](../.work/results/env-20261105T165556Z-d-initial-K7Y6SP/).

- 수집 완료 시각: **2026-11-06 01:55:56 KST**.
- 전체 58개 항목 중 **45개 정상, 13개 누락·오류**.
- 최종 상태: `PARTIAL`, 종료 코드 `2`.
- 도구·정보 누락 4개: CPU governor 정보, `iperf3`, `perf`, `shellcheck`.
- 권한 거부 8개: 주소, IPv4/IPv6 라우팅, namespace, 링크, qdisc, 인터페이스 두 개의 offload 조회.
- 일반 오류 1개: 일부 `/proc/sys` 항목을 읽을 수 없어 `network-sysctl` 조회가 종료 코드 253으로 끝남.

근거는 [summary.txt](../.work/results/env-20261105T165556Z-d-initial-K7Y6SP/summary.txt), [statuses.tsv](../.work/results/env-20261105T165556Z-d-initial-K7Y6SP/statuses.tsv)와 각 항목의 `logs/`다. 이 결과는 당시 실행 환경에서의 조회 상태이며, 네트워크 기능 자체의 성공·실패 판정은 아니다. PMU 카운터 사용 가능 여부도 검증하지 않았다.

이 캡처의 `running.source`와 `build.source`는 모두 `/boot/config-5.4.214-kimseungbeom+`를 가리킨다. 따라서 캡처 안의 `build.config`를 이후 생성한 최종 V0 설정으로 해석하지 않는다. 최종 V0 설정은 다음 절의 `build-v0/final.config`다.

### 4.2 V0 설정 및 빌드 준비: 성공

실행은 **2026-11-06 01:56:59 KST**에 시작했고, 준비 상태 파일의 수정 시각은 **01:57:04 KST**다.

원본 설정은 `/boot/config-5.4.214-kimseungbeom+`를 사용했다. 주요 설정 변경은 다음과 같다.

| 설정 | 변경 내용 |
| --- | --- |
| `CONFIG_LOCALVERSION` | `-kimseungbeom` → `-rtx-v0` |
| `CONFIG_VETH` | 모듈(`m`) → builtin(`y`) |
| `CONFIG_NET_SCH_NETEM` | 모듈(`m`) → builtin(`y`) |
| `CONFIG_DEBUG_INFO` | `y` → 비활성화 |
| BTF·GDB 등 관련 debug 설정 | 설정 차이에 비활성화·제거 기록 |
| ARM64·namespace·네트워크 필수 설정 | 최종 설정에서 활성화 확인 |

`LOCALVERSION=`을 명시한 make 실행으로 최종 release가 `5.4.214-rtx-v0`임을 확인했다. [prepare-status.txt](../.work/build-v0/prepare-status.txt)의 결과는 다음과 같다.

```text
exit_code=0
stage=prepared
```

원본과 최종 설정의 SHA-256:

```text
seed.config
37b8cd3b70afa3d896e7601c0fe7d5586b09ca2eefdc341c5c135a58d70c94b8

final.config 및 현재 .config
244905ec793f52b799353aed7ce2fd32f3a48f9533ab5a9c3838c61d0db56e2f
```

문서 작성 시에도 `.config`와 `final.config`의 체크섬이 위 기록과 일치함을 확인했다. 세부 근거는 [commands.log](../.work/build-v0/commands.log), [prepare.log](../.work/build-v0/prepare.log), [config-delta.txt](../.work/build-v0/config-delta.txt), [config.sha256](../.work/build-v0/config.sha256)다.

### 4.3 TCP 소스 두 개의 개별 컴파일: 성공

V0 빌드 출력에 다음 객체 파일이 남아 있다.

- `net/ipv4/tcp_input.o`
- `net/ipv4/tcp_output.o`

[tcp-compile.log](../.work/build-v0/tcp-compile.log)에 두 파일의 `CC` 기록이 있으며, [tcp-compile.exitcode](../.work/build-v0/tcp-compile.exitcode)는 `0`이다. 종료 코드 파일의 수정 시각은 **2026-11-06 01:57:39 KST**다.

이는 V0 기준 소스 두 개의 컴파일 성공이다. 개발 브랜치 전체 빌드, 커널 링크, 부팅 또는 실제 TCP 통신을 검증한 결과는 아니다.

### 4.4 단위 테스트: 17개 통과

앞선 작업 상태 점검에서 실행한 명령:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s rtx-project/tests -v
```

결과는 **17개 모두 통과**, 실행 시간은 약 **0.55초**였다. 테스트는 임시 디렉터리와 모의 명령을 사용하며, 특히 V0 준비 테스트의 `make`는 실제 커널을 컴파일하지 않는다. 이 결과는 스크립트의 검증·제어 흐름·실패 처리에 대한 결과다. 이번 문서 작성에서는 테스트를 다시 실행하지 않았다.

| 대상 | 개수 | 검증 범위 |
| --- | --- | --- |
| 환경 수집 | 4 | 실패 분류·설정과 체크섬 보존, 재실행 시 결과 분리, 잘못된 입력 거부, 기존 출력 파일 보존 |
| V0 준비 | 13 | 잘못된 HEAD·태그, dirty source, Git 조회 실패, 비 ARM64 입력, 기존 출력, 끊어진 심볼릭 링크, 외부 빌드 플래그, 없는 인증서, make 실패, 필수 설정 누락, 잘못된 release, 마지막 소스 조회 실패, 정상 산출물·단계 순서 기록 |

17개 통과 결과는 이번 대화의 도구 실행 결과로 확인했다. 별도 영구 단위 테스트 로그 파일은 이번 정리에서 확인하지 못했으므로, 실제 실행 로그가 보존된 V0 준비·개별 컴파일과 구분한다.

또한 실제 준비·컴파일 로그는 01:57경 생성되었고, 스크립트는 이후 02:03경 추가 수정되었다. 최종 스크립트의 단위 테스트 통과는 확인했지만, 최종본으로 실제 V0 준비를 처음부터 다시 실행했다는 기록은 없다.

## 5. 문서화한 검증 계획

[testing.md](testing.md)에 다음 내용을 정리했다.

- VM 원격 터미널과 UTM 부팅 화면의 역할, 작업 위치, 기준 소스와 설정 식별 방법.
- ARM64 V0 준비 후 전체 빌드·설치·initramfs·GRUB·부팅을 각각 확인하는 순서.
- 기존 커널 네트워크 selftests의 재사용 후보와 해당 테스트가 보장하는 범위.
- namespace/veth를 사용한 독립 테스트 환경과 생성 자원만 정리하는 원칙.
- payload 길이·SHA-256, 정상 종료·half-close·reset·반복 연결을 통한 정확성 검증 항목.
- 손실·지연·재정렬, 커널 WARN/OOPS, 자료구조 모델과 TCP 경계 사례의 판정 기준.
- V0~V4 비교군, iperf3 원시 JSON, CPU 비용·goodput·지연 지표, 반복 측정·통계 계획.
- 계측 한계, `SKIP`·미검증 처리, 로그·종료 코드·체크섬 보존 기준.

이는 실행 계획이며 실제 통과 결과나 측정값이 아니다. 문서의 성능 개선 기준은 팀과 합의할 제안값이고, 현재 성능 개선을 입증한 수치는 없다.

## 6. 작업 보존 상태와 진행 이력

| 시각(KST) | 확인 내용 | 근거 |
| --- | --- | --- |
| 11월 6일 01:08:23 | 현재 저장소 clone | Git reflog |
| 11월 6일 01:09:18 | `test/d-benchmark`로 전환 | Git reflog |
| 11월 6일 01:55:56 | 환경 수집 부분 완료 | 환경 수집 summary |
| 11월 6일 01:56:59~01:57:04 | V0 설정·빌드 준비 성공 | preparation 및 상태 파일 |
| 11월 6일 01:57:39 | TCP 두 파일 컴파일 종료 코드 0 기록 | 종료 코드 파일 수정 시각 |
| 11월 6일 02:03~02:05 | 스크립트·문서·테스트 파일 수정 | 파일 수정 시각 |
| 이후 상태 점검 | 작업 파일·로그 보존, 단위 테스트 17개 통과 확인 | 이번 대화의 점검 결과 |
| 이번 작업 종료 | 장시간 빌드를 시작하지 않고 현재 결과로 마무리 | 사용자 요청 |

파일 수정 시각은 해당 파일의 마지막 저장 시각이며 모든 명령의 실행 시각을 뜻하지 않는다. 과거 연결이 끊긴 원인이나 정확한 중단 순간은 남은 파일만으로 확정하지 못했다.

문서 작성 직전에는 기존 추적 파일의 변경이 없고 새 파일 6개가 Git 미추적 상태였다. 이 문서를 추가하면 작업 산출물은 새 파일 총 7개가 된다. 이 작업으로 새 커밋을 만들거나 push하지 않았다.

`.work/`의 V0 소스·빌드 준비 산출물·환경 로그는 로컬에 보존되어 있고 Git에서는 제외된다. 위의 로컬 로그 링크는 이 VM에서 확인하는 용도이며, 이 문서만 다른 곳에 복사하면 해당 로그가 함께 전달되지는 않는다.

## 7. 미완료 작업과 다음 시작 지점

현재 출력에는 전체 커널 산출물인 `vmlinux`, `System.map`, `arch/arm64/boot/Image`와 전체 빌드의 `validation/build.log`, `validation/build.exitcode`가 없다.

다음 작업을 시작할 때는 기존 V0 worktree와 출력 디렉터리를 재사용하고, HEAD·소스 상태·설정 체크섬·메모리·디스크 여유를 먼저 재확인한다. 작업 순서는 다음과 같다.

1. V0 전체 `Image modules dtbs` 빌드와 로그·종료 코드 기록.
2. 빌드 성공 확인 후 모듈·커널 설치, initramfs·GRUB 확인.
3. UTM에서 복구 가능한 상태를 확보한 뒤 V0 부팅 및 실행 커널·설정 확인.
4. 테스트용 namespace/veth setup·cleanup 도구 구현.
5. 실제 TCP 송수신, 데이터 무결성, 종료·reset·반복 연결 검증.
6. iperf3 준비와 실행 파이프라인 확인, 테스트망에서 손실·지연·재정렬 조건 검사.
7. 구현·정확성 검증을 마친 비교군에 한해 반복 성능 측정과 분석.

현재 VM 사양에서 전체 빌드에 1~3시간 정도가 걸릴 수 있다는 초기 예상만 안내했다. 실제 전체 빌드를 실행하지 않았으므로 측정된 소요 시간은 없고, 설치·부팅·TCP 검증·벤치마크 시간은 별도다. 이번 작업의 완료 범위는 위의 준비·개별 컴파일·단위 테스트·문서화까지다.
