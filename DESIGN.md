# Design — FormaScale

## World

**Atlas de points** — fond nuit marine `#09101d`, encre crème `#ebeef0`, accent rouge lampe `#d8322e`. Marque en Mingzat. Un seul geste signature : l’anneau rouge partagé (nuage ↔ photo).

## Surfaces

| Écran | Rôle | Layout |
|---|---|---|
| `live/static/desk.html` | Commande live | Bande de commande dense + nuage plein cadre + inset téléphone |
| `live/static/phone.html` | Capture | Vidéo dominante, anneau, actions |
| `viewer/index.html` | Annotation A–B | Même chrome Atlas, workspace nuage + photos |

## Données réelles vs décor

Les images sous `assets/plates/` ne sont que des **états vides** pour la composition. Dès qu’un nuage ou un preview live arrive, le canvas / `#live-preview` prend le dessus (`has-cloud`, `has-live-preview`). Le produit montre toujours la reconstruction détectée, pas le mockup.

## Tokens

- Fond : `--bg #09101d`
- Encre : `--ink #ebeef0`
- Muet : `--muted #9e9d9f`
- Accent : `--accent #d8322e`
- Police UI : Mingzat (self-host `live/static/fonts/`)
