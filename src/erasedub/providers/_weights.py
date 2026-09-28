"""Load PyTorch checkpoints without running code from them.

``torch.load(weights_only=True)`` refuses checkpoints that pickle anything besides tensors and plain
containers, which rules out the big-lama checkpoint (a PyTorch Lightning file that also pickles its training
configuration). :func:`load_checkpoint` accepts those files too, but without importing the classes they
name: every global outside a small allow-list (tensor rebuild helpers, storages, dtypes, ``OrderedDict``)
is replaced by an inert placeholder, so the tensors load and nothing else from the file is executed.
"""

from __future__ import annotations

import pickle
import types
from pathlib import Path
from typing import IO, Any

from erasedub.errors import EraseDubError
from erasedub.providers._video import import_module

_ALLOWED: dict[str, frozenset[str]] = {
    "collections": frozenset({"OrderedDict"}),
    "torch._utils": frozenset(
        {"_rebuild_tensor", "_rebuild_tensor_v2", "_rebuild_parameter", "_rebuild_parameter_with_state"}
    ),
    "torch._tensor": frozenset({"_rebuild_from_type_v2"}),
    "torch.nn.parameter": frozenset({"Parameter"}),
}


class _Placeholder:
    """Stands in for any object a checkpoint pickles besides tensors (configs, callbacks, paths...)."""

    def __new__(cls, *args: object, **kwargs: object) -> _Placeholder:
        return super().__new__(cls)

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __setstate__(self, state: object) -> None:
        pass

    def __call__(self, *args: object, **kwargs: object) -> _Placeholder:
        return _Placeholder()


def _torch_name_allowed(name: str) -> bool:
    return name.endswith("Storage") or name in {"Size", "device"} or name in _DTYPES


_DTYPES = frozenset(
    [
        "float16",
        "float32",
        "float64",
        "bfloat16",
        "half",
        "float",
        "double",
        "uint8",
        "int8",
        "int16",
        "int32",
        "int64",
        "long",
        "int",
        "bool",
    ]
)


class _RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        # ``torch`` itself: storage classes and dtypes such as ``torch.FloatStorage`` or ``torch.float32``.
        if (module == "torch" and _torch_name_allowed(name)) or name in _ALLOWED.get(module, ()):
            return super().find_class(module, name)
        return _Placeholder


def _load(file: IO[bytes], **kwargs: Any) -> Any:
    return _RestrictedUnpickler(file, **kwargs).load()


#: A ``pickle_module`` for ``torch.load`` whose unpickler is :class:`_RestrictedUnpickler`.
RESTRICTED_PICKLE = types.ModuleType("erasedub_restricted_pickle")
setattr(RESTRICTED_PICKLE, "Unpickler", _RestrictedUnpickler)  # noqa: B010 — module built at runtime
setattr(RESTRICTED_PICKLE, "load", _load)  # noqa: B010


def load_checkpoint(path: Path) -> Any:
    """Load a checkpoint onto the CPU, keeping tensors and plain containers only."""
    torch = import_module("torch")
    try:
        return torch.load(str(path), map_location="cpu", weights_only=False, pickle_module=RESTRICTED_PICKLE)
    except (pickle.UnpicklingError, RuntimeError, EOFError, ValueError) as exc:
        raise EraseDubError(f"cannot read the model file {path}: {exc}") from exc


def state_dict(checkpoint: Any, *, key: str | None = None, prefix: str = "") -> dict[str, Any]:
    """Pick the weights out of a checkpoint: ``checkpoint[key]`` if given, only keys under ``prefix``
    (with the prefix removed), and without the ``module.`` prefix ``DataParallel`` adds."""
    weights = checkpoint[key] if key is not None else checkpoint
    if not isinstance(weights, dict):
        raise EraseDubError("the model file does not contain a state dict")
    out: dict[str, Any] = {}
    for name, value in weights.items():
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        name = name[len(prefix) :]
        out[name.removeprefix("module.")] = value
    if not out:
        raise EraseDubError(f"the model file has no weights under {prefix!r}")
    return out
