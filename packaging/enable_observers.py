"""Enable reviewed MHC-only maintenance/status and persistent-history drop-ins. No service action."""
import argparse
import os
from pathlib import Path
import stat

TARGET=Path('/etc/systemd/system/mayhome-center-collector.service.d')
SOURCE=Path(__file__).resolve().parent/'systemd/mayhome-center-collector.service.d'
FILES={'nextcloud-status.conf':'nextcloud-status.conf.example','persistent-history.conf':'persistent-history.conf.example'}


def trusted(path):
    for node in (path,*path.parents):
        info=node.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('Untrusted MHC drop-in path')


def configure():
    trusted(TARGET if TARGET.exists() else TARGET.parent)
    planned=[]
    for name,example in FILES.items():
        origin=SOURCE/example
        if origin.is_symlink():raise RuntimeError('Source drop-in symlink requires review')
        content=origin.read_bytes();target=TARGET/name
        if target.exists() or target.is_symlink():
            trusted(target)
            if target.read_bytes()!=content:raise RuntimeError('Existing MHC drop-in differs; review locally before changing it')
        else:planned.append((target,content))
    TARGET.mkdir(mode=0o755,exist_ok=True)
    created=[]
    try:
        for target,content in planned:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            created.append(target)
            with os.fdopen(fd,'wb') as stream:
                stream.write(content);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        for path in created:path.unlink()
        raise
    print('MHC observer drop-ins ready. Existing differing files were not overwritten.')
    print('Next: systemctl daemon-reload; systemctl start mayhome-center-collector.service')
    print('No Nextcloud/backup/tunnel service, auth secret or timer changed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reviewed',action='store_true');args=parser.parse_args()
    if os.geteuid()!=0 or os.uname().nodename.split('.')[0]!='mayhome' or not args.reviewed:
        parser.error('Requires administrator on mayhome and --reviewed after source review')
    os.umask(0o022);configure()

if __name__=='__main__':main()
