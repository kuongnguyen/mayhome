"""Build a deterministic stdlib-only read-only audit executable; never deploys."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'artifacts'
SOURCES = ('domain.py','collector.py','post_boot.py','preflight.py')

def build():
    DESTINATION.mkdir(exist_ok=True)
    archive = DESTINATION / 'mayhome-readonly-audit.pyz'
    entries = {
        '__main__.py':b'from mayhome_center.preflight import main\nraise SystemExit(main())\n',
        # Audit distribution intentionally has no Flask/web imports or dependencies.
        'mayhome_center/__init__.py':b'"""Standalone read-only audit distribution."""\n',
    }
    for name in SOURCES:
        entries['mayhome_center/'+name]=(ROOT/'src/mayhome_center'/name).read_bytes()
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for name,content in sorted(entries.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o644 << 16
            bundle.writestr(info,content)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.pyz.sha256').write_text(f'{digest}  {archive.name}\n')
    print(archive)
    print(archive.with_suffix('.pyz.sha256'))

if __name__=='__main__':build()
