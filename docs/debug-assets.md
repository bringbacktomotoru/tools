# Opt-in CC0 assets for test builds

## Opt-in PNG replacement for visual test builds

Pass `--debug-png PATH` to `online-test` or `offline-test` to replace one PNG
entity. It defaults to entity `92` in bundle `1-011b`: the English room 2
loading background (`/project/dl/loadingbg/pop/EN/room2.png`, 1280×960).
The loader in `game/game/squirrel/project/dl/loadingbg/stageload.sk.sq`
constructs `room2.png` through `room5.png` and picks
`a[skGetTime() % 4]`. Thus room 2 is not guaranteed at any particular startup;
relaunching may select it. Override with `--debug-png-bundle` and
`--debug-png-entity` as needed. PNG replacement is never enabled implicitly
and is rejected outside test modes. The input must be a valid PNG with the
target's dimensions; the SXR metadata and APK `pkg.json` are updated by the
existing build pipeline.

Example:

```powershell
python -m tools.build.build_apk --mode online-test `
  --debug-png .cache/kenney-ui-replacement.png `
  --output dist/tomotoru-online-test-visual.apk
```

The test image is `PNG/Blue/Default/button_rectangle_depth_flat.png` from
Kenney's UI Pack, CC0 (public domain dedication):
<https://kenney.nl/assets/ui-pack>. Download the official archive by requesting
Kenney's download-link endpoint for asset id `6173736574732f75692d7061636b`
(the returned download URL is temporary). Save it as
`.cache/kenney_ui_pack.zip`. The tested archive SHA-256 is
`a8a14a234911eb648c062622915c93e79e94e97cb7f9f375a70f6617f1174318`;
the source PNG SHA-256 is
`6c709a45aae0330ffff5b060d9f14cc2297839f664e0a4c9e516d90ad085ec0b`.
Recreate the exact test PNG with:

```python
import zipfile
from io import BytesIO
from PIL import Image

with zipfile.ZipFile(".cache/kenney_ui_pack.zip") as pack:
    image = Image.open(BytesIO(pack.read(
        "PNG/Blue/Default/button_rectangle_depth_flat.png"
    ))).convert("RGBA")
image.resize((1280, 960), Image.Resampling.LANCZOS).save(
    ".cache/kenney-ui-replacement.png", optimize=True
)
```

This retains the source alpha channel while conforming to the game's target
dimensions (verified with Pillow 13.0.0). The resulting PNG SHA-256 is
`06b3ab8c7848a18e4bf09feb4044b9039789c034350f435095f39edb69fff81c`.
This conspicuous diagnostic is not a proposed game asset. The target is chosen
because it is a known-size PNG in the resource index and appears in the early
loading flow (subject to the loader's time-based selection above).

`build_apk` accepts `--debug-audio PATH` to replace one audio entity in
`assets/sxr/b-010c.sxr`. The replacement must be an Ogg stream (`OggS` magic);
entity `1` is the default and can be selected explicitly with
`--debug-audio-entity`. For example:

```powershell
python -m tools.build.build_apk --mode online-test `
  --debug-audio .cache/kenney/Audio/click_001.ogg `
  --output dist/tomotoru-debug-audio.apk
```

The option is absent by default. Normal builds do not read or add any alternate
audio. The patched bundle is re-encrypted/recompressed according to its original
SXR entity metadata, and the APK's `pkg.json` size and MD5 are updated by the
existing builder.

## Asset license and provenance

The sound used in the smoke test is `Audio/click_001.ogg` from Kenney's
**Interface Sounds 1.0** pack:

- Official asset page and license: <https://kenney.nl/assets/interface-sounds>
- Public ZIP mirror used for this verification:
  <https://opengameart.org/sites/default/files/kenney_interfaceSounds.zip>
- License embedded in the ZIP: Creative Commons Zero 1.0 (CC0); the embedded
  notice explicitly grants use in personal, educational, and commercial
  projects. Attribution is optional.
- ZIP SHA-256: `f2193d072726d6758a5f7871b2dcc54dcce0d5c35c6f0a62f92549b327c81232`

To reproduce locally, place that ZIP at `.cache/kenney_interfaceSounds.zip`.
The debug-assets fixture directory also provided the original APK; its SHA-256
was `e457b42783e245677cdf775fa161a5a54aefaaf788d1c73dcd67470f67fa831b`, matching
the pinned value in `tools/assets/fetch_base.py`.

## Verification

`pytest tests/test_debug_png_asset.py` exercises the actual `1-011b.sxr` from
the release APK and CC0 UI Pack fixture. It checks that the default remains
unchanged, replacement bytes survive SXR repacking, and invalid PNGs,
mismatched dimensions, and non-test modes are rejected. Put the verified base
APK at `.cache/tomotoru_v1.0.4.apk` and the archive at
`.cache/kenney_ui_pack.zip` to run it.

`pytest tests/test_debug_audio_asset.py` checks an actual release bundle: it
replaces entity `1`, parses the resulting SXR, reverses the stored compression
and encryption, and verifies the recovered bytes exactly match Kenney's Ogg.
The stock APK bundle is also checked unchanged. This is a packaging-format
smoke test, not a claim that the selected sound has been auditioned in-game.

The earlier audio test APK was aligned and signed with v1, v2, and v3 schemes,
and was observed to reach the in-game room on a Moto G22. Successful playback
was not confirmed by listening, so this is not an audio-quality or audible
replacement verification. The visual PNG APK was separately aligned, signed,
and its embedded PNG bytes verified. No phone was accessed for this PNG work.
