"""Compile and inject the embedded Java offline server into Tomotoru's classes.dex.

Pipeline:
  1. Compile the tomoffline submodule's Java sources with javac against android.jar
  2. Convert compiled .class files to server.dex with dalvik-exchange / dx
  3. Disassemble server.dex and the APK's original classes.dex using baksmali
  4. Merge com/tomotoru/offline/*.smali into the original smali tree
  5. Hook ls/sakana/SakanaApplication.smali -> onCreate() to call:
       invoke-static {p0}, Lcom/tomotoru/offline/OfflineServer;->start(Landroid/content/Context;)V
  6. Reassemble the merged smali tree into a single classes.dex using smali
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ANDROID_SERVER_SRC = ROOT / "tomoffline" / "src" / "main" / "java" / "com" / "tomotoru" / "offline"
OFFLINE_SOURCE_RELATIVE = Path("src/main/java/com/tomotoru/offline")

ANDROID_JAR_CANDIDATES = (
    Path("/usr/lib/android-sdk/platforms/android-23/android.jar"),
    Path("/usr/share/java/com.android.android-23.jar"),
)


def _find_android_jar() -> Path:
    configured = os.environ.get("TOMOTORU_ANDROID_JAR")
    if configured and Path(configured).is_file():
        return Path(configured)
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env_var)
        if sdk:
            plat_dir = Path(sdk) / "platforms"
            if plat_dir.is_dir():
                for p in sorted(plat_dir.glob("android-*/android.jar"), reverse=True):
                    if p.is_file():
                        return p
    for cand in ANDROID_JAR_CANDIDATES:
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        "Could not find android.jar. Install libandroid-23-java or set ANDROID_HOME."
    )


def _find_dx() -> list[str]:
    for name in ("dalvik-exchange", "dx"):
        found = shutil.which(name)
        if found:
            return [found]
    debian_dx = Path("/usr/lib/android-sdk/build-tools/debian/dx")
    if debian_dx.is_file():
        return [str(debian_dx)]
    raise FileNotFoundError("Could not find 'dalvik-exchange' or 'dx'. Install dalvik-exchange.")


def _find_smali_command(name: str) -> list[str]:
    found = shutil.which(name)
    if found:
        return [found]
    jar = os.environ.get("TOMOTORU_SMALI_JAR")
    if os.name == "nt" and jar and (Path(jar).is_file() or (jar.endswith("*") and Path(jar[:-1]).is_dir())):
        java = _find_tool("java")
        main = f"org.jf.{name}.Main"
        return [java, "-cp", jar, main]
    raise FileNotFoundError(f"Could not find '{name}' on PATH or TOMOTORU_SMALI_JAR.")


def _find_d8_command() -> list[str] | None:
    if os.name != "nt":
        return None
    sdk = next(
        (Path(os.environ[var]) for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.environ.get(var)),
        None,
    )
    candidates = [Path(os.environ["TOMOTORU_D8_JAR"])] if os.environ.get("TOMOTORU_D8_JAR") else []
    if sdk:
        candidates.extend(sorted((sdk / "build-tools").glob("*/lib/d8.jar"), reverse=True))
    for jar in candidates:
        if jar.is_file():
            return [_find_tool("java"), "-cp", str(jar), "com.android.tools.r8.D8"]
    return None


def _find_tool(name: str) -> str:
    found = shutil.which(name)
    if not found and os.name == "nt":
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            candidate = Path(java_home) / "bin" / f"{name}.exe"
            if candidate.is_file():
                found = str(candidate)
    if not found:
        raise FileNotFoundError(f"Could not find '{name}' on PATH.")
    return found


def _run(cmd: list[str], desc: str) -> None:
    print(f"  > {desc}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"Command failed ({cmd[0]}): {err}")


def offline_java_sources() -> list[Path]:
    source_dir = ANDROID_SERVER_SRC
    configured_source = os.environ.get("TOMOTORU_OFFLINE_SOURCE")
    if configured_source:
        source_dir = Path(configured_source)
        if not (source_dir / "OfflineServer.java").is_file():
            source_dir = source_dir / OFFLINE_SOURCE_RELATIVE
    if not (source_dir / "OfflineServer.java").is_file():
        raise FileNotFoundError(
            f"tomoffline sources are missing at {source_dir}. Set "
            "TOMOTORU_OFFLINE_SOURCE to the checkout root or Java package directory, "
            "or run: git submodule update --init -- tomoffline"
        )
    return sorted(source_dir.glob("*.java"))


def patch_sakana_application_smali(smali_path: Path) -> None:
    """Inject OfflineServer.start(this) at the start of SakanaApplication.onCreate()."""
    text = smali_path.read_text(encoding="utf-8")
    hook = "    invoke-static {p0}, Lcom/tomotoru/offline/OfflineServer;->start(Landroid/content/Context;)V"
    if hook in text:
        return

    anchor = ".method public onCreate()V"
    idx = text.find(anchor)
    if idx < 0:
        raise RuntimeError(f"Could not find '{anchor}' in {smali_path}")

    # Insert right after the .locals/.registers directive inside onCreate()
    lines = text[idx:].splitlines(keepends=True)
    insert_offset = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(".locals ") or stripped.startswith(".registers "):
            insert_offset = i + 1
            break
        if stripped == ".end method":
            break

    if insert_offset is None:
        raise RuntimeError(f"Could not find .locals/.registers in onCreate() of {smali_path}")

    lines.insert(insert_offset, f"\n{hook}\n")
    patched = text[:idx] + "".join(lines)
    smali_path.write_text(patched, encoding="utf-8")


def _replace_smali_method(text: str, signature: str, new_body: str) -> str:
    idx = text.find(signature)
    if idx < 0:
        raise RuntimeError(f"Could not find method signature '{signature}'")
    end_marker = ".end method"
    end_idx = text.find(end_marker, idx)
    if end_idx < 0:
        raise RuntimeError(f"Could not find .end method for '{signature}'")
    end_idx += len(end_marker)
    return text[:idx] + new_body + text[end_idx:]


def patch_sanrio_activity_smali(smali_path: Path) -> None:
    """Delegate screenshot/storage/gallery/share methods in SanrioActivity to ScreenshotHelper."""
    text = smali_path.read_text(encoding="utf-8")

    text = _replace_smali_method(
        text,
        ".method public static getExternalStrageDirectory()Ljava/lang/String;",
        ".method public static getExternalStrageDirectory()Ljava/lang/String;\n"
        "    .registers 1\n\n"
        "    invoke-static {}, Lcom/tomotoru/offline/ScreenshotHelper;->getExternalStorageDir()Ljava/lang/String;\n\n"
        "    move-result-object v0\n\n"
        "    return-object v0\n"
        ".end method",
    )

    text = _replace_smali_method(
        text,
        ".method public static scanPhotoGallery(Ljava/lang/String;Landroid/content/Context;)V",
        ".method public static scanPhotoGallery(Ljava/lang/String;Landroid/content/Context;)V\n"
        "    .registers 2\n"
        "    .param p0, \"filename\"    # Ljava/lang/String;\n"
        "    .param p1, \"context\"    # Landroid/content/Context;\n\n"
        "    invoke-static {p0, p1}, Lcom/tomotoru/offline/ScreenshotHelper;->scanPhotoGallery(Ljava/lang/String;Landroid/content/Context;)V\n\n"
        "    return-void\n"
        ".end method",
    )

    text = _replace_smali_method(
        text,
        ".method public static startExternalIntent(Ljava/lang/String;Ljava/lang/String;Landroid/content/Context;)V",
        ".method public static startExternalIntent(Ljava/lang/String;Ljava/lang/String;Landroid/content/Context;)V\n"
        "    .registers 3\n"
        "    .param p0, \"filename\"    # Ljava/lang/String;\n"
        "    .param p1, \"postStr\"    # Ljava/lang/String;\n"
        "    .param p2, \"context\"    # Landroid/content/Context;\n\n"
        "    invoke-static {p0, p1, p2}, Lcom/tomotoru/offline/ScreenshotHelper;->startExternalIntent(Ljava/lang/String;Ljava/lang/String;Landroid/content/Context;)V\n\n"
        "    return-void\n"
        ".end method",
    )

    text = _replace_smali_method(
        text,
        ".method public static sarCheckStoragePermission(Landroid/content/Context;)Z",
        ".method public static sarCheckStoragePermission(Landroid/content/Context;)Z\n"
        "    .registers 2\n"
        "    .param p0, \"cont\"    # Landroid/content/Context;\n\n"
        "    invoke-static {p0}, Lcom/tomotoru/offline/ScreenshotHelper;->checkStoragePermission(Landroid/content/Context;)Z\n\n"
        "    move-result v0\n\n"
        "    return v0\n"
        ".end method",
    )

    smali_path.write_text(text, encoding="utf-8")


def inject_embedded_server_into_dex(orig_dex_bytes: bytes) -> bytes:
    """Compile com.tomotoru.offline and merge it into the APK's classes.dex."""
    java_files = [str(p) for p in offline_java_sources() if p.name != "StandaloneMain.java"]
    javac_bin = _find_tool("javac")
    smali_cmd = _find_smali_command("smali")
    baksmali_cmd = _find_smali_command("baksmali")
    dx_cmd = _find_dx() if os.name != "nt" else None
    d8_cmd = _find_d8_command()
    if dx_cmd is None and d8_cmd is None:
        raise FileNotFoundError("Could not find d8.jar in the Android SDK; set ANDROID_HOME.")
    android_jar = _find_android_jar()

    work_dir = Path(tempfile.mkdtemp(prefix="tomotoru_dex_"))
    try:
        classes_dir = work_dir / "classes"
        classes_dir.mkdir()
        server_dex = work_dir / "server.dex"
        server_smali_dir = work_dir / "smali_server"
        orig_dex = work_dir / "orig_classes.dex"
        orig_smali_dir = work_dir / "smali_orig"
        out_dex = work_dir / "classes.dex"

        # 1. Compile embedded server Java -> .class (Java 8 bytecode, Android API 23)
        _run(
            [
                javac_bin,
                "-source",
                "1.8",
                "-target",
                "1.8",
                "-cp",
                str(android_jar),
                "-d",
                str(classes_dir),
                *java_files,
            ],
            f"Compiling {len(java_files)} embedded server Java files",
        )

        # 2. Convert .class -> server.dex
        if dx_cmd:
            _run(
                [*dx_cmd, "--dex", f"--output={server_dex}", str(classes_dir)],
                "Converting embedded server classes to DEX with dx",
            )
        else:
            dex_dir = work_dir / "d8"
            dex_dir.mkdir()
            class_files = [str(p) for p in sorted(classes_dir.rglob("*.class"))]
            _run([*d8_cmd, "--min-api", "23", "--output", str(dex_dir), *class_files],
                 "Converting embedded server classes to DEX with d8")
            shutil.copy2(dex_dir / "classes.dex", server_dex)

        # 3. Disassemble server.dex and original classes.dex
        orig_dex.write_bytes(orig_dex_bytes)
        _run(
            [*baksmali_cmd, "d", str(server_dex), "-o", str(server_smali_dir)],
            "Disassembling embedded server DEX",
        )
        _run(
            [*baksmali_cmd, "d", str(orig_dex), "-o", str(orig_smali_dir)],
            "Disassembling original APK classes.dex",
        )

        # 4. Merge com/tomotoru/offline smali into orig_smali_dir
        src_pkg = server_smali_dir / "com" / "tomotoru" / "offline"
        dst_pkg = orig_smali_dir / "com" / "tomotoru" / "offline"
        if dst_pkg.exists():
            shutil.rmtree(dst_pkg)
        shutil.copytree(src_pkg, dst_pkg)

        # 5. Hook SakanaApplication.onCreate() and SanrioActivity screenshot helpers
        app_smali = orig_smali_dir / "ls" / "sakana" / "SakanaApplication.smali"
        if not app_smali.is_file():
            raise FileNotFoundError(f"SakanaApplication.smali not found at {app_smali}")
        patch_sakana_application_smali(app_smali)
        print("  > Hooked ls.sakana.SakanaApplication.onCreate() -> OfflineServer.start(this)")

        act_smali = orig_smali_dir / "com" / "cyberstep" / "sanriocharacters" / "SanrioActivity.smali"
        if act_smali.is_file():
            patch_sanrio_activity_smali(act_smali)
            print("  > Patched SanrioActivity screenshot/gallery/share methods -> ScreenshotHelper")

        # 6. Reassemble merged classes.dex
        _run(
            [*smali_cmd, "a", str(orig_smali_dir), "-o", str(out_dex)],
            "Reassembling patched classes.dex",
        )
        return out_dex.read_bytes()
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
