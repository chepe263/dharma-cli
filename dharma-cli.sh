#!/bin/sh
# Wrapper that KIROCREW_KIRO_BIN points at. KiroCrew invokes this exactly as it
# would invoke `kiro-cli` (e.g. `acp --agent X`, `whoami --format json`,
# `--version`, `chat --list-models --format json`). We forward every argument
# to the faux backend, which answers both the one-shot commands and the ACP
# session. The faux reads its own config from .env in this directory.
cd "$(dirname "$0")" || exit 1
exec python3 dharma_cli.py "$@"
