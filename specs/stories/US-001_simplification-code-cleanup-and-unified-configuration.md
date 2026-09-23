# US-001: Simplification, Code Cleanup and Unified Configuration

> Parent Spec: specs/20260923-205758-docconvert-simplification-and-multimedia-pipeline.md
> Epic: n/a
> Status: ready
> Priority: 1
> Depends On: none
> Complexity: M
> min_tier: 2
> Files touched: 5

## Objective
Remove unused CLI options and deprecated fallback models across the codebase. Implement standard hierarchical configuration loading via `$HOME/.config/doc-convert/config.yaml` to configure downstream multimedia workflows.

## Technical Context

### Stack
Python 3.10+, Typer CLI, Pydantic / pydantic-settings, PyYAML.

### Relevant File Structure
```text
src/
├── config.py
├── media_llm.py
└── doc_convert/
    ├── cli.py
    ├── formats.py
    └── vlm.py
tests/
├── test_config.py
└── test_cli.py
```

### Existing Patterns
Configuration is managed in `src/config.py` using `pydantic-settings`. CLI flags and commands are configured with Typer in `src/doc_convert/cli.py`.

### Data Model (excerpt)
Configuration schema loaded from `$HOME/.config/doc-convert/config.yaml`:
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

### Decisions That Governed This Story
- `DEC-003`: "Suppression complète du repli smolvlm local et de gemini-2.5-flash-preview-05-20 pour éliminer le code mort et clarifier le comportement d'erreur."

### Applicable NFRs
- `NFR-005`: "Le projet SHALL satisfaire ruff check sans avertissement, mypy en mode strict, et 100% de succès sur la suite pytest."

### Bounded Context
Core Configuration and CLI Interface.

## Functional Requirements

### FR-DEL-001: Remove Unused CLI Flags
- **EARS:** Le système SHALL supprimer de l'interface CLI les options `--analyze`, `--meeting-summary`, `--note`, `--start-audio`, `--slide-concurrency` et `-i/--instructions`.
- **Inputs / Outputs:** Invocations CLI avec ces options lèvent une erreur de parsing Typer; invocations standards restent valides sans ces options.
- **Business Rules:** Ne conserver que les options documentées dans la section 10 de la spécification.

### FR-DEL-002: Remove SmolVLM Fallbacks
- **EARS:** Le système SHALL supprimer le chemin de repli `smolvlm` dans `vlm.py` et `formats.py`.
- **Inputs / Outputs:** Suppression des références, énumérations ou branches conditionnelles associées à `smolvlm`.
- **Business Rules:** Aucun appel local ou repli vers `smolvlm` ne doit subsister.

### FR-DEL-003: Remove Gemini Other Fallback Model
- **EARS:** Le système SHALL supprimer la variable `_GEMINI_OTHER_FALLBACK_MODEL` (`src/media_llm.py:39`) et interdire toute bascule automatique vers `gemini-2.5-flash-preview-05-20`.
- **Inputs / Outputs:** L'échec d'un appel média doit lever l'erreur d'origine sans basculer silencieusement sur le modèle de repli obsolète.
- **Business Rules:** Respect strict du modèle configuré sans substitution automatique.

### FR-MOD-003: Load Standard Configuration File
- **EARS:** Le système SHALL charger ses paramètres depuis `$HOME/.config/doc-convert/config.yaml` avec priorité: code < config.yaml < .env < variables d'environnement < CLI.
- **Inputs / Outputs:** Fichier `$HOME/.config/doc-convert/config.yaml` en entrée; instance de configuration typée en sortie.
- **Business Rules:** Si le fichier YAML est absent, l'application fonctionne avec les valeurs par défaut du code; la hiérarchie de priorité définie doit être strictement respectée.

## Acceptance Tests

### Test Data
| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `sample_config.yaml` | Fichier YAML de test définissant les sections llm, video et calendar | Fixture de test | ready |

### TEST-001: CLI Flag Rejection
- **Category:** failure
- **Scenario:** Invocation de la CLI avec drapeaux obsolètes
- **Requirements:** FR-DEL-001
- **Preconditions:** Environnement CLI actif
- **Steps:**
  - Given: La CLI doc-convert installée
  - When: L'utilisateur exécute `doc-convert document.pdf --analyze` ou `--meeting-summary` ou `--note` ou `--start-audio` ou `--slide-concurrency` ou `-i instruction`
  - Then: La commande échoue immédiatement avec un code de retour non nul et un message d'option inconnue
  - And: Aucun traitement de fichier n'est initié
- **Cleanup:** Aucun fichier résiduel
- **Priority:** High

### TEST-002: Deprecated Fallback Model Removal
- **Category:** failure
- **Scenario:** Tentative d'appel média levant une erreur
- **Requirements:** FR-DEL-002, FR-DEL-003
- **Preconditions:** Clé API distante invalide ou blocage
- **Steps:**
  - Given: Un échec persistant lors d'un appel Gemini
  - When: `media_llm.py` traite l'erreur
  - Then: L'exception d'origine est propagée
  - And: Aucune tentative de redirection vers `gemini-2.5-flash-preview-05-20` n'est effectuée
  - And: `smolvlm` n'est plus importé ni référencé dans `vlm.py` et `formats.py`
- **Cleanup:** Aucun
- **Priority:** High

### TEST-003: Hierarchical Configuration Loading
- **Category:** happy
- **Scenario:** Chargement des réglages avec fichier config.yaml
- **Requirements:** FR-MOD-003
- **Preconditions:** Fichier YAML de configuration temporaire présent
- **Steps:**
  - Given: Un fichier `config.yaml` définissant `video.sample_interval_seconds: 30`
  - When: La configuration de l'application est initialisée
  - Then: La valeur `sample_interval_seconds` vaut 30
  - And: Une variable d'environnement surcharge la valeur du fichier YAML
  - And: Une option CLI surcharge la variable d'environnement
- **Cleanup:** Suppression du fichier temporaire
- **Priority:** High

## Constraints

### Files Not to Touch
- `src/doc_convert/recursive.py`
- `src/doc_convert/converters/media.py`
- `src/doc_convert/converters/eml.py`

### Dependencies Not to Add
- Ne pas ajouter de librairies lourdes de parsing ou de framework CLI alternatif.

### Patterns to Avoid
- Ne pas lire `os.environ` directement hors de l'infrastructure pydantic-settings.
- Ne pas rétablir de repli silencieux vers d'autres modèles en cas d'erreur API.

### Scope Boundary
- Uniquement le nettoyage des options mortes et la mise en place du chargeur de configuration. L'utilisation des réglages dans le pipeline vidéo ou calendrier appartient aux récits ultérieurs.

## Non Regression

### Existing Tests That Must Pass
- `tests/test_cli.py` pour toutes les options CLI conservées.
- `tests/test_config.py` pour les réglages existants.

### Behaviors That Must Not Change
- Les options CLI conservées (`-o`, `--llm`, `--media-llm`, `--captions`, etc.) doivent fonctionner sans altération de syntaxe ou de comportement.

### API Contracts to Preserve
- Interface publique de `Settings` dans `src/config.py`.

## Self-Review Checklist
Full 4-axis self-review per /implement Phase 3.3 Step 5.
