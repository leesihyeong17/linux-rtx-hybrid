# B split 실패 경계 검증과 선택 근거

검증일: 2026-11-11. 이 문서는 보고서에서 **무엇을 실제 실행했고 무엇을 추론했는지** 구분하기 위한 기록이다.

## 질문과 구현

`tcp_fragment()`가 prepare 전에 unclone 또는 새 skb 할당 실패를 받으면, RTX 큐가 원래 상태로 남고 다음 시도에서 정상 분할되는가?

`tests/integration/case_failures.c`가 실제 커널 소켓과 skb를 만든다. 원본 skb를 clone한 상태에서 두 실패 경계를 각각 실행한다. 오류 반환, prepare/commit 미실행, 닫힌 transaction, 단일 노드, 시퀀스 범위, 길이, 메모리 회계, pcount와 세 힌트를 확인한다. 실패를 해제한 재시도에서는 prepare/commit 각 1회, 연결·회계 불변식과 4,000바이트 payload 보존을 확인한다.

공개 A API나 production TCP 파일은 변경하지 않았다. `apply_overlay.py`는 `git archive HEAD`로 만든 컨테이너 내부 소스 사본에만 계측을 삽입한다. 특정 소켓의 호출 경계에서 오류를 반환하고, 다른 호출은 원래 함수를 실행한다. 정확한 대체 지점이 하나가 아니면 패치 적용을 거부한다.

## 대안과 트레이드오프

| 대안 | 얻는 것 | 비용·한계 | 결정 |
| --- | --- | --- | --- |
| 전역 failslab/fail_page_alloc | 실제 allocator 실패 경로 | 부팅·다른 소켓도 실패할 수 있어 목표 호출 도달과 재현성 관리가 어려움 | 다음 단계의 allocator 내부 검증에 적합 |
| 특정 소켓의 의존 함수 호출 경계에서 실패 반환 | 원하는 오류 분기와 재시도를 결정적으로 실행; 실제 tcp_fragment와 Core 사용 | allocator 내부 정리·메모리 압박 효과는 검증하지 못함 | 이번 회귀 검증에 선택 |
| tcp_fragment 전체 mock 또는 큐 모델 | 빠르고 환경 구축이 간단함 | 실제 실패 처리와 TCP 메모리 회계를 실행하지 않음 | 단독 근거로 사용하지 않음 |

할당 실패는 성공한 unclone 뒤에 발생할 수 있다. 따라서 데이터의 공유 소유권까지 실패 전과 동일하다고 요구하지 않는다. 큐·시퀀스·데이터·회계의 관측 가능한 보존과 재시도 성공이 판단 기준이다. 이 결과를 **실제 메모리 고갈 시험 통과**라고 쓰면 안 된다.

## 공통 재현 환경과 증거

```sh
RTX_BUILD_IMAGE=962f6cadeae0 rtx-project/scripts/run_b_integration.sh
python3 -B -m unittest discover -s rtx-project/tests/integration -p 'test_*.py' -v
python3 -B rtx-project/tests/integration/render_report.py rtx-project/.work/<실행 디렉터리>
```

호스트의 기존 Ubuntu 22.04 arm64 이미지를 사용한다. 전용 컨테이너의 Linux 파일시스템에서 x86_64 cross compiler로 빌드하고 QEMU TCG에서 실행한다. `x86_64_defconfig`에 TCP_RTX_DEBUG, initramfs와 serial console을 활성화한다. 이는 앞서 완료한 rtx_defconfig DEBUG off/on 빌드 비교와 다른 **DEBUG on 실행 시험**이다.

각 실행 폴더에는 기준 SHA, source.tar, 환경·설정·빌드 로그, 실제 계측 patch, harness 사본, 원본/계측 소스 hash, bzImage, initramfs, SHA256SUMS, serial.log, summary.json을 보존한다. `case_*.c`를 자동 검색하므로 항목별 커밋을 checkout해도 당시 포함된 시험만 실행한다. 커밋을 나누기 전 전체 harness로 실행한 증거는 기준 HEAD와 harness hash를 함께 사용해야 재현할 수 있다.

`check_results.py`는 누락·중복 항목, 0개 assertion, 합계 불일치, 커널 경고/오류, guest 또는 adapter 성공 누락을 실패로 판정한다. 정상 로그 1종과 잘못된 로그 9종으로 판정기의 거부 동작도 확인한다. 스크린샷은 검증된 원본 serial 로그를 행 번호와 SHA-256을 붙여 브라우저에서 보여준 화면이다. 커널 콘솔 자체를 촬영한 것으로 표현하지 않는다.

## 실행 결과

