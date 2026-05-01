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

## Cloud End-to-End Benchmark: The Green Mile + Avatar Way of Water

### 20260430T051846Z - Benchmark-only cloud ffmpeg e2e run

- Benchmark run id/date-time: `20260430T051846Z`
- Benchmark script: `scripts/route2-cloud-benchmark.py`
- Mode: `ffmpeg-e2e`
- Path measured: Google Drive API -> temporary Elvern benchmark localhost proxy -> ffmpeg -> isolated HLS/fMP4 output
- Thread counts: `4`, `6`, `10`, `12`
- Repeat count: `1`
- Profile: `mobile_2160p`
- Sample duration: `150s`
- Artifact paths:
  - `dev/artifacts/route2-cloud-benchmark/20260430T051846Z/summary.json`
  - `dev/artifacts/route2-cloud-benchmark/20260430T051846Z/summary.csv`

This run did not create normal Route2/native playback sessions and did not write production Route2 cache. The benchmark proxy exposed only tokenless localhost URLs to ffmpeg and forwarded provider Range reads internally.

| Media ID | Title | File Size |
|---:|---|---:|
| 102 | The Green Mile (1999) | 34,215,479,539 bytes / 31.87 GiB |
| 935 | Avatar.The.Way.of.Water.2022.2160p.REPACK.UHD.BluRay.REMUX.DV.HDR.HEVC.Atmos-TRiToN | 76,167,796,686 bytes / 70.94 GiB |

### Raw Cloud E2E Results

| Media ID | Threads | Wall Time | First Segment | 45s Runway | 120s Runway | Avg CPU Cores | Peak CPU Cores | Peak RSS | Supply Rate | Source Requests | Source Bytes | Source Rate | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 102 | 4 | 47.544s | 3.503s | 15.513s | 38.035s | 9.619 | 13.349 | 2.97 GiB | 3.155x | 6 | 374,949,165 | 7.521 MiB/s | success |
| 102 | 6 | 39.042s | 3.504s | 13.514s | 32.035s | 11.958 | 15.104 | 3.07 GiB | 3.843x | 6 | 374,949,165 | 9.159 MiB/s | success |
| 102 | 10 | 38.538s | 3.503s | 13.514s | 31.532s | 12.255 | 15.307 | 3.13 GiB | 3.893x | 6 | 374,949,165 | 9.279 MiB/s | success |
| 102 | 12 | 38.038s | 3.504s | 13.012s | 31.531s | 12.555 | 15.325 | 3.19 GiB | 3.944x | 6 | 375,997,741 | 9.427 MiB/s | success |
| 935 | 4 | 71.566s | 4.005s | 20.519s | 57.053s | 7.526 | 10.053 | 3.10 GiB | 2.096x | 6 | 891,611,340 | 11.881 MiB/s | success |
| 935 | 6 | 55.554s | 5.005s | 18.017s | 45.044s | 9.978 | 12.546 | 3.12 GiB | 2.701x | 6 | 891,611,340 | 15.306 MiB/s | success |
| 935 | 10 | 48.054s | 3.504s | 16.519s | 39.042s | 11.944 | 15.428 | 3.35 GiB | 3.122x | 6 | 895,805,644 | 17.778 MiB/s | success |
| 935 | 12 | 44.052s | 3.504s | 14.517s | 35.043s | 13.092 | 16.084 | 3.51 GiB | 3.406x | 6 | 891,611,340 | 19.302 MiB/s | success |

All cloud e2e runs succeeded. The benchmark proxy saw HTTP 206 Range responses for all source requests and no provider/auth/quota errors.

### Per-Movie Interpretation

The Green Mile:

- 6 improved materially over 4: wall time dropped from 47.544s to 39.042s, and 120s runway dropped from 38.035s to 32.035s.
- 10 barely improved over 6: wall time improved by about 0.504s and 120s runway by about 0.503s.
- 12 barely improved over 10: wall time improved by about 0.5s and 120s runway was effectively unchanged.
- Peak CPU stayed around 15.3 cores, below an 18-core Route2 upbound.
- Peak RSS stayed around 3.2 GiB.
- This sample looks CPU/thread-beneficial from 4 -> 6, then mostly flat. Past 6, it may be source/proxy, encode pipeline, or source media complexity limited rather than meaningfully thread-limited.

Avatar: The Way of Water:

