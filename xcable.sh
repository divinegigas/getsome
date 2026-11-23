#!/usr/bin/env bash
# XCable: the same program as Neon Cable 42 under its own name and look (black & gold, jazz),
# with completely separate channels, settings and schedules (~/.local/share/xcable, ~/.config/xcable).
export GETSOME_EDITION=xcable GETSOME_APP_NAME="XCable"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/getsome.sh" "$@"
