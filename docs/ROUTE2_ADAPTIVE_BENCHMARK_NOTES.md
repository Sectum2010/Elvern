# Route2 Adaptive Thread Benchmark Notes

This document is the curated human-readable record of Route2 adaptive thread benchmark data. Keep generated artifacts out of git; preserve the summarized measurements and interpretation here so future adaptive-thread changes have durable context.

## 20260430T041749Z - Local 4K UHD REMUX benchmark

- Benchmark run id/date-time: `20260430T041749Z`
- Input file path: `/home/sectum/Videos/Movies/Favorite Movies/Lord of the Rings (4K UHD)/The.Lord.of.the.Rings.The.Return.of.the.King.2003.Extended.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HEVC.REMUX-FraMeSToR.mkv`
- File size: `141,123,556,741 bytes`, about `131.43 GiB / 132G`
- Source type: local 4K UHD REMUX MKV
- Benchmark script: `scripts/route2-thread-benchmark.py`
- Artifact paths before cleanup:
  - `dev/artifacts/route2-thread-benchmark/20260430T041749Z/summary.json`
  - `dev/artifacts/route2-thread-benchmark/20260430T041749Z/summary.csv`

| Threads | Wall Time | First Segment | 45s Runway | 120s Runway | Avg CPU Cores | Peak CPU Cores | Peak RSS | Supply Rate | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2 | 437.382s | 4.504s | 108.099s | 350.803s | 2.867 | 3.956 | 2.30 GiB | 0.343x | success |
| 4 | 392.343s | 4.005s | 105.089s | 324.786s | 3.301 | 5.416 | 2.57 GiB | 0.382x | success |
| 6 | 251.715s | 3.003s | 79.566s | 211.679s | 5.279 | 8.571 | 2.83 GiB | 0.596x | success |
| 8 | 251.716s | 3.003s | 81.066s | 212.682s | 5.406 | 10.332 | 3.10 GiB | 0.596x | success |
| 10 | 197.170s | 21.520s | 64.554s | 166.640s | 7.093 | 12.649 | 3.39 GiB | 0.761x | success |
| 12 | 179.657s | 4.505s | 59.551s | 152.132s | 8.042 | 14.028 | 3.67 GiB | 0.835x | success |

### Interpretation

- 4 threads is too conservative for this heavy local 4K UHD REMUX sample.
- 6 threads is a strong first promotion target: 4 -> 6 materially improves runway preparation time.
- 8 threads did not improve over 6 in this sample.
- 10 threads materially improves over 8 and looks like the best conservative adaptive ceiling based on this one sample.
- 12 threads is still faster and appears safe here, but shows diminishing returns and should require more samples before becoming the default.
- Peak CPU at 12 threads was 14.028 cores, still below an 18-core Route2 upbound on a 20-core machine.
- Peak RSS at 12 threads was 3.67 GiB, safe on this machine.
- This is one sample only. Do not overfit globally. More benchmarks are needed across different media types.

### Current Policy Conclusion

- Keep real playback behavior unchanged for now.
- Shadow adaptive default ceiling should move from 8 to 10.
- Shadow promotion should treat 6 as the first CPU-bound promotion target.
- 10 should be the conservative high-performance shadow ceiling.
- 12 should remain configurable / experimental until more data is collected.
- Never promote threads unless CPU/thread is the likely bottleneck and source/client/storage/provider/RAM guards pass.

## Cloud Benchmark Plan / Source Probe Notes

Cloud benchmarks are separate from local file benchmarks because the bottleneck can be the provider/source path rather than CPU threads. Do not download an entire cloud file to local disk and then benchmark the local copy; that hides the real path:

`Google Drive API -> Elvern/Spark cloud proxy path -> ffmpeg -> Route2/HLS output`

Cloud source probes collect controlled Google Drive range-read measurements without creating live Route2/native playback sessions or writing production cache. Useful fields are request status, Range behavior, first-byte latency, bytes read, elapsed time, MiB/s, and provider/auth/quota errors. For interpretation:

- If cloud range throughput is low or highly variable while CPU is not active, classify the situation as SOURCE_BOUND rather than CPU_BOUND.
- If cloud throughput is healthy and Route2 supply is still low while the worker is CPU-active, CPU-bound promotion remains plausible.
- Provider auth, quota, HTTP 403/429/5xx, or token errors should stop cloud benchmarking and must not be papered over as thread-scaling evidence.

