"""big-lama generator (inference only): a ResNet of Fast Fourier Convolutions.

Adapted from ``saicinpainting/training/modules/ffc.py`` of https://github.com/advimman/lama at commit
``786f5936b27fb3dacd2b1ad799e4de968ea697e7``. Copyright (c) the LaMa authors; Apache License 2.0
(``LICENSE-LAMA.txt``).

Changes from upstream: only the configuration of the published big-lama checkpoint is kept (no squeeze-
excitation, spatial transform, local Fourier unit, gating, 3D FFT or spectral position encoding), so kornia
and the training package are not needed; the hyper-parameters are fixed in :func:`big_lama`. Module names
are unchanged, so the checkpoint's ``generator.*`` weights load as is.
"""

from __future__ import annotations

from typing import Any

import torch
from torch import nn


class FourierUnit(nn.Module):
    def __init__(self, in_channels: int, out_channels: int) -> None:
        super().__init__()
        self.conv_layer = nn.Conv2d(
            in_channels * 2, out_channels * 2, kernel_size=1, stride=1, padding=0, bias=False
        )
        self.bn = nn.BatchNorm2d(out_channels * 2)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch = x.shape[0]
        ffted = torch.fft.rfftn(x, dim=(-2, -1), norm="ortho")
        ffted = torch.stack((ffted.real, ffted.imag), dim=-1)
        ffted = ffted.permute(0, 1, 4, 2, 3).contiguous()
        ffted = ffted.view((batch, -1, *ffted.size()[3:]))
        ffted = self.relu(self.bn(self.conv_layer(ffted)))
        ffted = ffted.view((batch, -1, 2, *ffted.size()[2:])).permute(0, 1, 3, 4, 2).contiguous()
        ffted = torch.complex(ffted[..., 0], ffted[..., 1])
        return torch.fft.irfftn(ffted, s=x.shape[-2:], dim=(-2, -1), norm="ortho")


