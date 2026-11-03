import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_pwa_manifest_and_icon_are_release_ready():
    manifest_path = ROOT / "manifest.webmanifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest.get("name"), "PWA name is missing"
    assert manifest.get("short_name"), "PWA short name is missing"
    assert manifest.get("display") == "standalone", "Expected installable standalone app"
    assert manifest.get("start_url"), "PWA start_url is missing"
    assert manifest.get("scope"), "PWA scope is missing"
    icons = manifest.get("icons", [])
    assert icons, "PWA manifest must declare at least one icon"

    for icon in icons:
        icon_path = (ROOT / icon["src"]).resolve()
        assert icon_path.is_relative_to(ROOT.resolve()), "PWA icon path must stay inside repository"
        assert icon_path.is_file(), f"Manifest icon does not exist: {icon['src']}"


def test_app_declares_manifest_and_service_worker():
    html = (ROOT / "index.html").read_text(encoding="utf-8").lower()
    assert re.search(r'<link[^>]+rel=["\']manifest["\'][^>]+href=["\']manifest\.webmanifest', html) or (
        'manifest.webmanifest' in html and 'rel="manifest"' in html
    ), "index.html must link the PWA manifest"
    assert "serviceworker" in html and "navigator.serviceworker" in html, (
        "index.html must register the service worker"
    )


def test_service_worker_caches_shell_but_never_private_api_data():
    sw = (ROOT / "sw.js").read_text(encoding="utf-8").lower()

    assert "manifest.webmanifest" in sw, "Offline shell should cache the PWA manifest"
    assert "index.html" in sw, "Offline shell should cache index.html"
    assert "req.method!=='get'" in sw or "req.method !== 'get'" in sw, (
        "Service worker must avoid caching non-GET requests"
    )
    for private_prefix in ("/api", "/auth", "/progress"):
        assert private_prefix in sw, f"Service worker must explicitly bypass {private_prefix} routes"
    assert "url.origin!==self.location.origin" in sw or "url.origin !== self.location.origin" in sw, (
        "Service worker should only cache same-origin requests"
    )
