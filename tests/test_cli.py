import importlib.machinery
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner, Result

import erasedub
from erasedub import cli, config, engine, hardware, pipeline, regions, script
from erasedub.cli import app
from erasedub.errors import CancelledError, EraseDubError
from erasedub.hardware import FfmpegInfo, GpuInfo
from erasedub.links import missing_extra
from erasedub.models import Box, TextRegion, VideoInfo
from erasedub.providers import asr_whisperx, base, gpu_local

runner = CliRunner()

GOOD_FFMPEG = FfmpegInfo("/usr/bin/ffmpeg", "7.1", True, True, True, "/usr/bin/ffprobe")
RTX = GpuInfo(name="NVIDIA GeForce RTX 4070", memory_mib=12282, driver="560.94")


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"")  # never decoded: tests that process it replace the engine (``engine_calls``)
    return path


@pytest.fixture
def engine_calls(monkeypatch: pytest.MonkeyPatch) -> list[pipeline.VideoPlan]:
    """Replace the engine: record each plan and report the files a real run would write."""
    calls: list[pipeline.VideoPlan] = []

    def execute(request: pipeline.VideoPlan, video: Path, **kwargs: object) -> engine.Result:
        calls.append(request)
        on_notice = kwargs["on_notice"]
        assert callable(on_notice)
        on_notice("a notice from the engine")
        langs = request.config.general.target_languages
        scripts = tuple(script.srt_path(request.work, lang) for lang in langs)
        outputs = tuple(pipeline.output_path(request.config, video, lang) for lang in langs)
        return engine.Result(
            scripts=scripts if request.command != "render" else (),
            outputs=outputs if request.command != "prepare" else (),
            notices=("a notice from the engine",),
        )

    monkeypatch.setattr(engine, "execute", execute)
    return calls


@pytest.fixture(autouse=True)
def machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A machine without a GPU, with a good ffmpeg, no API keys and no Modal token; wide output."""
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda *a, **k: [])
    monkeypatch.setattr(gpu_local, "detect_nvidia_gpus", lambda *a, **k: [])
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda *a, **k: None)
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: GOOD_FFMPEG)
    for key in cli.KNOWN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.modal.toml from the developer's machine
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # no stray ./erasedub.toml either
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=300))
    monkeypatch.setattr(cli, "err", Console(stderr=True, highlight=False, width=300))


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every optional extra counts as installed."""
    monkeypatch.setattr(base, "module_available", lambda name: True)


@pytest.fixture
def gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda *a, **k: [RTX])
    monkeypatch.setattr(gpu_local, "detect_nvidia_gpus", lambda *a, **k: [RTX])
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda *a, **k: "/usr/bin/nvidia-smi")


@pytest.fixture
def modal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODAL_TOKEN_ID", "id-for-tests")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "secret-for-tests")


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def row(output: str, step: str) -> list[str]:
    """The plan row of ``step``, split into cells: stage, step, provider, runs, ready, note words..."""
    for line in output.splitlines():
        cells = line.split()
        if len(cells) > 1 and cells[1] == step:
            return cells
    raise AssertionError(f"no plan row for {step!r} in:\n{output}")


def write_config(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def prepared(workdir: Path, *langs: str) -> None:
    for lang in langs:
        script.save(
            script.Script(target_language=lang, lines=(script.ScriptLine(start=0, end=1, text="hi"),)),
            workdir,
        )


def save_regions(workdir: Path) -> None:
    info = VideoInfo(path=Path("clip.mp4"), width=1080, height=1920, duration=10, fps=30)
    region = TextRegion(start=0, end=1, box=Box(x=0, y=0, width=10, height=10), text="x")
    regions.save_regions(regions.RegionsFile(video=info, regions=(region,)), workdir)


# ---------------------------------------------------------------- basics


def test_version_flag_and_command() -> None:
    for args in (["--version"], ["version"]):
        result = invoke(*args)
        assert result.exit_code == 0
        assert result.stdout.strip() == erasedub.__version__


def test_run_dry_run_without_gpu_skips_erasing(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--to", "vi,en", "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "erase")[3] == "no"
    assert "skipped: no NVIDIA GPU found" in result.output
    assert "notice: Burned-in text will NOT be erased" in result.output


def test_run_dry_run_with_gpu_erases(video: Path, installed: None, gpu: None) -> None:
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "erase")[:5] == ["render", "erase", "sttn", "yes", "yes"]
    assert "on local GPU NVIDIA GeForce RTX 4070" in result.output
    assert "notice:" not in result.output


