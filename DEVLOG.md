# DEVLOG.md — 泳昇 Ultralight 数字人方案开发日志与避坑清单

## 1. 核心避坑清单（继承 ysaizb 经验 + Ultralight 专项）
- **U01（严禁无音轨静音视频训练）**：Ultralight-Digital-Human 的 UNet（160x160）需要音频特征与口型帧精确同步训练。`ysaizb/videos/*.mp4` 均为 `-an` 无声闭嘴待机片，直接用于训练会导致口型冻结。已在 `scripts/ultralight_pipeline.py` 强校验音频流（仅 `--checkpoint` 封装模式允许跳过音轨校验）。
- **U02（WeNet 20fps vs HuBERT 25fps 对齐）**：WeNet 必须严格 20fps（每帧 50ms / 800 samples）；HuBERT 必须 25fps（每帧 40ms / 640 samples）。`engine/audio_encoders.py` 与 `ultralight_server.py` 必须根据 `meta.json` 中的 `asr` 自动匹配 `fps` 与特征窗口形状 `[B,128,16,32]` vs `[B,16,32,32]`。
- **U03（torch.export ONNX 兼容）**：PyTorch 2.9 默认 `torch.onnx.export` 使用新 dynamo 导出器，在 `Model(6, asr)` 上易出现动态轴或算子报错，因此 `ultralight_pipeline.py` 固定传入 `dynamo=False, opset_version=11` 并立即用 `onnxruntime` 校验推理输出 `[1,3,160,160]`。
- **U04（Hubert FP16 显存控制）**：RTX 2080 仅 8GB 显存且为 Turing sm_75 架构，HuBERT-Large 加载后强制 `.half()`，避免占用额外显存；WeNet 全程 ONNX Runtime 执行。
- **U05（OBS 场景集合与端口完全隔离）**：本方案固定使用 `:8020` / `:28910` / `AI_Live_Hub_Ultralight`，严禁与原 `ysaizb`（`:8010` / `:28900` / `AI_Live_Hub`）混用端口或覆盖配置文件。
- **U06（Windows `onnxruntime-gpu` CUDA 12 与 `os.wait3` 兼容）**：PyPI 最新 `onnxruntime-gpu>=1.24`（如 `1.32.0`）依赖 CUDA 13 (`cublasLt64_13.dll`)，而 `torch 2.9.1+cu128` 为 CUDA 12.8 + cuDNN 9；因此 `requirements.txt` 固定锁定 `onnxruntime-gpu==1.22.0`，并通过 `ensure_onnxruntime_cuda_paths()` 自动将 `torch/lib` 加入 DLL 搜索路径以启用 GPU 推理。此外上游 `data_utils/get_landmark.py` 存在未使用的 `from os import wait3`（仅 Linux 存在），已在 `ultralight_pipeline.py` 注入兼容垫片。
- **U07（引擎只给本机用）**：`ultralight_server.py` 默认 `--host 127.0.0.1`、CORS 只放行本机 `:28910/:8020`；中控在本机代理 WebRTC，绝不需要 `0.0.0.0`。`.pth` 一律经 `safe_torch_load()`（`weights_only=True`），只有 `meta.json` 标记 `pth_trusted` 的本机训练/已校验权重才放宽；外部权重导入时先严格校验。
- **U08（说话必须串行保序 + 打断必须清干净）**：`/human` 与 `/humanaudio` 统一进每会话一条 `_speech_worker` 队列（分句流水线合成，首句先开口）；打断时 `_flush_gen` 代次 +1，丢弃打断前仍在合成/提特征的句子，并由渲染线程清空音视频轨道缓冲与 `_audio_accum`。
- **U09（2080 主机资源红线落到代码）**：帧按 `UL_OUT_HEIGHT`（默认 1280）缩放后常驻、`UL_MAX_FRAMES` 默认 300、形象 LRU 只留 `UL_MAX_LOADED` 个；ONNX Runtime 显存池 `UL_ORT_GPU_MB` 封顶；`UL_MAX_SESSION` 默认 1；训练走独立子进程，有直播画面在线时返回 409 拒绝（可强制）。
- **U10（主题点播只讲知识库里有的）**：`topic_router.py` 按「分类/章/小节」跨讲稿建主题目录；AI 只能从目录编号里选，不能编主题；冷却期内重复点播只回顾重点；模板一律 `fill_template()`，禁止对配置模板用 `str.format`。
- **U11（配置说明唯一真源）**：参数白话说明、预设、向导、体检全部在 `ai-live-hub/config_guide.py`，WebUI 与桌面端共用；`/api/guide/apply` 只接受预设/向导/登记过的参数/体检修复项，不接受任意补丁；写 env.bat 校验 ASCII 与危险字符。
- **U12（音画必须共用一个时钟）**：WebRTC 音频轨与视频轨的发送节奏必须以同一个会话时钟 t0（画面开始拉流时刻）为基准；禁止两条轨道各自从“第一次被拉取”开始计时——OBS/浏览器常先拉音频后拉画面，会让声音永久领先约一个前瞻缓冲（实测模拟 ~275ms，表现为“声音停了嘴还在动”）。残余固定延迟用 `UL_AV_OFFSET_MS` 微调。
- **U13（预览绝不能抢 OBS）**：预览页 `/stage?preview=1` 一律以 `role=preview` 连接；OBS 已在推时引擎只建 `MirrorSession` 复用渲染帧（不推理、不占 `max_session`），中控只对 `role=main` 绑定说话会话；OBS 后到时引擎让临时当主画面的预览让位。预览开关 `/api/preview`（`ui.preview_enabled`）关闭时广播 `preview_update`，所有预览页断开并停止重连。
- **U14（字幕调大必须换行不溢出）**：`#subtitle-box` 必须 `overflow-wrap:anywhere` + `box-sizing:border-box`；字号上限 80，按 `sub_max_lines` 判定行数，超出时 `paginate`（按标点分屏、每屏≈字数/4.6 秒）或 `shrink`（逐级缩小到 14px，仍放不下转分屏）。
- **P01~P38 通用铁律**：`.bat` 纯 ASCII + CRLF、锁定 `websockets==12.0`、`requirements.txt` 不含 `torch`、`*.example.json` 无真实 Key、JSON 原子写入、不留 `.bak/_old/archive/`、全成人视角合规、`health_monitor.py` 纯规则探针。