- 6 improved materially over 4: wall time dropped from 71.566s to 55.554s, and 120s runway dropped from 57.053s to 45.044s.
- 10 improved over 6: wall time dropped to 48.054s, and 120s runway dropped to 39.042s.
- 12 improved over 10: wall time dropped to 44.052s, and 120s runway dropped to 35.043s.
- Peak CPU at 12 was 16.084 cores, still below an 18-core Route2 upbound but closer to it than the Green Mile.
- Peak RSS stayed around 3.5 GiB.
- This sample still appears CPU/thread-beneficial through 12, with no cloud-source error signal in this first run.

### Cross-Cloud Interpretation

- The earlier source probes showed about 14-15 MiB/s average range throughput with HTTP 206 support.
- In e2e mode, higher thread counts increased source bytes consumed per second as ffmpeg demanded more input, and the proxy continued returning 206 without provider errors.
- Both cloud movies strongly support 4 -> 6 as a first CPU-bound promotion target.
- The Green Mile does not justify 10+ on its own, while Avatar still benefits from 10 and 12.
- Because these are single-repeat benchmark-only runs, do not change adaptive policy yet.
- 10 remains a reasonable conservative shadow ceiling based on the combined local LOTR and first cloud e2e data.
- 12 should remain configurable / experimental until a full 2-12 matrix with repeats confirms the benefit across more cloud titles and provider conditions.

### Policy Implication

- Keep real playback behavior unchanged.
- Keep treating cloud SOURCE_BOUND evidence as a hard blocker for thread promotion.
- If cloud source probes/e2e source metrics are healthy, low supply plus CPU-active workers can still be CPU_BOUND for cloud items.
- Run the full 2-12 thread matrix with at least 2 repeats before changing the shadow adaptive default or promotion ladder again.

## Cloud End-to-End Full Matrix: The Green Mile + Avatar Way of Water

### 20260430T053156Z - Full cloud ffmpeg e2e matrix attempt

- Benchmark run id/date-time: `20260430T053156Z`
- Resume/failure-confirmation run id/date-time: `20260430T061530Z`
- Benchmark script: `scripts/route2-cloud-benchmark.py`
- Mode: `ffmpeg-e2e`
- Path measured: Google Drive API -> temporary Elvern benchmark localhost proxy -> ffmpeg -> isolated HLS/fMP4 output
- Thread counts requested: `2`, `3`, `4`, `5`, `6`, `7`, `8`, `9`, `10`, `11`, `12`
- Repeat count requested: `2`
- Profile: `mobile_2160p`
- Sample duration: `150s`
- Artifact paths:
  - `dev/artifacts/route2-cloud-benchmark/20260430T053156Z/summary.json`
  - `dev/artifacts/route2-cloud-benchmark/20260430T053156Z/summary.csv`
  - `dev/artifacts/route2-cloud-benchmark/20260430T061530Z/summary.json`
  - `dev/artifacts/route2-cloud-benchmark/20260430T061530Z/summary.csv`

This run stayed benchmark-only. It did not create normal Route2/native playback sessions and did not write production Route2 cache. ffmpeg consumed tokenless localhost benchmark proxy URLs; OAuth/provider details stayed inside the benchmark proxy.

### Media Items

| Media ID | Title | Source | File Size |
|---:|---|---|---:|
| 102 | The Green Mile (1999) | Google Drive cloud | 34,215,479,539 bytes / 31.87 GiB |
| 935 | Avatar.The.Way.of.Water.2022.2160p.REPACK.UHD.BluRay.REMUX.DV.HDR.HEVC.Atmos-TRiToN | Google Drive cloud | 76,167,796,686 bytes / 70.94 GiB |

### Completion / Failure Notes

- The Green Mile completed the full `2-12` thread matrix with `2` repeats per thread: `22/22` successful runs.
- Avatar completed `2-9` threads with `2` repeats per thread: `16/16` successful runs through thread `9`.
- Avatar thread `10` repeat `1` in the full run aborted after provider/auth errors: proxy status counts included `401`, `403`, and repeated `416` reconnect-at-EOF responses.
- A fresh resume attempt for Avatar threads `10`, `11`, and `12` failed immediately with Google Drive `403 downloadQuotaExceeded`.
- Treat Avatar `10-12` as missing for the full-matrix aggregate. Do not infer thread scaling from the failed rows.

### Aggregated Successful Results

