# Parallel Android APK smoke test

## Safety

The installed `com.cyberstep.sanriocharacters` v1.0.4 app is non-debuggable and
contains user progress. Its signing certificate differs from the offline
release certificate. Do not update/replace, uninstall, or clear data from that
app. No ADB/device operation or push is part of this workflow.

A unique application ID gives the test build separate app-private storage, not
separate backend accounts or shared external storage. Use test data/accounts.
The intended test ID is `com.cyberstep.sanriocharacters.smoketest`.

## Opt-in transform

`tools/android/parallel_apk.py` uses apktool to decode/rebuild the manifest and
Smali. It changes the application ID and package-scoped manifest strings,
normalizes component class references to the original fully qualified Java
classes, and changes provider authorities to unique test authorities. It
updates old package-ID literals in Smali/XML resources but not Java/Dalvik class
descriptors. Before signing, it verifies the built package with `aapt`; after
signing, it verifies the signature and rejects old package/authority literals
remaining in DEX.

The validated input used for the current candidate is
`../debug-assets/dist/debug-assets/tomotoru-online-test-cc0-audio.apk`.
Its launcher is `com.cyberstep.sanriocharacters.SanrioActivity`. The output
retains that Java activity class.

## Reproduce on this Windows host

Requirements present on this host:

* Python 3.12 and JDK 17 at
  `%LOCALAPPDATA%\Temp\opencode\tomotoru-java\jdk-17.0.21.1+2`
* SDK build-tools at
  `%LOCALAPPDATA%\Temp\opencode\tomotoru-android-sdk\build-tools\android-15`
  (`aapt.exe`, `zipalign.exe`, `apksigner.bat`)
* apktool 3.0.3; the command below downloads its official GitHub release JAR.

Run from the repository root in PowerShell:

```powershell
$env:JAVA_HOME = "$env:LOCALAPPDATA\Temp\opencode\tomotoru-java\jdk-17.0.21.1+2"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
New-Item -ItemType Directory -Force .cache,dist | Out-Null
Invoke-WebRequest `
  https://github.com/iBotPeaches/Apktool/releases/download/v3.0.3/apktool_3.0.3.jar `
  -OutFile .cache\apktool_3.0.3.jar
if ((Get-FileHash .cache\apktool_3.0.3.jar -Algorithm SHA256).Hash -ne `
    "DBF930B076C6B9BE08D57C449CACEFC3BDD6B71EBD59B3066FC0E1F5B14F9423") {
  throw "apktool 3.0.3 SHA-256 mismatch"
}
if (-not (Test-Path .cache\tomotoru-parallel-test.keystore)) {
  keytool -genkeypair -keystore .cache\tomotoru-parallel-test.keystore `
    -alias tomotoru -keyalg RSA -keysize 2048 -validity 10000 `
    -storepass android -keypass android `
    -dname "CN=Tomotoru Parallel Test, OU=Local, O=Tomotoru, C=US"
}
python -m tools.android.parallel_apk `
  --apk ..\debug-assets\dist\debug-assets\tomotoru-online-test-cc0-audio.apk `
  --output dist\tomotoru-online-test-cc0-audio-parallel.apk `
  --apktool-jar .cache\apktool_3.0.3.jar `
  --sdk "$env:LOCALAPPDATA\Temp\opencode\tomotoru-android-sdk" `
  --keystore .cache\tomotoru-parallel-test.keystore
```

Keep the local keystore private and reuse it for subsequent candidate updates.
The default local test-key passwords are `android`; override them as appropriate.
The source builder's `tomoffline` submodule is not needed when starting from an
already built APK. Android 12 `arm64-v8a`/`armeabi-v7a` compatibility and the
validated audio assets still need to be checked on the host below. Do not use
ADB as part of these checks.

## Verified host artifact

The parallel candidate was built from the validated audio APK above and saved
at:

`_worktrees/android-device-smoke/dist/tomotoru-online-test-cc0-audio-parallel.apk`

Host checks passed: `aapt` reports package
`com.cyberstep.sanriocharacters.smoketest` and launcher
`com.cyberstep.sanriocharacters.SanrioActivity`; the FileProvider authority is
`com.cyberstep.sanriocharacters.smoketest.fileprovider`; the package-scoped
push permission was changed to the test package; `zipalign -c -v 4` and
`apksigner verify` passed. Both `arm64-v8a` and `armeabi-v7a` native libraries
are present. All `assets/` entries are byte-identical to the validated audio
input APK, and no exact old package or FileProvider authority string remains in
the output DEX.

The signing certificate SHA-256 is
`fc5ea69d63cea9229a4e9f3508199b7d1f4f6ef077c216351e257734f03f2936`
(DN `CN=Tomotoru Parallel Test, OU=Local, O=Tomotoru, C=US`). For comparison,
the host `phone-installed.apk` fixture has certificate SHA-256
`eb4ebc05ad1c323de55c18e28b1699b17472adfe08f81e1ee8282e2de87bc38e`.
No device was accessed, and this APK has not been installed or runtime-tested.
