#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
profile="${1:-quick}"
if [[ $# -gt 0 ]]; then
  shift
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_command="${PYTHON_BIN}"
elif command -v python3 >/dev/null 2>&1; then
  python_command="python3"
else
  python_command="python"
fi

cd "${repository_root}"
if [[ "${profile}" == "ui" ]]; then
  exec "${python_command}" -m tests.harness.cli ui "$@"
fi

case "${profile}" in
  quick|refactor|full-offline|integration) ;;
  *)
    printf 'Unknown profile: %s\n' "${profile}" >&2
    exit 2
    ;;
esac
exec "${python_command}" -m tests.harness.cli run --profile "${profile}" "$@"
