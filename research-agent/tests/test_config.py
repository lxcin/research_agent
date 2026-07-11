from research_agent.config import load_config, get_model_config, get_model_name, get_api_key, get_data_dir
import os, yaml


def test_load_config_creates_default(temp_data_dir):
    config = load_config()
    assert "model" in config
    assert "name" in config["model"]


def test_get_model_config(temp_data_dir):
    cfg = get_model_config()
    assert "name" in cfg
    assert "provider" in cfg


def test_get_model_name(temp_data_dir):
    name = get_model_name()
    assert len(name) > 0


def test_config_writes_to_disk(temp_data_dir):
    load_config()
    config_path = get_data_dir() / "config.yml"
    assert config_path.exists()


def test_custom_config(temp_data_dir):
    config_path = get_data_dir() / "config.yml"
    config_path.write_text("""
model:
  provider: deepseek
  name: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY
  api_base: https://api.deepseek.com/v1
""", encoding="utf-8")
    cfg = get_model_config()
    assert cfg["provider"] == "deepseek"
    assert cfg["name"] == "deepseek-chat"
    assert cfg["api_base"] == "https://api.deepseek.com/v1"