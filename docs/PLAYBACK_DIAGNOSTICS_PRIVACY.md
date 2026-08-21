# Playback Diagnostics Privacy

## Privacy boundary

Playback diagnostics are local research records. They are not general activity
logs and are not exported to an external service. The recorder observes only
playback-related behavior and operational measurements.

## Approved identity fields

- Random pseudonymous subject, client, session, attempt, attachment, epoch,
  worker, incident, trace, span, prediction, and decision IDs.
- Media item ID.
- Exact original movie basename.
- SHA-256 basename hash and source fingerprint.
- Normalized platform/browser/OS family and version.
- Source/path class and non-secret technical metadata.

The raw record does not contain a username. A random subject ID is associated
with the numeric account ID only in `identities/identity-map.enc`. That mapping
is encrypted with the active diagnostics journal-encryption key. A separate,
stable random `identity-hmac-key.bin` is used only to derive owner/IP
pseudonyms; it is never reused as the encryption key. Account deletion removes
the mapping without deleting already pseudonymized research sessions.

## Exact basename policy

The exact basename is the one explicitly approved sensitive exception. For a
source such as:

```text
/private/library/Films/Winnetou - Apache Gold (1963) 复刻版.mkv
```

the recorder may store:

```text
Winnetou - Apache Gold (1963) 复刻版.mkv
```

It may not store `/private/library/Films/` or the absolute source path. Basename
validation strips both slash styles, rejects null bytes and `.`/`..`, and limits
the UTF-8 representation to 4096 bytes.

The exact basename is preserved in structured private JSON/raw evidence. It is
not copied directly into a terminal, Markdown, or CSV control context. Local
human-readable output escapes newline, carriage return, tab, terminal control
bytes, and Markdown delimiter runs; CSV cells beginning with `=`, `+`, `-`, or
`@` receive a formula-safe prefix. These display/export transformations do not
rewrite the structured exact basename.

## Allowlisted data

The shared privacy layer accepts only the versioned event envelope and explicit
payload vocabulary. Examples include:

- playback timing, playhead, duration, buffer and frame metrics;
- segment index/range, byte count, status and timing;
- normalized Browser Playback route templates and SHA-256 URL identity hashes;
- provider throughput and existing request Range metadata;
- FFmpeg command fingerprint and sanitized error class, never the command;
- host aggregate counters, PSI, cgroup, filesystem, GPU and path class;
- recorder queue, ACK, drop, capacity and integrity metrics;
- exact source basename as the named exception above.

Unknown payload keys are discarded. Non-finite numeric values become `null`.
Nested values have depth and collection limits. Each persisted event is bounded
to 64,000 encoded bytes.

Normalized routes are accepted only under `/api/browser-playback/` and may not
contain `..`, query strings, fragments, or full URLs. This distinguishes an API
route identity from an absolute filesystem path.

## Prohibited data

The recorder rejects or never observes:

- username, password, TOTP, recovery code, or invite code;
- access, refresh, session, ID, OAuth, or provider tokens;
- cookies, `Authorization`, arbitrary headers, or response bodies;
- raw IP, Wi-Fi SSID, geolocation, packet payload, or media bytes;
- full user agent;
- full media path, cloud URL, resource key, or arbitrary URL;
- full FFmpeg command or exception representation that may contain a secret;
- subtitle content;
- ordinary keystrokes or mouse coordinates;
- browsing history, other open pages, or other application names.

Secret-pattern checks cover bearer/authorization text, token assignments,
cookies, OAuth-like values, sensitive query parameters, Google resource keys,
and absolute POSIX/Windows/UNC paths. Client data is sanitized before IndexedDB,
then validated and sanitized again before permanent server persistence.

## Network identity

Raw IP addresses are not persisted. Server-side classification may retain
`loopback`, `lan_or_tailnet`, or `public`, plus an HMAC-SHA-256 pseudonym
generated from address bytes and the stable independent identity HMAC key.
Tailscale observations retain a coarse path class
such as `direct`, `peer_relay`, `derp`, or `unknown`, and bounded health/state
counts. They do not retain auth keys, node keys, peer names, or packet payloads.

## Browser capability privacy

The browser stores parsed browser/OS family and version, not the full user agent.
Optional Web capability checks do not change COOP, COEP, CSP, isolation,
exposure, authentication, or cookie behavior. Unsupported browser internals are
recorded as unavailable rather than inferred from private device data.

## Encryption and local access

Raw journals and the identity map use AES-256-GCM with a random key from the
diagnostics journal key store. Compression occurs before journal encryption.
The independent identity HMAC key is not an encryption key. Neither key is
derived from or reused from Elvern auth, OAuth, backup, cookies, or provider
secrets. Direct-open session files are allowlisted plaintext for the local
operator, mode `0600`, under `0700` directories.

Local exports inherit the same allowlisted event content and are mode `0600`.
The CLI never uploads them. Anyone with operating-system access to the
diagnostics root and key can read local records, so filesystem access remains a
trusted administrative boundary.

## Backup and repository exclusion

The diagnostics root is explicitly absent from ordinary Elvern backup and
restore, Git, and Docker build contexts. This includes keys, mappings, catalog,
raw journals, summaries, exports, quarantine, and current status. Synthetic
benchmark output is written under ignored project `tmp/`.

## Verification

Focused tests prove:

- exact basename survives while its parent path does not;
- absolute paths, full URLs, query secrets, bearer/cookie/token patterns, and
  arbitrary keys are rejected or removed;
- normalized Browser Playback routes remain usable without permitting file
  paths;
- raw user agent is not part of the event schema;
- URL identities use full SHA-256 rather than a short or non-cryptographic hash;
- terminal/Markdown controls and CSV formula prefixes are rendered safely while
  the structured exact basename remains intact;
- identity mapping is encrypted and is unlinked on account deletion;
- ordinary backups and restore exclude diagnostics;
- Git and Docker ignore rules explicitly cover the root.
