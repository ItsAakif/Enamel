# Asset provenance - the public landing assets

Every delivered asset on the public landing, its origin, and its licence. The
rule this ledger enforces: **nothing ships without recorded provenance and
owner confirmation.** A landing asset that is not recorded here (or that the
owner has not confirmed for public use) must not ship.

## Routes on the public tree

```text
artifacts/enamel-landing/public/
|-- favicon.svg                    Visually delivered (browser tab icon)
|-- opengraph.jpg                  Owner-provided social-preview image
|-- images/
|   |-- dashboard-preview.png      Owner-provided landing preview image
|   |-- dental-xray.png            Owner-provided landing media (generated radiograph-style image)
|   |-- dental-xray.svg            Original project artwork (illustration; retained, currently unreferenced)
|   |-- hero-glow.png              Owner-provided hero backdrop (generated image)
|   |-- voice-ai-wave.png          Owner-provided landing media (generated waveform image)
|   `-- voice-ai-wave.svg          Original project artwork (waveform illustration; retained, currently unreferenced)
|-- videos/
|   `-- hero-bg.mp4                Owner-provided generated hero background video
`-- scripts + manifest files       Not licensed as media
```

## Ledger

| Asset | Origin | Licence | How it is used |
|---|---|---|---|
| **Inter** typeface | Google Fonts, externally loaded (no copies in the repository) | SIL Open Font License 1.1 | body/UI font on the public site (`index.html`, `src/index.css`) |
| **Manrope** typeface | Google Fonts, externally loaded (no copies in the repository) | SIL Open Font License 1.1 | display font on the public site (`src/index.css`) |
| **lucide-react** icons | npm package `lucide-react` | ISC | rendered as components by the SPA; no icon asset file is shipped |
| **favicon.svg** | App icon adapted from the Tabler Icons "dental" glyph | MIT (Tabler Icons) | rendered bitmap-free as an SVG icon |
| **dashboard-preview.png** | Owner-provided landing asset: an AI-generated preview image of the product interface | owner-provided, AI-generated art used with owner approval (no third-party licence claimed) | landing "Transformation" media |
| **dental-xray.png** | Owner-provided landing asset: an AI-generated, photographic radiograph-style image (not a patient image) | owner-provided, AI-generated art used with owner approval (no third-party licence claimed) | landing "Imaging" media |
| **voice-ai-wave.png** | Owner-provided landing asset: an AI-generated abstract waveform illustration (not a recording or signal of any real voice data) | owner-provided, AI-generated art used with owner approval (no third-party licence claimed) | landing "Voice Intelligence" background |
| **hero-glow.png** | Owner-provided landing asset: an AI-generated hero glow/backdrop image | owner-provided, AI-generated art used with owner approval; no third-party licence claimed | referenced by the owner's original landing implementation |
| **hero-bg.mp4** | Owner-provided generated hero background video (the owner's original landing video, restored at the owner's direction) | owner-provided, AI-generated video used with owner approval (no third-party licence claimed) | landing hero video background (`src/components/landing/Hero.tsx`) |
| **opengraph.jpg** | Owner-provided AI-generated social-preview image | owner-provided, AI-generated art used with owner approval | reserved OG image; no page currently declares it in a meta tag |
| **dental-xray.svg / voice-ai-wave.svg** | Original project artwork, hand-created for this repository | original project work | retained in the tree, currently unreferenced after the owner's landing-media restoration |
| **manifest.webmanifest / sw.js / service and config scripts** | project source, hand-written | project licence | application functionality |

The two fonts are fetched at runtime from Google Fonts (css2 service); no font
file is shipped in the tree. Favicon attribution is carried inside the SVG
itself.

## Owner confirmation

The photographic radiograph, waveform image, hero glow, dashboard preview,
OG image and the 13 MB hero background video were restored from the owner's
original landing implementation at the owner's direction. Their provenance is
**owner-confirmed**: the owner has approved their use on the landing. No
third-party content and no third-party licence is claimed or relied on for
any of them; each is AI-generated art provided by the project owner.

This confirmation is asset-specific. It does not change the standing rule:
**no third-party or unverified asset may ship without explicit owner
confirmation.** A future asset whose origin cannot be recorded and confirmed
here must stay out of the public tree.

## Verification of the items above

- The hero background video is the owner-provided generated video, SHA-256
  `DB667B503A3E70BD98596D1F59CF8AB7F08FF0FD9882300F7C2E94389CB4A3A1`
  (13,046,213 bytes).
- The landing PNG images and the OG image carry the hashes:
  - `dashboard-preview.png` `50831D1576F86599142754D59F9AF3D92CF3D7F088FF6CAC6A8FF495EA54E151`
  - `dental-xray.png` `EF4B70249EB554A04AC944506815F7CC771A08FAACDB797ED5787CF074A91EE8`
  - `voice-ai-wave.png` `87739D1B17D20BE68E87E63CDC9337F799EAA6B5968E8CF67C43EEA4C6FA2C03`
  - `hero-glow.png` `431A0049CD71CFECFCF26FE40C617D8896FC286C5E543A02DEA5F0C4248C725F`
  - `opengraph.jpg` `926566670386891BD57C8925F764F223954B0727171D3B5E640D2E211706A206`
- The public export size gate (`scripts/public_export.py`) has an explicit
  allowlist entry for the 13 MB video; the owner's media is never silently
  excluded.