@pytest.mark.parametrize(
    ("args", "step", "runs"),
    [
        (["--no-voice"], "speak", "no"),
        (["--no-subs"], "subtitles", "no"),
        (["--no-erase"], "erase", "no"),
        (["--no-erase"], "detect-text", "no"),
        ([], "detect-text", "yes"),  # text is detected whenever erasing is not off, even without a GPU
        (["--gpu", "modal"], "erase", "yes"),
    ],
)
def test_flags_change_the_plan(
    video: Path, installed: None, modal_token: None, args: list[str], step: str, runs: str
) -> None:
    result = invoke("run", str(video), *args, "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, step)[3] == runs


def test_path_and_language_options_show_in_the_plan(video: Path, installed: None, tmp_path: Path) -> None:
    music = tmp_path / "bed.mp3"
    music.write_bytes(b"")
    result = invoke(
        "run", str(video), "--from", "zh", "--to", "vi", "--workdir", "jobs", "--out", "done",
        "--music", str(music), "--dry-run",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert f"clip.mp4: zh -> vi   work dir: {Path('jobs/clip')}" in result.output
    assert f"{Path('jobs/clip/script.vi.srt')} -> {Path('done/clip.vi.mp4')}" in result.output
    assert f"music  {music}" in result.output


def test_output_defaults_to_the_video_folder(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--dry-run")
    assert f"-> {video.parent / 'clip.vi.mp4'}" in result.output


def test_targets_are_deduplicated_and_unverified_ones_flagged(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--to", "vi,VI,fr", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "-> vi, fr " in result.output
    assert "fr is outside the languages checked for each release" in result.output
    assert result.output.count("script.vi.srt") == 1


def test_prepare_takes_gpu_and_no_erase(video: Path, installed: None, modal_token: None) -> None:
    result = invoke("prepare", str(video), "--gpu", "modal", "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "detect-text")[3] == "yes"
    assert f"writes {Path('work/clip/script.vi.srt')} and script.vi.json" in result.output
    assert f"writes {Path('work/clip/regions.json')}" in result.output
    result = invoke("prepare", str(video), "--no-erase", "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "detect-text")[3] == "no"
    assert "regions.json" not in result.output


@pytest.mark.parametrize("command", ["prepare", "run"])
def test_processing_runs_the_engine_and_lists_what_it_wrote(
    video: Path, installed: None, engine_calls: list[pipeline.VideoPlan], command: str
) -> None:
    result = invoke(command, str(video), "--to", "vi,en")
    assert result.exit_code == 0, result.output
    assert [c.command for c in engine_calls] == [command]
    assert "notice: a notice from the engine" in result.output
    assert f"script: {Path('work/clip/script.en.srt')}" in result.output
    wrote_video = f"video: {video.parent / 'clip.vi.mp4'}" in result.output
    assert wrote_video == (command == "run")
    assert ("erasedub render" in result.output) == (command == "prepare")


def test_dry_run_never_calls_the_engine(
    video: Path, installed: None, engine_calls: list[pipeline.VideoPlan]
) -> None:
    assert invoke("run", str(video), "--dry-run").exit_code == 0
    assert engine_calls == []


@pytest.mark.parametrize(
    ("error", "code", "text"),
    [
        (EraseDubError("ffmpeg failed while writing clip.vi.mp4"), 1, "error: ffmpeg failed"),
        (CancelledError("cancelled by the user"), 130, "cancelled"),
        (KeyboardInterrupt(), 130, "cancelled"),
    ],
)
def test_engine_errors_exit_with_their_code(
    video: Path,
    installed: None,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    code: int,
    text: str,
) -> None:
    def execute(*args: object, **kwargs: object) -> engine.Result:
        raise error

    monkeypatch.setattr(engine, "execute", execute)
    result = invoke("run", str(video))
    assert result.exit_code == code, result.output
    assert text in result.output


# ---------------------------------------------------------------- setup validation


@pytest.mark.parametrize(
    ("toml", "field"),
    [
        ('[translate]\nprovider = "deepl"\n', "translate.provider"),
        ('[tts]\nprovider = "nope"\n', "tts.provider"),
        ('[erase]\nprovider = "no-such-eraser"\n', "erase.provider"),
        ('[subtitles]\nlayout = "fancy"\n', "subtitles.layout"),
        ('[asr]\nprovider = "other"\n', "asr.provider"),
        ('[ocr]\nprovider = "other"\n', "ocr.provider"),
    ],
)
def test_unknown_provider_names_exit_2(video: Path, installed: None, toml: str, field: str) -> None:
    write_config(Path(config.CONFIG_NAME), toml)
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 2, result.output
    assert field in result.output
    assert "erasedub plugins" in result.output


def test_unknown_gpu_backend_names_the_option(video: Path, installed: None) -> None:
    result = invoke("prepare", str(video), "--gpu", "cloud", "--dry-run")
    assert result.exit_code == 2
    assert "erase.gpu (--gpu): no gpu provider named 'cloud'" in result.output


def test_turned_off_steps_are_not_validated(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[tts]\nprovider = "nope"\n')
    result = invoke("run", str(video), "--no-voice", "--dry-run")
    assert result.exit_code == 0, result.output


def test_missing_extra_exits_3_with_the_fix(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: name != "whisperx")
    # On Python 3.14 the hint names the Python version instead of the extra.
    monkeypatch.setattr(asr_whisperx, "_unsupported_python", lambda: False)
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 3, result.output
    assert row(result.output, "transcribe")[3:5] == ["yes", "NO"]
    assert missing_extra("asr") in result.output
    assert "cannot run here: transcribe" in result.output


def test_missing_api_key_exits_3(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[translate]\nprovider = "openai"\n')
    result = invoke("prepare", str(video), "--dry-run")
    assert result.exit_code == 3, result.output
    assert "OPENAI_API_KEY" in result.output


def test_explicit_gpu_without_the_backend_extra_exits_3(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: name != "modal")
    for command in ("run", "prepare"):
        result = invoke(command, str(video), "--gpu", "modal", "--dry-run")
        assert result.exit_code == 3, result.output
        assert missing_extra("modal") in result.output
        text = " ".join(result.output.split())
        assert "GPU backend 'modal' was picked with --gpu, which asks for erasing" in text
        assert "leave out --gpu to erase only when it can run" in text
        # The user typed neither erase.enabled nor anything to fix with --gpu modal: the hint names neither.
        assert "use `--gpu modal`" not in text
        assert "erase.enabled" not in text


def test_explicit_gpu_error_does_not_suggest_auto(video: Path, installed: None) -> None:
    result = invoke("render", str(video), "--gpu", "local", "--dry-run")
    assert result.exit_code == 3
    text = " ".join(result.output.split())
    assert "'local' was picked with --gpu, which asks for erasing, but no NVIDIA GPU found" in text
    assert "erase.enabled = 'auto'" not in text


def test_erase_on_with_modal_in_the_config_does_not_suggest_gpu_modal(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[erase]\nenabled = "on"\ngpu = "modal"\n')
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 3
    text = " ".join(result.output.split())
    assert "erasing is set to 'on' but GPU backend 'modal' is not ready" in text
    assert "--gpu modal" not in text


def test_explicit_gpu_with_no_eraser_in_the_config_exits_2(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[erase]\nprovider = "none"\n')
    result = invoke("run", str(video), "--gpu", "modal", "--dry-run")
    assert result.exit_code == 2, result.output
    text = " ".join(result.output.split())
    assert "--gpu, which asks for erasing, but erase.provider = 'none' names no eraser" in text
    result = invoke("run", str(video), "--gpu", "modal", "--no-erase", "--dry-run")
    assert result.exit_code == 0, result.output  # --no-erase: nothing was asked for


def test_explicit_gpu_without_a_modal_token_exits_3(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--gpu", "modal", "--dry-run")
    assert result.exit_code == 3
    assert "GPU backend 'modal' is not ready: no Modal token" in result.output


def test_explicit_local_gpu_without_a_gpu_exits_3(video: Path, installed: None) -> None:
    result = invoke("prepare", str(video), "--gpu", "local", "--dry-run")
    assert result.exit_code == 3
    assert "no NVIDIA GPU found" in result.output


def test_gpu_backend_from_the_config_keeps_auto(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[erase]\ngpu = "modal"\n')
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "erase")[3] == "no"
    assert "GPU backend 'modal' is not ready: no Modal token" in result.output


def test_no_erase_wins_over_gpu(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--gpu", "modal", "--no-erase", "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "erase")[3] == "no"


def test_explicit_gpu_wins_over_erasing_off_in_the_config(
    video: Path, installed: None, modal_token: None
) -> None:
    write_config(Path(config.CONFIG_NAME), '[erase]\nenabled = "off"\n')
    result = invoke("run", str(video), "--gpu", "modal", "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "erase")[:4] == ["render", "erase", "sttn", "yes"]


def test_erase_on_without_gpu_exits_3(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[erase]\nenabled = "on"\n')
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 3
    assert "erasing is set to 'on' but no NVIDIA GPU found" in result.output


def test_bad_provider_options_exit_2(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), "[tts.options]\nbogus = 1\n")
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 2
    assert "options of tts provider 'edge'" in result.output


@pytest.mark.parametrize(
    ("ffmpeg", "step", "expected"),
    [
        (FfmpegInfo(None, None, False, False, False), "extract-audio", "ffmpeg not found"),
        (FfmpegInfo("/x/ffmpeg", "7", True, False, True, "/x/ffprobe"), "mix-and-mux", "libx264"),
        (FfmpegInfo("/x/ffmpeg", "7", True, True, False), "extract-audio", "ffprobe"),
    ],
)
def test_unusable_ffmpeg_exits_3(
    video: Path,
    installed: None,
    monkeypatch: pytest.MonkeyPatch,
    ffmpeg: FfmpegInfo,
    step: str,
    expected: str,
) -> None:
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: ffmpeg)
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 3, result.output
    assert row(result.output, step)[4] == "NO"
    assert expected in " ".join(row(result.output, step)[5:])


def test_ffmpeg_without_libass_is_fine_without_subtitles(
    video: Path, installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = FfmpegInfo("/x/ffmpeg", "7", False, True, True, "/x/ffprobe")
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: ffmpeg)
    assert invoke("run", str(video), "--no-subs", "--dry-run").exit_code == 0
    # With subtitles it still runs: they become a subtitle track instead of being burned in.
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "mix-and-mux")[4] == "yes"
    assert "no libass: subtitles become a subtitle track" in result.output


# ---------------------------------------------------------------- render inputs


def test_render_needs_each_language_script(video: Path, installed: None) -> None:
    prepared(Path("work/clip"), "vi")
    result = invoke("render", str(video), "--to", "vi,en", "--no-erase", "--dry-run")
    assert result.exit_code == 2, result.output
    assert f"reads {Path('work/clip/script.vi.srt')}" in result.output
    assert f"missing {Path('work/clip/script.en.json')}" in result.output
    assert "run `erasedub prepare --to en` first" in result.output


def test_render_with_scripts_plans_one_output_per_language(
    video: Path, installed: None, engine_calls: list[pipeline.VideoPlan]
) -> None:
    prepared(Path("work/clip"), "vi", "en")
    result = invoke("render", str(video), "--to", "vi,en", "--no-erase", "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"-> {video.parent / 'clip.en.mp4'}" in result.output
    result = invoke("render", str(video), "--to", "vi,en", "--no-erase")
    assert result.exit_code == 0, result.output
    assert f"video: {video.parent / 'clip.en.mp4'}" in result.output
    assert not engine_calls[0].plan.enabled("erase")


def test_render_defaults_to_the_configured_languages(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[general]\ntarget_languages = ["en"]\n')
    prepared(Path("work/clip"), "en")
    result = invoke("render", str(video), "--no-erase", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "clip.en.mp4" in result.output


def test_render_reports_a_broken_script_and_srt_warnings(video: Path, installed: None) -> None:
    work = Path("work/clip")
    prepared(work, "vi")
    script.srt_path(work, "vi").write_text("1\n00:00:02,000 --> 00:00:01,000\nbackwards\n", encoding="utf-8")
    result = invoke("render", str(video), "--no-erase", "--dry-run")
    assert result.exit_code == 2, result.output
    script.json_path(work, "vi").write_text("{", encoding="utf-8")
    result = invoke("render", str(video), "--no-erase", "--dry-run")
    assert result.exit_code == 2, result.output


def test_render_prints_script_warnings(video: Path, installed: None) -> None:
    work = Path("work/clip")
    prepared(work, "vi")
    script.srt_path(work, "vi").write_text(
        "1\n00:00:05,000 --> 00:00:06,000\nlater\n\n2\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8"
    )
    result = invoke("render", str(video), "--no-erase", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "warning:" in result.output


def test_render_without_regions_skips_erasing_under_auto(video: Path, installed: None, gpu: None) -> None:
    prepared(Path("work/clip"), "vi")
    result = invoke("render", str(video), "--dry-run")
    assert result.exit_code == 0, result.output
    assert row(result.output, "erase")[3] == "no"
    assert "there is no regions.json" in result.output


@pytest.mark.parametrize("how", ["config", "option"])
def test_render_that_must_erase_needs_regions(video: Path, installed: None, gpu: None, how: str) -> None:
    work = Path("work/clip")
    prepared(work, "vi")
    args = ["--gpu", "local"] if how == "option" else []
    if how == "config":
        write_config(Path(config.CONFIG_NAME), '[erase]\nenabled = "on"\n')
    result = invoke("render", str(video), *args, "--dry-run")
    assert result.exit_code == 2, result.output
    assert row(result.output, "erase")[3] == "yes"
    assert f"missing {Path('work/clip/regions.json')}" in result.output
    assert "Run `erasedub prepare` without --no-erase (install the ocr extra" in result.output
    assert "same --gpu" not in result.output
    save_regions(work)
    result = invoke("render", str(video), *args, "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"reads {Path('work/clip/regions.json')}" in result.output


def test_render_reports_unreadable_regions(video: Path, installed: None, gpu: None) -> None:
    work = Path("work/clip")
    prepared(work, "vi")
    regions.regions_path(work).write_text("{", encoding="utf-8")
    result = invoke("render", str(video), "--dry-run")
    assert result.exit_code == 2, result.output
    assert f"unreadable {Path('work/clip/regions.json')}" in result.output


def test_not_ready_steps_win_over_broken_render_inputs(
    video: Path, installed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = Path("work/clip")
    prepared(work, "vi", "en")
    script.json_path(work, "vi").write_text("{", encoding="utf-8")  # unreadable
    script.json_path(work, "en").unlink()  # missing
    ffmpeg = FfmpegInfo("/x/ffmpeg", "7", True, False, True, "/x/ffprobe")  # no libx264
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: ffmpeg)
    result = invoke("render", str(video), "--to", "vi,en", "--no-erase", "--dry-run")
    assert result.exit_code == 3, result.output  # every problem is printed, then "not ready" (3) wins
    assert f"unreadable {Path('work/clip/script.vi.json')}" in result.output  # the file that is broken
    assert f"missing {Path('work/clip/script.en.json')}" in result.output
    assert row(result.output, "mix-and-mux")[4] == "NO"
    error = " ".join(result.output.split("error:")[1].split())
    assert "cannot run here: mix-and-mux" in error
    assert "Also 2 render input(s) missing or unreadable, listed above." in error
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: GOOD_FFMPEG)
    result = invoke("render", str(video), "--to", "vi,en", "--no-erase", "--dry-run")
    assert result.exit_code == 2, result.output
    assert "script.vi.json" in result.output.split("error:")[1]
    assert "script.en.json" in result.output.split("error:")[1]


def test_broken_srt_is_shown_by_its_own_name(video: Path, installed: None) -> None:
    work = Path("work/clip")
    prepared(work, "vi")
    script.srt_path(work, "vi").write_text("not a subtitle file\n", encoding="utf-8")
    result = invoke("render", str(video), "--no-erase", "--dry-run")
    assert result.exit_code == 2, result.output
    assert f"unreadable {Path('work/clip/script.vi.srt')}" in result.output
    assert "script.vi.srt:1: text before the first timing line" in result.output


def test_not_ready_alone_does_not_mention_render_inputs(video: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: name != "whisperx")
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 3
    assert "render input" not in result.output


def test_music_from_the_config_must_exist(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[audio]\nmusic = "missing.mp3"\n')
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 2
    assert "audio.music: missing.mp3 is not a file" in result.output
    assert invoke("prepare", str(video), "--dry-run").exit_code == 0  # prepare does not mix music


# ---------------------------------------------------------------- options and config


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--to", "12"], "--to: Value error, not a language code"),
        (["--to", ""], "--to: empty value"),
        (["--to", " , "], "--to: empty value"),
        (["--from", ""], "--from: empty value"),
        (["--from", "12"], "--from: Value error, not a language code"),
        (["--gpu", ""], "--gpu:"),
    ],
)
def test_bad_option_values_name_the_option(video: Path, args: list[str], message: str) -> None:
    result = invoke("run", str(video), *args, "--dry-run")
    assert result.exit_code == 2
    assert message in result.output


def test_from_auto_means_detect(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--from", "auto", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "clip.mp4: auto -> vi" in result.output


@pytest.mark.parametrize("option", ["--workdir", "--out"])
def test_folder_options_reject_files(video: Path, option: str) -> None:
    result = invoke("run", str(video), option, str(video), "--dry-run")
    assert result.exit_code == 2


def test_missing_video_or_music_is_a_usage_error(video: Path) -> None:
    assert invoke("run", "missing.mp4", "--dry-run").exit_code == 2
    assert invoke("run", str(video), "--music", "missing.mp3", "--dry-run").exit_code == 2


def test_config_before_and_after_the_command(video: Path, installed: None) -> None:
    before = write_config(Path("before.toml"), '[general]\ntarget_languages = ["en"]\n')
    after = write_config(Path("after.toml"), '[general]\ntarget_languages = ["zh"]\n')
    result = invoke("-c", str(before), "run", str(video), "--dry-run")
    assert "-> en " in result.output
    result = invoke("run", str(video), "--dry-run", "--config", str(after))
    assert "-> zh " in result.output
    result = invoke("-c", str(before), "run", str(video), "--dry-run", "-c", str(after))
    assert result.exit_code == 0, result.output
    assert "-> zh " in result.output


def test_options_win_over_the_config_file(video: Path, installed: None) -> None:
    write_config(Path(config.CONFIG_NAME), '[general]\ntarget_languages = ["en"]\nworkdir = "w"\n')
    result = invoke("run", str(video), "--to", "zh", "--dry-run")
    assert "-> zh   work dir: w/clip" in result.output.replace("\\", "/")


def test_config_that_is_a_folder_exits_2(video: Path, tmp_path: Path) -> None:
    result = invoke("run", str(video), "-c", str(tmp_path), "--dry-run")
    assert result.exit_code == 2
    assert "is a folder" in result.output


def test_utf16_config_exits_2(video: Path) -> None:
    Path("u.toml").write_text('[general]\ntarget_languages = ["en"]\n', encoding="utf-16")
    result = invoke("run", str(video), "-c", "u.toml", "--dry-run")
    assert result.exit_code == 2
    assert "UTF-16" in result.output


def test_utf8_bom_config_is_read(video: Path, installed: None) -> None:
    Path("b.toml").write_text('[general]\ntarget_languages = ["en"]\n', encoding="utf-8-sig")
    result = invoke("run", str(video), "-c", "b.toml", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "-> en " in result.output


def test_secret_in_config_exits_2_without_echoing_it(video: Path) -> None:
    write_config(Path(config.CONFIG_NAME), '[translate.options]\napi_key = "sk-abc123-not-real"\n')
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 2
    assert "sk-abc123" not in result.output


# ---------------------------------------------------------------- output safety


def test_file_names_with_markup_are_printed_as_they_are(tmp_path: Path, installed: None) -> None:
    name = "clip [bold red]x [link=https:example.invalid]y [subbed].mp4"  # no "/" in POSIX file names
    video = tmp_path / name
    video.write_bytes(b"")
    result = invoke("run", str(video), "--workdir", "w[/]x", "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"{name}: auto -> vi" in result.output
    assert str(Path("w[/]x") / video.stem) in result.output


def test_cli_strings_are_ascii(video: Path, installed: None) -> None:
    result = invoke("run", str(video), "--dry-run")
    assert "→" not in result.output
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert source.isascii()


def _run_module(*args: str, encoding: str = "cp1252") -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "COLUMNS")}
    env["PYTHONIOENCODING"] = encoding
    return subprocess.run(  # noqa: S603 - our own interpreter and module
        [sys.executable, "-m", "erasedub", *args], capture_output=True, env=env, timeout=120, check=False
    )


@pytest.mark.parametrize(
    "args", [["--help"], ["run", "--help"], ["plugins"], ["doctor"], ["run", "--dry-run"]]
)
def test_legacy_windows_code_page_does_not_crash(tmp_path: Path, args: list[str]) -> None:
    if args == ["run", "--dry-run"]:
        video = tmp_path / "clip tiếng Việt → [x].mp4"  # not representable in cp1252
        video.write_bytes(b"")
        args = ["run", str(video), "--to", "vi,en", "--dry-run"]
    done = _run_module(*args)
    stderr = done.stderr.decode("cp1252", errors="replace")
    assert "Traceback" not in stderr
    assert "UnicodeEncodeError" not in stderr
    assert done.returncode in (0, 2, 3), stderr


def test_heavy_extras_are_not_imported() -> None:
    code = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from erasedub.cli import app\n"
        "for args in (['--help'], ['plugins'], ['run', '--help']):\n"
        "    CliRunner().invoke(app, args)\n"
        "heavy = ('torch', 'gradio', 'whisperx', 'modal', 'cv2', 'rapidocr', 'onnxruntime', 'openai')\n"
        "print(sorted(m for m in heavy if m in sys.modules))\n"
    )
    done = subprocess.run(  # noqa: S603 - our own interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_unexpected_tracebacks_never_show_locals() -> None:
    assert app.pretty_exceptions_show_locals is False


# ---------------------------------------------------------------- cancellation


@pytest.mark.parametrize("exc", [CancelledError("stopped by the user"), KeyboardInterrupt()])
def test_cancel_exits_130(
    video: Path, installed: None, monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    def cancelled(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(pipeline, "plan_video", cancelled)
    result = invoke("run", str(video), "--dry-run")
    assert result.exit_code == 130
    assert "cancelled" in result.output
    assert "error:" not in result.output


def test_main_exits_130_on_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "app", interrupted)
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 130


# ---------------------------------------------------------------- webui


@pytest.fixture
def fake_webui(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, str, int, bool]]:
    calls: list[tuple[object, str, int, bool]] = []
    module = types.ModuleType("erasedub.webui")

    def launch(cfg: config.Config, *, host: str, port: int, open_browser: bool) -> None:
        calls.append((cfg, host, port, open_browser))

    module.launch = launch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "erasedub.webui", module)
    return calls


def test_webui_launches_on_loopback_by_default(fake_webui: list[tuple[object, str, int, bool]]) -> None:
    result = invoke("webui")
    assert result.exit_code == 0, result.output
    [(cfg, host, port, open_browser)] = fake_webui
    assert isinstance(cfg, config.Config)
    assert (host, port, open_browser) == ("127.0.0.1", 7860, False)
    assert "warning" not in result.output


@pytest.mark.parametrize("host", ["localhost", "::1", "[::1]", "127.0.0.2"])
def test_webui_loopback_hosts_do_not_warn(fake_webui: list[tuple[object, str, int, bool]], host: str) -> None:
    result = invoke("webui", "--host", host)
    assert result.exit_code == 0
    assert "warning" not in result.output


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "my-laptop.local"])  # noqa: S104
def test_webui_warns_on_other_hosts(fake_webui: list[tuple[object, str, int, bool]], host: str) -> None:
    result = invoke("webui", "--host", host, "--port", "8080")
    assert result.exit_code == 0
    assert "no login" in result.stderr
    assert fake_webui[0][1:] == (host, 8080, False)


@pytest.mark.parametrize(("flag", "expected"), [("--open", True), ("--no-open", False)])
def test_webui_open_flag_is_passed_through(
    fake_webui: list[tuple[object, str, int, bool]], flag: str, expected: bool
) -> None:
    assert invoke("webui", flag).exit_code == 0
    assert fake_webui[0][3] is expected


@pytest.mark.parametrize("value", ["1", "true"])
def test_webui_in_container_prints_a_note_instead_of_the_warning(
    fake_webui: list[tuple[object, str, int, bool]], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ERASEDUB_IN_CONTAINER", value)
    result = invoke("webui", "--host", "0.0.0.0")  # noqa: S104
    assert result.exit_code == 0
    assert result.stderr.strip() == (
        "note: Running in a container: publish the port as -p 127.0.0.1:7860:7860 to keep it local."
    )
    assert "warning" not in result.output
    assert invoke("webui", "--host", "0.0.0.0", "--port", "8080").stderr.count("127.0.0.1:8080:8080") == 1  # noqa: S104


@pytest.mark.parametrize("value", [None, "", "0"])
def test_webui_outside_a_container_still_warns(
    fake_webui: list[tuple[object, str, int, bool]], monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("ERASEDUB_IN_CONTAINER", raising=False)
    else:
        monkeypatch.setenv("ERASEDUB_IN_CONTAINER", value)
    result = invoke("webui", "--host", "0.0.0.0")  # noqa: S104
    assert "no login" in result.stderr
    assert "container" not in result.stderr


def test_webui_in_container_on_loopback_prints_nothing(
    fake_webui: list[tuple[object, str, int, bool]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ERASEDUB_IN_CONTAINER", "1")
    assert invoke("webui").stderr == ""


def test_webui_uses_the_config_after_the_command(fake_webui: list[tuple[object, str, int, bool]]) -> None:
    write_config(Path("ui.toml"), '[general]\ntarget_languages = ["en"]\n')
    assert invoke("webui", "-c", "ui.toml").exit_code == 0
    cfg = fake_webui[0][0]
    assert isinstance(cfg, config.Config)
    assert cfg.general.target_languages == ["en"]


@pytest.mark.parametrize("port", ["0", "65536"])
def test_webui_rejects_bad_ports(fake_webui: list[tuple[object, str, int, bool]], port: str) -> None:
    assert invoke("webui", "--port", port).exit_code == 2
    assert fake_webui == []


def test_webui_not_in_this_build_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "erasedub.webui", None)  # makes the import fail
    result = invoke("webui")
    assert result.exit_code == 3
    assert "missing from this copy of EraseDub" in result.output
    assert "run `uv sync --extra webui`" in result.output


def _webui_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """A real ``erasedub.webui`` package whose import runs ``body``."""
    package = tmp_path / "extra" / "webui"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(body, encoding="utf-8")
    monkeypatch.setattr(
        erasedub, "__path__", [str(package.parent), *erasedub.__path__]
    )  # wins over a real one
    monkeypatch.delitem(sys.modules, "erasedub.webui", raising=False)


def test_webui_without_gradio_exits_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _webui_package(
        monkeypatch, tmp_path, "raise ModuleNotFoundError(\"No module named 'gradio'\", name='gradio')\n"
    )
    result = invoke("webui")
    assert result.exit_code == 3
    assert "the web UI needs Gradio - " + missing_extra("webui") in result.output
    monkeypatch.delitem(sys.modules, "erasedub.webui", raising=False)


def test_webui_bug_is_not_hidden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _webui_package(monkeypatch, tmp_path, "import erasedub_no_such_helper\n")
    result = invoke("webui")
    assert result.exit_code == 1
    assert isinstance(result.exception, ModuleNotFoundError)
    assert result.exception.name == "erasedub_no_such_helper"
    monkeypatch.delitem(sys.modules, "erasedub.webui", raising=False)


# ---------------------------------------------------------------- plugins and doctor


def test_plugins_lists_builtins_with_their_distribution() -> None:
    result = invoke("plugins")
    assert result.exit_code == 0
    for name in ("sttn", "whisperx", "rapidocr", "google", "edge", "bottom", "local", "modal"):
        assert name in result.stdout
    assert re.search(r"tts\s+edge\s+yes\s+erasedub", result.stdout)


def test_plugins_show_licence_and_weakness_even_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: False)
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=400))
    result = invoke("plugins")
    assert re.search(r"propainter\s+no\s+erasedub\s+.*non-commercial \(S-Lab License 1\.0\)", result.stdout)
    assert missing_extra("propainter") in result.stdout
    assert re.search(r"lama\s+no\s+erasedub\s+.*may flicker and flatten repeating patterns", result.stdout)


def doctor_row(output: str, name: str) -> str:
    for line in output.splitlines():
        if line[4:].startswith(name + " "):  # after the "ok  " / "!!  " / "--  " mark
            return line
    raise AssertionError(f"no doctor row {name!r} in:\n{output}")


def test_doctor_rows(tmp_path: Path) -> None:
    result = invoke("doctor")
    assert result.exit_code == 0, result.output
    names = [
        "erasedub",
        "python",
        "platform",
        "encoding",
        "config",
        "ffmpeg",
        "ffprobe",
        "nvidia-smi",
        "torch",
        "extras",
    ]
    for name in [*names, "nvidia gpu", "api keys"]:
        doctor_row(result.output, name)
    assert "/usr/bin/ffmpeg, version 7.1, libass=yes libx264=yes" in doctor_row(result.output, "ffmpeg")
    assert "not found" in doctor_row(result.output, "nvidia-smi")
    assert "defaults" in doctor_row(result.output, "config")
    assert "kind" in result.output  # the plugins table follows


def test_doctor_ffmpeg_without_libass_is_not_a_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    no_libass = FfmpegInfo("/x/ffmpeg", "7", False, True, True, "/x/ffprobe")
    monkeypatch.setattr(hardware, "detect_ffmpeg", lambda *a, **k: no_libass)
    row = doctor_row(invoke("doctor").output, "ffmpeg")
    assert row.startswith("ok")
    assert "libass=NO libx264=yes (subtitles are added as a subtitle track" in row


PROPAINTER_MODAL = '[erase]\nprovider = "propainter"\ngpu = "modal"\n'


@pytest.mark.parametrize(
    ("toml", "mark", "says"),
    [
        ("", "--", ["sttn (", "will NOT be erased: no NVIDIA GPU found", pipeline.CPU_ERASER_HINT]),
        ('[erase]\nenabled = "on"\n', "!!", ["erasing is set to 'on' but no NVIDIA GPU found"]),
        ('[erase]\nprovider = "propainter"\n', "--", ["non-commercial use only", pipeline.CPU_ERASER_HINT]),
        (
            '[erase]\nprovider = "lama"\n',
            "ok",
            ["may flicker and flatten repeating patterns", "the CPU or an Apple GPU"],
        ),
        (
            '[erase]\nprovider = "lama"\nenabled = "on"\n',
            "ok",
            ["erases on this machine's CPU or Apple GPU"],
        ),
        (PROPAINTER_MODAL, "--", ["GPU backend 'modal' is not ready", "non-commercial use only"]),
        (PROPAINTER_MODAL + 'enabled = "on"\n', "!!", ["GPU backend 'modal' is not ready"]),
        ('[erase]\nenabled = "off"\n', "--", ["erasing is off"]),
        ('[erase]\nprovider = "nope"\n', "!!", ["erase.provider: no eraser provider named 'nope'"]),
        ('[erase]\ngpu = "cloud"\n', "!!", ["erase.gpu: no gpu provider named 'cloud'"]),
        ('[erase]\nenabled = "maybe"\n', "--", ["see the config row"]),
    ],
)
def test_doctor_eraser_row_agrees_with_run_without_gpu(
    monkeypatch: pytest.MonkeyPatch, video: Path, toml: str, mark: str, says: list[str]
) -> None:
    """The doctor eraser row is planned like `run` (no Modal token here, so modal is not ready)."""
    monkeypatch.setattr(base, "module_available", lambda name: True)
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=1000))
    write_config(Path(config.CONFIG_NAME), toml)
    row = doctor_row(invoke("doctor").output, "eraser")
    assert row.startswith(mark), row
    for text in says:
        assert text in row, row
    if mark == "!!" and "provider named" not in says[0]:
        result = invoke("run", str(video), "--dry-run")
        assert result.exit_code == 3, result.output


def test_doctor_eraser_row_with_a_ready_remote_backend(
    monkeypatch: pytest.MonkeyPatch, modal_token: None
) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: True)
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=1000))
    write_config(Path(config.CONFIG_NAME), PROPAINTER_MODAL)
    row = doctor_row(invoke("doctor").output, "eraser")
    assert row.startswith("ok"), row
    assert "erases on remote GPU backend 'modal'" in row
    assert "non-commercial use only" in row


def test_doctor_eraser_row_without_the_modal_extra(
    monkeypatch: pytest.MonkeyPatch, modal_token: None
) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: name != "modal")
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=1000))
    write_config(Path(config.CONFIG_NAME), PROPAINTER_MODAL)
    row = doctor_row(invoke("doctor").output, "eraser")
    assert row.startswith("--"), row  # auto: skipped, like `run`
    assert missing_extra("modal") in row


@pytest.mark.parametrize(("enabled", "mark"), [("auto", "--"), ("on", "!!")])
def test_doctor_eraser_row_names_a_missing_extra(
    monkeypatch: pytest.MonkeyPatch, gpu: None, enabled: str, mark: str
) -> None:
    monkeypatch.setattr(base, "module_available", lambda name: False)
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=1000))
    write_config(Path(config.CONFIG_NAME), f'[erase]\nprovider = "propainter"\nenabled = "{enabled}"\n')
    row = doctor_row(invoke("doctor").output, "eraser")
    assert row.startswith(mark), row
    assert "eraser 'propainter' is not ready: missing Python packages" in row
    assert missing_extra("propainter") in row
    assert "S-Lab License 1.0" in row
    assert pipeline.CPU_ERASER_HINT not in row  # there is a GPU


def test_doctor_without_gpu_mentions_lama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "out", Console(highlight=False, width=1000))
    assert "an eraser that needs no NVIDIA GPU (lama)" in doctor_row(invoke("doctor").output, "nvidia gpu")


