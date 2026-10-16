"""Operator-only bounded evidence. No raw log messages, IDs, file paths or secrets printed."""
import json
import subprocess
from mayhome_center.collector import ProbeError
from mayhome_center.backup_history import parse_events, grammar_summary, reconstruct_jobs


def command(argv,limit):
    from mayhome_center.collector import run
    return run(argv,timeout=5,max_bytes=limit)


def main():
    report={}
    try:
        value=json.loads(command(['/usr/bin/docker','exec','--user','www-data','nextcloud-app','php','/var/www/html/occ','status','--output=json'],16384))
        if not isinstance(value,dict):raise ValueError('status shape')
        report['nextcloud']={key:value.get(key) if isinstance(value.get(key),bool) else None for key in ('installed','maintenance','needsDbUpgrade')}
    except (OSError,ValueError,ProbeError,subprocess.SubprocessError):report['nextcloud']={'state':'unavailable'}
    try:
        text=command(['/usr/bin/journalctl','--unit=nextcloud-auto-backup.service','--since=-30days','--lines=1000','--output=json','--no-pager'],524288)
        events=parse_events(text,1000);report['log_grammar']=grammar_summary(text,events)
        jobs=reconstruct_jobs(events)
        report['jobs']={'observed':len(jobs),'complete':sum(job['confidence']=='complete' for job in jobs),'partial':sum(job['confidence']=='partial' for job in jobs)}
    except (OSError,ValueError,ProbeError,subprocess.SubprocessError):report['log_grammar']={'state':'unavailable'}
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
