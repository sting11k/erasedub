"""Minimal inference code adapted from upstream model repositories (imported only when an eraser runs).

* :mod:`.sttn` - the STTN generator from https://github.com/researchmm/STTN at commit
  ``f39f62c5bbbe3e3eba084c487353a2c651bfdcde`` (``model/sttn.py``), MIT License, see ``LICENSE-STTN.txt``.
* :mod:`.lama` - the big-lama FFC generator from https://github.com/advimman/lama at commit
  ``786f5936b27fb3dacd2b1ad799e4de968ea697e7`` (``saicinpainting/training/modules/ffc.py``), Apache License
  2.0, see ``LICENSE-LAMA.txt``.

Only the layers needed to run the pretrained generators are kept; training code, discriminators and unused
options are left out. Module and parameter names follow upstream so the published checkpoints load
unchanged. Changes are listed at the top of each file.
"""
