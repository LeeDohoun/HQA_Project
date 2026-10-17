"""Audit-guarded invocation of the unchanged FW001 rehearsal CLI."""
import atexit
import json
import os
from pathlib import Path
import runpy
import sys

sys.dont_write_bytecode = True
root = os.path.abspath(sys.argv[sys.argv.index('--data-dir') + 1])
project = Path(__file__).resolve().parents[3]
counts = {'data_write_attempts': 0, 'env_read_attempts': 0,
          'protected_price_read_attempts': 0, 'network_attempts': 0,
          'data_read_opens': 0}

def path_string(value):
    return os.path.abspath(os.fsdecode(value)) if isinstance(value, (str, bytes, os.PathLike)) else ''

def in_data(path):
    return path == root or path.startswith(root + os.sep)

def guard(event, args):
    if event == 'open':
        path = path_string(args[0])
        mode, flags = args[1], args[2]
        writing = (isinstance(mode, str) and any(char in mode for char in 'wax+')) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if Path(path).name.startswith('.env'):
            counts['env_read_attempts'] += 1
            raise RuntimeError('replay forbids env reads')
        if in_data(path):
            if writing:
                counts['data_write_attempts'] += 1
                raise RuntimeError('replay forbids original-data writes')
            if '/market/krx_daily/' in path and '20260101' <= Path(path).stem <= '20260630':
                counts['protected_price_read_attempts'] += 1
                raise RuntimeError('replay forbids protected price contents')
            counts['data_read_opens'] += 1
    elif event in {'os.mkdir', 'os.remove', 'os.rename', 'os.link', 'os.symlink', 'os.rmdir',
                   'os.chmod', 'os.chown', 'os.utime', 'os.truncate'}:
        if any(in_data(path_string(arg)) for arg in args[:2]):
            counts['data_write_attempts'] += 1
            raise RuntimeError('replay forbids original-data mutations')
    elif event.startswith(('socket.connect', 'socket.getaddrinfo', 'socket.gethostby', 'socket.bind')):
        counts['network_attempts'] += 1
        raise RuntimeError('replay forbids networking')

sys.addaudithook(guard)
atexit.register(lambda: print(json.dumps({'audit': counts}, sort_keys=True)))
sys.path.insert(0, str(project))
runpy.run_path(str(project / 'scripts/forward/fw001.py'), run_name='__main__')
