# US-002: Recursive EML/MSG Attachment Pipeline for Audio and Video

> Parent Spec: specs/20260923-205758-docconvert-simplification-and-multimedia-pipeline.md
> Epic: n/a
> Status: ready
> Priority: 2
> Depends On: US-001
> Complexity: M
> min_tier: 2
> Files touched: 3

## Objective
Enable recursive conversion of audio and video attachments in EML and MSG emails. Audio and video files are extracted to the attachments directory, processed with MediaConverter, and linked in the parent email markdown report.

## Technical Context

### Stack
Python 3.10+, email standard library, extract_msg, OpenTelemetry tracing.

### Relevant File Structure
```text
src/
└── doc_convert/
    ├── recursive.py
    └── converters/
        ├── eml.py
        └── msg.py
tests/
└── test_recursive_media.py
```

### Existing Patterns
`src/doc_convert/recursive.py` detects file types and dispatches attachments to appropriate converters (e.g., DoclingConverter for PDF/Office). `eml.py` and `msg.py` parse email headers, bodies, and write attachments under `attachments/`.

### Data Model (excerpt)
Output directory layout for email attachments:
```text
<email_name>_docling/
├── document.md
└── attachments/
    ├── voice_note.mp3
    └── voice_note_docling/
        └── document.md
```

### Decisions That Governed This Story
None specific beyond standard architecture (Approche A).

### Applicable NFRs
- `NFR-004`: "Chaque étape nouvelle (recursive.audio, recursive.video) SHALL émettre un span OpenTelemetry structuré conforme à src/tracing.py."
- `NFR-005`: "Le projet SHALL satisfaire ruff check sans avertissement, mypy en mode strict, et 100% de succès sur la suite pytest."

### Bounded Context
Email Conversion and Recursive Attachment Pipeline.

## Functional Requirements

### FR-MOD-001: Route Audio and Video Attachments to MediaConverter
- **EARS:** WHEN `recursive.py` rencontre une pièce jointe de format audio (`.mp3`, `.wav`, `.m4a`, `.ogg`) ou vidéo (`.mp4`, `.mov`), le système SHALL instancier `MediaConverter` et convertir le média sous `attachments/<nom>_docling/`.
- **Inputs / Outputs:** Fichier binaire de pièce jointe extrait; sous-dossier `attachments/<nom>_docling/document.md` généré.
- **Business Rules:**
  - Nettoyer systématiquement les noms de fichiers pour éviter toute traversée de chemin (Section 11).
  - Enregistrer un span OpenTelemetry `recursive.audio` ou `recursive.video`.
  - En cas d'échec ou de fichier illisible (EXC-004), laisser le fichier brut sous `attachments/` et consigner l'échec sans interrompre la conversion de l'email parent.

### FR-MOD-002: Link Media Transcripts in Parent Markdown Document
- **EARS:** WHEN la conversion d'une pièce jointe audio ou vidéo réussit, le système SHALL insérer son compte rendu dans la section `## Attachments` du courriel parent avec son lien relatif.
- **Inputs / Outputs:** Chemin relatif du compte rendu enfant généré; section `## Attachments` du `document.md` parent mise à jour.
- **Business Rules:** Le lien relatif pointe vers `attachments/<nom>_docling/document.md`. Si la conversion a échoué, mentionner explicitement que la pièce jointe n'a pas pu être convertie.

## Acceptance Tests

### Test Data
| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `email_with_audio.eml` | Courriel EML contenant une pièce jointe audio voice_note.mp3 | Fixture de test | ready |
| `email_with_corrupt_media.eml` | Courriel contenant une pièce jointe audio chiffrée ou corrompue | Fixture de test | ready |

### E2E-001: Email Recursive Conversion with Audio Attachment
- **Category:** happy
- **Scenario:** Traitement d'un courriel EML avec pièce jointe audio
- **Requirements:** FR-MOD-001, FR-MOD-002
- **Preconditions:** Environnement avec `MediaConverter` opérationnel
- **Steps:**
  - Given: Un fichier `email_with_audio.eml` contenant `voice_note.mp3`
  - When: `doc-convert email_with_audio.eml` est exécuté
  - Then: Le dossier `email_with_audio_docling/attachments/voice_note_docling/document.md` est créé
  - And: Le fichier `email_with_audio_docling/document.md` contient une section `## Attachments`
  - And: La section `## Attachments` contient un lien vers `attachments/voice_note_docling/document.md`
  - And: Un span OpenTelemetry `recursive.audio` est émis
- **Cleanup:** Suppression du dossier généré
- **Priority:** Critical

### TEST-004: Unreadable or Corrupt Attachment Resilience (EXC-004)
- **Category:** edge
- **Scenario:** Pièce jointe audio ou vidéo corrompue dans un courriel
- **Requirements:** FR-MOD-001, FR-MOD-002, EXC-004
- **Preconditions:** Fichier EML avec pièce jointe illisible
- **Steps:**
  - Given: Un fichier EML contenant une pièce jointe `corrupt.mp3` invalide
  - When: Le courriel est converti par le système
  - Then: Le fichier brut reste présent sous `attachments/corrupt.mp3`
  - And: La conversion du courriel parent réussit sans exception non gérée
  - And: Le document parent indique que la pièce jointe n'a pas pu être convertie
- **Cleanup:** Suppression du dossier temporaire
- **Priority:** Medium

## Constraints

### Files Not to Touch
- `src/video.py`
- `src/config.py`
- `src/doc_convert/cli.py`

### Dependencies Not to Add
- Aucune nouvelle dépendance externe; utiliser `MediaConverter` et les analyseurs EML/MSG existants.

### Patterns to Avoid
- Ne pas lever d'exception non interceptée sur un échec de pièce jointe individuelle; consigner l'erreur et poursuivre.
- Ne pas utiliser de chemins absolus dans les liens markdown de la section `## Attachments`.

### Scope Boundary
- Couvre uniquement l'intégration récursive d'audio et vidéo dans EML et MSG. La logique interne de capture vidéo ou de calendrier n'est pas modifiée ici.

## Non Regression

### Existing Tests That Must Pass
- `tests/test_recursive.py` pour les pièces jointes PDF, Word et images.
- Tests d'extraction EML et MSG existants.

### Behaviors That Must Not Change
- Le traitement des pièces jointes documentaires (PDF, DOCX, XLSX) reste strictement identique.

### API Contracts to Preserve
- Signature et contrat de retour de `recursive_convert_attachments` dans `src/doc_convert/recursive.py`.

## Self-Review Checklist
Full 4-axis self-review per /implement Phase 3.3 Step 5.
