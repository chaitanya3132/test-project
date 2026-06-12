import pytest

from bot.config import ConfigError, alpaca_credentials, load_config


def write_config(tmp_path, mode="paper"):
    p = tmp_path / "config.yaml"
    p.write_text(f"mode: {mode}\nrisk:\n  max_risk_per_trade_pct: 2.0\n")
    return p


def test_defaults_to_paper_and_reads_risk(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.is_paper
    assert cfg.risk.max_risk_per_trade_pct == 2.0
    assert cfg.risk.max_daily_loss_pct == 5.0  # default preserved


def test_live_mode_requires_env_interlock(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_ALLOW_LIVE", raising=False)
    with pytest.raises(ConfigError, match="BOT_ALLOW_LIVE"):
        load_config(write_config(tmp_path, mode="live"))
    monkeypatch.setenv("BOT_ALLOW_LIVE", "yes")
    cfg = load_config(write_config(tmp_path, mode="live"))
    assert not cfg.is_paper


def test_invalid_mode_rejected(tmp_path):
    with pytest.raises(ConfigError, match="mode"):
        load_config(write_config(tmp_path, mode="yolo"))


def test_credentials_from_env_only(monkeypatch):
    assert alpaca_credentials() == ("test-key", "test-secret")
    monkeypatch.delenv("APCA_API_KEY_ID")
    with pytest.raises(ConfigError, match="APCA_API_KEY_ID"):
        alpaca_credentials()
