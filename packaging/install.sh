#!/bin/sh
# Run only on mayhome after production audit and explicit deployment approval.
set -eu
umask 022
[ "${1:-}" = "--install-reviewed" ] || { echo 'Requires completed Stage 0 and explicit deployment approval; see docs/ops-runbook.md.' >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo 'Installation requires an administrator.' >&2; exit 2; }
[ "$(hostname -s)" = mayhome ] || { echo 'Wrong target host.' >&2; exit 2; }
[ ! -e /opt/mayhome-center ] || { echo 'Existing release: preserve and review upgrade manually.' >&2; exit 2; }
getent passwd mayhome-center >/dev/null || useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin mayhome-center
for unit in mayhome-center.service mayhome-center-collector.service mayhome-center-collector.timer; do
 [ ! -e "/etc/systemd/system/$unit" ] || { echo 'MHC unit already exists; review upgrade.' >&2; exit 2; }
done
install -d -m 0755 /opt/mayhome-center
# Copy only known application files; never a development venv or secret files.
python3 packaging/stage_source.py
install -m 0644 pyproject.toml requirements.lock /opt/mayhome-center/
chown -R root:root /opt/mayhome-center
chmod -R go-w /opt/mayhome-center
python3 -m venv /opt/mayhome-center/.venv
/opt/mayhome-center/.venv/bin/pip install -r /opt/mayhome-center/requirements.lock
/opt/mayhome-center/.venv/bin/pip install --no-deps /opt/mayhome-center
for unit in mayhome-center.service mayhome-center-collector.service mayhome-center-collector.timer; do
 install -m 0644 "packaging/systemd/$unit" "/etc/systemd/system/$unit"
done
systemd-analyze verify /etc/systemd/system/mayhome-center.service /etc/systemd/system/mayhome-center-collector.service /etc/systemd/system/mayhome-center-collector.timer
printf '%s\n' 'Files installed only. No daemon reload, enable/start, DNS or tunnel changes performed. Follow the reviewed activation gates in docs/ops-runbook.md.'
