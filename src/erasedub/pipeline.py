"""Turns a configuration plus the detected hardware into a concrete plan of steps.

This is where the product rule "no usable GPU backend -> skip erasing, everything else still runs" lives.
Building a plan never processes media. Provider availability is injected (``availability``), so the planner
stays pure and ``--dry-run`` and tests can use it freely; production code passes
:func:`erasedub.registry.availability`.

``erase.enabled``:

``auto``
    Erase when the GPU backend and the eraser are usable; otherwise skip erasing with one notice that names
    the missing piece (no GPU, a missing extra, a missing Modal token, an unknown backend, ...).
``on``
    Same checks, but a missing piece is an error: :class:`ProviderUnavailableError` (exit 3), or
    :class:`ProviderNotFoundError` (exit 2) for an unknown provider or backend name.
``off``
    Never erase and never detect on-screen text. ``erase.provider = "none"`` means the same.

An eraser that needs no NVIDIA GPU (``requires_gpu = False``, such as ``lama``) runs on this machine's CPU
or Apple GPU with the ``local`` backend. Erasing is never switched to it automatically: when
a GPU eraser is skipped (or refused) for lack of an NVIDIA GPU, the message suggests
``erase.provider = "lama"`` instead.
Whenever erasing is not off, the selected eraser's own ``notice`` (licence limits, known weaknesses) is
added to the plan.

On-screen text is detected in ``prepare`` whenever erasing is not turned off, even if this machine cannot
erase: OCR runs on the CPU, and ``render --gpu modal`` (or a rented GPU) can erase later from
``regions.json``. Erasing needs regions, so it is planned only when OCR is usable or ``regions.json``
already exists (``regions_ready``); otherwise ``auto`` skips it with the same single notice. When only
``render`` is planned (``command="render"``), OCR does not run, so only ``regions.json`` counts.

Front ends (the CLI, the web UI) plan a video with :func:`plan_video`, which also applies the user's explicit
erase choices, so both always plan the same inputs the same way:

- a GPU backend picked for this run (``--gpu``) asks for erasing: ``erase.enabled`` becomes ``on``, even over
  ``off`` in the config, and a backend that cannot run is an error; with ``erase.provider = "none"`` it is a
  :class:`ConfigError`, because the config names no eraser;
- turning erasing off for this run (``--no-erase``) wins over a picked backend;
- ``erase.gpu`` set only in the config keeps ``erase.enabled`` as configured (normally ``auto``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from erasedub import registry
from erasedub.config import Config
from erasedub.errors import ConfigError, ProviderNotFoundError, ProviderUnavailableError
from erasedub.hardware import GpuInfo
from erasedub.links import doc
from erasedub.providers.base import Availability, GpuBackend, Kind, Provider
from erasedub.regions import REGIONS_NAME, regions_path

#: ``(kind, name) -> Availability``; raises ``ProviderNotFoundError`` for an unknown name.
AvailabilityCheck = Callable[[Kind, str], Availability]
#: ``(kind, name) -> provider class``; raises ``ProviderNotFoundError`` for an unknown name.
ClassLoader = Callable[[Kind, str], type[Provider]]
#: The command being planned; ``run`` is ``prepare`` followed by ``render``.
Command = Literal["prepare", "render", "run"]

#: Why a GPU eraser cannot run on this machine.
NO_GPU = "no NVIDIA GPU found"
#: How to erase without an NVIDIA GPU, suggested whenever that is what is missing.
CPU_ERASER_HINT = (
    'set erase.provider = "lama" to erase on the CPU or an Apple GPU (slower; per-frame: may flicker and '
    "flatten repeating patterns)"
)


@dataclass(frozen=True)
class Step:
    name: str
    provider: str | None
    enabled: bool
    note: str = ""


@dataclass(frozen=True)
class Plan:
    prepare: tuple[Step, ...]
    render: tuple[Step, ...]
    notices: tuple[str, ...] = ()

    def step(self, name: str) -> Step:
        return next(s for s in self.prepare + self.render if s.name == name)

    def enabled(self, name: str) -> bool:
        return any(s.name == name and s.enabled for s in self.prepare + self.render)


def _erase_blocker(
    config: Config, gpus: Sequence[GpuInfo], availability: AvailabilityCheck, load_class: ClassLoader
) -> tuple[str | None, str, bool]:
    """``(what is missing or None, where erasing runs, whether erasing needs a GPU)``.

    Erasing needs no GPU only with an eraser that needs none (``requires_gpu = False``) on a backend that
    runs on this machine; then a GPU is no fix for anything that is missing.

    Raises ``ProviderNotFoundError`` for unknown names and ``ProviderUnavailableError`` for a plugin that
    fails to import or targets another plugin API version.
    """
    erase = config.erase
    backend = load_class("gpu", erase.gpu)
    remote = issubclass(backend, GpuBackend) and backend.remote
    eraser = load_class("eraser", erase.provider)
    needs_gpu = remote or eraser.requires_gpu
    if remote:
        where = f"remote GPU backend '{erase.gpu}'"
    elif gpus:
        where = f"local GPU {gpus[0].name}"
    elif eraser.requires_gpu:
        return NO_GPU, "this machine", needs_gpu
    else:
        where = "this machine's CPU or Apple GPU"
    # A backend on this machine only provides the NVIDIA GPU; an eraser that needs none runs without it.
    if needs_gpu:
        status = availability("gpu", erase.gpu)
        if not status.ok:
            return f"GPU backend '{erase.gpu}' is not ready: {status.reason}", where, needs_gpu
    if not remote:  # a remote backend builds the eraser on its own machine
        status = availability("eraser", erase.provider)
        if not status.ok:
            return f"eraser '{erase.provider}' is not ready: {status.reason}", where, needs_gpu
    return None, where, needs_gpu


def _erase_off(config: Config) -> bool:
    return config.erase.enabled == "off" or config.erase.provider == "none"


def _detect_step(
    config: Config, availability: AvailabilityCheck, *, regions_ready: bool, command: Command
) -> tuple[Step, str | None]:
    """``(the detect-text step, why erasing cannot get regions or None)``."""
    provider = config.ocr.provider
    if _erase_off(config):
        return Step("detect-text", None, False, "erasing is off"), None
    if command == "render":  # OCR is not part of render: only regions.json matters
        step = Step("detect-text", None, False, "runs in prepare")
        if regions_ready:
            return step, None
        return step, f"there is no {REGIONS_NAME} (run `erasedub prepare` with OCR installed first)"
    try:
        status = availability("ocr", provider)
    except ProviderNotFoundError:
        if config.erase.enabled == "on":
            raise
        status = Availability(False, f"no ocr provider named {provider!r}")
    except ProviderUnavailableError as exc:
        status = Availability(False, str(exc))
    if status.ok:
        return Step("detect-text", provider, True, "finds the text to erase (CPU)"), None
    if regions_ready:
        return Step("detect-text", None, False, "skipped: render uses the existing regions.json"), None
    return (
        Step("detect-text", None, False, f"skipped: {status.reason}"),
        f"on-screen text cannot be detected: OCR '{provider}' is not ready ({status.reason})",
    )


def _erase_step(
    config: Config,
    gpus: Sequence[GpuInfo],
    availability: AvailabilityCheck,
    load_class: ClassLoader,
    *,
    no_regions: str | None,
    gpu_choice: str | None,
    gpu_undo: str | None,
) -> tuple[Step, tuple[str, ...]]:
    erase = config.erase
    if _erase_off(config):
        return Step("erase", None, False, "turned off"), ()
    try:
        missing, where, needs_gpu = _erase_blocker(config, gpus, availability, load_class)
    except (ProviderNotFoundError, ProviderUnavailableError) as exc:  # unknown or broken plugin
        if erase.enabled == "on":
            raise
        missing, where, needs_gpu = str(exc), "", True
    eraser_notices = _eraser_notice(config, load_class)
    # Only the lack of an NVIDIA GPU can be solved with the CPU eraser; name it then.
    lama = f"{CPU_ERASER_HINT}, " if missing == NO_GPU else ""
    missing = "; ".join(m for m in (missing, no_regions) if m) or None
    if missing is None:
        return Step("erase", erase.provider, True, f"on {where}"), eraser_notices
    # A GPU only helps an eraser that needs one; never suggest the backend that just failed.
    rental_url = doc("gpu-rental.md")
    rental = f"rent a GPU ({rental_url}), " if needs_gpu else ""
    modal = "use `--gpu modal`, " if needs_gpu and erase.gpu != "modal" else ""
    if gpu_choice is not None:
        raise ProviderUnavailableError(
            f"{_picked(erase.gpu, gpu_choice)}, which asks for erasing, but {missing}. Fix that, {rental}"
            f"{lama}or {_undo(gpu_choice, gpu_undo)} to erase only when it can run."
        )
    if erase.enabled == "on":
        raise ProviderUnavailableError(
            f"erasing is set to 'on' but {missing}. Fix that, {modal}{rental}{lama}or set "
            "erase.enabled = 'auto' to skip erasing."
        )
    if not needs_gpu:
        to_erase = "fix that"
    else:
        to_erase = f"{'use `--gpu modal` or' if modal else 'fix that or use'} a rented GPU ({rental_url})"
        if lama:
            to_erase += f", or {CPU_ERASER_HINT}"
    notice = (
        f"Burned-in text will NOT be erased: {missing}. Translation, voice and subtitles still run. "
        f"To erase, {to_erase}."
    )
    return Step("erase", None, False, f"skipped: {missing}"), (notice, *eraser_notices)


def _eraser_notice(config: Config, load_class: ClassLoader) -> tuple[str, ...]:
    """The selected eraser's own notice (licence limits, known weaknesses), if it has one."""
    try:
        eraser = load_class("eraser", config.erase.provider)
    except (ProviderNotFoundError, ProviderUnavailableError):  # already reported by the erase checks
        return ()
    notice = getattr(eraser, "notice", "")
    return (notice,) if isinstance(notice, str) and notice else ()


