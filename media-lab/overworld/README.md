# Pokémon Overworld Animation Lab

The supplied archive stores each overworld sprite as **four directions × two walking frames**. That is already an animation kit, merely distributed across folders with all the ceremony of a tax filing.

![Bulbasaur, Pikachu, and Arceus walking and turning](mini-parade.gif)

## Repository experiment

This branch commits real binary media through the GitHub connector:

- `mini-parade.gif`: Bulbasaur, Pikachu, and Arceus walking and turning together.
- `directional-atlas.png`: static four-direction inspection sheet.
- `samples/25.gif` and `samples/25.webp`: the same Pikachu animation in two animated formats.
- `overworld-mini-source.zip`: 48 original PNG frames covering three IDs, normal and shiny, four directions, and two frames.
- `manifest.json`: sizes and SHA-256 receipts, including the local full-atlas result.

The accompanying GitHub Actions workflow installs Pillow, rebuilds six animations from the committed PNG source package, verifies the resulting ZIP, and publishes the generated media as a downloadable workflow artifact.

A local full run produced **1,152 animated GIFs** across 564 normal, 564 shiny, 12 female, and 12 shiny-female variants. The full 4.26 MB archive is recorded by hash but is not pushed through a base64-in-JSON connector call, because turning the language model into a modem is not a serious bulk-file protocol.

Pokémon and related imagery belong to their respective rights holders. The assets were supplied by the repository owner for this authorized capability experiment; this repository does not claim ownership.
