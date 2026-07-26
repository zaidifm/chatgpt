# Pokémon Overworld Animation Lab

The supplied archive stores each overworld sprite as **four directions × two walking frames**. That is already an animation kit, merely distributed across folders with all the ceremony of a tax filing.

![Pikachu walking and turning as animated WebP](samples/25.webp)

## Repository experiment

This branch contains two byte-exact binary objects committed through the GitHub connector:

- `samples/25.webp`: a 16-frame Pikachu turntable in animated WebP.
- `directional-atlas.png`: a 900×112 four-direction inspection atlas.

GitHub Actions verifies their SHA-256 receipts and media metadata, converts the WebP to an animated GIF and 4×4 frame strip, creates an animated viewport scan across the atlas, validates the generated files, and publishes everything as a downloadable workflow artifact.

A separate local full run against the user-supplied archive produced **1,152 animated GIFs** across 564 normal, 564 shiny, 12 female, and 12 shiny-female variants. Its 4.26 MB ZIP is recorded in `manifest.json` by hash. It was not serialized through a roughly 5.68 MB base64-in-JSON connector call: the connector exposes no mounted-file upload action, and larger model-mediated binary payloads failed byte-integrity checks.

Pokémon and related imagery belong to their respective rights holders. The assets were supplied by the repository owner for this authorized capability experiment; this repository does not claim ownership.
