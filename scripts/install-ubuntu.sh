#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/opt/xiaotianwen}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

echo "This script installs the public code layout under: ${PROJECT_ROOT}"
echo "Instance data and secrets must be restored separately from the private repository."

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v "${PYTHON_BIN}" >/dev/null || { echo "${PYTHON_BIN} is required" >&2; exit 1; }

mkdir -p "${PROJECT_ROOT}/instance" "${PROJECT_ROOT}/secrets"
echo "Next steps:"
echo "  1. Copy private instance data into ${PROJECT_ROOT}/instance"
echo "  2. Install AstrBot and pinned plugin dependencies"
echo "  3. Create the host-only .env from config/.env.example"
echo "  4. Run scripts/doctor before scripts/start"