def _picked(gpu: str, gpu_choice: str) -> str:
    return f"GPU backend '{gpu}' was picked with {gpu_choice}"


def _undo(gpu_choice: str, gpu_undo: str | None) -> str:
    """How to take a picked backend back: the front end's wording, or "leave out <option>"."""
    return gpu_undo or f"leave out {gpu_choice}"


def build_plan(
    config: Config,
    gpus: Sequence[GpuInfo],
    availability: AvailabilityCheck,
    *,
    load_class: ClassLoader = registry.load_class,
    regions_ready: bool = False,
    command: Command = "run",
    gpu_choice: str | None = None,
    gpu_undo: str | None = None,
) -> Plan:
    """Plan ``prepare`` and ``render`` for ``config`` on a machine with ``gpus``.

    ``availability`` answers "can provider (kind, name) run here?", normally :func:`registry.availability`.
    ``load_class`` resolves provider classes to read their ``remote`` / ``requires_gpu`` flags.
    ``regions_ready`` says that ``regions.json`` already exists (``render`` passes
    ``regions_path(workdir).exists()``), so erasing does not need OCR on this machine.
    ``command`` is the command being planned. Both phases are always in the plan, but for ``render`` OCR is
    not checked (detect-text "runs in prepare") and erasing depends on ``regions_ready`` alone, so no
    notice talks about OCR.
    ``gpu_choice`` names the option the user picked ``erase.gpu`` with for this run (``--gpu``), if they did;
    errors then point at that option instead of at ``erase.enabled``, and tell the user to ``gpu_undo``
    (default: "leave out <gpu_choice>"). See :func:`plan_video`.
    """
    detect_step, no_regions = _detect_step(config, availability, regions_ready=regions_ready, command=command)
    erase_step, notices = _erase_step(
        config,
        gpus,
        availability,
        load_class,
        no_regions=no_regions,
        gpu_choice=gpu_choice,
        gpu_undo=gpu_undo,
    )
    prepare = (
        Step("extract-audio", "ffmpeg", True),
        Step("transcribe", config.asr.provider, True),
        detect_step,
        Step("translate", config.translate.provider, True),
    )
    render = (
        erase_step,
        Step("speak", config.tts.provider, config.tts.enabled),
        Step("subtitles", config.subtitles.layout, config.subtitles.enabled),
        Step("mix-and-mux", "ffmpeg", True),
    )
    return Plan(prepare=prepare, render=render, notices=notices)


