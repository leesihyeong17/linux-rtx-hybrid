# MANIFEST

## 소스
기준선: v0-baseline (991defc) = gregkh v5.4.214 (9f02aa34...) + rtx_defconfig
측정한 커밋: <해당 커밋 SHA>
설정: arch/x86/configs/rtx_defconfig, 비교군 옵션 = HYBRID, K = 8
컴파일러: gcc 9.4.0

## 환경
호스트: 4코어 노트북, VirtualBox 7.x
VM: vCPU 3, RAM 4GB, Ubuntu 20.04.6
네트워크: veth + netem, RTT 20ms, 손실 0.1%
혼잡 제어: cubic, offload: 기본값

## 측정
도구: iperf3 3.7, perf (커널 소스에서 빌드)
반복: 조건당 10회, warm-up 10초, 측정 30초