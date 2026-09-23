# Feature Specification: DocConvert Simplification and Multimedia Pipeline

- **Status**: Proposed
- **Date**: 2026-09-23
- **Author**: Sebastien Morand
- **Type**: Feature
- **Depth**: L
- **Branch**: spec/20260923-205758-docconvert-simplification-and-multimedia-pipeline
- **Owned ID Prefixes**: SC-, FR-, E2E-, DEC-, EXC-

---

## 1. Executive Summary & Problem Statement

Le projet `docling-scripts` (CLI `doc-convert`) sert de socle de conversion multiformat pour alimenter des agents et des chaînes de traitement documentaire. Bien que l'extraction structurelle s'appuie avec succès sur Docling, plusieurs limites fonctionnelles et dettes techniques ont été identifiées lors des benchmarks récents :
1. Les courriels (EML et MSG) ne convertissent pas récursivement les pièces jointes audio et vidéo (`src/doc_convert/recursive.py:134`), qui restent de simples binaires sans transcription.
2. Le traitement vidéo (`src/doc_convert/converters/media.py:193` et `src/video.py:193`) ne dispose d'aucun échantillonnage visuel régulier d'images : il transmet la vidéo brute à l'API distante, ce qui génère des descriptions de scènes approximatives et sans garantie temporelle.
3. Les publications Instagram ne sont pas directement acceptées en entrée de `doc-convert` malgré la présence de l'outil `mcp-instagram` sur la machine.
4. Les réunions audio manquent de contexte calendaire automatisé alors que l'outil `outlook-tool` permet d'extraire la liste officielle des participants et l'ordre du jour.
5. La CLI et le code contiennent des options inutilisées (`--analyze`, `--meeting-summary`, `--note`, `--start-audio`), des replis obsolètes (`smolvlm`, `gemini-2.5-flash-preview-05-20` dans `src/media_llm.py:39`), et des textes d'aide qui ne reflètent plus les modèles réels.

Cette spécification définit l'évolution vers un pipeline multimédia complet, sans réduction de périmètre, épuré des fonctionnalités mortes et configuré via un fichier standard `$HOME/.config/doc-convert/config.yaml`.

---

## 2. User Personas & Target Audience

- **Utilisateur direct en CLI** : souhaite convertir n'importe quel fichier ou lien (`doc-convert <source>`) sans passer d'options complexes, en obtenant une sortie Markdown exhaustive et prévisible dans `<nom>_docling/document.md`.
- **Agents autonomes et outils en aval** : exploitent la sortie Markdown riche et le catalogue d'images pour le RAG, l'analyse de réunion et la synthèse documentaire sans perte d'information.

---

## 3. Scope & Boundaries

### In-Scope
- **Périmètre vidéo enrichi** : prise en charge d'Instagram (`mcp-instagram dl --json`), extraction d'images clés par `ffmpeg` avec déduplication, description visuelle synchronisée avec la transcription audio.
- **Récursivité complète EML/MSG** : routage automatique des pièces jointes audio et vidéo vers `MediaConverter`.
- **Contexte de réunion Outlook** : recherche calendaire par plage horaire (`outlook-tool cal list --json`) et injection des participants vérifiés dans le prompt de transcription.
- **Simplification du code** : suppression de `--analyze`, `--meeting-summary`, `--note`, `--start-audio`, `--slide-concurrency`, `-i/--instructions`, suppression de `_GEMINI_OTHER_FALLBACK_MODEL` et de `smolvlm`.
- **Configuration standard** : support de `$HOME/.config/doc-convert/config.yaml`.

### Out-of-Scope
- Remplacement du moteur de mise en page Docling ou de TableFormer.
- Serveur API HTTP / FastAPI (ce lot se concentre sur le cœur de conversion CLI/bibliothèque).
- Interface utilisateur graphique.

---

## 4. System Context & Interfaces

- **Docling Core** : moteur de conversion structurelle pour PDF, DOCX, XLSX, PPTX.
- **ffmpeg / ffprobe** : découpage audio, extraction de durée et échantillonnage d'images vidéo.
- **mcp-instagram** (`~/.local/bin/mcp-instagram`) : téléchargement de vidéos, reels et métadonnées Instagram.
- **outlook-tool** (`~/.local/bin/outlook-tool`) : interrogation de l'agenda Microsoft Graph en lecture seule.
- **Passerelle IBM ICA** (`https://api.servicesessentials.ibm.com/v1`) : modèles multimodaux distants (`ibm/gemini-3.7-flash`, `ibm/claude-sonnet-5`).
- **Google Generative AI** : API Files pour les flux vidéo volumineux.

