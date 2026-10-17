# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Un seul opérateur. Il filme l’objet au téléphone, sur le même réseau, et lit la reconstruction sur le PC. Usage de labo répété : la prochaine action doit être lisible sans le README.

## Product Purpose

Reconstruire un objet en 3D à partir de photographies et préparer une mesure métrique. Succès live : assez de keyframes raccordées pour un nuage exploitable. Succès viewer : les mêmes centres A et B placés dans au moins trois photos raccordées, sous des angles différents, puis exportés. La conversion en millimètres reste hors du navigateur.

## Positioning

Le téléphone capture, le PC reconstruit au fil des keyframes, et l’échelle se fixe par les mêmes centres vus sous plusieurs angles. Un nuage sans repères n’est pas une mesure.

## Operating Context

Deux moments, trois écrans.

1. Live. `python live/server.py`. Bureau : `https://127.0.0.1:8443/desk`. Téléphone : `https://<IP>:8443/phone`, certificat auto-signé à accepter une fois. L’opérateur tourne lentement autour de l’objet. À partir de 3 keyframes le nuage se met à jour. Les keyframes vont dans `captures/live_YYYYMMDD_HHMMSS/`.
2. Après coup. Le viewer charge une capture, une expérience et un modèle. Il inspecte le nuage et les photos, place A/B et, si besoin, C/D, exporte les repères. Les positions restent dans ce navigateur pour ce modèle.

L’opérateur est seul : le téléphone est dans sa main, le bureau est l’écran qu’il consulte en revenant au PC.

## Capabilities and Constraints

- Écrans concernés : `live/static/desk.html`, `live/static/phone.html`, `viewer/index.html`. Un seul système visuel.
- Conserver les ids, les canvases, le WebSocket et le comportement déjà branché. Pas de nouvelle capacité produit.
- Français. Les libellés peuvent être réécrits. Les blocs peuvent être réordonnés si le parcours devient plus simple.
- Ne pas inventer de métriques, de témoignages, ni de précision non vérifiée. L’erreur de reprojection et les comptes (keyframes, points, vues) viennent des données réelles.
- Stack déjà en place : HTML, CSS et JS statiques, servis par `live/server.py` pour le live et ouverts comme fichiers pour le viewer. Pas de framework.
- Direction visuelle : non tranchée.

## Brand Commitments

Nom : FormaScale. Aucune autre contrainte de marque. L’apparence actuelle (fond sombre, accent sarcelle, Segoe UI, sourcils en capitales) est l’existant à remplacer, pas un engagement.

## Evidence on Hand

- Captures réelles sous `captures/` (`bose_01`, `dtu_scan001`, sessions live).
- Le README tient les seuls chiffres vérifiés du pipeline. Ils ne sont pas de la preuve d’interface.
- Pas de logo, pas de photo de marque, pas de témoignage. Ne pas en fabriquer.

## Product Principles

1. La prochaine action est visible avant l’explication.
2. Le nuage et la photo sont le travail. Le chrome s’efface.
3. Un état (hors ligne, attente, reconstruction, erreur) se lit sans paragraphe.
4. Ce qui n’est pas encore une mesure ne se présente pas comme une mesure.
5. Bureau, téléphone et viewer sont le même outil à trois moments.
