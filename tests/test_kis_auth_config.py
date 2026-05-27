import importlib


def _reload_kis_auth(monkeypatch, *, app_key="", app_secret="", paper_key="", paper_secret="", account_no="", paper_account_no=""):
    monkeypatch.setenv("KIS_APP_KEY", app_key)
    monkeypatch.setenv("KIS_APP_SECRET", app_secret)
    monkeypatch.setenv("KIS_PAPER_APP_KEY", paper_key)
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", paper_secret)
    monkeypatch.setenv("KIS_ACCOUNT_NO", account_no)
    monkeypatch.setenv("KIS_PAPER_ACCOUNT_NO", paper_account_no)
    import src.utils.kis_auth as kis_auth

    return importlib.reload(kis_auth)


def test_paper_mode_does_not_fallback_to_prod_keys(monkeypatch):
    kis_auth = _reload_kis_auth(
        monkeypatch,
        app_key="prod_key",
        app_secret="prod_secret",
        paper_key="",
        paper_secret="",
    )
    assert kis_auth.KISConfig.get_app_key(paper=True) == ""
    assert kis_auth.KISConfig.get_app_secret(paper=True) == ""
    assert kis_auth.is_api_available(paper=True) is False


def test_paper_mode_uses_paper_keys_only(monkeypatch):
    kis_auth = _reload_kis_auth(
        monkeypatch,
        app_key="prod_key",
        app_secret="prod_secret",
        paper_key="paper_key",
        paper_secret="paper_secret",
    )
    assert kis_auth.KISConfig.get_app_key(paper=True) == "paper_key"
    assert kis_auth.KISConfig.get_app_secret(paper=True) == "paper_secret"
    assert kis_auth.is_api_available(paper=True) is True


def test_paper_account_no_does_not_fallback_to_prod_account(monkeypatch):
    kis_auth = _reload_kis_auth(
        monkeypatch,
        account_no="12345678-01",
        paper_account_no="",
    )
    assert kis_auth.KISConfig.get_account_no(paper=True) == ""
