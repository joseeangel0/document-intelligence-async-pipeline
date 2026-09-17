# Load test results (2026-09-16 23:12)

* Documents uploaded: **122** by 16 parallel clients; accepted **122** in 1.0 s (upload latency p50 116 ms, p95 238 ms).
* Terminal states: {'SUCCEEDED': 122}; still pending after the deadline: **0**.
* All jobs drained in **50 s**: 175 pages, 28,133 LLM tokens, throughput 145 documents/min.

| Queue | Jobs | Wait p50 (s) | Wait p95 (s) | Wait max (s) | Mean processing (s) | Peak depth |
|---|---|---|---|---|---|---|
| heavy | 59 | 35.3 | 39.5 | 41.8 | 1.46 | 57 |
| light | 63 | 0.6 | 0.8 | 0.8 | 0.07 | 49 |
