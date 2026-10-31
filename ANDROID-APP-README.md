# WebChem Android preview

This branch packages the WebChem preview dashboard in a Capacitor Android shell.
It is deliberately isolated from the production repository and production backend.

## Build an installable test APK

1. Open the repository's Actions tab.
2. Select **Build WebChem Android APK**.
3. Choose **Run workflow** on branch `android-app`.
4. Wait for the workflow to complete.
5. Download the `webchem-debug-apk` artifact from the completed run.
6. Transfer the APK to an Android phone and install it for testing.

The APK is a debug/testing build, not a Play Store release.

## App identity

- Display name: WebChem
- Initial application ID: `com.webchem.learn`

The application ID becomes the permanent Play Store identity once the app is published. Confirm it before creating the Play Store listing.

## Safety notes

- Bundled web source is copied from the preview dashboard.
- The dashboard currently talks to the WebChem staging API.
- Production backend and production repository are not changed by this branch.
- Staging CORS must explicitly permit the Capacitor Android WebView origin before sign-in and cloud progress can work in the installed app.
- Do not put AI provider secrets or other private API keys in the web bundle.
