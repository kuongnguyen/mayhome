"""Read-only cloudflared Edge connectivity. No container lifecycle operations."""
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
from .domain import utcnow

GRACE_SECONDS = 300
METRIC = re.compile(r'^cloudflared_tunnel_ha_connections(?:\{[^\n]*\})?\s+([^\s]+)(?:\s+[0-9]+)?$', re.MULTILINE)


def connections(text):
    values = [float(value) for value in METRIC.findall(text)]
    if not values or any(not math.isfinite(value) or value < 0 or value > 1024 or not value.is_integer() for value in values):
        raise ValueError('invalid or absent connectivity metric')
    return int(sum(values))


def boot_context():
    uptime = float(Path('/proc/uptime').read_text().split()[0])
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not math.isfinite(uptime) or uptime < 0 or not re.fullmatch(r'[0-9a-f-]{36}', boot_id):
        raise ValueError('invalid boot context')
    return uptime, boot_id


def assess(ready_code, edge_connections, uptime):
    if ready_code == 200 and edge_connections is not None and edge_connections > 0:
        return 'HEALTHY', 'TUNNEL_EDGE_CONNECTED'
    disconnected = ready_code == 503 or edge_connections == 0
    if disconnected:
        if uptime is not None and uptime < GRACE_SECONDS:
            return 'WARNING', 'TUNNEL_BOOT_RECOVERING'
        return 'CRITICAL', 'TUNNEL_EDGE_DISCONNECTED'
    return 'UNKNOWN', 'TUNNEL_PROBE_UNAVAILABLE'


def collect_systemd(run, enabled=None):
    result = {'status':'UNKNOWN', 'code':'TUNNEL_MONITOR_NOT_CONFIGURED',
              'observed_at':utcnow().isoformat(), 'boot_id':None, 'uptime_sec':None,
              'grace_seconds':GRACE_SECONDS, 'ready_http_status':None, 'edge_connections':None,
              'scope':'cloudflared_to_edge', 'public_route_verified':False}
    try:
        uptime, boot_id = boot_context()
        result.update(uptime_sec=int(uptime), boot_id=boot_id)
    except (OSError, ValueError, IndexError):
        uptime = None
    if enabled is None:
        enabled = os.environ.get('MHC_TUNNEL_MONITOR') == '1'
    if not enabled:
        return result
    try:
        output = run(['/usr/bin/systemctl', 'show', 'cloudflared.service', '--property=ActiveState,MainPID'], timeout=3, max_bytes=4096)
        properties = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        state = properties.get('ActiveState')
        result['active_state'] = state if state in ('active','inactive','failed','activating','deactivating') else 'unknown'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        result['active_state'] = 'unknown'
    # Fixed local endpoint only; no HTTP redirects, user URLs or raw response exposed.
    # A host administrator must review the mapping to the correct cloudflared process.
    probe_endpoint(run, 'http://127.0.0.1:20241', result)
    result['status'], result['code'] = assess(result['ready_http_status'], result['edge_connections'], uptime)
    if result['active_state'] in ('inactive','failed','activating','deactivating'):
        result['status'], _ = assess(503, 0, uptime)
        result['code'] = 'TUNNEL_SERVICE_NOT_ACTIVE'
    elif result['active_state'] == 'unknown' and result['status'] == 'HEALTHY':
        result['status'], result['code'] = 'UNKNOWN', 'TUNNEL_SERVICE_UNVERIFIED'
    return result


def probe_endpoint(run, base, result):
    common = ['/usr/bin/curl', '--disable', '--silent', '--show-error', '--noproxy', '*',
              '--max-time', '2', '--max-filesize', '65536', '--proto', '=http']
    try:
        code = run(common + ['--output', '/dev/null', '--write-out', '%{http_code}',
                            base + '/ready'], timeout=3, max_bytes=16)
        result['ready_http_status'] = int(code.strip())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        pass
    try:
        text = run(common + ['--write-out', '\n%{http_code}', base + '/metrics'], timeout=3, max_bytes=65536)
        body, status = text.rsplit('\n', 1)
        if status.strip() != '200':
            raise ValueError('metrics HTTP status')
        result['edge_connections'] = connections(body)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        pass


DOCKER_NAME = 'cloudflared-tunnel'
DOCKER_NETWORK = 'cuongnh_nextcloud-net'
DOCKER_FORMAT = ('{"id":{{json .Id}},"name":{{json .Name}},"running":{{json .State.Running}},'
                 '"network_mode":{{json .HostConfig.NetworkMode}},'
                 '"ip":{{with index .NetworkSettings.Networks "cuongnh_nextcloud-net"}}{{json .IPAddress}}{{else}}null{{end}}}')


