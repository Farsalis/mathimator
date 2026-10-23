# Phase 4 report — template library and Cairo 3D

Date: 21 October 2026.

Phase 4 only. Phase 5 was not started. No pull request was opened. `docs/design-spec.md` was not revised.

These numbers are from a Linux VM. They are not a budget result. The reference machine in section 2 is Windows 11, 8 cores, 16 GB RAM, SSD, integrated graphics, plugged in, High performance. This machine is none of those things, so the Windows 20 second insert budget was not judged. The spec was not revised.

The phase 4 branch was cut from `origin/cursor/phase-3-equations-1504` at `2e642c7f889f820329b39c5b80b2d1c8ed63fa5d`.

## Machine

Intel Xeon, 4 cores, Ubuntu, Linux 6.12.94+. Not the Windows reference machine.

Python 3.14.8 and manim 0.22.0 come from the existing virtualenv. Typst is `typst 0.15.0 (3ae52774)` at `/usr/local/bin/typst`. Developer TeX is on `PATH`. TinyTeX was not vendored.

## What phase 4 contains

- `formula_transform` inserts two `math_formula` objects, a `write` beat, and a `transform_formula` beat. Defaults are `y = \sin(x)` to `y = \cos(x)`, anchor `edge_up`, color `#FFFFFF`, durations 2 s and 2 s. The destination's entrance is the transform, so codegen emits `TransformMatchingTex` and no separate `Create` for that formula. Editing either formula still uses the phase 3 Typst glyph.
- `three_d_axes` inserts only into an empty project. It sets `scene.kind` to `three_d_scene` and camera `{phi: 65, theta: -45, zoom: 1}`. Axes ranges are `[-3, 3, 1]`, `[-3, 3, 1]`, `[-2, 2, 1]`. The default child is the surface `sin(x)*cos(y)`, `u`/`v` range `[-2.5, 2.5]`, resolution `[8, 8]`, color `#58C4DD`, two `create` beats of 2 s. Curve mode replaces that child, retargets the second beat, and keeps both beat ids. Curve defaults are `cos(t)`, `sin(t)`, `t / 3`, `tRange` `[0, 12.56]`, color `#F2CC8F`.
- A resolution above 24 on either axis is refused and the document is unchanged. A camera edit dirties every beat.
- Mixed scenes are refused with the section 3 messages: "A 3D template needs an empty project.", "This template is for a 2D scene.", and "A 3D scene only holds the 3D template." Unknown keys and unknown templates are still refused.
- Surface expressions use `x` and `y`. Curve components use `t`. Anything outside the existing grammar is "This expression is not supported." and does not start Manim.
- Generated source for the default surface and for curve mode matches section 5, including `checkerboard_colors=False`. Manim 0.22 accepted that argument. Header, seed, and imports match Journey A. There is no `begin_ambient_camera_rotation`, `manimlib`, or `opengl`.
- The Add menu has Formula transform and 3D axes beside Axes and function, Formula, and Text. The 3D inspector has the surface/curve switch, the expression or the three components, resolution, phi, theta, and zoom, and the Cairo limits text. There is no orbit drag and no second preview window.
- Preview stays the fixed opaque 854×480, 15 fps Cairo mp4. The sample PNG sequence is a worker export, not a preset panel: `--renderer cairo -r 1280,720 --fps 30 --format png-sequence --transparent`. GIF and WebM are still refused at export time.

Absent, as specified: the export-preset matrix, the resolution picker, the transparency control in the UI, GIF, WebM as a product path, the installer, and offline bundling.

## Measurements

None of this is the reference-machine procedure. The Windows 20 s budget was not judged.

The insert clock starts at the `add_three_d_axes` edit, includes the 300 ms preview debounce, and stops when both partial movies exist. The preview cache was cold (`videos/` removed). The supervisor was already answering `status`.

| Check | Result |
| --- | --- |
| 3D insert until both partials | 2.841 s. Both partial movies existed under the project `.mathimator/cache/media`. |
| Surface preview | 854×480, 15 fps, duration 4.0 s. `out/phase4/surface-preview.mp4`. |
| Transparent PNG sequence | 120 frames, 1280×720. `out/phase4/surface/.mathimator/exports/63d4964c6e6bdc90/frames`. Frame `0108.png`: corner pixel alpha 0; 202812 cyan face pixels at alpha 255; 4858 antialiased edge pixels with a partial alpha. |
| Curve preview | Movie exists, 854×480, duration 4.0 s. |
| Formula-transform preview | Movie exists, 854×480, duration 4.0 s. Generated source contains `TransformMatchingTex`. |

