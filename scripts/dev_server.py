"""Cloud/local development only: ephemeral login, loopback, no production telemetry."""
import os
import argparse
from pathlib import Path
import secrets
import subprocess
import tempfile

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5010)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='mhc-dev-') as directory:
        token = secrets.token_urlsafe(32)
        token_path = Path(directory) / 'login-token'
        token_path.touch(mode=0o600)
        token_path.write_text(token)
        environment = os.environ.copy()
        environment.update(MHC_AUTH_TOKEN=token, MHC_SESSION_KEY=secrets.token_hex(32), MHC_COOKIE_SECURE='0')
        print(f'Development-only login token file: {token_path}', flush=True)
        command = [str(repo / '.venv/bin/gunicorn'), '--bind', f'127.0.0.1:{args.port}', '--workers', '2',
                   '--threads', '2', '--forwarded-allow-ips=', 'mayhome_center:create_app()']
        try:
            raise SystemExit(subprocess.call(command, cwd=repo, env=environment))
        except KeyboardInterrupt:
            pass
