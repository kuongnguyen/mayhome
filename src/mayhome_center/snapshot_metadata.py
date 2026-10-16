"""Bounded snapshot metadata only; no recursion into Nextcloud user data."""
from datetime import datetime
import os
from pathlib import Path
import re
import stat
import time

CHECKPOINT = re.compile(r'[0-9]{8}-[0-9]{6}')
STAGING = re.compile(r'\.staging-[A-Za-z0-9_-]{1,64}')


def metadata_stat(parent, relative):
    parts = relative.split('/')
    descriptor = os.dup(parent)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
    finally:
        os.close(descriptor)


def checksum_metadata(parent):
    directory = os.open('manifests', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        file = os.open('database.sha256', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(file, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return False
            body = stream.read(4097)
        if len(body) > 4096:
            return False
        # A metadata format check only. Never open the referenced SQL dump.
        return re.fullmatch(rb'[0-9a-fA-F]{64}[ \t]+\*?(?:database/)?nextcloud\.sql\.gz\r?\n?', body) is not None
    finally:
        os.close(directory)


def structure(parent, baseline=False):
    paths = {'data':'directory','config/application' if baseline else 'application':'directory',
             'config':'directory','database':'directory','manifests':'directory',
             'database/nextcloud-20261015-174303.sql.gz' if baseline else 'database/nextcloud.sql.gz':'file',
             'manifests/backup-20261015-174303.txt' if baseline else 'manifests/checkpoint.txt':'file'}
    if not baseline:
        paths.update({'COMPLETE':'file','manifests/database.sha256':'file'})
    checks = {}
    for relative, kind in paths.items():
        try:
            mode = metadata_stat(parent, relative).st_mode
            checks[relative] = stat.S_ISDIR(mode) if kind=='directory' else stat.S_ISREG(mode)
        except OSError:
            checks[relative] = False
    return checks


def scan(root, open_dir, max_entries=500):
    root = Path(root)
    rows = []
    base = open_dir(root, trusted=(str(root)=='/mnt/nextcloud_backup/nextcloud'))
    try:
        try:
            present = stat.S_ISREG(metadata_stat(base, 'database/nextcloud-20261015-174303.sql.gz').st_mode)
        except OSError:
            present = False
        if present:
            checks = structure(base, baseline=True)
            rows.append({'id':'baseline','type':'baseline','timestamp':None,'completeness':'unknown',
                         'integrity_level':'present','protected':True,'protection_reasons':['baseline'],
                         'structure_checks':checks,'db_checksum_manifest_present':False,'db_checksum_manifest_format_valid':False,
                         'db_checksum_verified':False,'restoration_verified':False,
                         'metadata_errors':['BASELINE_STRUCTURE_INCOMPLETE'] if not all(checks.values()) else []})
        directory = os.open('snapshots', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
        try:
            deadline = time.monotonic()+2
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= max_entries or time.monotonic() > deadline:
                        raise ValueError('snapshot metadata scan limit')
                    staging = STAGING.fullmatch(entry.name) is not None
                    if not staging:
                        if not CHECKPOINT.fullmatch(entry.name):
                            continue
                        try:
                            timestamp = datetime.strptime(entry.name, '%Y%m%d-%H%M%S').isoformat()
                        except ValueError:
                            continue
                    else:
                        timestamp = None
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        trusted = True
                        if str(root)=='/mnt/nextcloud_backup/nextcloud':
                            info = os.fstat(child)
                            trusted = info.st_uid==0 and not info.st_mode & 0o022
                        checks = structure(child) if trusted else {}
                        complete = checks.get('COMPLETE', False) and not staging
                        manifest = checks.get('manifests/database.sha256', False)
                        try:
                            format_valid = checksum_metadata(child) if manifest and not staging else False
                        except OSError:
                            format_valid = False
                        valid = complete and all(checks.values()) and format_valid
                        errors = []
                        if not trusted:
                            errors.append('SNAPSHOT_ROOT_UNTRUSTED')
                        elif not staging and not all(checks.values()):
                            errors.append('SNAPSHOT_STRUCTURE_INCOMPLETE')
                        if manifest and not format_valid and not staging:
                            errors.append('CHECKSUM_MANIFEST_FORMAT_INVALID')
                        fixed = entry.name=='20261015-210345'
                        rows.append({'id':entry.name,'type':'staging' if staging else 'checkpoint',
                                     'timestamp':timestamp,'timestamp_source':'directory_name_host_local',
                                     'completeness':'complete' if complete else 'incomplete',
                                     'integrity_level':'structure_valid' if valid else 'present' if complete else 'unknown',
                                     'protected':fixed,'protection_reasons':['fixed_checkpoint'] if fixed else [],
                                     'structure_checks':checks,'db_checksum_manifest_present':manifest,
                                     'db_checksum_manifest_format_valid':format_valid,'db_checksum_verified':False,
                                     'restoration_verified':False,'metadata_errors':errors})
                    finally:
                        os.close(child)
        finally:
            os.close(directory)
    finally:
        os.close(base)
    rows.sort(key=lambda row:row['id'], reverse=True)
    completed = [row for row in rows if row['type']=='checkpoint' and row['completeness']=='complete']
    for row in completed[:2]:
        row['protected'] = True
        row['protection_reasons'].append('latest_two_completed')
    return rows


def summary(rows, generated_at, known=True):
    return {'format_version':2,'known':known,'generated_at':generated_at,
            'count':len(rows) if known else None,
            'complete_count':sum(row.get('completeness')=='complete' for row in rows) if known else None,
            'staging_count':sum(row.get('type')=='staging' for row in rows) if known else None,
            'protected_count':sum(row.get('protected') is True for row in rows) if known else None,
            'baseline_observed':any(row.get('id')=='baseline' for row in rows) if known else None,
            'fixed_checkpoint_observed':any(row.get('id')=='20261015-210345' for row in rows) if known else None}


def cached_metadata(payload, now):
    """Validate a retained inventory independently of the outer collector timestamp."""
    from datetime import datetime
    try:
        data = payload['data']
        state = data['inventory']
        rows = data['snapshots']
        age = (now-datetime.fromisoformat(state['generated_at'])).total_seconds()
        if (state.get('format_version') != 2 or state.get('known') is not True or not 0 <= age <= 180
                or not isinstance(rows,list) or len(rows)>500
                or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) for row in rows)):
            return None
        return rows, {**state,'cached':True}
    except (ValueError,KeyError,TypeError,AttributeError):
        return None
