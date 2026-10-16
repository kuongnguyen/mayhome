"""Deterministic review source bundle; no Git publication or production actions."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {'.py','.md','.json','.txt','.css','.js','.html','.service','.timer','.example','.sh','.png'}
TOP = ('.gitignore','README.md','pyproject.toml','requirements.lock')
DIRECTORIES = ('src','tests','docs','packaging','scripts')

def sources():
    result = [ROOT/name for name in TOP]
    for name in DIRECTORIES:
        base = ROOT/name
        for path in base.rglob('*'):
            if path.is_symlink():
                raise ValueError('Review source contains symlink')
            if '__pycache__' in path.parts or any(part.endswith('.egg-info') for part in path.parts):
                continue
            if path.is_file() and path.suffix in EXTENSIONS:
                result.append(path)
    return sorted(set(result), key=lambda path:path.relative_to(ROOT).as_posix())

def build():
    output = ROOT/'artifacts';output.mkdir(exist_ok=True)
    files = sources()
    contents = {path.relative_to(ROOT).as_posix():path.read_bytes() for path in files}
    manifest = {'release':'0.1.0-review-dual-connectors','production_acceptance':'PENDING',
                'git_commit':None,'files':{name:hashlib.sha256(body).hexdigest() for name,body in contents.items()}}
    contents['SOURCE-MANIFEST.json']=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    archive=output/'mayhome-center-review.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name,body in sorted(contents.items()):
            info=zipfile.ZipInfo('mayhome-center-review/'+name,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o755 if name.endswith('.sh') else 0o644)<<16
            bundle.writestr(info,body)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n')
    print(f'{archive} ({len(files)} source/evidence files)')
    return archive

if __name__=='__main__':build()
