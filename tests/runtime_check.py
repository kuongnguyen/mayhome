"""Loopback integration smoke against scripts/dev_server.py, never production."""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
base=os.environ.get('MHC_TEST_BASE','http://127.0.0.1:5010')
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
assert json.load(opener.open(base+'/healthz'))=={'alive':True}
page=opener.open(base+'/login').read().decode()
csrf=re.search(r'name="csrf" value="([^"]+)"',page)[1]
token=Path(os.environ['MHC_TEST_TOKEN_FILE']).read_text()
response=opener.open(base+'/login',data=urllib.parse.urlencode({'csrf':csrf,'token':token}).encode())
assert 'An tâm' in response.read().decode()
latencies=[]
for _ in range(30):
    start=time.perf_counter()
    assert opener.open(base+'/backups').status==200
    latencies.append((time.perf_counter()-start)*1000)
try:opener.open(base+'/api/v1/overview')
except urllib.error.HTTPError as error:
    assert error.code==503
    assert json.load(error)['meta']['freshness']=='unavailable'
else:raise AssertionError('No production collector should be present')
print(json.dumps({'server':'Gunicorn 2 workers / 2 threads, uid 1000, loopback',
 'authenticated_requests':30,'page_latency_ms_mean':round(statistics.mean(latencies),2),
 'page_latency_ms_max':round(max(latencies),2),'unavailable_collector_http':503,
 'production_runtime_acceptance':'NOT RUN'},indent=2))
