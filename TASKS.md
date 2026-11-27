# TASKS.md — ysaizb-ultralight 任务看板

## 已完成里程碑
- [x] **M1 独立工作区与 Conda 环境**：建立 `d:\Antigravity\ysaizb-ultralight`，克隆上游 `anliyuan/Ultralight-Digital-Human`，搭建独立 conda `ultralight`（`torch 2.9.1+cu128`, `onnxruntime-gpu 1.32.0`, `kaldi-native-fbank`, `onnxscript`）。
- [x] **M2 模型下载与训练导出流水线**：实现 `scripts/download_models.py` 与 `scripts/ultralight_pipeline.py`（预处理 + 训练 + ONNX 导出校验 + 外部 `.pth`/`.onnx` 封装）。
- [x] **M3 原生 Ultralight WebRTC 引擎**：实现 `engine/audio_encoders.py`（WeNet 20fps ONNX & HuBERT 25fps FP16）与 `engine/ultralight_server.py`（`:8020`）。
- [x] **M4 Hub 中控与桌面启动器**：隔离端口 `:28910` 与 OBS `AI_Live_Hub_Ultralight`，集成 WebUI 与 Tkinter 启动器的 Ultralight 参数配置及训练/封装入口。
- [x] **M5 安装脚本与自检回归体系**：完善 `00_gpu_info.bat` ~ `05_test_keys.bat`、`build_exe.bat`、`start_all.bat`、`test_smoke.py`、`test_regressions.py`。
- [x] **M6 独立桌面应用 EXE 与全控制单元集成**：在 `launcher/app.py` 集成 7 大模块（含实时策略/讲解/绿幕抠像控制、系统诊断工具箱、录音克隆音色与专用「🎓 主播训练与管理」工作台），并通过 PyInstaller 打包生成根目录 `泳昇Ultralight直播平台.exe`。

## 已决设计决策
1. **双路径音频特征**：保留 `hubert` (25fps) 作为高质量默认项，同时完整支持 `wenet` (20fps ONNX) 与 `onnx`/`torch` 双推理后端。
2. **端口与环境完全隔离**：Engine `:8020` / Hub `:28910` / Conda `ultralight` / OBS `AI_Live_Hub_Ultralight`，确保与原 `ysaizb`（`:8010` / `:28900` / `livetalking` / `AI_Live_Hub`）零冲突。
3. **无音轨防呆校验**：`ultralight_pipeline.py` 强制检测输入视频是否包含音轨，防止误拿无声闭嘴待机片训练出不会张嘴的模型。
