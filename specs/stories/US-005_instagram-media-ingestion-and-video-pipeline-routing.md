# US-005: Instagram Media Ingestion and Video Pipeline Routing

> Parent Spec: specs/20260923-205758-docconvert-simplification-and-multimedia-pipeline.md
> Epic: n/a
> Status: ready
> Priority: 5
> Depends On: US-001, US-004
> Complexity: M
> min_tier: 2
> Files touched: 3

## Objective
Enable processing of Instagram publications and reels by URL. The CLI invokes `mcp-instagram dl --json` via subprocess, extracts the video and post caption, prepends the caption to the markdown preamble, and passes the media to the video sampling and transcription pipeline.

## Technical Context

### Stack
Python 3.10+, regex validation, subprocess, JSON parsing, OpenTelemetry tracing.

### Relevant File Structure
```text
src/
└── doc_convert/
    ├── instagram.py
    ├── base.py
    └── converters/
        └── media.py
tests/
└── test_instagram.py
```

### Existing Patterns
YouTube URL handling in `src/doc_convert/converters/media.py` / `src/video.py` uses `yt-dlp` via subprocess and delegates the resulting video to the conversion pipeline.

### Data Model (excerpt)
JSON output structure from `mcp-instagram dl --json`:
```json
{
  "status": "success",
  "media_path": "/path/to/downloaded_reel.mp4",
  "caption": "Texte explicatif du post Instagram avec hashtags #ai #architecture",
  "author": "docling_official",
  "timestamp": "2026-09-23T10:00:00"
}
```

### Decisions That Governed This Story
- `DEC-001`: "Choix de l'Approche A (sous-processus UNIX légers) pour mcp-instagram et outlook-tool plutôt qu'embarquer de lourds SDK Python, préservant la légèreté du projet et son isolation face aux pannes."

### Applicable NFRs
- `NFR-003`: "L'absence de mcp-instagram ou de outlook-tool dans l'environnement SHALL générer un message clair sans provoquer de traceback Python non géré."
- `NFR-004`: "Chaque étape nouvelle (instagram.download) SHALL émettre un span OpenTelemetry structuré conforme à src/tracing.py."
- `NFR-005`: "Le projet SHALL satisfaire ruff check sans avertissement, mypy en mode strict, et 100% de succès sur la suite pytest."

### Bounded Context
External Media Ingestion and Social Media Connectors.

## Functional Requirements

### FR-NEW-005: Detect Instagram URLs and Download via mcp-instagram
- **EARS:** WHEN l'argument d'entrée correspond à une URL Instagram (contenant `instagram.com/reel/`, `/p/` ou `/tv/`), le système SHALL appeler `mcp-instagram dl --json` pour rapatrier le fichier et la légende textuelle.
- **Inputs / Outputs:** URL Instagram en entrée; fichier vidéo local et métadonnées JSON en sortie.
- **Business Rules:**
  - Valider l'URL par une expression régulière stricte pour interdire toute injection de commande (Section 11).
  - Émettre le span OpenTelemetry `instagram.download` avec l'URL et le statut du téléchargement.
  - En cas d'outil `mcp-instagram` absent du système, émettre un message d'erreur clair sans traceback non géré (NFR-003).

### FR-NEW-006: Ingest Caption and Route to Video Pipeline
- **EARS:** WHEN le média Instagram est récupéré, le système SHALL inclure la légende d'origine dans le préambule de `document.md` et transmettre la vidéo au pipeline d'analyse complet.
- **Inputs / Outputs:** Fichier vidéo téléchargé et texte de la légende; document markdown produit par le pipeline vidéo avec la légende en préambule.
- **Business Rules:**
  - La légende originale doit figurer dans un bloc de métadonnées ou préambule en tête de `document.md`.
  - La vidéo est ensuite traitée avec l'échantillonnage de trames, la déduplication et l'entrelacement chronologique implémentés dans US-004.

## Acceptance Tests

### Test Data
| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `instagram_output_mock.json` | Mock JSON de sortie pour `mcp-instagram dl --json` | Fixture de test | ready |
| `reel_sample.mp4` | Vidéo téléchargée factice | Fixture de test | ready |

### E2E-004: Instagram URL Ingestion and Conversion
- **Category:** happy
- **Scenario:** Traitement d'une URL de reel Instagram
- **Requirements:** FR-NEW-005, FR-NEW-006
- **Preconditions:** Mock de `mcp-instagram dl --json` retournant `instagram_output_mock.json`
- **Steps:**
  - Given: Une URL `https://www.instagram.com/reel/DA12345/`
  - When: `doc-convert https://www.instagram.com/reel/DA12345/` est exécuté
  - Then: L'URL est validée par regex et transmise à `mcp-instagram dl --json`
  - And: La légende d'origine est extraite et insérée dans le préambule de `document.md`
  - And: La vidéo est transmise à `MediaConverter` pour extraction d'images et transcription
  - And: Un span OpenTelemetry `instagram.download` est émis
- **Cleanup:** Suppression des fichiers générés
- **Priority:** Critical

### TEST-010: Invalid URL Rejection and Command Injection Protection
- **Category:** failure
- **Scenario:** URL Instagram malformée ou suspecte
- **Requirements:** FR-NEW-005, Section 11
- **Preconditions:** Chaîne d'entrée contenant des caractères d'injection shell
- **Steps:**
  - Given: Une entrée `https://www.instagram.com/reel/123;rm -rf /`
  - When: Le système analyse l'entrée
  - Then: L'URL est rejetée par le validateur regex
  - And: Aucun sous-processus n'est exécuté
  - And: Un code d'erreur explicite est renvoyé
- **Cleanup:** Aucun
- **Priority:** High

### TEST-011: Missing mcp-instagram Binary Resilience
- **Category:** failure
- **Scenario:** Binaire `mcp-instagram` non présent sur le système
- **Requirements:** FR-NEW-005, NFR-003
- **Preconditions:** Environnement avec `mcp-instagram` absent du PATH
- **Steps:**
  - Given: Une URL Instagram valide mais aucun binaire `mcp-instagram` disponible
  - When: L'utilisateur lance la conversion
  - Then: Le système affiche un message indiquant que `mcp-instagram` est requis pour ce format
  - And: Le programme se termine proprement sans afficher de traceback Python
- **Cleanup:** Aucun
- **Priority:** High

## Constraints

### Files Not to Touch
- `src/doc_convert/converters/eml.py`
- `src/doc_convert/calendar.py`
- `src/config.py`

### Dependencies Not to Add
- Ne pas ajouter de librairie cliente Instagram non officielle en dépendance Python.

### Patterns to Avoid
- Ne pas exécuter de chaîne de commande brute via `shell=True` dans `subprocess`.
- Ne pas tenter de scraping web direct des pages Instagram sans passer par `mcp-instagram`.

### Scope Boundary
- Couvre uniquement la détection d'URL Instagram, l'appel sécurisé à `mcp-instagram dl`, l'extraction de la légende et le routage vers le pipeline vidéo.

## Non Regression

### Existing Tests That Must Pass
- `tests/test_youtube.py` pour la prise en charge des URL YouTube existantes.
- Tests du convertisseur média `tests/test_media.py`.

### Behaviors That Must Not Change
- Le traitement des URL YouTube et des fichiers locaux reste inchangé.

### API Contracts to Preserve
- Interface CLI principale `doc-convert <source>`.

## Self-Review Checklist
Full 4-axis self-review per /implement Phase 3.3 Step 5.
