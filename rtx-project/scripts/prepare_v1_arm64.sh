#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Prepare the pinned V1; never build Image, install, or reboot.
set -euo pipefail
exec bash "$(dirname -- "${BASH_SOURCE[0]}")/lib/prepare_arm64.sh" v1 "$@"
