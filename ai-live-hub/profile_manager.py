# -*- coding: utf-8 -*-
"""
profile_manager.py —— 单平台一键切播管理器（Ultralight 独立版，场景集 AI_Live_Hub_Ultralight）
"""
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCENE_COLLECTION_NAME = "AI_Live_Hub_Ultralight"

OBS_SCENE_MAP: Dict[str, tuple] = {
    "抖音横屏": ("Douyin_1296x1080", "landscape", "douyin"),
    "抖音竖屏": ("Douyin_720x1280", "portrait", "douyin"),
    "B站横屏": ("Bilibili_1920x1080", "landscape", "bilibili"),
    "B站竖屏": ("Bilibili_720x1280", "portrait", "bilibili"),
    "视频号竖屏": ("WeChat_720x1280", "portrait", "wechat"),
    "视频号横屏": ("WeChat_1280x720", "landscape", "wechat"),
    "YouTube横屏": ("YouTube_1280x720", "landscape", "youtube"),
    "YouTube竖屏": ("YouTube_720x1280", "portrait", "youtube"),
    "TikTok竖屏": ("TikTok_720x1280", "portrait", "tiktok"),
    "TikTok横屏": ("TikTok_1280x720", "landscape", "tiktok"),
}

OBS_PROFILE_DIMS: Dict[str, tuple] = {
    "Douyin_1296x1080": (1296, 1080),
    "Douyin_720x1280": (720, 1280),
    "Bilibili_1920x1080": (1920, 1080),
    "Bilibili_720x1280": (720, 1280),
    "WeChat_720x1280": (720, 1280),
    "WeChat_1280x720": (1280, 720),
    "YouTube_1280x720": (1280, 720),
    "YouTube_720x1280": (720, 1280),
    "TikTok_720x1280": (720, 1280),
    "TikTok_1280x720": (1280, 720),
}


