# Installation & PATH sur cette machine (Windows)

## 2026-09-30 — uv tool install + PATH corrompu

**Fait**: `uv tool install --force .` (make.exe absent sous Windows ; c'est l'équivalent de `make install`). L'exécutable atterrit dans `C:\Users\MORANSE\.local\bin\doc-convert.exe`. Vérification d'installation = à jour : diff complet `src/` vs `AppData\Roaming\uv\tools\docling-scripts\Lib\site-packages\` (les seules différences acceptables sont les `__pycache__`).

**Gotcha 1 — vieux shims dans `C:\Users\MORANSE\bin\`**: des zipapps de 47616 octets datés du 17 sept. y résident (doc-convert, agent-code, graphify, llm-bench, mcp-htmleditor). Seul `doc-convert.exe` y était cassé (point d'entrée obsolète `doc_convert.entry_point` ; il plantait en `ModuleNotFoundError`) ; il masquait l'install uv dans Git Bash et PowerShell 5.1 (le profil PS 5.1 préfixe `C:\Users\MORANSE\bin` au PATH). Envoyé à la corbeille le 2026-09-30. Les 4 autres fonctionnaient mais datent du 17 sept. et éclipsent les versions uv de `.local\bin` : en cas de comportement obsolète d'un de ces outils, vérifier lequel est résolu (`Get-Command <outil>`) avant de réinstaller.

**Gotcha 2 — USER PATH registre mutilé**: `HKCU\Environment\Path` contenait `.Path;C:\Users\BERTAIVE\Documents\python313 (x2, dossier inexistant);:USERPROFILE\.local\bin` (entrée avec `:` au lieu de `%`, jamais résolue, type REG_SZ). Réécrit proprement en `C:\Users\MORANSE\.local\bin` (REG_EXPAND_SZ) + broadcast WM_SETTINGCHANGE. Sauvegarde : `C:\Users\MORANSE\user-path-backup-20260930-134324.txt`. Symptôme type d'une entrée mutilée : uv avertit « not on your PATH » alors qu'une entrée ressemblante existe.

**Gotcha 3 — profil PS 7**: `Documents\PowerShell\Microsoft.PowerShell_profile.ps1` utilise `$env:HOME` (souvent vide sous Windows) pour ajouter `.local\bin` : bloc silencieusement sans effet. Sans conséquence depuis le fix registre. Le profil PS 5.1 (`Documents\WindowsPowerShell\...`) préfixe `C:\Users\MORANSE\bin`, voir Gotcha 1.

**Feature — SSL bypass pour proxies internes**: `DOC_CONVERT_DISABLE_SSL=true` (env var utilisateur) désactive la vérif SSL pour les proxies internes non-trustés (ex: EI `*.cm-cic.fr`). Code: `src/config.py` (+`disable_ssl: bool = False`) + `src/doc_convert/vision_llm.py` (`verify=not settings.disable_ssl`). Par défaut `false` (SSL ON).

**Vérif post-install (simule un terminal neuf)** :
```powershell
$m = [Environment]::GetEnvironmentVariable('Path','Machine'); $u = (Get-ItemProperty 'HKCU:\Environment').Path; $env:Path = "$m;$u"
(Get-Command doc-convert).Source   # doit pointer vers .local\bin
doc-convert --help                  # exit 0
```
Piège de vérification : `--help | Select-Object -First N` fait sortir le pipeline en code 1 sous PowerShell ; tester l'exit code sans troncature.