# --- One video, as the front ends ask for it -------------------------------------------------------------


def with_erase_choice(
    config: Config, *, gpu: str | None, no_erase: bool, label: str = "--gpu", undo: str | None = None
) -> Config:
    """``config`` with the user's erase choices for this run applied (see the module docs).

    ``gpu`` is a backend picked explicitly (None: use ``erase.gpu`` as configured); ``label`` names the
    option it was picked with and ``undo`` says how to take the pick back (default "leave out <label>"),
    for messages. Raises ``ConfigError`` for an empty ``gpu``, or when a picked
    backend asks for erasing but ``erase.provider = "none"``.
    """
    erase = config.erase
    if gpu is not None:
        gpu = gpu.strip()
        if not gpu:
            raise ConfigError(f"{label}: empty value; give a GPU backend such as local or modal")
        erase = erase.model_copy(update={"gpu": gpu})
    if no_erase:
        erase = erase.model_copy(update={"enabled": "off"})
    elif gpu is not None:
        if erase.provider == "none":
            raise ConfigError(
                f"{_picked(gpu, label)}, which asks for erasing, but erase.provider = 'none' names no "
                "eraser. Set erase.provider to an eraser (`erasedub plugins` lists them), or "
                f"{_undo(label, undo)}."
            )
        erase = erase.model_copy(update={"enabled": "on"})
    return config if erase is config.erase else config.model_copy(update={"erase": erase})