class SinglePlatformManager:
    def __init__(self, profiles_path: Path, state_path: Path, base_dir: Path, log_dir: Path) -> None:
        self.profiles_path = profiles_path
        self.state_path = state_path
        self.base_dir = base_dir
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, Any] = self._load()
        st = self._load_state()
        self.active_key: str = self.data.get("active_profile") or st.get("active_profile", "douyin")
        if self.active_key not in self.data.get("profiles", {}):
            self.active_key = next(iter(self.data.get("profiles", {}) or {"douyin": {}}))
        self.active_room: str = st.get("room", "")
        self.collector_proc: Optional[subprocess.Popen] = None
        self._collector_cmd: List[str] = []
        self._collector_env: Dict[str, str] = {}
        self._collector_log = None
        self._restart_times: List[float] = []
        self.want_collector: bool = False
        self.collector_gave_up: bool = False
        self.stopped_by_user: bool = False
        self.last_obs_msg: str = ""
        self.last_collector_msg: str = ""

    def _load(self) -> Dict[str, Any]:
        if not self.profiles_path.exists():
            return {"active_profile": "douyin", "obs_ws": {"enabled": False}, "profiles": {}}
        with self.profiles_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self) -> None:
        data = {"active_profile": self.active_key, "room": self.active_room,
                "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def reload(self) -> None:
        self.data = self._load()
        cfg_active = str(self.data.get("active_profile") or "").strip()
        if cfg_active and cfg_active in self.data.get("profiles", {}):
            self.active_key = cfg_active
            self._save_state()
        elif self.active_key not in self.data.get("profiles", {}):
            self.active_key = next(iter(self.data.get("profiles", {}) or {"douyin": {}}))

    def list_profiles(self) -> List[Dict[str, Any]]:
        out = []
        for k, v in self.data.get("profiles", {}).items():
            out.append({
                "key": k,
                "label": v.get("label", k),
                "orientation": v.get("orientation", "landscape"),
                "canvas": v.get("canvas", ""),
                "room_hint": v.get("room_hint", "房间号（可选）"),
                "has_collector": bool(v.get("collector_cmd")),
                "kb_doc": v.get("kb_doc", ""),
                "kb_enabled": bool(v.get("kb_enabled", True)),
                "kb_style": v.get("kb_style", k),
                "kb_language": v.get("kb_language", v.get("lang", "zh")),
            })
        return out

    def get_active(self) -> Dict[str, Any]:
        return self.data.get("profiles", {}).get(self.active_key, {})

    def bind_kb_doc(self, key: str, doc: str, enable: bool = True) -> bool:
        profiles = self.data.get("profiles", {})
        if key not in profiles:
            return False
        profiles[key]["kb_doc"] = doc
        if enable:
            profiles[key]["kb_enabled"] = True
        tmp = self.profiles_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.profiles_path)
        return True

    def set_active(self, key: str, room: str) -> bool:
        if key not in self.data.get("profiles", {}):
            return False
        self.active_key = key
        self.active_room = room.strip()
        self._save_state()
        return True

    def collector_running(self) -> bool:
        return self.collector_proc is not None and self.collector_proc.poll() is None

    def stop_collector(self) -> None:
        self.want_collector = False
        proc = self.collector_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self.collector_proc = None
        if self._collector_log:
            try:
                self._collector_log.close()
            except Exception:
                pass
            self._collector_log = None

    def _spawn(self) -> str:
        log_path = self.log_dir / f"collector_{self.active_key}.log"
        self._collector_log = open(log_path, "a", encoding="utf-8", errors="replace")
        self._collector_log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动: {' '.join(self._collector_cmd)}\n")
        self._collector_log.flush()
        self.collector_proc = subprocess.Popen(
            self._collector_cmd,
            cwd=str(self.base_dir),
            env=self._collector_env,
            stdout=self._collector_log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return f"已启动弹幕采集进程 PID={self.collector_proc.pid}（日志 logs\\{log_path.name}）"

    def start_collector(self, profile: Dict[str, Any], room_id: str, hub_url: str) -> str:
        self.stop_collector()
        cmd_tpl = profile.get("collector_cmd") or []
        if not cmd_tpl:
            return "该平台未配置内置采集器（请外部运行弹幕采集工具，推送到 /api/danmaku）"
        joined = " ".join(cmd_tpl)
        if "{room}" in joined and not room_id.strip():
            return "未填房间号，跳过拉起弹幕采集器"
        proxy = str(profile.get("proxy", "")).strip()
        if "{proxy}" in joined and not proxy:
            cleaned = []
            skip = False
            for i, part in enumerate(cmd_tpl):
                if skip:
                    skip = False
                    continue
                if i + 1 < len(cmd_tpl) and cmd_tpl[i + 1] == "{proxy}":
                    skip = True
                    continue
                cleaned.append(part)
            cmd_tpl = cleaned
        rep = {"{python}": sys.executable, "{room}": room_id.strip(), "{hub}": hub_url, "{proxy}": proxy}
        cmd = []
        for part in cmd_tpl:
            p = part
            for k, v in rep.items():
                p = p.replace(k, v)
            cmd.append(p)
        if cmd and cmd[0].lower() in ("python", "python.exe"):
            cmd[0] = sys.executable
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(k, None)
        if proxy:
            env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy})
        self._collector_cmd, self._collector_env = cmd, env
        self._restart_times = []
        try:
            msg = self._spawn()
            self.want_collector = True
            self.collector_gave_up = False
            self.stopped_by_user = False
            return msg
        except Exception as exc:
            return f"启动采集进程失败: {exc}"

    async def watchdog(self, on_event) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                if not self.want_collector or self.collector_running():
                    continue
                code = self.collector_proc.poll() if self.collector_proc else None
                now = time.time()
                self._restart_times = [t for t in self._restart_times if now - t < 600]
                if len(self._restart_times) >= 5:
                    self.want_collector = False
                    self.collector_gave_up = True
                    await on_event("error", f"弹幕采集器 10 分钟内崩溃 5 次已停止，请查看 logs\\collector_{self.active_key}.log")
                    continue
                self._restart_times.append(now)
                if self._collector_log:
                    try:
                        self._collector_log.close()
                    except Exception:
                        pass
                msg = self._spawn()
                await on_event("warn", f"弹幕采集器退出(code={code})，已自动重启：{msg}")
            except Exception as exc:
                await on_event("error", f"采集守护异常: {exc}")

    def sync_obs_blocking(self, obs_profile: str, obs_scene: str) -> str:
        ws_cfg = self.data.get("obs_ws", {})
        if not ws_cfg.get("enabled"):
            return "未启用 OBS 自动联动"
        if not obs_profile and not obs_scene:
            return "该平台未配置 OBS Profile / Scene"
        try:
            import obsws_python as obs
        except ImportError:
            return "未安装 obsws-python，跳过 OBS 自动切换"
        host = ws_cfg.get("host", "127.0.0.1")
        port = int(ws_cfg.get("port", 4455))
        try:
            socket.create_connection((host, port), timeout=1.0).close()
        except OSError:
            return "OBS 没打开或没开启 WebSocket（OBS 菜单：工具→WebSocket 服务器设置），本次跳过自动切换场景，不影响直播"
        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
        try:
            cl = obs.ReqClient(host=host, port=port, password=ws_cfg.get("password", ""), timeout=3)
        except Exception as exc:
            return f"OBS WebSocket 未连接: {exc}"
        notes = []
        try:
            streaming = False
            try:
                streaming = bool(cl.get_stream_status().output_active)
            except Exception:
                pass
            target_scene = obs_scene
            try:
                scl = cl.get_scene_collection_list()
                cols = list(getattr(scl, "scene_collections", []) or [])
                cur_col = getattr(scl, "current_scene_collection_name", "") or ""
                if SCENE_COLLECTION_NAME in cols and cur_col != SCENE_COLLECTION_NAME and not streaming:
                    cl.set_current_scene_collection(SCENE_COLLECTION_NAME)
                    notes.append(f"场景集→{SCENE_COLLECTION_NAME}")
                    time.sleep(0.15)
            except Exception:
                pass
            if obs_profile:
                plist = cl.get_profile_list()
                if obs_profile not in list(getattr(plist, "profiles", []) or []):
                    notes.append(f"⚠ OBS 中不存在配置文件「{obs_profile}」")
                elif plist.current_profile_name != obs_profile:
                    if streaming:
                        notes.append(f"⚠ 正在推流，未切换配置文件「{obs_profile}」")
                    else:
                        cl.set_current_profile(obs_profile)
                        notes.append(f"配置文件→{obs_profile}")
                        time.sleep(0.05)
            sl = cl.get_scene_list()
            scenes = [s.get("sceneName") for s in (getattr(sl, "scenes", []) or [])]
            if obs_scene:
                if obs_scene not in scenes:
                    notes.append(f"⚠ OBS 中不存在场景「{obs_scene}」")
                elif getattr(sl, "current_program_scene_name", "") != obs_scene:
                    cl.set_current_program_scene(obs_scene)
                    notes.append(f"场景→{obs_scene}")
            else:
                target_scene = getattr(sl, "current_program_scene_name", "") or ""
            resolved_profile = obs_profile or OBS_SCENE_MAP.get(target_scene, ("", "", ""))[0]
            wh = OBS_PROFILE_DIMS.get(resolved_profile)
            for inp in (getattr(cl.get_input_list(), "inputs", []) or []):
                if "browser" in str(inp.get("inputKind", "")).lower():
                    iname = inp.get("inputName", "")
                    if not iname:
                        continue
                    try:
                        cl.set_input_audio_monitor_type(iname, "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT")
                    except Exception:
                        pass
                    try:
                        if getattr(cl.get_input_mute(iname), "input_muted", False):
                            cl.set_input_mute(iname, False)
                    except Exception:
                        pass
                    try:
                        if float(getattr(cl.get_input_volume(iname), "input_volume_mul", 1.0)) < 0.5:
                            cl.set_input_volume(iname, vol_mul=1.0)
                    except Exception:
                        pass
                    if wh and iname == "数字人舞台":
                        try:
                            cl.set_input_settings(
                                iname,
                                {"width": int(wh[0]), "height": int(wh[1]), "reroute_audio": True, "shutdown": False},
                                True,
                            )
                        except Exception:
                            pass
                        if target_scene:
                            try:
                                sid = int(cl.get_scene_item_id(target_scene, iname).scene_item_id)
                                cl.set_scene_item_transform(target_scene, sid, {
                                    "positionX": 0.0,
                                    "positionY": 0.0,
                                    "scaleX": 1.0,
                                    "scaleY": 1.0,
                                    "boundsType": "OBS_BOUNDS_STRETCH",
                                    "boundsAlignment": 0,
                                    "boundsWidth": float(wh[0]),
                                    "boundsHeight": float(wh[1]),
                                })
                            except Exception:
                                pass
        except Exception as exc:
            notes.append(f"OBS 联动出错: {exc}")
        finally:
            try:
                cl.disconnect()
            except Exception:
                pass
        return "OBS: " + ("，".join(notes) if notes else "保持当前")

    def poll_obs_scene_blocking(self) -> Dict[str, Any]:
        ws_cfg = self.data.get("obs_ws", {})
        if not ws_cfg.get("enabled"):
            return {"connected": False}
        host = ws_cfg.get("host", "127.0.0.1")
        port = int(ws_cfg.get("port", 4455))
        try:
            socket.create_connection((host, port), timeout=0.35).close()
        except OSError:
            return {"connected": False}
        try:
            import obsws_python as obs
        except ImportError:
            return {"connected": False}
        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
        cl = None
        try:
            cl = obs.ReqClient(host=host, port=port, password=ws_cfg.get("password", ""), timeout=1.5)
            streaming = False
            try:
                streaming = bool(cl.get_stream_status().output_active)
            except Exception:
                pass
            scene = getattr(cl.get_scene_list(), "current_program_scene_name", "") or ""
            profile = getattr(cl.get_profile_list(), "current_profile_name", "") or ""
            exp_prof, exp_ori, exp_plat = OBS_SCENE_MAP.get(scene, ("", "", ""))
            need_fix = bool(exp_prof and not streaming and profile != exp_prof)
            wh = OBS_PROFILE_DIMS.get(exp_prof)
            for inp in (getattr(cl.get_input_list(), "inputs", []) or []):
                if "browser" not in str(inp.get("inputKind", "")).lower():
                    continue
                iname = inp.get("inputName", "")
                if not iname:
                    continue
                try:
                    if cl.get_input_audio_monitor_type(iname).monitor_type != "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT":
                        need_fix = True
                except Exception:
                    pass
                try:
                    if getattr(cl.get_input_mute(iname), "input_muted", False):
                        need_fix = True
                except Exception:
                    pass
                if wh and iname == "数字人舞台":
                    try:
                        cfg_s = getattr(cl.get_input_settings(iname), "input_settings", {}) or {}
                        if (int(cfg_s.get("width", 0) or 0), int(cfg_s.get("height", 0) or 0)) != wh:
                            need_fix = True
                        if not bool(cfg_s.get("reroute_audio", False)):
                            need_fix = True
                    except Exception:
                        pass
            return {
                "connected": True,
                "streaming": streaming,
                "scene": scene,
                "profile": profile,
                "expected_profile": exp_prof,
                "orientation": exp_ori,
                "platform": exp_plat,
                "need_fix": need_fix,
            }
        except Exception:
            return {"connected": False}
        finally:
            if cl is not None:
                try:
                    cl.disconnect()
                except Exception:
                    pass
