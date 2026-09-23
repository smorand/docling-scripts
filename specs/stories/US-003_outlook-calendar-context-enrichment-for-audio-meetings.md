# US-003: Outlook Calendar Context Enrichment for Audio Meetings

> Parent Spec: specs/20260923-205758-docconvert-simplification-and-multimedia-pipeline.md
> Epic: n/a
> Status: ready
> Priority: 3
> Depends On: US-001
> Complexity: M
> min_tier: 2
> Files touched: 3

## Objective
Enrich audio meeting transcriptions by retrieving calendar metadata from Outlook via `outlook-tool`. When a matching calendar event is found, inject verified attendee names, meeting subject, and agenda context into the transcription prompt.

## Technical Context

### Stack
Python 3.10+, subprocess, JSON parsing, OpenTelemetry tracing.

### Relevant File Structure
```text
src/
└── doc_convert/
    ├── calendar.py
    └── converters/
        └── media.py
tests/
└── test_calendar_context.py
```

### Existing Patterns
`src/doc_convert/converters/media.py` orchestrates audio transcription and diarization via LLM providers using system prompts. Subprocess execution follows patterns in `src/audio.py`.

### Data Model (excerpt)
Calendar configuration loaded from `config.yaml`:
```yaml
calendar:
  enabled: true
  lookup_window_minutes: 60
```
Calendar event structure parsed from `outlook-tool cal list --json`:
```json
[
  {
    "id": "AAMk...",
    "subject": "Point d'architecture Docling",
    "start": "2026-09-23T14:00:00",
    "end": "2026-09-23T15:00:00",
    "attendees": ["Sebastien Morand <sebastien.morand@ibm.com>", "Alice Durand <alice@example.com>"],
    "body": "Ordre du jour: simplification CLI et pipeline multimédia."
  }
]
```

### Decisions That Governed This Story
- `DEC-001`: "Choix de l'Approche A (sous-processus UNIX légers) pour mcp-instagram et outlook-tool plutôt qu'embarquer de lourds SDK Python, préservant la légèreté du projet et son isolation face aux pannes."

### Applicable NFRs
- `NFR-003`: "L'absence de mcp-instagram ou de outlook-tool dans l'environnement SHALL générer un message clair sans provoquer de traceback Python non géré."
- `NFR-004`: "Chaque étape nouvelle (calendar.lookup) SHALL émettre un span OpenTelemetry structuré conforme à src/tracing.py."
- `NFR-005`: "Le projet SHALL satisfaire ruff check sans avertissement, mypy en mode strict, et 100% de succès sur la suite pytest."

### Bounded Context
Calendar Integration and Audio Context Enrichment.

## Functional Requirements

### FR-NEW-007: Query Calendar for Audio Time Slot
- **EARS:** WHEN un fichier audio est soumis, le système SHALL extraire sa date de création ou de modification et interroger l'agenda via `outlook-tool cal list --start <date_debut> --end <date_fin> --json`.
- **Inputs / Outputs:** Fichier audio avec horodatage système; commande sous-processus `outlook-tool cal list --start ... --end ... --json` invoquée; liste d'événements JSON en sortie.
- **Business Rules:**
  - Définir la plage de recherche à partir de la date du fichier et de `lookup_window_minutes` (par défaut 60 minutes).
  - Émettre un span OpenTelemetry `calendar.lookup`.

### FR-NEW-008: Inject Calendar Context into Transcription Prompt
- **EARS:** WHEN un événement calendaire correspond au créneau horaire du fichier audio, le système SHALL injecter le sujet, le corps de réunion et la liste des participants vérifiés dans le prompt système de transcription.
- **Inputs / Outputs:** Événement sélectionné; prompt de transcription enrichi avec les noms des participants et le contexte métier.
- **Business Rules:**
  - En cas de conflit avec plusieurs réunions se chevauchant (EXC-003), sélectionner la réunion dont le titre présente la plus forte similarité lexicale avec le nom du fichier audio.
  - Conformément à la section 11, les données d'agenda ne sont utilisées que dans le prompt éphémère et le document local, sans journalisation en clair dans les logs ou les traces.

