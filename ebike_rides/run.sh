#!/bin/sh
set -e
LEVEL=$(python3 -c "import json;print(json.load(open('/data/options.json')).get('log_level','info'))" 2>/dev/null || echo info)
export LOG_LEVEL="$LEVEL"
exec python3 -m app.main
