"""Isolate CH preflight guards from credential files in the code checkout."""
import pytest

from backtesting.experiments import ch001, ch004


@pytest.fixture(autouse=True)
def hermetic_ch_roots(tmp_path_factory, monkeypatch):
    # CH002/CH003 delegate their environment preflight to CH001.
    guard_root = tmp_path_factory.mktemp("ch_guard_root")

    def isolate(module):
        registration = module._registration

        def register(repo_root):
            # PROJECT_ROOT also anchors code hashes outside registration.
            with monkeypatch.context() as guard:
                guard.setattr(module, "PROJECT_ROOT", guard_root)
                return registration(repo_root)

        monkeypatch.setattr(module, "_registration", register)

    isolate(ch001)
    isolate(ch004)
    return guard_root