def test_doctor_shows_gpus_with_unknown_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = GpuInfo(name="NVIDIA A100 MIG 1g.10gb", memory_mib=None, driver="550.54")
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda *a, **k: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda *a, **k: [mig])
    result = invoke("doctor")
    assert "NVIDIA A100 MIG 1g.10gb, ? MiB, driver 550.54" in doctor_row(result.output, "nvidia gpu")


def test_doctor_nvidia_smi_without_gpu_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "find_nvidia_smi", lambda *a, **k: "/usr/bin/nvidia-smi")
    result = invoke("doctor")
    assert doctor_row(result.output, "nvidia-smi").startswith("!!")
    assert "reports no GPU" in result.output


def test_doctor_reports_an_invalid_config_and_still_exits_0() -> None:
    write_config(Path(config.CONFIG_NAME), '[erase]\nenabled = "maybe"\n')
    result = invoke("doctor")
    assert result.exit_code == 0
    assert doctor_row(result.output, "config").startswith("!!")


def test_doctor_exits_0_without_ffmpeg_and_1_on_old_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hardware, "detect_ffmpeg", lambda *a, **k: FfmpegInfo(None, None, False, False, False)
    )
    result = invoke("doctor")
    assert result.exit_code == 0
    assert doctor_row(result.output, "ffmpeg").startswith("!!")
    monkeypatch.setattr(hardware, "python_ok", lambda: False)
    result = invoke("doctor")
    assert result.exit_code == 1
    assert "needs Python 3.11 or newer" in result.output


def test_doctor_survives_a_crashing_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> FfmpegInfo:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(hardware, "detect_ffmpeg", broken)
    monkeypatch.setattr(hardware, "find_nvidia_smi", broken)
    result = invoke("doctor")
    assert result.exit_code == 0, result.output
    assert "check failed: RuntimeError: probe exploded" in doctor_row(result.output, "ffmpeg")
    assert "check failed" in doctor_row(result.output, "nvidia-smi")


def test_doctor_survives_a_broken_torch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / "site" / "torch"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(
        'raise OSError("[WinError 126] The specified module could not be found. Error loading c10.dll")\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(fake.parent))
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    result = invoke("doctor")
    assert result.exit_code == 0, result.output
    line = doctor_row(result.output, "torch")
    assert line.startswith("!!")
    assert "import failed: OSError: [WinError 126]" in line
    assert "kind" in result.output  # the rest of the report is still printed
    monkeypatch.delitem(sys.modules, "torch", raising=False)


def _fake_torch(monkeypatch: pytest.MonkeyPatch, *, cuda: str | None, available: bool) -> None:
    torch = types.ModuleType("torch")
    torch.__spec__ = importlib.machinery.ModuleSpec("torch", None)
    torch.__version__ = "2.8.0+cpu" if cuda is None else f"2.8.0+cu{cuda}"  # type: ignore[attr-defined]
    torch.version = types.SimpleNamespace(cuda=cuda)  # type: ignore[attr-defined]
    torch.cuda = types.SimpleNamespace(is_available=lambda: available)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)


