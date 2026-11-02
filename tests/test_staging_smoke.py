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
            # Gate fields are stage-rendered; verify login controls are attached after initial startup.
            page.wait_for_selector('#gateEmail', state="attached", timeout=10_000)
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
            page.evaluate("""async () => {const t=window.__WEBCHEM_TEST__;
              await t.gateSuccess({token:'token-A', user:{id:'account-A', username:'Account A'}});
              t.state.completed=['A-only-marker']; t.save();
              await t.gateSuccess({token:'token-B', user:{id:'account-B', username:'Account B'}});
              const keyA=t.userStateKey({id:'account-A'}), keyB=t.userStateKey({id:'account-B'});
              return {keyA,keyB,active:t.currentUser&&t.currentUser.id};
            }""")
            result = page.evaluate("""() => {const t=window.__WEBCHEM_TEST__; return {
              aKey:t.userStateKey({id:'account-A'}), bKey:t.userStateKey({id:'account-B'}),
              a:JSON.parse(localStorage.getItem(t.userStateKey({id:'account-A'}))||'{}'),
              b:JSON.parse(localStorage.getItem(t.userStateKey({id:'account-B'}))||'{}'),
              active:t.currentUser&&t.currentUser.id
            }}""")
            assert result["active"] == "account-B", f"Expected account B active: {result}"
            assert result['aKey'].endswith('account-a') and result['bKey'].endswith('account-b'), f"Unexpected account storage keys: {result}"
            assert "A-only-marker" in result["a"].get("completed", []), f"Account A progress not stored separately: {result}"
            assert "lesson-account-a" not in result["b"].get("completed", []), f"Account A cloud data leaked into B: {result}"
            assert "lesson-account-b" in result["b"].get("completed", []), f"Account B cloud data missing: {result}"

            # Logout must clear auth and return to guest without leaking account B data.
            logout_result = page.evaluate("""() => {const t=window.__WEBCHEM_TEST__;
              t.state.completed=['B-only-marker']; t.save();
              t.logout();
              return {
                active:t.currentUser,
                token:localStorage.getItem(t.AUTH_TOKEN_KEY),
                guest:JSON.parse(localStorage.getItem(t.GUEST_STATE_KEY)||'{}'),
                accountB:JSON.parse(localStorage.getItem(t.userStateKey({id:'account-B'}))||'{}')
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
            page.evaluate("""() => {const t=window.__WEBCHEM_TEST__;
              t.state.completed=['reload-persistence-marker'];
              t.save();
            }""")
            page.reload(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(500)
            result=page.evaluate("""() => {const t=window.__WEBCHEM_TEST__; return {
              saved:JSON.parse(localStorage.getItem(t.GUEST_STATE_KEY)||'{}'),
              loaded:t.state.completed
            }}""")
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
            result = page.evaluate("""async () => {const t=window.__WEBCHEM_TEST__;
              const user={id:'retry-user',username:'Retry User'};
              localStorage.setItem(t.userStateKey(user),JSON.stringify({...t.defaultState,completed:['local-safe-marker']}));
              localStorage.setItem(t.AUTH_TOKEN_KEY,'retry-token');
              t.currentUser=user;
              t.cloudSessionVersion++;
              await t.loadCloudProgress(user,{mergeGuest:false});
              return {
                progress:t.state.completed,
                stored:JSON.parse(localStorage.getItem(t.userStateKey(user))||'{}').completed,
                ready:t.cloudReady,
                dirty:t.cloudDirty
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
            result = page.evaluate("""async () => {const t=window.__WEBCHEM_TEST__;
              const user={id:'save-retry-user',username:'Save Retry User'};
              await t.gateSuccess({token:'save-retry-token',user});
              await t.loadCloudProgress(user,{mergeGuest:false});
              t.state.completed=['cloud-save-retry-marker'];
              t.save();
              // First explicit flush hits the simulated 503.
              await t.syncProgressToCloud();
              const afterFailure={dirty:t.cloudDirty,ready:t.cloudReady};
              // Retry the pending dirty t.state; second PUT succeeds.
              await t.syncProgressToCloud();
              return {
                afterFailure,
                afterRetry:{dirty:t.cloudDirty,ready:t.cloudReady},
                saved:JSON.parse(localStorage.getItem(t.userStateKey(user))||'{}').completed
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


def test_cloud_load_recovers_after_network_returns():
    """A later successful cloud read restores readiness without losing local items."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        get_calls = {"count": 0}

        def api_mock(route):
            req = route.request
            if req.url.endswith("/api/auth/me"):
                route.fulfill(status=401, content_type="application/json",
                              body='{"success":false,"error":"Unauthenticated"}')
            elif req.url.endswith("/api/progress") and req.method == "GET":
                get_calls["count"] += 1
                if get_calls["count"] == 2:
                    route.fulfill(status=503, content_type="application/json",
                                  body='{"success":false,"error":"offline"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"success":true,"progress":{"completed":["cloud-restored-marker"]}}')
            elif req.url.endswith("/api/progress") and req.method == "PUT":
                route.fulfill(status=200, content_type="application/json",
                              body='{"success":true}')
            else:
                route.fulfill(status=200, content_type="application/json",
                              body='{"success":true}')
        page.route("**/api/**", api_mock)
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(400)
            result = page.evaluate("""async () => {const t=window.__WEBCHEM_TEST__;
              const user={id:'network-recovery-user',username:'Network Recovery'};
              await t.gateSuccess({token:'network-recovery-token',user});
              const key=t.userStateKey(user);
              localStorage.setItem(key,JSON.stringify({...t.defaultState,completed:['local-offline-marker']}));
              t.state.completed=['local-offline-marker'];
              await t.loadCloudProgress(user,{mergeGuest:false});
              const afterOffline={ready:t.cloudReady,local:[...t.state.completed]};
              await t.loadCloudProgress(user,{mergeGuest:false});
              return {
                afterOffline,
                afterRecovery:{ready:t.cloudReady,items:[...t.state.completed]},
                stored:JSON.parse(localStorage.getItem(key)||'{}').completed
              };
            }""")
            assert result["afterOffline"]["ready"] is False, f"Expected cloud unavailable after first GET: {result}"
            assert "local-offline-marker" in result["afterOffline"]["local"], f"Local progress lost while offline: {result}"
            assert result["afterRecovery"]["ready"] is True, f"Cloud did not recover after successful GET: {result}"
            assert "local-offline-marker" in result["afterRecovery"]["items"], f"Local progress lost during recovery: {result}"
            assert "cloud-restored-marker" in result["afterRecovery"]["items"], f"Cloud progress not restored: {result}"
            assert "local-offline-marker" in result["stored"], f"Recovered local progress not persisted: {result}"
            assert get_calls["count"] >= 2, f"Expected failed then successful GET, got {get_calls}"
        finally:
            browser.close()


def test_stale_cloud_response_after_logout_is_ignored():
    """A delayed cloud response must not repopulate state after logout."""
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
            page.wait_for_timeout(400)
            result = page.evaluate("""async () => {const t=window.__WEBCHEM_TEST__;
              const user={id:'stale-response-user',username:'Stale Response'};
              localStorage.setItem(t.AUTH_TOKEN_KEY,'stale-token');
              t.currentUser=user;
              t.cloudReady=false;
              t.cloudDirty=false;
              t.cloudSessionVersion++;
              const originalFetch=window.fetch;
              let fetchSignal=null;
              // Deliberately ignore AbortSignal and return a late response to exercise
              // the session-version guard even when a transport cannot cancel.
              window.fetch=async (_url,options)=>{
                fetchSignal=options&&options.signal;
                await new Promise(resolve=>setTimeout(resolve,200));
                return {ok:true,json:async()=>({success:true,progress:{completed:['stale-cloud-marker']}})};
              };
              const pending=t.loadCloudProgress(user,{mergeGuest:false});
              // Logout cancels the in-flight request and invalidates its session token.
              t.logout();
              t.state.completed=['guest-after-logout'];
              t.save();
              await pending;
              window.fetch=originalFetch;
              return {
                currentUser:t.currentUser,
                ready:t.cloudReady,
                dirty:t.cloudDirty,
                guest:JSON.parse(localStorage.getItem(t.GUEST_STATE_KEY)||'{}').completed,
                token:localStorage.getItem(t.AUTH_TOKEN_KEY),
                signalAborted:!!(fetchSignal&&fetchSignal.aborted)
              };
            }""")
            assert result["currentUser"] is None, f"Logout session was unexpectedly restored: {result}"
            assert result["ready"] is False, f"Stale response changed cloud readiness: {result}"
            assert result["dirty"] is False, f"Stale response changed cloud dirty state: {result}"
            assert result["token"] is None, f"Logout token was restored: {result}"
            assert "guest-after-logout" in result["guest"], f"Guest progress was lost: {result}"
            assert "stale-cloud-marker" not in result["guest"], f"Old account data leaked into guest: {result}"
        finally:
            browser.close()


def test_stale_cloud_save_response_after_logout_is_ignored():
    """A late successful PUT must not alter the logged-out guest session."""
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
            page.wait_for_timeout(400)
            result = page.evaluate("""async () => {const t=window.__WEBCHEM_TEST__;
              const user={id:'pending-save-user',username:'Pending Save'};
              localStorage.setItem(t.AUTH_TOKEN_KEY,'pending-save-token');
              t.currentUser=user;
              t.cloudReady=true;
              t.cloudDirty=true;
              t.cloudSyncBusy=false;
              t.cloudSessionVersion++;
              t.cloudStateVersion++;
              t.state.completed=['account-before-logout'];
              localStorage.setItem(t.userStateKey(user),JSON.stringify(t.state));
              const originalFetch=window.fetch;
              let resolveFetch, fetchSignal=null, fetchStartedResolve;
              const fetchStarted=new Promise(resolve=>{fetchStartedResolve=resolve});
              // Model browser fetch cancellation: an aborted request rejects promptly.
              window.fetch=(url,options={})=>new Promise((resolve,reject)=>{
                // Capture only the progress PUT under test. Logout/rendering may
                // issue unrelated requests, which must not overwrite this signal.
                if(String(url).endsWith('/api/progress')&&options.method==='PUT'){
                  resolveFetch=resolve;
                  fetchSignal=options.signal||null;
                  fetchStartedResolve(); // Confirm the PUT reached fetch before logout.
                  if(fetchSignal){
                    if(fetchSignal.aborted)reject(new DOMException('Aborted','AbortError'));
                    else fetchSignal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
                  }
                }else{
                  resolve({ok:true,json:async()=>({success:true})});
                }
              });
              const pending=t.syncProgressToCloud();
              await fetchStarted;
              t.logout(); // Must abort the in-flight PUT and reset cloud flags.
              t.state.completed=['guest-after-save-logout'];
              t.save();
              // A simulated late success after abort must not affect the guest session.
              if(resolveFetch)resolveFetch({ok:true,json:async()=>({success:true})});
              await pending;
              window.fetch=originalFetch;
              return {
                currentUser:t.currentUser,
                ready:t.cloudReady,
                dirty:t.cloudDirty,
                busy:t.cloudSyncBusy,
                guest:JSON.parse(localStorage.getItem(t.GUEST_STATE_KEY)||'{}').completed,
                token:localStorage.getItem(t.AUTH_TOKEN_KEY),
                saveSignalAborted:!!(fetchSignal&&fetchSignal.aborted)
              };
            }""")
            assert result["currentUser"] is None, f"Logout session unexpectedly restored: {result}"
            assert result["ready"] is False and result["dirty"] is False, f"Old save altered guest cloud flags: {result}"
            assert result["busy"] is False, f"Cloud sync busy flag remained set after logout: {result}"
            assert result["saveSignalAborted"] is True, f"Logout did not abort the pending cloud save: {result}"
            assert result["token"] is None, f"Auth token remained after logout: {result}"
            assert "guest-after-save-logout" in result["guest"], f"Guest progress missing: {result}"
            assert "account-before-logout" not in result["guest"], f"Account progress leaked to guest: {result}"
        finally:
            browser.close()


def test_pending_account_a_save_cannot_mutate_account_b_after_switch():
    """A late save from account A must not alter account B after an account switch."""
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
            page.wait_for_timeout(400)
            result = page.evaluate("""async () => {
              const t=window.__WEBCHEM_TEST__;
              const accountA={id:'race-account-A',username:'Race Account A'};
              const accountB={id:'race-account-B',username:'Race Account B'};
              localStorage.setItem(t.AUTH_TOKEN_KEY,'race-token-A');
              t.currentUser=accountA;
              t.cloudReady=true;
              t.cloudDirty=true;
              t.cloudSyncBusy=false;
              t.cloudSessionVersion++;
              t.cloudStateVersion++;
              t.state={...t.defaultState,completed:['A-private-progress']};
              localStorage.setItem(t.userStateKey(accountA),JSON.stringify(t.state));
              const originalFetch=window.fetch;
              let resolveOldPut, putStartedResolve;
              const putStarted=new Promise(resolve=>{putStartedResolve=resolve});
              window.fetch=(url,options={})=>{
                if(String(url).endsWith('/api/progress')&&options.method==='PUT'){
                  putStartedResolve();
                  // Deliberately ignore abort and resolve late to exercise session guards.
                  return new Promise(resolve=>{resolveOldPut=resolve});
                }
                if(String(url).endsWith('/api/progress')&&options.method!=='PUT'){
                  return Promise.resolve({ok:true,json:async()=>({success:true,progress:{completed:['B-cloud-progress']}})});
                }
                return Promise.resolve({ok:true,json:async()=>({success:true})});
              };
              const oldSave=t.syncProgressToCloud();
              await putStarted;
              await t.gateSuccess({token:'race-token-B',user:accountB});
              const afterSwitch={
                user:t.currentUser&&t.currentUser.id,
                token:localStorage.getItem(t.AUTH_TOKEN_KEY),
                completed:[...t.state.completed],
                ready:t.cloudReady
              };
              if(resolveOldPut)resolveOldPut({ok:true,json:async()=>({success:true})});
              await oldSave;
              window.fetch=originalFetch;
              return {
                afterSwitch,
                afterLateSave:{
                  user:t.currentUser&&t.currentUser.id,
                  token:localStorage.getItem(t.AUTH_TOKEN_KEY),
                  completed:[...t.state.completed],
                  ready:t.cloudReady,
                  dirty:t.cloudDirty,
                  busy:t.cloudSyncBusy,
                  accountB:JSON.parse(localStorage.getItem(t.userStateKey(accountB))||'{}').completed,
                  accountA:JSON.parse(localStorage.getItem(t.userStateKey(accountA))||'{}').completed
                }
              };
            }""")
            assert result["afterSwitch"]["user"] == "race-account-B", f"Account switch did not activate B: {result}"
            assert result["afterSwitch"]["token"] == "race-token-B", f"Account B token missing after switch: {result}"
            assert "B-cloud-progress" in result["afterSwitch"]["completed"], f"Account B cloud state was not loaded: {result}"
            final = result["afterLateSave"]
            assert final["user"] == "race-account-B", f"Late account A save changed active user: {result}"
            assert final["token"] == "race-token-B", f"Late account A save changed active token: {result}"
            assert "B-cloud-progress" in final["completed"], f"Late account A save overwrote B progress: {result}"
            assert "A-private-progress" not in final["completed"], f"Account A progress leaked into B: {result}"
            assert "A-private-progress" in final["accountA"], f"Account A local progress unexpectedly disappeared: {result}"
            assert final["ready"] is True, f"Late account A save altered B cloud readiness: {result}"
            assert final["busy"] is False, f"Late account A save altered B sync state: {result}"
        finally:
            browser.close()


def test_full_backup_export_includes_study_tools_and_preferences():
    """Export should preserve progress plus locally stored study tools/profile preferences."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
              localStorage.setItem('webchem-study-planner-v1',JSON.stringify({day:'2026-11-02',minutes:45,goal:60}));
              localStorage.setItem('webchem-flashcards-v1',JSON.stringify({'What is pH?':3}));
              localStorage.setItem('webchem-profile-details',JSON.stringify({name:'Chem Learner'}));
              localStorage.setItem('webchem-settings',JSON.stringify({appLanguage:'English',quizLanguage:'Hindi'}));
              localStorage.setItem('webchemQuizHistory',JSON.stringify([{percent:90}]));
              localStorage.setItem('webchemQuizMistakes',JSON.stringify(['q-1']));
              const oldCreate=URL.createObjectURL;
              URL.createObjectURL=blob=>{window.__backupBlob=blob;return 'blob:webchem-test'};
              const oldClick=HTMLAnchorElement.prototype.click;
              HTMLAnchorElement.prototype.click=function(){};
              try{
                exportProgress();
                const raw=await window.__backupBlob.text();
                return JSON.parse(raw);
              }finally{
                URL.createObjectURL=oldCreate;
                HTMLAnchorElement.prototype.click=oldClick;
              }
            }""")
            assert result["version"] == 2, f"Expected version 2 full backup: {result}"
            assert result["state"], f"Core progress missing from backup: {result}"
            assert result["studyPlanner"]["minutes"] == 45, f"Study planner data missing: {result}"
            assert "What is pH?" in result["flashcards"], f"Flashcard recall data missing: {result}"
            assert result["profile"]["name"] == "Chem Learner", f"Profile data missing: {result}"
            assert result["preferences"]["quizLanguage"] == "Hindi", f"Preferences missing: {result}"
            assert result["quizHistory"][0]["percent"] == 90, f"Quiz history missing: {result}"
            assert result["quizMistakes"] == ["q-1"], f"Quiz mistakes missing: {result}"
        finally:
            browser.close()



def test_invalid_backup_does_not_destroy_existing_quiz_data():
    """Malformed optional backup fields must be rejected without clearing local history."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
              const priorHistory=[{score:88}], priorMistakes=['keep-me'];
              localStorage.setItem('webchemQuizHistory',JSON.stringify(priorHistory));
              localStorage.setItem('webchemQuizMistakes',JSON.stringify(priorMistakes));
              const alerts=[]; window.alert=message=>alerts.push(String(message));
              window.confirm=()=>true;
              const file=new File([JSON.stringify({version:2,state:{completed:[]},quizHistory:'not-an-array'})],
                'broken-backup.json',{type:'application/json'});
              importProgressBackup({files:[file],value:'broken-backup.json'});
              await new Promise(resolve=>setTimeout(resolve,80));
              return {alerts,history:JSON.parse(localStorage.getItem('webchemQuizHistory')||'[]'),
                mistakes:JSON.parse(localStorage.getItem('webchemQuizMistakes')||'[]')};
            }""")
            assert any("Invalid quiz history" in msg for msg in result["alerts"]), f"Malformed backup was not rejected: {result}"
            assert result["history"] == [{"score": 88}], f"Existing quiz history was overwritten: {result}"
            assert result["mistakes"] == ["keep-me"], f"Existing quiz mistakes were overwritten: {result}"
        finally:
            browser.close()


def test_legacy_backup_keeps_newer_local_study_data():
    """Importing an older v1 backup should not erase data absent from that backup."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
              localStorage.setItem('webchemQuizHistory',JSON.stringify([{score:91}]));
              localStorage.setItem('webchemQuizMistakes',JSON.stringify(['old-local-mistake']));
              window.alert=()=>{}; window.confirm=()=>true;
              const file=new File([JSON.stringify({completed:['lesson-1'],points:5})],
                'legacy-backup.json',{type:'application/json'});
              importProgressBackup({files:[file],value:'legacy-backup.json'});
              await new Promise(resolve=>setTimeout(resolve,80));
              return {history:JSON.parse(localStorage.getItem('webchemQuizHistory')||'[]'),
                mistakes:JSON.parse(localStorage.getItem('webchemQuizMistakes')||'[]'),
                completed:window.__WEBCHEM_TEST__.state.completed};
            }""")
            assert result["history"] and result["history"][0] == {"score": 91}, f"Legacy backup erased newer quiz history: {result}"
            assert result["mistakes"] == ["old-local-mistake"], f"Legacy backup erased newer mistakes: {result}"
            assert "lesson-1" in result["completed"], f"Legacy core progress was not imported: {result}"
        finally:
            browser.close()


def test_backup_import_confirmation_shows_backup_summary_and_can_cancel():
    """Show metadata/counts before replacing data, and preserve data when cancelled."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
              localStorage.setItem('webchemQuizHistory',JSON.stringify([{score:77}]));
              const prompts=[]; window.confirm=message=>{prompts.push(String(message));return false};
              window.alert=()=>{};
              const backup={version:2,exportedAt:'2026-11-02T12:00:00Z',
                state:{completed:['new-lesson']},quizHistory:[{score:99}],quizMistakes:['m1'],
                studyPlanner:{minutes:20}};
              const file=new File([JSON.stringify(backup)],'backup.json',{type:'application/json'});
              importProgressBackup({files:[file],value:'backup.json'});
              await new Promise(resolve=>setTimeout(resolve,80));
              return {prompt:prompts[0],history:JSON.parse(localStorage.getItem('webchemQuizHistory')||'[]'),
                completed:window.__WEBCHEM_TEST__.state.completed};
            }""")
            for expected in ("Backup version: 2", "Quiz history: 1 records", "Quiz mistakes: 1 records", "studyPlanner"):
                assert expected in result["prompt"], f"Backup confirmation missing {expected}: {result}"
            assert result["history"] == [{"score": 77}], f"Cancelled import overwrote existing data: {result}"
            assert "new-lesson" not in result["completed"], f"Cancelled import changed progress: {result}"
        finally:
            browser.close()



