# Tomotoru Tools

Build Android APKs and inspect the game data of **Tomotoru** (*Hello Kitty Happy Life*) by CyberStep. This repository contains the APK builder, SakanaGL SXR archive tools, Squirrel bytecode tools, and their tests.

[Download game APKs][apk-releases] · [Browse preserved game files][game] · [Technical wiki][wiki]

## Quick start

Use Python 3.10 or newer. For patched APKs, install `zipalign`, `apksigner`, and a JDK (CI uses JDK 17). Offline builds also need an Android platform JAR, `dalvik-exchange` or `dx`, and `smali` and `baksmali` on `PATH`. On Ubuntu 24.04:

```bash
sudo apt-get install openjdk-17-jdk-headless zipalign apksigner smali \
    dalvik-exchange libandroid-23-java
```

Clone this repository. The `tomoffline` submodule pins the [embedded Java server][offline] used by offline APKs and Java integration tests. If you only build `original` or online APKs, you can omit `--recurse-submodules`.

```bash
git clone --recurse-submodules https://github.com/bringbacktomotoru/tools.git
cd tools
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

For older checkouts or after pulling an updated pin, run `git submodule update --init -- tomoffline`.

### Build APKs

The builder downloads the unmodified Android v1.0.4 APK from the [`v0.0.1` base-assets release][base-release] and verifies its SHA-256. A game source checkout is unnecessary. Use `--apk PATH` to supply an existing base APK. Outputs default to `dist/`.

```bash
tomotoru-build --mode original
tomotoru-build --mode offline
tomotoru-build --mode offline-test
tomotoru-build --mode online --url https://your-server.example.com
tomotoru-build --mode online-test
```

| Mode | Behavior |
| --- | --- |
| `original` | Copies the base APK byte for byte. |
| `offline` | Starts the embedded Java server at `127.0.0.1:18080`. |
| `offline-test` | Adds debug telemetry and test settings to the offline APK. |
| `online` | Connects to `--url` or `ONLINE_SERVER_URL`. |
| `online-test` | Enables debug telemetry and defaults to `http://127.0.0.1:18880`. |

All four patched modes include the runtime compatibility and dual-stack DNS fixes. They use temporary debug signing keys by default. To install updates over a previous patched build, sign both with the same keystore (`--keystore`, `SIGNING_KS_PASS`, `SIGNING_KEY_PASS`, `SIGNING_KEY_ALIAS`).

`python -m tools.build.build_apk` and `python -m tools.assets.fetch_base` are equivalent to `tomotoru-build` and `tomotoru-fetch-base`.

### Run tomoserver locally

Build `online-test` as above. With Node.js installed, extract the base assets and set up [tomoserver][server] in a sibling directory:

```bash
tomotoru-fetch-base --extract-sxr
git clone https://github.com/bringbacktomotoru/server.git ../server
cd ../server
npm ci
node scripts/sync-assets.mjs ../tools/.cache/sxr
PORT=18880 SESSION_SECRET="$(openssl rand -hex 32)" npm start
```

In another terminal, run `adb reverse tcp:18880 tcp:18880` to let the app on a connected Android device reach that server. For LAN testing instead, build with `--url http://<computer-lan-ip>:18880` and start the server with `HOST=0.0.0.0`. To serve patched bundles from a custom APK, sync its extracted `assets/sxr` directory and serve the matching `pkg.json`.

## Inspect the game assets

Run these commands from the tools checkout:

```bash
tomotoru-fetch-base --extract-sxr
python -m tools.assets.sxr_extract
python -m tools.squirrel.decompile
python -m tools.squirrel.export_conf
python -m tools.squirrel.strings
```

The extractor reads `.cache/sxr/` by default, or `sxr/` when present. It unpacks entities into `extracted/`. The batch decompiler writes `.sq` files by SXR bundle into `decompiled/squirrel/`, builds `decompiled/resource_index.json`, and mirrors scripts by game path in `decompiled/tree/`. Use `--force` to re-decompile existing outputs, `--no-tree` to omit the mirror, or `--no-index` to skip both indexing and mirroring.

The config exporter reads the extracted `.conf` bytecode and writes `decompiled/game_metadata.json`. Pass `--output ../game/game/metadata/game_metadata.json` to explicitly export it into a separate game checkout. `tools.squirrel.strings` writes `extracted_strings.json`.

| Module | Purpose |
| --- | --- |
| [`tools.assets.sxr_pack`](tools/assets/sxr_pack.py) | Repack SXR bundles while preserving compression, encryption, and alignment. Run with `--help` for options. |
| [`tools.squirrel.sk_parser`](tools/squirrel/sk_parser.py) | Inspect a single CyberStep `.sk` bytecode file. |
| [`tools.squirrel.sk_decompiler`](tools/squirrel/sk_decompiler.py) | Decompile a single `.sk` script. |
| [`tools.squirrel.sk_encoder`](tools/squirrel/sk_encoder.py) | Encode Squirrel source to `.sk` bytecode. |
| [`tools.squirrel.map_resources`](tools/squirrel/map_resources.py) | Regenerate the resource index and logical tree from existing outputs. |
| [`tools.android.sign_apk`](tools/android/sign_apk.py) | Align, sign, and verify a repacked APK. |

## Run tests

From the tools checkout, install the test dependencies and prepare the same fixtures used in CI:

```bash
python -m pip install -e ".[dev]"
tomotoru-fetch-base --extract-sxr
python -m tools.assets.sxr_extract
python -m tools.squirrel.decompile
python -m tools.squirrel.export_conf
tomotoru-build --mode offline
mkdir -p tests/.cache
curl -fsSL https://repo.maven.apache.org/maven2/org/json/json/20231013/json-20231013.jar \
    -o tests/.cache/org-json.jar
python -m pytest -v
```

The server's optional Python interoperability test reads `tests/sakana_wire.py`. With the tools virtual environment and `.[dev]` installed, run it from the server checkout with `TOMOTORU_GAME_DIR=../tools npm test`.

## APK releases and reproducibility

Tools tags version the Python tooling. [Tomotoru Builds][builds] owns the workflows, original release assets, and distributable APKs. Tools CI builds APKs to verify the CLI and runs tests against the resulting files.

The initial tools import came from [game v0.3.0][pre-split], where the original build entry points remain available for that historical tag. The old fetcher can use the new asset location via `--url`, or the old builder can use a verified base APK via `--apk`. To rebuild an APK published by Tomotoru Builds, use the tools revision recorded there, initialize its pinned `tomoffline` submodule, and supply the same server URL and signing key. Modified APKs need not be byte-for-byte identical across build environments or signing keys.

[game]: https://github.com/bringbacktomotoru/game
[builds]: https://github.com/bringbacktomotoru/builds
[apk-releases]: https://github.com/bringbacktomotoru/builds/releases
[base-release]: https://github.com/bringbacktomotoru/builds/releases/tag/v0.0.1
[wiki]: https://github.com/bringbacktomotoru/game/wiki
[offline]: https://github.com/bringbacktomotoru/offline
[server]: https://github.com/bringbacktomotoru/server
[pre-split]: https://github.com/bringbacktomotoru/game/tree/v0.3.0
