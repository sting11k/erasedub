# 0010 — GPL ffmpeg in the Docker image and the Windows bundle

- Status: Accepted
- Date: 2026-09-26

## Context

Burning subtitles needs libass and producing H.264 needs an encoder. The static
[BtbN FFmpeg builds](https://github.com/BtbN/FFmpeg-Builds) include libass; their `gpl` variants include
libx264 and are therefore GPL. EraseDub itself only calls ffmpeg as a separate program (mere aggregation),
but distributing the binary inside the Docker image or the Windows bundle carries GPL obligations.

## Decision

- Use the pinned BtbN `gpl` build (checksum-verified) in the Docker image and the Windows bundle.
- Ship the ffmpeg licence text, the exact upstream FFmpeg revision and the BtbN build-script commit with each
  distribution, and publish the corresponding source (a mirrored source tarball as a release asset) with
  every release that contains the binary. This is on the [release checklist](../dev/release-checklist.md).
- pip installs never download ffmpeg; users install it themselves.

## Alternatives considered

- **LGPL build + OpenH264 (Cisco)**: lighter obligations, but OpenH264 quality/speed is lower than x264.
- **Rely on the user's system ffmpeg everywhere**: many system builds lack libass (e.g. Homebrew's default).

## Consequences

Each release that ships binaries must include the source mirror; forgetting it is a licence violation.
