import pytest

from app.config import ConfigError, Settings, load_settings

FULL_ENV = {
    "OPENAI_API_KEY": "sk-secret-value",
    "OPENAI_BASE_URL": "https://example.test/v1",
    "MODEL": "test-model",
}


def test_all_present_returns_settings():
    assert load_settings(FULL_ENV) == Settings(
        openai_api_key="sk-secret-value",
        openai_base_url="https://example.test/v1",
        model="test-model",
    )


def test_one_missing_is_named():
    env = {k: v for k, v in FULL_ENV.items() if k != "MODEL"}
    with pytest.raises(ConfigError, match="MODEL"):
        load_settings(env)


def test_several_missing_named_in_one_message():
    with pytest.raises(ConfigError) as exc:
        load_settings({"OPENAI_BASE_URL": "https://example.test/v1"})
    assert "OPENAI_API_KEY" in str(exc.value)
    assert "MODEL" in str(exc.value)


def test_blank_value_counts_as_missing():
    with pytest.raises(ConfigError, match="MODEL"):
        load_settings({**FULL_ENV, "MODEL": "   "})


def test_secret_not_in_error_message():
    with pytest.raises(ConfigError) as exc:
        load_settings({**FULL_ENV, "MODEL": ""})
    assert "sk-secret-value" not in str(exc.value)


def test_secret_not_in_repr():
    assert "sk-secret-value" not in repr(load_settings(FULL_ENV))


def test_env_file_does_not_override_real_env_vars(monkeypatch, tmp_path):
    """Cloud Run sets real env vars; a .env file must never override them."""
    import dotenv

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-from-file\nOPENAI_BASE_URL=https://file.test/v1\nMODEL=from-file\n"
    )
    monkeypatch.setattr(
        "app.config.load_dotenv", lambda override: dotenv.load_dotenv(env_file, override=override)
    )
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL", "from-env")

    settings = load_settings()
    assert settings.model == "from-env"  # real env var wins
    assert settings.openai_base_url == "https://file.test/v1"  # missing ones come from .env


# --- Phase 16: PROMPT_VERSION -------------------------------------------------------------


# def test_prompt_version_defaults_to_v1():
#     from app.config import prompt_version

#     assert prompt_version({}) == "v1"
#     assert prompt_version({"PROMPT_VERSION": "  "}) == "v1"


def test_prompt_version_read_and_trimmed_without_api_key():
    from app.config import prompt_version

    assert prompt_version({"PROMPT_VERSION": " v2 "}) == "v2"  # no OPENAI_* needed


def test_prompt_version_from_process_env(monkeypatch):
    from app.config import prompt_version

    monkeypatch.setenv("PROMPT_VERSION", "v2")
    assert prompt_version() == "v2"