### FR-NEW-009: Graceful Fallback on Calendar Failure or Absence
- **EARS:** IF l'interrogation de `outlook-tool` échoue ou ne renvoie aucun événement, THEN le système SHALL continuer la transcription standard sans interrompre le processus.
- **Inputs / Outputs:** Erreur sous-processus ou liste vide en entrée; poursuite transparente de la transcription standard en sortie.
- **Business Rules:**
  - Si l'outil `outlook-tool` est absent du PATH (FileNotFoundError), logger un message d'information sans traceback et poursuivre (NFR-003).

## Acceptance Tests

### Test Data
| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `meeting.ogg` | Fichier audio avec date de modification fixée | Fixture de test | ready |
| `calendar_mock.json` | Réponse JSON simulée de `outlook-tool cal list --json` | Fixture de test | ready |

### E2E-003: Audio Transcription with Calendar Context Enrichment
- **Category:** happy
- **Scenario:** Traitement d'un fichier audio correspondant à un événement du calendrier
- **Requirements:** FR-NEW-007, FR-NEW-008
- **Preconditions:** Mock de `outlook-tool cal list --json` renvoyant `calendar_mock.json`
- **Steps:**
  - Given: Un fichier audio `meeting.ogg` horodaté au 2026-09-23 à 14:15
  - When: `doc-convert meeting.ogg` est exécuté
  - Then: La commande `outlook-tool cal list` est appelée avec la plage couvrant l'horodatage
  - And: Le sujet et les participants sont injectés dans le prompt envoyé au modèle de transcription
  - And: Le document markdown final inclut les participants identifiés
  - And: Un span OpenTelemetry `calendar.lookup` est enregistré
- **Cleanup:** Suppression du dossier temporaire
- **Priority:** Critical

### TEST-005: Overlapping Calendar Events Resolution (EXC-003)
- **Category:** edge
- **Scenario:** Deux réunions présentes sur le même créneau horaire
- **Requirements:** FR-NEW-008, EXC-003
- **Preconditions:** Réponse JSON avec 2 réunions chevauchantes
- **Steps:**
  - Given: Deux réunions au même horaire ("Point Architecture Docling" et "Synchronisation Commerciale")
  - When: Le fichier `docling_architecture_sync.mp3` est analysé
  - Then: Le système sélectionne la réunion "Point Architecture Docling" par similarité lexicale
  - And: Les participants de cette réunion sont injectés dans le prompt
- **Cleanup:** Aucun
- **Priority:** Medium

### TEST-006: Missing Tool or Calendar Failure Resilience
- **Category:** failure
- **Scenario:** `outlook-tool` introuvable ou retournant une erreur
- **Requirements:** FR-NEW-009, NFR-003
- **Preconditions:** Environnement où `outlook-tool` lève `FileNotFoundError` ou code 1
- **Steps:**
  - Given: Un binaire `outlook-tool` absent ou défaillant
  - When: Un fichier audio est converti
  - Then: Un message d'avertissement clair est émis sans traceback
  - And: La transcription standard continue et réussit normalement
- **Cleanup:** Suppression du dossier temporaire
- **Priority:** High

## Constraints

### Files Not to Touch
- `src/video.py`
- `src/doc_convert/recursive.py`
- `src/doc_convert/formats.py`

### Dependencies Not to Add
- Ne pas ajouter de SDK Azure ou Microsoft Graph direct en dépendance Python.

### Patterns to Avoid
- Ne pas bloquer la transcription en cas d'erreur réseau ou calendrier.
- Ne pas consigner les courriels des participants en clair dans les spans OpenTelemetry (Section 11).

### Scope Boundary
- Uniquement la recherche d'événements calendrier pour enrichir le prompt de transcription audio.

## Non Regression

### Existing Tests That Must Pass
- `tests/test_media.py` pour la transcription audio sans calendrier.
- Tests audio existants dans `tests/test_audio.py`.

### Behaviors That Must Not Change
- La conversion de fichiers audio simples sans correspondance de calendrier reste identique à l'existant.

### API Contracts to Preserve
- Méthode `convert` de `MediaConverter`.

## Self-Review Checklist
Full 4-axis self-review per /implement Phase 3.3 Step 5.
