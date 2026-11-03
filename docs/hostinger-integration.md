# Pruebas de integración en el VPS Hostinger (guía futura)

> **Estado:** solo documentación. No se ha conectado ni modificado nada en el VPS.
> **Cuándo usarla:** cuando tu PC no tenga pgvector, o para probar en Linux real.

## Reglas

- No tocar Coolify, sus contenedores ni la base de datos de producción de Carpetazo (`Carpetazo DB`).
- No usar credenciales de producción: ni las de Coolify, ni las de R2 de producción, ni `DATABASE_URL` de producción.
- No abrir puertos al exterior. Todo escucha en `127.0.0.1` y se accede por **túnel SSH**.
- Una base y un usuario **propios y desechables**, con nombres que no se confundan (`vision_dev`, `vision_test`).
- Pide autorización antes de cada paso que instale paquetes o cambie el VPS: esta guía es para que lo hagas tú.

## 0. Reconocimiento (solo lectura)

```bash
ssh <usuario>@<vps>
lsb_release -a; python3 --version          # Ubuntu 24.04 trae Python 3.12
ss -ltnp | grep -E ':(5432|5433|5440)\b'   # qué puertos de PostgreSQL ya están en uso
free -h; df -h /                           # el VPS (≈ 8 GB) comparte RAM con producción
```

Elige un puerto libre (esta guía usa **5440**) y no uses el 5432 si aparece ocupado.

## 1. PostgreSQL + pgvector nativos, en un clúster aparte

Un clúster independiente evita tocar cualquier PostgreSQL existente.

```bash
sudo apt-get update
sudo apt-get install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh     # repositorio oficial PGDG
# Evita que se cree un clúster 'main' por defecto:
sudo sed -i 's/^#\?create_main_cluster.*/create_main_cluster = false/' /etc/postgresql-common/createcluster.conf
sudo apt-get install -y postgresql-16 postgresql-16-pgvector      # (o la versión mayor que prefieras)

sudo pg_createcluster 16 vision --port 5440 --start
sudo -u postgres psql -p 5440 -c "ALTER SYSTEM SET max_connections = 20"
sudo -u postgres psql -p 5440 -c "ALTER SYSTEM SET shared_buffers = '128MB'"
sudo systemctl restart postgresql@16-vision
```

Por defecto un clúster nuevo escucha solo en `localhost`. Verifica: `ss -ltn | grep 5440` debe mostrar `127.0.0.1:5440`.

## 2. Base y usuario de desarrollo

```bash
sudo -u postgres psql -p 5440 <<'SQL'
CREATE ROLE vision_dev LOGIN PASSWORD '<contraseña-de-desarrollo>';
CREATE DATABASE vision_dev  OWNER vision_dev;
CREATE DATABASE vision_test OWNER vision_dev;
\c vision_dev
CREATE EXTENSION vector;
\c vision_test
CREATE EXTENSION vector;
SQL
```

## 3. Código y entorno Python en el VPS

```bash
git clone <tu-repo-privado> ~/carpetazo-vision-engine     # o: rsync -a --exclude .venv --exclude evaluation/reports ...
cd ~/carpetazo-vision-engine
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env && chmod 600 .env
```

En `.env` (credenciales de **desarrollo**):

```
VISION_DATABASE_URL=postgresql+psycopg://vision_dev:<contraseña-de-desarrollo>@127.0.0.1:5440/vision_dev
VISION_SECRET_KEY=<generada>
VISION_SERVICE_API_KEYS=<generada>
```

## 4. Migraciones, verificación y pruebas (con pgvector real)

```bash
alembic upgrade head
python scripts/dev/pg_check.py --expect-head        # extensión, tablas, índices

export VISION_TEST_POSTGRES_URL="postgresql+psycopg://vision_dev:<contraseña>@127.0.0.1:5440/vision_test"
pytest -q -m postgres -rs                           # SIN VISION_TEST_ALLOW_VECTOR_STUB: aquí sí se valida pgvector
pytest -q                                           # toda la suite
```

Importación limitada de prueba (solo lectura contra TCGdex, escribe en `vision_dev`):

```bash
python -m app.cli catalog-import --language en --set base1 --set sv10.5b --dry-run
python -m app.cli catalog-import --language en --set base1 --set sv10.5b
python scripts/dev/inspect_catalog.py
```

## 5. Acceder desde tu PC sin abrir puertos

```powershell
ssh -N -L 5440:127.0.0.1:5440 <usuario>@<vps>      # deja esta ventana abierta
# y en .env de tu PC: VISION_DATABASE_URL=postgresql+psycopg://vision_dev:<contraseña>@127.0.0.1:5440/vision_dev
```

## 6. Revertir

| Qué | Cómo |
|---|---|
| Una migración | `alembic downgrade -1` (o `alembic downgrade 0003`) |
| Todo el esquema | `alembic downgrade base` |
| Importación mala | restaurar el respaldo previo (`pg_dump -Fc -p 5440 vision_dev > vision_dev.dump`; `pg_restore -c -d vision_dev vision_dev.dump`). El importador nunca borra filas. |
| Todo el entorno de pruebas | `sudo pg_dropcluster --stop 16 vision` (solo borra este clúster) |

Haz el `pg_dump` **antes** de importaciones grandes y antes de cualquier `downgrade`.

## 7. Qué no hacer

- No ejecutar nada de esto contra el PostgreSQL de Coolify ni usar su puerto o sus credenciales.
- No publicar el puerto 5440 (ni `ufw allow 5440`).
- No importar el catálogo completo en horas de tráfico: son decenas de miles de peticiones a TCGdex.
- No ejecutar `images-apply` hasta que haya alias confirmados por una persona.
