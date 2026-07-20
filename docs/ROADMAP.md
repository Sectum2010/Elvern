# Elvern Roadmap

Elvern is a private, self-hosted family media library and playback control
plane. This roadmap describes the direction the project is taking and the order
in which work is expected to happen.

## How to read this roadmap

Items are grouped by horizon, not by importance. Everything here is subject to
research and regression testing before it becomes a default behaviour, and the
grouping may change as earlier work reveals new constraints. No dates are
implied.

- **Near term** — actively planned; foundations that later work depends on.
- **Medium term** — designed after near-term foundations land.
- **Long term** — directionally committed, still open on approach.
- **Next major phase** — a distinct capability that depends on most of the above.

Each item states a goal and the condition that ends the work.

## Current foundation

- Local and Google Drive media libraries with authenticated, per-user access.
- A React PWA and FastAPI backend with protected in-memory Library caching.
- Poster indexing and high-quality card poster derivatives.
- Browser Lite and Full playback paths for weaker or less stable connections.
- VLC-first desktop handoff, plus VLC and Infuse handoff on mobile platforms.
- Adaptive Route2 playback resource controls and shared-output groundwork.
- Encrypted backup checkpoints, recovery previews, connectivity recovery, and
  offline PWA support.

Installed native players remain the high-quality playback surface. Browser
playback remains the universal fallback and the primary weak-network path.

---

## Near term

### Owner identity and setup safety

**Goal.** Establish an Owner identity with stronger privileges and clearer
protection than an ordinary administrator, and stabilise the guided setup flow.

Owner creation, transfer, recovery, and removal rules are defined before the
current role model changes. Setup asks whether the installation is being run as
an **Owner (Recommended)** or as a **Developer**. Developer access is an
explicit deployment and feature profile, never an accidental privilege
escalation. Experimental features remain closed by default unless the operator
knowingly enables them.

*Done when:* the Owner lifecycle is specified and implemented, setup completes
reliably on a clean install for both profiles, and no experimental surface is
reachable without deliberate opt-in.

*Note:* several later items — recovery, updates, and assistant integration —
assume this model exists. It is scheduled first for that reason.

### Prepared playback handoff for external apps

**Goal.** Give external players the same anti-stutter preparation and playhead
control that Elvern provides around browser playback, beginning with VLC.

Linux same-host VLC, the Windows and macOS desktop helper, and mobile VLC
handoffs are examined separately, because they do not share a transport or a
control channel. The work covers prepared-stream ownership, resume reporting,
cache lifetime, seek behaviour, and safe fallback when preparation is
unavailable. Existing direct-file VLC playback is not made slower in order to
force it through a cache.

*Done when:* each supported path either uses prepared handoff measurably
without regressing startup time, or documents why it falls back.

### Title normalisation

**Goal.** Turn raw release filenames into structured title, year, edition, and
quality fields so that poster matching, external-ID resolution, deduplication,
and search all operate on clean data.

A deterministic rule-based parser handles the common cases. A small pretrained
encoder–decoder model runs only on the entries the parser cannot resolve
confidently. The model runs offline and on CPU, within a stated per-file
inference budget, and is trained on synthetic filename pairs rather than
scraped catalogues. Normalisation happens at index time, never on the playback
request path. User corrections are stored and always take precedence.

Elvern does not rename files on disk. Normalisation is a metadata layer.

*Done when:* accuracy is reported both as exact title-and-year match and as
correct external-ID resolution, corrections persist across re-indexing, and
unresolved entries degrade to the raw filename rather than to a wrong match.

### Faster Library and poster discovery

**Goal.** Reflect source changes in the gallery as quickly as practical without
repeatedly scanning an entire library, exhausting cloud API quotas, or
interrupting playback.

Local filesystem freshness, cloud change detection, poster-directory changes,
metadata updates, and frontend Library revision delivery are handled as
distinct problems.

*Done when:* each source has a measured detection delay and a manual refresh
fallback.

### Multi-track audio and subtitle switching

**Goal.** Bring browser playback to full parity on multiple audio and subtitle
tracks.

Switching works across Lite, Full, seek, resume, recovery, and shared-delivery
scenarios. Existing track-switching behaviour is the regression baseline.

*Done when:* track changes survive seek, resume, and recovery without
desynchronisation or a playback restart.

