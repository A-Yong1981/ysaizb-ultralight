# DEVLOG.md — 泳昇 Ultralight 数字人方案开发日志与避坑清单

## 1. 核心避坑清单（继承 ysaizb 经验 + Ultralight 专项）
- **U01（严禁无音轨静音视频训练）**：Ultralight-Digital-Human 的 UNet（160x160）需要音频特征与口型帧精确同步训练。`ysaizb/videos/*.mp4` 均为 `-an` 无声闭嘴待机片，直接用于训练会导致口型冻结。已在 `scripts/ultralight_pipeline.py` 强校验音频流（仅 `--checkpoint` 封装模式允许跳过音轨校验）。
- **U02（WeNet 20fps vs HuBERT 25fps 对齐）**：WeNet 必须严格 20fps（每帧 50ms / 800 samples）；HuBERT 必须 25fps（每帧 40ms / 640 samples）。`engine/audio_encoders.py` 与 `ultralight_server.py` 必须根据 `meta.json` 中的 `asr` 自动匹配 `fps` 与特征窗口形状 `[B,128,16,32]` vs `[B,16,32,32]`。
- **U03（torch.export ONNX 兼容）**：PyTorch 2.9 默认 `torch.onnx.export` 使用新 dynamo 导出器，在 `Model(6, asr)` 上易出现动态轴或算子报错，因此 `ultralight_pipeline.py` 固定传入 `dynamo=False, opset_version=11` 并立即用 `onnxruntime` 校验推理输出 `[1,3,160,160]`。
- **U04（Hubert FP16 显存控制）**：RTX 2080 仅 8GB 显存且为 Turing sm_75 架构，HuBERT-Large 加载后强制 `.half()`，避免占用额外显存；WeNet 全程 ONNX Runtime 执行。
- **U05（OBS 场景集合与端口完全隔离）**：本方案固定使用 `:8020` / `:28910` / `AI_Live_Hub_Ultralight`，严禁与原 `ysaizb`（`:8010` / `:28900` / `AI_Live_Hub`）混用端口或覆盖配置文件。
- **U06（Windows `onnxruntime-gpu` CUDA 12 与 `os.wait3` 兼容）**：PyPI 最新 `onnxruntime-gpu>=1.24`（如 `1.32.0`）依赖 CUDA 13 (`cublasLt64_13.dll`)，而 `torch 2.9.1+cu128` 为 CUDA 12.8 + cuDNN 9；因此 `requirements.txt` 固定锁定 `onnxruntime-gpu==1.22.0`，并通过 `ensure_onnxruntime_cuda_paths()` 自动将 `torch/lib` 加入 DLL 搜索路径以启用 GPU 推理。此外上游 `data_utils/get_landmark.py` 存在未使用的 `from os import wait3`（仅 Linux 存在），已在 `ultralight_pipeline.py` 注入兼容垫片。
- **P01~P38 通用铁律**：`.bat` 纯 ASCII + CRLF、锁定 `websockets==12.0`、`requirements.txt` 不含 `torch`、`*.example.json` 无真实 Key、JSON 原子写入、不留 `.bak/_old/archive/`、全成人视角合规、`health_monitor.py` 纯规则探针。

## 2. 迭代记录
- **2026-11-27（直播画面章节卡/口播字幕配置与 SIL OFL 1.1 免费商用字体）**：
  1. 新增唯一真源模块 [overlay_presets.py](ai-live-hub/overlay_presets.py)，内置 5 款 `SIL OFL 1.1` 永久免费可商用字体预设（思源黑体 Noto Sans SC、思源宋体 Noto Serif SC、思源宋特粗、离线随包内置的得意黑 `SmileySans-Oblique.ttf` 与霞鹜文楷 `LXGWWenKaiLite-Regular.ttf`）与 6 种字幕/板书视觉效果预设。
  2. 在 [server.py](ai-live-hub/server.py) 暴露 `GET/POST /api/overlay/config` 与 SSE `overlay_update` 热推送，改造 [overlay.html](ai-live-hub/static/overlay.html) 支持右上角/左上角知识库章节卡开关与下部口播字幕实时切换、字号/颜色/描边/底框透明度/边距与动效配置，OBS 无需刷新浏览器源。
  3. 在桌面端 [app.py](launcher/app.py) Tab 4 新增快捷控制栏及 Pillow 真字体渲染详调弹窗，在 WebUI [index.html](ai-live-hub/static/index.html) 同步新增折叠控制面板；补齐 [test_smoke.py](ai-live-hub/tests/test_smoke.py) 与 [test_regressions.py](ai-live-hub/tests/test_regressions.py) 用例并重编 `泳昇Ultralight直播平台.exe`。
