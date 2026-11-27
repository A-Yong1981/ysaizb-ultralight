# -*- coding: utf-8 -*-
"""
ysaizb-ultralight 架构与避坑回归测试：
涵盖隔离端口/环境检查 (U01~U05) 及通用红线 (P01, P04, P10, P14, P18, P20, P36)。
"""
import json
import re
import sys
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = HUB_DIR.parent


def test_u_port_and_obs_isolation():
    """确保 Ultralight 方案与原 ysaizb 端口 (:8010/:28900) 及 OBS 场景集合完全隔离。"""
    cfg = json.loads((HUB_DIR / "config.json").read_text(encoding="utf-8"))
    ex_cfg = json.loads((HUB_DIR / "config.example.json").read_text(encoding="utf-8"))
    for c, label in ((cfg, "config.json"), (ex_cfg, "config.example.json")):
        assert int(c["server"]["port"]) == 28910, f"{label} server.port must be 28910"
        assert c["livetalking"]["base_url"] == "http://127.0.0.1:8020", f"{label} base_url must be :8020"
        assert "ultralight" in c, f"{label} missing ultralight config block"

    must_not_have_old_ports = [
        HUB_DIR / "server.py",
        HUB_DIR / "health_monitor.py",
        HUB_DIR / "profile_manager.py",
        HUB_DIR / "danmaku_relay.py",
        HUB_DIR / "tiktok_relay.py",
        HUB_DIR / "static" / "index.html",
        ROOT_DIR / "launcher" / "backend.py",
        ROOT_DIR / "launcher" / "app.py",
        ROOT_DIR / "engine" / "ultralight_server.py",
        ROOT_DIR / "env.bat",
    ]
    for p in must_not_have_old_ports:
        txt = p.read_text(encoding="utf-8")
        assert "127.0.0.1:28900" not in txt, f"{p.name} still references legacy hub port 28900"
        assert "127.0.0.1:8010" not in txt, f"{p.name} still references legacy engine port 8010"

    pm_txt = (HUB_DIR / "profile_manager.py").read_text(encoding="utf-8")
    bk_txt = (ROOT_DIR / "launcher" / "backend.py").read_text(encoding="utf-8")
    assert 'SCENE_COLLECTION_NAME = "AI_Live_Hub_Ultralight"' in pm_txt
    assert "AI_Live_Hub_Ultralight" in bk_txt
    print("[PASS] test_u_port_and_obs_isolation")


def test_p01_bat_pure_ascii_crlf():
    """P01: 所有 .bat 必须纯 ASCII 且严格 CRLF。"""
    bats = list(ROOT_DIR.glob("*.bat")) + list((ROOT_DIR / "scripts").glob("*.bat")) + list((ROOT_DIR / "gptsovits").glob("*.bat"))
    assert len(bats) >= 15
    for b in bats:
        raw = b.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise AssertionError(f"{b} contains non-ASCII character: {exc}")
        assert b"\r\n" in raw, f"{b} missing CRLF"
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{b} has bare LF"
    print(f"[PASS] test_p01_bat_pure_ascii_crlf ({len(bats)} files)")


def test_p04_requirements_hygiene():
    """P04/U06: requirements.txt 锁定 websockets==12.0 与 CUDA 12 兼容的 onnxruntime-gpu==1.22.0，不含 torch。"""
    req = (HUB_DIR / "requirements.txt").read_text(encoding="utf-8")
    assert "websockets==12.0" in req
    assert "onnxruntime-gpu==1.22.0" in req
    assert "kaldi-native-fbank" in req
    for line in req.splitlines():
        pkg = line.split("#")[0].strip().lower()
        assert not re.match(r"^(torch|torchvision|torchaudio)\b", pkg), f"requirements.txt must not install {pkg}"
    print("[PASS] test_p04_requirements_hygiene")