The surface preview argv was:

```text
--renderer cairo --seed 1 -r 854,480 --fps 15 --format mp4
```

The PNG argv was:

```text
--renderer cairo --seed 1 -r 1280,720 --fps 30 --format png-sequence --transparent
```

Neither command contained `opengl`, `--live-preview`, or `--preview`.

## Tests

```bash
.venv/bin/python -m pytest tests -q --tb=line
```

70 passed in 64.26 s. That is the phase 1 suite, the phase 2 document and render tests, the phase 3 translator and Typst tests, the phase 4 template and refusal tests, and the phase 4 surface, PNG-sequence, curve, and formula-transform renders.

The phase 4 checks that passed:

- Each template inserts the objects, beats, and defaults in section 4.
- Changing the `axes_function` expression to `cos(x)` updates the plot and the lambda and leaves the durations that were already stored.
- Surface and curve source match section 5. Preview argv contains `--renderer cairo` and `--seed` and does not contain `opengl`, `--live-preview`, or `--preview`.
- Mixed 2D/3D inserts are refused with the messages above, and the document is unchanged.
- Resolution `[25, 8]` and `[8, 25]` are refused. `[24, 24]` is kept.
- Switching surface and curve keeps both beat ids.
- A rejected surface expression does not log an argv line.

Journey A codegen is still byte-identical to the section 5 block.

## Launch

```bash
Xvfb :98 -screen 0 1280x800x24
cd desktop
DISPLAY=:98 WEBKIT_DISABLE_DMABUF_RENDERER=1 GDK_BACKEND=x11 \
  MATHIMATOR_PYTHON=../.venv/bin/python \
  npm run tauri dev
```

The window opened on an empty project. Code view stayed closed.

1. Add → 3D axes. The timeline showed two 2.0 s creates. The inspector showed the Cairo limits text, mode Surface, expression `sin(x)*cos(y)`, resolution 8 and 8, and, after scrolling the panel, phi 65, theta -45, and zoom 1.
2. The preview finished in the same window. Status returned to Ready. Scrubbing to 3.33 s / 4.0 s showed the axes and the cyan flat-shaded surface on the black canvas. The worker log for that render was `--renderer cairo` at 854×480, 15 fps, mp4, with `--seed 1`. It did not contain `opengl`.
3. The mode control was switched to Curve. The object became `parametric_curve`, the fields became `cos(t)`, `sin(t)`, and `t / 3`, and the canvas kept the previous picture with "Preview is out of date" while the new render ran. When it finished, the canvas showed the tan helix at 3.33 s, then playback in that same canvas reached 4.0 s / 4.0 s.

`xwininfo` during this session showed the Mathimator window at 1180×800. A 10×10 window of the same `mathimator` class and a 10×10 `xdg-desktop-portal-gtk` window were also present. No window was a Manim or OpenGL preview.

The formula-transform movie was the pytest preview above, not a second pass through this window.

## Divergences

1. The Windows 20 s budget was not judged. This VM's insert was 2.841 s. That is not a pass or a fail of the reference machine, and the spec was not revised.
2. Cairo lights the flat-shaded faces, so surface pixels are cyan and brighter than the stored `#58C4DD`. The fill pixels are opaque. Antialiased edges have a partial alpha. The background corner is alpha 0. `checkerboard_colors=False` was accepted, so the generated call matches section 5.
3. 3D axis lengths are omitted. The section 5 `ThreeDAxes` call does not pass them, and Manim's own defaults apply.
4. The PNG sequence is requested by a `set_export_profile` edit and the existing `export` command. There is no preset panel, no resolution picker, and no transparency control in the window. The Export button still starts whatever profile is stored, which for a new project is the phase 2 MP4 default. GIF and WebM still stop with "This phase exports MP4 only."
5. Template params are the source of truth and round-trip in `project.json`. The formula template stores both LaTeX strings, both colors, one anchor, and one shift. The 3D template stores the mode, the camera, and both the surface settings and the curve settings so a switch does not drop the other mode.

## Failures

None in the pytest command above. The surface, the helix, the Cairo limits text, and the camera numbers were visible in the Mathimator window. The Windows 20 s budget was not failed and was not passed. Phase 5 was not started.
