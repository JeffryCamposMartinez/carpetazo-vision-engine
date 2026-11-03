# 3. Herramienta local de importación, revisión, exportación y respaldo

Se ejecuta con el entorno del proyecto (sin Docker, sin red, sin R2). Atajo PowerShell: `.\scripts\dev\dataset.ps1 <comando>` ≡ `python -m app.dataset_lab.cli <comando>`.
Todos los comandos aceptan `--root RUTA` (por defecto `datasets/real-photos`).

## 3.1 Flujo normal
```powershell
.\scripts\dev\dataset.ps1 init
.\scripts\dev\dataset.ps1 session-add --id s-2026-11-05-a --date 2026-11-05 --device "iPhone 14" --place "escritorio"
.\scripts\dev\dataset.ps1 card-add --name "..." --set "..." --number "058/102" --language es --finish holo --category holo
.\scripts\dev\dataset.ps1 card-verify pc-001 --by TuNombre --note "comparada con la carta física: nombre, símbolo de set, número, idioma"
.\scripts\dev\dataset.ps1 import --from datasets\real-photos\inbox\s-2026-11-05-a\pc-001 --session s-2026-11-05-a --card pc-001 `
    --lighting natural_directa --angle frontal --background madera --sleeve sin_funda --dry-run     # ensayo: no escribe nada
.\scripts\dev\dataset.ps1 import --from ... --session ... --card pc-001 --lighting ... --by TuNombre           # importación real
.\scripts\dev\dataset.ps1 review --card pc-001 --status verified --by TuNombre                                  # tras mirar las fotos
.\scripts\dev\dataset.ps1 validate
.\scripts\dev\dataset.ps1 split --name piloto-1
.\scripts\dev\dataset.ps1 export --out datasets\real-photos\exports\piloto-1 --split piloto-1
.\scripts\dev\dataset.ps1 backup --dest D:\Respaldos
```

## 3.2 Qué hace cada comando
| Comando | Qué hace | Qué NO hace |
|---|---|---|
| `init` | Crea la estructura y `dataset.json` | No borra nada existente |
| `session-add` | Registra una sesión (id, fecha, teléfono, lugar, operador) | |
| `card-add` / `card-verify` / `card-reference` / `card-list` | Registra y verifica cartas físicas; `card-reference` fija la imagen de R2 aceptada a mano como referencia | No inventa `card_id`/`printing_id`/`variant_id` (quedan `null`) |
| `import` | **Copia** las fotos de una carpeta a `originals/`, calcula SHA-256, valida y registra | No mueve, edita ni borra el origen; no sube nada |
| `review` | Cambia el estado de fotos (`verified`, `rejected`, `needs_review`) y corrige condiciones | `verified` exige `--by` y carta verificada; se niega ante banderas bloqueantes o duplicados salvo `--accept-*` |
| `validate` | Comprueba esquema, archivos, SHA-256, huérfanos, coherencia de identidad y cobertura del piloto | Solo lee |
| `split` | Separa **sesiones** en calibración/prueba y busca fugas | No mueve fotos |
| `export` | Copia las fotos verificadas y escribe `manifest.csv/json` + `SHA256SUMS.txt` | No sobrescribe: exige carpeta nueva o vacía; no exporta dentro de `originals/` |
| `backup` | Copia todo a `<dest>\real-photos-backup-<fecha>` y verifica cada archivo por SHA-256 | No borra nada en origen ni destino |
| `references-fetch` | **Lee** de R2 (GET) únicamente los objetos que una persona puso en `reference_object_key` de cartas verificadas | **No escribe** en R2 |
| `evaluate` | Ejecuta la evaluación (ver `04-protocolo-de-evaluacion.md`) | Se niega con datos ficticios |
| `example` | Regenera el manifiesto de ejemplo ficticio | |

### Importación con metadatos por archivo
`--sidecar plantilla.csv` (ver `docs/dataset/plantilla-metadatos.csv`): columnas `filename`, `lighting_condition`, `camera_angle`, `background`, `sleeve_condition`, `glare`, `focus_quality`, `notes`.
Los valores inválidos detienen la importación antes de copiar nada.

### Detección de duplicados y calidad (al importar)
- **Duplicado exacto** (mismo SHA-256 en cualquier sesión): se omite y se informa.
- **Posible duplicado entre sesiones** (hash perceptual dHash a ≤ 4 bits de otra foto de la misma carta en otra sesión): se importa como `needs_review`, porque puede ser la misma toma guardada dos veces (fuga).
- **Calidad**: resolución, nitidez, brillo y reflejo; formatos admitidos JPEG/PNG/WebP (HEIC se rechaza con un mensaje claro).

## 3.3 Respaldo y recuperación
1. **Después de cada sesión:** `dataset.ps1 backup --dest D:\Respaldos` (otro disco o unidad externa; no la dejes dentro del repositorio).
2. El respaldo incluye `originals`, `manifest`, `splits`, `references` y `dataset.json`, y se verifica archivo por archivo.
3. **Restaurar:** copia la carpeta del respaldo a `datasets\real-photos` (con el PC sin importar nada en ese momento) y ejecuta `dataset.ps1 validate`.
4. Regla 3-2-1 recomendada: 3 copias, 2 soportes distintos, 1 fuera del PC. Si usas un servicio en la nube, que sea **tuyo y cifrado**: estas fotos no deben ir a R2 de Carpetazo.
5. **Privacidad:** las fotos pueden contener ubicación (GPS) en el EXIF. Los originales se conservan intactos (por eso el manifiesto solo guarda `has_gps`); no compartas las exportaciones fuera de este equipo sin limpiar el EXIF.
