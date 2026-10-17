#!/usr/bin/env bash
# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
#
# Run on the round VM as root: vm_setup.sh cpu|gpu. Installs Docker, git,
# make and python3 (with PyYAML for the harness outside the containers), and
# on a GPU VM checks the driver and the NVIDIA container runtime.
set -euo pipefail
kind="${1:-cpu}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git make python3 python3-yaml python3-venv ca-certificates curl
if ! command -v docker > /dev/null; then
  apt-get install -y -qq docker.io
fi
systemctl start docker
if [ "$kind" = gpu ]; then
  for _ in $(seq 1 60); do nvidia-smi > /dev/null 2>&1 && break; sleep 10; done
  nvidia-smi || { echo "FATAL: no NVIDIA driver on the GPU VM" >&2; exit 2; }
  if ! docker info 2>/dev/null | grep -qi nvidia; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq && apt-get install -y -qq nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
  fi
fi
docker version > /dev/null
