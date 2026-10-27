# Security policy

## Reporting a vulnerability

Report a vulnerability privately, through GitHub's private vulnerability
reporting: open
<https://github.com/Farsalis/mathimator/security/advisories/new>, or choose
"Report a vulnerability" on the Security tab of
<https://github.com/Farsalis/mathimator>. Never report one in a public issue,
pull request, discussion or comment. Only you and the maintainers can see the
report.

Please include:

- the Mathimator version (the installer's file name, or the commit of a
  development build) and the Windows version;
- what an attacker controls, such as a project folder someone opens, and what
  they gain;
- the steps to reproduce, with the smallest project that shows the problem;
- whether the problem is already public anywhere.

The maintainers answer in the report and work on the fix there. The advisory
is published once a release with the fix is out.

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
and the CI and release workflows.

Mathimator treats a project as data. Opening, previewing or exporting a
project must never run code or TeX commands of its author's choosing, or read
or write files outside the project folder. Formula LaTeX is limited to an
allowlist of commands and runs with shell escape off. Mathimator refuses a
project whose working folders are symbolic links or junctions, and no delete
follows a link. An installed Mathimator runs only the Python, TeX and Typst of
its own runtime, and passes none of the inherited `MATHIMATOR_*` development
variables to its worker. A way around any of this is a vulnerability.

A problem in a bundled component, such as Manim, PyAV and its FFmpeg, TinyTeX,
Typst, Tauri or WebView2, belongs with that project. If the version Mathimator
ships is affected, also report it here privately so that the bundle can be
updated.

## Verifying a download

v1 installers are unsigned. Download them only from the project's GitHub
Releases, <https://github.com/Farsalis/mathimator/releases>, and check them
against the `SHA256SUMS` published with each release.
`SHA256SUMS` also lists `THIRD_PARTY_NOTICES.txt`, so in the folder that holds
the installer and `SHA256SUMS`, run `sha256sum -c --ignore-missing SHA256SUMS`,
which skips the files you did not download. In PowerShell, compare
`(Get-FileHash -Algorithm SHA256 <installer>).Hash.ToLower()` with the hash on
the installer's line in `SHA256SUMS`.
