# 泳昇 Ultralight 数字人直播平台（独立路线）

本目录（`ysaizb-ultralight`）是基于 **Ultralight-Digital-Human**（轻量级 160x160 UNet 口腔驱动 + WeNet 20fps ONNX / HuBERT 25fps FP16 双特征引擎）搭建的**完全独立**实时数字人直播方案，与 `ysaizb`（`wav2lip256 + LiveTalking`）彻底物理隔离、互不干扰。

## 1. 目录与端口隔离

| 组件 | `ysaizb` (Wav2Lip 路线) | `ysaizb-ultralight` (本方案) |
| :--- | :--- | :--- |
| 代码根目录 | `d:\Antigravity\ysaizb` | `d:\Antigravity\ysaizb-ultralight` |
| Conda 环境 | `livetalking` | `ultralight` |
| 推理引擎 | `D:\LiveTalking` (`:8010`) | `engine/ultralight_server.py` (`:8020`) |
| Hub 中控端口 | `:28900` | `:28910` |
| 形象资产目录 | `D:\LiveTalking\data\avatars` | `avatars/` (`full_body_img/`, `landmarks/`, `unet.onnx`) |
| OBS 场景集合 | `AI_Live_Hub` | `AI_Live_Hub_Ultralight` |

## 2. 两条音频特征路线对比

- **HuBERT 25fps (`--asr hubert`)**（默认推荐）：
  - 帧率 `25 fps`（40ms/帧），唇形精度高，RTX 2080 实测约 ~1.3 GB 显存；
  - 特征模型：`models/hubert-large-ls960-ft`（FP16）；
- **WeNet 20fps (`--asr wenet`)**（极致轻量 ONNX）：
  - 帧率 `20 fps`（50ms/帧），纯 ONNX Runtime 执行，显存 < 1 GB；
  - 特征模型：`models/encoder.onnx` + `kaldi-native-fbank`；

## 3. 快速上手流程

1. **安装环境**（若尚无 `ultralight` 环境，会自动克隆 `Ultralight-Digital-Human` 并生成默认 `config.json` / `platform_profiles.json`）：双击 `01_install.bat`
2. **下载基础模型**（`encoder.onnx`、`hubert-large-ls960-ft`、`VGG19`）：双击 `02_download_models.bat`
3. **环境与回归自检**：双击 `03_check_env.bat` 或 `05_smoke_test.bat`
4. **制作/训练 Ultralight 主播形象**：
   - ⚠️ **重要前提**：Ultralight 的 UNet 是针对**特定说话人**从头训练的；且本地训练视频**必须包含正常真人说话画面与音轨**（3~5 分钟、正脸清晰、光照稳定），严禁使用静音闭嘴待机片从头训练！
   - **路线 A（本地训练 + 导出 ONNX）**：
     ```bat
     04_train_avatar.bat host_id host_name hubert D:\path\to\speaking_video.mp4
     ```
   - **路线 B（已有外部训练好的 `.pth` 或 `.onnx` 权重直接封装）**：
     ```bat
     scripts\run_train.bat --avatar_id host_id --name host_name --asr hubert --video D:\path\to\speaking_video.mp4 --checkpoint D:\path\to\ultralight.pth
     ```
   - **路线 C（同一位已训主播零重训极速换装，10秒出新衣服造型）**：
     ```bat
     scripts\run_train.bat --avatar_id host_suit --name 商务西装 --base_avatar host_id --epochs 0 --video D:\path\to\new_outfit_video.mp4
     ```
   - 也可直接双击根目录 **`泳昇Ultralight直播平台.exe`**（或运行 `python launcher/app.py`）打开独立桌面应用，在「🎓 主播训练与管理」页图形化完成视频体检、AI 一键配参训练、**一键为主播换新衣服**、外部权重封装与 ONNX 导出。
