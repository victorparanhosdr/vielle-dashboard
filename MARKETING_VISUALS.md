# Visuais da campanha DOC4DOCS

Modo: ferramenta integrada de geracao e edicao de imagens (image_gen). Sem CLI.

## Arquivos selecionados

- `static/marketing-hero-panorama-v2.png`: composicao de abertura, notebook com atendimento real e dados ficticios.
- `static/marketing-palm-general-v2.png`: foto aprovada do celular na mao, editada para mostrar o Painel Geral, com dados financeiros ficticios.

A logo existente foi preservada. Os paineis nao representam resultados de clientes.
As fontes de interface vieram de servidores locais isolados, sem dados de producao.

## Prompt da abertura

```text
Edit target: the attached DOC4DOCS campaign product photograph.
Change ONLY framing and scene composition to an ULTRA-WIDE panoramic website hero, aspect ratio 12:5, 2400x1000. Keep the same realistic silver laptop, EXACT original DOC4DOCS interface on screen, dark pine-green room, silver desk, cool daylight and restrained glass materials. Preserve the genuine on-screen logo and typography.
Expand the dark pine-green wall to occupy the entire LEFT 47 percent, uninterrupted and free of objects from top to bottom except the very bottom desk edge. Remove the pen cup and notebooks. Place the full laptop in the RIGHT 51 percent. The laptop must be wholly visible, not cropped: top of the lid at 22 percent image height, bottom of base at 91 percent image height, right edge at 96 percent image width. Camera straight-on; keep screen legible and proportions correct.
The top 16 percent across the ENTIRE picture should stay dark green architectural wall so website navigation in white will be legible. At the far right, a frosted window begins below that dark header area. Calm cinematic light, no gradient overlays, no added type, no invented UI panels, no spheres or particles, no people. One continuous full-bleed photograph, never a split collage.
This framing is critical: a real product visible on the right, sufficient dark negative space on the left for the website's headline and call-to-action, a composed premium technology launch rather than a generic app screenshot.
```

Fonte da primeira composicao:

```text
Use case: compositing / product-mockup.
Asset type: flagship hero photograph for the real DOC4DOCS clinic-management SaaS website, ultrawide cinematic 16:9, 3840x2160 quality.
Input image 1 is the ACTUAL product UI. Use it faithfully as the screen of the laptop; preserve layout, dark green navigation, original logo, white workspace, conversation and patient context. All data in this reference is synthetic. It is not a layout reference for the entire picture.
Create a magnificent but restrained photorealistic technology campaign photograph. A precision-machined silver laptop sits open on a low satin-aluminum architectural desk, occupying the RIGHT 55 percent of the composition. Camera nearly front-on with slight elevated perspective, screen large, crisp, fully visible, practical and believable, no extreme angle. The laptop screen displays the attached DOC4DOCS interface. Scene is a refined contemporary clinic director's studio: deep closed pine-green architectural wall, a single tall frosted-glass vertical partition at the far right catching cool daylight, clean silver surfaces, subtle material texture. A small translucent glass rectangular prism near the laptop evokes clarity, not decoration overload. No people.
The LEFT 43 percent of the image is uninterrupted flat dark pine-green wall, calm enough for white web typography to be overlaid in HTML. The right side feels luminous and dimensional, studio-quality product lighting, highly sophisticated art direction, crisp device silhouette and real materials, editorial design at the level of a major technology launch. The desk spans the FULL width along the bottom, making it one continuous full-bleed scene, not a split collage. Laptop at lower right but entirely inside the frame with breathing room, screen center near x=72%, y=57%.
Palette: pine green #123D31, platinum silver, clear glass and gentle cool white daylight. No brown, beige, purple, neon, gradients as illustrations, abstract blobs, spheres, sci-fi wires, stock-photo look, floating UI cards or extra screens. No ad headlines, no typography outside the real screen, no new logos, no watermark. Do not turn the actual product into invented futuristic UI. The emotional message is calm mastery and a successful clinic owner finally having peace.
```

## Prompt do celular aprovado

