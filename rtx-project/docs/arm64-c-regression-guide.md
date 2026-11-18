# ARM64 RB·HYBRID 회귀 테스트 실행 가이드

2026-11-18 KST. 대상은 C가 지정한 고정 소스의 ARM64 정확성 검증이다.
실제 실행 결과와 원본 증거는 [대표 검증 문서](pr/issue-15-d-arm64-c-regression.md)에 있다.
이 가이드는 이미 빌드·설치된 D의 UTM VM에서 시험을 다시 실행하는 순서다.
새 팀원 VM의 준비 조건은 8절을 따른다.

## 1. 무엇을 검사하는가

| 단계 | 실행·수집 대상 | 확인할 내용 |
| --- | --- | --- |
| 부팅 fixture | 부팅 때 커널 안에서 실행된 BVERIFY·CSTATE | split·ACK/SACK·힌트·회계 경계, RB/HYBRID 상태 비교 |
| 실제 TCP 시험 | 격리된 namespace/veth에서 양방향 전송 | 손실 확률 0%·1%, 데이터 길이·SHA-256, half-close·reset |
| 패킷 시험 | TUN 상대가 구성하는 다섯 시나리오 | SACK·부분 ACK·DSACK·DSACK_OFO·SACK reneging 반응 |

`collect_c_arm64.py`는 이미 부팅 때 실행된 fixture 로그를 수집한다. fixture를 새로
실행하려면 해당 커널을 다시 부팅한다. TCP·패킷 시험은 명령을 실행할 때마다 새로 실행된다.

| 고정 입력 | 값 |
| --- | --- |
| Production | `e4de76000e09497907562f587efb9a5e914da7d1` |
| Harness | `01acce2b2c39288a76b928add03fb36f567d53c6`의 `rtx-project/tests/integration/` |
| 공통 환경 | native ARM64, GCC 9.4.0, DEBUG/STATS on, KASAN off |
| RB release | `5.4.214-rtx-c23-rb` |
| HYBRID release | `5.4.214-rtx-c23-hybrid`, K=16 |

이번 RB/HYBRID는 #18에 적힌 과거 V0·V1과 구분한다. N 분포·성능 측정 절차도 별도다.

## 2. 시작 전 확인

명령은 **Mac 터미널이 아닌 VM에 연결한 VS Code 터미널**에서 실행한다.
커널 선택은 **Mac의 UTM VM 화면**에서 한다. 다른 검증과 동시에 네트워크 시험을 실행하지 않는다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
git status --short --branch
git rev-parse HEAD
uname -m
uname -r
gcc -dumpfullversion
python3 --version
command -v ip tc tcpdump ethtool gcc
df -h / /boot
```

- `uname -m`은 `aarch64`, GCC는 이번 기준에서 `9.4.0`이어야 한다.
- 이 가이드와 `collect_c_arm64.py`가 들어 있는 D 브랜치/PR을 사용한다.
  저장소 HEAD는 시험 도구의 버전이며, 부팅한 커널의 production SHA와 별도 기록한다.
- TCP 시험에는 `ip`, `tc`, `tcpdump`, offload 정보 수집용 `ethtool`이 필요하고,
  패킷 상대는 VM의 `gcc`로 빌드한다. 커널은 NET_NS·VETH·NET_SCH_NETEM·TUN을 지원해야 한다.
- 수집기·도구만 다시 검사하려면 아래 단위 테스트를 실행한다. 정상 결과는 14개 PASS이며,
  합성 입력을 사용하는 이 검사는 실제 부팅 fixture 결과를 대체하지 않는다.

```bash
python3 -B -m unittest discover -s rtx-project/tests -p test_collect_c_arm64.py -v
```

D VM의 완료된 빌드 기록은 다음 경로다. `status.json`과 그 안에 기록된 source/build
디렉터리를 함께 보존해야 한다. `status=PASS`이며 RB/HYBRID 두 빌드가 모두 PASS인지 확인한다.

```bash
RTX_C_BUILD_RUN="$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd"
python3 -m json.tool "$RTX_C_BUILD_RUN/status.json"
```

빌드 기록 안의 `boot: NOT RUN`은 빌드 종료 당시 상태다. 부팅 결과는 이후의
`c-boot-*/status.json`에서 별도로 판정한다. D VM에는 두 커널이 이미 설치돼 있으므로
같은 release를 재설치하거나 기존 결과 디렉터리를 지울 필요가 없다.

## 3. RB 커널 부팅과 fixture 수집

이미 `uname -r`이 `5.4.214-rtx-c23-rb`이면 부팅 절차를 건너뛴다.
그 외에는 UTM VM 화면을 열어둔 뒤 실행한다. VS Code 연결은 잠시 끊긴다.

```bash
sudo reboot
```

UTM 부팅 메뉴에서 **Advanced options for Ubuntu → Ubuntu, with Linux
5.4.214-rtx-c23-rb**를 선택한다. `recovery mode`가 없는 일반 항목이다.
접속이 복구되면 다음을 실행한다. 재부팅 후 셸 변수는 다시 설정한다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
uname -r
RTX_C_BUILD_RUN="$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd"
if [ "$(uname -r)" = "5.4.214-rtx-c23-rb" ]; then
    sudo -v && python3 -B rtx-project/scripts/collect_c_arm64.py \
        --build-run "$RTX_C_BUILD_RUN" --mode rb
else
    echo 'RB 커널로 부팅한 뒤 다시 진행하세요.'
fi
```