### Safe direct-copy playback

**Goal.** Complete the Route2 direct-copy path for media already compatible
with the browser playback contract.

The conservative classifier and command preview are validated against a codec,
container, audio, subtitle, HDR, seek, and browser compatibility matrix before
direct copy activates. Any uncertain or failed case falls back to transcoding.

*Done when:* the matrix is published and every uncertain case demonstrably
falls back.

### Recovery panel cleanup

**Goal.** Simplify the Backup and Recovery panel and provide a one-action entry
into a guided restore.

A convenient restore still preserves checkpoint validation, Owner
re-authentication, a clear preview, a backup of current state, and a rollback
path. "One tap" does not mean silently replacing live state.

*Depends on:* Owner identity.

*Done when:* a restore can be completed from one entry point with every
protection above intact.

---

## Medium term

### One browser playback container

**Goal.** Combine the Lite and Full playback modals into a single container
with an Auto mode.

Auto selects an initial strategy from measured source, network, client, and
server conditions, and the user can still switch. A shared interface does not
erase the different runway, readiness, recovery, and resource policies behind
each mode.

*Done when:* Auto's selection is explainable to the user and manual override
is always available.

### Better external-app experience

**Goal.** Replace plain handoff buttons with a clearer, app-oriented interface.

Recognisable app logos, platform-aware availability, and a restrained action
for obtaining additional players — without turning the playback surface into an
app-store page. Capability detection honestly distinguishes **installed**,
**not detected**, and **cannot be verified** rather than guessing from browser
behaviour.

Native-player support expands beyond VLC and Infuse to further desktop players,
each using a shared adapter contract while keeping platform-specific launch and
security rules.

*Done when:* detection never reports a false positive, and adding a player
requires only a new adapter.

### Broader operating-system support

**Goal.** Publish a platform capability matrix for Apple, Microsoft, and Linux
systems.

Library, playback, native-player handoff, helper installation, setup, recovery,
and update behaviour each declare whether they are supported, degraded, or
unavailable, rather than promising identical behaviour where operating-system
constraints differ.

*Done when:* the matrix is published and kept current as a release requirement.

---

## Long term

### Device-aware loading and virtual Library rendering

**Goal.** Keep very large libraries responsive on modest devices.

A self-detecting resource adapter chooses a loading profile from runtime
evidence. Device labels, CPU count, reported memory, and network APIs are weak
hints; measured request latency, image decode cost, frame timing, blank-card
time, and memory pressure are the stronger signals.

Virtual rendering limits mounted poster nodes to the current view. It must
preserve card identity, Detail return positioning, horizontal rails, duplicate
items across sections, resize behaviour, and orientation recovery on phones and
tablets. Work begins with an isolated desktop grid experiment rather than
replacing every Library section at once.

*Done when:* a large library scrolls within a stated frame budget on a
low-powered target device with no loss of card identity or return position.

### Rotation and poster-motion refinement

**Goal.** Improve poster positioning after rotation, zoom, and repeated Detail
returns, and explore calmer scrolling motion.

Improvements build on measured centring error and preserve existing
stable-anchor and rail restoration behaviour. Motion must not shift layout,
delay interaction, or increase image loading pressure, and must respect
reduced-motion preferences.

*Done when:* centring error is reduced against the recorded baseline on real
devices.

### Advanced title ranking

**Goal.** Build a ranking system broader than the current media-file quality
tiers, drawing on IMDb, TMDB, and TVDB data.

Ranking may combine ratings, vote confidence, popularity, release age, genre
context, audience suitability, and user preference. The research defines data
licensing, attribution, refresh frequency, source-disagreement handling,
missing-data behaviour, anti-bias rules, and the difference between a global
score and a private recommendation. Collected data is not treated as
trustworthy merely because it came from a known site.

*Depends on:* title normalisation, for reliable external-ID resolution.

*Done when:* licensing and attribution terms are settled and a score can be
explained from its inputs.

### Poster adoption data pack

**Goal.** Prepare a poster pack that makes adoption easy for new installations.

The target is at least 1,000 verified 4K movie posters, 50 TV, 50 cartoon, and
50 anime. "4K" is verified from actual image dimensions rather than inferred
from a filename. Generated card caches, application artwork, and private media
are excluded.

