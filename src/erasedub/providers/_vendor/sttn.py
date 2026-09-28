"""STTN inpainting generator (inference only).

Adapted from ``model/sttn.py`` of https://github.com/researchmm/STTN at commit
``f39f62c5bbbe3e3eba084c487353a2c651bfdcde``. Copyright (c) the STTN authors; MIT License
(``LICENSE-STTN.txt``).

Changes from upstream: training-only parts (weight init, discriminator, spectral norm) removed; the
attention mask is dropped because upstream computes ``scores.masked_fill(...)`` without using the result, so
the pretrained model attends to every patch and this code does the same; the transformer blocks pass
``(x, b, c)`` instead of a dict. Layer names are unchanged, so the published ``sttn.pth`` loads as is.

Input frames are ``432*k x 240*m`` pixels (the feature map, a quarter of that, must split into the
``108 x 60`` patches of the coarsest attention head).
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F  # noqa: N812 — upstream spelling
from torch import nn

#: Attention patch sizes ``(width, height)`` on the quarter-resolution feature map, one per head.
PATCH_SIZES = ((108, 60), (36, 20), (18, 10), (9, 5))


class Deconv(nn.Module):
    def __init__(
        self, input_channel: int, output_channel: int, kernel_size: int = 3, padding: int = 0
    ) -> None:
        super().__init__()
        self.conv = nn.Conv2d(
            input_channel, output_channel, kernel_size=kernel_size, stride=1, padding=padding
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.interpolate(x, scale_factor=2, mode="bilinear", align_corners=True)
        return self.conv(x)


class MultiHeadedAttention(nn.Module):
    def __init__(self, patchsize: tuple[tuple[int, int], ...], d_model: int) -> None:
        super().__init__()
        self.patchsize = patchsize
        self.query_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.value_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.key_embedding = nn.Conv2d(d_model, d_model, kernel_size=1, padding=0)
        self.output_linear = nn.Sequential(
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=1), nn.LeakyReLU(0.2, inplace=True)
        )

    def forward(self, x: torch.Tensor, b: int, c: int) -> torch.Tensor:
        bt, _, h, w = x.size()
        t = bt // b
        d_k = c // len(self.patchsize)
        heads = len(self.patchsize)
        output = []
        queries = torch.chunk(self.query_embedding(x), heads, dim=1)
        keys = torch.chunk(self.key_embedding(x), heads, dim=1)
        values = torch.chunk(self.value_embedding(x), heads, dim=1)
        for (width, height), query, key, value in zip(self.patchsize, queries, keys, values, strict=True):
            out_w, out_h = w // width, h // height

            def patches(
                v: torch.Tensor,
                out_w: int = out_w,
                out_h: int = out_h,
                width: int = width,
                height: int = height,
            ) -> torch.Tensor:
                v = v.view(b, t, d_k, out_h, height, out_w, width)
                return (
                    v.permute(0, 1, 3, 5, 2, 4, 6)
                    .contiguous()
                    .view(b, t * out_h * out_w, d_k * height * width)
                )

            q, k, v = patches(query), patches(key), patches(value)
            scores = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(q.size(-1))
            y = torch.matmul(F.softmax(scores, dim=-1), v)
            y = y.view(b, t, out_h, out_w, d_k, height, width)
            y = y.permute(0, 1, 4, 2, 5, 3, 6).contiguous().view(bt, d_k, h, w)
            output.append(y)
        return self.output_linear(torch.cat(output, 1))


class FeedForward(nn.Module):
    def __init__(self, d_model: int) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=2, dilation=2),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(d_model, d_model, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x)


class TransformerBlock(nn.Module):
    def __init__(self, patchsize: tuple[tuple[int, int], ...], hidden: int = 128) -> None:
        super().__init__()
        self.attention = MultiHeadedAttention(patchsize, d_model=hidden)
        self.feed_forward = FeedForward(hidden)

    def forward(self, x: torch.Tensor, b: int, c: int) -> torch.Tensor:
        x = x + self.attention(x, b, c)
        return x + self.feed_forward(x)


class InpaintGenerator(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        channel = 256
        self.transformer = nn.Sequential(*(TransformerBlock(PATCH_SIZES, hidden=channel) for _ in range(8)))
        self.encoder = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=2, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 64, kernel_size=3, stride=1, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(128, channel, kernel_size=3, stride=1, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
        )
        self.decoder = nn.Sequential(
            Deconv(channel, 128, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(128, 64, kernel_size=3, stride=1, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            Deconv(64, 64, kernel_size=3, padding=1),
            nn.LeakyReLU(0.2, inplace=True),
            nn.Conv2d(64, 3, kernel_size=3, stride=1, padding=1),
        )

    def infer(self, feat: torch.Tensor) -> torch.Tensor:
        """Run the transformer over the encoded features of one clip (``t x c x h x w``)."""
        _, c, _, _ = feat.size()
        for block in self.transformer:
            feat = block(feat, 1, c)
        return feat
