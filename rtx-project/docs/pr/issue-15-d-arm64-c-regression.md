# #15 D: PR #23 고정 소스의 ARM64 빌드·부팅·TCP 회귀 검증

2026-11-17 KST. 관련 작업은 [C PR #23](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/23),
[#15 공동 정확성 회귀](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/15)다.
[#18 D ARM64 검증](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/18)의 관련 후속 결과지만,
이번 RB/HYBRID 결과가 #18의 기존 V0·V1 검증을 대체하지는 않는다. 두 이슈의 자동 종료는 요청하지 않는다.
D의 이번 변경은 소스 조합·overlay·설정, 전체 빌드, native 부팅 수집기와 실제 TCP 회귀 증거다.
별도 D PR은 아직 없다.

후속 원격 반영과 ARM64 객체 컴파일 결과는 7절에 기록했다.

2026-11-18 전체 빌드·산출물 검사와 native 부팅 인계는 8절, 사용자 실행 후 RB 설치·부팅·
실제 BVERIFY/CSTATE 검증 PASS는 9절을 따른다. 최신 결과는 10절이다. RB/HYBRID 모두
부팅 fixture·TCP 16개·패킷 5개 시험이 통과했고, k16 CSTATE 139단계가 일치했다.
이는 고정 소스·설정의 해당 정확성 시험 결과이며 #24 N 분포나 성능 측정 결과가 아니다.

## 1. 문제와 범위

C fixture는 #21의 실제 ACK/SACK 카운터 증가 지점을 읽는다. 따라서 예전 D의
고정 V1이나 PR #23 브랜치 단독으로 준비하면 C가 지정한 시험 조건이 아니다.
C가 전달한 다음 두 입력을 고정한다. 움직이는 `main`/PR 브랜치 HEAD로 대체하지 않는다.

| 입력 | 전체 SHA / 경로 |
| --- | --- |
| Production | `e4de76000e09497907562f587efb9a5e914da7d1` — #20·#21이 포함된 main |
| Harness | `01acce2b2c39288a76b928add03fb36f567d53c6`의 `rtx-project/tests/integration/`만 사용 |
| 이전 D V1 | `231db9ed6a204d6407d88954da60ce7db1c2dabd` — 별도 과거 검증 기록, 이번 소스로 사용하지 않음 |

C는 이 조합이 자신의 main 기준 4차 실행 트리와 같다고 전달했다. 이번 D 작업에서는
지정 SHA와 파일 조합을 확인했으며, C의 원본 4차 실행 산출물과 비교한 것은 아니다.
C가 예고한 후속 `c_stress.c`·문서 커밋은 이번 고정 integration 시험에 포함하지 않는다.

최초 준비 완료 조건은 고정 소스 조합, 원본 harness 보존, overlay 적용, 비교기 단위 테스트,
ARM64 RB/HYBRID 설정 및 증거 보존이다. 커널 전체 빌드·설치·부팅·fixture 실행·
성능 측정은 최초 준비 완료에 포함하지 않았다. 후속 승인 범위는 RB/HYBRID 전체 빌드,
로그·산출물 hash 보존, 설치·부팅 준비 상태 확인이었다. 이후 사용자가 RB를 설치·부팅하고
fixture 수집을 실행했으며 그 결과를 9절에서 검증했다. 이어 HYBRID 수집·RB 상태 비교와
두 커널의 실제 TCP·패킷 시험을 실행했으며 10절에 원본 증거와 판정 범위를 기록했다.
production/Core API 변경은 없다.

## 2. 준비한 결과와 설정

작업 루트는 `rtx-project/.work/d-c23-20261117-nf8ghq43/`다.

| 경로 | 내용 |
| --- | --- |
| `source/` | Production HEAD의 detached worktree에 지정 harness와 시험 overlay 적용 |
| `build-rb/` | ARM64, HYBRID off, DEBUG/STATS on |
| `build-hybrid/` | ARM64, HYBRID on, K=16, DEBUG/STATS on |
| `evidence/` | 두 SHA, harness·생성 파일 hash, 패치, 명령·오류·테스트 로그, 설정 비교 |

두 구성은 같은 소스와 기존 D ARM64 `build-v1/final.config`를 설정 출발점으로 쓴다.
과거 V1의 설정 파일 재사용은 과거 V1 소스로 빌드한다는 뜻이 아니다.
`olddefconfig` 후 ARM64·NET_NS·VETH·NET_SCH_NETEM·DEBUG/STATS가 켜졌는지 검사했다.

| 구성 | kernelrelease | 최종 config SHA-256 |
| --- | --- | --- |
| RB | `5.4.214-rtx-c23-rb` | `350dfb30d1c956fea4c52688aa994586ead6dc24cc9ba140125e9903fcb4a957` |
| HYBRID K=16 | `5.4.214-rtx-c23-hybrid` | `955901c01b5d6a1ebeef2cec260253514975f96bce54a90999f84dd55fd32554` |

이번 두 설정은 KASAN off다. KASAN on의 빌드·부팅 가능 여부는 아직 검증하지 않았다.
추가 검증 시 새 출력 경로와 config hash를 사용하고 적용 여부를 별도 기록한다.

## 3. Python 3.8 호환 문제와 처리

실제 VM은 aarch64, GCC 9.4.0, Python 3.8.10이다. 원본 `apply_overlay.py` 실행은
`str.removeprefix()`가 없어 실패했다. 실패 당시 복사된 fixture 파일과 오류 로그를 보존했고,
`tcp_input.c`·`tcp_output.c`는 아직 production 원본과 같음을 확인했다.

원본 integration 디렉터리는 수정하지 않았다. 실행용 사본
`evidence/apply_overlay_python38.py`에서 다음 두 호출만 바꿨다.

```python
case.stem.removeprefix('case_')  # -> case.stem[len('case_'):]
p.stem.removeprefix('case_')     # -> p.stem[len('case_'):]
```

두 호출 모두 `case_*.c`에서 얻은 이름이며 접두사 조건을 검사했다. 이 입력 범위에서
이름 처리 의미는 같다. 실행용 사본·원본과의 diff·SHA-256을 보존한다. fixture C 코드와
판정 기준은 바꾸지 않는다. Python 3.9 이상 환경에서는 원본 스크립트를 직접 사용할 수 있다.

대안은 새 Python 설치였으나, 이번 준비에서는 시스템 도구를 교체하지 않고 두 문자열
호출의 호환 사본을 사용했다. C에게 전달하는 재현 정보에도 이 실행 도구 차이를 포함한다.

## 4. 재현과 이어서 진행할 절차

### 새 소스 조합 준비

아래는 새 디렉터리에 준비하는 예다. 이미 준비한 `source/`에 overlay를 다시 적용하지 않는다.
사용자가 받은 예시의 잘린 경로는 `rtx-project/tests/integration/`이다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
git fetch --no-tags origin main c/sack-ack-regression
RTX_C_RUN=$(mktemp -d "$PWD/rtx-project/.work/d-c23-XXXXXXXX")
export RTX_C_RUN
git worktree add --detach "$RTX_C_RUN/source" e4de76000e09497907562f587efb9a5e914da7d1
git -C "$RTX_C_RUN/source" restore \
  --source=01acce2b2c39288a76b928add03fb36f567d53c6 --worktree \
  -- rtx-project/tests/integration/
mkdir "$RTX_C_RUN/evidence"
```

Python 3.8에서 원본 harness를 보존하며 overlay를 실행하는 방법이다.

```bash
python3 -B - <<'PY'
import difflib
import os
from pathlib import Path
import subprocess
import sys

run = Path(os.environ['RTX_C_RUN'])
source, evidence = run / 'source', run / 'evidence'
harness = source / 'rtx-project/tests/integration'
original = (harness / 'apply_overlay.py').read_text()
needle = ".removeprefix('case_')"
assert original.count(needle) == 2
assert all(p.stem.startswith('case_') for p in harness.glob('case_*.c'))
modified = original.replace(needle, "[len('case_'):]")
script = evidence / 'apply_overlay_python38.py'
script.write_text(modified)
(evidence / 'python38-compat.patch').write_text(''.join(difflib.unified_diff(
    original.splitlines(True), modified.splitlines(True),
    fromfile='pinned/apply_overlay.py', tofile='execution/apply_overlay_python38.py')))
subprocess.run([sys.executable, '-B', str(script), str(source),
                str(harness), str(evidence)], check=True)
PY
python3 -B -m unittest discover \
  -s "$RTX_C_RUN/source/rtx-project/tests/integration" -p 'test_*.py' -v
```

Production SHA와 harness SHA는 별도 항목으로 저장하고, 원본 harness의 파일별 hash,
실행용 호환 스크립트, 생성된 `b_verify_*` 파일, overlay patch도 함께 보존한다.
이번 실제 준비에서 수행한 추가 무결성 검사·명령은 `evidence/prepare_overlay.py`와
`commands.log`에 있다.

### 준비된 ARM64 설정 사용

현재 결과를 이어서 사용할 때는 다음 경로다.

```bash
RTX_C_RUN="$PWD/rtx-project/.work/d-c23-20261117-nf8ghq43"
```

설정 준비의 실제 명령은 `evidence/prepare_configs.py`·`commands.log`에 보존했다.
같은 seed를 복사하고 `scripts/config`로 DEBUG/STATS on, HYBRID off/on 및 K=16,
각 LOCALVERSION을 설정한 뒤 `ARCH=arm64 CC=gcc`의 `olddefconfig`를 실행했다.
새로 준비할 때도 두 구성의 seed와 공통 옵션을 동일하게 적용한다.

**기존 `prepare_v1_arm64.sh`와 `build_arm64.py --variant v1`은 사용하지 않는다.**
그 도구는 예전 V1 SHA·clean source·DEBUG/STATS off를 검사한다. 검사를 풀거나
과거 pin을 덮어쓰지 않고 이번 source와 각 `O=` 경로에 직접 Kbuild를 실행한다.
빌드 대상은 `Image modules dtbs`, 도구는 native GCC 9.4.0으로 고정한다.
빌드 명령·종료 코드·이미지/config hash를 남긴 후
[기존 설치·부팅 절차](../testing.md)를 참고하되
이번 source/output/release로 치환한다. 최초 준비 당시에는 빌드·설치·부팅을 실행하지 않았으며,
후속 전체 빌드 결과와 현재 인계 명령은 8절을 따른다.

### 부팅 후 수집과 비교

부팅한 release·설정·설치 이미지가 해당 빌드와 연결되는지 확인한 뒤 전체 dmesg와
`BVERIFY_CASE`·`BVERIFY_TOTAL`·`CSTATE` 발췌본을 각각 저장한다. 예를 들어 HYBRID는:

```bash
sudo dmesg --color=never > "$RTX_C_RUN/evidence/dmesg-hybrid.log"
rg 'BVERIFY_CASE|BVERIFY_TOTAL|CSTATE' "$RTX_C_RUN/evidence/dmesg-hybrid.log" \
  > "$RTX_C_RUN/evidence/fixture-hybrid.log"
```

RB도 별도 부팅 후 `dmesg-rb.log`·`fixture-rb.log`로 저장한다. K=16의 상태 비교는:

```bash
python3 -B "$RTX_C_RUN/source/rtx-project/tests/integration/c_diff_states.py" \
  "$RTX_C_RUN/evidence/dmesg-hybrid.log" \
  "$RTX_C_RUN/evidence/dmesg-rb.log" 16
```

종료 코드와 JSON을 보존한다. 상태 일치만으로 회귀 통과를 선언하지 않는다.
`cases.json`의 모든 사례가 한 번씩 존재하고 checks가 양수이며 failed=0인지,
TOTAL의 합계가 일치하는지, 새 WARNING/BUG/Oops/panic이 없는지도 전체 로그로 확인한다.
로그 누락은 통과로 취급하지 않는다. 현재 `check_results.py`는 QEMU guest의
`RTX_RUNTIME PASS`까지 요구하므로 native 부팅 dmesg만 넣어 바로 사용할 수 없다.
그 요구를 삭제하거나 성공 문자열을 인위적으로 추가하지 않는다.

## 5. 최초 준비 시점의 검증과 증거

| 검증 | 결과 |
| --- | --- |
| 두 고정 SHA·production #21 증가 지점 | 확인 |
| 원본 harness 20개 파일과 Git blob 비교 | PASS, 적용 후에도 동일 |
| 원본 overlay / Python 3.8 | FAIL, removeprefix 미지원. 로그 보존 |
| 실행용 호환 사본으로 overlay 적용 | PASS, 10개 fixture 파일 포함 |
| 상태 비교기 5개·로그 판정기 2개 단위 테스트 | 7/7 PASS, 누락·차이·실패 거부 포함 |
| RB/HYBRID ARM64 olddefconfig·kernelrelease | PASS, 최종 config와 diff 보존 |
| 커널 전체 빌드·설치·부팅·fixture 실행 | NOT RUN |
| 실제 ARM64 RB/HYBRID CSTATE 비교·KASAN | NOT RUN |
| #24 N/C 카운터 수집·최종 성능 측정 | NOT RUN |

증거는 로컬에만 있으며 공유 저장소에 업로드하지 않았다.

- [준비 메타데이터](../../.work/d-c23-20261117-nf8ghq43/evidence/preparation.json)
- [harness 원본 hash](../../.work/d-c23-20261117-nf8ghq43/evidence/harness-hashes.json)
- [Python 3.8 원본 실행 실패](../../.work/d-c23-20261117-nf8ghq43/evidence/overlay-python38-original.log)
- [호환 사본 diff](../../.work/d-c23-20261117-nf8ghq43/evidence/python38-compat.patch)
- [단위 테스트 원본 로그](../../.work/d-c23-20261117-nf8ghq43/evidence/integration-unit-tests.log)
- [최종 설정 식별자](../../.work/d-c23-20261117-nf8ghq43/evidence/configs.json)
- [RB/HYBRID 설정 차이](../../.work/d-c23-20261117-nf8ghq43/evidence/rb-hybrid.diffconfig)
- [명령 기록](../../.work/d-c23-20261117-nf8ghq43/evidence/commands.log)
- [준비 증거 체크섬](../../.work/d-c23-20261117-nf8ghq43/evidence/SHA256SUMS)

실제 커널 부팅을 하지 않아 커널 콘솔 캡처는 없다. 이번 준비 증거는 위 원본 로그·
명령·hash로 제공하며 실행하지 않은 BVERIFY/CSTATE 결과나 스크린샷은 만들지 않았다.

## 6. 남은 작업과 다른 측정과의 구분

1. KASAN 구성의 별도 빌드·부팅 가능 여부와 HYBRID K=1 검증. RB의 k1 사례는 HYBRID K=1을 대체하지 않는다.
2. 실제 패킷 시험 중 내부 GSO trim·DSACK walker 관찰은 별도 계측이 필요하다.
3. C에게 두 SHA·실행 도구 호환 patch·config·전체 로그·상태 비교 결과 전달 및 공유 증거 업로드.
4. #24 N/C 카운터는 fixture overlay 없는 별도 STATS on 소스로 수집한다.
   이번 overlay 커널의 boot fixture 카운터를 workload 관찰값으로 사용하지 않는다.
5. 최종 성능은 회귀를 통과한 통합본의 DEBUG/STATS off 구성으로 별도 수행한다.

현재 D 브랜치의 역사적 V0/V1 pin·검증 문서는 유지했다. 이번 소스 worktree는
후속 빌드 입력으로 보존하며 commit·push·PR 생성은 수행하지 않았다.

## 7. 2026-11-17 원격 후속 변경 반영과 ARM64 컴파일 확인

### 원격 상태와 반영 범위

`git fetch --no-tags origin` 후 확인한 원격 상태다. PR 번호는 각 브랜치의 문서를
따르며, main 포함 여부는 해당 커밋의 ancestry로 확인했다.

| 대상 | 확인한 HEAD | main 포함·준비 결과 |
| --- | --- | --- |
| main | `e4de76000e09497907562f587efb9a5e914da7d1` | 현재 D 작업 브랜치를 `167a86828`에서 이 SHA까지 fast-forward |
| C / PR #23 | `52fda65d64115908e641dd0cb9e753356e1b8278` | main 미포함. `01acce2b2` 이후 integration 디렉터리 변경 없음 |
| B / PR #25 | `4e204ffda288734f722674bb696d40efbb78e9d6` | main 미포함. CACHE·TAIL 및 최소 벤치마크 구현이 원격 브랜치에 존재 |

기존 D 문서 변경은 먼저 복사·diff로 보존한 뒤 main을 반영했으며 충돌은 없었다.
과거 V0/V1 및 C 검증용 worktree도 유지했다. PR #23·#25 브랜치 전체를 임의로
main과 합친 새 성능 기준은 만들지 않았다.

C 후속 두 커밋은 스트레스 수신기의 무트래픽 종료 한도를 120초에서 300초로 늘리고
종료 시 `/proc/net/tcp` 진단을 남기는 수정과 문서다. 현재 준비한 integration
20개 파일은 원래 pin과 그대로 일치하므로 기존 fixture 소스·config를 재사용했다.
최신 `c_stress.c`는 별도 증거 디렉터리에 정확한 사본과 diff를 보존하고 컴파일했다.
이 파일은 guest init 용도이며 이번 VM에서 실행하지 않았다.

이전 협의 때 미구현이었던 V2/V3에 대응하는 CACHE·TAIL 선택지가 이제 #25에 있다.
최종 성능 비교에서는 해당 변경과 C 카운터를 포함한 통합 SHA의 회귀 결과를 확인한 후
RB cache/tail과 HYBRID의 공통 설정을 맞춰야 한다. main에 구현이 통합된 상태나
D에서 성능 우위를 확인한 것으로 표시하지 않는다.

### 이번에 실행한 검증

증거 루트는 `rtx-project/.work/d-remote-20261117-kgzl6hn6/evidence/`다.

| 검증 | 결과와 범위 |
| --- | --- |
| C integration pin 대비 최신 브랜치 diff | 변경 없음 |
| 원래 준비 증거 SHA256SUMS | 그대로 일치 |
| RB / ARM64 gcc 9.4.0 | tcp_input·tcp_output·tcp_rtx_stats·tcp_rtx_selftest의 객체 4개 컴파일 PASS |
| HYBRID K=16 / ARM64 gcc 9.4.0 | 같은 객체 4개 컴파일 PASS |
| 위 커널 컴파일 진단 | 두 구성 모두 warning/error 0, 최종 config hash 불변 |
| 최신 C 스트레스 수신기 | `-O2 -Wall -Wextra -Werror -pthread -c` 객체 컴파일 PASS, 실행 안 함 |
| 산출물 아키텍처 | 객체 9개 모두 ELF Machine AArch64 확인 |
| 전체 커널 링크·설치·부팅·BVERIFY/CSTATE 실행 | NOT RUN |

커널 컴파일은 두 구성을 순차 실행했고 기존 ARM64 build lock을 사용했다.
명령에는 `ARCH=arm64`, `CROSS_COMPILE=`, `CC=gcc`, `-j2`와 네 객체 경로를 명시했다.
`Image modules dtbs` 빌드나 실제 TCP 시험의 통과로 해석하지 않는다.

- [원격 SHA](../../.work/d-remote-20261117-kgzl6hn6/evidence/remote-refs.json)
- [main 포함 여부·integration 비교](../../.work/d-remote-20261117-kgzl6hn6/evidence/remote-review.json)
- [컴파일 상태·명령·객체 hash](../../.work/d-remote-20261117-kgzl6hn6/evidence/arm64-objects.json)
- [RB 원본 로그](../../.work/d-remote-20261117-kgzl6hn6/evidence/rb-objects.log)
- [HYBRID 원본 로그](../../.work/d-remote-20261117-kgzl6hn6/evidence/hybrid-objects.log)
- [컴파일 진단](../../.work/d-remote-20261117-kgzl6hn6/evidence/compiler-diagnostics.json)
- [C 수신기 컴파일 명령](../../.work/d-remote-20261117-kgzl6hn6/evidence/c-stress-command.log)
- [원본 행 번호·hash가 있는 로그 보고서](../../.work/d-remote-20261117-kgzl6hn6/evidence/report.html)
- [실제 로그 보고서 화면 캡처](../../.work/d-remote-20261117-kgzl6hn6/evidence/report.png)
- [후속 검증 체크섬](../../.work/d-remote-20261117-kgzl6hn6/evidence/SHA256SUMS)

캡처는 위 로컬 HTML을 Firefox로 실제 렌더링한 화면이며 커널 콘솔 자체가 아니다.
첫 제한 환경 실행은 40초 시간 초과였고, 별도 작업용 profile로 제한 밖에서 재시도해
성공했다. 임시 서버는 사용하지 않았고 캡처 프로세스는 종료했다. 준비·컴파일 증거는
모두 로컬 보존 상태이며 공유 저장소에는 업로드하지 않았다.

### 이제 가능한 D 작업

1. 준비한 RB/HYBRID의 전체 ARM64 `Image modules dtbs` 빌드.
2. 각 빌드의 이미지·config hash를 기록하고 설치·부팅한 뒤 BVERIFY/CSTATE 수집과 비교.
3. 별도 STATS on, fixture overlay 없는 구성에서 #24의 N·C 카운터 예비 관찰.
4. #25를 포함한 최종 비교 소스·공통 설정·정확성 결과를 고정한 뒤 성능 실험.

이번 확인 시 여유 공간은 약 13 GiB이고 기존 V0/V1 전체 빌드 출력은 각각 약 3.0 GiB다.
앞으로의 빌드 크기를 보장하는 값은 아니므로 전체 빌드 시 여유 공간을 다시 기록한다.
이번 작업에서 새 커밋·push·PR 생성·커널 설치·재부팅은 하지 않았다.

## 8. 2026-11-18 전체 빌드와 native 부팅 수집

작업 브랜치는 `test/d-arm64-c-regression`이다. 원격을 다시 fetch한 결과 main·C·B의
HEAD는 7절과 같았고, 고정 소스와 준비된 RB/HYBRID 구성을 유지했다.

첫 실행 루트는 `rtx-project/.work/d-cbuild-20261118-ulmig88j/`다.
재개 시 해당 `status.json`은 RUNNING이었지만 호스트의 빌드 프로세스가 없고 공유 lock도
해제되어 있었다. RB 종료 코드가 없어 완료로 취급하지 않았다. 원인은 확정하지 않는다.
이전 로그·상태·실행 스크립트는 그대로 보존했다.

재개 실행 루트는 `rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd/`다.
기존 출력 객체를 재사용하되 새로운 명령·로그·상태를 남긴다. 원격 main, C PR #23,
비교군 PR #25의 SHA는 첫 실행과 같고, 고정 integration과 현재 C HEAD의 diff도 없다.
준비 증거 `SHA256SUMS`의 77개 항목이 재검증에 통과했다.

`build_pair.py`가 공유 ARM64 build lock 아래에서 RB 다음 HYBRID를 순차 빌드한다.
각 make 명령은 `ARCH=arm64`, GCC 9.4.0, `LOCALVERSION=`, `-j2 Image modules dtbs`다.
기존 V0/V1의 artifact 검사 함수를 재사용하고, 소스 patch·harness·생성 fixture·config를
빌드 전후 검사한다. 모든 `.ko` 존재, DTB 수, Image/vmlinux/System.map hash, release와
AArch64 ELF를 확인해야 빌드 PASS로 기록한다. 재개 실행에서는 모든 `.ko`·DTB와
`modules.builtin`·`modules.builtin.modinfo`의 파일별 hash도 별도 manifest에 보존한다.
KASAN은 두 구성 모두 off다.

전체 빌드 종료 시점 상태는 [status.json](../../.work/d-cbuild-resume-20261118-qrqu_pkd/status.json)에 있다.
그 파일의 설치·부팅 NOT RUN은 빌드 당시 상태이며 이후 RB 실행 증거는 9절에 별도로 보존한다.
전체 빌드가 완료되기 전에는 설치하지 않는다. `build_pair.py`·각 구성의 `command.txt`,
`build.log`·`build.exitcode`는 실제 실행 증거이며 과거 준비 로그를 덮어쓰지 않는다.

| 구성 | 전체 빌드·산출물 검사 | 모듈 / DTB | Image SHA-256 |
| --- | --- | --- | --- |
| RB | PASS, make exit 0 | 5968 / 264 | `ddb29913904a77155f04deb989f3c1def3b8f67cc42ba6da5cca210a66433bcb` |
| HYBRID K=16 | PASS, make exit 0 | 5968 / 264 | `d7871dde77861603a782642086a4f5881a7af11719ac22f9a96ee264450b9e8b` |

두 구성의 산출물은 새 설치 도우미의 manifest·전체 파일 hash 검사에도 통과했다.
이 결과는 빌드 결과이며 설치·부팅·실제 fixture 통과를 뜻하지 않는다.

두 빌드 모두 AArch64 ELF와 kernelrelease를 확인했고 최종 config hash는 2절과 같다.
RB 경고는 첫 실행 2건·재개 실행 4건, HYBRID는 6건이다. 경로를 정규화해 과거 V1
로그와 대조한 결과 전부 동일한 스택 크기 경고였으며 새 진단과 컴파일 오류는 0건이다.
경고 원문은 삭제하거나 무시하지 않고 [진단 목록](../../.work/d-cbuild-resume-20261118-qrqu_pkd/compiler-diagnostics.json)에 보존했다.

| 검증 수준 | 최종 결과 |
| --- | --- |
| ARM64 `Image modules dtbs` 전체 타겟 빌드·링크 | RB/HYBRID PASS |
| 소스·harness·config 불변, 산출물·모듈·DTB hash | 두 구성 PASS |
| native 수집기 / 고정 comparator 단위 검사 | 14 / 5 PASS, 합성 로그 |
| 설치 도우미 검증 / 실제 기존 모듈 modinfo 조회 | 22 PASS / PASS |
| 실제 RB 설치·부팅·fixture | 후속 사용자 실행 PASS, 9절 |
| HYBRID 부팅·RB/HYBRID 상태 비교 | 빌드 당시 NOT RUN → 후속 사용자 실행 PASS, 10절 |
| 실제 TCP·패킷 시험 | 빌드 당시 NOT RUN → 두 커널 후속 사용자 실행 PASS, 10절 |
| KASAN·DEBUG off 구성·성능 측정 | NOT RUN |

전체 재컴파일 대신 소스·설정 불변을 확인한 기존 객체를 재사용하고 전체 타겟을 완료했다.
중단 전 로그는 보존하고 새 실행 로그를 분리해, 종료 코드가 없는 과거 실행을 PASS로
바꾸지 않았다. 산출물 검사는 기존 helper를 재사용하면서 모듈·DTB까지 파일별 hash를
확장했다. 추가 읽기·manifest 저장 비용이 있지만 설치 입력을 완료된 빌드에 연결할 수 있다.
이 hash 검사 비용을 RTX 성능 결과로 해석하지 않는다.

변경은 D의 수집기·테스트·문서와 로컬 실행 도우미에 한정된다. production 커널 C와
Core/B/C API는 변경하지 않았으므로 이번 변경에 대한 커널 C checkpatch는 해당 없음이다.
DEBUG/STATS on의 고정 fixture 빌드만 확인했으며 다른 설정의 runtime 정확성을 주장하지 않는다.

### native 수집기와 검증

기존 `check_results.py`의 QEMU 성공 조건을 약화하지 않고 별도
[`collect_c_arm64.py`](../../scripts/collect_c_arm64.py)를 추가했다.

- 성공한 두 빌드의 고정 production/harness SHA, 현재 aarch64 release와 설치 이미지·config·System.map hash를 확인한다.
- 실제 dmesg와 boot_id를 새 실행 디렉터리에 보존한다. dmesg를 지우지 않는다.
- 모든 BVERIFY 사례·TOTAL·Core 자체 테스트가 존재하고 실패 0인지 확인한다. 누락·중복·잘못된 합계·커널 진단은 거부한다.
- HYBRID는 k16 139단계, RB는 k16 139단계·k1 128단계의 CSTATE가 정확한 순서로 있어야 한다. 누락·중복·순서 변경·잘린 payload와 CFAIL·RTX_RUNTIME FAIL/TIMEOUT도 거부한다.
- HYBRID 수집 시 `--reference`로 이전 RB 수집 경로를 주면, 같은 빌드 기준인지와 로그 hash를 확인하고 원본 C 비교기로 k16 상태를 대조한다.
- 수집 성공과 RB 비교 성공을 별도 필드로 기록한다. 실제 부팅 전에는 실행 결과를 만들지 않는다.

초기 수집기 테스트 10개는 첫 실행에 보존했다. 재개 중 CSTATE 일부만 남아도 통과하고,
runtime FAIL/TIMEOUT을 놓치는 판정 문제를 발견했다. 고정 harness의 상태 단계·출력 형식을
검사하고 RB 참조 로그도 원본 비교기 호출 전에 다시 검증하도록 수정했다.
수정 후 수집기 단위 테스트 14개와 원본 comparator 테스트 5개가 통과했다.
합성 로그를 이용한 단위 검사이며 커널 실행 결과가 아니다.
잘못된 커널·소스 pin·설치 이미지, 누락/실패 로그, 다른 RB 구성·변조된 reference 로그를
거부하는 음성 대조를 포함한다. 수정 전 ACCEPT → 수정 후 REJECT인 네 입력을
[negative-controls.json](../../.work/d-collector-review-20261118-gkubltn_/negative-controls.json)에 보존했다.
최종 테스트 출력은 [collector-tests.log](../../.work/d-collector-audit-20261118-w64rt01p/collector-tests.log)에 있다.

목록을 사용하는 합성 테스트만으로 정확성을 주장하지 않도록 고정 C 소스의 실제 wrapper와
`c_check`·`snprintf` 위치를 추출한 독립 C 추적으로 순서를 대조했다. k16/k1 및 IPv6·wraparound
순서와 출력 형식이 일치했다. [감사 결과](../../.work/d-collector-audit-20261118-w64rt01p/audit-results.json)는
정상 경로의 소스 제어 흐름 검증이며 커널 fixture 실행은 아니다. 단계 목록은 고정 harness SHA에
종속된다. 향후 pin을 바꾸면 목록과 소스 감사를 함께 갱신해야 한다.

### 설치·부팅 인계

빌드 인계 당시 에이전트의 `sudo -n true`는 `a password is required`로 실패했다. 자동 승인 검토의
거절이 아니라 VM의 sudo 인증이 필요한 상태다. 비밀번호는 채팅이나 로그에 넣지 않는다.
[재개 중 인증 확인 결과](../../.work/d-cbuild-resume-20261118-qrqu_pkd/sudo-readiness.json)도 같다.
아래는 최초 RB 설치 재현 명령이다. 현재 RB 설치는 완료됐으므로 이후 진행은 9절을 따른다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
RTX_C_BUILD_RUN="$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd"
python3 -B "$RTX_C_BUILD_RUN/install_kernel.py" rb
```

설치 도우미는 두 빌드 PASS와 소스·산출물 hash를 먼저 확인하고 터미널에서 `sudo -v`를
실행한다. modules_install·install·initramfs·GRUB 처리와 설치 파일 hash를 확인해 새 로그에
남긴다. 기존 같은 release의 설치 흔적이 있으면 덮어쓰지 않고 중단한다. 도우미는 재부팅하지 않는다.

재개 시 도우미도 보완했다. 기존 검사는 `.ko` 존재만 확인했고 설치된 initramfs는 비어 있지
않으면 통과했다. 이제 추가 manifest의 전체 모듈·DTB·builtin 목록과 hash를 설치 전에 확인한다.
설치 후에는 모듈 목록·의존성 메타데이터와 각 모듈의 vermagic release를 확인한다.
설치 시 strip/sign으로 바이트가 바뀔 수 있으므로 설치된 `.ko`를 원본 hash와 직접 비교하지 않는다.
이 VM은 LVM root와 모듈 형태의 virtio block driver를 쓰므로 initramfs에 해당 release의
`virtio_blk.ko`, LVM 활성화 스크립트·실행파일·모듈 의존성이 있어야 통과한다.

읽기 전용 [부팅 준비 조사](../../.work/d-cbuild-resume-20261118-qrqu_pkd/boot-readiness.json)에서는
두 대상 release의 설치 흔적이 없고 `/boot` 여유가 약 1.27 GiB임을 확인했다.
기존 `5.4.214-kimseungbeom+`의 initramfs에는 필요한 root 접근 구성요소가 있다.
GRUB은 DEFAULT=0·timeout=10초이며 기존 정상 커널의 복구 항목도 남아 있다.
`mokutil --sb-state`는 미지원 문구와 exit 255를 반환했으며 Secure Boot 검증 성공으로 기록하지 않는다.
이 조사와 도우미 단위 검사는 새 커널 설치·부팅 성공을 대신하지 않는다.
설치 도우미의 합성 검증 22개가 통과했고, 현재 정상 커널 모듈 2개를 사용한 실제 batched
`modinfo -F vermagic` 조회도 통과했다. 원본 출력은
[tests-final.log](../../.work/d-cbuild-resume-20261118-qrqu_pkd/installer-validation/tests-final.log),
[modinfo-smoke.log](../../.work/d-cbuild-resume-20261118-qrqu_pkd/installer-validation/modinfo-smoke.log)에 있다.

UTM 콘솔에서 기존 커널로 복구할 수 있는 상태를 확인한 뒤 재부팅하고 GRUB에서
`5.4.214-rtx-c23-rb`를 선택한다. 접속이 복구되면 같은 저장소에서:

```bash
sudo -v
python3 -B rtx-project/scripts/collect_c_arm64.py \
  --build-run "$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd" --mode rb
```

출력된 `c-boot-rb-*` 경로를 기록한다. RB 수집이 PASS이면 HYBRID를 설치한다.

```bash
python3 -B "$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd/install_kernel.py" hybrid
```

같은 방식으로 `5.4.214-rtx-c23-hybrid`를 선택해 부팅하고 아래의 RB 경로를 실제 출력값으로 바꾼다.

```bash
sudo -v
python3 -B rtx-project/scripts/collect_c_arm64.py \
  --build-run "$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd" --mode hybrid \
  --reference /실제/c-boot-rb-실행경로
```

설치·부팅·fixture 검증·RB 상태 비교는 각각 실제 결과가 확보된 단계만 완료로 표시한다.

### 최종 증거와 작업 상태

아래 증거는 모두 로컬에만 보존했으며 PR이나 공유 저장소에 업로드하지 않았다.
산출물 본체는 2절의 `build-rb/`·`build-hybrid/`에 있고 새 실행의 status/manifest가 해당 경로를 가리킨다.

- [재개 경위와 원격 기준](../../.work/d-cbuild-resume-20261118-qrqu_pkd/resume.json), [원격 원본 SHA](../../.work/d-cbuild-resume-20261118-qrqu_pkd/remote-refs.txt)
- [RB 명령](../../.work/d-cbuild-resume-20261118-qrqu_pkd/rb/command.txt), [RB 원본 로그](../../.work/d-cbuild-resume-20261118-qrqu_pkd/rb/build.log), [RB 모듈·DTB hash](../../.work/d-cbuild-resume-20261118-qrqu_pkd/rb/modules-dtbs-sha256.json)
- [HYBRID 명령](../../.work/d-cbuild-resume-20261118-qrqu_pkd/hybrid/command.txt), [HYBRID 원본 로그](../../.work/d-cbuild-resume-20261118-qrqu_pkd/hybrid/build.log), [HYBRID 모듈·DTB hash](../../.work/d-cbuild-resume-20261118-qrqu_pkd/hybrid/modules-dtbs-sha256.json)
- [완료된 두 빌드의 설치 전 무결성 재검증](../../.work/d-cbuild-resume-20261118-qrqu_pkd/preinstall-artifacts.json)
- [원본 행 번호·hash를 표시한 보고서](../../.work/d-cbuild-resume-20261118-qrqu_pkd/report.html), [실제 Firefox 화면 캡처](../../.work/d-cbuild-resume-20261118-qrqu_pkd/report.png)
- [증거 묶음 체크섬](../../.work/d-cbuild-resume-20261118-qrqu_pkd/SHA256SUMS), [최종 검사 기록](../../.work/d-cbuild-resume-20261118-qrqu_pkd/final-verification.json)

캡처는 실제 원본 로그 보고서를 렌더링한 것으로 커널 콘솔 캡처가 아니다.
보고서 안의 단위 검사·음성 대조는 합성 입력임을 표시했다. PNG SHA-256은
`2755dd7c9c082176568b68cdb9dae0cc8796771b7f33fc5d041e9bf2649d78e4`다.
별도 브라우저 프로필로 캡처했고 임시 서버는 사용하지 않았다.

빌드 재개 단계에서는 커밋·push·PR 생성, 설치·재부팅을 하지 않았다. 이후 사용자 RB 실행은
9절에 기록한다. 기존 사용자 변경과 새
수집기·테스트·대표 문서는 미커밋 상태로 남긴다. 빌드 프로세스는 종료됐고 공유 build lock은
해제됐다. Docker 자원은 생성하지 않았다. 후속 실제 부팅 결과는 9·10절, 현재 남은 작업은 6절을 따른다.

## 9. 2026-11-18 RB 실제 설치·부팅·fixture 검증

사용자가 VM 터미널에서 RB를 설치하고 GRUB 일반 항목 `5.4.214-rtx-c23-rb`로 부팅한 뒤
native 수집기를 실행했다. 저장된 결과를 에이전트가 읽기 전용으로 재검증했다.
동일 부팅의 정상 수집본이 있어 다시 sudo를 호출하거나 dmesg를 새로 수집하지 않았다.
이번 후속 변경은 이 대표 문서와 새 로컬 증거에 한정하며 커널·수집기 코드는 바꾸지 않았다.

| 단계 | 실제 결과·증거 |
| --- | --- |
| RB 설치 | `install-rb-t8k_z7_3/status.json` PASS; 5,968개 모듈·vermagic·initramfs·GRUB 확인 |
| 설치 소요 | 13:00:32–13:01:26 KST, 약 54초. 이 실행의 실측이며 일반 보장값은 아님 |
| 현재 커널·아키텍처 | `5.4.214-rtx-c23-rb`, aarch64 |
| 설치 Image/config/System.map | 완료된 RB 빌드의 hash와 일치 |
| 수집본·현재 부팅 동일성 | boot_id 일치, 원본 dmesg hash와 manifest 9개 파일 일치 |
| BVERIFY 10개 사례 | 5,151 checks, failed=0; 원본 dmesg 799행 TOTAL |
| Core 자체 검사 | 556 passed, 0 failed; 803행 |
| CSTATE | k16 139단계·k1 128단계, 각각 IPv6 33단계; 순서·형식 일치 |
| 수집 로그의 실패·커널 진단 | 수집기 실패 표식과 WARNING/BUG/Oops/panic/KASAN/UBSAN 없음 |
| HYBRID 부팅·RB/HYBRID 비교·성능 | NOT RUN |

RB 수집 경로는 `rtx-project/.work/c-boot-rb-ulypwl6d/`이며 수집 완료 시각은
13:11:09 KST다. 이후 로그나 장시간 workload까지 검증한 것으로 해석하지 않는다.
원본 dmesg SHA-256은 `6e3ed403eedf70d5f9bda61a3ab0bd13e1a51ba860d091382f7d786619c5ecd3`다.

최종 PASS만 확인하지 않고 원본 450–454·494–498행의 split 실패·재시도,
775–776행의 unclone/allocation 복구, 786행의 SACK 두 번째 분할 실패 후 retry 표식도 확인했다.
이들은 fixture가 의도한 실패 경로의 관찰이며 테스트 실패를 의미하지 않는다.
778행의 `hybrid checks=1`은 HYBRID off의 `B_EXPECT(true)` 분기다. HYBRID 승격 경로의
실행 증거로 사용하지 않는다. 수집본의 `state_comparison: NOT RUN`도 아직 비교 상대를
부팅하지 않았기 때문에 정상이다.

- [사용자 RB 설치 결과](../../.work/d-cbuild-resume-20261118-qrqu_pkd/install-rb-t8k_z7_3/status.json)
- [RB 수집 결과](../../.work/c-boot-rb-ulypwl6d/status.json), [전체 dmesg](../../.work/c-boot-rb-ulypwl6d/dmesg.log), [수집본 체크섬](../../.work/c-boot-rb-ulypwl6d/SHA256SUMS)
- [독립 재검증 결과](../../.work/d-rb-boot-review-20261118-hjblfnp3/verification.json), [원본 행 번호·hash 발췌](../../.work/d-rb-boot-review-20261118-hjblfnp3/raw-excerpts.txt)
- [로그 보고서](../../.work/d-rb-boot-review-20261118-hjblfnp3/report.html), [실제 보고서 화면 캡처](../../.work/d-rb-boot-review-20261118-hjblfnp3/report.png)
- [후속 증거 체크섬](../../.work/d-rb-boot-review-20261118-hjblfnp3/SHA256SUMS)

보고서 캡처는 원본 로그·행 번호·hash를 표시한 실제 렌더링이며 커널 콘솔 캡처가 아니다.
증거는 로컬에만 있으며 이전 빌드·수집 결과를 덮어쓰지 않았다. 임시 서버와 Docker는
사용하지 않는다. 이번 RB 검증에서 예기치 않은 실패는 없었다.

아래는 RB 검증 당시 안내한 HYBRID 설치·수집 절차다. 이후 실행 결과는 10절에 있으며,
이미 설치한 같은 release를 재설치하는 명령으로 사용하지 않는다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
python3 -B rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd/install_kernel.py hybrid
```

설치 PASS 후 재부팅하고 GRUB의 일반 `5.4.214-rtx-c23-hybrid` 항목을 선택한다.
`uname -r`로 확인한 뒤 아래 명령으로 실제 RB 수집본과 비교한다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
sudo -v
python3 -B rtx-project/scripts/collect_c_arm64.py \
  --build-run "$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd" \
  --mode hybrid --reference "$PWD/rtx-project/.work/c-boot-rb-ulypwl6d"
```

HYBRID 수집의 `status: PASS`와 `state_comparison: PASS`를 모두 확인해야 이번 두 구성의
native fixture 회귀·상태 비교가 완료된다. 커밋·push·PR 생성과 성능 측정은 별도다.

## 10. 2026-11-18 양쪽 커널의 실제 TCP·패킷 회귀 완료

사용자가 UTM VM 터미널에서 각 커널을 선택해 부팅하고 기존 native 시험을 실행했다.
에이전트는 저장된 원본 dmesg·송수신 JSON·패킷 로그·설정/이미지 hash를 읽어 재검증했다.
이번 후속 변경은 이 문서, testing.md의 안내와 새 로컬 증거다. 커널·시험 판정 코드는 바꾸지 않았다.

### 결과와 판정 범위

환경과 입력은 1·2절의 ARM64 UTM, native GCC 9.4.0, production `e4de76000` +
harness `01acce2b2`다. 두 커널 모두 DEBUG/STATS on·KASAN off·정확성 overlay 적용 상태다.
HYBRID 설치 결과 `install-hybrid-1looczvz/status.json`도 PASS이며 수집 당시 설치
Image/config/System.map은 완료된 빌드와 일치했다. 현재 보존된 파일의 hash도 재확인했다.

| 검사 | RB | HYBRID K=16 |
| --- | --- | --- |
| 실제 부팅 release | `5.4.214-rtx-c23-rb` | `5.4.214-rtx-c23-hybrid` |
| BVERIFY 10개 사례 | 5,151 checks, 실패 0 | 3,716 checks, 실패 0 |
| Core 자체 검사 | 556 passed, 실패 0 | 749 passed, 실패 0 |
| CSTATE | k16 139단계·k1 128단계 | k16 139단계, RB와 차이 0 |
| 실제 TCP 전송·half-close·reset | 16/16 PASS | 16/16 PASS |
| SACK·PARTIAL·DSACK·DSACK_OFO·RENEGING | 5/5 PASS | 5/5 PASS |
| DSACKRecv / DSACKOfoRecv / SACKReneging 증가량 | 1 / 1 / 1 | 1 / 1 / 1 |
| SackShifted / SackMerged / SackShiftFallback 증가량 | 1 / 0 / 6 | 1 / 0 / 6 |
| 시험 중 저장된 커널 진단·정리 오류 | 없음 | 없음 |

TCP 16개는 netem 송신 방향별 손실 확률 0%·1%, 정방향·역방향 각각에 대해
0·4,097·1,048,576 bytes 전송 3개와 reset 1개를 실행한 것이다. 전송 12개는 송신·수신·
응답 JSON의 길이와 SHA-256을 실제 payload 패턴의 기대 hash까지 대조했고 모두 일치했다.
reset 4개는 `reset_observed=true`를 확인했다. 1%는 설정한 확률이므로 짧은 실행마다
실제 손실·SACK·재전송이 반드시 생긴다는 뜻은 아니다. RB TCP 캡처에는 SACK 블록이
관찰되지 않았으며, 별도 결정적 패킷 시험에서 SACK·재전송 반응을 확인했다.

패킷 시험의 RENEGING에서는 두 커널 모두 먼저 도착한 offset 0 재전송을 기록하고 건너뛴 뒤
대상 1,000 bytes 재전송, `sacked=0`, `TCPSACKReneging` 증가량 1을 확인했다.
PARTIAL은 TLP를 끄고 2,000 bytes ACK 후 `unacked=2`를 확인했다. 이 패킷 관찰만으로
하나의 GSO skb가 실제 `tcp_trim_head()`를 거쳤다고 주장하지 않는다. DSACK_OFO 역시
미ACK 데이터와 DSACK MIB 관찰이며 내부 walker 관찰 완료를 뜻하지 않는다.

CSTATE 139단계 일치는 고정 fixture 로그의 비교다. 실제 패킷 로그의 모든 중간 상태가
동일한 것은 아니다. 예를 들어 DSACK_OFO 직후 lost/retrans는 선택한 HYBRID 로그에서
1/1, RB 로그에서 0/0이다. 원인은 이번 재검증에서 확정하지 않았고, 두 로그 모두 기존
패킷 시나리오 판정을 통과했다. MIB 값 일치도 성능이나 전체 실행 경로 동일성의 근거로 쓰지 않는다.

### 재현 명령과 원본 증거

각 커널로 부팅하고 `uname -r`로 대상 release를 확인한 뒤 실행한 명령이다.
`--smoke-current-kernel`은 현재 부팅한 커널을 시험하며 RB/HYBRID를 자동 전환하지 않는다.

```bash
cd /home/seungbeom/tcp-rtx/linux-rtx-hybrid
uname -r
sudo python3 -B rtx-project/scripts/run_arm64_tcp.py --smoke-current-kernel
sudo python3 -B rtx-project/scripts/run_arm64_packets.py --smoke-current-kernel
```

| 증거 | RB | HYBRID |
| --- | --- | --- |
| 부팅 수집 | [c-boot-rb-ulypwl6d](../../.work/c-boot-rb-ulypwl6d/status.json) | [c-boot-hybrid-u6w8wcgn](../../.work/c-boot-hybrid-u6w8wcgn/status.json) |
| 실제 TCP | [d-tcp-0ctflars](../../.work/d-tcp-0ctflars/summary.json) | [d-tcp-0g339wk4](../../.work/d-tcp-0g339wk4/summary.json) |
| 패킷 반응 | [d-packets-kuqgq5rf](../../.work/d-packets-kuqgq5rf/summary.json) | [d-packets-lv594gvo](../../.work/d-packets-lv594gvo/summary.json) |

HYBRID의 `d-packets-_yetwkzf`, `d-tcp-mbvwno5y`, `d-packets-6mx0xi57` 추가 PASS 실행도
그대로 보존했다. 마지막 두 실행은 RB로 전환하기 전 HYBRID에서 반복한 결과이므로 RB 증거에
포함하지 않았다. 위 표의 RB 두 실행은 `running_kernel`이 실제 RB임을 확인했다.

새 재검증 증거 루트는 `rtx-project/.work/d-c23-runtime-review-20261118-QSXP2UjL/`다.

- [읽기 전용 재검증 결과](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/verification.json), [실행 출력](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/verification.log)
- [원본 증거 파일별 SHA-256](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/source-evidence-sha256.json)
- [원본 경로·행 번호·hash 발췌](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/raw-excerpts.txt)
- [실제 로그 보고서](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/report.html), [보고서 화면 캡처](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/report.png)
- [재검증 증거 체크섬](../../.work/d-c23-runtime-review-20261118-QSXP2UjL/SHA256SUMS)

보고서 캡처는 원본 로그를 표시한 로컬 HTML의 실제 렌더링이며 커널 콘솔 자체가 아니다.
Firefox는 PNG 생성 후 종료 대기에서 25초 제한에 걸려 작업 프로세스 그룹을 종료했다.
생성된 PNG를 직접 열어 표·원본 행 번호·hash가 표시되는 것을 확인했으며 실행 로그와
종료 코드는 `screenshot.log`·`screenshot-status.json`에 보존했다. 임시 서버는 사용하지 않았다.
모든 원본과 캡처는 로컬에만 있다. PR 첨부나 팀 공유 위치 업로드는 아직 수행하지 않았다.

### 실패·수정과 남은 범위

HYBRID 첫 수집 `c-boot-hybrid-cw9yjs8v`는 dmesg 접근 인증 실패로 FAIL이었다. 원본 오류를
보존했고 이후 `c-boot-hybrid-u6w8wcgn`에서 수집·fixture·RB 상태 비교가 모두 PASS였다.
이것은 커널 fixture 실패와 구분한다. 새 보고서 생성의 첫 시도도 스크립트 자체의 상대 경로를
절대 경로 기준으로 변환하다 실패했다. 경로를 resolve하도록 고치고 재검증에 통과했으며
첫 실행 코드·오류는 같은 증거 디렉터리에 남겼다. 커널이나 시험 기대값을 완화한 수정은 없다.

KASAN·HYBRID K=1·패킷 중 내부 trim/walker 계측, #24 N/C 카운터 workload,
재정렬·장시간 부하와 최종 성능 측정은 이번 완료 범위에 포함하지 않는다. N 관찰은
fixture overlay 없는 STATS on 구성, 성능은 합의된 통합 SHA의 DEBUG/STATS off 구성으로
별도 진행한다. 이번 결과만으로 #15 전체를 닫거나 하이브리드의 성능 우위를 선언하지 않는다.

재검증 시 선택한 네 네트워크 시험이 만든 netns는 모두 제거된 상태였다. 새 커널/네트워크
시험·재부팅·Docker 자원 생성은 없었고, 커밋·push·PR 생성은 하지 않았다.
