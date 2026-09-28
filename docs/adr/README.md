# Architecture decision records

Short records of decisions that shape EraseDub, so contributors can see *why* things are the way they
are before proposing a change. Format: [MADR](https://adr.github.io/madr/)-style, one file per decision,
numbered in order. A decision is changed by adding a new record that supersedes the old one. Before the first
release, records may still be edited in place.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-providers-as-entry-point-plugins.md) | Every pipeline step is a provider plugin discovered through entry points | Accepted |
| [0003](0003-two-step-prepare-render.md) | Two steps, `prepare` then `render`, with an editable SRT in between | Accepted |
| [0004](0004-no-gpu-means-no-erasing.md) | Without an NVIDIA GPU, skip erasing by default and run everything else | Accepted |
| [0005](0005-dependency-and-supply-chain-policy.md) | Dependency and supply-chain policy | Accepted |
| [0006](0006-non-commercial-models-are-opt-in.md) | Models with non-commercial licences are opt-in and never bundled | Accepted |
| [0007](0007-python-3-11-minimum.md) | Python 3.11 is the minimum | Accepted |
| [0008](0008-whisperx-alignment-off-by-default.md) | WhisperX forced alignment is off by default | Accepted |
| [0009](0009-apache-2-0-license.md) | License: Apache-2.0 | Accepted |
| [0010](0010-gpl-ffmpeg-in-binary-distributions.md) | GPL ffmpeg in the Docker image and the Windows bundle | Accepted |
| [0011](0011-unofficial-free-web-services-as-defaults.md) | Free web services are the no-key defaults for translation and voice | Accepted |
| [0012](0012-secrets-only-in-environment.md) | Secrets only in environment variables | Accepted |
| [0013](0013-amd64-only-binaries-for-v0-1.md) | Docker image and Windows bundle are amd64-only for v0.1 | Accepted |
| [0014](0014-erasers-sttn-default-others-opt-in.md) | Erasers: STTN default; ProPainter and LaMa as opt-ins | Accepted |
