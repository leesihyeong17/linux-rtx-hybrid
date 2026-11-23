# ARM64에서 TCP 재전송 큐 길이 분포 수집

[D 이슈 #28](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/28) ·
[성능 비교 #16](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/16) ·
[K 검토 #24](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/24)

팀 VM의 STATS v2 본 측정은 [새 커널 빌드와 7조건 실행 절차](issue-28-arm64-stats-v2.md)를 따른다.
아래의 C23 기록은 v1 무손실 예비 실행에 사용한다.

## 무엇을 세는가

`N`은 한 소켓의 재전송 큐에 skb를 연결한 **직후** 그 큐의 skb 수다. GSO skb 하나는
한 노드로 센다. 커널은 삽입 시점마다 `n_hist`의 16개 구간 중 하나를 증가시킨다
(`1-1`, `2-3`, …, `32768-`). 각 비율의 분모는 해당 실행에서 증가한 전체 삽입 표본 수다.
따라서 이 표로 알 수 있는 것은 “삽입 중 몇 %가 이 구간에서 일어났나”다.
빈 큐에 머문 시간, 소켓별 분포, 개별 세그먼트의 비율은 제공하지 않는다.
K가 구간 중간에 걸리면 히스토그램만으로 정확한 `N <= K` 비율을 계산할 수 없다.

> **STATS v2 (A, [PR 문서](a-stats-v2.md))**: 수집기가 v2 출력도 읽는다. v2 커널에서는
> `n_link`(위 `n_hist`와 같은 삽입 시점)와 `n_ack`(ACK 처리 시점)를 각각 74구간(N 0~64는 1 단위)으로
> 받아 정확한 `N <= K` 비율(`le_share`)을 내고, 측정 동안 포트 5001만 세도록 필터를 건다.
> v1 커널에서의 동작·결과 형식은 아래와 같다(CSV에 `histogram` 열만 추가). 자세한 차이는
> [A_N_HISTOGRAM.md](../A_N_HISTOGRAM.md) 2절.

도구 기준은 main `cb13e743146e8b900ede9b56b29280fe1e3b45ae`다. 변경 파일은
[실행기](../../scripts/run_arm64_n_distribution.py)와
[판정기 시험](../../tests/test_arm64_n_distribution.py), 이 문서다.
기존 `Network`의 임시 namespace/veth·TCP endpoint·길이/SHA-256 검증·정리와
C23 빌드 신원 검증을 재사용한다. 커널 API 변경은 없다.

## 실행 조건과 측정 절차

ARM64 UTM의 `5.4.214-rtx-c23-hybrid`를 사용한다. production은 `e4de76000`,
fixture는 `01acce2b2`, GCC는 9.4.0이며 DEBUG/STATS on, K=16이다. 실제 실행
시 빌드 기록 및 설치 Image/config/System.map을 대조한다. K=16은 관측용 현재값이다.

VM 콘솔에서 다른 TCP 시험을 멈추고 다음을 실행한다.

```bash
cd ~/tcp-rtx/linux-rtx-hybrid
uname -r
sudo python3 -B rtx-project/scripts/run_arm64_n_distribution.py \
  --build-run rtx-project/.work/d-cbuild-resume-20261118-qrqu_pkd --pilot-only
```

다른 VM에서는 그 VM에서 검증한 C23 HYBRID 빌드의 기록 경로를 지정한다.

| 조건 | 손실(양쪽 egress 각각) | 설정한 추가 RTT | 재정렬 |
| --- | --- | --- | --- |
| 예비 1회 | 0% | 0 ms | 없음 |
| 기본 6개 조합 | 0%, 1% | 0, 20, 50 ms | 없음 |
| 추가 1개 | 1% | 20 ms | 송신측 egress 25% 설정 |

추가 RTT의 절반을 각 방향에 설정한다. RTT 0은 추가 지연 0이다. 값은 netem
설정값이며 실제 TCP RTT나 수신측 재정렬 비율의 실측값과 다를 수 있다. 기본은
조건당 3회, 1회당 순방향 1 MiB 전송이다. 본 측정은 7조건 × 3회로 총 21회다.
`--repetitions`와 `--bytes`로 바꿀 수 있다. 이 반복 횟수는 예비 분포 관측용이다.
throughput·CPU 비용의 최종 성능 표본은 별도 설정과 반복이 필요하다.

실행기는 시도마다 새 network namespace 두 개를 생성한다. 양쪽 veth의 offload
설정과 qdisc를 기록하고 검증된 전송 전후에 `/proc/rtx_stats`를 읽는다. 통계는
**VM 전체**를 합한 값이라 SSH나 다른 서비스도 기여할 수 있다. namespace로
격리되는 것은 시험 트래픽 경로다. 전후 호스트의 established TCP 연결을
`commands.log`에 대응하는 로그로 남기며, 짧은 외부 연결이 빠질 수 있다.
부팅 때 fixture가 남긴 값은 전후 차분에서 제외한다. 전역 reset 대신 차분을
선택해 기존 통계를 보존한다. 같은 수집기의 동시 실행은 proc 파일 잠금으로 막는다.

## 결과 판독과 증거

실행할 때마다 새 `rtx-project/.work/d-n-dist-*/`에 다음 파일이 생긴다.

- 최상위 `status.json`에는 커널·워크로드·실행 인자·반복별 결과를,
  `env-*/`에는 `capture_env.sh`로 수집한 CPU·메모리·도구·네트워크 설정과
  개별 수집 성공 여부를 보존한다.
- `pilot-*/`, `matrix-*/`에는 `before.txt`, `after.txt`, endpoint 로그,
  offload·qdisc 기록, `status.json`이 있다. 반복별 JSON에는 구간별 증가량·백분율과
  `sack_block`, `sack_walk_skb`, `find_cover_list_steps`, `shift_*`, `ack_*`을
  포함한 카운터 차이가 들어간다. 0회인 C 카운터도 그대로 남긴다.
- `distribution.csv`는 예비와 본 측정을 구분하고, 같은 조건의 성공한 반복에서
  **구간별 표본 수 합계 / 전체 표본 수 합계**로 백분율을 계산한다.
  전체 `status=PASS`와 조건별 완료 횟수를 함께 확인한다. 실패하면 해당 시도의
  원본과 앞선 결과를 보존하고 이후 측정을 중단한다.
- `SHA256SUMS`는 실행 폴더 안에서 `sha256sum -c SHA256SUMS`로 대조한다.
  로컬 원본에는 주소와 환경 정보가 포함될 수 있다. 공유용은 조건표·분포표·체크섬과
  실제 팀 증거 링크를 연결한다.

## 검증 현황

| 검사 | 결과와 근거 |
| --- | --- |
| 수집기 판정 시험 | 5개 통과. 기존 값 차감, 16개 구간·열 누락/중복, 카운터 감소·빈 표본 거부, RTT/재정렬 생성, 전송 실패 시 원본 보존·정리, 반복 가중 집계 확인. 입력 통계와 네트워크 제어는 모의 자료 |
| ARM64 도구 회귀 | 신규 시험 포함 56개 통과. 원본은 `.work/d-n28-review-8wkggjdr/arm64-unittests.log` |
| V0에서 예비 실행 시도 | `.work/d-n-dist-krt_ol4s/status.json`에 BLOCKED, 종료 코드 2. 부팅 커널이 `5.4.214-rtx-v0`여서 트래픽 생성 전 중단. 저장한 파일의 체크섬 대조 통과 |
| 실제 C23 HYBRID 예비·본 측정 | V0에서 수집 도구를 준비했으며 STATS 커널 부팅 후 실행 대기. 분포값·성능 결론 미제시 |
| 커널 재빌드·checkpatch | 해당 없음. 기존 커널과 사용자 공간 Python 수집기 사용 |

검증 로그의 행 번호와 SHA-256을 표시한 로컬 보고서 캡처는
`.work/d-n28-review-8wkggjdr/report.png`에 있다. 캡처에는 단위 시험과 V0에서
중단된 예비 실행 결과가 담겨 있다.

실제 STATS 실행 후 예비 결과, 조건별 표, 체크섬, 팀 공유 위치를 이 문서에 연결한다.
최종 K 선택 및 DEBUG/STATS off 성능 비교는 #24·#16에서 다룬다.
