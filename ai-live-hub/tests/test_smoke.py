# -*- coding: utf-8 -*-
"""
ysaizb-ultralight 冒烟测试：
验证编译完整性、KB 引擎、纯规则健康探针、OBS 预设隔离、Ultralight UNet 前向与 ONNX 导出推理、
Hub FastAPI 接口以及 Launcher 后端配置。
"""
import asyncio
import json
import py_compile
import sys
import tempfile
from pathlib import Path

import httpx

HUB_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = HUB_DIR.parent
UDH_DIR = ROOT_DIR / "Ultralight-Digital-Human"

for p in (HUB_DIR, ROOT_DIR, UDH_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def test_compile_all_python():
    targets = [
        ROOT_DIR / "engine" / "audio_encoders.py",
        ROOT_DIR / "engine" / "ultralight_server.py",
        ROOT_DIR / "scripts" / "download_models.py",
        ROOT_DIR / "scripts" / "ultralight_pipeline.py",
        ROOT_DIR / "scripts" / "check_env.py",
        ROOT_DIR / "scripts" / "install.py",
        ROOT_DIR / "scripts" / "build_exe.py",
        ROOT_DIR / "launcher" / "backend.py",
        ROOT_DIR / "launcher" / "app.py",
        HUB_DIR / "server.py",
        HUB_DIR / "profile_manager.py",
        HUB_DIR / "health_monitor.py",
        HUB_DIR / "kb_engine.py",
        HUB_DIR / "tts_engine.py",
        HUB_DIR / "danmaku_relay.py",
        HUB_DIR / "tiktok_relay.py",
        HUB_DIR / "overlay_presets.py",
    ]
    for f in targets:
        assert f.is_file(), f"Missing file: {f}"
        py_compile.compile(str(f), doraise=True)
    print(f"[PASS] test_compile_all_python ({len(targets)} files)")


def test_kb_engine():
    import kb_engine

    cfg = json.loads((HUB_DIR / "config.json").read_text(encoding="utf-8"))
    kb = kb_engine.KnowledgeBaseEngine(HUB_DIR / "kb_data", cfg.get("knowledge_base", {}))
    docs = kb.list_docs()
    assert len(docs) > 0, "kb_data must contain at least one markdown doc"
    md_text = kb.doc_path(docs[0]).read_text(encoding="utf-8")
    sections, chunks, faqs = kb.parse_markdown(md_text)
    assert sections or chunks, "Parsed KB doc must contain sections or chunks"
    print(f"[PASS] test_kb_engine (docs={len(docs)}, chunks={len(chunks)}, faqs={len(faqs)})")


def test_profile_manager_isolation():
    import profile_manager

    assert profile_manager.SCENE_COLLECTION_NAME == "AI_Live_Hub_Ultralight"
    mgr = profile_manager.SinglePlatformManager(
        HUB_DIR / "platform_profiles.json",
        HUB_DIR / "runtime_state.json",
        HUB_DIR,
        ROOT_DIR / "logs",
    )
    profs = mgr.list_profiles()
    assert len(profs) >= 1, "At least one platform profile required"
    from launcher import backend as bk

    ok, msg = bk.generate_project_obs_presets()
    assert ok, f"generate_project_obs_presets failed: {msg}"
    scene_file = ROOT_DIR / "OBS配置" / "场景集合" / "泳昇Ultralight直播.json"
    assert scene_file.is_file(), f"Missing {scene_file}"
    txt = scene_file.read_text(encoding="utf-8")
    assert "http://127.0.0.1:28910/stage" in txt, "OBS scene collection must target :28910"
    assert '"monitoring_type": 2' in txt and '"bounds_type": 2' in txt
    print(f"[PASS] test_profile_manager_isolation (profiles={len(profs)})")


def test_health_monitor():
    import health_monitor
    import kb_engine
    import profile_manager

    cfg = json.loads((HUB_DIR / "config.json").read_text(encoding="utf-8"))
    kb = kb_engine.KnowledgeBaseEngine(HUB_DIR / "kb_data", cfg.get("knowledge_base", {}))
    mgr = profile_manager.SinglePlatformManager(
        HUB_DIR / "platform_profiles.json",
        HUB_DIR / "runtime_state.json",
        HUB_DIR,
        ROOT_DIR / "logs",
    )
    mon = health_monitor.SystemHealthMonitor(HUB_DIR)

    class _DummyState:
        config = cfg
        tts_mode = "edgetts"
        lt_sessionid = 0
        warmup_timer_active = True

        def stats(self):
            return {"llm_calls": 0, "tts_ok": 0}

    async def _run():
        async with httpx.AsyncClient() as client:
            return await mon.run_all_checks(client, _DummyState(), kb, mgr, {"speaking": False})

    res = asyncio.run(_run())
    assert res["overall"] in ("ok", "warn", "error"), f"Unexpected overall={res['overall']}"
    assert isinstance(res.get("modules"), list) and len(res["modules"]) >= 4
    print(f"[PASS] test_health_monitor (overall={res['overall']}, modules={len(res['modules'])})")


def test_unet_and_onnx_roundtrip():
    import numpy as np
    import onnxruntime as ort
    import torch
    from unet import Model

    with torch.no_grad():
        for asr, a_shape in (("hubert", (1, 16, 32, 32)), ("wenet", (1, 128, 16, 32))):
            net = Model(6, asr).eval()
            img = torch.zeros(1, 6, 160, 160)
            aud = torch.zeros(*a_shape)
            out = net(img, aud)
            assert tuple(out.shape) == (1, 3, 160, 160), f"Unexpected {asr} shape {out.shape}"

            if asr == "hubert":
                with tempfile.TemporaryDirectory() as td:
                    onnx_path = Path(td) / "unet.onnx"
                    torch.onnx.export(
                        net,
                        (img, aud),
                        str(onnx_path),
                        input_names=["image", "audio"],
                        output_names=["output"],
                        opset_version=11,
                        dynamo=False,
                    )
                    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
                    ort_out = sess.run(None, {"image": img.numpy().astype(np.float32), "audio": aud.numpy().astype(np.float32)})[0]
                    assert ort_out.shape == (1, 3, 160, 160)
    print("[PASS] test_unet_and_onnx_roundtrip (hubert + wenet + onnxruntime)")


def test_hub_fastapi_endpoints():
    from fastapi.testclient import TestClient
    import server

    with TestClient(server.app) as client:
        for path in ("/", "/stage", "/api/status", "/api/health", "/api/kb/status", "/api/avatar/list", "/api/platform/list", "/api/overlay/config"):
            r = client.get(path)
            assert r.status_code == 200, f"GET {path} failed with {r.status_code}"
        st = client.get("/api/status").json()
        assert "status" in st and "active_platform" in st["status"]
        assert "overlay" in st["status"]
        r_get_ov = client.get("/api/overlay/config").json()
        assert r_get_ov.get("code") == 0 and len(r_get_ov.get("fonts", [])) >= 5 and len(r_get_ov.get("effects", [])) >= 6
        orig_ov = r_get_ov["overlay"]
        try:
            r_post = client.post(
                "/api/overlay/config",
                json={
                    "show_kb_board": False,
                    "board_position": "top-left",
                    "show_subtitle": True,
                    "font_preset": "smiley_sans",
                    "effect_preset": "gold_story",
                },
            )
            assert r_post.status_code == 200
            upd = r_post.json()["overlay"]
            assert upd["show_kb_board"] is False and upd["board_position"] == "top-left" and upd["font_preset"] == "smiley_sans"
            r_font = client.get("/static/fonts/SmileySans-Oblique.ttf")
            assert r_font.status_code == 200 and len(r_font.content) > 100_000
        finally:
            client.post("/api/overlay/config", json=orig_ov)
    print("[PASS] test_hub_fastapi_endpoints")


def test_launcher_backend_config():
    from launcher import backend as bk

    env = bk.parse_env_bat()
    assert env.get("CONDA_ENV") == "ultralight", f"Expected ultralight, got {env.get('CONDA_ENV')}"
    assert env.get("UL_PORT") == "8020", f"Expected 8020, got {env.get('UL_PORT')}"
    assert env.get("HUB_PORT") == "28910", f"Expected 28910, got {env.get('HUB_PORT')}"
    av_dir = bk.get_avatars_dir()
    assert av_dir.resolve() == (ROOT_DIR / "avatars").resolve()
    assets = bk.list_ultralight_assets()
    assert isinstance(assets, list)
    probe_bad = bk.probe_video_info(str(ROOT_DIR / "nonexistent_video.mp4"))
    assert probe_bad.get("ok") is False
    kb_txt = bk.load_ultralight_config_kb()
    assert "RTX 2080" in kb_txt and "batch_size" in kb_txt
    p_fast = bk.rule_recommend_ultralight_config("我想极速测试看看效果", {"ok": True, "has_audio": True, "frames": 100, "duration_sec": 4.0}, has_ckpt=False)
    assert p_fast["epochs"] <= 20 and p_fast["batch_size"] <= 10 and p_fast["can_train"] is True
    p_silent = bk.rule_recommend_ultralight_config("正式开播", {"ok": True, "has_audio": False, "frames": 250, "duration_sec": 10.0}, has_ckpt=False)
    assert p_silent["can_train"] is False and p_silent["warnings"]
    p_ckpt = bk.rule_recommend_ultralight_config("已有外部权重封装", {"ok": True, "has_audio": False, "frames": 250, "duration_sec": 10.0}, has_ckpt=True)
    assert p_ckpt["mode"] == "封装外部权重 (.pth/.onnx)" and p_ckpt["epochs"] == 0 and p_ckpt["can_train"] is True
    ov_clean = bk.sanitize_overlay_cfg({"font_size": 999, "board_position": "bad", "text_color": "invalid", "font_preset": "lxgw_wenkai"})
    assert ov_clean["font_size"] == 48 and ov_clean["board_position"] == "top-right" and ov_clean["text_color"] == "#FFFFFF" and ov_clean["font_preset"] == "lxgw_wenkai"
    print(f"[PASS] test_launcher_backend_config (assets={len(assets)}, ai_kb_rules=ok)")


if __name__ == "__main__":
    test_compile_all_python()
    test_kb_engine()
    test_profile_manager_isolation()
    test_health_monitor()
    test_unet_and_onnx_roundtrip()
    test_hub_fastapi_endpoints()
    test_launcher_backend_config()
    print("ALL SMOKE TESTS PASSED")