def inspect_container(run):
    data = json.loads(run(['/usr/bin/docker', 'inspect', '--type', 'container', '--format', DOCKER_FORMAT, DOCKER_NAME],
                          timeout=3, max_bytes=4096))
    if (not isinstance(data, dict) or data.get('name') != '/' + DOCKER_NAME
            or not isinstance(data.get('running'), bool)
            or not isinstance(data.get('id'), str) or not re.fullmatch(r'[a-f0-9]{64}', data['id'])
            or data.get('network_mode') != DOCKER_NETWORK):
        raise ValueError('unverified container identity/network')
    return data


def resolve_address(run, data):
    config = json.loads(run(['/usr/bin/docker', 'network', 'inspect', '--format', '{{json .IPAM.Config}}', DOCKER_NETWORK],
                            timeout=3, max_bytes=4096))
    if not isinstance(config, list) or len(config) > 4:
        raise ValueError('network config')
    networks = [ipaddress.ip_network(row['Subnet'], strict=True) for row in config]
    networks = [network for network in networks if network.version == 4]
    if len(networks) != 1:
        raise ValueError('ambiguous network')
    network = networks[0]
    private = [ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
    address = ipaddress.ip_address(data['ip'])
    if (address.version != 4 or network.prefixlen < 16 or network.prefixlen > 30
            or not any(network.subnet_of(block) for block in private)
            or address not in network or address in (network.network_address, network.broadcast_address)):
        raise ValueError('unsafe target')
    return str(address), str(network)


def collect_docker(run, enabled=None):
    result = {'status':'UNKNOWN','code':'TUNNEL_MONITOR_NOT_CONFIGURED','observed_at':utcnow().isoformat(),
              'running':None,'ready_http_status':None,'edge_connections':None,'address':None,'network_cidr':None,
              'container_name':DOCKER_NAME,'network_name':DOCKER_NETWORK,
              'scope':'cloudflared_to_edge','public_route_verified':False,'uptime_sec':None,'boot_id':None,'grace_seconds':GRACE_SECONDS}
    try:
        uptime, boot_id = boot_context()
        result.update(uptime_sec=int(uptime), boot_id=boot_id)
    except (OSError, ValueError, IndexError):
        uptime = None
    if enabled is None:
        enabled = os.environ.get('MHC_TUNNEL_DOCKER_MONITOR') == '1'
    if not enabled:
        return result
    failures = (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError)
    try:
        before = inspect_container(run)
        result['running'] = before['running']
        if not before['running']:
            result['status'], _ = assess(503, 0, uptime)
            result['code'] = 'TUNNEL_CONTAINER_NOT_RUNNING'
            return result
        address, cidr = resolve_address(run, before)
        result.update(address=address, network_cidr=cidr)
        probe_endpoint(run, 'http://' + address + ':20241', result)
        after = inspect_container(run)
        if any(before[key] != after[key] for key in ('id','ip','running','network_mode')):
            result.update(code='TUNNEL_CONTAINER_CHANGED',ready_http_status=None,edge_connections=None)
            return result
        result['status'], result['code'] = assess(result['ready_http_status'],result['edge_connections'],uptime)
    except failures:
        result.update(status='UNKNOWN',code='TUNNEL_CONTAINER_UNVERIFIED',ready_http_status=None,edge_connections=None)
    return result


def collect_post_boot(run, enabled=None, docker_enabled=None):
    connectors = {'systemd':collect_systemd(run, enabled), 'docker':collect_docker(run, docker_enabled)}
    statuses = [row['status'] for row in connectors.values()]
    overall = next(status for status in ('CRITICAL','UNKNOWN','WARNING','HEALTHY') if status in statuses)
    return {'status':overall,'code':'TUNNEL_CONNECTORS_' + ('HEALTHY' if overall == 'HEALTHY' else 'DEGRADED'),
            'observed_at':utcnow().isoformat(),'connectors':connectors,
            'scope':'cloudflared_to_edge','public_route_verified':False}



def display_status(monitor, freshness):
    if freshness == 'stale':
        return 'STALE'
    if freshness != 'fresh':
        return 'UNKNOWN'
    return monitor.get('status', 'UNKNOWN')


def present_monitor(monitor, freshness):
    result = {**monitor, 'status':display_status(monitor, freshness)}
    if isinstance(monitor.get('connectors'), dict):
        result['connectors'] = {
            name:{**row,'status':display_status(row,freshness)}
            for name,row in monitor['connectors'].items() if name in ('systemd','docker') and isinstance(row,dict)
        }
    return result
