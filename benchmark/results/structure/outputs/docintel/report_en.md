<!-- page: 1 -->

# Quarterly Operations Report

This report summarizes the document processing platform during the third quarter. It covers volume, regional teams and the incidents that affected availability.

## 1. Summary

The platform processed more documents than in any previous quarter while keeping the failure rate below the agreed service level.

- Processed 1.2 million pages across all tenants
- Median time to first result fell to 4.1 seconds
- Two critical incidents were resolved within the target window
- Storage costs decreased by 12 percent after lifecycle rules

## 2. Processing volume

### 2.1 Documents by type

| Type | Documents | Pages | Avg time (s) |
|---|---|---|---|
| PDF (digital) | 182,400 | 951,220 | 0.8 |
| PDF (scanned) | 41,730 | 228,915 | 38.2 |
| Images | 65,110 | 65,110 | 1.4 |
| Word | 22,905 | 110,340 | 0.3 |
| Spreadsheets | 8,412 | 20,101 | 0.5 |
| Email | 15,066 | 15,066 | 0.2 |

Scanned PDFs remain the most expensive category because every page requires OCR.

### 2.2 Regional teams

| Region | Lead | Headcount | Budget (USD) |
|---|---|---|---|
| North America | A. Johnson | 14 | 1,240,000 |
| Latin America | M. Hernández | 9 | 610,000 |
| Europe | K. Müller | 11 | 980,000 |
| Asia Pacific | S. Tanaka | 7 | 540,000 |

## 3. Incident log

The following table lists every incident opened during the quarter, ordered by identifier.

| ID | Date | Component | Severity | Resolution |
|---|---|---|---|---|
| INC-1001 | 2026-02-08 | Object storage | Low | Replaced disk |
| INC-1002 | 2026-03-15 | API gateway | Medium | Re-queued jobs |
| INC-1003 | 2026-04-22 | API gateway | Low | Rotated credentials |
| INC-1004 | 2026-05-02 | Database | Low | Updated certificate |
| INC-1005 | 2026-06-09 | Object storage | Critical | Patched parser |
| INC-1006 | 2026-07-16 | Broker | Critical | Updated certificate |

<!-- page: 2 -->

| ID | Date | Component | Severity | Resolution |
|---|---|---|---|---|
| INC-1007 | 2026-08-23 | OCR worker | Critical | Restarted service |
| INC-1008 | 2026-09-03 | API gateway | Medium | Updated certificate |
| INC-1009 | 2026-01-10 | OCR worker | High | Patched parser |
| INC-1010 | 2026-02-17 | Database | High | Replaced disk |
| INC-1011 | 2026-03-24 | Scheduler | Medium | Updated certificate |
| INC-1012 | 2026-04-04 | OCR worker | High | Patched parser |
| INC-1013 | 2026-05-11 | Object storage | Critical | Replaced disk |
| INC-1014 | 2026-06-18 | API gateway | Low | Re-queued jobs |
| INC-1015 | 2026-07-25 | Object storage | Medium | Patched parser |
| INC-1016 | 2026-08-05 | Object storage | Medium | Scaled workers |
| INC-1017 | 2026-09-12 | Scheduler | High | Replaced disk |
| INC-1018 | 2026-01-19 | Scheduler | Critical | Re-queued jobs |
| INC-1019 | 2026-02-26 | Scheduler | Critical | Scaled workers |
| INC-1020 | 2026-03-06 | Object storage | Critical | Scaled workers |
| INC-1021 | 2026-04-13 | API gateway | High | Restarted service |
| INC-1022 | 2026-05-20 | API gateway | Medium | Increased memory limit |
| INC-1023 | 2026-06-27 | API gateway | Low | Replaced disk |
| INC-1024 | 2026-07-07 | Database | Critical | Re-queued jobs |
| INC-1025 | 2026-08-14 | Database | Low | Patched parser |
| INC-1026 | 2026-09-21 | OCR worker | High | Scaled workers |
| INC-1027 | 2026-01-01 | API gateway | Critical | Replaced disk |
| INC-1028 | 2026-02-08 | Database | Low | Rotated credentials |
| INC-1029 | 2026-03-15 | Database | Low | Increased memory limit |
| INC-1030 | 2026-04-22 | Broker | High | Patched parser |
| INC-1031 | 2026-05-02 | Database | Medium | Updated certificate |
| INC-1032 | 2026-06-09 | Broker | High | Re-queued jobs |
| INC-1033 | 2026-07-16 | Scheduler | Low | Patched parser |
| INC-1034 | 2026-08-23 | Object storage | High | Patched parser |
| INC-1035 | 2026-09-03 | Database | Low | Increased memory limit |
| INC-1036 | 2026-01-10 | Broker | High | Restarted service |
| INC-1037 | 2026-02-17 | Scheduler | Low | Restarted service |
| INC-1038 | 2026-03-24 | OCR worker | Medium | Patched parser |
| INC-1039 | 2026-04-04 | Object storage | Low | Updated certificate |
| INC-1040 | 2026-05-11 | Broker | High | Restarted service |
| INC-1041 | 2026-06-18 | Scheduler | Medium | Rotated credentials |
| INC-1042 | 2026-07-25 | Database | Low | Patched parser |
| INC-1043 | 2026-08-05 | API gateway | Medium | Scaled workers |
| INC-1044 | 2026-09-12 | API gateway | Low | Increased memory limit |
| INC-1045 | 2026-01-19 | OCR worker | High | Scaled workers |

<!-- page: 3 -->

| ID | Date | Component | Severity | Resolution |
|---|---|---|---|---|
| INC-1046 | 2026-02-26 | OCR worker | Critical | Updated certificate |
| INC-1047 | 2026-03-06 | OCR worker | Critical | Scaled workers |
| INC-1048 | 2026-04-13 | OCR worker | Medium | Scaled workers |
| INC-1049 | 2026-05-20 | Scheduler | Medium | Scaled workers |
| INC-1050 | 2026-06-27 | Object storage | Low | Scaled workers |
| INC-1051 | 2026-07-07 | Object storage | Low | Increased memory limit |
| INC-1052 | 2026-08-14 | API gateway | Medium | Re-queued jobs |
| INC-1053 | 2026-09-21 | Broker | High | Restarted service |
| INC-1054 | 2026-01-01 | Object storage | High | Scaled workers |
| INC-1055 | 2026-02-08 | Object storage | Critical | Rotated credentials |
| INC-1056 | 2026-03-15 | API gateway | High | Replaced disk |
| INC-1057 | 2026-04-22 | OCR worker | High | Replaced disk |
| INC-1058 | 2026-05-02 | Object storage | Low | Patched parser |
| INC-1059 | 2026-06-09 | API gateway | Low | Re-queued jobs |
| INC-1060 | 2026-07-16 | Scheduler | High | Rotated credentials |

## 4. Next steps

- Split large PDFs into page-range subtasks
- Offer Markdown and chunked output for retrieval pipelines
- Add antivirus scanning before processing

The operations team will review these actions at the next monthly meeting.