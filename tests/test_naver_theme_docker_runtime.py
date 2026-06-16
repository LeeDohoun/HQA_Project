from src.ingestion import naver_theme


def test_chrome_binary_prefers_env(monkeypatch):
    monkeypatch.setenv("CHROME_BINARY", " /opt/chrome/chrome ")
    monkeypatch.setattr(naver_theme.shutil, "which", lambda _name: None)

    assert naver_theme._resolve_chrome_binary() == "/opt/chrome/chrome"


def test_chrome_binary_falls_back_to_docker_chromium(monkeypatch):
    monkeypatch.delenv("CHROME_BINARY", raising=False)
    original_exists = naver_theme.Path.exists
    monkeypatch.setattr(
        naver_theme.Path,
        "exists",
        lambda path: False if str(path) == "/opt/google/chrome/chrome" else original_exists(path),
    )

    def fake_which(name):
        return "/usr/bin/chromium" if name == "chromium" else None

    monkeypatch.setattr(naver_theme.shutil, "which", fake_which)

    assert naver_theme._resolve_chrome_binary() == "/usr/bin/chromium"


def test_chromedriver_prefers_env(monkeypatch):
    monkeypatch.setenv("CHROMEDRIVER", " /opt/chromedriver ")
    monkeypatch.setattr(
        naver_theme.shutil,
        "which",
        lambda _name: "/usr/bin/chromedriver",
    )

    assert naver_theme._resolve_chromedriver() == "/opt/chromedriver"


def test_chromedriver_falls_back_to_system_path(monkeypatch):
    monkeypatch.delenv("CHROMEDRIVER", raising=False)
    monkeypatch.setattr(naver_theme, "_resolve_selenium_cached_chromedriver", lambda: None)
    monkeypatch.setattr(
        naver_theme.shutil,
        "which",
        lambda name: "/usr/bin/chromedriver" if name == "chromedriver" else None,
    )
    monkeypatch.setattr(naver_theme, "_is_snap_launcher", lambda _path: False)

    assert naver_theme._resolve_chromedriver() == "/usr/bin/chromedriver"


def test_chromedriver_prefers_selenium_cache_over_snap_wrapper(tmp_path, monkeypatch):
    cache_driver = tmp_path / ".cache" / "selenium" / "chromedriver" / "linux64" / "149.0.1" / "chromedriver"
    cache_driver.parent.mkdir(parents=True)
    cache_driver.write_text("#!/bin/sh\n", encoding="utf-8")
    cache_driver.chmod(0o755)

    monkeypatch.delenv("CHROMEDRIVER", raising=False)
    monkeypatch.setattr(naver_theme.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        naver_theme.shutil,
        "which",
        lambda name: "/usr/bin/chromedriver" if name == "chromedriver" else None,
    )
    monkeypatch.setattr(naver_theme, "_is_snap_launcher", lambda path: path == "/usr/bin/chromedriver")

    assert naver_theme._resolve_chromedriver() == str(cache_driver)