## 2. 迭代记录
- **2026-11-27（预览开关 + 字幕字号与自动换行）**：
  1. 预览开关：WebUI「直播画面预览」开关与桌面端「开启预览 / 🖥 看真实画面」共用 `/api/preview`；预览页在 OBS 开播时共享同一路画面（`MirrorSession`），模拟测试 OBS 与预览两路音画偏差均 0ms，中控说话会话始终留在 OBS。新增 `UL_MAX_PREVIEW`（默认 1）。
  2. 字幕：字号 14→80px，WebUI/桌面端加 A−/A＋；新增“最多几行 / 太长放不下时（分屏轮播·自动缩小）/ 最宽占屏幕%”；舞台页强制换行、分屏时长按语速估算（各屏累计≈整句朗读时长）；桌面详调预览按真实规则换行演示。
  3. 傻瓜配置新增“直播画面”分组 4 项 + `UL_MAX_PREVIEW`；新增回归测试 `test_subtitle_wrap_and_preview_switch`。
- **2026-11-27（修复音画不同步：声音停了嘴还在动）**：`ultralight_server.py` 音视频轨道改为共用会话时钟（视频开拉为 t0，音频等画面开始后一起计时，纯音频客户端 2 秒兜底；卡顿超 2 秒整体顺延）；模拟“画面晚 1~3 秒开拉”时声音领先由 ~275ms 降为 0ms，卡顿 2.5 秒恢复后仍对齐；新增 `UL_AV_OFFSET_MS` 音画微调（傻瓜配置可调，体检提示过大值）。
- **2026-11-27（审核优化：主题点播 + 傻瓜配置 + 引擎安全/性能 + 训练提速）**：
  1. 新增 [topic_router.py](ai-live-hub/topic_router.py)：全部讲稿按主题索引；弹幕“先讲讲/想听/说说 + 主题”由关键词秒判、含糊说法交给已配置大模型复核（只能选目录编号），命中即插播该主题讲稿（礼物 > 点播 > 普通提问 > 章节连播），点播期间可穿插答疑，讲完自动回原章节；讲稿支持 `> [主题词] a|b` 补充观众叫法；新增 `/api/topics*` 接口、WebUI「🎯 观众点播主题」面板与桌面端「🎯 主题点播目录」。
  2. 新增 [config_guide.py](ai-live-hub/config_guide.py)：31 项参数白话说明 + 专业说明 + 2080 推荐值，5 个一键预设、三问向导、按主机红线体检与一键修复；新增 `/api/guide*`、WebUI「🧸 傻瓜配置向导」与桌面端顶栏「🧸 傻瓜配置」。
  3. 引擎：默认只监听本机、CORS 收紧、`.pth` 安全加载、串行保序说话队列 + 分句流水线、打断清空轨道缓冲、帧缩放 + LRU、ORT 显存上限、动态 batch 追帧、事件唤醒替代 4ms 轮询、训练子进程 + 直播中 409 防呆 + 可取消、会话 ID 改 uuid。
  4. 训练：FP16 混合精度、多进程读数据（失败自动退回）、5% 验证集挑 best.pth、ONNX 动态 batch 导出、meta.json 原子写入。
  5. 工程：`.gitattributes` 强制 .bat CRLF 并全部转换；全新克隆自动由 `*.example.json` 生成配置；OBS 预设改 25fps；字幕字体缺失时退回随包霞鹜文楷（可选下载思源黑体）；修复桌面端「测试问法检索」缺 `import json`；`T()`/礼物模板改安全填充；平台状态原子写入；新增 4 组回归测试。
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