출력된 `c-boot-rb-…` 전체 경로를 기록한다. 그 폴더의 `status.json`이 PASS이면
4절로 진행한다. HYBRID에서 비교할 때도 **이 RB 수집 경로**를 사용한다.
RB 수집의 `state_comparison: NOT RUN`은 정상이다. 두 커널 비교는 HYBRID 수집에서 수행한다.

## 4. 현재 커널의 실제 TCP·패킷 시험

이 절을 RB와 HYBRID에서 각각 한 번 실행한다. 먼저 `uname -r`로 이번 대상인지 확인한다.
`--smoke-current-kernel`은 현재 커널을 시험하며 RB/HYBRID를 자동 전환하지 않는다.
과거 V0/V1용 `--variant` 또는 `--build-output` 옵션을 섞지 않는다.

```bash
uname -r
sudo python3 -B rtx-project/scripts/run_arm64_tcp.py --smoke-current-kernel
echo "exit_code=$?"
```

출력된 `d-tcp-…` 경로를 기록한다. 종료 코드가 0이고 `summary.json`의 `status`가
PASS인지 확인한 뒤 다음 명령을 실행한다. 출력된 경로만으로 성공을 판단하지 않는다.

```bash
sudo python3 -B rtx-project/scripts/run_arm64_packets.py --smoke-current-kernel
echo "exit_code=$?"
```

`d-packets-…` 경로와 종료 코드를 기록하고 6절의 기준으로 판정한다.
각 시험은 고유 namespace를 만들고 종료 시 자기 자원을 정리한다.
결과 폴더는 실행마다 새로 생성되며 실패한 결과도 보존한다.

## 5. HYBRID 부팅·RB 상태 비교

RB 수집과 두 네트워크 시험을 마쳤으면 UTM 화면을 열고 `sudo reboot`를 실행한다.
**Advanced options for Ubuntu → Ubuntu, with Linux 5.4.214-rtx-c23-hybrid**의
일반 항목으로 부팅한다. 접속 복구 후 아래 변수의 RB 경로를 3절에서 기록한 값으로 바꾼다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
uname -r
RTX_C_BUILD_RUN="$PWD/rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd"
RTX_RB_FIXTURE="/3절에서/출력된/c-boot-rb-실제경로"
if [ "$(uname -r)" = "5.4.214-rtx-c23-hybrid" ]; then
    sudo -v && python3 -B rtx-project/scripts/collect_c_arm64.py \
        --build-run "$RTX_C_BUILD_RUN" --mode hybrid --reference "$RTX_RB_FIXTURE"
else
    echo 'HYBRID 커널로 부팅한 뒤 다시 진행하세요.'
