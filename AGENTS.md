# docling-scripts — AI agent index

Convertisseur de documents unifié (PDF, images, DOCX, XLSX, PPTX, audio, vidéo, Google Docs/Sheets) basé sur Docling. Documentation détaillée : `CLAUDE.md` (architecture, conventions, flux de conversion) et `.agent_docs/`.

## Documentation index

- [`.agent_docs/installation-windows.md`](.agent_docs/installation-windows.md) — install uv tool sous Windows, shims obsolètes dans `~/bin`, PATH registre mutilé, vérification post-install

## Build / Test / Run

```bash
make sync          # installer les dépendances (dev)
make check         # gate qualité complet
make run ARGS='document.pdf'
make install       # installe comme uv tool (doc-convert)
```

Pas de `make.exe` sur cette machine : utiliser directement `uv tool install --force .` (détails dans `.agent_docs/installation-windows.md`).

## Conventions

Voir `CLAUDE.md` (racine du dépôt) — source de vérité pour l'architecture, les axes de flags, les chemins de conversion et les standards de code.