def used_providers(config: Config) -> list[tuple[str, Kind, str]]:
    """``(config field, kind, name)`` of every provider ``config`` can use."""
    used: list[tuple[str, Kind, str]] = [
        ("asr.provider", "asr", config.asr.provider),
        ("translate.provider", "translator", config.translate.provider),
    ]
    if config.erase.enabled != "off":
        used += [
            ("ocr.provider", "ocr", config.ocr.provider),
            ("erase.provider", "eraser", config.erase.provider),
            ("erase.gpu", "gpu", config.erase.gpu),
        ]
    if config.tts.enabled:
        used.append(("tts.provider", "tts", config.tts.provider))
    if config.subtitles.enabled:
        used.append(("subtitles.layout", "layout", config.subtitles.layout))
    return used


def check_names(config: Config, *, labels: Mapping[str, str] | None = None) -> None:
    """Unknown provider names are configuration errors (``ProviderNotFoundError``, exit 2), whatever the step.

    ``labels`` maps config fields to the option that can set them (``{"erase.gpu": "--gpu"}``).
    """
    for field, kind, name in used_providers(config):
        known = registry.names(kind)
        if name not in known:
            option = f" ({labels[field]})" if labels and field in labels else ""
            raise ProviderNotFoundError(
                f"{field}{option}: no {kind} provider named {name!r} "
                f"(installed: {', '.join(known) or 'none'}). `erasedub plugins` lists them; "
                "a third-party provider must be installed first."
            )


def work_folder(config: Config, video: Path) -> Path:
    """The folder holding one video's working files (scripts, ``regions.json``)."""
    return config.general.workdir / video.stem


def output_path(config: Config, video: Path, lang: str) -> Path:
    """The finished video for ``lang``: ``<output_dir or the video's folder>/<video stem>.<lang>.mp4``."""
    return (config.general.output_dir or video.parent) / f"{video.stem}.{lang}.mp4"


@dataclass(frozen=True)
class VideoPlan:
    """The plan for one video, with the configuration it was made for."""

    config: Config
    plan: Plan
    command: Command
    #: The video's work folder.
    work: Path

    @property
    def regions_file(self) -> Path:
        return regions_path(self.work)

    @property
    def regions_missing(self) -> bool:
        """``render`` erases but ``regions.json`` does not exist yet: render cannot start (CLI exit 2).

        Only ``render`` alone needs the file up front; ``run`` writes it in its prepare phase.
        """
        return self.command == "render" and self.plan.enabled("erase") and not self.regions_file.is_file()


def plan_video(
    config: Config,
    video: Path,
    command: Command,
    gpus: Sequence[GpuInfo],
    availability: AvailabilityCheck,
    *,
    gpu: str | None = None,
    no_erase: bool = False,
    labels: Mapping[str, str] | None = None,
    gpu_undo: str | None = None,
    load_class: ClassLoader = registry.load_class,
) -> VideoPlan:
    """Plan ``command`` for ``video``: the one entry point the CLI and the web UI both use.

    ``config`` is the loaded configuration with the other options of this run applied; ``gpu`` and
    ``no_erase`` are the explicit erase choices (:func:`with_erase_choice`). ``labels`` maps config fields to
    the options that set them, for messages (``{"erase.gpu": "--gpu"}``); ``gpu_undo`` is the front end's
    wording for taking a picked backend back (default "leave out <option>"; the web UI says "choose
    Default"). The rules stay here; front ends only supply words. ``regions.json`` is looked up in
    the video's work folder. Raises ``ConfigError`` / ``ProviderNotFoundError`` (exit 2) and
    ``ProviderUnavailableError`` (exit 3) like :func:`build_plan`.

    Under ``on``, ``render`` is planned as if ``regions.json`` existed: a GPU problem is still an error, and
    a missing file is reported with the other render inputs (:attr:`VideoPlan.regions_missing`).
    """
    gpu_label = (labels or {}).get("erase.gpu", "the GPU choice")
    config = with_erase_choice(config, gpu=gpu, no_erase=no_erase, label=gpu_label, undo=gpu_undo)
    check_names(config, labels=labels)
    work = work_folder(config, video)
    regions_ready = regions_path(work).exists() or (command == "render" and config.erase.enabled == "on")
    plan = build_plan(
        config,
        gpus,
        availability,
        load_class=load_class,
        regions_ready=regions_ready,
        command=command,
        gpu_choice=gpu_label if gpu is not None and not no_erase else None,
        gpu_undo=gpu_undo,
    )
    return VideoPlan(config=config, plan=plan, command=command, work=work)
