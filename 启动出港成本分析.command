#!/bin/sh
cd "$(dirname "$0")" || exit 1
./runtime-mac/bin/python3 scripts/check_dependencies.py || exit 1
exec ./runtime-mac/bin/python3 server.py --port 8888
