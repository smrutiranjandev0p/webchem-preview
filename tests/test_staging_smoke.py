import os
from pathlib import Path
from playwright.sync_api import sync_playwright

FRONTEND_URL = os.getenv(
    "WEBCHEM_FRONTEND_URL",
    "https://smrutiranjandev0p.github.io/webchem-preview/",
).rstrip("/") + "/"
API_URL = os.getenv(
    "WEBCHEM_API_URL",
    "https://beneficial-balance-staging.up.railway.app",
).rstrip("/")
ARTIFACTS = Path("artifacts")


def test_frontend_and_auth_gate():
    """Check the static frontend separately from the backend API."""
    ARTIFACTS.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            print(f"\n[frontend] URL: {FRONTEND_URL}")
            response = page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=45_000)
            assert response is not None, f"No HTTP response from frontend: {FRONTEND_URL}"
            print(f"[frontend] HTTP {response.status}; final URL={page.url}")
            assert response.status == 200, f"Frontend returned HTTP {response.status}: {page.url}"
            assert "WebChem" in page.title(), f"Unexpected page title: {page.title()}"
            page.wait_for_selector("#authGate", timeout=15_000)
            assert page.locator("#gateEmail").count() >= 1, "Missing login email field"
            assert page.locator("#gatePassword").count() >= 1, "Missing login password field"
            assert page.locator("#gateSubmit").count() >= 1, "Missing login submit button"
            page.wait_for_timeout(1500)
            assert not errors, "Uncaught JS errors:\n" + "\n".join(errors)
        except Exception:
            try:
                page.screenshot(path=str(ARTIFACTS / "frontend-failure.png"), full_page=True)
            except Exception:
                pass
            if errors:
                print("[frontend] JS errors:\n" + "\n".join(errors))
            raise
        finally:
            browser.close()


def test_backend_auth_endpoint_is_reachable():
    """The backend root may legitimately return 404; check a known auth API route instead."""
    with sync_playwright() as p:
        request = p.request.new_context()
        try:
            url = API_URL + "/api/auth/me"
            response = request.get(url, timeout=30_000)
            print(f"\n[backend] GET {url} -> HTTP {response.status}")
            # Unauthenticated /me is expected to reject auth, typically 401/403.
            # 200 is also valid if the deployment allows a public response.
            assert response.status in (200, 401, 403), (
                f"Auth API endpoint unexpected HTTP {response.status}; "
                f"backend may be unavailable or route missing. URL={url}"
            )
        finally:
            request.dispose()
