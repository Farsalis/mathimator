# Security policy

## Reporting a vulnerability

Report a vulnerability in a confidential issue in Mathimator's GitLab project,
never in a public issue, merge request or comment. When you create the issue,
select the checkbox that marks it confidential, so that only you and the
project team can see it.

Please include:

- the Mathimator version (the installer's file name, or the commit of a
  development build) and the Windows version;
- what an attacker controls, such as a project folder someone opens, and what
  they gain;
- the steps to reproduce, with the smallest project that shows the problem;
- whether the problem is already public anywhere.

The maintainer answers in the issue and works on the fix there. The issue is
made public once a release with the fix is out.

## Supported versions

Mathimator has not had a release yet. Once it has, security fixes go into a
new release, not into older ones.

| Version | Security fixes |
| --- | --- |
| The newest release | Yes |
| Older releases | No |

## Scope

The scope is Mathimator's own code and what it ships: the shell, the UI, the
worker and the scene code it generates, the installer and its bundled runtime,
and the CI and release pipeline.

Mathimator treats a project as data. Opening, previewing or exporting a
project must never run code or TeX commands of its author's choosing, or read
or write files outside the project folder. Formula LaTeX is limited to an
allowlist of commands and runs with shell escape off. An installed Mathimator
runs only the Python, TeX and Typst of its own runtime. A way around any of
this is a vulnerability.

A problem in a bundled component, such as Manim, PyAV and its FFmpeg, TinyTeX,
Typst, Tauri or WebView2, belongs with that project. If the version Mathimator
ships is affected, also open a confidential issue here so that the bundle can
be updated.

## Verifying a download

v1 installers are unsigned. Download them only from the project's GitLab
Releases, and check them against the `SHA256SUMS` published with each release.
`SHA256SUMS` also lists `THIRD_PARTY_NOTICES.txt`, so in the folder that holds
the installer and `SHA256SUMS`, run `sha256sum -c --ignore-missing SHA256SUMS`,
which skips the files you did not download. In PowerShell, compare
`(Get-FileHash -Algorithm SHA256 <installer>).Hash.ToLower()` with the hash on
the installer's line in `SHA256SUMS`.
