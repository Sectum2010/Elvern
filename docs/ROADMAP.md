# Elvern Roadmap

This roadmap is meant to describe likely areas of improvement based on the current repository state. It is not a promise that every item will ship on a fixed timeline.

## Current direction

Elvern is currently centered on a private, self-hosted family media stack with:

- a FastAPI backend plus React frontend
- cookie-based auth and basic family-user administration
- local media scanning with `ffprobe`
- browser playback for convenience and fallback
- VLC-first desktop playback as the primary high-quality path
- Ubuntu/systemd automation and a first-pass all-in-one Docker deployment path

## Near term

Likely next improvements:

- Docker polish so the first self-hosted container path is clearer and less fragile
- README and setup-doc polish so first-time self-hosted users can get running faster
- broader backend tests and CI coverage around auth, media safety, and playback-adjacent contracts
- install ergonomics and helper setup clarity, especially for the desktop VLC opener flow

## Later

Possible later work, if the current direction continues to prove useful:

- signed or more polished helper packaging for desktop clients
- better playback diagnostics for desktop handoff, browser fallback, and route selection
- backup and restore documentation for SQLite data and persistent app state
- more deployment hardening guidance for private self-hosted environments

## Non-goals reminder

The project is not trying to become:

- a public streaming service
- a content-sharing or piracy distribution platform
- a feature-for-feature replacement for larger media servers

The main target remains a smaller private control plane for family media playback, where installed VLC is the preferred desktop playback surface.
