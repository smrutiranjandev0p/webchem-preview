# WebChem

WebChem is a chemistry-learning web app and progressive web app (PWA) for chemistry study, quizzes, and practice.

> **Development status:** This repository is the staging/preview frontend. It is not a production-release declaration. Keep development and verification on the staging branch until the project owner explicitly approves production release.

## What’s included

- Chemistry learning and quick-search content
- MCQ/CBT practice, including fertilizer and urea topics
- Practice support for reviewing answers and weak topics
- Educational urea synthesis/material-balance simulator
- PWA manifest, app icons, and service worker
- Sign-in and cloud progress features connected to a separately deployed staging API

The urea simulator is an **educational approximation**. It is not a validated industrial process model and must not be used for plant operating decisions.

## Repository layout

- `index.html` — frontend application
- `manifest.webmanifest` — PWA metadata
- `sw.js` — service worker and app-shell caching
- `icons/` — application icons
- `tests/` — automated regression and staging checks
- `.github/workflows/webchem-staging-smoke.yml` — staging CI workflow
- `docs/RELEASE_READINESS.md` — release gates and approval checklist

## Run the frontend locally

The frontend is a static web app. From the repository root, start a local HTTP server:

```bash
python -m http.server 8000
```

Then open [http://localhost:8000](http://localhost:8000).

Some sign-in, cloud-sync, and account features depend on the staging backend and its configured environment. A local static server alone does not reproduce the full deployed environment.

## Automated checks

The staging GitHub Actions workflow runs the repository’s test suite and staging smoke checks. Check the [Actions tab](https://github.com/smrutiranjandev0p/webchem-preview/actions) for the latest run and its actual result.

Do not mark a release as verified based on a previous run; confirm the latest staging commit has a successful workflow.

## Staging and release policy

1. Make application changes on the staging branch.
2. Confirm the CI run for the latest commit is green.
3. Complete manual acceptance on supported mobile and desktop browsers, including sign-in, refresh/session restore, cloud progress, logout/guest behavior, and PWA installation/offline shell.
4. Verify the separately hosted staging backend, required environment configuration, backup, and rollback procedure.
5. Obtain explicit owner approval before any production deployment.

**Do not deploy this staging branch to production without explicit approval.** Production deployment and production data are outside the scope of this repository’s staging workflow.

## Release checklist

See [Release Readiness](docs/RELEASE_READINESS.md) for the current release gates, blockers, and approval sequence.