### 20260430T050918Z - Google Drive source probes

- Benchmark run id/date-time: `20260430T050918Z`
- Benchmark script: `scripts/route2-cloud-benchmark.py`
- Mode: source probe only
- Range sizes: `8 MiB`, `32 MiB`
- Positions: start, middle, near-end
- Artifact paths:
  - `dev/artifacts/route2-cloud-benchmark/20260430T050918Z/summary.json`
  - `dev/artifacts/route2-cloud-benchmark/20260430T050918Z/source_probes.csv`

Cloud end-to-end thread benchmark is deferred. The current probe intentionally avoids creating Route2/native playback sessions; full end-to-end cloud benchmarking should use a benchmark-only proxy/session design that still measures the Elvern proxy path without touching live sessions or production cache.

#### Media Items

| Media ID | Title | Source | File Size | Probe Result |
|---:|---|---|---:|---|
| 102 | The Green Mile (1999) | Google Drive cloud | 34,215,479,539 bytes / 31.87 GiB | 6/6 probes succeeded |
| 935 | Avatar.The.Way.of.Water.2022.2160p.REPACK.UHD.BluRay.REMUX.DV.HDR.HEVC.Atmos-TRiToN | Google Drive cloud | 76,167,796,686 bytes / 70.94 GiB | 6/6 probes succeeded |

#### Raw Source Probe Results

| Media ID | Probe | HTTP | First Byte | Elapsed | Bytes Read | Throughput | Result |
|---:|---|---:|---:|---:|---:|---:|---|
| 102 | start 8 MiB | 206 | 0.567s | 0.958s | 8,388,608 | 8.355 MiB/s | success |
| 102 | start 32 MiB | 206 | 0.353s | 1.354s | 33,554,432 | 23.633 MiB/s | success |
| 102 | middle 8 MiB | 206 | 0.620s | 0.987s | 8,388,608 | 8.104 MiB/s | success |
| 102 | middle 32 MiB | 206 | 0.409s | 1.643s | 33,554,432 | 19.478 MiB/s | success |
| 102 | near-end 8 MiB | 206 | 0.582s | 1.022s | 8,388,608 | 7.826 MiB/s | success |
| 102 | near-end 32 MiB | 206 | 0.525s | 1.720s | 33,554,432 | 18.605 MiB/s | success |
| 935 | start 8 MiB | 206 | 0.645s | 1.034s | 8,388,608 | 7.733 MiB/s | success |
| 935 | start 32 MiB | 206 | 0.398s | 1.773s | 33,554,432 | 18.052 MiB/s | success |
| 935 | middle 8 MiB | 206 | 0.453s | 0.691s | 8,388,608 | 11.584 MiB/s | success |
| 935 | middle 32 MiB | 206 | 0.540s | 1.598s | 33,554,432 | 20.021 MiB/s | success |
| 935 | near-end 8 MiB | 206 | 0.493s | 0.793s | 8,388,608 | 10.083 MiB/s | success |
| 935 | near-end 32 MiB | 206 | 0.396s | 1.402s | 33,554,432 | 22.832 MiB/s | success |

#### Aggregate Source Probe Summary

| Media ID | Avg Throughput | Median Throughput | Min Throughput | Max Throughput | Avg First Byte |
|---:|---:|---:|---:|---:|---:|
| 102 | 14.334 MiB/s | 13.480 MiB/s | 7.826 MiB/s | 23.633 MiB/s | 0.509s |
| 935 | 15.051 MiB/s | 14.818 MiB/s | 7.733 MiB/s | 22.832 MiB/s | 0.488s |

#### Cloud Probe Interpretation

- Both cloud items support byte Range requests and returned HTTP 206 for all tested ranges.
- First-byte latency was roughly 0.35s to 0.65s in this run.
- 8 MiB windows were slower and more latency-sensitive, around 7.7-11.6 MiB/s.
- 32 MiB windows sustained roughly 18-23.6 MiB/s.
- These source probes do not prove CPU thread scaling. They show the cloud source path is capable of moderate range throughput in this run, but full Route2 cloud preparation still needs end-to-end measurement before applying local-file thread conclusions to cloud media.
- If a real cloud Route2 worker shows low supply while these source numbers are the limiting factor and CPU is low/moderate, the adaptive classifier should remain SOURCE_BOUND and must not add threads.
