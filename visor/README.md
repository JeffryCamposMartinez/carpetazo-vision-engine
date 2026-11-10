# Visor de cartas (prueba exploratoria)

Visor web para el celular: apuntas la cámara a una carta, se detecta sola y se buscan las candidatas en el catálogo de Carpetazo.cl (Pokémon EN/ES/JP, Magic, Yu-Gi-Oh, One Piece, Lorcana, Digimon, Riftbound y Mitos y Leyendas).

> Las coincidencias son **candidatas sin verificar**: no hay identidad confirmada y la impresión exacta puede variar. No escribe en Carpetazo ni en R2.

## Piezas
| Archivo | Para qué |
|---|---|
| `server.py` | Servidor FastAPI con HTTPS: sirve la página, recibe los cuadros (`/scan`), busca en el índice y re-ordena con el texto leído |
| `index.html` | Página del celular: cámara, marco, filtro por juego, comprobación de movimiento / nitidez / luz y resultado |
| `build_index.py` | Crea el índice de embeddings DINOv2 del catálogo leyendo cada imagen por su URL pública, **en memoria** (no se guardan imágenes) |
| `ocr_worker.ps1` | OCR del motor de Windows (en-US / es-ES), sin instalar nada |
| `umbrales.example.json` | Umbrales ajustables en vivo (copiar a `data/umbrales.json`) |

## Cómo se usa
1. Sincronizar el catálogo al espejo local (SQLite):
   ```powershell
   python -X utf8 -m app.catalog_mirror.cli sync --categories 3 100 85 1 2 63 68 71 89 99 --database-url sqlite:///visor/data/catalogo.sqlite
   ```
   (Antes hay que crear las tablas con `Base.metadata.create_all`, ver `docs/catalog-mirror.md`.)
2. Crear el índice (reanudable; unas 15 imágenes por segundo en una GTX 960):
   ```powershell
   python -X utf8 visor/build_index.py visor/data/catalogo.sqlite visor/data/indice
   ```
3. Generar un certificado de prueba (la cámara del celular exige HTTPS) y arrancar:
   ```powershell
   openssl req -x509 -newkey rsa:2048 -nodes -keyout visor/data/key.pem -out visor/data/cert.pem -days 30 -subj "/CN=visor" -addext "subjectAltName=IP:<IP-de-tu-PC>"
   $env:VISOR_CATALOG_DB="visor/data/catalogo.sqlite"; $env:VISOR_INDEX_DIR="visor/data/indice"
   python -X utf8 visor/server.py visor/data/cert.pem visor/data/key.pem 8443
   ```
4. Abrir `https://<IP-de-tu-PC>:8443` en el celular (misma Wi-Fi), aceptar el aviso del certificado y dar permiso a la cámara.

Variables: `VISOR_CATALOG_DB`, `VISOR_INDEX_DIR`, `VISOR_DATA_DIR` (telemetría, escaneos y `umbrales.json`; por defecto `visor/data/`, ignorado por git).

## Cómo reconoce
1. Página: espera a que la carta esté quieta, enfocada y con luz usable, y manda el recorte del marco.
2. Servidor: detector de carta (E1) y recorte del marco → embedding DINOv2 → similitud con el índice.
3. Lee el nombre de la carta (OCR) y sube las candidatas con el mismo nombre; el margen se mide contra cartas de otro nombre (las reimpresiones no cuentan como duda).
4. La página confirma cuando la misma carta sale en 3 de las últimas 5 lecturas.

## Límites conocidos
- Umbrales provisionales; la precisión real no está medida con un conjunto verificado.
- El OCR de Windows no lee japonés; en cartas en español el nombre no coincide con el catálogo (los nombres del espejo están en inglés), y ahí solo ayuda la comparación visual.
- Los reflejos, las cartas inclinadas o en la mano y los fondos desordenados siguen siendo la principal causa de fallos.
