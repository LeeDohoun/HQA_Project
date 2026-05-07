from src.ingestion import naver_theme


def test_chrome_binary_prefers_env(monkeypatch):
    monkeypatch.setenv("CHROME_BINARY", " /opt/chrome/chrome ")
    monkeypatch.setattr(naver_theme.shutil, "which", lambda _name: None)

    assert naver_theme._resolve_chrome_binary() == "/opt/chrome/chrome"


def test_chrome_binary_falls_back_to_docker_chromium(monkeypatch):
    monkeypatch.delenv("CHROME_BINARY", raising=False)

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
    monkeypatch.setattr(
        naver_theme.shutil,
        "which",
        lambda name: "/usr/bin/chromedriver" if name == "chromedriver" else None,
    )

    assert naver_theme._resolve_chromedriver() == "/usr/bin/chromedriver"