class SpectralTransform(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, stride: int = 1) -> None:
        super().__init__()
        self.downsample: nn.Module = (
            nn.AvgPool2d(kernel_size=(2, 2), stride=2) if stride == 2 else nn.Identity()
        )
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, out_channels // 2, kernel_size=1, bias=False),
            nn.BatchNorm2d(out_channels // 2),
            nn.ReLU(inplace=True),
        )
        self.fu = FourierUnit(out_channels // 2, out_channels // 2)
        self.conv2 = nn.Conv2d(out_channels // 2, out_channels, kernel_size=1, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv1(self.downsample(x))
        return self.conv2(x + self.fu(x))


Pair = tuple[Any, Any]


class FFC(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        ratio_gin: float,
        ratio_gout: float,
        stride: int = 1,
        padding: int = 0,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        in_cg = int(in_channels * ratio_gin)
        in_cl = in_channels - in_cg
        out_cg = int(out_channels * ratio_gout)
        out_cl = out_channels - out_cg
        self.ratio_gout = ratio_gout

        def conv(cin: int, cout: int) -> nn.Module:
            if cin == 0 or cout == 0:
                return nn.Identity()
            return nn.Conv2d(
                cin, cout, kernel_size, stride, padding, dilation, bias=False, padding_mode="reflect"
            )

        self.convl2l = conv(in_cl, out_cl)
        self.convl2g = conv(in_cl, out_cg)
        self.convg2l = conv(in_cg, out_cl)
        self.convg2g: nn.Module = (
            nn.Identity() if in_cg == 0 or out_cg == 0 else SpectralTransform(in_cg, out_cg, stride)
        )

    def forward(self, x: Any) -> Pair:
        x_l, x_g = x if isinstance(x, tuple) else (x, 0)
        out_xl: Any = 0
        out_xg: Any = 0
        if self.ratio_gout != 1:
            out_xl = self.convl2l(x_l) + self.convg2l(x_g)
        if self.ratio_gout != 0:
            out_xg = self.convl2g(x_l) + self.convg2g(x_g)
        return out_xl, out_xg


class FFCBnAct(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        ratio_gin: float,
        ratio_gout: float,
        stride: int = 1,
        padding: int = 0,
        dilation: int = 1,
    ) -> None:
        super().__init__()
        self.ffc = FFC(
            in_channels, out_channels, kernel_size, ratio_gin, ratio_gout, stride, padding, dilation
        )
        global_channels = int(out_channels * ratio_gout)
        self.bn_l: nn.Module = (
            nn.Identity() if ratio_gout == 1 else nn.BatchNorm2d(out_channels - global_channels)
        )
        self.bn_g: nn.Module = nn.Identity() if ratio_gout == 0 else nn.BatchNorm2d(global_channels)
        self.act_l: nn.Module = nn.Identity() if ratio_gout == 1 else nn.ReLU(inplace=True)
        self.act_g: nn.Module = nn.Identity() if ratio_gout == 0 else nn.ReLU(inplace=True)

    def forward(self, x: Any) -> Pair:
        x_l, x_g = self.ffc(x)
        return self.act_l(self.bn_l(x_l)), self.act_g(self.bn_g(x_g))


class FFCResnetBlock(nn.Module):
    def __init__(self, dim: int, ratio: float) -> None:
        super().__init__()
        self.conv1 = FFCBnAct(dim, dim, kernel_size=3, ratio_gin=ratio, ratio_gout=ratio, padding=1)
        self.conv2 = FFCBnAct(dim, dim, kernel_size=3, ratio_gin=ratio, ratio_gout=ratio, padding=1)

    def forward(self, x: Pair) -> Pair:
        id_l, id_g = x
        x_l, x_g = self.conv2(self.conv1(x))
        return id_l + x_l, id_g + x_g


class ConcatTupleLayer(nn.Module):
    def forward(self, x: Pair) -> torch.Tensor:
        x_l, x_g = x
        if not torch.is_tensor(x_g):
            return x_l
        return torch.cat(x, dim=1)


class FFCResNetGenerator(nn.Module):
    """Upstream ``FFCResNetGenerator``: ``init``/``downsample`` ratios 0, the given ``resnet`` ratio."""

    def __init__(
        self,
        input_nc: int = 4,
        output_nc: int = 3,
        ngf: int = 64,
        n_downsampling: int = 3,
        n_blocks: int = 18,
        resnet_ratio: float = 0.75,
        max_features: int = 1024,
    ) -> None:
        super().__init__()
        model: list[nn.Module] = [
            nn.ReflectionPad2d(3),
            FFCBnAct(input_nc, ngf, kernel_size=7, ratio_gin=0, ratio_gout=0),
        ]
        for i in range(n_downsampling):
            mult = 2**i
            ratio_gout = resnet_ratio if i == n_downsampling - 1 else 0
            model.append(
                FFCBnAct(
                    min(max_features, ngf * mult),
                    min(max_features, ngf * mult * 2),
                    kernel_size=3,
                    ratio_gin=0,
                    ratio_gout=ratio_gout,
                    stride=2,
                    padding=1,
                )
            )
        features = min(max_features, ngf * 2**n_downsampling)
        model += [FFCResnetBlock(features, resnet_ratio) for _ in range(n_blocks)]
        model.append(ConcatTupleLayer())
        for i in range(n_downsampling):
            mult = 2 ** (n_downsampling - i)
            channels = min(max_features, int(ngf * mult / 2))
            model += [
                nn.ConvTranspose2d(
                    min(max_features, ngf * mult),
                    channels,
                    kernel_size=3,
                    stride=2,
                    padding=1,
                    output_padding=1,
                ),
                nn.BatchNorm2d(channels),
                nn.ReLU(True),
            ]
        model += [nn.ReflectionPad2d(3), nn.Conv2d(ngf, output_nc, kernel_size=7, padding=0), nn.Sigmoid()]
        self.model = nn.Sequential(*model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)


def big_lama() -> FFCResNetGenerator:
    """The generator configuration of the published big-lama checkpoint."""
    return FFCResNetGenerator(
        input_nc=4, output_nc=3, ngf=64, n_downsampling=3, n_blocks=18, resnet_ratio=0.75
    )
