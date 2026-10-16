"""Read-only deployment evidence. Never installs, enables, starts or repairs anything."""
import argparse
import json
import os
import subprocess
from .collector import ProbeError, mount_status, open_dir, run, unit_status
from .domain import CONTAINERS, MOUNTS, UNITS, utcnow
from .post_boot import collect_post_boot


def recovery_marker():
    directory = open_dir('/var/lib/nextcloud-auto-backup', trusted=True)
    try:
        try:
            os.stat('maintenance-owned', dir_fd=directory, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    finally:
        os.close(directory)


def audit(probe_tunnel=False):
    report = {'schema_version':1, 'generated_at':utcnow().isoformat(),
              'target_host_verified':False, 'deployment_allowed':False,
              'read_only':True, 'automatic_checks_passed':False, 'checks':[], 'manual_gates':[
                  'NEXTCLOUD_MAINTENANCE_OFF_VERIFIED', 'BACKUP_GUARD_REVIEWED_AND_VERIFIED',
                  'RELEASE_AND_COLLECTOR_PERMISSIONS_REVIEWED', 'TUNNEL_ENDPOINT_MAPPING_REVIEWED',
                  'CLOUDFLARE_TUNNEL_ROUTES_REVIEWED', 'ENGINE_SCRIPT_HASHES_RECORDED',
                  'BASELINE_AND_CHECKPOINT_PRESERVED', 'RETENTION_AND_0200_RUN_EVIDENCE_REVIEWED',
                  'EXPLICIT_DEPLOYMENT_AUTHORIZATION',
              ]}
    def add(code, outcome, data=None):
        report['checks'].append({'code':code, 'outcome':outcome, 'data':data})
    # Avoid accidentally presenting a cloud machine's mounts/services as mayhome evidence.
    if os.uname().nodename.split('.')[0] != 'mayhome':
        add('TARGET_HOST_MISMATCH','BLOCKED')
        return report
    report['target_host_verified'] = True
    failures = (OSError, ValueError, KeyError, TypeError, ProbeError, subprocess.SubprocessError)
    for path, uuid in MOUNTS.items():
        try:
            observed = mount_status(path, uuid)
            add('MOUNT_VERIFICATION', 'PASS' if observed['status']=='HEALTHY' else 'BLOCKED', observed)
        except failures:
            add('MOUNT_UNVERIFIED','BLOCKED', {'mountpoint':path})
    for unit in UNITS:
        try:
            observed = unit_status(unit)
            outcome = 'OBSERVED'
            if unit == 'nextcloud-auto-backup.service':
                # Active/deactivating/activating/failed and not-found all block deployment.
                outcome = 'PASS' if observed.get('load_state')=='loaded' and observed.get('active_state')=='inactive' and observed.get('result')=='success' else 'BLOCKED'
            elif unit == 'nextcloud-mount-guard.timer':
                outcome = 'PASS' if observed.get('active_state')=='active' and observed.get('enabled_state')=='enabled' else 'BLOCKED'
            elif unit in ('nextcloud-auto-backup.timer','nextcloud-backup-watchdog.timer'):
                outcome = 'PASS' if observed.get('active_state')=='active' and observed.get('enabled_state')=='enabled' else 'BLOCKED'
            # Retention timer is observed only; never demand enablement.
            add('SERVICE_STATE',outcome,observed)
        except failures:
            add('SERVICE_UNVERIFIED','BLOCKED',{'unit_name':unit})
    for name in CONTAINERS:
        try:
            observed = run(['/usr/bin/docker','inspect','--type','container','--format','{{json .State.Running}}',name]).strip()
            add('CONTAINER_STATE','PASS' if observed=='true' else 'BLOCKED',
                {'name':name,'running':True if observed=='true' else False if observed=='false' else None})
        except failures:
            add('CONTAINER_UNVERIFIED','BLOCKED',{'name':name})
    try:
        present = recovery_marker()
        add('RECOVERY_MARKER_PRESENT' if present else 'RECOVERY_MARKER_ABSENT', 'BLOCKED' if present else 'PASS')
    except failures:
        add('RECOVERY_MARKER_UNVERIFIED','BLOCKED')
    if probe_tunnel:
        observed = collect_post_boot(run, enabled=True, docker_enabled=True)
        add('TUNNEL_CONNECTIVITY', 'PASS' if observed['status']=='HEALTHY' else 'BLOCKED', observed)
    else:
        add('TUNNEL_PROBE_NOT_REQUESTED','UNVERIFIED')
    # Manual gates cannot be auto-approved by a host check or command-line switch.
    report['automatic_checks_passed'] = all(c['outcome'] in ('PASS','OBSERVED') for c in report['checks'])
    return report


def main():
    parser = argparse.ArgumentParser(description='MHC read-only audit; does not authorize deployment.')
    parser.add_argument('--probe-tunnel', action='store_true', help='Also GET the fixed local cloudflared readiness/metrics endpoints.')
    args = parser.parse_args()
    report = audit(probe_tunnel=args.probe_tunnel)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Always pending until the independent manual gates are reviewed.
    return 2

if __name__ == '__main__':
    raise SystemExit(main())