def test_p10_single_file_html():
    """P10: index.html 与 overlay.html 为零构建原生单文件且包含关键路由。"""
    idx = (HUB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    ov = (HUB_DIR / "static" / "overlay.html").read_text(encoding="utf-8")
    assert "Ultralight" in idx
    assert ":28910/stage" in idx
    assert "/api/avatar/task" in idx
    assert "/api/events" in ov and "RTCPeerConnection" in ov
    for banned in ("vite", "react-dom", "webpack"):
        assert banned not in idx.lower() and banned not in ov.lower()
    print("[PASS] test_p10_single_file_html")


def test_p14_adult_compliance():
    """P14: 讲稿、暖场库与默认配置禁止向未成年人直接喊话。"""
    banned = ("小朋友们好", "小朋友好", "小朋友记住", "同学们请点赞", "各位小朋友", "孩子们好")
    files = list((HUB_DIR / "kb_data").glob("*.md")) + [
        HUB_DIR / "warmup_topics.json",
        HUB_DIR / "config.example.json",
    ]
    for f in files:
        if not f.is_file():
            continue
        txt = f.read_text(encoding="utf-8")
        for kw in banned:
            assert kw not in txt, f"Compliance violation '{kw}' found in {f}"
    print("[PASS] test_p14_adult_compliance")


def test_p18_p20_no_bak_no_key_in_examples():
    """P18 & P20: 禁止残留 .bak/_old/archive；示例配置严禁真实 API Key。"""
    for p in ROOT_DIR.rglob("*"):
        if any(part in p.parts for part in (".git", "__pycache__", "Ultralight-Digital-Human")):
            continue
        name = p.name.lower()
        assert not name.endswith(".bak") and "_old" not in name and name != "archive", f"Backup junk found: {p}"

    for name in ("config.example.json", "platform_profiles.example.json"):
        txt = (HUB_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"sk-[A-Za-z0-9]{16,}", txt), f"Real API key leaked in {name}"
    print("[PASS] test_p18_p20_no_bak_no_key_in_examples")


def test_u01_u03_ultralight_pipeline_and_server_contracts():
    """U01/U02/U03: 训练流水线静音拦截、ONNX opset11 dynamo=False 导出、引擎接口契约。"""
    pipe_txt = (ROOT_DIR / "scripts" / "ultralight_pipeline.py").read_text(encoding="utf-8")
    assert "_probe_video" in pipe_txt and "if need_train and not has_audio:" in pipe_txt
    assert "dynamo=False" in pipe_txt and '"opset_version": 11' in pipe_txt
    assert "ensure_onnxruntime_cuda_paths" in pipe_txt and "wait3" in pipe_txt

    srv_txt = (ROOT_DIR / "engine" / "ultralight_server.py").read_text(encoding="utf-8")
    for route in ('"/offer"', '"/human"', '"/humanaudio"', '"/interrupt_talk"', '"/is_speaking"', '"/set_audiotype"', '"/api/avatar/list"', '"/api/avatar/task"'):
        assert route in srv_txt, f"Missing route {route} in ultralight_server.py"

    enc_txt = (ROOT_DIR / "engine" / "audio_encoders.py").read_text(encoding="utf-8")
    assert "WeNetEncoder" in enc_txt and "HuBERTEncoder" in enc_txt
    assert "half()" in enc_txt, "HuBERT must use FP16 half() on RTX 2080"

    app_txt = (ROOT_DIR / "launcher" / "app.py").read_text(encoding="utf-8")
    for sym in ("_build_tab_train", "on_open_health_dialog", "on_save_sched_cfg", "on_save_bg_cfg", "on_clone_voice_dialog", "on_train_start", "on_train_ai_recommend", "_toggle_tr_advanced", "on_train_clone_outfit"):
        assert sym in app_txt, f"Desktop app missing integrated control unit: {sym}"
    # 防止 Frame 后建于 Treeview 导致 Tk stacking order 遮挡主播管理表格
    assert "self.tv_avatars.master =" not in app_txt and "in_=tv_wrap" not in app_txt
    # Tab 4 底部互动区固定 bottom 并分两行展示动作，预览画布自适应真实尺寸不裁切
    assert 'ctrl_wrap.pack(side="bottom", fill="x")' in app_txt
    assert "CompactPrimary.TButton" in app_txt and "_on_live_canvas_configure" in app_txt
    bk_txt = (ROOT_DIR / "launcher" / "backend.py").read_text(encoding="utf-8")
    assert "_wait_bat_service" in bk_txt and "120.0" in bk_txt
    assert ".sentinel" in bk_txt
    assert bk_txt.index("launch_obs(obs_exe") < bk_txt.index("http://127.0.0.1:28910/api/platform/switch")
    assert (ROOT_DIR / "launcher" / "ultralight_config_kb.md").is_file()
    assert "export_only" in pipe_txt and "base_avatar" in pipe_txt
    print("[PASS] test_u01_u03_ultralight_pipeline_and_server_contracts")


def test_overlay_subtitles_and_free_fonts():
    """U04: 知识库章节卡与口播字幕开关/效果控制 + 免费可商用(SIL OFL 1.1)离线字体校验。"""
    import sys

    if str(HUB_DIR) not in sys.path:
        sys.path.insert(0, str(HUB_DIR))
    import overlay_presets as op

    ov_html = (HUB_DIR / "static" / "overlay.html").read_text(encoding="utf-8")
    for proprietary in ("Microsoft YaHei", "SimSun", "SimHei", "PingFang SC"):
        assert proprietary not in ov_html, f"Proprietary font '{proprietary}' found in overlay.html"
    assert 'id="right-card"' in ov_html and "applyOverlayConfig" in ov_html and "overlay_update" in ov_html

    fonts_dir = HUB_DIR / "static" / "fonts"
    for fname in ("SmileySans-Oblique.ttf", "LXGWWenKaiLite-Regular.ttf", "LICENSE_FONTS.txt"):
        assert (fonts_dir / fname).is_file(), f"Missing free font asset: {fname}"
    for fp in op.OVERLAY_FONT_PRESETS:
        assert "SIL OFL" in fp["license"]
        resolved = op.resolve_font_path(fp["id"], fonts_dir)
        assert resolved is not None and resolved.is_file(), f"Unresolved free font file for {fp['id']}"

    idx_html = (HUB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    for dom_id in ("ov-show-kb", "ov-show-sub", "ov-board-pos", "ov-font", "ov-effect", "saveOverlayCfg"):
        assert dom_id in idx_html, f"Missing WebUI overlay control: {dom_id}"

    app_txt = (ROOT_DIR / "launcher" / "app.py").read_text(encoding="utf-8")
    for sym in ("var_ov_kb", "var_ov_sub", "var_ov_pos", "var_ov_font", "var_ov_eff", "on_save_overlay_quick", "on_open_overlay_dialog"):
        assert sym in app_txt, f"Missing desktop overlay control: {sym}"
    print("[PASS] test_overlay_subtitles_and_free_fonts")


if __name__ == "__main__":
    test_u_port_and_obs_isolation()
    test_p01_bat_pure_ascii_crlf()
    test_p04_requirements_hygiene()
    test_p10_single_file_html()
    test_p14_adult_compliance()
    test_p18_p20_no_bak_no_key_in_examples()
    test_u01_u03_ultralight_pipeline_and_server_contracts()
    test_overlay_subtitles_and_free_fonts()
    print("ALL REGRESSION CHECKS PASSED")
