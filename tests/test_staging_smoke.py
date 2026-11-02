import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv(
    "WEBCHEM_STAGING_URL",
    "https://beneficial-balance-staging.up.railway.app",
).rstrip("/")
ARTIFACTS = Path("artifacts")


def test_staging_homepage_and_auth_gate():
    """Smoke-test deployed page and guest auth UI; no credentials required."""
    ARTIFACTS.mkdir(exist_ok=True)
    browser_errors = []
    failed_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url}: {request.failure}"
            ),
        )

        try:
            print(f"\n[staging-smoke] Target URL: {BASE_URL}")
            response = page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
            assert response is not None, f"No HTTP response from {BASE_URL}"

            print(f"[staging-smoke] HTTP status: {response.status}")
            print(f"[staging-smoke] Final URL: {page.url}")
            print(f"[staging-smoke] Content type: {response.headers.get('content-type', 'unknown')}")

            # Print page title and a short visible text sample for quick failure diagnosis.
            try:
                print(f"[staging-smoke] Page title: {page.title()}")
            except Exception as error:
                print(f"[staging-smoke] Could not read title: {error}")
            try:
                print(f"[staging-smoke] Body sample: {(page.locator('body').inner_text(timeout=5000))[:700]}")
            except Exception as error:
                print(f"[staging-smoke] Could not read body: {error}")

            assert response.status == 200, f"Unexpected HTTP status {response.status} at {page.url}"

            page.wait_for_selector("title", timeout=10_000)
            assert "WebChem" in page.title()

            assert page.locator("#authGate").count() == 1, "Missing #authGate"
            assert page.locator("#gateEmail").count() >= 1, "Missing #gateEmail"
            assert page.locator("#gatePassword").count() >= 1, "Missing #gatePassword"
            assert page.locator("#gateSubmit").count() >= 1, "Missing #gateSubmit"

            page.wait_for_timeout(2_000)
            assert not browser_errors, "Uncaught browser errors:\n" + "\n".join(browser_errors)
        except Exception:
            try:
                page.screenshot(path=str(ARTIFACTS / "staging-failure.png"), full_page=True)
                print(f"[staging-smoke] Screenshot saved: {ARTIFACTS / 'staging-failure.png'}")
            except Exception as screenshot_error:
                print(f"[staging-smoke] Screenshot capture failed: {screenshot_error}")
            if browser_errors:
                print("[staging-smoke] Browser errors:\n" + "\n".join(browser_errors))
            if failed_requests:
                print("[staging-smoke] Failed requests:\n" + "\n".join(failed_requests[:30]))
            raise
        finally:
            browser.close()
