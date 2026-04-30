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
