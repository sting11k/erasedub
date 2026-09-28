# Demo set

The README animations and the demo videos come from two short clips. They were made outside EraseDub; a demo set
rendered by EraseDub on the same clips is on the [roadmap](roadmap.md).

## Clips

| Id | Format | Subtitles in the source | Comparison |
|---|---|---|---|
| `doc` | vertical 9:16, 1080×1920, 15 s | a product clip with real burned-in Chinese subtitles | hardsub \| erased (there is no clean original) |
| `ngang` | horizontal 16:9, 1920×1080, 19 s | a scene from *Tears of Steel* with Chinese subtitles we burned in ourselves | hardsub \| erased, and the clean original exists for comparison |

`doc` and `ngang` are Vietnamese for vertical and horizontal; the release file names use them.

*Tears of Steel* © Blender Foundation | [mango.blender.org](https://mango.blender.org) ·
[studio.blender.org](https://studio.blender.org/films/tears-of-steel/).

## Outputs

Each clip is rendered into Vietnamese, English and Chinese, plus a side-by-side "hardsub | erased" video. When the
target is the clip's own spoken language (`doc` → zh, `ngang` → en), the original voice is kept and only the
subtitles are replaced.

The full videos are **release assets** of v0.1, not files in Git:

- `doc.vi.mp4`, `doc.en.mp4`, `doc.zh.mp4`, `doc.side-by-side.mp4`
- `ngang.vi.mp4`, `ngang.en.mp4`, `ngang.zh.mp4`, `ngang.side-by-side.mp4`

## README animations

The READMEs show animated WebP (GitHub shows it in an `<img>` tag), not GIF, stored in `docs/assets/`:

| File | What | Size | Limit |
|---|---|---|---|
| `demo-before-after.webp` | short preview at the top: each frame split in the middle, left "Before" (hardsub), right "After" (erased, with Vietnamese subtitles); 720×406, 4 s | 432,406 B | under 500 KB |
| `compare-horizontal.webp` | full comparison of `ngang` in the Demo section: "Before" (hardsub) and "After" (erased, no new subtitles) side by side; 960×270, 19.2 s | 2,341,920 B | at most 3 MB |
| `compare-vertical.webp` | full comparison of `doc` in the Demo section, same layout; 720×638, 15.3 s | 2,464,458 B | at most 3 MB |

All three are silent, 10 frames per second, looping.

- Pre-commit enforces the limits on every file, in CI too (`check-added-large-files --enforce-all`): 500 KB
  everywhere, and 3 MB only for WebP files directly in `docs/assets/` (`^docs/assets/[^/]+\.webp$`).
- Linked from the READMEs with absolute `https://github.com/sting11k/erasedub/raw/main/docs/assets/...` URLs.

## Social preview

`docs/assets/social-preview.png` (1280×640 PNG, 375,839 B, under GitHub's 1 MB limit) is the image shown when a link
to the repository is shared. It is uploaded by hand in Settings → General → Social preview
([ci.md](dev/ci.md#repository-settings-to-turn-on)); the READMEs do not use it. It shows
the name, the tagline and a frame from `demo-before-after.webp` at its native 720×406, with the same credit and
"made outside EraseDub" note as the READMEs. Replace it when a logo exists or the demo is re-rendered by EraseDub.
