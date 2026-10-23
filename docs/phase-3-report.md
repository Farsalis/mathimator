# Phase 3 report — equation editor

Date: 21 October 2026.

Phase 3 only. Phase 4 was not started. No pull request was opened.

These numbers are from a Linux VM. They are not a budget result. The reference machine in section 2 is Windows 11, 8 cores, 16 GB RAM, SSD, integrated graphics, plugged in, High performance. This machine is none of those things, so the Windows timing budgets were not judged here. The spec was not revised.

`origin/cursor/phase-2-timeline-1504` was fetched before the implementation push and again before this report. It was still `afb558deace4380e3c237f918ee30f5ad31a4867`. Nothing newer was merged.

## Machine

Same VM as phase 2: Intel Xeon, 4 cores, 15 GiB RAM, Ubuntu 24.04, Linux 6.12.94+. Not the Windows reference machine.

## Versions

Tauri crate 2.12.5, Tauri CLI 2.12.4, React 19.3.1, Vite 8.3.5, TypeScript 5.9.3, Rust 1.99.1, Python 3.14.8, and Manim 0.22.0 are unchanged.

Typst is the 0.15.0 release binary, `typst 0.15.0 (3ae52774)`. The VM had no `typst` package. The musl build `typst-x86_64-unknown-linux-musl.tar.xz` from the v0.15.0 GitHub release was installed at `/usr/local/bin/typst`. The `manim[typst]` extra is not installed. Scene source does not contain `Typst`, `TypstMath`, or `MathTypst`.

## What phase 3 contains

- `mathimator_worker.equation` translates the supported LaTeX subset to a Typst document. Pytest imports that module. The supervisor does not.
- The shell runs `python -m mathimator_worker.equation`, writes one `.typ` file under the project `.mathimator/equation/` directory, and runs `typst compile` to one `.svg`. That process is not the supervisor and it is not on the render queue. A new formula keystroke cancels the Typst process for that generation. A later compile does not wait for a scene render.
- The inspector waits 100 ms after a formula edit, then asks the shell for the glyph. The caption "Live preview is Typst. The video uses LaTeX and will look different." is shown whenever a formula is selected.
- `y = \sin(x)` becomes the section 6 document and an SVG. `\int` and any other command, and any character outside the subset below, produce no document and no Typst process. The editor shows "No live preview for this formula."
- A Typst failure leaves the previous SVG on screen and shows the Typst diagnostic.
- Codegen is unchanged. Formulas are still `MathTex`. The Journey A block stays byte-identical.

The supported mapping is the section 6 table. Whitespace is passed through so `y = \sin(x)` keeps its spaces. `\frac{a}{b}` becomes `frac(a, b)`. `\sqrt{x}` becomes `sqrt(x)`.

Absent, as specified: the formula-transform template, the 3D template, GIF, WebM, PNG sequence, the installer, and TinyTeX bundling. Developer TeX on `PATH` is what rendered the `MathTex` frames.

## Measurements

None of this is the reference-machine procedure. The 200 ms equation budget was not judged.

| Check | Result |
| --- | --- |
| `typst --version` | `typst 0.15.0 (3ae52774)` |
| Typst compile of `y = \sin(x)` | 0.097 s. SVG 7727 bytes, with `<svg` and `<path>`. No new Manim process. |
| UI, Formula click to a decoded SVG | 333 ms. This includes the 100 ms debounce, the shell, Typst, and the image load. At that moment the status still said "Rendering preview", so the glyph was not waiting on the scene render. |
| Integral scene preview | 854×480, 0.533 s (the 0.5 s write at 15 fps). Path `out/phase3/integral/.mathimator/cache/media/videos/scene/480p15/MainScene.mp4`. |
| Window export | 3.014 s from the click until the path was shown. 1920×1080, 6.0 s. |
| Debug binary mtime to `shell-ready.txt` | 1.092 s. The `npm run tauri dev` command that produced that binary spent 6.69 s compiling and reached ready 10.066 s after it was started. Not an 8 s budget result. |

The Typst compile time is `out/phase3/typst-compile.json`, which is not committed. The UI time was read from `performance.now()` around the Formula click.

## Tests

Before the implementation push, without the Manim renders:

```bash
.venv/bin/python -m pytest tests -q -m "not render and not export" -k "not preview_partials and not first_seven" --tb=short
```

54 passed, 6 deselected, in 3.27 s.