실행 `b-integration-kiJKsMWl`: **38 assertion 통과, 0 실패**. 공통 실행 전체는 285 assertion 통과, adapter 자체 테스트 495 통과, 기본 TCP smoke 4 사례 통과다. assertion 수에는 fixture 생성·정리 검사도 포함되며 서로 독립적인 네트워크 시나리오 수가 아니다.

- [원본 serial 로그](../.work/b-integration-kiJKsMWl/serial.log), [구조화 결과](../.work/b-integration-kiJKsMWl/summary.json)
- [결과 스크린샷](../.work/b-integration-kiJKsMWl/report/failures.png), [브라우저 보고서](../.work/b-integration-kiJKsMWl/report/failures.html)
- [빌드 로그](../.work/b-integration-kiJKsMWl/build.log), [소스·계측·바이너리 체크섬](../.work/b-integration-kiJKsMWl/SHA256SUMS)

스크린샷은 실제 실행 로그를 표시한 로컬 보고서 페이지를 Playwright로 캡처했다. 원본 로그의 행 번호와 hash를 함께 보존한다. `.work` 산출물은 Git에 포함하지 않으므로 팀 보고서 공유 시 별도로 첨부해야 한다.


### 첫 실패에서 배운 점

첫 실행 `.work/b-integration-Qxp2fwbw`에서는 285 assertion과 TCP smoke가 통과했지만 `__copy_skb_header()`의 dst refcount 경고가 두 번 발생했다. 판정기는 이를 성공으로 인정하지 않았다. 원인은 시험 fixture에서 RTX skb를 바로 `skb_clone()`한 것이었다. `tcp_tsorted_anchor`와 `_skb_refdst`가 겹치는 필드를 TCP의 기존 `tcp_skb_tsorted_save/restore` 방식으로 보호해야 한다. fixture를 고쳤으며 production split 구현을 수정하지 않았다.

경고를 무시하거나 해당 문자열을 필터에서 제외하는 방법은 채택하지 않았다. 그렇게 하면 양성 결과가 잘못 구성한 fixture를 숨길 수 있기 때문이다. 실패 실행의 원본 `serial.log`와 `build.log`, 체크섬 및 실패 화면을 보존한다. 실패 실행에서도 task 컨테이너 제거를 확인했다.

### 최종 환경·검사 기록

| 항목 | 실제 값 |
| --- | --- |
| 기준 HEAD | `98eaccc0353af4e324447098a3f3a50e38092481` |
| 이미지 | 기존 Ubuntu 22.04 arm64 `962f6cadeae0` |
| 컴파일러 | x86_64-linux-gnu-gcc 11.4.0 |
| 실행 | QEMU 6.2.0, TCG, x86_64, 1 CPU, RAM 512 MiB |
| 설정 | x86_64_defconfig 기반, TCP_RTX_DEBUG=y, MODULES=n |
| 커널 | 5.4.214-rtx-b-integration |
| bzImage SHA-256 | `6f2dfe20a5d13e6d8787d9eb27f11b916495854449f38ecd2e77e0572742b17d` |
| config SHA-256 | `abf612e378a71f76bca6638d9d85be27c9ecc2cf728fbaae8216526cc189d716` |
| serial.log SHA-256 | `0d1d0718735585ca69d40d968c34e3e4c09db0c26592ecee9e559c1a5b4ebc12` |

최종 실행의 커널 WARN/BUG/Oops/panic은 0건이다. 빌드에는 OpenSSL deprecated API 7건과 compressed kernel linker 경고 2건이 있다. 이전 부팅 빌드 `b-runtime-nFfGZmCz/build.log`와 행 번호를 제외하고 비교했을 때 새 경고는 없었다. 이는 DEBUG off/on 재비교 결과가 아니라 이전 DEBUG on 빌드와의 경고 비교다.

C harness 6개 파일의 `scripts/checkpatch.pl --no-tree --file`은 오류 0, 경고 0이다. shell syntax 검사와 판정기 unittest 2개(잘못된 로그 9종 포함), staged diff의 whitespace 검사도 통과했다. 실행 당시 harness 사본과 최종 추적 파일의 내용이 같은지 확인했다. 원래 TCP production 파일과 A 공개 헤더의 diff는 없다.

두 실행의 task 컨테이너는 모두 제거했고 `remaining-containers.txt`가 비어 있다. 재사용 이미지와 기존 컨테이너·볼륨은 보존했다. 스크린샷 촬영용 임시 HTTP 서버도 종료했다.

[첫 실패 화면](../.work/b-integration-kiJKsMWl/report/failure-review.png)은 assertion 통과만으로 성공 판정하지 않은 이유를 보여준다. 첫 실패 원본 로그는 [별도 실행 폴더](../.work/b-integration-Qxp2fwbw/serial.log)에 남아 있다.