- **2026-11-27（OBS 隔离启动秩序与 Tab 4 布局修复）**：修复 Tab 4 底部预览与互动按钮裁切，补全 `auto_obs`、OBS `.sentinel` Safe-Mode 防弹窗清理与「先拉起 OBS 再切平台」顺序，保持 `AI_Live_Hub`（`:28900`）与 `AI_Live_Hub_Ultralight`（`:28910`）两套独立 OBS 配置互不覆盖。
- **2026-11-27（同主播零训练极速换装）**：利用 Ultralight UNet 仅驱动嘴部 `160x160` 区域的特性，在 [ultralight_pipeline.py](file:///d:/Antigravity/ysaizb-ultralight/scripts/ultralight_pipeline.py) 新增 `--base_avatar` 参数（`epochs=0` 零重训直接复用已训主播的 `unet.onnx`/`ultralight.pth`，仅抽取新服饰帧与关键点，支持无声换装视频）；修复 `backend.py` 热切路由 `/api/avatar/switch`；在桌面端 `🎓 主播训练与管理` 新增 **「👗 为选中主播换新衣服(零训练)」** 按钮及弹窗，支持一键生成同主播多套服装造型并在「开播预设 / 直播中」绑定不同讲稿或弹幕关键词换装。
- **2026-11-27（傻瓜式 AI 智能配参训练工作台）**：新增 [ultralight_config_kb.md](file:///d:/Antigravity/ysaizb-ultralight/launcher/ultralight_config_kb.md) 显存与超参配置专家知识库及 `backend.py` 的 `ai_recommend_ultralight_config()`（接入已配置的大模型容灾池 + 硬件/静音防呆钳位 + 离线规则兜底）；改造 Tab 6 为「只选参考视频 + 快捷场景/一句话提要求 → 点 AI 分析配置 → 展示通俗人话解读 → 一键生成」，并将底层超参折叠进默认隐藏的高级区，重编 `泳昇Ultralight直播平台.exe` 并通过测试。
- **2026-11-27（桌面端集成与 EXE 打包）**：对照原 `ysaizb` 独立桌面应用，升级 `launcher/app.py`、`launcher/backend.py`、`scripts/ultralight_pipeline.py` 并新增 [build_exe.py](file:///d:/Antigravity/ysaizb-ultralight/scripts/build_exe.py)：
  1. **完整控制单元集成**：顶部栏新增可点击 `🩺 诊断/工具箱` 胶囊（6 模块健康探针、强制解锁、模型下载、环境自检、全量 Key 测试、一键开 WebUI）；Tab 1 集成「录音克隆硅基音色」与「全测 Key」；Tab 3 集成「重建索引」「问法测试」；Tab 4 集成「自动答疑/礼物打断/冷场暖场开关」「讲解暂停/切换」「清队列/停收弹幕」「绿幕/知识库视频/原画面切换与 WebGL 抠像滑块」「表情快捷动作」与 OBS 舞台 URL 复制。
  2. **独立「🎓 主播训练与管理」中心**：支持 `Treeview` 形象资产管理表（ASR/帧率/ONNX+PTH 状态/动作数、设为主播、直播热切换、PTH 重导 ONNX、导入外部完整目录、删除）、说话视频音轨防呆体检、从头训练 / 断点续训 / 外部权重封装三模式、0~100% 实时进度条、实时日志与强制终止子进程。
  3. **独立 EXE 打包**：新增 `build_exe.bat`、`00_gpu_info.bat`、`05_test_keys.bat`，打包生成根目录独立桌面程序 `泳昇Ultralight直播平台.exe`。
- **2026-11-27**：完成 `d:\Antigravity\ysaizb-ultralight` 全套独立方案搭建（独立 `ultralight` conda 环境、模型下载器、预处理/训练/ONNX导出流水线、WeNet+HuBERT 双后端 WebRTC 引擎 `:8020`、隔离中控 `:28910`、OBS 场景隔离、Tkinter 启动器集成与全套回归测试）。
