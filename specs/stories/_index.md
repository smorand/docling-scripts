# User Stories Index

> Source Specification: specs/20260923-205758-docconvert-simplification-and-multimedia-pipeline.md
> Nature: FEAT
> Depth: L
> Generated on: 2026-09-23
> Target tier: 2 (standard frontier), resolved from default
> Total: 5 stories in 0 epics

## Slicing Verdict
| Verdict | SLICEABLE |
|---|---|
| Epics refused at this tier | none |
| Carried drift entries | none |

## Implementation Order
| Order | ID | Epic | Title | FRs | Scenarios | Tests | Files | Depends On | min_tier | Status |
|-------|----|------|-------|-----|-----------|-------|-------|------------|----------|--------|
| 1 | US-001 | n/a | Simplification, Code Cleanup and Unified Configuration | FR-DEL-001, FR-DEL-002, FR-DEL-003, FR-MOD-003 | Unit (Flags, Config) | TEST-001, TEST-002, TEST-003 | 5 | none | 2 | todo |
| 2 | US-002 | n/a | Recursive EML/MSG Attachment Pipeline for Audio and Video | FR-MOD-001, FR-MOD-002 | E2E-001, EXC-004 | E2E-001, TEST-004 | 3 | US-001 | 2 | todo |
| 3 | US-003 | n/a | Outlook Calendar Context Enrichment for Audio Meetings | FR-NEW-007, FR-NEW-008, FR-NEW-009 | E2E-003, EXC-003 | E2E-003, TEST-005, TEST-006 | 3 | US-001 | 2 | todo |
| 4 | US-004 | n/a | Video Frame Sampling, Deduplication and Chronological Timeline | FR-NEW-001, FR-NEW-002, FR-NEW-003, FR-NEW-004 | E2E-002, EXC-001, EXC-002 | E2E-002, TEST-007, TEST-008, TEST-009 | 4 | US-001 | 2 | todo |
| 5 | US-005 | n/a | Instagram Media Ingestion and Video Pipeline Routing | FR-NEW-005, FR-NEW-006 | E2E-004 | E2E-004, TEST-010, TEST-011 | 3 | US-001, US-004 | 2 | todo |

## Dependency Graph
```text
US-001 (Simplification & Config)
  │
  ├──> US-002 (EML/MSG Audio/Video Recursivity)
  │
  ├──> US-003 (Outlook Calendar Enrichment)
  │
  └──> US-004 (Video Sampling & Timeline)
         │
         └──> US-005 (Instagram Media Ingestion)
```

## Coverage Verification (Phase 5 gate)
- Requirements in spec (`FR-`/`BR-`/`DR-`): 15 | assigned: 15 | unassigned: none
- Tests in spec (`E2E-`/`BT-`/`DT-`): 4 | assigned: 4 | unassigned: none
- Scenarios in spec: 4 | covered: 4 | uncovered: none
- SC-orphan FRs homed: FR-DEL-001 (US-001), FR-DEL-002 (US-001), FR-DEL-003 (US-001), FR-MOD-003 (US-001)
- Matrix-unassigned tests homed: 3 unit test criteria (CLI flags rejection in US-001, config loading in US-001, 120-frame adaptation cap in US-004)
