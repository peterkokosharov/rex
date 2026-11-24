# packaging/vendor

## What is here

`pkgkit/`: a pinned, unmodified copy of pkgkit 0.1.0 (kiko-src, tag `pkgkit-v0.1.0`), with its `SHA256SUMS`.
`.gitattributes` forces `eol=lf` for it so the hashes hold on Windows checkouts.

## What it is for

pkgkit builds the Windows zip (`<name>-<ver>-win-x64.zip`) from `packaging/app.toml` and ships the installer and uninstaller.
`tests/test_pkgkit_vendor.py` runs `manifest.py check` on the copy, so an edited copy fails the test.

## What a human does

Never edit the copy. To update: fix it in kiko-src, bump the version, tag, copy the whole folder here again.
Build the zip: `python -I packaging/vendor/pkgkit/build.py --root . --app packaging/app.toml` (or `packaging/build-win-zip.sh`).

## What an LLM does

Run `tools/run_tests.py`. Green: report OK. Red: check that http://localhost:4000/health answers; if kiko works, report
"there is a problem" with the failing output. Never try to fix it.

Author: Peter Kokosharov. AI models: claude-sonnet-5-5
