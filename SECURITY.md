# Security Policy

Elvern is a self-hosted private media server intended for use inside a private network, such as Tailscale or an equivalent WireGuard tailnet. Security reports are taken seriously. Reports must use the private channels below; public GitHub issues are not acceptable for vulnerabilities.

## Supported versions

| Version | Supported |
| --- | --- |
| Latest tagged release on main | Supported |
| Older tags | Not supported; please update. |
| Forks / modified deployments | Not supported; reports about your local modifications will be declined. |

There are no formal release tags yet, so today the supported version is effectively the latest commit on `main`. The table is forward-looking and will become accurate when tagging begins.

## Reporting a vulnerability

Primary channel: use GitHub Private Vulnerability Reporting at https://github.com/Sectum2010/Elvern/security/advisories/new. GitHub Private Vulnerability Reporting opens a private advisory draft with the maintainer so the issue can be discussed, fixed, and disclosed without first publishing exploit details.

Secondary channel: email samuel.yang.yrx@gmail.com if you prefer email or cannot use GitHub Private Vulnerability Reporting. Please include the word `security` in the subject line and avoid attaching exploit code in plaintext to the first message.

Do not open a public GitHub issue for a vulnerability.

Please include:

- Affected version, commit, or branch.
- Deployment environment, including whether Elvern was Tailscale-only or exposed outside the private network.
- Clear reproduction steps.
- Suspected impact.
- Whether you demonstrated impact or are reporting a theoretical risk.

## Response process

- New reports will be acknowledged within 7 days.
- The maintainer will assess severity, confirm reproducibility, and discuss a coordinated-disclosure window with the reporter.
- Fixes for confirmed issues will be developed on a private branch where appropriate, then released on `main`.
- The reporter will be credited in the resulting GitHub Security Advisory unless they request otherwise.

Elvern is a single-maintainer project, so response speed depends on real-life availability. There is no fix-timeline commitment beyond the 7-day acknowledgement.

## In scope

- Authentication and session handling, including login, logout, cookie/token lifetimes, and session revocation.
- Authorization and access-control bypass, including admin-only endpoints accessible without admin role or one user's data accessible by another.
- Path-traversal and unsafe filesystem access.
- Token leakage in logs, URLs, headers, browser storage, or backup contents.
- Stored or reflected XSS, CSRF, SSRF, and open-redirect vulnerabilities.
- Information disclosure about other users' watch history, file paths, or filenames beyond what the UI needs.
- Vulnerabilities in playback handoff, including browser, mobile, native VLC, Infuse, and the desktop helper, that allow unauthorized stream access or token replay after logout.
- OAuth flow vulnerabilities for cloud library connections, including state validation, code-injection, and token leakage.
- Backup, restore, and audit-log integrity issues.
- Dependency vulnerabilities with a demonstrated exploit path in Elvern's actual code.

## Out of scope

- Denial-of-service via legitimate request volume. Elvern is a family-scale tool; the maintainer cannot harden the network edge for arbitrary-volume attacks.
- Issues only exploitable when `ELVERN_BIND_HOST=0.0.0.0` is set against documented guidance. Operators who deliberately expose Elvern outside a private network are operating outside the supported configuration.
- Anything requiring physical or local shell access to the server host. If the attacker is the operator, the threat model has already failed at a more important layer.
- Reports of dependency CVEs without a real exploit path in Elvern's code.
- Best-practice suggestions or hardening ideas. These are welcome as regular GitHub issues, not as security reports.
- Self-XSS that requires a user to paste attacker-supplied content into their own DevTools.

## Coordinated disclosure

The default disclosure window is 90 days from the maintainer's confirmation of the issue, or earlier if a fix ships sooner.

The reporter and the maintainer can agree on a longer or shorter window per case.

Public details, including advisory text, technical writeups, or blog posts, should not be published before the agreed disclosure date.

## Lawful use

Elvern is intended for streaming media that the operator legitimately owns to people they have invited.

The project's non-goals, publicly visible in [docs/ROADMAP.md](docs/ROADMAP.md), include serving as a public streaming service or as a tool for unauthorized redistribution of copyrighted material.

Reports requesting features that would help circumvent content-protection schemes, enable unauthorized large-scale extraction, or otherwise serve those non-goals will be declined regardless of technical merit.

This is not a comment on the reporter's intent; it is a scope statement.

## What this policy does not promise

- No warranty. The Apache 2.0 license already disclaims warranties, and this policy does not change that.
- No bounty program.
- No service-level agreement beyond the 7-day acknowledgement.
- No guarantee that any specific report will be classified as a security issue rather than a regular bug.
