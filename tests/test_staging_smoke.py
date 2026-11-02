import os
import re
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv(
    "WEBCHEM_STAGING_URL",
    "https://beneficial-balance-staging.up.railway.app",
).rstrip("/")


def test_staging_homepage_and_auth_gate():
    """Smoke-test deployed page and guest auth UI; no credentials required."""
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

        response = page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45_000)
        assert response is not None, f"No HTTP response from {BASE_URL}"
        assert response.status == 200, f"Unexpected HTTP status: {response.status}"

        page.wait_for_selector("title", timeout=10_000)
        assert "WebChem" in page.title()

        # Main app and login controls should exist in a clean guest session.
        assert page.locator("#authGate").count() == 1
        assert page.locator("#gateEmail").count() >= 1
        assert page.locator("#gatePassword").count() >= 1
        assert page.locator("#gateSubmit").count() >= 1

        # Wait briefly for deferred app initialization and catch runtime exceptions.
        page.wait_for_timeout(2_000)
        browser.close()

    assert not browser_errors, "Uncaught browser errors:\n" + "\n".join(browser_errors)
