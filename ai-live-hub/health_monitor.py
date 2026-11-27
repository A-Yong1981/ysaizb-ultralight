# -*- coding: utf-8 -*-
"""
health_monitor.py —— 系统健康诊断中心（Ultralight-Digital-Human 独立版，纯规则确定性探针）
"""
import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MINOR_PHRASES = (
    "小朋友们好", "小朋友记住", "小朋友请注意", "小朋友们记住", "小朋友，", "小朋友可不能",
    "小朋友们，", "小朋友们注意", "同学们好",
)
DEFAULT_CFG = {
    "interval_sec": 5,
    "vram_stable_gb": 4.5,
    "vram_warn_gb": 6.0,
    "vram_error_gb": 7.2,
    "temp_warn_c": 78,
    "temp_error_c": 85,
    "queue_warn": 20,
    "disk_warn_gb": 2.0,
    "disk_error_gb": 0.5,
}


def _item(mid: str, name: str, level: str, metric: str, message: str, fix: str = "") -> Dict[str, Any]:
    return {"id": mid, "name": name, "level": level, "metric": metric, "message": message, "fix": fix}


class SystemHealthMonitor:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._doc_cache: Tuple[str, float, str] = ("", 0.0, "")
        self._cache_write: Tuple[float, bool] = (0.0, True)
        self.last_report: Dict[str, Any] = {
            "overall": "warn",
            "summary": "系统诊断中...",
            "checked_at": "--:--:--",
            "gpu": {"used_gb": 0.0, "total_gb": 8.0, "temp_c": 0, "util_pct": 0, "name": ""},
            "modules": [],
        }

    @staticmethod
    def _nvidia_smi() -> Optional[Dict[str, Any]]:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu,utilization.gpu,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = r.stdout.decode("utf-8", errors="ignore").strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]

        def num(x: str) -> float:
            try:
                return float(x)
            except ValueError:
                return 0.0
        return {"used_gb": round(num(parts[0]) / 1024.0, 2), "total_gb": round(num(parts[1]) / 1024.0, 1),
                "temp_c": int(num(parts[2])), "util_pct": int(num(parts[3])),
                "name": parts[4] if len(parts) > 4 else ""}

    async def _probe_gpu(self, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        name = "显卡显存与温度"
        try:
            g = await asyncio.to_thread(self._nvidia_smi)
        except FileNotFoundError:
            return _item("gpu_vram", name, "warn", "未检测到", "找不到 nvidia-smi，读不到显卡状态",
                         "确认 NVIDIA 驱动已安装；不影响直播"), self.last_report["gpu"]
        except Exception as exc:
            return _item("gpu_vram", name, "warn", "读取失败", f"读取显卡状态失败：{exc}",
                         "稍后会自动重试"), self.last_report["gpu"]
        used, total, temp, util = g["used_gb"], g["total_gb"], g["temp_c"], g["util_pct"]
        metric = f"{used}/{total}G · {temp}°C"
        if used >= cfg["vram_error_gb"] or temp >= cfg["temp_error_c"]:
            return _item("gpu_vram", name, "error", metric,
                         f"显存 {used}GB、温度 {temp}°C，接近红线",
                         "先暂停讲解；把 env.bat 的 UL_ASR 改成 wenet（ONNX 模式仅占 <1GB 显存）后重启"), g
        if used >= cfg["vram_warn_gb"] or temp >= cfg["temp_warn_c"]:
            return _item("gpu_vram", name, "warn", metric,
                         f"显存 {used}GB、温度 {temp}°C，负载偏高",
                         "别同时开训练脚本或大型游戏；检查散热"), g
        note = f"，高于 {cfg['vram_stable_gb']}GB 稳定线，留意" if used > cfg["vram_stable_gb"] else "，余量充足"
        return _item("gpu_vram", name, "ok", metric, f"显存 {used}/{total}GB · 温度 {temp}°C · 占用 {util}%{note}"), g

    async def _probe_engine(self, client: Any, state: Any, base: str) -> Dict[str, Any]:
        name = "Ultralight 数字人"
        try:
            await client.get(base.rstrip("/") + "/", timeout=2.0)
            online = True
        except Exception:
            online = False
        state.lt_online = online
        if not online:
            return _item("livetalking", name, "error", "引擎未响应", "Ultralight 推理引擎没启动或已退出",
                         "双击 start_all.bat 重新启动；报错看 Ultralight 窗口或 logs\\ultralight_engine.log")
        avatar = state.config.get("livetalking", {}).get("avatar_id", "-")
        if state.switching_avatar:
            return _item("livetalking", name, "warn", "换装中", f"正在换成 [{avatar}]，等待画面重连",
                         "超过 10 秒还没好，点右上角「强制解锁」")
        if not state.session_id:
            return _item("livetalking", name, "warn", "画面未连接", "Ultralight 引擎已就绪，等待 OBS 或预览连接",
                         "打开 OBS 浏览器源 /stage?layer=all，或在控制台点「在本页预览」")
        host_name = state.config.get("persona", {}).get("name", "主播") or "主播"
        return _item("livetalking", name, "ok", "画面已连接",
                     f"Ultralight 渲染正常，形象 [{avatar}]，{host_name}{'正在说话' if state.is_speaking else '等待中'}")

    def _probe_scheduler(self, state: Any, ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
        name = "中控调度与磁盘"
        q = len(state.priority_queue) + len(state.normal_queue)
        try:
            free_gb = round(shutil.disk_usage(self.base_dir).free / 1024 ** 3, 2)
        except Exception:
            free_gb = 99.0
        if not ctx.get("scheduler_alive", True):
            return _item("scheduler", name, "error", "调度已停止", "后台调度停了，弹幕和讲解都不会再播",
                         "重新双击 start_all.bat")
        if free_gb < cfg["disk_error_gb"]:
            return _item("scheduler", name, "error", f"磁盘剩 {free_gb}G", "磁盘空间不足",
                         "清理磁盘空间或 tts_cache 缓存")
        if state.switching_avatar:
            return _item("scheduler", name, "warn", "换装暂停", "正在换装重连，弹幕会先排队", "")
        if q >= cfg["queue_warn"] or free_gb < cfg["disk_warn_gb"]:
            return _item("scheduler", name, "warn", f"排队 {q} · 剩 {free_gb}G",
                         f"排队弹幕 {q} 条、磁盘剩 {free_gb}GB", "可点「清空排队弹幕」")
        return _item("scheduler", name, "ok", f"排队 {q} · 剩 {free_gb}G", f"调度正常，排队 {q} 条，磁盘剩 {free_gb}GB")

    def _cache_writable(self, cache_dir: Path) -> bool:
        ts, ok = self._cache_write
        if time.time() - ts < 60:
            return ok
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            p = cache_dir / ".write_probe"
            p.write_bytes(b"1")
            p.unlink()
            ok = True
        except Exception:
            ok = False
        self._cache_write = (time.time(), ok)
        return ok

    def _probe_llm_tts(self, state: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
        name = "大模型与声音"
        ready = int(ctx.get("llm_ready", 0))
        mode = ctx.get("tts_mode", "edgetts")
        label = ctx.get("tts_label", mode)
        last = state.last_provider_name or "待命"
        metric = f"{ready} 个大模型 · {label}"
        if not self._cache_writable(Path(ctx["tts_cache_dir"])):
            return _item("llm_tts", name, "error", "缓存写不进", "语音缓存目录不可写", "检查目录权限")
        if "全部限流" in last:
            return _item("llm_tts", name, "error", "大模型全失败", "大模型调用失败，当前使用本地兜底",
                         "检查 config.json Key 余额或网络")
        if ready == 0:
            return _item("llm_tts", name, "warn", metric, "未填大模型 Key，只用讲稿精准问答和兜底",
                         "在 config.json 填写可用 API Key 后点热重载")
        if mode == "siliconflow" and not ctx.get("tts_key_ok", False):
            return _item("llm_tts", name, "warn", metric, "选了硅基克隆声音但未填 Key，自动降级 Edge-TTS",
                         "在 config.json 填 tts.siliconflow.api_key")
        if ctx.get("last_tts_tag") == "edgetts(fallback)":
            return _item("llm_tts", name, "warn", metric, "克隆声音异常已自动降级为免费声音", "检查硅基额度或网络")
        return _item("llm_tts", name, "ok", metric, f"{ready} 个大模型可用（最近：{last}），声音：{label}")

    def _doc_text(self, path: Path) -> str:
        try:
            mt = path.stat().st_mtime
        except OSError:
            return ""
        key, cmt, txt = self._doc_cache
        if key == str(path) and cmt == mt:
            return txt
        txt = path.read_text(encoding="utf-8", errors="ignore")
        self._doc_cache = (str(path), mt, txt)
        return txt

    def _probe_kb(self, kb: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
        name = "讲稿与成人合规"
        phrases = list(ctx.get("minor_phrases") or DEFAULT_MINOR_PHRASES)
        bound = str(ctx.get("bound_doc") or "")
        texts = dict(ctx.get("compliance_texts") or {})
        doc_path = kb.kb_dir / bound if bound else None
        if doc_path and doc_path.exists():
            texts[f"讲稿 {bound}"] = self._doc_text(doc_path)
        for where, txt in texts.items():
            for bad in phrases:
                if bad and bad in (txt or ""):
                    return _item("kb", name, "error", "合规告警", f"{where} 含直接对未成年人喊话「{bad}」",
                                 "改成面向家长/成年观众表达后重载")
        if not ctx.get("kb_enabled", True):
            return _item("kb", name, "ok", "只答疑", "本平台仅答疑；成人合规通过")
        if not bound or not doc_path or not doc_path.exists():
            return _item("kb", name, "error", "讲稿缺失", f"绑定讲稿 [{bound or '未选'}] 找不到", "在右侧重新选一篇讲稿")
        if getattr(kb, "building", False):
            return _item("kb", name, "warn", "准备中", "正在准备讲稿索引", "")
        if kb.active_doc != bound or not kb.chunks:
            return _item("kb", name, "error", "讲稿没加载", f"讲稿 [{bound}] 未加载成功", "点「重建索引」")
        metric = f"第 {min(kb.cursor + 1, len(kb.chunks))}/{len(kb.chunks)} 段"
        if not kb.faqs:
            return _item("kb", name, "warn", metric, f"讲稿 [{bound}] 缺少 Q:/A: 精准问答", "建议补充 Q:/A: 问答")
        if not kb.vectorized:
            return _item("kb", name, "warn", metric + " · 关键词检索", f"讲稿 [{bound}] 仅用词法检索", "配好 Key 后点重建索引")
        return _item("kb", name, "ok", metric, f"讲稿 [{bound}] 就绪，语义检索 + {len(kb.faqs)} 条精准问答，合规通过")

    async def _probe_obs_collector(self, prof_mgr: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
        name = "OBS 与弹幕接收"
        ws = prof_mgr.data.get("obs_ws", {})
        obs_on = bool(ws.get("enabled"))
        obs_ok = False
        if obs_on:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(ws.get("host", "127.0.0.1"), int(ws.get("port", 4455))), 1.0)
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass
                obs_ok = True
            except Exception:
                obs_ok = False
        active = prof_mgr.get_active()
        label = ctx.get("platform_label") or active.get("label", prof_mgr.active_key)
        has_cmd = bool(active.get("collector_cmd"))
        running = prof_mgr.collector_running()
        if has_cmd and getattr(prof_mgr, "collector_gave_up", False):
            return _item("obs", name, "error", "弹幕已中断", f"[{label}] 弹幕接收连续崩溃已停止",
                         f"查看 logs\\collector_{prof_mgr.active_key}.log")
        issues, fixes = [], []
        if obs_on and not obs_ok:
            issues.append("OBS 未开或未启用 WebSocket(4455)")
            fixes.append("OBS 开启 WebSocket 服务(4455)")
        if has_cmd and not running:
            if getattr(prof_mgr, "want_collector", False):
                issues.append("弹幕接收重启中")
            elif getattr(prof_mgr, "stopped_by_user", False):
                issues.append("已手动停止接收弹幕")
            elif not prof_mgr.active_room:
                issues.append("未填房间号，尚未接收弹幕")
            else:
                issues.append("弹幕采集未运行")
        if issues:
            return _item("obs", name, "warn", f"{label} · 待命", "；".join(issues), "；".join(fixes))
        return _item("obs", name, "ok", f"{label} · 就绪", ("OBS 已连通，" if obs_on else "") + f"[{label}] 就绪")

    async def run_all_checks(self, client: Any, state: Any, kb: Any, prof_mgr: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
        cfg = dict(DEFAULT_CFG)
        cfg.update({k: v for k, v in (ctx.get("health_cfg") or {}).items() if k in DEFAULT_CFG})

        async def safe_async(coro, mid, name):
            try:
                return await coro
            except Exception as exc:
                return _item(mid, name, "warn", "探针异常", f"检查出错：{exc}", "")

        def safe(fn, mid, name, *a):
            try:
                return fn(*a)
            except Exception as exc:
                return _item(mid, name, "warn", "探针异常", f"检查出错：{exc}", "")

        gpu_pair, lt, obs = await asyncio.gather(
            safe_async(self._probe_gpu(cfg), "gpu_vram", "显卡显存与温度"),
            safe_async(self._probe_engine(client, state, ctx.get("lt_base", "http://127.0.0.1:8020")), "livetalking", "Ultralight 数字人"),
            safe_async(self._probe_obs_collector(prof_mgr, ctx), "obs", "OBS 与弹幕接收"),
        )
        gpu_item, gpu_meta = gpu_pair if isinstance(gpu_pair, tuple) else (gpu_pair, self.last_report["gpu"])
        modules: List[Dict[str, Any]] = [
            gpu_item,
            lt,
            safe(self._probe_scheduler, "scheduler", "中控调度与磁盘", state, ctx, cfg),
            safe(self._probe_llm_tts, "llm_tts", "大模型与声音", state, ctx),
            safe(self._probe_kb, "kb", "讲稿与成人合规", kb, ctx),
            obs,
        ]
        errs = [m for m in modules if m["level"] == "error"]
        warns = [m for m in modules if m["level"] == "warn"]
        if errs:
            overall = "error"
            summary = f"{len(errs)} 项故障：{errs[0]['name']}" + (" 等" if len(errs) > 1 else "")
        elif warns:
            overall = "warn"
            summary = f"{len(warns)} 项提醒：{warns[0]['name']}" + (" 等" if len(warns) > 1 else "")
        else:
            overall = "ok"
            summary = f"全部正常（{len(modules)}/{len(modules)}）"
        self.last_report = {"overall": overall, "summary": summary, "checked_at": time.strftime("%H:%M:%S"),
                            "gpu": gpu_meta, "modules": modules}
        return self.last_report
