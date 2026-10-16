#!/bin/sh
set -eu
[ "${1:-}" = "--disable-mhc-reviewed" ] || { echo 'Explicit MHC rollback authorization required.' >&2; exit 2; }
# Disable MHC only. No wildcard commands, deletion or Nextcloud changes.
systemctl disable --now mayhome-center-collector.timer mayhome-center.service
systemctl stop mayhome-center-collector.service
printf '%s\n' 'MHC stopped. Files and diagnostics retained; Nextcloud units untouched.'
