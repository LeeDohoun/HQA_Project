import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path('/home/are01/src/HQA_Project/HQA_lh003')
label = sys.argv[1]
env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHON_DOTENV_DISABLED='1', RUN_KIS_LIVE_TESTS='0',
           OPENAI_API_KEY='offline-disabled', OPENAI_BASE_URL='http://127.0.0.1:9/v1',
           LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false',
           OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
           MALLOC_ARENA_MAX='2', MALLOC_TRIM_THRESHOLD_='131072',
           PYTHONPATH='/tmp/lh003-round21/guard:' + str(root))
log = Path('/tmp/lh003-round21') / (label + '.log')
command = [str(root / 'venv/bin/python'), '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *sys.argv[2:]]
started = time.monotonic()
max_rss = tree_peak = tree_pss_peak = 0
violation = None
with log.open('w') as out:
    p = subprocess.Popen(command, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    while p.poll() is None:
        entries = {}
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                entries[int(path.name)] = int(stat[1]), int(stat[21]) * os.sysconf('SC_PAGE_SIZE') / 2**20
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        family = {p.pid}
        while True:
            expanded = family | {pid for pid, (ppid, _) in entries.items() if ppid in family}
            if expanded == family:
                break
            family = expanded
        values = [entries[pid][1] for pid in family if pid in entries]
        max_rss = max(max_rss, max(values, default=0))
        tree_peak = max(tree_peak, sum(values))
        pss = 0
        for pid in family:
            try:
                rollup = Path('/proc', str(pid), 'smaps_rollup').read_text()
                pss += next(int(line.split()[1]) / 1024 for line in rollup.splitlines() if line.startswith('Pss:'))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        tree_pss_peak = max(tree_pss_peak, pss)
        if any(rss > 1450 for rss in values) or pss > 1450:
            violation = 'individual RSS or tree PSS exceeded 1450 MiB'
            os.killpg(p.pid, signal.SIGKILL)
            break
        time.sleep(.05)
    code = p.wait()
result = dict(command=command, exit_code=code, seconds=round(time.monotonic()-started, 3),
              max_process_rss_mib=round(max_rss, 3), process_tree_peak_rss_mib=round(tree_peak, 3),
              process_tree_peak_pss_mib=round(tree_pss_peak, 3), violation=violation, log=str(log), sandbox_poll=env.get('LH003_SANDBOX_POLL') == '1')
Path('/tmp/lh003-round21', label+'.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
print(log.read_text()[-20000:])
raise SystemExit(code)
