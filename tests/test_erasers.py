"""The opt-in erasers (propainter, lama): registration, extras, notices and planning.

Three erasers, sttn the default; the others are opt-in, each with its own extra, none in
``full``; lama is the one that needs no NVIDIA GPU, and is suggested but never picked automatically.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from erasedub import config, links, registry
from erasedub.errors import ProviderUnavailableError
from erasedub.hardware import GpuInfo
from erasedub.links import missing_extra
from erasedub.pipeline import CPU_ERASER_HINT, AvailabilityCheck, build_plan, check_names, plan_video
from erasedub.providers import base as provider_base
from erasedub.providers import eraser_lama, eraser_propainter, eraser_sttn
from erasedub.providers.base import Availability, Kind, TextEraser

GPU = [GpuInfo(name="RTX 4090", memory_mib=24564, driver="580.1")]
NO_LOCAL_GPU = Availability(False, "no NVIDIA GPU found (nvidia-smi)")
PYPROJECT = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8"))
EXTRAS: dict[str, list[str]] = PYPROJECT["project"]["optional-dependencies"]
OPT_IN = {"propainter": "propainter", "lama": "lama"}  # name -> extra


def _avail(**overrides: Availability) -> AvailabilityCheck:
    def check(kind: Kind, name: str) -> Availability:
        registry.load_class(kind, name)
        return overrides.get(f"{kind}_{name}", Availability.ready())

    return check


def _cfg(**erase: Any) -> config.Config:
    return config.from_mapping({"erase": erase})


def _eraser(name: str) -> type[TextEraser]:
    cls = registry.load_class("eraser", name)
    assert issubclass(cls, TextEraser)
    return cls


# --- Registration and extras -----------------------------------------------------------------------------


def test_three_erasers_plus_none_are_registered() -> None:
    assert set(registry.names("eraser")) >= {"sttn", "propainter", "lama", "none"}
    assert not _eraser("sttn").supports_mps
    assert config.Config().erase.provider == "sttn"  # the default never changes to an opt-in eraser


@pytest.mark.parametrize(("name", "gpu"), [("propainter", True), ("lama", False)])
def test_opt_in_erasers_declare_their_extra_and_gpu_need(name: str, gpu: bool) -> None:
    cls = _eraser(name)
    assert cls.name == name
    assert cls.extra == OPT_IN[name]
    assert cls.requires_gpu is gpu
    assert cls.supports_mps is not gpu  # lama runs on an Apple GPU; propainter needs CUDA
    assert cls.notice


@pytest.mark.parametrize("name", sorted(OPT_IN))
def test_missing_packages_point_at_the_erasers_own_extra(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_base, "module_available", lambda module: False)
    status = registry.availability("eraser", name)
    assert not status.ok
    assert status.reason.endswith(missing_extra(OPT_IN[name]))


def test_opt_in_extras_exist_and_are_not_in_full() -> None:
    full = " ".join(EXTRAS["full"])
    for extra in OPT_IN.values():
        assert EXTRAS[extra], extra
        assert not re.search(rf"\b{extra}\b", full), extra


def test_opt_in_extras_stay_on_the_whisperx_torch_line() -> None:
    """Their torch floor must accept the torch 2.8 that whisperx pins, so one lock serves every extra."""
    for extra in OPT_IN.values():
        torch = [d for d in EXTRAS[extra] if re.match(r"torch\b(?!vision)", d)]
        assert torch == ["torch>=2.4"], extra


def test_all_extras_install_together() -> None:
    """No declared conflicts, so the universal lock (checked in CI) resolves every extra together."""
    assert "conflicts" not in PYPROJECT.get("tool", {}).get("uv", {})


@pytest.mark.parametrize("provider", ["sttn", "propainter", "lama", "none"])
def test_config_accepts_every_eraser(provider: str) -> None:
    cfg = config.from_mapping({"erase": {"provider": provider}})
    assert cfg.erase.provider == provider
    check_names(cfg)  # a known name: no ProviderNotFoundError


# --- Licences and pinned sources -------------------------------------------------------------------------


def test_propainter_licence_is_visible() -> None:
    cls = _eraser("propainter")
    assert "non-commercial (S-Lab License 1.0)" in cls.summary
    assert "S-Lab License 1.0" in cls.notice
    assert "non-commercial use only" in cls.notice
    assert links.doc("models-and-licenses.md") in cls.notice


def test_lama_names_its_weaknesses() -> None:
    cls = _eraser("lama")
    assert cls.summary == (
        "LaMa image inpainting - CPU or Apple GPU, per-frame: may flicker and flatten repeating patterns"
    )
    assert "the CPU or an Apple GPU" in cls.notice
    assert "may flicker" in cls.notice
    assert "repeating patterned backgrounds" in cls.notice
    assert links.doc("models-and-licenses.md") in cls.notice


def test_sources_are_pinned_to_full_commits() -> None:
    sha = re.compile(r"^[0-9a-f]{40}$")
    for commit in (
        eraser_propainter.SOURCE_COMMIT,
        eraser_lama.SOURCE_COMMIT,
        eraser_sttn.SOURCE_COMMIT,
    ):
        assert sha.match(commit), commit
    assert eraser_propainter.WEIGHTS_RELEASE == "v0.1.0"
    assert eraser_propainter.SOURCE_REPO == "https://github.com/sczhou/ProPainter"
    assert eraser_lama.SOURCE_REPO == "https://github.com/advimman/lama"
    assert eraser_sttn.SOURCE_REPO == "https://github.com/researchmm/STTN"


# --- Planning ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["propainter", "lama"])
def test_selected_eraser_adds_its_notice(name: str) -> None:
    plan = build_plan(_cfg(provider=name), GPU, _avail())
    assert plan.enabled("erase")
    assert plan.notices == (_eraser(name).notice,)


def test_default_eraser_adds_no_notice() -> None:
    assert build_plan(config.Config(), GPU, _avail()).notices == ()


@pytest.mark.parametrize("name", ["propainter", "lama"])
def test_no_eraser_notice_when_erasing_is_off(name: str) -> None:
    assert build_plan(_cfg(provider=name, enabled="off"), GPU, _avail()).notices == ()


@pytest.mark.parametrize("name", ["sttn", "propainter"])
def test_no_gpu_skips_a_gpu_eraser_and_suggests_lama(name: str) -> None:
    plan = build_plan(_cfg(provider=name), [], _avail(gpu_local=NO_LOCAL_GPU))
    assert not plan.enabled("erase")  # never switched to lama automatically
    skipped = plan.notices[0]
    assert skipped.startswith("Burned-in text will NOT be erased: no NVIDIA GPU found.")
    assert CPU_ERASER_HINT in skipped
    assert "may flicker and flatten repeating patterns" in skipped
    assert plan.notices[1:] == ((_eraser(name).notice,) if name != "sttn" else ())


def test_lama_is_not_suggested_for_other_missing_pieces() -> None:
    no_extra = Availability(False, "missing Python packages torch - " + missing_extra("propainter"))
    plan = build_plan(_cfg(provider="propainter"), GPU, _avail(eraser_propainter=no_extra))
    assert "lama" not in plan.notices[0]


@pytest.mark.parametrize("enabled", ["auto", "on"])
def test_lama_erases_without_an_nvidia_gpu(enabled: str) -> None:
    plan = build_plan(_cfg(provider="lama", enabled=enabled), [], _avail(gpu_local=NO_LOCAL_GPU))
    assert plan.enabled("erase")
    assert plan.step("erase").note == "on this machine's CPU or Apple GPU"
    assert plan.notices == (_eraser("lama").notice,)


def test_lama_uses_an_nvidia_gpu_when_there_is_one() -> None:
    plan = build_plan(_cfg(provider="lama"), GPU, _avail())
    assert plan.step("erase").note == "on local GPU RTX 4090"


def test_lama_still_needs_its_extra() -> None:
    no_extra = Availability(False, "missing Python packages torch - " + missing_extra("lama"))
    plan = build_plan(_cfg(provider="lama"), [], _avail(gpu_local=NO_LOCAL_GPU, eraser_lama=no_extra))
    assert not plan.enabled("erase")
    assert "uv sync --extra lama" in plan.notices[0]
    assert CPU_ERASER_HINT not in plan.notices[0]


def test_on_without_gpu_error_suggests_lama() -> None:
    with pytest.raises(ProviderUnavailableError, match=re.escape(CPU_ERASER_HINT)):
        build_plan(_cfg(enabled="on"), [], _avail(gpu_local=NO_LOCAL_GPU))


def _plan(tmp_path: Path, gpus: list[GpuInfo], **kwargs: Any) -> Any:
    cfg = config.from_mapping({"erase": {"provider": "lama"}, "general": {"workdir": str(tmp_path)}})
    return plan_video(cfg, tmp_path / "clip.mp4", "run", gpus, _avail(gpu_local=NO_LOCAL_GPU), **kwargs)


def test_picking_local_for_lama_without_a_gpu_is_fine(tmp_path: Path) -> None:
    request = _plan(tmp_path, [], gpu="local", labels={"erase.gpu": "--gpu"})
    assert request.config.erase.enabled == "on"
    assert request.plan.enabled("erase")


def test_lama_on_modal_is_allowed(tmp_path: Path) -> None:
    request = _plan(tmp_path, [], gpu="modal", labels={"erase.gpu": "--gpu"})
    assert request.plan.step("erase").note == "on remote GPU backend 'modal'"
    assert request.plan.notices == (_eraser("lama").notice,)


def test_picking_local_for_sttn_without_a_gpu_suggests_lama(tmp_path: Path) -> None:
    cfg = config.from_mapping({"general": {"workdir": str(tmp_path)}})
    with pytest.raises(ProviderUnavailableError, match=re.escape(CPU_ERASER_HINT)):
        plan_video(cfg, tmp_path / "clip.mp4", "run", [], _avail(gpu_local=NO_LOCAL_GPU), gpu="local")


def test_lama_missing_its_extra_is_not_told_to_rent_a_gpu() -> None:
    """A GPU fixes nothing for an eraser that needs none."""
    no_extra = Availability(False, "missing Python packages torch - " + missing_extra("lama"))
    avail = _avail(gpu_local=NO_LOCAL_GPU, eraser_lama=no_extra)
    notice = build_plan(_cfg(provider="lama"), [], avail).notices[0]
    assert notice.endswith("To erase, fix that.")
    assert "rent" not in notice
    with pytest.raises(ProviderUnavailableError) as error:
        build_plan(_cfg(provider="lama", enabled="on"), [], avail)
    assert "rent a GPU" not in str(error.value)
    assert "--gpu modal" not in str(error.value)
    assert "Fix that, or set erase.enabled = 'auto'" in str(error.value)


def test_local_backend_without_gpu_says_lama_does_not_need_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """`plugins` lists the local backend as not ready, but lama still runs through it."""
    from erasedub.providers import gpu_local

    monkeypatch.setattr(gpu_local, "detect_nvidia_gpus", lambda *a, **k: [])
    status = registry.availability("gpu", "local")
    assert not status.ok
    assert "only GPU erasers need one, lama runs without it" in status.reason
