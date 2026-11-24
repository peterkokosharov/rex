# Debian packaging: kikolib -> kiko -> rex -> front-ends (plan)

Date: 2026-11-23. Status: plan only, nothing changed. Goal: one clean install order
`python3-kikolib -> kiko -> rex -> layka | sisi | lassie`, one copy of every Python module on the system, the same
version rule in every repo.

## 1. Current state

| package | where built | version | Depends | install paths | vendors / notes |
|---|---|---|---|---|---|
| rex | `rex/packaging/build-deb.sh` (hand-made `DEBIAN/control`, `dpkg-deb`) | default `0.2.2` hard-coded in the script; CI passes no version | `python3 (>= 3.12)` | `/usr/lib/python3/dist-packages/{runtime,runtimelib}`, `/usr/bin/rex` (sh wrapper: `python3 -m runtime`), `runtimelib/native/librex_native.so`, Arch `amd64` | **vendors kikolib** at `runtimelib/kikolib/` (13 files, ~2.5k lines). Imported relatively in 4 places (`config/config.py`, `config/sources.py`, `llm/client.py`, `tools/webfetch.py`); `guard/entrypoint.py` and `Dockerfile` still use the old path `runtime.kikolib` (already flagged broken in the 2026-11-05 audit). |
| layka | `layka/packaging/build-deb.sh` (same style) | from tag (`${GITHUB_REF_NAME#v}`), default `0.3.0`; tags v0.1.0, v0.2.0 | `python3, rex (>= 0.2.0), libncursesw6, libtinfo6, libc6, libgcc-s1, libstdc++6, curl` | `/usr/lib/layka/layka-bin`, `/usr/bin/layka`, `/usr/share/layka/lang`, desktop file, icons | starts `python3 -P -m runtime --jsonl`; relies on rex being in dist-packages. Has `rex_dir` / `LAYKA_REX_DIR` (prepended to PYTHONPATH). Description still says "install rex_0.2.0_amd64.deb first". |
| sisi | **no deb.** `packaging/package-linux.sh` makes `sisi-linux-x86_64.tar.gz` with bundled Qt 6.8.3 | CMake `0.1.0` | none (tarball) | self-contained folder | needs Qt6 Widgets, Network, Svg. Has `rex_dir` / `SISI_REX_DIR`; default = folder `rex` next to the program. |
| lassie | **no packaging at all**; pyproject has no build-system; no tags | `0.1.0` | - | run as `python -m lassie` from checkout | starts `python -P -m runtime`; `rex_dir` / `LASSIE_REX_DIR` optional. |
| python3-kikolib | old kiko monorepo snapshot (`packages/kikolib/debian/`, debhelper + pybuild) | `0.1.0-1` | `${python3:Depends}, python3` | `/usr/lib/python3/dist-packages/kikolib/` | a built `python3-kikolib_0.1.0-1_all.deb` exists in the snapshot. Same 13 modules as rex's copy; real drift is small (rex copy: added copyright header, `typing.Self` replaced by class names, `exc_info=True` in one log call). |
| python3-kiko-core, kiko-proxy | same snapshot (`packages/core`, `packages/proxy`) | `0.1.0-1` | core: `python3-kikolib (>= 0.1.0)`; proxy: kikolib + core | dist-packages + `kiko` console script + systemd **user** unit `/usr/lib/systemd/user/kiko-proxy.service` | this is the older kiko design; the current kiko is different (see 1.1). |

**Duplicates and conflicts found.** (a) Two kikolib copies: `runtimelib.kikolib` inside rex, and top-level
`kikolib` from python3-kikolib (and inside kiko). No file conflict (different paths), but two copies drift. (b) rex
installs the very generic top-level names `runtime` and `runtimelib` into dist-packages; any other package with
those names would collide. (c) rex's CI always builds `rex_0.2.2` even on a later tag. (d) Layka's install hint is
stale. (e) rex's README has no install section (only `RELEASE.md` "pip install -e ." from v0.1.0).

