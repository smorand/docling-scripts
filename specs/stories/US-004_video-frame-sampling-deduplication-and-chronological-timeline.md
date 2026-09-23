# US-004: Video Frame Sampling, Deduplication and Chronological Timeline

> Parent Spec: specs/20260923-205758-docconvert-simplification-and-multimedia-pipeline.md
> Epic: n/a
> Status: ready
> Priority: 4
> Depends On: US-001
> Complexity: L
> min_tier: 2
> Files touched: 4

## Objective
Implement regular keyframe sampling for videos using ffmpeg with adaptive interval capping at 120 images per 30-minute block. Filter consecutive perceptual duplicates, obtain timestamped visual descriptions, and merge visual scene descriptions with audio transcription into a unified chronological markdown log.

## Technical Context

### Stack
Python 3.10+, ffmpeg / ffprobe, dataclasses, vision LLM, OpenTelemetry tracing.

### Relevant File Structure
```text
src/
├── video.py
└── doc_convert/
    ├── markdown.py
    └── converters/
        └── media.py
tests/
└── test_video_sampling.py
```

### Existing Patterns
`src/video.py` wraps video duration extraction and chunking. `src/doc_convert/converters/media.py` dispatches media to LLM providers. `src/doc_convert/markdown.py` formats conversion outputs.

### Data Model (excerpt)
Dataclass for video frame captures:
```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class VideoFrame:
    path: Path
    timestamp_seconds: float
    formatted_time: str
    description: str = ""
```
Video configuration loaded from `config.yaml`:
```yaml
video:
  sample_interval_seconds: 15
  max_frames_per_chunk: 120
```

### Decisions That Governed This Story
- `DEC-002`: "Rejet de l'Approche C (vidéo sans échantillonnage d'images) suite aux benchmarks confirmant les hallucinations et sauts de contexte des descriptions visuelles non contrôlées dans le temps."
- `DEC-004`: "Plafonnement de l'échantillonnage vidéo à 120 images par tranche de 30 minutes pour prévenir la saturation disque et maîtriser les coûts d'appels API vision."

### Applicable NFRs
- `NFR-001`: "L'extraction des images clés d'une tranche vidéo de 30 minutes via ffmpeg SHALL s'exécuter en moins de 10 secondes sur Apple Silicon."
- `NFR-002`: "L'empreinte mémoire RSS du processus pendant l'échantillonnage et la légende vidéo SHALL rester inférieure à 2 Go."
- `NFR-004`: "Chaque étape nouvelle (video.sample_frames, video.caption_frames) SHALL émettre un span OpenTelemetry structuré conforme à src/tracing.py."
- `NFR-005`: "Le projet SHALL satisfaire ruff check sans avertissement, mypy en mode strict, et 100% de succès sur la suite pytest."

### Bounded Context
Video Extraction, Vision Sampling and Multimodal Timeline.

## Functional Requirements

