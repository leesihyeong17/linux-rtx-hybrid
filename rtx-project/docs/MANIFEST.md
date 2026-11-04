# MANIFEST

## 소스 기준선
- 기준 태그: `v0-baseline` (커밋 `991defc`)
- 업스트림: gregkh/linux stable `v5.4.214` = `9f02aa34e179d1b0185a27980dd5bafd0f2e6e59`
- 차이: `arch/x86/configs/rtx_defconfig` 추가 1개뿐 (2026-11-02 확인)
- 확인 방법:
  ```bash
  git fetch --depth 1 https://github.com/gregkh/linux.git tag v5.4.214
  git diff --name-status v5.4.214 v0-baseline   # rtx_defconfig 한 줄만 나와야 함
  ```
- 원본: kernel.org `linux-5.4.214.tar.xz` (sha256: 9abc79b4808c827eb82f8a44313ce6d3ade255949989bb98c11acda7ca3bc5db)

## 빌드 설정
- 설정: `make rtx_defconfig` (localmodconfig 기반, LOCALVERSION=-rtx, DEBUG_INFO off)
- 컴파일러: gcc (Ubuntu 9.4.0-1ubuntu1~20.04.2) 9.4.0
- 빌드 명령: `make CC="ccache gcc" -j$(nproc)` → `sudo make INSTALL_MOD_STRIP=1 modules_install` → `sudo make install`

## 실행 환경 (실험 때 D가 채움)
- 호스트 / VirtualBox 버전:
- VM: vCPU, RAM, Ubuntu 20.04.6
- PMU(cycles) 지원 여부:미지원 (VirtualBox, 2026-11-04 확인)
- 네트워크 조건 (netem, RTT, 손실, qdisc):
- 혼잡 제어 / offload:
- 측정 도구 버전: