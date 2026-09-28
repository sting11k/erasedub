import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from erasedub import config, links, regions, registry
from erasedub.errors import ConfigError, ProviderNotFoundError, ProviderUnavailableError
from erasedub.hardware import GpuInfo
from erasedub.links import missing_extra
from erasedub.pipeline import (
    AvailabilityCheck,
    ClassLoader,
    Command,
    build_plan,
    check_names,
    plan_video,
    with_erase_choice,
)
from erasedub.providers.base import Availability, Kind, Provider
from erasedub.providers.gpu_local import LocalGpu

GPU = [GpuInfo(name="RTX 4090", memory_mib=24564, driver="580.1")]
NO_EXTRA = Availability(False, "missing Python packages torch, cv2 - " + missing_extra("erase"))
NO_MODAL = Availability(False, "no Modal token - run `modal token new`")
NO_OCR = Availability(False, "missing Python packages rapidocr - " + missing_extra("ocr"))


def _avail(**overrides: Availability) -> AvailabilityCheck:
    """All available except ``overrides`` (keyed ``kind_name``); unknown names raise like the registry."""

    def check(kind: Kind, name: str) -> Availability:
        registry.load_class(kind, name)  # ProviderNotFoundError for unknown names
        return overrides.get(f"{kind}_{name}", Availability.ready())

    return check


def _cfg(**erase: Any) -> config.Config:
    return config.from_mapping({"erase": erase})


def test_plan_with_local_gpu_erases_and_runs_ocr() -> None:
    plan = build_plan(config.Config(), GPU, _avail())
    assert plan.enabled("erase")
    assert plan.step("erase").note == "on local GPU RTX 4090"
    assert plan.enabled("detect-text")
    assert plan.notices == ()
    assert isinstance(plan.prepare, tuple)
    assert isinstance(plan.render, tuple)


def test_plan_without_gpu_skips_erase_but_keeps_the_rest() -> None:
    plan = build_plan(config.Config(), [], _avail(gpu_local=Availability(False, "no NVIDIA GPU found")))
    assert not plan.enabled("erase")
    for step in ("detect-text", "transcribe", "translate", "speak", "subtitles", "mix-and-mux"):
        assert plan.enabled(step), step
    assert len(plan.notices) == 1
    assert "no NVIDIA GPU found" in plan.notices[0]
    assert links.doc("gpu-rental.md") in plan.notices[0]


# (config, gpus, unavailable) -> (erase enabled?, piece named in the notice)
MATRIX: list[tuple[Mapping[str, Any], list[GpuInfo], Mapping[str, Availability], bool, str]] = [
    ({}, GPU, {}, True, ""),
    ({}, [], {}, False, "no NVIDIA GPU found"),
    ({}, GPU, {"eraser_sttn": NO_EXTRA}, False, "eraser 'sttn' is not ready: missing Python packages torch"),
    ({}, GPU, {"gpu_local": Availability(False, "driver gone")}, False, "GPU backend 'local' is not ready"),
    ({"gpu": "modal"}, [], {}, True, ""),
    ({"gpu": "modal"}, [], {"eraser_sttn": NO_EXTRA}, True, ""),  # remote: local torch not needed
    (
        {"gpu": "modal"},
        [],
        {"gpu_modal": NO_MODAL},
        False,
        "GPU backend 'modal' is not ready: no Modal token",
    ),
    (
        {"gpu": "modal"},
        [],
        {"gpu_modal": Availability(False, "missing Python packages modal - " + missing_extra("modal"))},
        False,
        "uv sync --extra modal",
    ),
    ({"gpu": "localgpu"}, GPU, {}, False, "no gpu provider named 'localgpu'"),
    ({"provider": "no-such-eraser"}, GPU, {}, False, "no eraser provider named 'no-such-eraser'"),
]


@pytest.mark.parametrize(("erase", "gpus", "unavailable", "erases", "missing"), MATRIX)
def test_auto_availability_matrix(
    erase: Mapping[str, Any],
    gpus: list[GpuInfo],
    unavailable: Mapping[str, Availability],
    erases: bool,
    missing: str,
) -> None:
    plan = build_plan(_cfg(**erase), gpus, _avail(**unavailable))
    assert plan.enabled("erase") is erases
    assert plan.enabled("detect-text")
    if erases:
        assert plan.notices == ()
    else:
        assert len(plan.notices) == 1
        assert missing in plan.notices[0]
        assert "will NOT be erased" in plan.notices[0]


@pytest.mark.parametrize(
    ("erase", "gpus", "unavailable", "erases", "missing"), [m for m in MATRIX if not m[3]]
)
def test_on_turns_a_missing_piece_into_an_error(
    erase: Mapping[str, Any],
    gpus: list[GpuInfo],
    unavailable: Mapping[str, Availability],
    erases: bool,
    missing: str,
) -> None:
    unknown_name = "provider named" in missing
    error: type[Exception] = ProviderNotFoundError if unknown_name else ProviderUnavailableError
    with pytest.raises(error, match=re.escape(missing)):
        build_plan(_cfg(enabled="on", **erase), gpus, _avail(**unavailable))


def test_prepare_without_gpu_then_render_on_modal() -> None:
    """Decision #12: a laptop without GPU prepares (OCR included), then erases with --gpu modal."""
    laptop = _avail(gpu_local=Availability(False, "no NVIDIA GPU found"), eraser_sttn=NO_EXTRA)
    prepare = build_plan(config.Config(), [], laptop)
    assert prepare.enabled("detect-text")  # regions.json is written even though this machine cannot erase
    assert not prepare.enabled("erase")

    render = build_plan(_cfg(gpu="modal"), [], laptop)
    assert render.enabled("erase")
    assert render.step("erase").note == "on remote GPU backend 'modal'"
    assert render.notices == ()


def test_third_party_local_backend_is_not_treated_as_remote() -> None:
    class RocmGpu(LocalGpu):
        name = "rocm"

    def load_class(kind: Kind, name: str) -> type[Provider]:
        return RocmGpu if (kind, name) == ("gpu", "rocm") else registry.load_class(kind, name)

    def avail(kind: Kind, name: str) -> Availability:
        return Availability.ready()

    plan = build_plan(_cfg(gpu="rocm"), [], avail, load_class=load_class)
    assert not plan.enabled("erase")  # STTN requires an NVIDIA GPU on a local backend
    assert "no NVIDIA GPU found" in plan.notices[0]


def test_erase_off_and_no_voice() -> None:
    cfg = config.from_mapping({"erase": {"enabled": "off"}, "tts": {"enabled": False}})
    plan = build_plan(cfg, GPU, _avail())
    assert not plan.enabled("erase")
    assert not plan.enabled("detect-text")
    assert not plan.enabled("speak")
    assert plan.notices == ()


@pytest.mark.parametrize("erase", [{"provider": "none"}, {"provider": "none", "enabled": "on"}])
def test_eraser_none_turns_erasing_and_ocr_off(erase: Mapping[str, Any]) -> None:
    plan = build_plan(_cfg(**erase), [], _avail(ocr_rapidocr=NO_OCR))
    assert plan.step("erase").note == "turned off"
    assert not plan.enabled("detect-text")  # nothing would use the regions
    assert plan.step("detect-text").note == "erasing is off"
    assert plan.notices == ()


def test_missing_ocr_skips_erasing_with_one_notice_under_auto() -> None:
    plan = build_plan(config.Config(), GPU, _avail(ocr_rapidocr=NO_OCR))
    assert not plan.enabled("detect-text")
    assert not plan.enabled("erase")  # no regions, so nothing to erase: the plan must not claim otherwise
    assert len(plan.notices) == 1
    assert "will NOT be erased" in plan.notices[0]
    assert "OCR 'rapidocr' is not ready" in plan.notices[0]
    assert "uv sync --extra ocr" in plan.notices[0]
    with pytest.raises(ProviderUnavailableError, match=r"OCR 'rapidocr' is not ready"):
        build_plan(_cfg(enabled="on"), GPU, _avail(ocr_rapidocr=NO_OCR))


def test_missing_ocr_and_gpu_are_named_in_the_same_notice() -> None:
    plan = build_plan(config.Config(), [], _avail(ocr_rapidocr=NO_OCR))
    assert len(plan.notices) == 1
    assert "no NVIDIA GPU found" in plan.notices[0]
    assert "OCR 'rapidocr' is not ready" in plan.notices[0]


@pytest.mark.parametrize("enabled", ["auto", "on"])
def test_existing_regions_let_render_erase_without_ocr(enabled: str) -> None:
    plan = build_plan(_cfg(enabled=enabled), GPU, _avail(ocr_rapidocr=NO_OCR), regions_ready=True)
    assert plan.enabled("erase")
    assert not plan.enabled("detect-text")
    assert plan.step("detect-text").note == "skipped: render uses the existing regions.json"
    assert plan.notices == ()


def test_existing_regions_do_not_stop_ocr_when_it_is_available() -> None:
    plan = build_plan(config.Config(), GPU, _avail(), regions_ready=True)
    assert plan.enabled("detect-text")
    assert plan.enabled("erase")


def test_render_alone_never_talks_about_ocr() -> None:
    """OCR runs in prepare only, so planning render must not warn that OCR is missing."""
    no_ocr = _avail(ocr_rapidocr=NO_OCR)
    render = build_plan(config.Config(), GPU, no_ocr, command="render", regions_ready=True)
    assert render.enabled("erase")
    assert render.step("detect-text").note == "runs in prepare"
    assert render.notices == ()

    missing = build_plan(config.Config(), GPU, no_ocr, command="render")
    assert not missing.enabled("erase")
    assert len(missing.notices) == 1
    assert "there is no regions.json" in missing.notices[0]
    assert "OCR" not in missing.notices[0].replace("with OCR installed", "")
    assert "not ready" not in missing.notices[0]


def test_render_alone_does_not_check_ocr() -> None:
    def avail(kind: Kind, name: str) -> Availability:
        assert kind != "ocr", "render must not ask for OCR"
        return Availability.ready()

    cfg = config.from_mapping({"ocr": {"provider": "tesseract"}, "erase": {"enabled": "on"}})
    assert build_plan(cfg, GPU, avail, command="render", regions_ready=True).enabled("erase")
    with pytest.raises(ProviderUnavailableError, match=r"there is no regions\.json"):
        build_plan(cfg, GPU, avail, command="render")


@pytest.mark.parametrize("command", ["prepare", "run"])
def test_commands_that_detect_text_keep_the_ocr_notice(command: Command) -> None:
    plan = build_plan(config.Config(), GPU, _avail(ocr_rapidocr=NO_OCR), command=command)
    assert len(plan.notices) == 1
    assert "OCR 'rapidocr' is not ready" in plan.notices[0]


def _broken(kind: Kind, name: str) -> ClassLoader:
    def load_class(k: Kind, n: str) -> type[Provider]:
        if (k, n) == (kind, name):
            raise ProviderUnavailableError(f"erasedub.{k}:{n} failed to import: ImportError: boom")
        return registry.load_class(k, n)

    return load_class


@pytest.mark.parametrize(("kind", "name"), [("eraser", "sttn"), ("gpu", "local")])
def test_broken_plugin_is_a_notice_under_auto_and_an_error_under_on(kind: Kind, name: str) -> None:
    plan = build_plan(config.Config(), GPU, _avail(), load_class=_broken(kind, name))
    assert not plan.enabled("erase")
    assert plan.enabled("detect-text")
    assert len(plan.notices) == 1
    assert "failed to import" in plan.notices[0]
    with pytest.raises(ProviderUnavailableError, match="failed to import"):
        build_plan(_cfg(enabled="on"), GPU, _avail(), load_class=_broken(kind, name))


def test_broken_ocr_plugin_is_reported_not_raised() -> None:
    def avail(kind: Kind, name: str) -> Availability:
        if kind == "ocr":
            raise ProviderUnavailableError("erasedub.ocr:rapidocr failed to import: OSError: boom")
        return Availability.ready()

    plan = build_plan(config.Config(), GPU, avail)
    assert not plan.enabled("erase")
    assert "failed to import" in plan.notices[0]


def test_unknown_ocr_provider() -> None:
    cfg = config.from_mapping({"ocr": {"provider": "tesseract"}})
    assert not build_plan(cfg, GPU, _avail()).enabled("detect-text")
    with pytest.raises(ProviderNotFoundError):
        build_plan(
            config.from_mapping({"ocr": {"provider": "tesseract"}, "erase": {"enabled": "on"}}), GPU, _avail()
        )


def test_default_checker_is_the_registry() -> None:
    # Smoke test with the real registry: never raises for built-in names, whatever is installed.
    plan = build_plan(config.Config(), [], registry.availability)
    assert plan.enabled("transcribe")


def test_notices_never_suggest_the_backend_that_failed() -> None:
    plan = build_plan(_cfg(gpu="modal"), [], _avail(gpu_modal=NO_MODAL))
    assert "--gpu modal" not in plan.notices[0]
    assert "To erase, fix that or use a rented GPU" in plan.notices[0]
    with pytest.raises(ProviderUnavailableError) as info:
        build_plan(_cfg(gpu="modal", enabled="on"), [], _avail(gpu_modal=NO_MODAL))
    assert "--gpu modal" not in str(info.value)
    local = build_plan(config.Config(), [], _avail())
    assert "To erase, use `--gpu modal` or a rented GPU" in local.notices[0]  # a good hint here


# --- plan_video: the explicit erase choices, shared by the CLI and the web UI -------------------------------


@pytest.mark.parametrize(
    ("erase", "gpu", "no_erase", "expected"),
    [
        ({}, None, False, ("auto", "local")),  # nothing picked: the config as it is
        ({"gpu": "modal"}, None, False, ("auto", "modal")),  # a backend in the config keeps auto
        ({}, "modal", False, ("on", "modal")),  # a picked backend asks for erasing
        ({"enabled": "off"}, "modal", False, ("on", "modal")),  # ... even over "off" in the config
        ({}, "modal", True, ("off", "modal")),  # turning erasing off wins
        ({"enabled": "on"}, None, True, ("off", "local")),
        ({}, " local ", False, ("on", "local")),
    ],
)
def test_with_erase_choice(
    erase: Mapping[str, Any], gpu: str | None, no_erase: bool, expected: tuple[str, str]
) -> None:
    cfg = with_erase_choice(_cfg(**erase), gpu=gpu, no_erase=no_erase)
    assert (cfg.erase.enabled, cfg.erase.gpu) == expected


def test_with_erase_choice_keeps_everything_else() -> None:
    base = config.from_mapping({"erase": {"options": {"x": 1}}, "tts": {"enabled": False}})
    assert with_erase_choice(base, gpu=None, no_erase=False) is base
    cfg = with_erase_choice(base, gpu="modal", no_erase=False)
    assert cfg.erase.options == {"x": 1}
    assert not cfg.tts.enabled
    assert base.erase.enabled == "auto"  # not changed in place


def test_picked_backend_needs_an_eraser() -> None:
    no_eraser = _cfg(provider="none")
    with pytest.raises(ConfigError, match=r"picked with --gpu, which asks for erasing, but erase\.provider"):
        with_erase_choice(no_eraser, gpu="modal", no_erase=False)
    assert with_erase_choice(no_eraser, gpu="modal", no_erase=True).erase.enabled == "off"
    assert with_erase_choice(no_eraser, gpu=None, no_erase=False).erase.enabled == "auto"


def test_empty_picked_backend_names_the_option() -> None:
    with pytest.raises(ConfigError, match=r"^GPU: empty value"):
        with_erase_choice(config.Config(), gpu=" ", no_erase=False, label="GPU")


def test_check_names_names_the_option() -> None:
    with pytest.raises(ProviderNotFoundError, match=r"^erase\.gpu \(--gpu\): no gpu provider named 'cloud'"):
        check_names(_cfg(gpu="cloud"), labels={"erase.gpu": "--gpu"})
    with pytest.raises(ProviderNotFoundError, match=r"^tts\.provider: no tts provider named 'x'"):
        check_names(config.from_mapping({"tts": {"provider": "x"}}))
    check_names(config.from_mapping({"tts": {"provider": "x", "enabled": False}}))  # not used, not checked
    check_names(_cfg(gpu="cloud", enabled="off"))


VIDEO = Path("clip.mp4")


@pytest.fixture
def workdir(tmp_path: Path) -> config.Config:
    return config.from_mapping({"general": {"workdir": str(tmp_path)}})


def _write_regions(cfg: config.Config) -> None:
    path = regions.regions_path(cfg.general.workdir / VIDEO.stem)
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")


def test_plan_video_looks_for_regions_in_the_video_work_folder(workdir: config.Config) -> None:
    request = plan_video(workdir, Path("videos") / VIDEO, "render", GPU, _avail())
    assert request.work == workdir.general.workdir / "clip"
    assert request.regions_file == request.work / "regions.json"
    assert not request.plan.enabled("erase")  # auto, no regions.json: skipped with a notice
    assert not request.regions_missing
    _write_regions(workdir)
    request = plan_video(workdir, VIDEO, "render", GPU, _avail())
    assert request.plan.enabled("erase")
    assert not request.regions_missing


@pytest.mark.parametrize(("command", "missing"), [("render", True), ("run", False), ("prepare", False)])
def test_plan_video_under_on_plans_render_and_reports_the_missing_file(
    workdir: config.Config, command: Command, missing: bool
) -> None:
    request = plan_video(workdir, VIDEO, command, GPU, _avail(), gpu="local")
    assert request.config.erase.enabled == "on"
    assert request.plan.enabled("erase")
    assert request.regions_missing is missing  # run and prepare write regions.json first


def test_plan_video_error_names_the_picked_option(workdir: config.Config) -> None:
    with pytest.raises(ProviderUnavailableError) as info:
        plan_video(workdir, VIDEO, "run", [], _avail(), gpu="local", labels={"erase.gpu": "--gpu"})
    text = str(info.value)
    assert text.startswith("GPU backend 'local' was picked with --gpu, which asks for erasing, but no NVIDIA")
    assert "leave out --gpu to erase only when it can run" in text
    assert "erase.enabled" not in text
    with pytest.raises(ProviderUnavailableError, match=r"picked with the GPU choice, which"):
        plan_video(workdir, VIDEO, "run", [], _avail(), gpu="local")  # no labels: a neutral name
    always = config.from_mapping(
        {"general": {"workdir": str(workdir.general.workdir)}, "erase": {"enabled": "on"}}
    )
    with pytest.raises(ProviderUnavailableError, match=r"^erasing is set to 'on' but no NVIDIA GPU found"):
        plan_video(always, VIDEO, "run", [], _avail())


def test_front_end_words_how_to_take_the_pick_back(workdir: config.Config) -> None:
    """The rule lives in core; the web UI only supplies its words.

    It says "choose Default", since a radio button cannot be "left out".
    """
    labels = {"erase.gpu": "the GPU option"}
    with pytest.raises(ProviderUnavailableError, match=r"or choose Default to erase only when it can run\.$"):
        plan_video(workdir, VIDEO, "run", [], _avail(), gpu="local", labels=labels, gpu_undo="choose Default")
    no_eraser = _cfg(provider="none")
    with pytest.raises(ConfigError, match=r"or choose Default\.$"):
        plan_video(
            no_eraser, VIDEO, "run", GPU, _avail(), gpu="modal", labels=labels, gpu_undo="choose Default"
        )
    with pytest.raises(ConfigError, match=r"or leave out --gpu\.$"):
        with_erase_choice(no_eraser, gpu="modal", no_erase=False)


def test_plan_video_no_erase_wins(workdir: config.Config) -> None:
    request = plan_video(workdir, VIDEO, "run", [], _avail(), gpu="local", no_erase=True)
    assert request.plan.step("erase").note == "turned off"
    assert request.plan.notices == ()


def test_plan_video_checks_provider_names(workdir: config.Config) -> None:
    with pytest.raises(ProviderNotFoundError, match=r"^erase\.gpu \(--gpu\)"):
        plan_video(workdir, VIDEO, "run", GPU, _avail(), gpu="cloud", labels={"erase.gpu": "--gpu"})