### FR-NEW-001: Extract Regular Keyframes via ffmpeg
- **EARS:** WHEN une vidéo (locale ou issue d'un téléchargement) est traitée, le système SHALL extraire des images clés à intervalle régulier via `ffmpeg` dans le dossier temporaire ou sous-dossier `frames/`.
- **Inputs / Outputs:** Fichier vidéo local en entrée; fichiers images PNG/JPEG stockés sous `frames/` avec horodatages.
- **Business Rules:**
  - Respecter `sample_interval_seconds` (par défaut 15 secondes) configuré dans `config.yaml`.
  - Émettre le span OpenTelemetry `video.sample_frames` avec les attributs de durée et de nombre d'images.
  - L'extraction doit respecter le critère de performance NFR-001.

### FR-NEW-002: Dynamic Interval Capping at 120 Frames per 30-Minute Chunk
- **EARS:** IF le nombre d'images calculé pour un bloc de 30 minutes dépasse 120 images, THEN le système SHALL ajuster automatiquement l'intervalle pour plafonner l'échantillonnage à 120 images maximum par bloc.
- **Inputs / Outputs:** Durée du segment vidéo et intervalle initial en entrée; intervalle ajusté et nombre de captures limité à 120 au maximum en sortie.
- **Business Rules:**
  - Si `chunk_duration / interval > 120`, nouvel intervalle = `chunk_duration / 120`.

### FR-NEW-003: Perceptual Deduplication and Vision Captioning
- **EARS:** WHEN les images sont extraites, le système SHALL filtrer les doublons perceptuels consécutifs et soumettre chaque image retenue au modèle de vision avec son horodatage exact.
- **Inputs / Outputs:** Séquence d'instances `VideoFrame`; liste filtrée enrichie avec `description` générée par l'API de vision.
- **Business Rules:**
  - En cas de plan fixe ou vidéo statique (EXC-002), éliminer les images quasi identiques consécutives pour n'envoyer qu'une seule requête de légende.
  - Émettre le span OpenTelemetry `video.caption_frames` avec le nombre d'appels vision effectués.
  - Maintenir l'empreinte mémoire sous 2 Go (NFR-002).

### FR-NEW-004: Unified Chronological Markdown Timeline
- **EARS:** WHEN les descriptions d'images et la transcription audio sont produites, le système SHALL générer un journal chronologique unifié dans `document.md` associant les propos tenus aux éléments visibles à l'écran.
- **Inputs / Outputs:** Segments de transcription audio horodatés et captures `VideoFrame` décrites; fichier markdown final `document.md`.
- **Business Rules:**
  - Entrelacer les événements selon leur horodatage chronologique.
  - En cas de vidéo muette sans audio (EXC-001), transcrire la piste audio comme vide et générer le compte rendu à partir des seules captures visuelles.

## Acceptance Tests

### Test Data
| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `sample_clip.mp4` | Extrait vidéo MP4 de test de 60 secondes avec audio et changements de plan | Fixture de test | ready |
| `silent_clip.mp4` | Extrait vidéo MP4 sans piste audio | Fixture de test | ready |
| `static_clip.mp4` | Extrait vidéo de plan fixe sans mouvement | Fixture de test | ready |

### E2E-002: Video Keyframe Sampling and Chronological Timeline
- **Category:** happy
- **Scenario:** Traitement d'un fichier vidéo MP4 complet
- **Requirements:** FR-NEW-001, FR-NEW-002, FR-NEW-003, FR-NEW-004
- **Preconditions:** Environnement avec `ffmpeg` installé et modèle vision configuré
- **Steps:**
  - Given: Un extrait vidéo `sample_clip.mp4`
  - When: `doc-convert sample_clip.mp4` est exécuté
  - Then: Le sous-dossier `sample_clip_docling/frames/` contient les images clés extraites
  - And: Chaque image clé retenue possède une description visuelle horodatée
  - And: Le fichier `document.md` présente un journal chronologique unifié associant transcription et descriptions visuelles
  - And: Les spans `video.sample_frames` et `video.caption_frames` sont enregistrés
- **Cleanup:** Suppression du dossier temporaire
- **Priority:** Critical

### TEST-007: Frame Interval Adaptation Cap (120 Max)
- **Category:** happy
- **Scenario:** Calcul du taux d'échantillonnage pour un segment de 30 minutes avec intervalle court
- **Requirements:** FR-NEW-002, DEC-004
- **Preconditions:** Segment de 1800 secondes avec intervalle demandé de 5 secondes (soit 360 images calculées)
- **Steps:**
  - Given: Une tranche de 1800 secondes et une consigne d'intervalle de 5s
  - When: Le système calcule le plan d'échantillonnage
  - Then: L'intervalle effectif est relevé à 15 secondes
  - And: Le nombre total d'images échantillonnées ne dépasse pas 120
- **Cleanup:** Aucun
- **Priority:** High

### TEST-008: Silent Video Handling (EXC-001)
- **Category:** edge
- **Scenario:** Vidéo sans piste audio
- **Requirements:** FR-NEW-004, EXC-001
- **Preconditions:** Fichier vidéo `silent_clip.mp4`
- **Steps:**
  - Given: Une vidéo valide dépourvue de flux audio
  - When: La conversion vidéo est lancée
  - Then: Le processus aboutit sans erreur
  - And: Le document final consigne l'absence d'audio et détaille la chronologie visuelle des scènes
- **Cleanup:** Suppression du dossier temporaire
- **Priority:** Medium

### TEST-009: Perceptual Deduplication on Static Scenes (EXC-002)
- **Category:** edge
- **Scenario:** Vidéo sur plan fixe
- **Requirements:** FR-NEW-003, EXC-002
- **Preconditions:** Fichier vidéo `static_clip.mp4`
- **Steps:**
  - Given: Une vidéo de 60 secondes sans variation visuelle
  - When: Les images sont filtrées par le détecteur de doublons perceptuels
  - Then: Seule la première image est transmise au modèle de vision
  - And: Les doublons consécutifs sont ignorés
- **Cleanup:** Suppression du dossier temporaire
- **Priority:** Medium

## Constraints

### Files Not to Touch
- `src/doc_convert/recursive.py`
- `src/doc_convert/converters/eml.py`
- `src/doc_convert/calendar.py`

### Dependencies Not to Add
- Ne pas ajouter OpenCV ou Pillow lourd si les opérations peuvent être réalisées via ffmpeg ou modules existants (Approche A).

### Patterns to Avoid
- Ne pas charger l'intégralité des images non compressées en mémoire vive simultanément (respecter NFR-002).
- Ne pas déléguer aveuglément la vidéo brute sans échantillonnage local d'images (DEC-002).

### Scope Boundary
- Couvre l'extraction d'images, le filtrage, la légende des images et la fusion chronologique dans le document markdown.

## Non Regression

### Existing Tests That Must Pass
- Tests vidéo existants dans `tests/test_video.py`.
- Tests de transcription média existants dans `tests/test_media.py`.

### Behaviors That Must Not Change
- Le support des fichiers audio purs dans `MediaConverter` reste intact.

### API Contracts to Preserve
- Interface de sortie du dossier `<nom>_docling/document.md`.

## Self-Review Checklist
Full 4-axis self-review per /implement Phase 3.3 Step 5.
