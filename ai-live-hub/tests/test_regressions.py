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
if str(HUB_DIR) not in sys.path:
    sys.path.insert(0, str(HUB_DIR))

import config_guide  # noqa: E402

# 全新克隆没有 config.json / platform_profiles.json（被 .gitignore 排除）：从 *.example.json 自动生成
config_guide.ensure_runtime_configs(HUB_DIR)


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
        HUB_DIR / "static" / "overlay.html",
        HUB_DIR / "static" / "access-guide.html",
        ROOT_DIR / "launcher" / "backend.py",
        ROOT_DIR / "launcher" / "app.py",
        ROOT_DIR / "engine" / "ultralight_server.py",
        ROOT_DIR / "env.bat",
    ]
    for p in must_not_have_old_ports:
        txt = p.read_text(encoding="utf-8")
        assert "127.0.0.1:28900" not in txt, f"{p.name} still references legacy hub port 28900"
        assert "127.0.0.1:8010" not in txt, f"{p.name} still references legacy engine port 8010"

    # 网页控制台与桌面端统一用 Ultralight 名称，不再显示旧方案「泳昇多模态直播平台」/ LiveTalking
    idx_txt = (HUB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    assert "泳昇 Ultralight 数字人直播平台" in idx_txt and "泳昇多模态直播平台" not in idx_txt
    for p in (HUB_DIR / "static" / "index.html", HUB_DIR / "static" / "access-guide.html"):
        assert "LiveTalking" not in p.read_text(encoding="utf-8"), f"{p.name} still shows legacy LiveTalking wording"
    assert "NO_CACHE" in (HUB_DIR / "server.py").read_text(encoding="utf-8")

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


def test_topic_router_viewer_requests():
    """主题点播：按主题索引全部讲稿；明确点播命中主题，普通提问/闲聊/知识库没有的主题不点播。"""
    import asyncio
    import topic_router as tr

    idx = tr.TopicIndex(HUB_DIR / "kb_data", {"chunk_max_chars": 65})
    res = idx.build()
    assert res["topics"] >= 10 and res["docs"] >= 3, res
    expect = {
        "主播先讲讲防骗吧": "防骗",
        "想听三国": "三国",
        "能不能说说AI换脸": "换脸",
        "聊聊逆风局": "逆风",
        "介绍一下RAG": "RAG",
    }
    for text, word in expect.items():
        r = asyncio.run(idx.route(None, text, [], ""))
        assert r["is_request"] and word.lower() in r["topic"]["title"].lower(), (text, r["reason"], r.get("topic"))
        plan = idx.build_plan(r["tid"], user="测试")
        assert plan and len(plan["beats"]) >= 2 and "测试" in plan["beats"][0]["text"] and plan["beats"][-1].get("outro")
    for text in ("AI会犯错吗", "主播好", "讲讲量子力学", "验证码能告诉别人吗"):
        r = asyncio.run(idx.route(None, text, [], ""))
        assert not r["is_request"], (text, r)
    assert tr.fill_template("{user}想听「{title}」{user.__class__}", user="A", title="B") == "A想听「B」{user.__class__}"
    print(f"[PASS] test_topic_router_viewer_requests (topics={res['topics']})")


def test_config_guide_wizard_presets_doctor():
    """傻瓜配置：每项都有白话说明；预设/向导写入 env.bat 保持 ASCII+CRLF；体检能发现 2080 主机红线问题。"""
    import shutil
    import tempfile

    for it in config_guide.GUIDE_ITEMS:
        assert it["plain"] and it["label"] and "recommend" in it, it["key"]
    with tempfile.TemporaryDirectory() as td:
        hub = Path(td) / "hub"
        hub.mkdir()
        for f in ("config.example.json", "platform_profiles.example.json"):
            shutil.copyfile(HUB_DIR / f, hub / f)
        shutil.copyfile(ROOT_DIR / "env.bat", Path(td) / "env.bat")
        assert set(config_guide.ensure_runtime_configs(hub)) == {"config.json", "platform_profiles.json"}
        w = config_guide.wizard({"pc": "busy", "content": "interactive", "ai": "rules"})
        res = config_guide.apply_patch(hub, Path(td), w["patch"])
        assert res["restart_engine"] is True and res["changed"]
        env = config_guide.parse_env_bat(Path(td) / "env.bat")
        assert env["UL_ASR"] == "wenet" and env["UL_OUT_HEIGHT"] == "720" and env["UL_HOST"] == "127.0.0.1"
        # 个人设置写进 env.local.bat，env.bat 保持仓库原样（否则 git pull 会被拒绝）
        assert (Path(td) / "env.bat").read_bytes() == (ROOT_DIR / "env.bat").read_bytes()
        raw = (Path(td) / "env.local.bat").read_bytes()
        assert raw.isascii() and b"\n" not in raw.replace(b"\r\n", b"") and b'set "UL_ASR=wenet"' in raw
        cfg = json.loads((hub / "config.json").read_text(encoding="utf-8"))
        assert cfg["knowledge_base"]["topic_router"]["detect_mode"] == "rules"
        try:
            config_guide.update_env_bat(Path(td) / "env.bat", {"UL_HOST": "x & del *"})
            raise AssertionError("unsafe env value must be rejected")
        except ValueError:
            pass
    bad = config_guide.doctor({}, {"UL_HOST": "0.0.0.0", "UL_MAX_SESSION": "2", "UL_MAX_FRAMES": "600",
                                   "UL_OUT_HEIGHT": "0", "UL_MAX_LOADED": "4"}, topic_count=0)
    levels = {f["title"]: f["level"] for f in bad}
    assert any(l == "error" for l in levels.values()) and len(bad) >= 4, levels
    print(f"[PASS] test_config_guide_wizard_presets_doctor (items={len(config_guide.GUIDE_ITEMS)}, presets={len(config_guide.PRESETS)})")


def test_engine_security_and_perf_contracts():
    """引擎：默认只监听本机、CORS 不放行 *、.pth 安全加载、串行说话队列、打断代次、显存上限、动态 batch、训练子进程。"""
    srv = (ROOT_DIR / "engine" / "ultralight_server.py").read_text(encoding="utf-8")
    enc = (ROOT_DIR / "engine" / "audio_encoders.py").read_text(encoding="utf-8")
    pipe = (ROOT_DIR / "scripts" / "ultralight_pipeline.py").read_text(encoding="utf-8")
    run_bat = (ROOT_DIR / "scripts" / "run_engine.bat").read_text(encoding="utf-8")
    assert 'os.environ.get("UL_HOST", "127.0.0.1")' in srv and 'host="0.0.0.0"' not in srv
    assert 'defaults={"*"' not in srv and "DEFAULT_CORS_ORIGINS" in srv
    assert "weights_only=True" in enc and "UL_ALLOW_UNSAFE_PTH" in enc
    assert "weights_only=False" not in srv and "weights_only=False" not in pipe
    assert "submit_text" in srv and "_flush_gen" in srv and "_apply_flush" in srv and "split_sentences" in srv
    assert "gpu_mem_limit" in enc and "kSameAsRequested" in enc
    assert "OrderedDict" in srv and "_evict_lru" in srv and "out_height" in srv
    assert "subprocess.Popen" in srv and "code=409" in srv
    assert '"dynamic_axes"' in pipe and "GradScaler" in pipe and "val_ratio" in pipe and "pth_trusted" in pipe
    # 音画同步：两条轨道必须共用会话时钟（禁止各自 self._start 计时导致声音领先画面）
    assert "def clock_t0" in srv and "self.session.clock_t0(self.kind)" in srv and not re.search(r"self\._start\b", srv)
    assert "av_offset_ms" in srv
    # 不说话时必须闭嘴：没有待机视频不能直接播训练原片（原片里本人在说话）
    assert "def build_silent_patches" in srv and "def silence_window" in srv and "def idle_frame" in srv
    assert "self.avatar.idle_frames[idx]" not in srv and "--idle_mouth %UL_IDLE_MOUTH%" in run_bat
    for flag in ("--host %UL_HOST%", "--batch %UL_BATCH%", "--out_height %UL_OUT_HEIGHT%", "--ort_gpu_mem_mb %UL_ORT_GPU_MB%",
                 "--av_offset_ms %UL_AV_OFFSET_MS%"):
        assert flag in run_bat, flag
    env = config_guide.parse_env_bat(ROOT_DIR / "env.bat")
    assert env.get("UL_MAX_SESSION") == "1" and env.get("UL_HOST") == "127.0.0.1"
    print("[PASS] test_engine_security_and_perf_contracts")


def test_obs_presets_25fps():
    """OBS 预设与 HuBERT 25fps 数字人一致，避免 30fps 每秒重复 5 帧的顿挫。"""
    inis = list((ROOT_DIR / "OBS配置").glob("*/basic.ini"))
    assert len(inis) >= 10
    for ini in inis:
        txt = ini.read_text(encoding="utf-8")
        assert "FPSType=1" in txt and "FPSInt=25" in txt, ini
    bk_txt = (ROOT_DIR / "launcher" / "backend.py").read_text(encoding="utf-8")
    assert 'cp.set("Video", "FPSInt", "25")' in bk_txt
    print(f"[PASS] test_obs_presets_25fps ({len(inis)} presets)")


def test_subtitle_wrap_and_preview_switch():
    """字幕：字号 14~80，强制换行、最多 N 行、分屏/缩小防溢出；预览：可开关，预览页共享 OBS 画面不抢说话会话。"""
    import overlay_presets as op

    c = op.sanitize_overlay_cfg({"font_size": 999, "sub_max_lines": 9, "sub_overflow": "bad", "sub_width_pct": 10})
    assert c["font_size"] == 80 and c["sub_max_lines"] == 5 and c["sub_overflow"] == "paginate" and c["sub_width_pct"] == 60
    ov = (HUB_DIR / "static" / "overlay.html").read_text(encoding="utf-8")
    assert "overflow-wrap: anywhere" in ov and "function layoutSubtitle" in ov and "function splitSubPages" in ov
    assert 'role: preview ? "preview" : "main"' in ov and "stopPreviewStream" in ov and "/api/preview" in ov
    idx = (HUB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="preview-switch"' in idx and "setPreviewEnabled" in idx and "bumpSubFont" in idx and 'id="ov-max-lines"' in idx
    srv = (HUB_DIR / "server.py").read_text(encoding="utf-8")
    assert '@app.post("/api/preview")' in srv and 'data.get("mirror")' in srv and "preview_sessions" in srv
    eng = (ROOT_DIR / "engine" / "ultralight_server.py").read_text(encoding="utf-8")
    assert "class MirrorSession" in eng and "_speech_session" in eng and "max_preview" in eng
    app_txt = (ROOT_DIR / "launcher" / "app.py").read_text(encoding="utf-8")
    assert "on_toggle_preview" in app_txt and "on_bump_sub_font" in app_txt and "sub_max_lines" in app_txt
    keys = {it["key"] for it in config_guide.GUIDE_ITEMS}
    assert {"ui.preview_enabled", "overlay.font_size", "overlay.sub_max_lines", "overlay.sub_overflow", "env.UL_MAX_PREVIEW"} <= keys
    print("[PASS] test_subtitle_wrap_and_preview_switch")


def test_safe_update_and_version():
    """一键更新：.bat 原样存取不被误判已修改；个人设置在 env.local.bat；更新脚本跳过生成文件并显示版本号。"""
    ga = (ROOT_DIR / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat -text" in ga and "eol=crlf" not in ga and "eol=lf" not in ga
    env_txt = (ROOT_DIR / "env.bat").read_text(encoding="ascii")
    assert 'if exist "%~dp0env.local.bat" call "%~dp0env.local.bat"' in env_txt
    assert env_txt.index("env.local.bat") < env_txt.index("Download mirrors")
    assert "env.local.bat" in (ROOT_DIR / ".gitignore").read_text(encoding="utf-8")
    bk_txt = (ROOT_DIR / "launcher" / "backend.py").read_text(encoding="utf-8")
    assert "ENV_BAT_PATH.with_suffix" not in bk_txt and "_cg.update_env_bat(ENV_BAT_PATH" in bk_txt
    su = (ROOT_DIR / "scripts" / "safe_update.py").read_text(encoding="utf-8")
    for key in ("migrate_env_customizations", '"stash", "push"', '"reset", "--hard"', '"stash", "pop"', "exe_running", "GENERATED_PREFIXES"):
        assert key in su, key
    bat = (ROOT_DIR / "06_update.bat").read_bytes()
    assert bat.isascii() and b"safe_update.py" in bat
    import app_version

    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}-r\d+", app_version.APP_VERSION)
    assert "APP_VERSION" in (ROOT_DIR / "launcher" / "app.py").read_text(encoding="utf-8")
    assert "app_version" in (HUB_DIR / "server.py").read_text(encoding="utf-8")
    assert 'id="app-ver"' in (HUB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    print(f"[PASS] test_safe_update_and_version (v{app_version.APP_VERSION})")


if __name__ == "__main__":
    test_topic_router_viewer_requests()
    test_config_guide_wizard_presets_doctor()
    test_engine_security_and_perf_contracts()
    test_obs_presets_25fps()
    test_subtitle_wrap_and_preview_switch()
    test_safe_update_and_version()
    test_u_port_and_obs_isolation()
    test_p01_bat_pure_ascii_crlf()
    test_p04_requirements_hygiene()
    test_p10_single_file_html()
    test_p14_adult_compliance()
    test_p18_p20_no_bak_no_key_in_examples()
    test_u01_u03_ultralight_pipeline_and_server_contracts()
    test_overlay_subtitles_and_free_fonts()
    print("ALL REGRESSION CHECKS PASSED")