| Movie | Threads | Runs | Avg Wall | Median Wall | Avg 45s Runway | Median 45s Runway | Avg 120s Runway | Median 120s Runway | Avg Supply | Median Supply | Avg CPU Cores | Max Peak CPU | Max Peak RSS | 120s Repeat Diff |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| The Green Mile | 2 | 2 | 82.076s | 82.076s | 25.023s | 25.023s | 65.811s | 65.811s | 1.828x | 1.828x | 5.348 | 7.470 | 2.86 GiB | 0.497s |
| The Green Mile | 3 | 2 | 59.057s | 59.057s | 19.017s | 19.017s | 47.546s | 47.546s | 2.540x | 2.540x | 7.599 | 10.410 | 2.89 GiB | 0.002s |
| The Green Mile | 4 | 2 | 47.794s | 47.794s | 15.764s | 15.764s | 38.035s | 38.035s | 3.139x | 3.139x | 9.604 | 13.907 | 2.97 GiB | 0.000s |
| The Green Mile | 5 | 2 | 41.042s | 41.042s | 14.017s | 14.017s | 33.035s | 33.035s | 3.655x | 3.655x | 11.373 | 14.785 | 2.98 GiB | 0.003s |
| The Green Mile | 6 | 2 | 39.290s | 39.290s | 13.763s | 13.763s | 32.282s | 32.282s | 3.819x | 3.819x | 12.016 | 15.049 | 3.06 GiB | 0.505s |
| The Green Mile | 7 | 2 | 38.791s | 38.791s | 13.514s | 13.514s | 31.532s | 31.532s | 3.867x | 3.867x | 12.195 | 15.309 | 3.15 GiB | 0.001s |
| The Green Mile | 8 | 2 | 38.291s | 38.291s | 13.263s | 13.263s | 31.283s | 31.283s | 3.918x | 3.918x | 12.384 | 15.263 | 3.06 GiB | 0.498s |
| The Green Mile | 9 | 2 | 38.543s | 38.543s | 13.264s | 13.264s | 31.532s | 31.532s | 3.893x | 3.893x | 12.302 | 15.549 | 3.11 GiB | 0.003s |
| The Green Mile | 10 | 2 | 38.790s | 38.790s | 13.764s | 13.764s | 31.782s | 31.782s | 3.868x | 3.868x | 12.264 | 15.187 | 3.12 GiB | 0.499s |
| The Green Mile | 11 | 2 | 38.540s | 38.540s | 13.514s | 13.514s | 31.532s | 31.532s | 3.893x | 3.893x | 12.366 | 15.404 | 3.33 GiB | 0.002s |
| The Green Mile | 12 | 2 | 38.541s | 38.541s | 13.514s | 13.514s | 31.534s | 31.534s | 3.893x | 3.893x | 12.418 | 15.248 | 3.21 GiB | 0.003s |
| Avatar Way of Water | 2 | 2 | 124.369s | 124.369s | 31.779s | 31.779s | 99.094s | 99.094s | 1.207x | 1.207x | 4.188 | 6.296 | 2.92 GiB | 0.008s |
| Avatar Way of Water | 3 | 2 | 83.078s | 83.078s | 22.520s | 22.520s | 66.562s | 66.562s | 1.806x | 1.806x | 6.388 | 9.232 | 2.96 GiB | 0.002s |
| Avatar Way of Water | 4 | 2 | 71.821s | 71.821s | 20.269s | 20.269s | 57.557s | 57.557s | 2.089x | 2.089x | 7.449 | 9.849 | 3.10 GiB | 0.002s |
| Avatar Way of Water | 5 | 2 | 59.058s | 59.058s | 18.018s | 18.018s | 47.546s | 47.546s | 2.540x | 2.540x | 9.268 | 12.251 | 3.09 GiB | 1.001s |
| Avatar Way of Water | 6 | 2 | 56.059s | 56.059s | 17.267s | 17.267s | 45.047s | 45.047s | 2.676x | 2.676x | 9.935 | 12.108 | 3.17 GiB | 1.008s |
| Avatar Way of Water | 7 | 2 | 51.553s | 51.553s | 15.766s | 15.766s | 41.543s | 41.543s | 2.910x | 2.910x | 10.979 | 13.883 | 3.27 GiB | 0.006s |
| Avatar Way of Water | 8 | 2 | 48.053s | 48.053s | 15.016s | 15.016s | 38.542s | 38.542s | 3.122x | 3.122x | 11.872 | 15.006 | 3.38 GiB | 0.002s |
| Avatar Way of Water | 9 | 2 | 45.556s | 45.556s | 15.518s | 15.518s | 36.793s | 36.793s | 3.295x | 3.295x | 12.613 | 15.675 | 3.34 GiB | 1.508s |

