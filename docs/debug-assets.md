# Opt-in CC0 audio asset for debug builds

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

`pytest tests/test_debug_audio_asset.py` checks an actual release bundle: it
replaces entity `1`, parses the resulting SXR, reverses the stored compression
and encryption, and verifies the recovered bytes exactly match Kenney's Ogg.
The stock APK bundle is also checked unchanged. This is a packaging-format
smoke test, not a claim that the selected sound has been auditioned in-game.

In the Windows verification environment, the `online-test` pipeline completed
SXR patching and APK repacking, including the alternate audio and related
`pkg.json` metadata, but could not finish APK alignment/signing because Android
SDK `zipalign` (and the other Android build tools) are not installed. No Android
device was queried or used.
