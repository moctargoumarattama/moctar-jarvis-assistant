"""PWA resources stay available over HTTP in tests and never cache NOC data."""
import struct

import pytest
from fastapi.testclient import TestClient

from config import Settings
from main import create_app


@pytest.fixture
def client():
    # No lifespan: static routes do not need collectors, Ollama or certificates.
    return TestClient(create_app(Settings()), base_url="https://testserver")


@pytest.mark.parametrize("path,media_type", [
    ("/", "text/html"),
    ("/manifest.webmanifest", "application/manifest+json"),
    ("/service-worker.js", "application/javascript"),
    ("/static/pwa.js", "application/javascript"),
    ("/static/pwa.css", "text/css"),
    ("/static/icons/moctar-192.png", "image/png"),
    ("/static/icons/moctar-512.png", "image/png"),
    ("/static/icons/moctar-logo-v2-180.png", "image/png"),
    ("/static/icons/moctar-logo-v2-192.png", "image/png"),
    ("/static/icons/moctar-logo-v2-512.png", "image/png"),
])
def test_pwa_resources_are_served_without_http_cache(client, path, media_type):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "no-store"
    assert "manifest-src 'self'" in response.headers["content-security-policy"]
    assert "worker-src 'self'" in response.headers["content-security-policy"]


def test_manifest_installation_scope_icons_and_worker_scope(client):
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["name"]
    assert manifest["display"] == "standalone"
    assert manifest["scope"] == "/"
    assert manifest["start_url"].startswith("/")
    icons = {icon["sizes"]: icon for icon in manifest["icons"]}
    for size in (192, 512):
        icon = icons[f"{size}x{size}"]
        assert icon["src"] == f"/static/icons/moctar-logo-v2-{size}.png"
        response = client.get(icon["src"])
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", response.content[16:24]) == (size, size)
    assert client.get("/service-worker.js").headers["service-worker-allowed"] == "/"


def test_ios_installation_uses_native_sized_logo(client):
    page = client.get("/").text
    assert 'rel="apple-touch-icon" sizes="180x180" href="/static/icons/moctar-logo-v2-180.png"' in page
    response = client.get("/static/icons/moctar-logo-v2-180.png")
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", response.content[16:24]) == (180, 180)


def test_pwa_icon_route_cannot_serve_other_files(client):
    assert client.get("/static/icons/config.py").status_code == 404
    assert client.get("/static/icons/unknown.png").status_code == 404
