"""Debug & runtime workaround Squirrel (.sk) patches for Tomotoru Offline (--debug mode).

These patches are intentionally separated from URL patching in patch_apk.py so that
Normal mode produces a pure URL + pkg.json patch with zero Squirrel logic changes, while
--debug mode injects runtime fallbacks, crash workarounds, and live /app_kitty/report telemetry:

  - 114.sk         : Live stack-trace reporting in UserData.load() and changeSceneBody()
  - 129.sk         : Live stack-trace reporting in TitleScene._pressedStart()
  - 158.sk         : SoundManagerKitty fallback to /music/BGM01.ogg for missing CDN BGM
  - 194.sk         : Disable AR (ENABLE_AR <- false) to avoid libsksmartar.so segfault on Android 12+
  - 267.sk         : Guard StageObject.setEffectScript(null) against missing effect resources
  - 270.sk         : Fallback for missing CDN /image/icon/* (.png/.jpg) textures and sprites
  - 384.sk         : Fix HomeUI.movePanel early-return + try/catch MOVEPANEL_ERR + guaranteed setEventStopper(false)
  - SK3DObject.sk  : Fallback for missing CDN .pm 3D models (flower.pm for /furniture/, kitty_face.pm otherwise)
"""

from __future__ import annotations

import sys
from pathlib import Path

from tools.squirrel.sk_decompiler import Decompiler
from tools.squirrel.sk_encoder import encode_sk
from tools.squirrel.sk_parser import parse_func_data, parse_header


def _decompile_sk_bytes(sk_bytes: bytes) -> str:
    _, token_data, strings = parse_header(sk_bytes)
    root = parse_func_data(token_data, strings)
    return Decompiler(root, strings).decompile()


def _fmt_exception_snippet(prefix_expr: str, report_call: str) -> str:
    """Build a Squirrel snippet that formats ex.message + ex.getStackInfo() and sends it."""
    return (
        f"local _dbg_msg = {prefix_expr};\n"
        "try {\n"
        '  _dbg_msg = _dbg_msg + ex.message + "\\n";\n'
        "  foreach (_si in ex.getStackInfo()) {\n"
        '    _dbg_msg = _dbg_msg + "  at " + _si[0] + "(" + _si[1] + ":" + _si[2] + ")\\n";\n'
        "  }\n"
        "} catch (_e2) {\n"
        '  _dbg_msg = _dbg_msg + ex;\n'
        "}\n"
        f"{report_call}(_dbg_msg);"
    )


def patch_debug_hooks(entity_name: str, sk_bytes: bytes) -> tuple[bytes, int]:
    """Inject live exception reporting to /app_kitty/report into 114.sk and 129.sk."""
    if sk_bytes[:2] != b"\xfa\xfa" or entity_name not in ("114", "129"):
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    hooks = 0

    if entity_name == "114":
        # 1. Report UserData.load() exceptions live with stack trace
        target_ud = 'printf("userdata broken.....\\n");'
        if target_ud in src:
            snippet = _fmt_exception_snippet('"USERDATA_LOAD_ERR: "', "sendReportToServer")
            src = src.replace(target_ud, snippet + "\n          " + target_ud, 1)
            hooks += 1

        # 2. Report changeScene setup failures live with stack trace
        target_scn = 'print("changeScene setup failure " <+ scn <+ "\\n");'
        if target_scn in src:
            snippet = _fmt_exception_snippet('"CHANGESCENE_ERR(" <+ scn <+ "): "', "sendReportToServer")
            src = src.replace(target_scn, snippet + "\n      " + target_scn, 1)
            hooks += 1

    elif entity_name == "129":
        # 3. Catch & report uncaught exceptions inside TitleScene._pressedStart() worker thread
        target_start = "this.is_starting = true;\n        _pressedStart();"
        if target_start in src:
            snippet = _fmt_exception_snippet('"PRESSED_START_ERR: "', "::gamesession.sendReportToServer")
            replacement = (
                "try {\n"
                "          this.is_starting = true;\n"
                "          _pressedStart();\n"
                "        } catch (ex) {\n"
                f"          {snippet}\n"
                "          this.is_starting = false;\n"
                "        }"
            )
            src = src.replace(target_start, replacement, 1)
            hooks += 1

    if hooks == 0:
        return sk_bytes, 0

    return encode_sk(src), hooks


