# AGENTS.md — ysaizb-ultralight 独立方案最高开发约束

本项目是运行于 Windows 宿主机（Intel i5-12400F / 32GB RAM / NVIDIA RTX 2080 8GB）的 **Ultralight-Digital-Human** 独立数字人直播方案，与 `d:\Antigravity\ysaizb`（Wav2Lip + LiveTalking）完全物理隔离。

## 1. 硬件与性能红线（严禁突破）
- **显卡约束**：RTX 2080 8GB 为 Turing 架构（sm_75），仅支持 FP16，不支持 BF16；严禁安装 CUDA 13 (`cu130`) 轮子，使用 `torch 2.9.1 + cu128`。
- **显存红线**：整机稳定显存占用压在 **4.5GB 以内**，绝不突破 **7.2GB**；HuBERT 模型在 RTX 2080 上强制 `.half()` FP16 推理，ONNX Runtime 使用 CUDA/CPU Provider。
- **零显存原则**：LLM、向量检索、Edge/硅基流动 TTS 保持 0 本地显存增量。

## 2. 独立架构边界与端口契约（禁止与 ysaizb 冲突）
- **禁止修改旧方案**：任何针对 `ysaizb-ultralight` 的开发或测试均严禁改动 `d:\Antigravity\ysaizb`、`D:\LiveTalking` 或 `livetalking` conda 环境。
- **固定端口表**：
  - `127.0.0.1:8020` — `engine/ultralight_server.py`（WebRTC + 音频驱动；默认只监听本机，严禁默认 `0.0.0.0`）
  - `127.0.0.1:28910` — `ai-live-hub/server.py`（中控 + WebUI + SSE `/api/events`）
  - `127.0.0.1:4455` — OBS WebSocket v5（使用独立 Scene Collection `AI_Live_Hub_Ultralight`）
- **前端极简约束**：`ai-live-hub/static/index.html` 与 `overlay.html` 必须保持零构建、纯原生 HTML/CSS/JS 单文件。
- **配置说明唯一真源**：新增/修改任何可调参数，必须同步登记到 `ai-live-hub/config_guide.py` 的 `GUIDE_ITEMS`（白话说明 + 专业说明 + 2080 推荐值），WebUI 与桌面端只从这里读取。
- **主题点播边界**：`topic_router.py` 只调取知识库已有讲稿；AI 只能从主题目录编号中选择，不得生成主题内容；配置模板只能用 `fill_template()`，禁止 `str.format`。
- **纯规则健康探针**：`ai-live-hub/health_monitor.py` 必须保持纯规则确定性检查，禁止引入 LLM 诊断或重型依赖。

## 3. Ultralight 训练与脚本规范
- **有效训练素材要求**：本地训练 Ultralight UNet 时，视频**必须**包含正常真人说话音轨（无声闭嘴待机片不可用于从头训练；无音轨视频仅在传入 `--checkpoint` 封装外部 `.pth`/`.onnx` 时允许）。
- **批处理编码铁律**：所有 `.bat` 脚本必须严格纯 ASCII + CRLF 换行（中文说明统一放 `scripts/msg/*.txt`）。
- **配置安全与无备份残留**：`*.example.json` 严禁包含真实 API Key；禁止在工作区残留 `.bak` / `_old` / `archive/` 目录；所有 JSON 写入使用原子 `.tmp` + `os.replace`。
- **改完必测、必记**：任何改动后必须在 `ai-live-hub` 运行 `python tests/test_smoke.py` 和 `python tests/test_regressions.py` 并更新 `DEVLOG.md`。
