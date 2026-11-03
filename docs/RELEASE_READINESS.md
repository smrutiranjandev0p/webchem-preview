# WebChem Release Readiness

This checklist is the release gate for the staging branch. Feature development is frozen unless a release blocker is found.

## Release sequence

1. Keep all work on `staging-cloud-account-isolation`.
2. Run the combined CI suite (JavaScript syntax, PWA shell checks, browser smoke, backend auth response, and account-isolation regressions).
3. Review the staging deployment manually on desktop and Android: login/register, Google sign-in, tutor, quizzes, virtual lab, progress sync, logout, install/offline behavior.
4. Confirm Railway staging health, environment variables, database backup/restore readiness, and rollback path.
5. Record known limitations and obtain owner approval before production deployment.
6. Deploy production only after explicit owner approval; verify health and auth/progress flows immediately after deployment.

## Release blockers

- Any CI failure or uncaught browser error.
- Authenticated/account-specific responses being cached by the service worker.
- Account A data visible in account B or guest mode.
- Failed cloud reads overwriting local progress, or failed saves silently losing changes.
- Missing PWA manifest/icon or broken offline shell.
- No verified backup/restore or rollback path for production data/deployment.

## Scope freeze

Do not add new learning features during release hardening. Only fix release blockers, security/privacy issues, or deployment defects. Production remains untouched until approval.