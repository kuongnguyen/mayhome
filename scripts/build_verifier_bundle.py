"""Reproducible standalone stdlib verifier, without web imports or runtime activation."""
import hashlib
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]

def build():
    output=ROOT/'artifacts';output.mkdir(exist_ok=True)
    entries={'__main__.py':b'from mayhome_center.verifier import main\nraise SystemExit(main())\n',
             'mayhome_center/__init__.py':b'"""Independent MHC verifier bundle."""\n'}
    for name in ('verifier.py','collector.py','domain.py'):
        entries['mayhome_center/'+name]=(ROOT/'src/mayhome_center'/name).read_bytes()
    path=output/'mayhome-verifier.pyz'
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,body in sorted(entries.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
            archive.writestr(info,body)
    path.with_suffix('.pyz.sha256').write_text(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n')
    print(path)
    return path

if __name__=='__main__':build()
