# brunna logo

The mark: three projects, each running into one well (brunna is Old Norse for a well: every project's notes run into
it). Indigo `#4f46e5` water on ink `#1f2937`; on dark
backgrounds `#818cf8` on `#e5e7eb`, with `#111827` as the icon's background.

| File | Use |
|---|---|
| `mark.svg`, `mark-dark.svg` | the mark alone, transparent, for light / dark backgrounds |
| `wordmark.svg`, `wordmark-dark.svg` | mark + name (README header) |
| `icon.svg`, `icon-512.png`, `apple-touch-icon.png` | square icon (avatars, app icons) |
| `favicon.ico`, `favicon-16.png`, `favicon-32.png` | browser tabs (the status page) |
| `social.svg`, `social.png` | GitHub social preview (1280×640; set it in the repository settings) |

The wordmark's text uses the system sans-serif, so it renders slightly differently per platform. PNGs are rendered
from the SVGs with `rsvg-convert`; `favicon.ico` holds the 16 and 32 pixel PNGs.