### 1.1 What could NOT be found on this machine

- **The current kiko repo.** `C:/Users/a1bg546136/kiko/kiko/` is empty. Per `HANDOFF-home-2026-11-06.md` kiko is a
  LiteLLM fork, branch `kiko-main`, with kiko-lite (`kikolib/`, `kiko_lite/`) inside; it moved to the home machine.
  Its packaging is unknown here. Notes say it had `deb/kiko_0.2.0_amd64/DEBIAN/{control,postinst,conffiles}` and a
  user unit `kiko.service`, and that GitHub `peterkokosharov/kiko` @ `aed3171` had `kikolib/` and `debian/`. Not
  verified.
- **A standalone kikolib repo.** None exists locally; only the monorepo snapshot (`D-kiko`; `H-kiko` lacks kikolib).
- The owner's real deb approach for user services (`litellm_try`, home machine) - not here.
- No `dpkg` on this machine: nothing was built or installed for this plan.

## 2. Target layout

```
python3-kikolib  (all)    Depends: python3 (>= 3.12)
kiko             (all)    Depends: python3 (>= 3.12), python3-kikolib (>= 0.2)
rex              (amd64)  Depends: python3 (>= 3.12), python3-kikolib (>= 0.2)   Recommends: kiko
layka            (amd64)  Depends: rex (>= 0.3), libncursesw6, ...
sisi             (amd64)  Depends: rex (>= 0.3), ${shlibs:Depends} (libqt6widgets6, libqt6network6, libqt6svg6)
lassie           (all)    Depends: rex (>= 0.3), python3 (>= 3.12)
```

Decisions (technical, taken here):

1. **Libraries in dist-packages, applications private.** `python3-kikolib` is a real library -> `/usr/lib/python3/dist-packages/kikolib/`
   (Debian Python policy name `python3-<module>`). kiko, rex and lassie are applications -> code in
   `/usr/share/<pkg>/`, a small `/usr/bin/<pkg>` sh wrapper sets `PYTHONPATH=/usr/share/<pkg>` and runs
   `python3 -P -m <module>`. This removes the `runtime`/`runtimelib` collision risk. rex's native helper moves to
   `/usr/lib/rex/librex_native.so` (arch-specific files do not belong in `/usr/share`) and rex looks there first.
2. **Front-ends find rex through their existing `rex_dir` setting.** Each front-end's `/usr/bin` wrapper exports
   `LAYKA_REX_DIR` / `SISI_REX_DIR` / `LASSIE_REX_DIR=/usr/share/rex` unless already set. No C++ change needed.
3. **rex -> kiko is Recommends, not Depends.** rex works with any OpenAI-compatible endpoint (LiteLLM, the
   claude-cli gateway). apt installs Recommends by default, so the order kikolib -> kiko -> rex still holds on a
   normal `apt install ./rex_*.deb`; a server without kiko is still possible.
4. **One kikolib source of truth.** kikolib gets its own repo (or one folder in the kiko repo with its own
   `debian/`); rex and kiko stop owning a copy:
   - rex code imports top-level `kikolib` (4 import sites + guard entrypoint + Dockerfile).
   - The rex repo keeps a **pinned vendored copy at `vendor/kikolib/`** only for the source checkout, the Windows
     zip, macOS and Docker. A file `vendor/kikolib/VERSION` holds the kikolib tag and a sha256 of the tree; a test
     fails if the copy was edited by hand. A sync script copies a tagged release in.
   - The rex deb does **not** ship `vendor/`; it uses python3-kikolib. Path order makes the system copy win.
