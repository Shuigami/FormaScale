# FormaScale

Reconstruction 3D et mesure metrique a partir de photographies
(pipeline offline COLMAP/pycolmap, mode live telephone→PC, evaluation DTU).

**Hors depot** (deja ignores) : `captures/`, `datasets/`, certificats
`live/certs/`. Cloner puis recreer les captures localement ou reutiliser
celles deja preparees sur la machine de labo.

```sh
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Premier jeu de donnees

Les donnees locales sont dans `captures/bose_01`. Les HEIC originaux restent
dans `images/`; les copies orientees, reduites a 2000 pixels maximum, sont
dans `prepared/images/`. La conversion est pour l'instant en RGB 8 bits via
pillow-heif; elle ne conserve pas la dynamique HDR originale.

Les notes de capture indiquent une reference de 100 mm entre les croix et
les dimensions x=178, y=52, z=59 mm. L'association de ces axes aux points de
mesure doit etre precisee avant toute validation metrique.

## Environnement

Ubuntu-24.04 sous WSL2, utilisateur shui. Environnement Python isole :
`/home/shui/.venvs/formascale`. Le disque virtuel Ubuntu est sur D:.
Les versions exactes sont dans `captures/bose_01/reports/environment.txt`.

Depuis Ubuntu :

```sh
cd /mnt/d/Dev/FormaScale
source /home/shui/.venvs/formascale/bin/activate
python scripts/inspect_capture.py captures/bose_01
python scripts/reconstruct_sparse.py captures/bose_01 --run sift_cpu_03
```

Chaque calcul de reconstruction exige un nouveau nom de run. Le nombre de
threads par defaut est limite a 2; `--threads 8` reproduit celui du premier
calcul termine. Les graines et versions sont enregistrees sans garantie de
resultats binaires identiques entre plateformes ou nombres de threads.

## Etat verifie

- 59/59 fichiers lisibles, aucun doublon de contenu exact.
- Trois resolutions source; aucune photo exclue automatiquement.
- Planches de controle : `captures/bose_01/reports/contact_*.jpg`.
- Premier calcul termine : `captures/bose_01/runs/sift_cpu_02`.
- Plus grand modele : 29 photos enregistrees sur 59, 743 points 3D,
  erreur moyenne de reprojection 0.848 pixel, duree totale 47.36 secondes.
- Pic RAM du processus : 6918 Mio, mesure Linux ru_maxrss.
- Calcul sur CPU; cette installation de pycolmap n'utilise pas CUDA.
- La premiere tentative `sift_cpu_01` est incomplete et conservee pour diagnostic.

Ce sont des points epars de la scene, pouvant inclure le bureau et la feuille.
Ni la geometrie de l'enceinte, ni son echelle, ni la precision des mesures
ne sont validees. Une faible erreur de reprojection ne suffit pas a les garantir.
Le probleme de memoire virtuelle Windows observe pendant l'essai demande un
diagnostic; C: etait presque plein. Aucun nettoyage systeme automatique effectue.

## Benchmark public DTU (SampleSet)

Donnees locales (hors Git) :

- `datasets/dtu/SampleSet/` — ReadMe, calibration, ObsMask, Rectified scan1/scan6
- `datasets/dtu/Points/` — nuages STL de reference (stl001 et stl006 copies sous SampleSet)

Citeware : Jensen et al., *Large Scale Multi-view Stereopsis Evaluation*, CVPR 2014.

```sh
cd /mnt/d/Dev/FormaScale
source /home/shui/.venvs/formascale/bin/activate

# Evaluation de la baseline Tola fournie (deja dans le repere STL)
python scripts/evaluate_dtu.py --scan 1 \
  --pred "datasets/dtu/SampleSet/MVS Data/Points/tola/tola001_l3.ply"

# Preparer scan1 (eclairage l3) puis reconstruction SfM eparse
python scripts/prepare_dtu_capture.py --scan 1 --run-name dtu_scan001
python scripts/reconstruct_dtu.py captures/dtu_scan001 --run sift_shared_01
python scripts/evaluate_dtu.py --scan 1 \
  --model captures/dtu_scan001/runs/sift_shared_01 --align-cameras
```

L'evaluation Python porte accuracy (data→STL) et completeness (STL→data)
avec masque / plan DTU. Les chiffres leaderboard officiels restent ceux du
code MATLAB fourni. Un nuage SfM epars n'est pas comparable a un MVS dense.

## Mode live (telephone + reconstruction)

Nouveau flux experimental, separe du pipeline offline. Le telephone filme,
le PC affiche cote a cote le flux camera et le nuage SfM qui se met a jour
quand assez de keyframes arrivent.

Depuis Ubuntu (venv FormaScale, pycolmap requis pour la reconstruction) :

```sh
cd /mnt/d/Dev/FormaScale
source /home/shui/.venvs/formascale/bin/activate
python live/server.py
```

Puis :

1. Sur le PC : ouvrir `https://127.0.0.1:8443/desk`
2. Sur le telephone (meme Wi-Fi) : ouvrir `https://<IP>:8443/phone`
3. Accepter l'avertissement certificat une fois (certificat auto-signe)
4. Demarrer la camera, activer les auto-keyframes ou appuyer sur Keyframe
5. Tourner lentement autour de l'objet ; a partir de 3 keyframes le nuage se reconstruit

Sous WSL2, si le telephone n'atteint pas l'IP affichee, utiliser l'IPv4
Windows (`ipconfig`) et/ou le mode reseau mirrored. Sans pycolmap, le double
affichage video + UI fonctionne quand meme, mais la reconstruction reste en
attente. Option `--http` pour tests locaux sans camera iPhone.

Les keyframes sont enregistrees dans `captures/live_YYYYMMDD_HHMMSS/`.

## Reconstruction dense GPU (MVS)

Apres un sparse valide (run live ou offline), densifier avec COLMAP PatchMatch
sur GPU. Le venv FormaScale (pycolmap CPU) ne suffit pas : il faut un binaire
`colmap` compile avec CUDA.

Installation une fois (WSL Ubuntu-24.04, micromamba dans `~/bin`) :

```sh
export MAMBA_ROOT_PREFIX=$HOME/mamba
export PATH=$HOME/bin:$PATH
micromamba create -y -n colmap-cuda -c conda-forge 'colmap=*=cuda*'
micromamba run -n colmap-cuda colmap help   # doit afficher "with CUDA"
```

Puis, depuis le venv FormaScale :

```sh
cd /mnt/d/Dev/FormaScale
source /home/shui/.venvs/formascale/bin/activate
python scripts/reconstruct_dense.py captures/live_20261017_170736 --run live
```

Sorties dans `runs/<run>/dense/` : `fused.ply`, `meshed-poisson.ply`,
`summary.json`. Option `--colmap` si le binaire n'est pas dans
`~/mamba/envs/colmap-cuda/bin/colmap`. `--max-image-size 1600` limite la VRAM
(RTX 4060 8 Go). L'echelle metrique n'est pas validee par la densification.

Affichage :

- Viewer : `python scripts/export_diagnostics.py captures/<capture> --refresh-catalog`
  puis ouvrir `viewer/index.html` — modele `Dense MVS` si `fused.ply` existe.
- Desk live : bouton **Dense** (`POST /api/dense`) charge le fused existant
  ou lance `reconstruct_dense.py` (GPU). Le sparse reste via Relancer.

## Prochain jalon

Valider les metriques DTU sur la baseline Tola, puis sur scan1 FormaScale.
Sur Bose : annoter l'echelle A–B et mesurer les dimensions de controle.
Essayer le mode live telephone pour accelerer la capture guidee.
