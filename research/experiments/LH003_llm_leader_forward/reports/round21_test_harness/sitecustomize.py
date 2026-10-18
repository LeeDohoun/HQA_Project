import os
import sys
from pathlib import Path

_main = Path('/home/are01/src/HQA_Project/HQA_Project')
_work = Path('/home/are01/src/HQA_Project/HQA_lh003')
_protected = [_main, _work / 'data', _work / '.git', _work / '.agents', _work / '.codex',
              _work / 'research/experiments/registry.csv', _work / 'research/experiments/holdout_ledger.jsonl',
              _work / 'research/experiments/LH003_llm_leader_forward/preregistration.md']

_resolved_protected = [p.resolve() for p in _protected]

def checked(path, write=False, dir_fd=None):
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    p = Path(os.fsdecode(path))
    if not p.is_absolute() and isinstance(dir_fd, int) and dir_fd >= 0:
        p = Path(os.readlink('/proc/self/fd/' + str(dir_fd))) / p
    p = p.absolute()
    if not write and p.name.startswith('.env') and not p.is_relative_to('/tmp'):
        raise RuntimeError('LH003 guard: environment file read forbidden')
    if write:
        resolved = p.resolve()
        if any(p == r or p.is_relative_to(r) for r in _protected) or any(resolved == r or resolved.is_relative_to(r) for r in _resolved_protected):
            raise RuntimeError('LH003 guard: protected write forbidden: ' + str(p))

def audit(event, args):
    if event == 'open':
        mode, flags = args[1:3]
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        checked(args[0], writing)
    elif event in ('os.remove', 'os.rmdir'):
        checked(args[0], True, args[1])
    elif event in ('os.mkdir', 'os.chmod'):
        checked(args[0], True, args[2])
    elif event == 'os.truncate':
        checked(args[0], True)
    elif event in ('os.rename', 'os.link'):
        if event == 'os.rename':
            checked(args[0], True, args[2])
        checked(args[1], True, args[3])
    elif event == 'os.symlink':
        checked(args[1], True, args[2])
    elif event in ('socket.connect', 'socket.getaddrinfo'):
        if event == 'socket.getaddrinfo' or isinstance(args[1], tuple):
            raise RuntimeError('LH003 guard: network forbidden')
    elif event == 'subprocess.Popen':
        command = args[1]
        if isinstance(command, (list, tuple)) and command and Path(str(command[0])).name in ('codex', 'curl', 'wget', 'ssh'):
            raise RuntimeError('LH003 guard: live subprocess forbidden')

sys.addaudithook(audit)

# The managed sandbox loses asyncio's cross-thread self-pipe wakeup. Bound epoll
# waits only for this test process; sockets/network calls remain blocked above.
if os.environ.get('LH003_SANDBOX_POLL') == '1':
    import selectors
    _epoll_select = selectors.EpollSelector.select
    def _bounded_epoll(self, timeout=None):
        return _epoll_select(self, .05 if timeout is None else min(timeout, .05))
    selectors.EpollSelector.select = _bounded_epoll