def test_full_backup_import_restores_all_v2_data():
    """A valid v2 backup should restore core progress and each included study dataset."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
              const backup={
                version:2, exportedAt:'2026-11-02T12:00:00Z',
                state:{completed:['backup-lesson'],points:125,correct:8,calculations:4},
                quizHistory:[{topic:'Stoichiometry',percent:95,correct:19,total:20}],
                quizMistakes:['missed-q-3'],
                studyPlanner:{day:'2026-11-02',minutes:35,goal:50},
                flashcards:{'What is molarity?':2},
                profile:{name:'Backup Learner'},
                preferences:{quizLanguage:'Hindi',appLanguage:'English'}
              };
              window.confirm=()=>true; window.alert=()=>{};
              const file=new File([JSON.stringify(backup)],'complete-backup.json',{type:'application/json'});
              importProgressBackup({files:[file],value:'complete-backup.json'});
              await new Promise(resolve=>setTimeout(resolve,100));
              const read=k=>JSON.parse(localStorage.getItem(k)||'null');
              return {
                state:window.__WEBCHEM_TEST__.state,
                history:read('webchemQuizHistory'),
                mistakes:read('webchemQuizMistakes'),
                planner:read('webchem-study-planner-v1'),
                flashcards:read('webchem-flashcards-v1'),
                profile:read('webchem-profile-details'),
                preferences:read('webchem-settings')
              };
            }""")
            assert "backup-lesson" in result["state"]["completed"], f"Core progress not restored: {result}"
            assert result["state"]["points"] == 125, f"Progress points not restored: {result}"
            assert any(x.get("topic") == "Stoichiometry" and x.get("percent") == 95
                       for x in result["history"]), f"Quiz history not restored: {result}"
            assert result["mistakes"] == ["missed-q-3"], f"Quiz mistakes not restored: {result}"
            assert result["planner"]["minutes"] == 35, f"Study planner not restored: {result}"
            assert result["flashcards"]["What is molarity?"] == 2, f"Flashcards not restored: {result}"
            assert result["profile"]["name"] == "Backup Learner", f"Profile not restored: {result}"
            assert result["preferences"]["quizLanguage"] == "Hindi", f"Preferences not restored: {result}"
        finally:
            browser.close()