After that push:

```bash
.venv/bin/python -m pytest tests -q --tb=short
```

60 passed in 51.20 s. That is the phase 1 suite, the phase 2 document and render tests, the phase 3 translator and Typst tests, and the one integral `MathTex` preview.

The phase 3 checks that passed:

- Every command in the section 6 table, nested `\frac{\sqrt{x}}{\pi}`, and the pass-through characters.
- `\int`, `\int_0^1 x dx`, and `\sum` have no document.
- `y = \sin(x)` is the section 6 Typst document, and `typst compile` writes a non-empty SVG with a drawing element. Manim is not started for that SVG.
- The integral scene's generated source contains `MathTex` and the integral, and does not contain `TypstMath` or `Typst(`. The preview movie exists.
- Journey A codegen is still byte-identical to the section 5 block.

## Launch

```bash
Xvfb :98 -screen 0 1280x800x24
cd desktop
DISPLAY=:98 WEBKIT_DISABLE_DMABUF_RENDERER=1 GDK_BACKEND=x11 \
  MATHIMATOR_PYTHON=../.venv/bin/python \
  npm run tauri dev
```

The window opened on the empty project. Code view was closed. The journey was:

1. Rename the project to "Sine introduction". That did not start a scene render.
2. Add → Axes and function. The timeline shows two 2.0 s creates.
3. Add → Formula. The field is `y = \sin(x)`, anchor top edge. The caption and the white Typst glyph `y = sin(x)` were visible in the inspector. Code view stayed closed.
4. Scrub to 3.2 s / 6.0 s. The canvas showed the axes, the labels, and the cyan sine curve. The worker was not asked to render again for the scrub.
5. Export. The button and the status both read "Rendering export" in the same turn as the click. The file is `/tmp/mathimator-41269-1792619699681/.mathimator/exports/2b82685e61af5ab7/scene.mp4`, 1920×1080, 6.0 s. It is not the 854×480 preview. A frame at 5.8 s shows the axes, the sine curve, and the title formula.

The generated scene for that export is the window Journey A, not `reference-60`:

```python
formula_1 = MathTex(r"y = \sin(x)", color="#FFFFFF")
formula_1.to_edge(UP)
self.play(Write(formula_1), run_time=2)
```

The plot line is `lambda x: np.sin(x)`. There is no `Typst(`.

The Typst file written for the glyph was:

```typst
#set page(width: auto, height: auto, margin: 8pt, fill: none)
$ y = sin(x) $
```

After the export, typing `\sqrt{}` into the formula field, without committing it, kept the sine glyph and showed the Typst error `missing argument: radicand`. Replacing that draft with `\int_0^1 x dx` removed the glyph and showed "No live preview for this formula." The caption stayed. The stored `project.json` was still `y = \sin(x)`, because the field was not committed. The integral movie is the pytest preview above, and its scene source uses `MathTex`.

## Divergences

1. Characters that are not in the section 6 table, other than whitespace, are unsupported. `\int` is the command the spec names. `*`, `#`, and a bare `{` take the same "No live preview" path rather than being handed to Typst. Whitespace is passed through because the sine example contains spaces.
2. The window Journey A uses the phase 2 insert defaults: two 2 s creates and a 2 s write, 6.0 s total. The section 5 block, which the byte-identity test still matches, uses 2 s, 3 s, 2 s, and a 1 s wait. This is the same window scene phase 2 exported.
3. Typst's math is black on a transparent page. The inspector draws the SVG on a white plate so the glyph can be seen on the dark panel. The `.typ` file is the section 6 document, with no fill and no text color.
4. WebKitGTK on this VM receives the SVG the same way it receives the preview movie: the shell passes a file path, and the page fetches those bytes and plays a `blob:`. The bytes are not base64 on the supervisor stream. Windows WebView2 was not tested.
5. An empty `\sqrt{}` is inside the translator's syntax, so it becomes `sqrt()` and Typst fails. That is the failed-run path, which keeps the last good SVG. It is not the unsupported-command path.
6. The shell resolves `typst` on `PATH`, or `MATHIMATOR_TYPST` when that variable is set, and refuses a binary whose `--version` does not start with `typst 0.15.0`.

## Failures

None in the pytest command above. The glyph, the caption, the unsupported message, and the 1080p30 export were visible and checked in the window. The Windows budgets were not failed and were not passed.
