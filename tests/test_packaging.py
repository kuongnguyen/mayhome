import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('stage_source',Path(__file__).parents[1]/'packaging/stage_source.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)

def test_new_release_modes_and_secret_cache_exclusion(tmp_path):
    source=tmp_path/'source';source.mkdir(mode=0o700)
    nested=source/'pkg/templates';nested.mkdir(parents=True,mode=0o700)
    for name in ['app.html','.env','private.sql','cache.pyc']:
        path=nested/name;path.write_text('fixture');path.chmod(0o600)
    cache=source/'pkg/__pycache__';cache.mkdir();(cache/'other.py').write_text('not source')
    destination=tmp_path/'release'
    stage.stage_sources(source,destination)
    assert (destination/'pkg/templates/app.html').stat().st_mode & 0o777==0o644
    for path in [destination,destination/'pkg',destination/'pkg/templates']:
        assert path.stat().st_mode & 0o777==0o755
    assert len([path for path in destination.rglob('*') if path.is_file()])==1
    assert not (destination/'pkg/__pycache__').exists()
    with pytest.raises(FileExistsError):stage.stage_sources(source,destination)

def test_source_symlink_rejected_before_staging(tmp_path):
    source=tmp_path/'source';source.mkdir();outside=tmp_path/'private';outside.write_text('secret')
    (source/'app.py').symlink_to(outside)
    destination=tmp_path/'release'
    with pytest.raises(ValueError):stage.stage_sources(source,destination)
    assert not destination.exists()
