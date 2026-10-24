## What and why

<!-- What changes and why. Link the issue, and name the audit findings or
decisions the change follows. -->

## How it was tested

<!-- The commands you ran and what they reported, and the CI lanes that cover
the change. -->

## Checklist

- [ ] The commits follow Conventional Commits, one per verified step, with no
      AI attribution trailers.
- [ ] Tests cover the change, and the lanes it touches pass. A change to
      rendering, export or the bundle also ran the manual
      `test:render:windows` job.
- [ ] `docs/design-spec.md` describes any change of behaviour.
- [ ] A change to the generated scene code bumps `GENERATOR_VERSION` in
      `worker/mathimator_worker/document.py` and updates `scenes/` and spec
      section 5.
- [ ] A change to the commands or events between the shell and the worker
      updates both sides, `tests/fixtures/protocol/commands.json` and spec
      section 6, and bumps the protocol version if it is incompatible.
- [ ] The pinned versions are untouched, or change in every copy with a spec
      revision: Manim 0.22.0, Typst 0.15.0, CPython 3.14.9 and 3.13.17, uv
      0.13.5, Rust 1.93.0, Node 24, Tauri 2.12.x, React 19 and Vite 8.
