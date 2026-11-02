import os
from pathlib import Path
from playwright.sync_api import sync_playwright

FRONTEND_URL = os.getenv("WEBCHEM_FRONTEND_URL", "http://127.0.0.1:8000/").rstrip("/") + "/"
API_URL = os.getenv("WEBCHEM_API_URL", "https://beneficial-balance-staging.up.railway.app").rstrip("/")
ARTIFACTS = Path("artifacts")


def test_staging_branch_frontend_and_auth_gate():
    """Run against the checked-out staging branch, not the production Pages site."""
    ARTIFACTS.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            response = page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            assert response is not None, f"No HTTP response from local staging frontend: {FRONTEND_URL}"
            assert response.status == 200, f"Staging branch frontend HTTP {response.status}"
            assert "WebChem" in page.title(), f"Unexpected title: {page.title()}"
            page.wait_for_selector("#authGate", timeout=15_000)
            for selector in ("#gateEmail", "#gatePassword", "#gateSubmit"):
                assert page.locator(selector).count() >= 1, f"Missing auth control: {selector}"
            page.wait_for_timeout(1200)
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


def test_backend_auth_endpoint():
    """Check a real backend route; backend root / is not expected to serve HTML."""
    with sync_playwright() as p:
        request = p.request.new_context()
        try:
            url = API_URL + "/api/auth/me"
            response = request.get(url, timeout=30_000)
            print(f"\n[backend] GET {url} -> HTTP {response.status}")
            assert response.status in (200, 401, 403), (
                f"Unexpected auth endpoint status {response.status}; route may be down/missing"
            )
        finally:
            request.dispose()


def test_account_switch_isolation_with_mocked_cloud_api():
    """Regression test account A/B isolation without real credentials or writes."""
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def mock_api(route):
            req = route.request
            url = req.url
            if url.endswith("/api/auth/me"):
                route.fulfill(status=401, content_type="application/json",
                                    body='{"success":false,"error":"Unauthenticated"}')
            elif url.endswith("/api/health"):
                route.fulfill(status=200, content_type="application/json",
                                    body='{"status":"ok"}')
            elif url.endswith("/api/progress") and req.method == "GET":
                token = req.headers.get("authorization", "")
                progress = {"completed": ["lesson-account-a"]} if token == "Bearer token-A" else {"completed": ["lesson-account-b"]}
                route.fulfill(status=200, content_type="application/json",
                                    body='{"success":true,"progress":' + __import__("json").dumps(progress) + '}')
            elif url.endswith("/api/progress") and req.method == "PUT":
                route.fulfill(status=200, content_type="application/json", body='{"success":true}')
            else:
                route.fulfill(status=200, content_type="application/json", body='{"success":true}')
        page.route("**/api/**", mock_api)
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            # Let startup auth restore settle before simulating authenticated sessions.
            page.wait_for_timeout(500)
            page.evaluate("""async () => {
              await gateSuccess({token:'token-A', user:{id:'account-A', username:'Account A'}});
              state.completed=['A-only-marker']; save();
              await gateSuccess({token:'token-B', user:{id:'account-B', username:'Account B'}});
              const keyA=userStateKey({id:'account-A'}), keyB=userStateKey({id:'account-B'});
              return {keyA,keyB,active:currentUser&&currentUser.id};
            }""")
            result = page.evaluate("""() => ({
              aKey:userStateKey({id:'account-A'}), bKey:userStateKey({id:'account-B'}),
              a:JSON.parse(localStorage.getItem(userStateKey({id:'account-A'}))||'{}'),
              b:JSON.parse(localStorage.getItem(userStateKey({id:'account-B'}))||'{}'),
              active:currentUser&&currentUser.id
            })""")
            assert result["active"] == "account-B", f"Expected account B active: {result}"
            assert result['aKey'].endswith('account-a') and result['bKey'].endswith('account-b'), f"Unexpected account storage keys: {result}"
            assert "A-only-marker" in result["a"].get("completed", []), f"Account A progress not stored separately: {result}"
            assert "lesson-account-a" in result["a"].get("completed", []), f"Account A cloud data missing: {result}"
            assert "lesson-account-a" not in result["b"].get("completed", []), f"Account A cloud data leaked into B: {result}"
            assert "lesson-account-b" in result["b"].get("completed", []), f"Account B cloud data missing: {result}"

            # Logout must clear auth and return to guest without leaking account B data.
            logout_result = page.evaluate("""() => {
              state.completed=['B-only-marker']; save();
              logout();
              return {
                active:currentUser,
                token:localStorage.getItem(AUTH_TOKEN_KEY),
                guest:JSON.parse(localStorage.getItem(GUEST_STATE_KEY)||'{}'),
                accountB:JSON.parse(localStorage.getItem(userStateKey({id:'account-B'}))||'{}')
              };
            }""")
            assert logout_result["active"] is None, f"User remained authenticated after logout: {logout_result}"
            assert logout_result["token"] is None, f"Auth token remained after logout: {logout_result}"
            assert "B-only-marker" in logout_result["accountB"].get("completed", []), f"Account B progress not saved: {logout_result}"
            assert "B-only-marker" not in logout_result["guest"].get("completed", []), f"Account B data leaked into guest state: {logout_result}"
            assert not errors, "Uncaught JS errors:\n" + "\n".join(errors)
        finally:
            browser.close()