---

## 4.5 Data Flow & Sequence Diagrams

```
[Courriel EML/MSG] ──> EmlConverter
                           │
                           ├──> Nettoyage HTML + En-têtes Markdown
                           └──> attachments/
                                   ├── PDF/DOCX ──> DoclingConverter
                                   ├── Audio ──────> MediaConverter (Diarisation + Outlook)
                                   └── Video ──────> MediaConverter (ffmpeg frames + Audio)

[Lien Instagram] ──> Detection URL ──> mcp-instagram dl ──> Video + Metadata
                                                                 │
                                                                 v
                                                     MediaConverter (Frames + Audio)
```

---

## 5. Functional Requirements (EARS)

### Module Vidéo et Échantillonnage Visuel
- **FR-NEW-001** : WHEN une vidéo (locale ou issue d'un téléchargement) est traitée, le système SHALL extraire des images clés à intervalle régulier via `ffmpeg` dans le dossier temporaire ou sous-dossier `frames/`.
- **FR-NEW-002** : IF le nombre d'images calculé pour un bloc de 30 minutes dépasse 120 images, THEN le système SHALL ajuster automatiquement l'intervalle pour plafonner l'échantillonnage à 120 images maximum par bloc.
- **FR-NEW-003** : WHEN les images sont extraites, le système SHALL filtrer les doublons perceptuels consécutifs et soumettre chaque image retenue au modèle de vision avec son horodatage exact.
- **FR-NEW-004** : WHEN les descriptions d'images et la transcription audio sont produites, le système SHALL générer un journal chronologique unifié dans `document.md` associant les propos tenus aux éléments visibles à l'écran.

### Module Instagram
- **FR-NEW-005** : WHEN l'argument d'entrée correspond à une URL Instagram (contenant `instagram.com/reel/`, `/p/` ou `/tv/`), le système SHALL appeler `mcp-instagram dl --json` pour rapatrier le fichier et la légende textuelle.
- **FR-NEW-006** : WHEN le média Instagram est récupéré, le système SHALL inclure la légende d'origine dans le préambule de `document.md` et transmettre la vidéo au pipeline d'analyse complet.

### Module Récursivité EML / MSG
- **FR-MOD-001** : WHEN `recursive.py` rencontre une pièce jointe de format audio (`.mp3`, `.wav`, `.m4a`, `.ogg`) ou vidéo (`.mp4`, `.mov`), le système SHALL instancier `MediaConverter` et convertir le média sous `attachments/<nom>_docling/`.
- **FR-MOD-002** : WHEN la conversion d'une pièce jointe audio ou vidéo réussit, le système SHALL insérer son compte rendu dans la section `## Attachments` du courriel parent avec son lien relatif.

### Module Contexte Calendrier Outlook
- **FR-NEW-007** : WHEN un fichier audio est soumis, le système SHALL extraire sa date de création ou de modification et interroger l'agenda via `outlook-tool cal list --start <date_debut> --end <date_fin> --json`.
- **FR-NEW-008** : WHEN un événement calendaire correspond au créneau horaire du fichier audio, le système SHALL injecter le sujet, le corps de réunion et la liste des participants vérifiés dans le prompt système de transcription.
- **FR-NEW-009** : IF l'interrogation de `outlook-tool` échoue ou ne renvoie aucun événement, THEN le système SHALL continuer la transcription standard sans interrompre le processus.

### Simplification et Configuration
- **FR-DEL-001** : Le système SHALL supprimer de l'interface CLI les options `--analyze`, `--meeting-summary`, `--note`, `--start-audio`, `--slide-concurrency` et `-i/--instructions`.
- **FR-DEL-002** : Le système SHALL supprimer le chemin de repli `smolvlm` dans `vlm.py` et `formats.py`.
- **FR-DEL-003** : Le système SHALL supprimer la variable `_GEMINI_OTHER_FALLBACK_MODEL` (`src/media_llm.py:39`) et interdire toute bascule automatique vers `gemini-2.5-flash-preview-05-20`.
- **FR-MOD-003** : Le système SHALL charger ses paramètres depuis `$HOME/.config/doc-convert/config.yaml` avec priorité : code < config.yaml < .env < variables d'environnement < CLI.

---

## 6. Non-Functional Requirements (NFR)

- **NFR-001 (Performance d'échantillonnage)** : L'extraction des images clés d'une tranche vidéo de 30 minutes via `ffmpeg` SHALL s'exécuter en moins de 10 secondes sur Apple Silicon.
  *Vérification* : chronométrage sous test unitaire avec fichier vidéo de référence.
- **NFR-002 (Plafond mémoire)** : L'empreinte mémoire RSS du processus pendant l'échantillonnage et la légende vidéo SHALL rester inférieure à 2 Go.
  *Vérification* : mesure `resource.getrusage` sur le banc de test vidéo.
- **NFR-003 (Résilience des outils externes)** : L'absence de `mcp-instagram` ou de `outlook-tool` dans l'environnement SHALL générer un message clair sans provoquer de traceback Python non géré.
  *Vérification* : tests d'intégration avec variables PATH modifiées ou mocks levant `FileNotFoundError`.
- **NFR-004 (Observabilité OpenTelemetry)** : Chaque étape nouvelle (`video.sample_frames`, `video.caption_frames`, `instagram.download`, `calendar.lookup`, `recursive.audio`) SHALL émettre un span OpenTelemetry structuré conforme à `src/tracing.py`.
  *Vérification* : assertion sur le fichier de log JSONL généré.
- **NFR-005 (Qualité de code et typage)** : Le projet SHALL satisfaire `ruff check` sans avertissement, `mypy` en mode strict, et 100% de succès sur la suite `pytest`.
  *Vérification* : exécution locale de `make check`.

---

## 7. Edge Cases, Failures & Boundary Conditions

- **EXC-001 (Vidéo sans piste audio)** : la vidéo contient uniquement des images ; le système transcrit la piste audio comme vide et produit le compte rendu basé uniquement sur les captures visuelles.
- **EXC-002 (Vidéo statique / plan fixe)** : la vidéo présente peu ou pas de mouvement ; le filtre perceptuel élimine les images redondantes pour n'envoyer qu'une seule requête de légende par scène stable.
- **EXC-003 (Conflit d'événements calendrier)** : plusieurs réunions se chevauchent sur la même plage horaire ; sélection de la réunion dont le titre présente la plus forte similarité lexicale avec le nom du fichier audio.
- **EXC-004 (Courriel avec pièce jointe chiffrée ou illisible)** : le fichier reste écrit sous `attachments/` et le rapport parent consigne la pièce jointe comme non convertible sans planter.

---

## 8. Architecture Approach & Technical Decisions

**Approche retenue : Approche A (Modulaire par sous-processus et outils UNIX)**
- Utilisation de `ffmpeg` directement pour les manipulations de flux vidéo et d'images.
- Appel des CLI `mcp-instagram` et `outlook-tool` via `subprocess.run` sécurisé, réutilisant les configurations et sessions existantes.
- Extension de `Settings` dans `src/config.py` avec la source de configuration YAML.

*Les approches B (bibliothèques Python lourdes internes comme OpenCV ou Azure SDK) et C (délégation aveugle au modèle distant sans échantillonnage local) ont été rejetées (détails au registre des décisions, Section 17).*

---

## 9. Data Model & Schema Changes

### Fichier `$HOME/.config/doc-convert/config.yaml`
```yaml
llm:
  default_provider: ibm
  default_model: gemini-3.7-flash
  media_provider: google
  media_model: gemini-3.7-flash

video:
  sample_interval_seconds: 15
  max_frames_per_chunk: 120

calendar:
  enabled: true
  lookup_window_minutes: 60
```

### Dataclass interne pour les captures vidéo
```python
@dataclass(frozen=True)
class VideoFrame:
    path: Path
    timestamp_seconds: float
    formatted_time: str
    description: str = ""
```

---

## 10. API & Interface Specifications

### CLI `doc-convert`
```bash
doc-convert <source> [OPTIONS]
```
- `<source>` : chemin de fichier (PDF, DOCX, XLSX, PPTX, EML, MSG, MP3, MP4, image) ou URL (YouTube, Instagram).
- Drapeaux conservés : `-o/--output`, `--llm`, `--media-llm`, `--captions`, `--engine`, `--no-ocr`, `--ocr-model`, `--no-figures`, `--no-caption-filter`, `--no-slide-screenshots`, `--slide-vlm`, `--llm-concurrency`, `--cpu`, `--all`, `--stdout`, `-f/--force`, `--symlink`, `-P/--parallel`, `-v`, `-q`.
- Drapeaux retirés : `--analyze`, `--meeting-summary`, `--note`, `--start-audio`, `--slide-concurrency`, `-i/--instructions`.

---

## 11. Security, Privacy & Abuse Vectors

- **Désinfection stricte des entrées** : validation des URL YouTube et Instagram par regex avant appel sous-processus pour éviter toute injection de commande.
- **Sécurisation des chemins de pièces jointes** : nettoyage systématique des caractères de séparation de chemin (`/`, `\`, `..`) pour interdire toute traversée de répertoire lors de l'extraction des courriels.
- **Protection des données d'agenda** : les noms et courriels issus de Microsoft Graph ne sont utilisés que dans le prompt éphémère de transcription et dans le compte rendu local final, sans journalisation en clair dans les traces.

---

## 12. Test Plan & Acceptance Criteria

### Scénarios de bout en bout (E2E)
- **E2E-001 (Récursivité EML avec audio)** : traiter un courriel de test contenant une pièce jointe audio ; vérifier la création de `attachments/<audio>_docling/document.md` et son inclusion dans le document final.
- **E2E-002 (Échantillonnage vidéo)** : traiter un extrait vidéo MP4 local ; vérifier la création des vignettes dans `frames/`, l'émission des descriptions et leur entrelacement chronologique avec l'audio.
- **E2E-003 (Enrichissement calendrier)** : traiter un fichier audio avec mock de la réponse `outlook-tool cal list` ; vérifier que les participants identifiés sont correctement nommés dans le dialogue final.
- **E2E-004 (Lien Instagram)** : invoquer `doc-convert` sur une URL Instagram mockée ; vérifier l'appel à `mcp-instagram dl` et la prise en charge du fichier résultant.

### Tests unitaires
- Validation du rejet des drapeaux CLI supprimés.
- Validation du chargement de `config.yaml`.
- Validation de l'adaptation de cadence si le nombre d'images dépasse 120.

---

## 13. Deployment, Operations & Migration Strategy

- Installation via `uv tool install --editable .` ou `make install`.
- Dépendances système requises : `ffmpeg`, `yt-dlp` (via Homebrew).
- Dépendances CLI optionnelles recommandées : `mcp-instagram`, `outlook-tool`.

---

## 14. Rollback & Disaster Recovery

En cas de régression, retour immédiat au commit précédent via `git revert`. Le format de sortie `<nom>_docling/document.md` restant inchangé, aucune migration de données n'est requise pour les documents déjà convertis.

---

## 15. Observability, Logging & Telemetry

- Nouveaux spans OpenTelemetry instrumentés avec `trace_span` :
  - `video.sample_frames` (attributs: durée, nombre d'images extraites)
  - `video.caption_frames` (attributs: nombre de requêtes vision)
  - `instagram.download` (attributs: URL source, statut)
  - `calendar.lookup` (attributs: créneau horaire, résultat trouvé ou non)
  - `recursive.audio` et `recursive.video`

---

## 16. Open Questions & Assumptions

- **Hypothèse 1** : L'utilisateur dispose des droits de lecture sur son calendrier via `outlook-tool` (session device-code active).
- **Hypothèse 2** : `ffmpeg` est installé et présent dans le PATH de la machine.

---

## 17. Decisions Log

- **DEC-001** : Choix de l'Approche A (sous-processus UNIX légers) pour `mcp-instagram` et `outlook-tool` plutôt qu'embarquer de lourds SDK Python, préservant la légèreté du projet et son isolation face aux pannes.
- **DEC-002** : Rejet de l'Approche C (vidéo sans échantillonnage d'images) suite aux benchmarks confirmant les hallucinations et sauts de contexte des descriptions visuelles non contrôlées dans le temps.
- **DEC-003** : Suppression complète du repli `smolvlm` local et de `gemini-2.5-flash-preview-05-20` pour éliminer le code mort et clarifier le comportement d'erreur.
- **DEC-004** : Plafonnement de l'échantillonnage vidéo à 120 images par tranche de 30 minutes pour prévenir la saturation disque et maîtriser les coûts d'appels API vision.

---

## 18. Backward Compatibility & Breaking Changes

- **Rupture mineure** : suppression des drapeaux CLI `--analyze`, `--meeting-summary`, `--note`, `--start-audio`. Tout script externe s'appuyant sur ces drapeaux devra être mis à jour.
- **Compatibilité ascendante préservée** : l'arborescence de sortie standard (`<nom>_docling/document.md`) et la syntaxe principale `doc-convert <fichier>` restent 100% compatibles.

---

## 19. Implementation Drift Register

*(Registre initialement vide, réservé à la phase d'audit d'implémentabilité)*
