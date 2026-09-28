<div align="center">

# EraseDub

**去除视频里的硬字幕、翻译、配音——一个工具搞定，在你自己的电脑上运行。**

[English](README.md) | **简体中文** | [Tiếng Việt](README.vi.md)

[文档](docs/index.md) · [安装](docs/index.md#install) · [路线图](docs/roadmap.md)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

</div>

<p align="center">
  <img src="https://github.com/EraseDub/erasedub/raw/main/docs/assets/demo-before-after.webp" alt="前后对比：去掉烧录在画面里的中文字幕，加上越南语字幕" width="720">
</p>
<p align="center"><sub>前 / 后：去掉烧录在画面里的中文字幕，加上越南语字幕。演示素材在 EraseDub 之外制作。<em>Tears of Steel</em> © Blender Foundation | mango.blender.org</sub></p>

EraseDub 是一个开源的 AI 视频翻译、配音和视频本地化工具。它用视频修复（inpainting）去除硬字幕（烧录字幕、
内嵌字幕），用 Whisper 做语音识别（语音转文字），用 Google 翻译或大语言模型（LLM）翻译字幕，再用 AI 语音合成
（TTS）配音并加上新字幕。

## 工作流程

```text
视频 ──> 去除画面文字 ──> 语音识别 ──> 翻译 ──> 配音 ──> 加字幕 ──> video.<lang>.mp4
```

可以一次跑完（`erasedub run`），也可以分两步：`prepare` 生成可编辑的字幕文件，你用任意字幕编辑器改好译文，
再用 `render` 出片。

## 演示

完整的前后对比，左右并排：左边是带烧录中文字幕的原片，右边是同一画面去掉字幕后的效果（没有加新字幕，方便看清去字效果）。演示素材在 EraseDub 之外制作。对比动图没有声音；带配音和字幕（越南语、英语、中文）的完整视频附在 v0.1 的发布页中。

<p align="center">
  <img src="https://github.com/EraseDub/erasedub/raw/main/docs/assets/compare-horizontal.webp" alt="Tears of Steel 一个场景的完整前后对比：左边带烧录的中文字幕，右边是同一画面去掉字幕后的效果" width="960">
</p>
<p align="center"><sub><em>Tears of Steel</em> 的一个场景（19 秒），中文字幕是我们自己压上去的。<em>Tears of Steel</em> © Blender Foundation | mango.blender.org</sub></p>

<p align="center">
  <img src="https://github.com/EraseDub/erasedub/raw/main/docs/assets/compare-vertical.webp" alt="竖屏商品视频的完整前后对比：左边带原有的烧录中文字幕，右边是同一画面去掉字幕后的效果" width="720">
</p>
<p align="center"><sub>一段竖屏商品视频（15 秒），自带烧录的中文字幕。</sub></p>

## 为什么做这个

要翻译一段字幕已经“烧”进画面的视频，需要三件事：去掉原来的文字、翻译语音、配上新的声音和字幕。
现有的开源工具各自做好了一部分：

- [pyvideotrans](https://github.com/jianchang512/pyvideotrans) 和 [VideoLingo](https://github.com/Huanshere/VideoLingo)
  能识别、翻译、配音，但**不去除硬字幕**，新字幕下面还压着旧字。
- [video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) 能去除硬字幕，但**不翻译也不配音**。

EraseDub 一次跑完全流程，在本地运行，默认方案免费、无需任何 API Key。

## 功能

每一步用到的工具见 [Provider](#provider)。

- **一个工具做完全部**：去字、识别、翻译、配音、加字幕，一次运行完成。
- **默认免费，无需 API Key**：只有想用付费的翻译或配音时才需要自己的 Key。
- **没有显卡也能用**：会跳过去字这一步并明确提示，其余步骤照常运行；也可以用你自己的 [Modal](https://modal.com)
  账号在云端去字（`--gpu modal`），或手动选用在 CPU 上运行的去字器 `lama`（更慢，容易闪烁，会把重复花纹的背景抹平）。
- **两步流程**：`prepare` 为每种目标语言生成可编辑的字幕文件（如 `script.vi.srt`），用任意字幕编辑器改好后再 `render`。
- **普通字幕**：画面底部居中，可选背景音乐和响度标准化。
- **命令行 + 简洁的网页界面**（Gradio）。
- **插件**：每一步都是一个 provider，可以由第三方包替换（[docs/plugins.md](docs/plugins.md)）。

## Provider

每一步都是一个 provider，在 `erasedub.toml` 里选择；`erasedub plugins` 会列出已安装的 provider。

| 步骤 | 默认（免费、无需 Key） | 可选 | 需要什么 |
|---|---|---|---|
| 去除画面文字 | `sttn` —— STTN 视频修复 | `propainter` —— ProPainter，难处理的画面效果更好，许可证**禁止商用** · `lama` —— LaMa，给没有 NVIDIA 显卡的电脑用，逐帧处理：容易闪烁，会把重复花纹的背景抹平 · `none` —— 保留原画面 | `sttn`、`propainter`：NVIDIA 显卡。`lama`：CPU 或 Apple GPU（MPS）。附加包 `erase`、`propainter`、`lama` |
| 去字在哪里运行 | `local` —— 本机的 NVIDIA 显卡 | `modal` —— [Modal](https://modal.com) 云端 GPU（`--gpu modal`） | `modal`：附加包 `modal` 和你自己的 Modal 账号，费用记在该账号 |
| 检测画面文字 | `rapidocr` —— RapidOCR | — | CPU。附加包 `ocr` |
| 语音识别 | `whisperx` —— WhisperX | — | 附加包 `asr`；有 NVIDIA 显卡时更快。可选：逐词时间戳（`align`，因许可证原因默认关闭）和说话人标注（`diarize`，需要 `HF_TOKEN`） |
| 翻译 | `google` —— Google 翻译网页接口 | `openai`（或任意 OpenAI 兼容 API）· `gemini` · `claude` · `ollama` —— 本地模型，无需 Key | `google`：需联网，非官方。`openai`/`gemini`/`claude`：附加包 `llm` 和你自己的 API Key。`ollama`：附加包 `ollama` 和本机的 Ollama 服务 |
| 配音 | `edge` —— Edge-TTS 语音 | `elevenlabs` —— ElevenLabs | `edge`：需联网，非官方。`elevenlabs`：附加包 `elevenlabs` 和你自己的 API Key |
| 字幕布局 | `bottom` —— 底部居中 | — | 带 libass 的 ffmpeg |

`erasedub[full]` 会安装 `asr`、`ocr`、`erase`、`ollama` 和 `webui`：所有不需要 Key 的部分，不含可选去字器。
许可证见 [模型与服务的许可](#模型与服务的许可)。
该选哪个去字器：[docs/erasers.md](docs/erasers.md)。

## 快速开始

**Windows：便携包**（从 GitHub 发布页下载）。解压后双击 `EraseDub.exe` 即可；不用装 Python，已带好 CUDA 版
PyTorch。见 [Windows 安装指南](docs/install/windows.md)。

**从源码安装**（需要 [uv](https://docs.astral.sh/uv/) 或 pip、Python 3.11–3.13，以及带 libass 和 libx264 的 ffmpeg，
见 [安装指南](docs/index.md#install)）：

```bash
git clone https://github.com/EraseDub/erasedub
cd erasedub
uv sync --python 3.12 --extra full      # 或者在 venv 里：pip install -e ".[full]"
source .venv/bin/activate               # Windows：.venv\Scripts\activate
erasedub doctor                         # 检查 Python、ffmpeg、显卡、各 provider 和 Key
```

一步到位：

```bash
erasedub run video.mp4 --to vi          # -> video.vi.mp4
```

或者分两步，先改好译文：

```bash
erasedub prepare video.mp4 --to vi      # 识别 + 检测文字 + 翻译
                                        # -> work/video/script.vi.srt（+ script.vi.json、regions.json）
# 用记事本、Subtitle Edit 等修改 work/video/script.vi.srt（可选）
erasedub render video.mp4               # 去字（GPU）+ 配音 + 字幕 -> video.vi.mp4
```

**网页界面**（已包含在 `full` 中）：

```bash
erasedub webui --open                   # 启动 http://127.0.0.1:7860 并在浏览器中打开
```

**Docker**（Linux amd64）：在存放视频的文件夹上运行 `ghcr.io/erasedub/erasedub` 镜像。
运行命令、GPU 设置和文件权限见 [Docker 指南](docs/install/docker.md)。

详见 [命令行参考](docs/cli.md) 和 [配置](docs/configuration.md)。

## 硬件

去除硬字幕属于视频修复（inpainting），默认的去字器需要 NVIDIA 显卡。其余步骤普通电脑就能跑。

| 你的电脑 | 去除硬字幕 | 识别、翻译、配音、字幕 |
|---|---|---|
| Linux + NVIDIA 显卡 | 可以，本地运行 | 可以 |
| Windows + NVIDIA 显卡 | 可以，本地运行，但要先装 **CUDA 版 PyTorch**（见下） | 可以 |
| 没有 NVIDIA 显卡（macOS、AMD 或 Intel 显卡） | 默认跳过，并给出提示。也可手动选用 `lama` 去字器：在 CPU 或 Apple GPU（MPS）上运行；更慢，逐帧处理（容易闪烁），会把重复花纹的背景抹平 | 可以（CPU 上识别较慢） |
| 没有 NVIDIA 显卡 + `--gpu modal` | 可以，在 [Modal](https://modal.com) 上运行，费用记在**你自己的** Modal 账号 | 可以，本地运行 |
| 租用的 GPU（vast.ai、RunPod 等） | 可以 | 可以 |

<details>
<summary><b>Windows + NVIDIA 从源码安装时：换成 CUDA 版 PyTorch</b></summary>

推荐使用 Windows 便携包，里面已带好合适的 PyTorch。
从源码安装时：PyPI 上的 PyTorch 在 Windows 下只有 CPU 版。请在 `uv sync --python 3.12 --extra full` **之后**，
从 PyTorch 官方源换成 WhisperX 要求的版本（torch 2.8）的 CUDA 12.8 版：

```powershell
uv pip install --force-reinstall --no-deps "torch==2.8.0" "torchaudio==2.8.0" "torchvision==0.23.0" --index-url https://download.pytorch.org/whl/cu128
erasedub doctor
```

之后再运行 `uv sync` 会装回 CPU 版；那时请重新执行上面的安装。

漏了这一步时，`erasedub doctor` 会提示已安装的 torch “is a CPU-only build but an NVIDIA GPU is present”。
详见 [Windows 安装指南](docs/install/windows.md)。

</details>

默认的免费翻译和配音（Google 翻译、Edge-TTS）是在线服务，需要联网。
参考：[租用 GPU](docs/gpu-rental.md) · [常见问题排查](docs/troubleshooting.md)。

## 语言

源语言：WhisperX 能识别的语言都可以。

| 目标语言 | 状态 |
|---|---|
| 越南语（`vi`）、英语（`en`）、中文（`zh`） | **已验证**：每次发布前人工检查译文、配音和字幕 |
| 你选的翻译和 TTS 支持的其他语言 | **支持**：不在发布前的检查范围内 |

非常欢迎其他语言的反馈和修正。

## 说明

- **用 `sttn` 或 `propainter` 去字需要 NVIDIA 显卡**：本机、租用的机器或 Modal（`--gpu modal`，费用记在你的账号）
  都行。没有显卡时，去字这一步会跳过并给出提示，或者选用 `lama`。
- **`lama` 在 CPU 或 Apple GPU（MPS）上运行。** 它逐帧处理，所以更慢，容易闪烁，会把重复花纹的背景抹平。
- **免费的翻译和配音是非官方服务。** Google 翻译和 Edge-TTS 通过公开的网页接口调用，并不是面向开发者的 API，
  可能变更或被限流。
- **每种目标语言只用一个声音**（`tts.voice`）读完整段视频。说话人标注（可选，`diarize`）会写进脚本，但不用来选择声音。
- **只支持 amd64**：Docker 镜像和 Windows 便携包都是。

## 对比

下表对比 EraseDub v0.1 与同类开源项目（EraseDub 受它们启发，但没有复用它们的代码）。其他项目的内容来自它们
2026-09-26 的 README 和文档；❌ 表示没有提供；“?” 表示没有说明。如有错误欢迎指正。

| | EraseDub v0.1 | [pyvideotrans](https://github.com/jianchang512/pyvideotrans) | [VideoLingo](https://github.com/Huanshere/VideoLingo) | [video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) |
|---|---|---|---|---|
| 去除硬字幕 | ✅ STTN；可选 ProPainter、LaMa | ❌ | ❌ | ✅ STTN、LaMa、ProPainter、OpenCV |
| 语音识别 | ✅ WhisperX | ✅ faster-whisper 及多种 API | ✅ WhisperX、ElevenLabs API | ❌ |
| 翻译 | ✅ Google（免费）、自带 Key 的大模型、Ollama | ✅ 大模型、Google、微软等 | ✅ OpenAI 兼容的大模型 | ❌ |
| 配音（TTS） | ✅ Edge-TTS（免费）、ElevenLabs | ✅ Edge-TTS 及更多 | ✅ Edge-TTS 及更多 | ❌ |
| 声音克隆 | ❌（路线图中） | ✅ F5-TTS、CosyVoice、GPT-SoVITS | ✅ GPT-SoVITS、CosyVoice2、F5-TTS | ❌ |
| 出片前可修改脚本 | ✅ `script.<lang>.srt` | ✅ 每个阶段可暂停校对 | ?（每步可暂停/继续，未说明能否编辑） | — |
| 界面 | 命令行、网页（Gradio） | 桌面 GUI、命令行、网页（Gradio） | 网页（Streamlit）、批量模式（beta） | 桌面 GUI、命令行 |
| 去字所需硬件 | NVIDIA 显卡，或用你自己的 Modal 账号；用 LaMa 时 CPU 或 Apple GPU 即可 | — | — | NVIDIA、DirectML（AMD/Intel）、CPU、Apple Silicon |
| 安装方式 | 源码（uv 或 pip）、Docker（amd64）、Windows 便携包 | Windows `.exe`、源码、Dockerfile | 源码、Dockerfile | Windows 预编译包、源码、Docker 镜像 |
| 许可证 | Apache-2.0 | GPL-3.0 | Apache-2.0 | Apache-2.0 |
| GitHub Star（2026-09-27） | 新项目 | 约 1.92 万 | 约 1.85 万 | 约 1.31 万 |

这些项目在各自的领域都很出色。如果你只需要其中一步，直接用它们就好。

## 模型与服务的许可

EraseDub 自身代码采用 Apache-2.0。它可以调用的模型和服务各有条款：

- **有一个可选去字器禁止商用。** ProPainter（S-Lab License 1.0）不能用于商业用途。它只能手动开启（`propainter`
  附加包，不在 `full` 中），从不随 EraseDub 一起分发，每次被选用时 EraseDub 都会显示其许可证。
  商用前请先确认所选可选去字器的许可证。
  出于同样的原因，WhisperX 的逐词对齐**默认关闭**：它的部分语言对齐模型（例如越南语）是 CC BY-NC 4.0。
- Edge-TTS 和免费的 Google 翻译接口属于对微软、Google 在线服务的非官方使用，随时可能变更或限流。请自行确认是否符合你的用途。
- Docker 镜像和 Windows 便携包附带 GPL 版 ffmpeg（libx264 需要），并随每个版本一起提供其许可证、确切的上游版本和对应源代码。

详情和来源：[docs/models-and-licenses.md](docs/models-and-licenses.md)。你需要自行确保拥有所处理视频的相应权利。

## 文档

- [文档目录](docs/index.md)
- 安装：[Linux](docs/install/linux.md) · [macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Docker](docs/install/docker.md)
- [命令行参考](docs/cli.md) · [配置](docs/configuration.md) · [脚本格式](docs/script-format.md) · [如何选择去字器](docs/erasers.md)
- [架构](docs/architecture.md) · [编写插件](docs/plugins.md)
- [租用 GPU](docs/gpu-rental.md) · [常见问题排查](docs/troubleshooting.md)
- [模型与许可证](docs/models-and-licenses.md) · [路线图](docs/roadmap.md)

（详细文档目前只有英文。）

## 社区与支持

所有交流都在 GitHub 上：

- **提问、想法、分享成果：** [GitHub Discussions](https://github.com/EraseDub/erasedub/discussions)。
- **Bug 和安装问题：** [GitHub Issues](https://github.com/EraseDub/erasedub/issues)，请使用 issue 表单。
  [SUPPORT.md](SUPPORT.md) 说明了要附上哪些信息（先附上 `erasedub doctor` 的输出）。
- **安全问题：** 不要发在公开 issue 里。请通过 GitHub 的私密漏洞报告功能私下报告，做法见 [SECURITY.md](SECURITY.md)。

## 参与贡献

欢迎贡献，尤其是 bug 报告、不同机器上的安装反馈，以及 vi/en/zh 以外语言的效果检查。
请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [行为准则](CODE_OF_CONDUCT.md)。

## 致谢

EraseDub 建立在以下项目的成果之上——它们是 EraseDub 依赖的库，或首次运行时会下载的模型：

- 去字：[STTN](https://github.com/researchmm/STTN)、[ProPainter](https://github.com/sczhou/ProPainter)、
  [LaMa](https://github.com/advimman/lama)。
- 语音与文字：[WhisperX](https://github.com/m-bain/whisperX)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、
  [RapidOCR](https://github.com/RapidAI/RapidOCR)（使用 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) 模型）、
  [deep-translator](https://github.com/nidhaloff/deep-translator)、[Ollama](https://github.com/ollama/ollama)。
- 配音：[edge-tts](https://github.com/rany2/edge-tts)。
- 视频与界面：[FFmpeg](https://ffmpeg.org/)（Docker 镜像和 Windows 便携包使用
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) 的构建）、
  [Gradio](https://github.com/gradio-app/gradio)、[Typer](https://github.com/fastapi/typer)、
  [Rich](https://github.com/Textualize/rich)、[Pydantic](https://github.com/pydantic/pydantic)、
  [Modal client](https://github.com/modal-labs/modal-client)。

“一个工具做完全部”的想法受到 [pyvideotrans](https://github.com/jianchang512/pyvideotrans)、
[VideoLingo](https://github.com/Huanshere/VideoLingo) 和
[video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) 的启发；EraseDub 没有复用它们的代码。

## 许可证

[Apache License 2.0](LICENSE)。第三方声明见 [NOTICE](NOTICE)。
