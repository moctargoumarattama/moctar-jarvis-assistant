"""Native TLS selection never needs production certificates or a live server."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import config
from config import Settings
from main import app, run_server


def test_ssl_configuration_defaults_and_environment_override():
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings.from_env()
    assert settings.ssl_keyfile == (config.APP_ROOT / "certs" / "server.key").as_posix()
    assert settings.ssl_certfile == (config.APP_ROOT / "certs" / "server.crt").as_posix()
    with patch.dict(os.environ, {"NOC_SSL_KEYFILE": "C:/certs/local.key", "NOC_SSL_CERTFILE": "C:/certs/local.crt"}, clear=True):
        settings = Settings.from_env()
    assert settings.ssl_keyfile == "C:/certs/local.key"
    assert settings.ssl_certfile == "C:/certs/local.crt"


@pytest.mark.parametrize("key_present,cert_present", [(False, False), (True, False), (False, True)])
def test_missing_certificate_pair_keeps_http(tmp_path, key_present, cert_present):
    key = tmp_path / "server.key"
    cert = tmp_path / "server.crt"
    if key_present:
        key.write_text("test key", encoding="utf-8")
    if cert_present:
        cert.write_text("test certificate", encoding="utf-8")
    with patch("uvicorn.run") as launch:
        run_server(Settings(ssl_keyfile=str(key), ssl_certfile=str(cert)))
    launch.assert_called_once()
    assert launch.call_args.args == (app,)
    assert "ssl_keyfile" not in launch.call_args.kwargs
    assert "ssl_certfile" not in launch.call_args.kwargs


def test_existing_certificate_pair_enables_https(tmp_path):
    key = tmp_path / "server.key"
    cert = tmp_path / "server.crt"
    key.write_text("test key; uvicorn is mocked", encoding="utf-8")
    cert.write_text("test certificate; uvicorn is mocked", encoding="utf-8")
    with patch("uvicorn.run") as launch:
        run_server(Settings(host="0.0.0.0", port=5050, ssl_keyfile=str(key), ssl_certfile=str(cert)))
    assert launch.call_args.kwargs["ssl_keyfile"] == str(key)
    assert launch.call_args.kwargs["ssl_certfile"] == str(cert)
    assert launch.call_args.kwargs["host"] == "0.0.0.0"
    assert launch.call_args.kwargs["port"] == 5050
    assert launch.call_args.kwargs["workers"] == 1


def test_certificate_directories_do_not_enable_https(tmp_path):
    with patch("uvicorn.run") as launch:
        run_server(Settings(ssl_keyfile=str(tmp_path), ssl_certfile=str(tmp_path)))
    assert "ssl_keyfile" not in launch.call_args.kwargs


def test_inaccessible_certificate_keeps_http():
    with patch.object(Path, "is_file", side_effect=PermissionError("unreadable")), patch("uvicorn.run") as launch:
        run_server(Settings())
    assert "ssl_keyfile" not in launch.call_args.kwargs


def test_launcher_reads_environment_when_no_settings_provided(tmp_path):
    settings = Settings(ssl_keyfile=str(tmp_path / "absent.key"), ssl_certfile=str(tmp_path / "absent.crt"))
    with patch("main.Settings.from_env", return_value=settings) as from_env, patch("uvicorn.run") as launch:
        run_server()
    from_env.assert_called_once_with()
    assert "ssl_keyfile" not in launch.call_args.kwargs