```text
Use case: compositing / ads-marketing.
Asset: premium editorial product photograph for the DOC4DOCS website section about managing a clinic from the palm of your hand. Vertical 4:5 composition, high resolution.
Input image 1 is the actual responsive DOC4DOCS dashboard, with synthetic demonstration data. Preserve this exact interface on the phone display: original square 4D mark, clinic header, dark green figures on a white surface, thin chart, bottom navigation. Do not invent extra features, extra labels or impressive financial numbers. Make the screen authentic and clear.
Photorealistic close-up of a relaxed clinic owner seated beside a sunlit floor-to-ceiling window, holding a slim titanium smartphone in one natural hand, wearing a crisp white shirt with a lightly rolled cuff and a simple steel wristwatch. Only hand, forearm and a little torso in frame, NO face. Phone upright in the center, entire device visible, nearly straight-on to the camera so the actual dashboard is readable. Phone occupies about 55% of the image height and 40% width. Natural five fingers, comfortable unforced grip, thumb not covering the screen.
The surroundings are an elegant contemporary clinic office: sage-to-pine green chair fabric, light cool-gray architectural wall, subtle frosted glass, daylight. Clean refined photographic realism, silver and green palette, good exposure, screen sharp. Convey calm confidence, breathing room and freedom from daily operational chaos. Not dark, not blue tech neon, not a staged stock handshake photo. No added headline, advertising text, watermark, invented logos, floating UI panels, glowing orbs, gradients or decorative particles. This must feel like a distinctive luxury technology editorial, not a collage.
```

## Edicao final: Painel Geral

```text
Precise product-screen edit.
Image 1 is the APPROVED photograph and the edit target: hand holding titanium phone, silver wristwatch, white sleeve, green chair, sunlit clinic. The user loves this image. Preserve EVERYTHING about it: hand, pose, anatomy, phone body and exact position, lighting, reflections, watch, shirt, green chair, window, background, framing, photo quality, colors. Do not change or reimagine the photograph.
Image 2 is the replacement phone screen content: the real DOC4DOCS "Painel geral / Resumo mensal" in the actual mobile interface, using entirely fictitious demo figures.
Change ONLY the pixels inside the phone's display in image 1. Replace the old CRM Dashboard with image 2, fitting its full portrait width naturally into the phone screen with correct perspective. Preserve the original small 4D logo, the title "Painel geral" and "Resumo mensal", the green financial summary panels "TOTAL VENDIDO", "R$ 15.000", "TOTAL RECEBIDO", "R$ 14.520,00". Preserve the screenshot's arrangement and legitimate UI; no invented extra charts, buttons, claims or numbers. The top system cutout / dynamic island remains physically correct, not duplicated. Show the screenshot as faithfully as possible and legibly.
No typography outside the phone, no watermark, no other visual changes. Output the same portrait composition as image 1.
```

## Revisao de conversao: foco no dono da clinica

- `static/marketing-hero-owner-v3.png`: edicao pelo image_gen integrado, preservando a foto aprovada e substituindo apenas a tela pelo Resumo Mensal real.
- `static/marketing-monthly-v3.png`, `static/marketing-sales-v3.png` e `static/marketing-procedures-v3.png`: capturas da interface real com dados integralmente ficticios, gerados em `tests/serve_receipts_preview.py --marketing-fixture`, sem acesso a APIs externas ou bancos reais.
- As notas de IA e os valores de Bronze, Prata e Ouro da pagina sao exemplos explicitamente simulados. Nao representam resultados, clientes ou depoimentos reais.
- A classificacao comercial considera compras com o profissional; nao comprova recebimento nem classifica a condicao clinica.

Prompt final da edicao (image_gen integrado; nenhuma CLI/API externa):

```text
Use case: compositing. Edit target: Image 1, the approved panoramic DOC4DOCS premium silver laptop photo, dark pine-green clinic office wall, silver desk, sunlight, quiet sophistication. Supporting insert: Image 2 is an actual screenshot of DOC4DOCS Resumo mensal using fictitious demo data. CHANGE ONLY the laptop display in Image 1: replace its chat interface with the actual Resumo mensal management dashboard from Image 2, perspective-matched within the exact screen edges. Preserve the real dashboard's brand and arrangement: Resumo mensal title, Total vendido R$ 107.400, Total recebido R$ 104.148,00, meta mensal R$180.000, projeção R$158.543, ticket médio R$2.826 and green/white panels and chart. Do not invent a different dashboard. The page sells to clinic OWNERS who want business performance, not chat operators. Preserve EVERYTHING outside the laptop screen: full panoramic approximately 2.4:1 framing, large clean negative space on LEFT for live webpage text, laptop on RIGHT, metal body, keyboard, desk reflection, room, plant, color, shadows, light. NO extra text, captions, floating widgets, letters, badges or graphics outside the screen. Do not add headline text into the bitmap. High quality sharp product photography. Do not crop the laptop or change composition. Output wide panoramic landscape, matching original 1942x809 as closely as possible.
```
