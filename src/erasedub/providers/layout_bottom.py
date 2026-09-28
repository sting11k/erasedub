from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import Field

from erasedub.context import RunContext
from erasedub.models import Box, SubtitleEvent, TextRegion, VideoInfo
from erasedub.providers.base import PLUGIN_API_VERSION, ProviderOptions, SubtitleLayout
from erasedub.script import Script


class BottomLayoutOptions(ProviderOptions):
    #: Distance from the bottom edge, as a fraction of the video height.
    margin_ratio: float = Field(default=0.08, ge=0, le=0.45)
    #: Widest a line may be, as a fraction of the video width; longer text is wrapped and, if needed,
    #: shrunk by the renderer to stay inside.
    max_width_ratio: float = Field(default=0.9, gt=0.2, le=1)
    #: Tallest the subtitle block may be, as a fraction of the video height.
    max_height_ratio: float = Field(default=0.25, gt=0.05, le=0.5)


class BottomLayout(SubtitleLayout):
    """Classic subtitles: bottom centre, fixed margin, wrapped inside a centred box.

    Options: ``margin_ratio`` (default 0.08), ``max_width_ratio`` (0.9) and ``max_height_ratio`` (0.25).
    Empty lines get no event.
    """

    name: ClassVar[str] = "bottom"
    api_version: ClassVar[int] = PLUGIN_API_VERSION
    summary: ClassVar[str] = "standard bottom-centre subtitles"
    Options: ClassVar[type[ProviderOptions]] = BottomLayoutOptions

    options: BottomLayoutOptions

    def layout(
        self, script: Script, *, video: VideoInfo, regions: Sequence[TextRegion] = (), ctx: RunContext
    ) -> list[SubtitleEvent]:
        margin = round(video.height * self.options.margin_ratio)
        width = max(1, round(video.width * self.options.max_width_ratio))
        height = max(1, min(video.height - margin, round(video.height * self.options.max_height_ratio)))
        area = Box(
            x=(video.width - width) // 2, y=max(0, video.height - margin - height), width=width, height=height
        )
        return [
            SubtitleEvent(
                start=line.start, end=line.end, text=line.text.strip(), alignment=2, margin_v=margin, box=area
            )
            for line in script.lines
            if line.text.strip()
        ]
