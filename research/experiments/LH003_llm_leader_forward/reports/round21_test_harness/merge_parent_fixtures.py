"""Pin only disposable preregistration fixtures to the committed merge parent."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path('/home/are01/src/HQA_Project/HQA_lh003')
EXPECTED = 'ea3583936d39b07a4daa64abecf7a27eb9fb8295'


def pytest_configure(config):
    # Memoize pure tuple-returning dependency arithmetic, never calendar rules.
    from functools import lru_cache
    from exchange_calendars.pandas_extensions import korean_holiday
    korean_holiday.korean_lunar_to_solar = lru_cache(maxsize=8192)(korean_holiday.korean_lunar_to_solar)
    korean_holiday.korean_solar_to_lunar = lru_cache(maxsize=8192)(korean_holiday.korean_solar_to_lunar)
    for path in (ROOT / 'research/experiments').glob('*/preregistration.md'):
        content = subprocess.run(['git', '-C', str(ROOT), 'show',
            EXPECTED + ':' + path.relative_to(ROOT).as_posix()], check=True, capture_output=True).stdout
        if content != path.read_bytes():
            raise pytest.UsageError('pinned merge-parent preregistration differs: ' + str(path))


@pytest.fixture(autouse=True)
def committed_merge_parent_clone(monkeypatch):
    original = subprocess.run
    def run(command, *args, **kwargs):
        fixture_clone = (isinstance(command, list) and command[:2] == ['git', 'clone']
            and '--shared' in command and '--no-checkout' in command and str(ROOT) in command)
        if not fixture_clone:
            return original(command, *args, **kwargs)
        destination = Path(command[-1]).resolve()
        if not destination.is_relative_to('/tmp'):
            raise AssertionError('preregistration fixture destination must be temporary')
        result = original(command, *args, **kwargs)
        if result.returncode == 0:
            # This writes ONLY the newly cloned temporary fixture's HEAD. It
            # creates no commit/merge and checks out no environment/data files.
            original(['git', '-C', str(destination), 'update-ref', '--no-deref', 'HEAD', EXPECTED],
                     check=True, capture_output=True)
        return result
    monkeypatch.setattr(subprocess, 'run', run)