def patch_missing_bgm_fallback(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 158.sk (SoundManagerKitty) so missing CDN /music/*.ogg files fall back to BGM01.ogg."""
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = '    local r = ::getResource("/music/" <+ name);'
    repl = (
        '    local r = ::getResource("/music/" <+ name);\n'
        '    if (!r) r = ::getResource("/music/BGM01.ogg");'
    )
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def patch_load_arutils_addon(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 107.sk so skLoadAddon("sksmartar") registers ARUtils on Android even when ENABLE_AR is false.

    ARModuleAndroid.sk remains gated behind ENABLE_AR so Sony SmartAR camera/SLAM is never started,
    while ARUtils (storage/gallery/share JNI bindings) stays available for screenshots.
    """
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = (
        "if (ENABLE_AR) {\n\n"
        "  local os_name = skGetOSName();\n"
        '  if (os_name == "iOS") {\n'
        '    skLoadAddon("skARKit");\n'
        '    runsk("/src/ar/ARModuleIOS.sk");\n'
        "  }\n"
        '  else if (os_name == "Android") {\n'
        '    skLoadAddon("sksmartar");\n'
        '    runsk("/src/ar/ARModuleAndroid.sk");\n'
        "  }\n"
        "}"
    )
    repl = (
        'if (skGetOSName() == "Android") {\n'
        '  skLoadAddon("sksmartar");\n'
        "}\n"
        "if (ENABLE_AR) {\n"
        "  local os_name = skGetOSName();\n"
        '  if (os_name == "iOS") {\n'
        '    skLoadAddon("skARKit");\n'
        '    runsk("/src/ar/ARModuleIOS.sk");\n'
        "  }\n"
        '  else if (os_name == "Android") {\n'
        '    runsk("/src/ar/ARModuleAndroid.sk");\n'
        "  }\n"
        "}"
    )
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def patch_disable_ar(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 194.sk to set ENABLE_AR <- false so tapping AR doesn't segfault libsksmartar.so."""
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = 'ENABLE_AR <- _os.equals("Android") || _os.equals("iOS");'
    repl = "ENABLE_AR <- false;"
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def patch_stageobject_null_effect(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 267.sk (StageObject.setEffectScript) to ignore null effect resources."""
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = "  function setEffectScript(r) {\n    local sk3 = r.getContent();"
    repl = "  function setEffectScript(r) {\n    if (!r) return;\n    local sk3 = r.getContent();"
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def patch_missing_icon_fallback(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 270.sk (getTexture/getSprite) so missing CDN /image/icon/* assets fall back safely.

    Standalone APK v1.0.4 bundles 8 core SXR packages (0,1,5,6,7,9,a,b) and omits the
    CDN-only 2D icon packs (2,3,4,8) under /image/icon/*. Without a fallback, callers like
    MenuButtonSetting.getIconSprite() crash dereferencing null.blend.
    """
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    patches = 0

    target_tex = (
        "  if (src instanceof SS_STRING) {\n"
        "    src = ::getResource(src);\n"
        "    if (!src) return null;\n"
        "  }"
    )
    repl_tex = (
        "  if (src instanceof SS_STRING) {\n"
        "    local _orig_path = src;\n"
        "    src = ::getResource(src);\n"
        '    if (!src && (_orig_path.endsWith(".png") || _orig_path.endsWith(".jpg"))) {\n'
        '      src = ::getResource("/ui/image/base/icon_change.png");\n'
        "    }\n"
        "    if (!src) return null;\n"
        "  }"
    )
    if target_tex in src:
        src = src.replace(target_tex, repl_tex, 1)
        patches += 1

    target_sp = (
        "    if (!r && old_path) {\n"
        "      r = ::getResource(old_path);\n"
        "    }\n"
        "\n"
        "    if (!r) {\n"
        "      return errsp ? getErrorSprite(path) : null;\n"
        "    }"
    )
    repl_sp = (
        "    if (!r && old_path) {\n"
        "      r = ::getResource(old_path);\n"
        "    }\n"
        '    if (!r && (path.endsWith(".png") || path.endsWith(".jpg"))) {\n'
        '      r = ::getResource(path.indexOf("/menu/bg_") >= 0 ? "/ui/image/base/back_y.png" : "/ui/image/base/icon_change.png");\n'
        "    }\n"
        "\n"
        "    if (!r) {\n"
        "      return errsp ? getErrorSprite(path) : null;\n"
        "    }"
    )
    if target_sp in src:
        src = src.replace(target_sp, repl_sp, 1)
        patches += 1

    if patches == 0:
        return sk_bytes, 0

    return encode_sk(src), patches


def patch_charafocus_finish_mdl(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 376.sk (CharaFocusPanel.CharaSentenceProcessor._finishProcess) undefined `_mdl` bug.

    Original CyberStep v1.0.4 bytecode calls showGetHapinessEffect(_mdl) where `_mdl` is undefined
    (the member is `this.model`). When MAIN_CMD_COMUNICATION_TAP returns add_gm > 0, that throws
    an uncaught exception before SentenceProcessor sets `this.thread = null`, permanently softlocking
    the character at the end of dialogue bubbles.
    """
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = "::gamesession.getCurrentScene().showGetHapinessEffect(_mdl);"
    repl = "::gamesession.getCurrentScene().showGetHapinessEffect(this.model);"
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def patch_sentence_processor_cleanup(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 421.sk (SentenceProcessor.startSentence) so `this.thread = null` always runs after _finishProcess."""
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = "      _finishProcess();\n      this.thread = null;"
    repl = (
        "      try {\n"
        "        _finishProcess();\n"
        "      } catch (ex) {\n"
        "        printexception(ex);\n"
        "      }\n"
        "      this.thread = null;"
    )
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def patch_homeui_eventstopper(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch 384.sk (HomeUI.movePanel) so setEventStopper(false) is always reached.

    Fixes an early-return / null prev_clazz bug on initial moveTopPanel() during scene load
    where pnl_eventstopper stayed enabled forever, blocking all touches on Menu and D-pad,
    and reports any movePanel exceptions via /app_kitty/report (MOVEPANEL_ERR).
    """
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    patches = 0

    target_bgm = (
        "        local _scene = ::gamesession.getCurrentScene();\n"
        "        if (getclass(_scene) != ::SanrioGameScene) return;\n"
        '        _scene.playStageBGM(prev_clazz["unique_bgm"] != null);'
    )
    repl_bgm = (
        "        local _scene = ::gamesession.getCurrentScene();\n"
        "        if (getclass(_scene) == ::SanrioGameScene) {\n"
        '          _scene.playStageBGM(prev_clazz ? (prev_clazz["unique_bgm"] != null) : false);\n'
        "        }"
    )
    if target_bgm in src:
        src = src.replace(target_bgm, repl_bgm, 1)
        patches += 1

    target_open = "    newthread().start(function() {\n      setEventStopper(true);"
    target_close = "      setEventStopper(false);\n    }, this)"
    if target_open in src and target_close in src:
        catch_body = _fmt_exception_snippet('"MOVEPANEL_ERR: "', "::gamesession.sendReportToServer")
        src = src.replace(
            target_open,
            "    newthread().start(function() {\n      setEventStopper(true);\n      try {",
            1,
        )
        src = src.replace(
            target_close,
            f"      }} catch (ex) {{\n        {catch_body}\n      }}\n      setEventStopper(false);\n    }}, this)",
            1,
        )
        patches += 1

    # Show clean native SimpleDialog alert when AR is disabled (Home button + movePanel catch-all)
    target_ar_btn = "  function actionevent_movear(e) {\n    e.consume();"
    repl_ar_btn = (
        "  function actionevent_movear(e) {\n"
        "    e.consume();\n"
        "    if (!::ENABLE_AR) {\n"
        "      newthread().start(function() {\n"
        "        try {\n"
        "          local _dlg = ::SimpleDialog();\n"
        '          _dlg.lbl_msg.setText(::dict.get("can_not_use_AR"));\n'
        "          _dlg.show();\n"
        "        } catch (ex) {\n"
        '          ::gamesession.sendReportToServer("AR_DLG_ERR: " + ex);\n'
        "        }\n"
        "      }, this);\n"
        "      return;\n"
        "    }"
    )
    if target_ar_btn in src:
        src = src.replace(target_ar_btn, repl_ar_btn, 1)
        patches += 1

    target_ar_panel = (
        "  function movePanel(clazz, pop = false, param = null, trans = null) {\n"
        "    if (getclass(this.current_panel) == clazz) return;"
    )
    repl_ar_panel = (
        "  function movePanel(clazz, pop = false, param = null, trans = null) {\n"
        "    if (clazz == ::ARPanel && !::ENABLE_AR) {\n"
        "      newthread().start(function() {\n"
        "        local _dlg = ::SimpleDialog();\n"
        '        _dlg.lbl_msg.setText(::dict.get("can_not_use_AR"));\n'
        "        _dlg.show();\n"
        "      }, this);\n"
        "      return;\n"
        "    }\n"
        "    if (getclass(this.current_panel) == clazz) return;"
    )
    if target_ar_panel in src:
        src = src.replace(target_ar_panel, repl_ar_panel, 1)
        patches += 1

    # Fix saveImage RGBAImage dimensions to match sk3dview.bnd.w/h (sprite.tex0)
    target_save_img = (
        "  function saveImage(sprite, path, width, height) {\n"
        "    local rgba_image = RGBAImage(width, height);\n"
        "    local x = 0;\n"
        "    local y = 0;\n"
        "    local w = getGameScene().getStage().sk3dview.bnd.w;\n"
        "    local h = getGameScene().getStage().sk3dview.bnd.h;"
    )
    repl_save_img = (
        "  function saveImage(sprite, path, width, height) {\n"
        "    local x = 0;\n"
        "    local y = 0;\n"
        "    local w = getGameScene().getStage().sk3dview.bnd.w;\n"
        "    local h = getGameScene().getStage().sk3dview.bnd.h;\n"
        "    local rgba_image = RGBAImage(int(w), int(h));"
    )
    if target_save_img in src:
        src = src.replace(target_save_img, repl_save_img, 1)
        patches += 1

    if patches == 0:
        return sk_bytes, 0

    return encode_sk(src), patches


def patch_missing_pm_fallback(sk_bytes: bytes) -> tuple[bytes, int]:
    """Patch SK3DObject.sk (in main.sxr) so missing CDN .pm models fall back to a valid bundled .pm.

    Standalone APK v1.0.4 bundles all 3D rooms, animations, facial rigs, and base furniture,
    but omits the CDN-only character clothes/hands/extra-furniture packs (2,3,4,8).
    """
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, 0

    src = _decompile_sk_bytes(sk_bytes)
    target = '      if (!r) throw Exception("source path not found : " <+ src);'
    repl = (
        '      if (!r && src.endsWith(".pm")) {\n'
        '        r = ::getResource(src.indexOf("/furniture/") >= 0 ? "/res/goods/goods_1/flower.pm" : "/res/facial/kitty_face.pm");\n'
        "      }\n"
        '      if (!r) throw Exception("source path not found : " <+ src);'
    )
    if target not in src:
        return sk_bytes, 0

    return encode_sk(src.replace(target, repl, 1)), 1


def apply_debug_patch(
    entity_name: str, sk_bytes: bytes, include_telemetry: bool = True
) -> tuple[bytes, str | None]:
    """Dispatch runtime compatibility & debug Squirrel patches by entity name.

    When include_telemetry=False (release offline/online modes), applies all Android 12+
    and bug fixes (AR alert, MediaStore screenshots, 376/421 dialogue softlock, CDN fallbacks)
    without injecting live /app_kitty/report crash hooks.
    """
    if sk_bytes[:2] != b"\xfa\xfa":
        return sk_bytes, None

    if entity_name == "107":
        patched, count = patch_load_arutils_addon(sk_bytes)
        if count > 0:
            return patched, "Enabled ARUtils addon load (sksmartar) independently of ENABLE_AR in 107.sk"
    elif entity_name in ("114", "129") and include_telemetry:
        patched, count = patch_debug_hooks(entity_name, sk_bytes)
        if count > 0:
            return patched, f"Injected {count} live error hook(s) into {entity_name}.sk"
    elif entity_name == "158":
        patched, count = patch_missing_bgm_fallback(sk_bytes)
        if count > 0:
            return patched, f"Injected {count} missing-BGM fallback(s) into 158.sk"
    elif entity_name == "194":
        patched, count = patch_disable_ar(sk_bytes)
        if count > 0:
            return patched, "Disabled AR (ENABLE_AR <- false) in 194.sk"
    elif entity_name == "267":
        patched, count = patch_stageobject_null_effect(sk_bytes)
        if count > 0:
            return patched, "Guarded StageObject.setEffectScript(null) in 267.sk"
    elif entity_name == "270":
        patched, count = patch_missing_icon_fallback(sk_bytes)
        if count > 0:
            return patched, f"Injected {count} missing-icon fallback(s) into 270.sk"
    elif entity_name == "376":
        patched, count = patch_charafocus_finish_mdl(sk_bytes)
        if count > 0:
            return patched, "Fixed undefined _mdl -> this.model in 376.sk (CharaFocusPanel._finishProcess)"
    elif entity_name == "384":
        patched, count = patch_homeui_eventstopper(sk_bytes)
        if count > 0:
            return patched, f"Patched HomeUI guards/AR alert/screenshots ({count}) in 384.sk"
    elif entity_name == "421":
        patched, count = patch_sentence_processor_cleanup(sk_bytes)
        if count > 0:
            return patched, "Guarded SentenceProcessor._finishProcess() cleanup in 421.sk"
    elif entity_name == "SK3DObject.sk":
        patched, count = patch_missing_pm_fallback(sk_bytes)
        if count > 0:
            return patched, f"Injected {count} missing-3D-model fallback(s) into SK3DObject.sk"

    return sk_bytes, None
