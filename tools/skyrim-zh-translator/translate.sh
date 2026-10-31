#!/usr/bin/env bash
# 用法： ./translate.sh                 交互向导
#        ./translate.sh "/path/to/mods" 直接指定目录
set -e
cd "$(dirname "$0")"
exec python3 run.py "$@"
