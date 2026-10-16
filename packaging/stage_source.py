"""Stage only application sources into a NEW MHC release, with web-readable modes."""
from pathlib import Path
import shutil
import sys

ALLOWED = {'.py', '.html', '.css', '.js'}

def stage_sources(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_dir() or source.is_symlink():
        raise ValueError('Invalid source directory')
    candidates = list(source.rglob('*'))
    if any(path.is_symlink() for path in candidates):
        raise ValueError('Symlinks in application source require review')
    destination.mkdir(mode=0o755)
    destination.chmod(0o755)
    for path in candidates:
        if not path.is_file() or '__pycache__' in path.parts or path.suffix not in ALLOWED:
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        # Only directories created under this new destination are changed.
        for parent in target.relative_to(destination).parents:
            (destination / parent).chmod(0o755)
        shutil.copyfile(path, target)
        target.chmod(0o644)

if __name__ == '__main__':
    stage_sources('src', '/opt/mayhome-center/src')
