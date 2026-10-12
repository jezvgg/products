from dataclasses import MISSING, fields

from main import (
    DEFAULT_MCP_TOKEN,
    DEFAULT_MONGODB_DATABASE,
    DEFAULT_MONGODB_URI,
    DEFAULT_UI_PASSWORD,
    Settings,
)


def test_settings_defaults_are_plain_strings() -> None:
    """`slots=True` turns class attributes into descriptors, so guard them."""
    for field in fields(Settings):
        if field.default is not MISSING:
            assert isinstance(field.default, str)
    assert Settings().mongodb_database == DEFAULT_MONGODB_DATABASE


def test_from_env_uses_defaults_when_nothing_is_configured(monkeypatch) -> None:
    for name in ("MONGODB_URI", "MONGODB_DATABASE", "MCP_TOKEN", "UI_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings == Settings(
        mongodb_uri=DEFAULT_MONGODB_URI,
        mongodb_database=DEFAULT_MONGODB_DATABASE,
        mcp_token=DEFAULT_MCP_TOKEN,
        ui_password=DEFAULT_UI_PASSWORD,
    )


def test_from_env_reads_every_variable(monkeypatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://mongo:27017")
    monkeypatch.setenv("MONGODB_DATABASE", "cards")
    monkeypatch.setenv("MCP_TOKEN", "secret")
    monkeypatch.setenv("UI_PASSWORD", "hunter2")

    assert Settings.from_env() == Settings(
        mongodb_uri="mongodb://mongo:27017",
        mongodb_database="cards",
        mcp_token="secret",
        ui_password="hunter2",
    )