def test_doctor_flags_cpu_only_torch_next_to_a_gpu(monkeypatch: pytest.MonkeyPatch, gpu: None) -> None:
    _fake_torch(monkeypatch, cuda=None, available=False)
    line = doctor_row(invoke("doctor").output, "torch")
    assert line.startswith("!!")
    assert "is a CPU-only build but an NVIDIA GPU is present" in line
    assert "troubleshooting.md#doctor-says-torch-is-a-cpu-only-build-typical-on-windows" in line


@pytest.mark.parametrize(
    ("cuda", "available", "has_gpu", "mark", "text"),
    [
        (None, False, False, "ok", "(CPU-only build)"),
        ("12.8", True, True, "ok", "CUDA 12.8"),
        ("12.8", False, True, "!!", "cannot use the GPU"),
        ("12.8", False, False, "ok", "no GPU here"),
    ],
)
def test_doctor_torch_states(
    monkeypatch: pytest.MonkeyPatch, cuda: str | None, available: bool, has_gpu: bool, mark: str, text: str
) -> None:
    _fake_torch(monkeypatch, cuda=cuda, available=available)
    if has_gpu:
        monkeypatch.setattr(hardware, "find_nvidia_smi", lambda *a, **k: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(hardware, "detect_nvidia_gpus", lambda *a, **k: [RTX])
    line = doctor_row(invoke("doctor").output, "torch")
    assert line.startswith(mark)
    assert text in line


def test_doctor_takes_the_config_after_the_command() -> None:
    write_config(Path("before.toml"), "")
    write_config(Path("after.toml"), "")
    result = invoke("-c", "before.toml", "doctor", "-c", "after.toml")
    assert result.exit_code == 0, result.output
    assert "after.toml (valid)" in doctor_row(result.output, "config")


def test_every_command_takes_the_config_after_it() -> None:
    """docs/cli.md: -c works before and after every command."""
    import typer.main

    commands = typer.main.get_command(app).commands  # type: ignore[attr-defined]
    assert {"prepare", "render", "run", "webui", "doctor", "plugins", "version"} <= set(commands)
    for name, command in commands.items():
        options = {opt for param in command.params for opt in getattr(param, "opts", [])}
        assert {"-c", "--config"} <= options, name
    write_config(Path("mine.toml"), "")
    assert invoke("plugins", "-c", "mine.toml").exit_code == 0
    result = invoke("version", "-c", "mine.toml")
    assert (result.exit_code, result.stdout.strip()) == (0, erasedub.__version__)


@pytest.mark.parametrize("where", ["cwd", "option"])
def test_doctor_never_prints_a_secret_from_the_config(where: str) -> None:
    named = "SENTINEL-CONFIG-NAMED-VALUE"
    shaped = "sk-" + "a1B2" * 12  # looks like an OpenAI key; built at runtime so scanners stay quiet
    text = f'[translate.options]\napi_key = "{named}"\nbase_url = "https://x.invalid/?k={shaped}"\n'
    path = write_config(Path(config.CONFIG_NAME if where == "cwd" else "mine.toml"), text)
    args = ["doctor"] if where == "cwd" else ["doctor", "-c", str(path)]
    result = invoke(*args)
    assert result.exit_code == 0
    line = doctor_row(result.output, "config")
    assert line.startswith("!!")
    assert "look like secrets" in line
    assert named not in result.output
    assert shaped not in result.output
    assert "a1B2a1B2" not in result.output


def test_doctor_never_prints_key_values(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinels = {key: f"SENTINEL{i}VALUE" for i, key in enumerate(cli.KNOWN_ENV_KEYS)}
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)
    result = invoke("doctor")
    assert result.exit_code == 0
    for key, value in sentinels.items():
        assert value not in result.output
        assert f"{key}=set" in result.output