def test_backup_import_rejects_unknown_future_version_without_changes():
    """Do not silently interpret a backup from a newer incompatible format."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            result = page.evaluate("""async () => {
              const prior=window.__WEBCHEM_TEST__.state.completed.slice();
              localStorage.setItem('webchemQuizHistory',JSON.stringify([{score:73}]));
              const alerts=[]; window.alert=message=>alerts.push(String(message)); window.confirm=()=>true;
              const backup={version:3,state:{completed:['future-data']},quizHistory:[{score:100}]};
              const file=new File([JSON.stringify(backup)],'future-backup.json',{type:'application/json'});
              importProgressBackup({files:[file],value:'future-backup.json'});
              await new Promise(resolve=>setTimeout(resolve,80));
              return {alerts,prior,completed:window.__WEBCHEM_TEST__.state.completed,
                history:JSON.parse(localStorage.getItem('webchemQuizHistory')||'[]')};
            }""")
            assert any("Unsupported backup version" in msg for msg in result["alerts"]), f"Future backup version was not rejected: {result}"
            assert "future-data" not in result["completed"], f"Future backup modified progress: {result}"
            assert result["history"] == [{"score": 73}], f"Future backup modified local quiz history: {result}"
        finally:
            browser.close()

def test_mobile_layout_has_no_page_level_horizontal_overflow():
    """Check auth-gated layout on narrow phones, regular phones, and tablet widths."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        errors = []
        try:
            for width, height in [(320, 720), (360, 800), (390, 844), (430, 932), (768, 1024), (800, 900), (820, 1180), (1024, 768)]:
                page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/**", lambda route: route.fulfill(
                    status=401 if route.request.url.endswith("/api/auth/me") else 200,
                    content_type="application/json",
                    body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
                ))
                try:
                    page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_selector("#authGate", timeout=15_000)
                    page.wait_for_timeout(300)
                    dimensions = page.evaluate("""() => ({
                      viewport: document.documentElement.clientWidth,
                      document: document.documentElement.scrollWidth,
                      body: document.body.scrollWidth,
                      gate: document.querySelector('#authGate')?.getBoundingClientRect().width
                    })""")
                    assert dimensions["document"] <= dimensions["viewport"] + 1, (
                        f"Document horizontal overflow at {width}px: {dimensions}"
                    )
                    assert dimensions["body"] <= dimensions["viewport"] + 1, (
                        f"Body horizontal overflow at {width}px: {dimensions}"
                    )
                    gate = page.locator("#authGate")
                    assert gate.is_visible(), f"Auth gate is not visible at {width}px"
                except Exception:
                    try:
                        page.screenshot(path=str(ARTIFACTS / f"mobile-overflow-{width}px.png"), full_page=True)
                    except Exception:
                        pass
                    raise
                finally:
                    page.close()
            assert not errors, "Uncaught responsive JS errors:\\n" + "\\n".join(errors)
        finally:
            browser.close()

def test_mobile_navigation_drawer_opens_and_closes_accessibly():
    """Mobile menu must toggle state, expose ARIA state, and close via Escape/scrim."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            trigger = page.locator("#menuTrigger")
            scrim = page.locator("#drawerScrim")
            page.wait_for_selector("#authGate", timeout=15_000)
            # The auth gate is a modal overlay; simulate a successful test login
            # so it no longer intercepts the navigation button's pointer events.
            page.evaluate("""async () => {
              const t = window.__WEBCHEM_TEST__;
              await t.gateSuccess({token:'drawer-test-token', user:{id:'drawer-test-user', username:'Drawer Test'}});
            }""")
            page.wait_for_function("!document.querySelector('#authGate') || getComputedStyle(document.querySelector('#authGate')).display === 'none' || document.querySelector('#authGate').hidden", timeout=10_000)
            assert trigger.is_visible(), "Mobile navigation trigger should be visible"
            assert trigger.get_attribute("aria-expanded") == "false"

            trigger.click()
            assert page.locator("body").evaluate("(el) => el.classList.contains('drawer-open')")
            assert trigger.get_attribute("aria-expanded") == "true"
            assert trigger.get_attribute("aria-label") == "Close navigation"
            assert scrim.get_attribute("aria-hidden") == "false"

            page.keyboard.press("Escape")
            assert not page.locator("body").evaluate("(el) => el.classList.contains('drawer-open')")
            assert trigger.get_attribute("aria-expanded") == "false"
            assert scrim.get_attribute("aria-hidden") == "true"

            trigger.click()
            scrim.click(position={"x": 365, "y": 400})
            assert not page.locator("body").evaluate("(el) => el.classList.contains('drawer-open')")
            assert trigger.get_attribute("aria-expanded") == "false"
        finally:
            browser.close()


def test_cbt_quiz_navigation_mark_clear_and_submission():
    """Exercise real CBT controls: answer, clear, question palette, mark, and submit."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        browser_errors = []
        page.on("pageerror", lambda error: browser_errors.append(f"{error}\nSTACK: {error.stack}"))
        page.on("console", lambda message: browser_errors.append(f"console.{message.type}: {message.text}") if message.type == "error" else None)
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true,"progress":{}}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("#authGate", timeout=15_000)
            page.evaluate("""async () => {
              await window.__WEBCHEM_TEST__.gateSuccess({
                token:'cbt-smoke-token',user:{id:'cbt-smoke-user',username:'CBT Smoke'}
              });
              navigate('quiz');
            }""")
            page.wait_for_selector("#quizTopic", timeout=10_000)
            page.locator("#quizTopic").select_option(label="All topics")
            page.locator("#quizCount").select_option("10")
            page.locator("#quizMode").select_option("cbt")
            page.locator("#quizMinutes").select_option("5")
            # Stub native confirm in this deterministic smoke test so both the
            # start and submit confirmations are accepted without dialog races.
            page.evaluate("window.confirm = () => true")
            page.locator("#startQuizBtn").click()
            try:
                page.wait_for_selector(".quizq", timeout=10_000)
            except Exception as exc:
                snapshot = page.evaluate("""() => ({area:document.querySelector("#quizArea")?.innerText, topic:document.querySelector("#quizTopic")?.value, mode:document.querySelector("#quizMode")?.value, button:document.querySelector("#startQuizBtn")?.outerHTML})""")
                raise AssertionError(f"CBT did not start; browser errors={browser_errors}; page={snapshot}") from exc

            # CBT answer selection/clearing must not award points before final submission.
            points_before = page.evaluate("window.__WEBCHEM_TEST__.state.points")
            correct_before = page.evaluate("window.__WEBCHEM_TEST__.state.correct")
            page.locator("[data-answer]").first.click()
            assert page.locator(".quiz-palette-btn.answered").count() == 1
            assert page.evaluate("window.__WEBCHEM_TEST__.state.points") == points_before, "CBT points must be awarded only at final submission"
            assert page.evaluate("window.__WEBCHEM_TEST__.state.correct") == correct_before, "CBT correct-count must be finalized only once"
            page.locator("#clearAnswer").click()
            assert page.locator(".quiz-palette-btn.answered").count() == 0
            assert page.evaluate("window.__WEBCHEM_TEST__.state.points") == points_before, "Clearing a CBT answer must not alter points"

            # Mark for review, move to question 2, then return through palette.
            page.locator("#markQuestion").click()
            assert page.locator(".quiz-palette-btn.marked").count() == 1
            page.locator("#nextExam").click()
            assert "Question 2 of" in page.locator(".quiz-run-head h3").inner_text()
            page.locator('[data-jump="0"]').click()
            assert "Question 1 of" in page.locator(".quiz-run-head h3").inner_text()
            # Re-answer Q1 before submitting to verify score and unanswered counts.
            page.locator("[data-answer]").first.click()
            assert page.locator(".quiz-palette-btn.answered").count() == 1

            page.locator("#submitExam").click()
            try:
                page.wait_for_selector(".quiz-result-panel", timeout=10_000)
            except Exception as exc:
                snapshot = page.evaluate("""() => ({area:document.querySelector("#quizArea")?.innerText, history:localStorage.getItem("webchemQuizHistory"), state:window.state?JSON.stringify(window.state):"not-global"})""")
                raise AssertionError(f"CBT submit failed; browser errors={browser_errors}; page={snapshot}") from exc
            assert "Quiz complete!" in page.locator(".quiz-result-panel").inner_text()
            history = page.evaluate("JSON.parse(localStorage.getItem('webchemQuizHistory')||'[]')")
            assert history and history[-1]["mode"] == "cbt", f"CBT attempt not recorded: {history}"
            assert history[-1]["total"] == 10, f"Unexpected quiz total in history: {history[-1]}"
            assert history[-1]["correct"] + history[-1]["wrong"] == 1, f"One response should be scored: {history[-1]}"
            assert history[-1]["unanswered"] == 9, f"Expected nine unanswered questions: {history[-1]}"
            assert 0 <= history[-1]["percent"] <= 100, f"Invalid CBT percentage: {history[-1]}"
            assert page.evaluate("window.__WEBCHEM_TEST__.state.points") == points_before + history[-1]["correct"] * 5, "CBT should award exactly five points per correct answer, once"
            assert page.evaluate("window.__WEBCHEM_TEST__.state.correct") == correct_before + history[-1]["correct"], "CBT correct-answer count should increment exactly once per correct answer"
            assert page.locator(".quiz-review-row").count() == 10, "Result should show one review row per question"
            export_button = page.get_by_role("button", name="Export result (CSV)")
            assert export_button.is_visible()
            with page.expect_download() as download_info:
                export_button.click()
            download = download_info.value
            csv_bytes = download.path().read_bytes()
            assert csv_bytes.startswith(b"\xef\xbb\xbf"), "CSV should include UTF-8 BOM for spreadsheet compatibility"
            csv_text = csv_bytes.decode("utf-8-sig")
            csv_lines = csv_text.splitlines()
            assert csv_lines[0] == "Date,Topic,Mode,Percent,Correct,Wrong,Unanswered,Total"
            assert len(csv_lines) == 2, f"CSV should contain header and one attempt row: {csv_lines}"
            assert csv_lines[1].split(",")[2] == "cbt", f"CSV should preserve CBT mode: {csv_lines[1]}"
        finally:
            browser.close()


def test_chemistry_calculators_validate_inputs_and_allow_zero_amounts():
    """Calculator regressions: zero solute/component is valid; blank/invalid values are rejected."""
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", lambda route: route.fulfill(
            status=401 if route.request.url.endswith("/api/auth/me") else 200,
            content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}' if route.request.url.endswith("/api/auth/me") else '{"success":true,"progress":{}}'
        ))
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("#authGate", timeout=15_000)
            page.evaluate("""async () => {
              await window.__WEBCHEM_TEST__.gateSuccess({
                token:'calculator-smoke-token',user:{id:'calculator-smoke-user',username:'Calculator Smoke'}
              });
              navigate('tools');
            }""")

            # Molarity: zero moles is valid when the final volume is positive.
            page.locator('[data-tool="molarity"]').click()
            page.locator("#molAmount").fill("0")
            page.locator("#molVolume").fill("500")
            page.locator("button", has_text="Calculate molarity").click()
            assert "0.000000 mol/L" in page.locator("#toolResult").inner_text()

            # Blank values must not be silently interpreted as zero.
            before = page.evaluate("window.__WEBCHEM_TEST__.state.calculations")
            page.locator("#molAmount").fill("")
            page.locator("button", has_text="Calculate molarity").click()
            assert "Enter a number" in page.locator("#toast").inner_text()
            assert page.evaluate("window.__WEBCHEM_TEST__.state.calculations") == before

            # Dilution: a zero target concentration correctly requires zero stock.
            page.locator('[data-tool="dilution"]').click()
            page.locator("#dC1").fill("2")
            page.locator("#dC2").fill("0")
            page.locator("#dV2").fill("250")
            page.locator("button", has_text="Calculate stock volume").click()
            assert "0.000 mL stock" in page.locator("#toolResult").inner_text()

            # Mass percent: zero component mass is a valid 0% result.
            page.locator('[data-tool="percent"]').click()
            page.locator("#pctPart").fill("0")
            page.locator("#pctTotal").fill("100")
            page.locator("button", has_text="Calculate mass percent").click()
            assert "0.000% by mass" in page.locator("#toolResult").inner_text()

            # Formula parsing: standard nested formulas work; zero subscripts are rejected.
            page.locator('[data-tool="molar"]').click()
            page.locator("#formula").fill("Ca(OH)2")
            page.locator("button", has_text="Calculate molar mass").click()
            assert "74.0920 g/mol" in page.locator("#toolResult").inner_text()
            before = page.evaluate("window.__WEBCHEM_TEST__.state.calculations")
            page.locator("#formula").fill("H0")
            page.locator("button", has_text="Calculate molar mass").click()
            assert "subscripts must be positive" in page.locator("#toast").inner_text()
            assert page.evaluate("window.__WEBCHEM_TEST__.state.calculations") == before

            assert not errors, "Uncaught JS errors:\\n" + "\\n".join(errors)
        finally:
            browser.close()


def test_lesson_completion_awards_points_once_and_persists_after_reload():
    """Completing a real lesson persists; repeat completion and unknown IDs cannot farm points."""
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
            page.wait_for_selector("#authGate", timeout=15_000)
            page.wait_for_timeout(300)

            first = page.evaluate("""() => {
              const t=window.__WEBCHEM_TEST__;
              t.state={...t.defaultState,completed:[],points:0};
              completeLesson('atomic');
              const afterFirst={completed:[...t.state.completed],points:t.state.points};
              completeLesson('atomic');
              const afterRepeat={completed:[...t.state.completed],points:t.state.points};
              completeLesson('not-a-real-lesson');
              return {afterFirst,afterRepeat,afterInvalid:{completed:[...t.state.completed],points:t.state.points}};
            }""")
            assert first["afterFirst"] == {"completed": ["atomic"], "points": 10}, f"Lesson completion did not award expected progress: {first}"
            assert first["afterRepeat"] == first["afterFirst"], f"Repeated completion awarded duplicate points: {first}"
            assert first["afterInvalid"] == first["afterFirst"], f"Unknown lesson mutated progress: {first}"

            page.reload(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("#authGate", timeout=15_000)
            page.wait_for_timeout(350)
            restored = page.evaluate("""() => {
              const t=window.__WEBCHEM_TEST__;
              return {completed:t.state.completed,points:t.state.points,
                guest:JSON.parse(localStorage.getItem(t.GUEST_STATE_KEY)||'{}')};
            }""")
            assert "atomic" in restored["completed"], f"Lesson completion was not restored after refresh: {restored}"
            assert restored["points"] == 10, f"Lesson points did not persist correctly: {restored}"
            assert restored["guest"].get("points") == 10, f"Guest progress storage missing awarded points: {restored}"
        finally:
            browser.close()


def test_quiz_question_bank_schema_and_unique_ids():
    """Every curated quiz item must have valid fields, answer index, and a unique stable ID."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("#authGate", timeout=15_000)
            audit = page.evaluate("window.__WEBCHEM_TEST__.auditQuizQuestionBank()")
            assert audit["total"] >= 100, f"Question library unexpectedly small: {audit}"
            assert audit["invalidIndexes"] == [], f"Malformed question-bank entries at indexes: {audit['invalidIndexes']}"
            assert audit["duplicateIds"] == [], f"Question IDs must be unique: {audit['duplicateIds']}"
        finally:
            browser.close()


def test_ai_tutor_request_error_handling_and_mode_history_isolation():
    """AI responses are stored only on success; tutor and FireBot histories stay separate."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def ai_route(route):
            payload = route.request.post_data_json
            mode = payload["mode"]
            prompt = payload["messages"][-1]["content"]
            if prompt == "trigger failure":
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body='{"success":false,"error":"AI temporarily unavailable"}',
                )
            else:
                answer = "Tutor stub response" if mode == "tutor" else "FireBot stub response"
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"success":true,"answer":"' + answer + '"}',
                )

        page.route("**/api/auth/me", lambda route: route.fulfill(
            status=401, content_type="application/json",
            body='{"success":false,"error":"Unauthenticated"}'
        ))
        page.route("**/api/ai/chat", ai_route)
        try:
            page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_selector("#authGate", timeout=15_000)
            result = page.evaluate("""async () => {
              const t=window.__WEBCHEM_TEST__;
              const tutor=await t.askWebChemAI('tutor','successful tutor prompt');
              const firebot=await t.askWebChemAI('firebot','successful FireBot prompt');
              let error='';
              try { await t.askWebChemAI('tutor','trigger failure'); }
              catch(e) { error=e.message; }
              return {
                tutor,firebot,error,
                tutorHistory:t.webChemAIHistory.tutor,
                firebotHistory:t.webChemAIHistory.firebot
              };
            }""")
            assert result["tutor"] == "Tutor stub response"
            assert result["firebot"] == "FireBot stub response"
            assert "AI temporarily unavailable" in result["error"], result
            assert len(result["tutorHistory"]) == 2, "Failed AI request must not be added to tutor history"
            assert result["tutorHistory"][-1]["content"] == "Tutor stub response"
            assert len(result["firebotHistory"]) == 2, "FireBot history must remain separate from tutor history"
            assert result["firebotHistory"][-1]["content"] == "FireBot stub response"
        finally:
            browser.close()


