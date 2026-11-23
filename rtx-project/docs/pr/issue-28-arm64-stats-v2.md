# 팀 ARM64 VM에서 STATS v2로 재전송 큐 길이 측정하기

[이슈 #28](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/28)의 목표는 실제 TCP 전송에서 삽입 직후와 ACK 정리 직전의 큐 길이를 측정해 [#24의 K 후보 검토](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/24)에 제공하는 것이다. 이 변경은 #38까지 머지된 production `18060408b1833b61b7f3fa6bfef67e17494ab218`의 빌드·부팅 확인, 같은 전송의 카운터 대조, 조건별 집계를 연결한다. 팀 VM에서 예비 1회와 본 측정 21회가 통과하고 원본과 조건표가 공유되면 #28을 닫을 수 있다.

## 어떤 커널을 쓰는가

| 용도 | 커널·설정 | 확인할 결과 |
| --- | --- | --- |
| v1 파이프라인 예비 실행 | 기존 C23 HYBRID, `--pilot-only` | 무손실 전송에서 `n_hist` 증가 |
| #28 관측 | 같은 main의 V4, K=16, HYBRID/CACHE/TAIL/STATS on, DEBUG off | 삽입 및 ACK 분포, 조회·shift·ACK 카운터 |
| #16 실제 TCP 성능 | 합의한 동일 SHA의 V3·V4, CACHE/TAIL on, DEBUG/STATS off | 수신 바이트당 VM CPU 시간, 처리량, RR 지연 |

기존 C23 fixture는 DEBUG 전용 필드를 읽고 과거 빌드 SHA를 검증한다. 해당 검사를 완화하면 새 이미지의 출처를 증명할 수 없다. D의 기존 native 빌더에 `n-distribution` 모드를 추가해 명시한 Git 커밋을 꺼내고, overlay가 없는 소스에서 빌드한다. 커널 C 코드와 A·B·C의 카운터 API 변경은 없다. 설치한 Image·config와 빌드 사본의 해시, 실행 커널과 vmlinux의 GNU build ID, 실제 설정의 K를 대조한다. 연산용 `observation` fixture와 C23의 빌드·부팅 인터페이스는 각각 별도로 사용한다.

## 빌드와 실행

팀 VM의 저장소 루트에서 새 빌드 디렉터리를 지정한다. 이 VM의 기존 빌드는 소스와 출력만으로 한 벌에 약 4 GiB를 사용했다. 전체 빌드와 모듈·initramfs 설치에 필요한 여유 공간을 확인한다.

```bash
build="$PWD/rtx-project/.work/d28-v4-stats-18060408"
python3 -B rtx-project/scripts/run_arm64_b_benchmark.py build \
  --source-sha 18060408b1833b61b7f3fa6bfef67e17494ab218 \
  --base-config /boot/config-5.4.214-rtx-v0 --directory "$build" \
  --variant hybrid-tail --mode n-distribution --k 16 --jobs 2
```

`evidence/status.json`에서 `status=PASS`와 `stage=built`를 확인한다. 위의 `build` 디렉터리를 사용해 [D의 native 커널 설치 절차](issue-30-d-arm64-performance.md)에 있는 기존 release 충돌 검사, 모듈·Image·initramfs 설치를 따른다. GRUB에서 해당 release로 부팅한 뒤 저장소 루트에서 실행한다.

```bash
uname -r
# 5.4.214-d30-hybrid-tail-n-distribution-k16-18060408
sudo python3 -B rtx-project/scripts/run_arm64_n_distribution.py \
  --build-run "$PWD/rtx-project/.work/d28-v4-stats-18060408/evidence" --repetitions 3
```

무손실 예비 실행이 통과하면 1 MiB 전송으로 손실 0·1% × 추가 RTT 0·20·50 ms의 여섯 조건과 손실 1%·추가 RTT 20 ms·송신 재정렬 25% 한 조건을 각 3회 실행한다. 손실은 양쪽 egress에 각각 설정하고 지연은 양쪽에 절반씩 적용한다. 예비만 실행할 때는 `--pilot-only`를 추가한다.

## D가 B·C에 전달할 파일

- `conditions.csv`에서 `phase=matrix`인 일곱 행과 각 행의 `repetitions_completed=3`을 확인한다. `n_link_le_{4,8,16}_pct`와 `n_ack_le_{4,8,16}_pct`는 각 시점의 이벤트 수를 합한 뒤 계산한 백분율이다. `build_k`는 실제 커널 설정이다. 예를 들어 K=16 한 번의 빌드에서 N≤4 비율도 얻을 수 있고, 이때 LIST 탐색 횟수는 K=16에서 관측한 값이다.
- 각 행에 `find_cover`, TREE·LIST 단계, `sack_block`, `sack_walk_skb`, `sack_skip_reuse`, shift 세 종류와 ACK 세 종류의 증가량이 있다. `steps_per_find_cover`는 TREE·LIST 단계 합계를 조회 수로 나눈 값이다. 조회 0회면 빈 칸이다.
- 각 시도에서 새로 만든 두 netns의 `/proc/net/netstat`를 전송 전후에 저장한다. 양 끝의 `TCPSackShifted`, `TCPSackMerged`, `TCPSackShiftFallback` 증가량을 합해 각각 RTX의 `shift_partial`, `shift_merge`, `shift_fallback` 증가량과 대조한다. 불일치·감소·필수 필드 누락·필터 오류는 실패로 기록한다. 세 증가량이 모두 0이면 그 전송에서 해당 이벤트 관측이 0회였다는 뜻이다.
- `distribution.csv`에는 두 히스토그램의 74개 구간, 시도별 `status.json`에는 반올림 전 비율과 양 끝의 증가량이 있다. 최상위 `status.json`, `build-record.json`, `kernel.config`, `env-*/`, 조건별 RTX·TcpExt 원본과 `SHA256SUMS`를 함께 보존한다. 실패한 시도는 집계에서 제외하며 전체 실행은 실패로 끝난다.

## 이 브랜치에서 확인한 범위

| 확인 항목 | 결과 |
| --- | --- |
| 팀 ARM64, Ubuntu GCC 9.4.0, V4 K=16 | 지정한 main에서 `tcp_output.o`, `tcp_input.o`, `tcp_rtx_stats.o` 컴파일 통과. `kernel.config` SHA-256은 `8ec67e0f532ca1431d54e08f2bafb20c7fb76803d63c45ad2f66bb11de6ddd14` |
| 수집기 시험 | 15개 통과. 두 netns에서 각각 증가 후 합치기, 잘못된 포트·필드 누락·RTX/TcpExt 불일치 거부, 실행 커널 build ID·Image 불일치 거부, 워밍업·실패 제외, 이벤트 가중 집계, 조회 0회 확인 |
| 프로젝트 회귀 | ARM64 VM에서 116개 통과. 샌드박스에서는 소켓 생성이 제한돼 실패했고 같은 소스로 VM에서 재실행 |
| 실제 실행 진입점 음성 대조 | object-only 기록은 FAIL(종료 1), V1 부팅에서 C23 예비 시도는 BLOCKED(종료 2). 둘 다 전송 0회 |
| 새 커널 전체 링크·설치·부팅, 예비·21회 본 측정 | 미실행. VM에 남은 공간은 전체 빌드보다 작고 현재 부팅 release는 `5.4.214-rtx-v1`이다 |

시험은 저장소 루트에서 `python3 -B -m unittest discover -s rtx-project/tests -v`로 재현한다. 위 빌드 명령에 `--object-only`를 추가하면 세 TCP 오브젝트를 확인할 수 있다. 로컬 증거 디렉터리는 `rtx-project/.work/d28-stats-v2-zTQ8pH/main-18060408/`이다. `evidence/`에 빌드 로그·설정·오브젝트 해시, `regression-vm.log`에 전체 시험, `guard-runs.log`에 음성 대조와 원본 폴더, `report.png`에 원본 로그를 표시한 캡처가 있다. 이 로컬 경로는 GitHub 첨부파일이 아니다.

팀 VM에서 본 측정 후 조건표와 원본 공유 링크를 #28·#24에 연결한다. B의 별도 1 vCPU VM 측정은 [#38](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/38)로 머지됐고 [#16](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/16)은 종료됐다. 팀 VM 재현은 열린 [D 이슈 #30](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/30)에 기록한다. #24의 K, 주 네트워크 조건, 소켓 메모리 허용량과 신뢰구간 계산법을 확정한 후 성능용 V3·V4를 교대로 부팅한다.
