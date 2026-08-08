# lilith-tools

PC control, browser automation, RAG tools.

Part of the Yggdrasil ecosystem.

## Forja design automation

`forja_design_batch` convierte un brief de Lilith en un lote persistente de
arte apparel: variantes, reintentos, limpieza de fondo, PNG finales a 300 DPI,
QA geométrico v2 (incluido margen mínimo de fuente) y review board.
`forja_design_batch_status` recupera el lote por `run_id` si
la sesión se interrumpe. Para una sola imagen permanece `forja_generate`.