### Failed / Incomplete Rows

| Run ID | Movie | Threads | Repeat | Result | Provider Evidence |
|---|---|---:|---:|---|---|
| 20260430T053156Z | Avatar Way of Water | 10 | 1 | failed after 487.449s | `CloudSourceAbort`; status counts included `206`, `401`, `403`, `416` |
| 20260430T061530Z | Avatar Way of Water | 10 | 1 | failed immediately | Google Drive `403 downloadQuotaExceeded` |
| 20260430T061530Z | Avatar Way of Water | 10 | 2 | failed immediately | Google Drive `403 downloadQuotaExceeded` |
| 20260430T061530Z | Avatar Way of Water | 11 | 1 | failed immediately | Google Drive `403 downloadQuotaExceeded` |
| 20260430T061530Z | Avatar Way of Water | 11 | 2 | failed immediately | Google Drive `403 downloadQuotaExceeded` |
| 20260430T061530Z | Avatar Way of Water | 12 | 1 | failed immediately | Google Drive `403 downloadQuotaExceeded` |
| 20260430T061530Z | Avatar Way of Water | 12 | 2 | failed immediately | Google Drive `403 downloadQuotaExceeded` |

### Per-Movie Interpretation

The Green Mile:

- 5 materially improved over 4: average 120s runway improved from 38.035s to 33.035s.
- 6 improved only slightly over 5: average 120s runway improved from 33.035s to 32.282s.
- 7 and 8 produced small additional gains; 8 was the fastest successful average 120s runway at 31.283s.
- 9, 10, 11, and 12 were effectively flat or slightly worse than 8.
- Diminishing returns begin around 6, with a practical plateau around 7-8.
- Peak CPU never approached the 18-core Route2 upbound; max peak CPU was 15.549 cores.
- Peak RSS remained safe, maxing at 3.33 GiB.
- This title looks strongly thread-beneficial from 2 -> 5 and modestly beneficial through 8, then flat.

Avatar: The Way of Water:

- 5 materially improved over 4: average 120s runway improved from 57.557s to 47.546s.
- 6 improved only modestly over 5: average 120s runway improved from 47.546s to 45.047s.
- 7, 8, and 9 continued to improve repeatably, with thread 9 reaching 36.793s average 120s runway.
- The full-matrix run could not measure 10-12 because the provider path hit auth/quota failures. The earlier small cloud e2e run did show 10 and 12 improving this title, but that needs a fresh full repeat after quota resets.
- Peak CPU stayed below the 18-core Route2 upbound; max successful peak CPU through thread 9 was 15.675 cores.
- Peak RSS remained safe, maxing at 3.38 GiB in the successful rows.
- Successful 2-9 rows look CPU/thread-beneficial rather than source-bound, but the provider quota failure is a hard SOURCE/provider guard for this benchmark window.

### Cross-Movie Interpretation

- 6 remains a strong first promotion target across local LOTR, the small cloud e2e run, and this full cloud attempt.
- The new Green Mile full matrix suggests some cloud titles plateau earlier, around 7-8, even when source reads are healthy.
- Avatar remains the heavier cloud sample: successful rows improved through 9 here, and the earlier small run improved through 12.
- Odd thread counts matter: 5 was a large improvement over 4 for both cloud titles; 7 and 9 were meaningful for Avatar, while Green Mile flattened after 7-8.
- 10 remains a reasonable conservative high-performance shadow ceiling from the combined local LOTR plus Avatar evidence, but this run does not strengthen the case for making 12 default.
- 12 should remain experimental/configurable until a quota-clean full repeat captures Avatar 10-12 and additional titles.
- Cloud behavior differs from local behavior because provider/auth/quota can become the hard limiter independently of CPU/RAM. Adaptive promotion must keep provider/SOURCE_BOUND guards ahead of CPU-bound promotion.

### Policy Implication

- Do not change real playback behavior from this benchmark.
- Do not change adaptive policy solely from this partial full matrix.
- Keep 6 as the safest first CPU-bound promotion target.
- Keep 10 as the conservative shadow high-performance ceiling for now.
- Treat 12 as experimental until more full-repeat data exists.
- For cloud media, provider/auth/quota failures must block thread promotion even if prior rows showed CPU/thread scaling.
