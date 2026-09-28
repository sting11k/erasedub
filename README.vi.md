<div align="center">

# EraseDub

**Xoá phụ đề cứng, dịch và lồng tiếng cho video — trong một công cụ, chạy ngay trên máy của bạn.**

[English](README.md) | [简体中文](README.zh-CN.md) | **Tiếng Việt**

[Tài liệu](docs/index.md) · [Cài đặt](docs/index.md#install) · [Lộ trình](docs/roadmap.md)

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

</div>

<p align="center">
  <img src="https://github.com/sting11k/erasedub/raw/main/docs/assets/demo-before-after.webp" alt="Trước và sau: xoá phụ đề tiếng Trung in cứng trong hình, thêm phụ đề tiếng Việt" width="720">
</p>
<p align="center"><sub>Trước / sau: xoá phụ đề tiếng Trung in cứng trong hình, thêm phụ đề tiếng Việt. Bản demo được làm bên ngoài EraseDub. <em>Tears of Steel</em> © Blender Foundation | mango.blender.org</sub></p>

EraseDub là công cụ mã nguồn mở để dịch video, lồng tiếng bằng AI và bản địa hoá video. Nó xoá phụ đề cứng
(hardsub, chữ in sẵn trong hình) bằng kỹ thuật video inpainting, chuyển giọng nói thành văn bản bằng Whisper
(speech-to-text), dịch phụ đề bằng Google Translate hoặc một LLM, rồi lồng tiếng bằng giọng đọc AI
(text-to-speech, TTS) và thêm phụ đề mới.

## Cách hoạt động

```text
video ──> xoá chữ in trong hình ──> nhận dạng giọng nói ──> dịch ──> lồng tiếng ──> thêm phụ đề ──> video.<lang>.mp4
```

Chạy một mạch (`erasedub run`), hoặc hai bước: `prepare` tạo file phụ đề sửa được, bạn sửa bản dịch bằng trình sửa
phụ đề bất kỳ, rồi `render` xuất video.

## Demo

So sánh trước / sau đầy đủ, đặt cạnh nhau: bên trái là bản gốc có phụ đề tiếng Trung in cứng, bên phải là cùng
khung hình đã xoá chữ (không thêm phụ đề mới, để bạn đánh giá chất lượng xoá). Bản demo được làm bên ngoài
EraseDub. Các bản so sánh này không có tiếng; video đầy đủ có giọng và phụ đề (tiếng Việt, tiếng Anh, tiếng Trung)
được đính kèm trong bản phát hành v0.1.

<p align="center">
  <img src="https://github.com/sting11k/erasedub/raw/main/docs/assets/compare-horizontal.webp" alt="So sánh trước và sau đầy đủ một cảnh trong Tears of Steel: bên trái có phụ đề tiếng Trung in cứng, bên phải là cùng khung hình đã xoá chữ" width="960">
</p>
<p align="center"><sub>Một cảnh trong <em>Tears of Steel</em> (19 giây) với phụ đề tiếng Trung do chúng tôi tự in vào. <em>Tears of Steel</em> © Blender Foundation | mango.blender.org</sub></p>

<p align="center">
  <img src="https://github.com/sting11k/erasedub/raw/main/docs/assets/compare-vertical.webp" alt="So sánh trước và sau đầy đủ một video sản phẩm quay dọc: bên trái có sẵn phụ đề tiếng Trung in cứng, bên phải là cùng khung hình đã xoá chữ" width="720">
</p>
<p align="center"><sub>Video sản phẩm quay dọc (15 giây) có sẵn phụ đề tiếng Trung in cứng.</sub></p>

## Vì sao có EraseDub

Muốn dịch một video có phụ đề đã "in chết" vào hình, bạn cần làm ba việc: xoá chữ cũ, dịch lời nói, rồi thêm giọng
đọc và phụ đề mới. Các công cụ mã nguồn mở hiện có mỗi cái làm tốt một phần:

- [pyvideotrans](https://github.com/jianchang512/pyvideotrans) và [VideoLingo](https://github.com/Huanshere/VideoLingo)
  nhận dạng, dịch và lồng tiếng, nhưng **không xoá phụ đề cứng**, nên chữ cũ vẫn nằm dưới phụ đề mới.
- [video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) xoá được phụ đề cứng, nhưng
  **không dịch, không lồng tiếng**.

EraseDub làm trọn cả luồng trong một lần chạy, trên máy bạn, mặc định dùng các dịch vụ miễn phí,
không cần API key.

## Tính năng

Công cụ dùng cho từng bước nằm ở mục [Provider](#provider).

- **Một công cụ cho cả việc:** xoá chữ, nhận dạng, dịch, lồng tiếng và thêm phụ đề trong một lần chạy.
- **Mặc định miễn phí, không cần API key:** chỉ cần key của bạn nếu muốn dùng bộ dịch hoặc giọng đọc trả phí.
- **Không có GPU vẫn dùng được:** bước xoá chữ được bỏ qua kèm thông báo rõ ràng, các bước khác vẫn chạy; hoặc xoá
  chữ trên [Modal](https://modal.com) bằng tài khoản của bạn (`--gpu modal`), hoặc tự chọn bộ xoá chạy CPU `lama`
  (chậm hơn, dễ nhấp nháy, làm phẳng nền có hoa văn lặp lại).
- **Hai bước:** `prepare` tạo file phụ đề sửa được cho từng ngôn ngữ (`script.vi.srt`); bạn sửa bằng trình sửa phụ đề bất kỳ rồi mới `render`.
- **Phụ đề thường** căn giữa phía dưới khung hình, có thể thêm nhạc nền và chuẩn hoá âm lượng.
- **Dòng lệnh và giao diện web đơn giản** (Gradio).
- **Plugin:** mỗi bước là một provider, gói bên thứ ba có thể thay thế ([docs/plugins.md](docs/plugins.md)).

## Provider

Mỗi bước là một provider bạn chọn trong `erasedub.toml`; `erasedub plugins` liệt kê các provider đã cài.

| Bước | Mặc định (miễn phí, không cần key) | Lựa chọn thêm | Cần gì |
|---|---|---|---|
| Xoá chữ in trong hình | `sttn` — STTN (video inpainting) | `propainter` — ProPainter, đẹp hơn ở cảnh khó, giấy phép **cấm dùng thương mại** · `lama` — LaMa, cho máy không có GPU NVIDIA, xử lý từng khung hình: dễ nhấp nháy, làm phẳng nền có hoa văn lặp lại · `none` — giữ nguyên hình | `sttn`, `propainter`: GPU NVIDIA. `lama`: CPU hoặc GPU Apple (MPS). Gói phụ `erase`, `propainter`, `lama` |
| Nơi chạy bước xoá chữ | `local` — GPU NVIDIA của máy này | `modal` — GPU đám mây [Modal](https://modal.com) (`--gpu modal`) | `modal`: gói phụ `modal` và tài khoản Modal của chính bạn, tính tiền vào tài khoản đó |
| Tìm chữ trên hình | `rapidocr` — RapidOCR | — | CPU. Gói phụ `ocr` |
| Nhận dạng giọng nói | `whisperx` — WhisperX | — | Gói phụ `asr`; nhanh hơn khi có GPU NVIDIA. Tuỳ chọn bật thêm: mốc thời gian từng từ (`align`, tắt theo mặc định vì giấy phép) và gắn nhãn người nói (`diarize`, cần `HF_TOKEN`) |
| Dịch | `google` — cổng web Google Translate | `openai` (hoặc API bất kỳ tương thích OpenAI) · `gemini` · `claude` · `ollama` — mô hình local, không cần key | `google`: cần mạng, không chính thức. `openai`/`gemini`/`claude`: gói phụ `llm` và API key của bạn. `ollama`: gói phụ `ollama` và máy chủ Ollama trên máy |
| Giọng đọc | `edge` — giọng Edge-TTS | `elevenlabs` — ElevenLabs | `edge`: cần mạng, không chính thức. `elevenlabs`: gói phụ `elevenlabs` và API key của bạn |
| Bố cục phụ đề | `bottom` — giữa phía dưới | — | ffmpeg có libass |

`erasedub[full]` cài `asr`, `ocr`, `erase`, `ollama` và `webui`: mọi thứ không cần key, trừ các bộ xoá tuỳ chọn.
Giấy phép: xem [Giấy phép của mô hình và dịch vụ](#giấy-phép-của-mô-hình-và-dịch-vụ).
Nên chọn bộ xoá nào: [docs/erasers.md](docs/erasers.md).

## Bắt đầu nhanh

**Windows: gói chạy liền** (tải từ bản phát hành trên GitHub). Giải nén rồi bấm đúp `EraseDub.exe`; không cần cài
Python, đã kèm PyTorch bản CUDA. Xem [hướng dẫn cài trên Windows](docs/install/windows.md).

**Từ mã nguồn** (cần [uv](https://docs.astral.sh/uv/) hoặc pip, Python 3.11–3.13 và ffmpeg có libass, libx264 —
xem [hướng dẫn cài](docs/index.md#install)):

```bash
git clone https://github.com/sting11k/erasedub
cd erasedub
uv sync --python 3.12 --extra full      # hoặc, trong một venv: pip install -e ".[full]"
source .venv/bin/activate               # Windows: .venv\Scripts\activate
erasedub doctor                         # kiểm tra Python, ffmpeg, GPU, các provider và key
```

Chạy một mạch:

```bash
erasedub run video.mp4 --to vi          # -> video.vi.mp4
```

Hoặc hai bước, để sửa bản dịch trước:

```bash
erasedub prepare video.mp4 --to vi      # nhận dạng + tìm chữ trên hình + dịch
                                        # -> work/video/script.vi.srt (+ script.vi.json, regions.json)
# sửa work/video/script.vi.srt bằng Notepad, Subtitle Edit... (không bắt buộc)
erasedub render video.mp4               # xoá chữ (GPU) + giọng đọc + phụ đề -> video.vi.mp4
```

**Giao diện web** (có sẵn trong `full`):

```bash
erasedub webui --open                   # chạy http://127.0.0.1:7860 và mở trong trình duyệt
```

**Docker** (Linux amd64): chạy image `ghcr.io/sting11k/erasedub` trên thư mục chứa video. Lệnh chạy, cách bật GPU và quyền ghi file có trong [hướng dẫn Docker](docs/install/docker.md).

Xem thêm [tham chiếu dòng lệnh](docs/cli.md) và [cấu hình](docs/configuration.md).

## Phần cứng

Xoá chữ in trong hình là bài toán video inpainting: bộ xoá mặc định cần GPU NVIDIA. Các bước còn lại chạy trên máy tính
bình thường.

| Máy của bạn | Xoá phụ đề cứng | Nhận dạng, dịch, giọng đọc, phụ đề |
|---|---|---|
| Linux + GPU NVIDIA | Có, chạy trên máy | Có |
| Windows + GPU NVIDIA | Có, chạy trên máy, sau khi cài **PyTorch bản CUDA** (xem dưới) | Có |
| Không có GPU NVIDIA (macOS, card đồ hoạ AMD hoặc Intel) | Mặc định bỏ qua, có thông báo. Hoặc tự chọn bộ xoá `lama`: chạy trên CPU hoặc GPU Apple (MPS); chậm hơn, xử lý từng khung hình (dễ nhấp nháy), làm phẳng nền có hoa văn lặp lại | Có (nhận dạng trên CPU chậm hơn) |
| Không có GPU NVIDIA + `--gpu modal` | Có, chạy trên [Modal](https://modal.com), tính tiền vào tài khoản Modal **của bạn** | Có, chạy trên máy |
| GPU thuê (vast.ai, RunPod...) | Có | Có |

<details>
<summary><b>Windows + NVIDIA khi cài từ mã nguồn: cài PyTorch bản CUDA</b></summary>

Cách được khuyên dùng là gói Windows chạy liền, đã kèm sẵn PyTorch phù hợp. Nếu cài từ mã nguồn: bản PyTorch trên
PyPI cho Windows chỉ chạy CPU. **Sau khi** `uv sync --python 3.12 --extra full`, bạn thay nó bằng bản CUDA 12.8
đúng phiên bản WhisperX yêu cầu (torch 2.8), từ kho chính thức của PyTorch:

```powershell
uv pip install --force-reinstall --no-deps "torch==2.8.0" "torchaudio==2.8.0" "torchvision==0.23.0" --index-url https://download.pytorch.org/whl/cu128
erasedub doctor
```

Chạy `uv sync` lần nữa sẽ đưa bản CPU trở lại; khi đó hãy cài lại bản CUDA như trên.

Nếu thiếu bước này, `erasedub doctor` sẽ báo torch đã cài "is a CPU-only build but an NVIDIA GPU is present".
Xem thêm [hướng dẫn cài trên Windows](docs/install/windows.md).

</details>

Bộ dịch và giọng đọc miễn phí mặc định (Google Translate, Edge-TTS) là dịch vụ trực tuyến nên cần có mạng.
Tham khảo: [thuê GPU](docs/gpu-rental.md) · [xử lý sự cố](docs/troubleshooting.md).

## Ngôn ngữ

Ngôn ngữ nguồn: bất kỳ ngôn ngữ nào WhisperX nhận dạng được.

| Ngôn ngữ đích | Trạng thái |
|---|---|
| Tiếng Việt (`vi`), tiếng Anh (`en`), tiếng Trung (`zh`) | **Đã kiểm**: bản dịch, giọng đọc và phụ đề được kiểm tra thủ công trước mỗi bản phát hành |
| Các ngôn ngữ khác mà bộ dịch và giọng TTS bạn chọn hỗ trợ | **Hỗ trợ**: không nằm trong bước kiểm tra trước khi phát hành |

Rất mong bạn góp ý và sửa lỗi cho các ngôn ngữ khác.

## Ghi chú

- **Xoá chữ bằng `sttn` hoặc `propainter` cần GPU NVIDIA**: trên máy bạn, trên máy thuê, hoặc trên Modal
  (`--gpu modal`, tính tiền vào tài khoản của bạn). Không có GPU thì bước xoá được bỏ qua kèm thông báo, hoặc bạn
  chọn `lama`.
- **`lama` chạy trên CPU hoặc GPU Apple (MPS).** Nó xử lý từng khung hình nên chậm hơn, dễ nhấp nháy và làm phẳng
  nền có hoa văn lặp lại.
- **Bộ dịch và giọng đọc miễn phí là dịch vụ không chính thức.** Google Translate và Edge-TTS được gọi qua cổng web
  công khai, không phải API cho lập trình viên; chúng có thể thay đổi hoặc bị giới hạn.
- **Mỗi ngôn ngữ đích dùng một giọng** (`tts.voice`) cho cả video. Nhãn người nói (tuỳ chọn, `diarize`) được ghi
  vào kịch bản nhưng không dùng để chọn giọng.
- **Chỉ amd64** cho Docker image và gói Windows.

## So sánh

So sánh EraseDub v0.1 với các dự án gần nhất (EraseDub lấy cảm hứng từ chúng nhưng không dùng lại mã của
chúng). Thông tin về các dự án khác lấy từ README và tài liệu của họ ngày 2026-09-26; ❌ = họ không cung cấp;
"?" = không nói rõ. Nếu có chỗ sai, mong bạn góp ý.

| | EraseDub v0.1 | [pyvideotrans](https://github.com/jianchang512/pyvideotrans) | [VideoLingo](https://github.com/Huanshere/VideoLingo) | [video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) |
|---|---|---|---|---|
| Xoá phụ đề cứng | ✅ STTN; tuỳ chọn ProPainter, LaMa | ❌ | ❌ | ✅ STTN, LaMa, ProPainter, OpenCV |
| Nhận dạng giọng nói | ✅ WhisperX | ✅ faster-whisper và nhiều API | ✅ WhisperX, API ElevenLabs | ❌ |
| Dịch | ✅ Google (miễn phí), LLM với key của bạn, Ollama | ✅ LLM, Google, Microsoft... | ✅ LLM tương thích OpenAI | ❌ |
| Lồng tiếng (TTS) | ✅ Edge-TTS (miễn phí), ElevenLabs | ✅ Edge-TTS và nhiều loại khác | ✅ Edge-TTS và nhiều loại khác | ❌ |
| Nhân bản giọng | ❌ (trong lộ trình) | ✅ F5-TTS, CosyVoice, GPT-SoVITS | ✅ GPT-SoVITS, CosyVoice2, F5-TTS | ❌ |
| Sửa kịch bản trước khi xuất video | ✅ `script.<lang>.srt` | ✅ dừng và soát ở từng bước | ? (dừng/chạy tiếp ở từng bước; không nói rõ có sửa được không) | — |
| Giao diện | dòng lệnh, web (Gradio) | GUI desktop, dòng lệnh, web (Gradio) | web (Streamlit), chế độ hàng loạt (beta) | GUI desktop, dòng lệnh |
| Phần cứng để xoá chữ | GPU NVIDIA, hoặc Modal bằng tài khoản của bạn; CPU hoặc GPU Apple với LaMa | — | — | NVIDIA, DirectML (AMD/Intel), CPU, Apple Silicon |
| Cách cài | từ mã nguồn (uv hoặc pip), Docker (amd64), gói Windows chạy liền | `.exe` cho Windows, mã nguồn, Dockerfile | mã nguồn, Dockerfile | gói Windows dựng sẵn, mã nguồn, Docker image |
| Giấy phép | Apache-2.0 | GPL-3.0 | Apache-2.0 | Apache-2.0 |
| Sao GitHub (2026-09-27) | mới | ~19,2k | ~18,5k | ~13,1k |

Các dự án này rất giỏi ở phần việc của chúng. Nếu bạn chỉ cần một bước, cứ dùng chúng.

## Giấy phép của mô hình và dịch vụ

Mã của EraseDub dùng giấy phép Apache-2.0. Các mô hình và dịch vụ mà nó dùng có điều khoản riêng:

- **Một bộ xoá tuỳ chọn cấm dùng thương mại.** ProPainter (S-Lab License 1.0) không được dùng cho mục đích thương
  mại. Nó chỉ bật khi bạn tự chọn (gói `propainter`, không nằm trong `full`), không bao giờ đi kèm EraseDub, và
  EraseDub hiện giấy phép mỗi khi nó được chọn. Hãy kiểm tra giấy phép của bộ xoá tuỳ chọn trước khi dùng thương mại.
  Cũng vì lý do đó, bước căn mốc từng từ của WhisperX **tắt theo mặc định**: một số mô hình căn mốc theo ngôn ngữ
  (ví dụ mô hình tiếng Việt) dùng giấy phép CC BY-NC 4.0.
- Edge-TTS và cổng Google Translate miễn phí là cách dùng không chính thức dịch vụ trực tuyến của Microsoft và Google,
  có thể thay đổi hoặc bị giới hạn bất cứ lúc nào. Bạn hãy tự kiểm tra điều khoản cho mục đích của mình.
- Docker image và gói Windows kèm bản ffmpeg giấy phép GPL (cần cho libx264), cùng giấy phép, phiên bản gốc chính
  xác và mã nguồn tương ứng, phát hành kèm mỗi bản.

Chi tiết và nguồn: [docs/models-and-licenses.md](docs/models-and-licenses.md). Bạn tự chịu trách nhiệm về quyền đối
với video mình xử lý.

## Tài liệu

- [Mục lục tài liệu](docs/index.md)
- Cài đặt: [Linux](docs/install/linux.md) · [macOS](docs/install/macos.md) · [Windows](docs/install/windows.md) · [Docker](docs/install/docker.md)
- [Tham chiếu dòng lệnh](docs/cli.md) · [Cấu hình](docs/configuration.md) · [Định dạng kịch bản](docs/script-format.md) · [Chọn bộ xoá chữ](docs/erasers.md)
- [Kiến trúc](docs/architecture.md) · [Viết plugin](docs/plugins.md)
- [Thuê GPU](docs/gpu-rental.md) · [Xử lý sự cố](docs/troubleshooting.md)
- [Mô hình và giấy phép](docs/models-and-licenses.md) · [Lộ trình](docs/roadmap.md)

(Tài liệu chi tiết hiện chỉ có tiếng Anh.)

## Cộng đồng và hỗ trợ

Mọi trao đổi đều trên GitHub:

- **Hỏi đáp, ý tưởng, khoe kết quả:** [GitHub Discussions](https://github.com/sting11k/erasedub/discussions).
- **Lỗi và sự cố cài đặt:** [GitHub Issues](https://github.com/sting11k/erasedub/issues), theo mẫu có sẵn.
  [SUPPORT.md](SUPPORT.md) ghi những gì cần gửi kèm (trước hết là kết quả của `erasedub doctor`).
- **Lỗi bảo mật:** đừng đăng trong issue công khai. Hãy báo riêng qua tính năng báo lỗ hổng riêng tư của GitHub,
  theo hướng dẫn trong [SECURITY.md](SECURITY.md).

## Đóng góp

Rất hoan nghênh đóng góp, nhất là báo lỗi, báo cáo cài đặt trên nhiều loại máy, và kiểm tra các ngôn ngữ ngoài
vi/en/zh. Bạn đọc [CONTRIBUTING.md](CONTRIBUTING.md) và [Quy tắc ứng xử](CODE_OF_CONDUCT.md) trước nhé.

## Lời cảm ơn

EraseDub dựa trên công sức của các dự án sau, là thư viện nó dùng hoặc mô hình nó tải về khi chạy lần đầu:

- Xoá chữ: [STTN](https://github.com/researchmm/STTN), [ProPainter](https://github.com/sczhou/ProPainter),
  [LaMa](https://github.com/advimman/lama).
- Giọng nói và chữ: [WhisperX](https://github.com/m-bain/whisperX), [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
  [RapidOCR](https://github.com/RapidAI/RapidOCR) với mô hình của [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR),
  [deep-translator](https://github.com/nidhaloff/deep-translator), [Ollama](https://github.com/ollama/ollama).
- Giọng đọc: [edge-tts](https://github.com/rany2/edge-tts).
- Video và giao diện: [FFmpeg](https://ffmpeg.org/) (Docker image và gói Windows dùng bản build của
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)),
  [Gradio](https://github.com/gradio-app/gradio), [Typer](https://github.com/fastapi/typer),
  [Rich](https://github.com/Textualize/rich), [Pydantic](https://github.com/pydantic/pydantic),
  [Modal client](https://github.com/modal-labs/modal-client).

Ý tưởng làm trọn cả việc trong một công cụ lấy cảm hứng từ [pyvideotrans](https://github.com/jianchang512/pyvideotrans),
[VideoLingo](https://github.com/Huanshere/VideoLingo) và
[video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover); EraseDub không dùng lại mã của chúng.

## Giấy phép

[Apache License 2.0](LICENSE). Thông báo của bên thứ ba xem ở [NOTICE](NOTICE).
