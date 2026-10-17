"""Offline, read-only validation launcher for this merge; never opens a holdout."""
import atexit
import json
import os
from pathlib import Path
import runpy
import sys

WORKTREE = Path(__file__).resolve().parents[5]
DATA = Path('/home/are01/src/HQA_Project/HQA_Project/data').resolve()
REAL_ROOTS = (WORKTREE, DATA.parent)
counts = {'data_read_opens': 0, 'protected_price_read_attempts': 0,
          'denied_write_attempts': 0, 'env_open_attempts': 0, 'network_attempts': 0}


def path(value):
    return Path(os.path.abspath(os.fsdecode(value))).resolve() if isinstance(value, (str, bytes, os.PathLike)) else None


def deny_write(target):
    if target is None or target == Path(os.devnull):
        return
    protected = any(target == root / 'research/experiments' / name
                    for root in REAL_ROOTS for name in
                    ('registry.csv', 'registry.csv.lock', 'holdout_ledger.jsonl', 'holdout_ledger.jsonl.lock'))
    if target.is_relative_to(DATA) or not target.is_relative_to(WORKTREE) or protected:
        counts['denied_write_attempts'] += 1
        raise RuntimeError('validation forbids mutation: ' + str(target))


def guard(event, args):
    if event == 'open':
        target = path(args[0])
        if target is None:
            return
        if target.name.startswith('.env'):
            counts['env_open_attempts'] += 1
            raise RuntimeError('validation forbids .env opens')
        mode, flags = args[1:3]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if writing:
            deny_write(target)
        elif target.is_relative_to(DATA):
            if '/market/krx_daily/' in str(target) and '20260101' <= target.stem <= '20260630':
                counts['protected_price_read_attempts'] += 1
                raise RuntimeError('validation forbids protected price contents')
            counts['data_read_opens'] += 1
    elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.chown', 'os.utime', 'os.truncate'}:
        deny_write(path(args[0]))
    elif event in {'os.rename', 'os.link'}:
        deny_write(path(args[0]))
        deny_write(path(args[1]))
    elif event == 'os.symlink':
        deny_write(path(args[1]))
    elif event.startswith(('socket.connect', 'socket.getaddrinfo', 'socket.gethostby', 'socket.bind')):
        counts['network_attempts'] += 1
        raise RuntimeError('validation forbids network')


sys.dont_write_bytecode = True
sys.addaudithook(guard)
atexit.register(lambda: print(json.dumps({'audit': counts}, sort_keys=True), file=sys.stderr))
sys.path.insert(0, str(WORKTREE))
mode = sys.argv.pop(1)
if mode == 'tests':
    # Keep multiprocessing's shared-memory backing files in the worktree too.
    import multiprocessing.heap
    multiprocessing.heap.Arena._dir_candidates = [str(WORKTREE / 'research/experiments' /
        'AV002_avoidance_holdout_recovery/validation/merge/runtime_tmp')]
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:]))
if mode not in {'--preflight', '--reproduce'}:
    raise ValueError('only read-only AV002 modes are permitted')
sys.argv = ['av002', mode, '--data-dir', str(DATA)]
runpy.run_module('backtesting.experiments.av002', run_name='__main__')