fi
```

이 폴더의 `status.json`에서 **`status: PASS`와 `state_comparison: PASS`를 모두**
확인한다. `--reference` 없이 수집하면 수집 자체가 PASS여도 RB 비교는 완료되지 않는다.
이후 **4절의 TCP·패킷 시험을 HYBRID에서도 실행**하고 결과 경로 두 개를 기록한다.

이미 보존된 D VM의 RB 기준을 재사용하는 경우는
`$PWD/rtx-project/.work/c-boot-rb-ulypwl6d`다. 이를 사용했다면 신규 RB 시험으로 세지 말고
과거 참조 로그 재사용이라고 기록한다. 다른 빌드의 RB 로그나 최신 폴더라는 이유만으로 고른
로그를 섞으면 안 된다.

## 6. 결과 파일을 읽고 PASS 판정하기

아래 변수에는 방금 실행한 실제 출력 경로를 넣는다. 한 커널의 결과끼리 확인한 뒤
다른 커널에서도 같은 방식으로 읽는다.

```bash
RTX_FIXTURE_RUN="/실제/c-boot-rb-또는-hybrid-결과경로"
RTX_TCP_RUN="/실제/d-tcp-결과경로"
RTX_PACKET_RUN="/실제/d-packets-결과경로"
python3 -m json.tool "$RTX_FIXTURE_RUN/status.json"
python3 -m json.tool "$RTX_TCP_RUN/summary.json"
python3 -m json.tool "$RTX_PACKET_RUN/summary.json"
```

| 결과 파일 | 통과 조건 |
| --- | --- |
| 공통 | `running_kernel`이 이번 대상 release와 일치하고 `status=PASS` |
| Fixture | BVERIFY 전체 사례·Core 검사 실패 0, 로그 누락·중복·진단 없음 |
| HYBRID 상태 비교 | `state_comparison=PASS`; `state-comparison.json`의 `shape=16`, `steps=139`, `diffs=[]` |
| TCP | `cases` 16개 모두 PASS; 전송 12개 길이·SHA-256 일치, reset 4개 성공 |
| 패킷 | `packet_cases`의 SACK·PARTIAL·DSACK·DSACK_OFO·RENEGING 모두 PASS |
| 패킷 카운터 | `counter_deltas`의 TCPDSACKRecv·TCPDSACKOfoRecv·TCPSACKReneging 각각 1 |
| 두 네트워크 시험 | `kernel_errors=[]`, `cleanup_errors=[]` |

이번 고정 환경의 fixture 기준값은 RB 5,151 checks/Core 556, HYBRID 3,716 checks/Core 749다.
RB는 k16 139단계와 k1 128단계, HYBRID는 k16 139단계를 기록한다.
RB k1 사례는 HYBRID K=1 실행 증거가 아니다.

TCP 시험의 손실 1%는 netem에 설정한 확률이다. 짧은 전송에서 매번 손실·SACK·재전송이
생기는 것은 아니므로 SACK 협상이나 전송 성공만으로 내부 SACK 경로 실행을 주장하지 않는다.
패킷 시험의 PARTIAL도 단독으로 GSO skb 내부 trim을 입증하지 않는다.
각 summary의 `partial_skb_trim`, `dsack_walk`, `performance` 미검증 표시는 그대로 남긴다.

## 7. 실패·중단 시 확인할 것

| 현상 | 다음 확인 |
| --- | --- |
| `sudo`의 `Sorry, try again` | VM 사용자 비밀번호로 다시 인증한다. GitHub 토큰을 넣는 곳이 아니다. |
| `dmesg capture failed` | 같은 터미널에서 `sudo -v` 후 수집기를 다시 실행한다. 이전 FAIL 폴더는 보존한다. |
| `boot the selected ARM64 fixture kernel first` | `uname -r`과 UTM 부팅 항목을 확인한다. |
| 설치 이미지/config mismatch | 수집한 build-run과 설치 release가 같은 빌드인지 확인한다. hash나 status를 수동으로 맞춰 통과시키지 않는다. |
| reference·CSTATE 불일치 | RB/HYBRID 두 원본 dmesg, 고정 SHA·설정, `state-comparison.json`을 함께 확인한다. |
| 결과가 `SKIP`, 종료 코드 2 | 커널 로그 수집·연속성 등 관측이 불충분하다. PASS로 보고하지 않는다. |
| 패킷/TCP `FAIL` 또는 시간 초과 | `error`, `peer.log`, 송수신 JSON, `command-*.log`, `kernel-new.log`를 보존하고 원인을 확인한다. |
| `cleanup_errors`가 비어 있지 않음 | `owned-netns.json`과 현재 `ip netns list`를 대조한다. 다른 작업의 namespace를 일괄 삭제하지 않는다. |

시험의 종료 코드 0도 결과 JSON과 함께 확인한다. `RUNNING`에서 끝났거나 결과 파일이
없으면 완료로 처리하지 않는다. 진단을 위해 `dmesg`를 지우거나 실패 로그를 삭제하지 않는다.

## 8. 다른 팀원 VM에서 실행할 때

Git clone/pull만으로 D의 `.work`와 `/boot` 파일이 생기지는 않는다. 이 가이드의 D 경로를
그대로 복사하기 전에 새 환경에 아래 준비가 필요하다.

1. [대표 문서 4절](pr/issue-15-d-arm64-c-regression.md)의 고정 production/harness 조합을
   새 worktree에 준비하고 overlay를 적용한다. Python 3.8은 문서의 실행용 호환 사본을 사용한다.
2. VM의 부팅 장치를 지원하는 ARM64 설정을 출발점으로 RB/HYBRID 설정을 준비한다.
   DEBUG/STATS on·KASAN off·HYBRID K=16·서로 다른 LOCALVERSION을 확인하고 config 차이를 보존한다.
   다른 config를 쓰면 D의 이미지/hash와 동일하다고 기록하지 않는다.
3. native GCC 9.4.0으로 각 `Image modules dtbs`를 빌드하고 명령·종료 코드·config·산출물 hash를 남긴다.
   구체적인 기존 빌드·설치 검증 계약은 대표 문서 8절에 있다.
4. `collect_c_arm64.py`에는 **실제 성공한 빌드의 `status.json`과 그 파일이 가리키는 source/build**,
   `source`의 형제 디렉터리 `evidence/cases.json`·`harness-hashes.json`이 필요하다.
   `build_pair.py`·`install_kernel.py`는 현재 D의 `.work`에 있는 로컬 실행 도구이며 이 PR에 포함되지 않는다.
   D에게 해당 도구·입력·manifest 공유를 요청하거나, 같은 계약으로 새 빌드 증거를 생성해야 한다.
   JSON의 경로·PASS를 임의로 채워 넣은 것은 빌드 증거가 아니다.
5. 기존 정상 커널을 보존하며 새 이미지·모듈·initramfs를 설치하고 UTM에서 각각 부팅한다.
   새 VM에 맞는 실제 build-run 경로를 사용해 3–6절을 실행한다.

새 환경에서의 빌드·설치는 이 가이드 작성 때 다시 수행하지 않았다. 현재 즉시 재실행할 수 있는
대상은 준비가 끝난 D VM이며, 다른 VM에서 완전히 자동으로 재현하는 설치 도구 제공은 후속 범위다.

## 9. 팀에 전달할 결과

한 실행 묶음마다 아래 항목을 남긴다.

- 날짜·VM/CPU/메모리·GCC·Python 버전, production/harness/시험 도구 SHA.
- RB/HYBRID kernelrelease·config hash, DEBUG/STATS·KASAN·K 설정.
- 두 fixture 경로, 두 TCP 경로, 두 패킷 경로와 각각의 PASS/FAIL/SKIP.
- HYBRID `state-comparison.json`, 전체 dmesg·peer.log·송수신 JSON·명령·종료 코드·pcap.
- 원본의 SHA-256과 실제 로그 화면 캡처. 보고서 캡처라면 원본 경로·행 번호·hash도 표시한다.
- 로컬 보존인지 팀 공유 위치에 업로드됐는지, 남은 미검증 범위.

fixture 폴더에는 수집기가 `SHA256SUMS`를 만든다. TCP·패킷 폴더 전체 manifest는 자동
생성되지 않으므로 공유할 때 별도 증거 디렉터리에 파일별 hash를 기록한다.
원본 로그·커널 이미지·대형 캡처는 `.work`에 보존하고 Git에는 요약·재현 문서를 올린다.
GitHub에서는 `.work` 상대 링크가 열리지 않는다. 팀 공유 링크가 생기면 대표 문서에 연결한다.

이 시험 묶음의 PASS는 해당 고정 소스·설정·시나리오의 정확성 증거다.
#15 전체 완료, #18 V0/V1 완료, #24 N 분포 수집, 하이브리드 성능 우위를 대신 선언하지 않는다.