5. **一键开播**：推荐直接双击 **`泳昇Ultralight直播平台.exe`**（已集成工作流启停、AI 模型与录音克隆、主播训练与管理、知识库、OBS 多模式/实时策略/绿幕抠像/章节字幕与免费商用字体控制、平台配置及系统诊断工具箱）；也支持双击 `start_all.bat` 纯控制台启动（拉起 `:8020` Ultralight 引擎与 `:28910` 中控）。
6. **OBS 画面接入**：浏览器源指向 `http://127.0.0.1:28910/stage?layer=all`（在桌面启动器点「一键自动配置并导入 OBS」会自动导入 `AI_Live_Hub_Ultralight` 场景集合；内置 5 款 `SIL OFL 1.1` 免费商用字体并支持实时热更新右上角/左上角知识库章节卡与下部口播字幕）。
7. **重新打包桌面 EXE**（如修改了 `launcher/` 代码或首次从 GitHub 源码克隆）：双击 `build_exe.bat`。

## 更新到最新版（推荐方式）

关闭桌面程序后，双击根目录 **`06_update.bat`**：自动保存你的个人设置、备份你改过的文件、拉取最新代码、还原、重新打包 `泳昇Ultralight直播平台.exe`。完成后窗口标题栏和网页控制台左上角会显示新版本号（如 `v2026.11.27-r3`）。

> 你的主播、声线、引擎参数等个人设置保存在 `env.local.bat`（不进 git），`env.bat` 只放默认值，更新时不会冲突。

## 4. 观众点播主题 + 傻瓜配置（新）

- **观众点播主题**：观众在弹幕打“先讲讲防骗”“想听三国”“说说 AI 换脸”，只要知识库里有这个主题，主播就把这部分讲稿整段讲给他听（默认 6 段约 1.5 分钟），讲完自动“回到「原章节」接着讲”。说法明显的关键词秒判；说得含糊的（“讲讲那个怎么防止被骗钱的事”）交给已配置的大模型判断，大模型只能从主题目录里选，不会编内容。点播中途有人提问会先简短回答，再“咱们接着说……”。
  - WebUI「🎯 观众点播主题」面板：看当前点播、切换识别方式、测试观众说法会点到哪、浏览主题目录并手动“现在讲”。
  - 桌面端「3. 知识库主题」页 →「🎯 主题点播目录」。
  - 讲稿里给章节加一行 `> [主题词] 防骗|反诈|骗子`，写上观众平时的叫法，识别更准。
- **🧸 傻瓜配置向导**（WebUI 高级功能区 / 桌面端顶栏「🧸 傻瓜配置」）：回答 3 个大白话问题自动配好；或点一个预设（🛡️ 稳定开播 · RTX 2080 推荐 / 👄 口型最清晰 / 🍃 最省资源 / 💬 互动答疑优先 / 📚 讲课连播优先）；「配置体检」按 12400F / 32GB / RTX 2080 红线找问题并一键修复；每个参数都有白话说明和推荐值。
- **2080 主机推荐（稳定开播预设即此值）**：`UL_MAX_SESSION=1`、`UL_OUT_HEIGHT=1280`、`UL_MAX_FRAMES=300`、`UL_MAX_LOADED=2`、`UL_ORT_GPU_MB=2048`、`UL_BATCH=4`、`UL_HOST=127.0.0.1`；OBS 25fps + NVENC。

## 5. 关键脚本说明

- `engine/ultralight_server.py`：原生 Ultralight 实时 WebRTC 推流与音频驱动服务（兼容 `ai-live-hub` `/offer`, `/human`, `/humanaudio`, `/is_speaking`, `/interrupt_talk`, `/set_audiotype`, `/api/avatar/*`）。
- `scripts/download_models.py`：下载 `encoder.onnx`、HuBERT、VGG19，校验 SCRFD/PFLD。
- `scripts/ultralight_pipeline.py`：一键预处理 -> `train.py` 训练 -> `unet.onnx` 导出（ONNXRuntime 校验）-> 封装进 `avatars/<id>/`。
- `ai-live-hub/topic_router.py`：知识库主题目录 + 弹幕点播识别（关键词秒判 + AI 复核）+ 插播计划。
- `ai-live-hub/config_guide.py`：参数白话说明、一键预设、三问向导、配置体检（WebUI 与桌面端共用）。
- `launcher/app.py`：独立桌面启动器（双主题界面、全 6 步工作流与 Ultralight 参数/训练集成）。
