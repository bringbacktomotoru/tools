from pathlib import Path

from tools.android.parallel_apk import rewrite_hardcoded_ids, rewrite_manifest


def test_manifest_rewrite_isolates_package_and_authority(tmp_path: Path) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        """<manifest xmlns:android="http://schemas.android.com/apk/res/android"
          package="com.cyberstep.sanriocharacters">
          <permission android:name="com.cyberstep.sanriocharacters.permission.PUSH"/>
          <application android:name=".GameApplication">
            <activity android:name=".SanrioActivity"/>
            <provider android:name="android.support.v4.content.FileProvider"
              android:authorities="com.cyberstep.sanriocharacters.fileprovider"/>
          </application>
        </manifest>""",
        encoding="utf-8",
    )

    old, authorities, classes = rewrite_manifest(
        manifest, "com.cyberstep.sanriocharacters.smoketest"
    )

    xml = manifest.read_text(encoding="utf-8")
    assert old == "com.cyberstep.sanriocharacters"
    assert 'package="com.cyberstep.sanriocharacters.smoketest"' in xml
    assert "com.cyberstep.sanriocharacters.smoketest.permission.PUSH" in xml
    assert (
        'android:authorities="com.cyberstep.sanriocharacters.smoketest.fileprovider"'
        in xml
    )
    assert "com.cyberstep.sanriocharacters.SanrioActivity" in classes
    assert "com.cyberstep.sanriocharacters.GameApplication" in classes
    assert authorities == [
        (
            "com.cyberstep.sanriocharacters.fileprovider",
            "com.cyberstep.sanriocharacters.smoketest.fileprovider",
        )
    ]


def test_hardcoded_ids_rewritten_without_renaming_class_descriptors(
    tmp_path: Path,
) -> None:
    smali = tmp_path / "smali" / "Example.smali"
    smali.parent.mkdir()
    smali.write_text(
        '.class public Lcom/cyberstep/sanriocharacters/SanrioActivity;\n'
        '.field public static final APPLICATION_ID:Ljava/lang/String; = '
        '"com.cyberstep.sanriocharacters"\n'
        '    const-string v0, '
        '"com.cyberstep.sanriocharacters.fileprovider"\n',
        encoding="utf-8",
    )

    changed = rewrite_hardcoded_ids(
        tmp_path,
        "com.cyberstep.sanriocharacters",
        "com.cyberstep.sanriocharacters.smoketest",
    )
    result = smali.read_text(encoding="utf-8")

    assert changed == 2
    assert (
        ".class public Lcom/cyberstep/sanriocharacters/SanrioActivity;" in result
    )
    assert '"com.cyberstep.sanriocharacters.smoketest"' in result
    assert '"com.cyberstep.sanriocharacters.smoketest.fileprovider"' in result