Nothing is published until contents, duplicates, dimensions, provenance,
privacy, distribution suitability, and rights have been reviewed.

*Done when:* the review is complete and the pack can be distributed without
open questions.

### VLC to AirPlay stability

**Goal.** Understand and, where possible, reduce jitter and unexpected
disconnection along the VLC-to-AirPlay-to-TV path.

Much of this chain sits outside Elvern's control, so the first milestone is
reproducible diagnostics that distinguish Elvern delivery stalls, VLC
buffering, local network instability, AirPlay session loss, and TV-side
behaviour. Remediation follows the evidence.

*Done when:* a failure can be attributed to a specific stage with confidence,
and Elvern-side causes are addressed.

### Secure update pipeline

**Goal.** Provide one-click install and replacement built on a complete trust
chain.

Signed releases, verified provenance, compatibility checks, pre-update backup,
database migration safety, failure rollback, release channels, and Owner
authorisation. Automatic code download and execution is not introduced without
all of these. Manual update remains supported throughout.

*Depends on:* Owner identity.

*Done when:* an update can be applied and rolled back on a live installation
without data loss.

### ATC Beta 1 laboratory program

**Goal.** Collect structured Adaptive Thread Controller data from at least 100
varied titles.

Measurements cover assigned threads, CPU, RAM, source and publish throughput,
ready runway, lead time, stalls, recovery, codec and container characteristics,
local versus cloud source behaviour, and external host pressure. Conditions are
repeatable, and results are published only after removing media paths, private
URLs, account data, and other identifying information.

*Done when:* the data shows when ATC improves preparation, when it wastes
resources, and when another bottleneck makes additional threads ineffective.

### Improve ATC from measured evidence

**Goal.** Use the laboratory results to improve ATC.

Algorithm changes are introduced one decision at a time, compared against a
fixed baseline, and guarded by rollback and active-playback protection.
Reclaim, downshift, and resupply remain measurable and reversible.

*Depends on:* ATC Beta 1.

*Done when:* each accepted change shows a measured improvement over the
baseline.

### Shared delivery for concurrent viewers

**Goal.** Allow compatible users watching the same title to reuse prepared
output instead of starting duplicate work.

This requires canonical shared segments, permission checks at attach and fetch
time, leases, cleanup protection, sparse manifests, seek behaviour, independent
per-user progress, and failure isolation. The user-facing modal is designed
only after the delivery contract is proven, and must explain whether a viewer
is preparing, sharing, falling back, or leaving a session without exposing
another user's identity or playback history.

*Done when:* two viewers can share delivery, and one can fail or leave without
affecting the other.

### Assistant and LLM integration

**Goal.** Research an optional Elvern assistant through hosted APIs, local
Ollama, or local Hugging Face endpoints.

The system is opt-in. Strict permissions, confirmation, audit,
data-minimisation, and tool boundaries sit between any model and Library,
playback, administration, filesystem, and network actions. Hosted and local
models are governed by separate privacy and trust contracts.

This is unrelated to title normalisation, which is a narrow offline utility
with no system access.

*Depends on:* Owner identity.

*Done when:* the permission and audit model is specified and every action a
model can take is enumerable.

---

## Next major phase: Virtual Theatre

Virtual Theatre is an encrypted live pathway for multiple authorised Elvern
users to watch the same title together, from different locations and devices,
with synchronised playback and low-latency comments.

It depends on mature shared delivery, cross-device clock and playhead
synchronisation, per-user authorisation, revocable session keys, reconnect and
late-join behaviour, host transfer, moderation and blocking controls, rate
limits, comment privacy, auditability, and safe fallback when a participant's
device cannot use the shared stream.

Virtual Theatre remains a private, authenticated feature. It does not turn
Elvern into a public streaming or content-distribution service.

---

## Non-goals and safety boundaries

Elvern is not trying to become:

- a public streaming service;
- a piracy or public content-distribution platform;
- an unbounded cloud account that receives private Library data by default;
- a feature-for-feature copy of a larger media server;
- an autonomous LLM with unrestricted access to the host system.

New playback paths keep safe fallbacks. New public data packs are reviewed
before distribution. Security, permissions, privacy, and recovery are release
requirements, not cleanup after a feature ships.