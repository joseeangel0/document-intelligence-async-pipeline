# Platform Migration Plan

This document describes how the document intelligence platform moves to the new cluster.

## Goals

* Zero lost jobs during the migration
* Less than five minutes of upload downtime
* Keep every stored document and result

## Timeline

|  |  |  |  |
| --- | --- | --- | --- |
| Phase | Start | End | Owner |
| Preparation | 2026-10-01 | 2026-10-07 | Platform team |
| Data copy | 2026-10-08 | 2026-10-10 | Storage team |
| Cut-over | 2026-10-11 | 2026-10-11 | On-call engineer |
| Clean-up | 2026-10-12 | 2026-10-20 | Platform team |

### Rollback

If the error rate doubles after the cut-over, traffic returns to the old cluster within ten minutes.

## Risks

|  |  |  |
| --- | --- | --- |
| Risk | Likelihood | Mitigation |
| Queue backlog | Medium | Scale OCR workers before cut-over |
| Certificate expiry | Low | Renew certificates one week earlier |