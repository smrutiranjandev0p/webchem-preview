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


def test_local_progress_survives_page_reload():
    """Verify local guest progress persists across a browser refresh."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true,"progress":{}}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(700)
            page.evaluate("""() => {
              state.completed=['reload-persistence-marker'];
              save();
            }""")
            page.reload(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(500)
            result=page.evaluate("""() => ({
              saved:JSON.parse(localStorage.getItem(GUEST_STATE_KEY)||'{}'),
              loaded:state.completed
            })""")
            assert "reload-persistence-marker" in result["saved"].get("completed", []), f"Guest progress not persisted locally: {result}"
            assert "reload-persistence-marker" in result["loaded"], f"Guest progress not restored after refresh: {result}"
        finally:
            browser.close()


def test_cloud_load_failure_preserves_local_progress_and_retries():
    """A failed cloud read must not replace local progress; retries stay session-scoped."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        progress_get_calls = {"count": 0}

        def api_mock(route):
            url = route.request.url
            if url.endswith("/api/auth/me"):
                route.fulfill(status=401, content_type="application/json",
                              body='{"success":false,"error":"Unauthenticated"}')
            elif url.endswith("/api/progress") and route.request.method == "GET":
                progress_get_calls["count"] += 1
                route.fulfill(status=503, content_type="application/json",
                              body='{"success":false,"error":"temporary outage"}')
            else:
                route.fulfill(status=200, content_type="application/json",
                              body='{"success":true}')
        page.route("**/api/**", api_mock)
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(400)
            result = page.evaluate("""async () => {
              const user={id:'retry-user',username:'Retry User'};
              localStorage.setItem(userStateKey(user),JSON.stringify({...defaultState,completed:['local-safe-marker']}));
              localStorage.setItem(AUTH_TOKEN_KEY,'retry-token');
              currentUser=user;
              cloudSessionVersion++;
              await loadCloudProgress(user,{mergeGuest:false});
              return {
                progress:state.completed,
                stored:JSON.parse(localStorage.getItem(userStateKey(user))||'{}').completed,
                ready:cloudReady,
                dirty:cloudDirty
              };
            }""")
            assert "local-safe-marker" in result["progress"], f"Local progress lost on cloud failure: {result}"
            assert "local-safe-marker" in result["stored"], f"Stored local progress lost: {result}"
            assert result["ready"] is False, f"Cloud should remain unready after failed load: {result}"
            assert result["dirty"] is False, f"Failed cloud load should not mark data dirty: {result}"
            assert progress_get_calls["count"] >= 1, "Cloud GET route was not called"
        finally:
            browser.close()


def test_cloud_save_failure_keeps_dirty_and_recovers_on_retry():
    """A transient cloud PUT failure must retain dirty state and recover on retry."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        put_calls = {"count": 0}

        def api_mock(route):
            req = route.request
            if req.url.endswith("/api/auth/me"):
                route.fulfill(status=401, content_type="application/json",
                              body='{"success":false,"error":"Unauthenticated"}')
            elif req.url.endswith("/api/progress") and req.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body='{"success":true,"progress":{}}')
            elif req.url.endswith("/api/progress") and req.method == "PUT":
                put_calls["count"] += 1
                if put_calls["count"] == 1:
                    route.fulfill(status=503, content_type="application/json",
                                  body='{"success":false,"error":"temporary outage"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"success":true}')
            else:
                route.fulfill(status=200, content_type="application/json",
                              body='{"success":true}')
        page.route("**/api/**", api_mock)
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(400)
            result = page.evaluate("""async () => {
              const user={id:'save-retry-user',username:'Save Retry User'};
              await gateSuccess({token:'save-retry-token',user});
              await loadCloudProgress(user,{mergeGuest:false});
              state.completed=['cloud-save-retry-marker'];
              save();
              // First explicit flush hits the simulated 503.
              await syncProgressToCloud();
              const afterFailure={dirty:cloudDirty,ready:cloudReady};
              // Retry the pending dirty state; second PUT succeeds.
              await syncProgressToCloud();
              return {
                afterFailure,
                afterRetry:{dirty:cloudDirty,ready:cloudReady},
                saved:JSON.parse(localStorage.getItem(userStateKey(user))||'{}').completed
              };
            }""")
            assert result["afterFailure"]["ready"] is True, f"Cloud load should be ready before save: {result}"
            assert result["afterFailure"]["dirty"] is True, f"Failed PUT must retain dirty state: {result}"
            assert result["afterRetry"]["dirty"] is False, f"Successful retry should clear dirty state: {result}"
            assert result["afterRetry"]["ready"] is True, f"Cloud readiness unexpectedly lost: {result}"
            assert "cloud-save-retry-marker" in result["saved"], f"Local state missing after retry: {result}"
            assert put_calls["count"] >= 2, f"Expected failed PUT followed by retry, got {put_calls}"
        finally:
            browser.close()