5. **Version = git tag.** Every repo tags `vX.Y.Z`; the deb version is `X.Y.Z` (Debian revision `-1` only for
   debhelper-built packages). CI passes `${GITHUB_REF_NAME#v}`; untagged builds get `X.Y.Z~dev+<shortsha>` (sorts
   lower than the release). The version in `pyproject.toml` / `CMakeLists.txt` must equal the tag; CI checks it.
   First versions: python3-kikolib 0.2.0 (rex's newer copy becomes 0.2.0), rex 0.3.0, kiko next, layka 0.3.0,
   sisi 0.2.0, lassie 0.2.0. Dependent minimums move only when an API is needed, not on every release.
6. **Build style.** Keep the hand-made `dpkg-deb` scripts that rex and layka already have (small, readable, no
   debhelper on the CI runner); copy the same script shape to kikolib, kiko, sisi, lassie. The old debhelper/pybuild
   trees in the monorepo snapshot are reference only. `lintian` runs in CI as a warning, not a gate.
7. **kiko service.** systemd **user** unit at `/usr/lib/systemd/user/kiko.service`, data in `~/.config/kiko` and
   `~/.local/share/kiko`; postinst does not start it as root (the user runs `systemctl --user enable --now kiko`),
   as agreed in `deferred-deb.md`. Details stay with the kiko repo.

## 3. CI per repo (GitHub Actions, ubuntu-24.04)

Every repo: job `tests` -> job `deb` (runs `packaging/build-deb.sh "$VERSION"`, `dpkg-deb --info/--contents`,
then an **install smoke test** in a clean `debian:13` / `ubuntu:24.04` container: `apt install ./deps/*.deb ./dist/*.deb`,
run `<cmd> --help`) -> on a `v*` tag, `gh release upload`. Dependency debs for the smoke test are downloaded
from the latest GitHub release of the upstream repo (`gh release download -R peterkokosharov/kikolib`), so
rex's job pulls kikolib, layka's pulls kikolib + rex, and so on. Sisi keeps its tar.gz/zip jobs and adds the deb.

## 4. Implementation steps

| # | repo | step | done when |
|---|---|---|---|
| 1 | kikolib (new) | owner says yes to a public repo `kikolib` (MIT, personal author); import rex's copy (newest) as 0.2.0 with its tests; `packaging/build-deb.sh` -> `python3-kikolib_0.2.0_all.deb` | deb installs in a clean container; `python3 -c "import kikolib; print(kikolib.__version__)"` prints 0.2.0; tag v0.2.0 released |
| 2 | rex | move `runtimelib/kikolib` -> `vendor/kikolib` + `VERSION`; imports -> `kikolib`; fix `guard/entrypoint.py`, `Dockerfile`; sync check test | `pytest` green on 3 OS; `grep -r "runtimelib.kikolib\|\.\.kikolib"` empty; Windows zip still starts |
| 3 | rex | `build-deb.sh`: `/usr/share/rex`, `/usr/lib/rex/librex_native.so`, Depends `python3-kikolib (>= 0.2)`, Recommends `kiko`, no vendor/; CI version from tag; README "Install" section (order kikolib -> kiko -> rex) | smoke test installs kikolib + rex, `rex --help` works, `dpkg -S kikolib` shows only python3-kikolib; tag v0.3.0 |
| 4 | kiko (home) | drop own kikolib copy, Depends `python3-kikolib (>= 0.2)`, `/usr/share/kiko` + wrapper + user unit; same CI shape | clean container: kikolib + kiko install, `kiko --help` works, `systemctl --user cat kiko` shows the unit |
| 5 | layka | wrapper exports `LAYKA_REX_DIR=/usr/share/rex`; Depends `rex (>= 0.3)`; fix description; smoke test with rex deb | in container: layka starts rex (`--jsonl` handshake `ready` seen in log) |
| 6 | sisi | new `packaging/build-deb.sh` against system Qt6 (`/usr/lib/sisi/sisi-bin`, wrapper with `SISI_REX_DIR`, desktop file, icons), Depends via `dpkg-shlibdeps`; if Sisi does not build with the distro Qt, bundle Qt in `/usr/lib/sisi` instead | deb installs on Debian 13 and Ubuntu 24.04; Sisi starts and reaches rex `ready` (xvfb) |
| 7 | lassie | add build-system to pyproject; `build-deb.sh` (`/usr/share/lassie`, wrapper with `LASSIE_REX_DIR`, Arch all); first tag | `lassie --help` works after install; page served on 127.0.0.1 in the smoke test |
| 8 | all | independent Opus review of the packaging diff in each repo before its tag | review findings fixed or recorded |

Decision (owner, 2026-11-23): kikolib lives in the kiko repo, which is its single source of truth and builds the
`python3-kikolib` deb; rex takes kikolib from there (deb dependency, and a pinned copy from a kiko release for the
source checkout / Windows / macOS / Docker). No separate kikolib repo.

Progress on step 2 (2026-11-23): DONE - `runtimelib/kikolib` is deleted; rex vendors the unmodified kiko release
`kikolib-v0.2.0` (commit add4685) at `vendor/kikolib/` (with its `SHA256SUMS`; `.gitattributes` forces LF).
`tests/test_vendor_kikolib.py` checks every file against the manifest and `__version__ == "0.2.0"`. Code imports the
top-level `kikolib`: `runtimelib/__init__.py` appends `<root>/vendor` to `sys.path` (a system copy wins), `guard/entrypoint.py`
does the same fallback, the image copies `vendor/kikolib` to `/app/vendor/kikolib` (`PYTHONPATH=/app:/app/vendor`), and the
zip/deb scripts ship `vendor/kikolib` next to `runtimelib`. Renames `http_client`->`http`, `posix_utils`->`posix`. rex pins the
stdlib HTTP backend at start-up. Image tag unified to `ai-rex-guard:latest`, version read from `runtimelib.__version__`.
A separate `VERSION` file is not needed (the version lives in kikolib itself); the deb dropping `vendor/` for
`python3-kikolib` belongs to step 3.

## Sources

- `rex/packaging/build-deb.sh`, `rex/packaging/build-win-zip.sh`, `rex/pyproject.toml`, `rex/README.md`,
  `rex/RELEASE.md`, `rex/.github/workflows/build.yml`, `rex/runtimelib/kikolib/`, `rex/Dockerfile` - local repo
  `C:/Users/a1bg546136/rex`, HEAD `a893e45`, read 2026-11-23.
- `layka/packaging/build-deb.sh`, `layka/.github/workflows/build.yml`, `layka/src/core.h`, `layka/src/main.cpp` -
  `C:/Users/a1bg546136/layka`, HEAD `0592b25`, read 2026-11-23.
- `sisi/README.md`, `sisi/packaging/package-linux.sh`, `sisi/CMakeLists.txt`, `sisi/.github/workflows/build.yml` -
  `C:/Users/a1bg546136/sisi`, HEAD `5bb879b`, read 2026-11-23.
- `lassie/pyproject.toml`, `lassie/README.md`, `lassie/lassie/config.py`, `lassie/lassie/rexproc.py` -
  `C:/Users/a1bg546136/lassie`, HEAD `1e2e617`, read 2026-11-23.
- Old kiko monorepo snapshot (`packages/{kikolib,core,proxy}/debian/`, `pyproject.toml`, `.github/workflows/ci.yml`,
  built debs dated 2026-11-01) - `roni/projects/kiko/notes/import-2026-11-03/D-kiko` (and `H-kiko`), imported
  2026-11-03.
- kiko notes `deferred-deb.md` (2026-10-28), `HANDOFF-home-2026-11-06.md`, `PLAYBOOK.md`, `AUDIT-rex-2026-11-05.md` -
  `roni/projects/kiko/notes/`.
- `kiko/kiko-identity/pyproject.toml` (0.9.0), `kiko/kiko-tray/pyproject.toml` (0.1.0) - not part of the chain;
  `kiko/kiko/` is empty.
