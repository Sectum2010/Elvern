# Security Engineering Checklist

Use this checklist for security-sensitive Elvern changes, especially after CodeQL findings.

## Path Input Safety

- Never construct `Path(...)` directly from request or admin input in route/service code.
- Use central local path validators for local media roots, library references, poster references, and browse targets.
- Block system roots, broad roots, temporary roots, and Elvern internals from media/library-reference configuration.
- Revalidate stored paths before later filesystem use; stored config can become stale or hostile.
- Guard scanners against symlink escape, symlink loops, and recursive traversal outside approved roots.

## Command Execution Safety

- Do not pass request-controlled free text into subprocess command arguments.
- Prefer purpose or enum allowlists for command labels, picker titles, modes, and actions.
- Use absolute executable paths returned by `shutil.which(...)`; do not rely on PATH lookup during `subprocess.run(...)`.
- Do not trust client-provided `same_host_hint` for host GUI actions or local process launch decisions.

## Proxy And SSRF Safety

- Do not build fetch targets from raw `request.url`.
- Accept only validated origin-form paths from clients.
- Keep the final upstream origin fixed or allowlisted, including redirects.
- Validate proxy body-size and streaming behavior before adding large request paths.

## Regex And ReDoS Safety

- Do not run complex regex patterns on uncontrolled long strings.
- Prefer linear scanners for filename/title parsing when inputs can be attacker-controlled.
- Keep diagnostic and parser input limits generous enough for real media names but bounded.

## Backup And CLI Output Safety

- Treat backup contents as secret-bearing: env values, OAuth tokens, sessions, database data, helper metadata, and uploads may be included.
- CLI create commands should print safe summaries, not full manifests or nested metadata.
- Keep backup contents, manifests, encryption, restore, and inspect behavior separate from safe stdout summaries.

## Logging Safety

- Do not log raw session IDs, access tokens, tokenized URLs, or local media paths.
- Use stable non-reversible fingerprints for correlation.
- Log sanitized origins only as scheme plus host/port.
- Prefer media item IDs, endpoint names, return codes, and exception types over raw paths or exception strings.
- Do not log ffprobe stdout/stderr, env dumps, request tokens, or filesystem paths that reveal private media names.

## CodeQL Remediation Workflow

- Fix the root cause first.
- Avoid broad suppressions.
- If a denylist or Plan B approach still triggers CodeQL, document tests and evidence before considering a narrow suppression.
- Run targeted tests for the affected surface.
- Run `./scripts/elvern-ci-local.sh --fresh` before claiming completion for security work.
- Do not manually dismiss CodeQL alerts; let a new scan close fixed findings.
