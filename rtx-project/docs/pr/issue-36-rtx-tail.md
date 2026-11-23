# #36 TAIL 단독 설정과 ARM64 벤치마크 스택

[PR #37](https://github.com/leesihyeong17/linux-rtx-hybrid/pull/37)

[이슈 #36](https://github.com/leesihyeong17/linux-rtx-hybrid/issues/36)은 #25 병합 후 발견한 설정 문제, 트리 삽입 전제, ARM64 컴파일 경고를 다룬다. 기준은 `main`의 `f78f5626a`, 커널과 벤치마크 수정본은 `0df8dcd98`이다.

## 구현

- `net/ipv4/Kconfig`에서 TAIL이 CACHE를 선택한다. 기존에는 RB 백엔드에서 CACHE를 끄고 TAIL만 요청하면 `olddefconfig`가 TAIL을 제거했다. Kconfig에서 해결하면 빌드 스크립트를 쓰지 않아도 TAIL이 필요한 캐시를 얻는다. CACHE만 켜는 비교군도 유지된다. [기존 빌드 기록](../../evidence/A/1123_merge25/buildmatrix_ref.txt)
- `include/net/tcp_rtx.h`의 `__rtx_tree_append()` 위에 두 호출 경로를 기록했다. RB의 `tcp_event_new_data_sent()`는 새로 송신한 skb를 `rtx_append()`에 넘긴다. 하이브리드의 `__rtx_limit()`은 트리 뒤의 시퀀스 구간에 속하는 recent 리스트에서 첫 skb를 승격한다. 두 경로 모두 기존 트리의 마지막 skb 다음에 붙일 수 있다.
- `rtx-project/tests/benchmark/benchmark.c`의 `bm_valid()`는 RTX와 송신 시각 큐를 한 노드씩 함께 순회한다. 각 skb의 링크 주소와 양방향 링크를 대조하고, 마지막에는 시각 큐의 헤드에 도달했는지 검사한다. 기존 128개 상한과 시퀀스·회계·캐시 검사는 유지한다. 포인터 배열이나 동적 할당이 필요 없다.
- ARM64 실행기는 빌드와 수집에 고정된 벤치마크 커밋을 사용한다. 기본값을 수정 커밋으로 옮겼고, 이전 기록은 해당 기록의 `harness_sha`와 해시로 검증한다. 과거 커널과 벤치마크 조합을 재현하는 옵션은 [ARM64 실행 안내](issue-30-d-arm64-performance.md)에 있다.

## 검증

Ubuntu 20.04 ARM64 컨테이너에서 GCC `9.4.0-1ubuntu1~20.04.2`를 사용했다. `arm64 defconfig`의 프레임 경고 기준을 1,024바이트로 지정했다.

| 검사 | 결과 |
| --- | --- |
| `olddefconfig` 뒤의 CACHE/TAIL/HYBRID | RB 기본 `n/n/n`, CACHE 단독 `y/n/n`, TAIL 단독 `y/y/n`, HYBRID 단독 `y/n/y`, HYBRID+TAIL `y/y/y` |
| 수정 전 `bm_valid()` | GCC 프레임 경고 1,040 B. 같은 빌드의 `-fstack-usage` 출력은 1,136 B |
| 수정 후 `bm_valid()` | performance/observation 각각 DEBUG off/on으로 `tcp_output.o` 빌드. 네 경우 모두 `-Werror=frame-larger-than=1024` 통과, `-fstack-usage` 출력 80 B |
| 검증 함수의 경계 | 실제 함수 본문을 사용한 호스트 fixture에서 빈 큐·단일 노드·128개는 허용했다. 129개, 시각 큐의 누락·초과·순서 오류·역방향 링크 오류, 잘못된 회계와 시퀀스 간격은 거부했다. RB 기본과 HYBRID+CACHE 설정에서 각각 11개 검사 통과 |

D의 실행기에서 같은 오브젝트 빌드를 재현하려면 ARM64 호스트에서 NET_SCHED가 켜진 팀 기준 설정을 사용한다. `--directory`에는 새 출력 경로를 지정한다.

```sh
python3 -B rtx-project/scripts/run_arm64_b_benchmark.py build \
  --directory "$PWD/rtx-project/.work/issue36-rb-tail" \
  --base-config /boot/config-5.4.214-rtx-v0 \
  --variant rb-tail --mode observation --object-only
```

이 명령으로 `rb-tail`의 observation 오브젝트를 빌드했다. 별도로 DEBUG를 켠 HYBRID+TAIL(K=16) 커널 이미지를 QEMU ARM64에서 부팅했다. 워밍업 없이 케이스별 1회씩 실행해 append, split, 예상한 할당 실패까지 10개가 모두 통과했다. 같은 Linux 환경에서 프로젝트 테스트 111개와 벤치마크 수집기·overlay 테스트 9개가 통과했다. 이 실행에서는 실제 TCP 성능을 측정하지 않았다.

## 검증 중 조정한 환경

첫 QEMU 실행은 기본 가상 NIC의 부팅 ROM이 없어 커널 시작 전에 종료됐다. 벤치마크는 부팅 중 커널 소켓을 사용하므로 기본 장치를 제외하고 직렬 콘솔만 연결해 다시 실행했다. ARM64 실행기의 첫 오브젝트 빌드는 기본 설정에 NET_SCHED가 꺼져 NET_SCH_NETEM을 유지하지 못했다. 실행기가 설정 불일치를 거부하는 것을 확인한 뒤 seed에서 NET_SCHED를 켜고 재실행했다. macOS에서 Linux의 `/proc`와 도구를 요구한 테스트, 일부 소스만 복사한 환경에서 파일을 찾지 못한 overlay 테스트는 ARM64 Linux의 전체 체크아웃에서 다시 실행했다.

## 증거

[컴파일러·설정·부팅 로그 발췌](../../evidence/B/issue-36-arm64.txt)에 원본 파일별 SHA-256과 줄 번호가 있다. 전체 로그, 설정, 검증 스크립트, 부팅 이미지와 로그 화면 캡처는 로컬 `rtx-project/.work/issue36-DdN2M5kB/`에 보존했다. `result.png`는 원본 로그를 읽어 만든 화면이며, 직렬 콘솔 원본은 `retry-boot.log`다. 해당 디렉터리에서 `shasum -a 256 -c SHA256SUMS`로 파일을 확인할 수 있다. 작업 컨테이너 두 개는 종료 후 제거했다.