def test_service_worker_never_caches_account_or_api_responses():
    """Keep authenticated/account-specific API data out of the offline cache."""
    source = Path("sw.js").read_text(encoding="utf-8")
    assert "const CACHE_NAME='webchem-shell-v3'" in source
    # Bump cache version so activation deletes the old broad cache, not just future API responses.
    assert "k.startsWith('webchem-')&&k!==CACHE_NAME" in source
    assert "const path=url.pathname.toLowerCase()" in source
    assert "path===prefix||path.startsWith(prefix+'/')" in source
    # Include both bare route roots (/auth, /api) and nested endpoints.
    assert "['/api','/auth','/progress']" in source
    assert "Never cache API/auth/progress responses" in source


def test_profile_details_storage_is_account_scoped():
    """Prevent guest/account profile details from being shared in local storage."""
    source = Path("index.html").read_text(encoding="utf-8")
    assert "const profileStore=()=>" in source
    assert "webchem-profile-details-'+String(id).toLowerCase()" in source
    assert "read(profileStore(),{})" in source
    assert "localStorage.setItem(profileStore(),JSON.stringify(d))" in source


def test_quiz_history_and_mistakes_are_account_scoped():
    """Quiz analytics and review mistakes must not mix across signed-in learners."""
    source = Path("index.html").read_text(encoding="utf-8")
    assert "function quizHistoryKey()" in source
    assert "function quizMistakesKey()" in source
    assert "localStorage.getItem(quizHistoryKey())" in source
    assert "localStorage.setItem(quizHistoryKey()," in source
    assert "localStorage.getItem(quizMistakesKey())" in source
    assert "localStorage.setItem(quizMistakesKey()," in source
