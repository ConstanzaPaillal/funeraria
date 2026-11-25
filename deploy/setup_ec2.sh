#!/usr/bin/env bash
# =============================================================================
# Despliegue de Cristo Rey en UNA instancia EC2 (Ubuntu 22.04/24.04, AWS Academy)
# PostgreSQL + Django (gunicorn) + Nginx, todo en la misma máquina.
#
# Uso (dentro de la instancia, con el código ya clonado en ~/cristorey):
#     cd ~/cristorey
#     bash deploy/setup_ec2.sh
#
# Es idempotente: puedes volver a ejecutarlo tras hacer `git pull` para
# actualizar dependencias, migraciones y archivos estáticos.
# =============================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(whoami)"
DB_NAME="${DB_NAME:-cristorey}"
DB_USER="${DB_USER:-cristorey}"

echo "==> Instalando paquetes del sistema"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip postgresql postgresql-contrib nginx curl

# --- IP pública (IMDSv2) -----------------------------------------------------
TOKEN="$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
IP_PUBLICA="$(curl -s -H "X-aws-ec2-metadata-token: ${TOKEN}" http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
IP_PUBLICA="${IP_PUBLICA:-localhost}"
echo "    IP pública detectada: ${IP_PUBLICA}"

# --- .env (solo se crea si no existe, para no pisar tus claves) --------------
if [ ! -f "${APP_DIR}/.env" ]; then
  echo "==> Creando .env con claves aleatorias"
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  DB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "${APP_DIR}/.env" <<EOF
DJANGO_SECRET_KEY=${SECRET}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=${IP_PUBLICA},localhost,127.0.0.1
DJANGO_HTTPS=False
DJANGO_CSRF_TRUSTED_ORIGINS=http://${IP_PUBLICA}
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASS}
DB_HOST=localhost
DB_PORT=5432
EOF
  chmod 600 "${APP_DIR}/.env"
fi
set -a; source "${APP_DIR}/.env"; set +a

# --- PostgreSQL --------------------------------------------------------------
echo "==> Configurando PostgreSQL (rol y base de datos)"
sudo systemctl enable --now postgresql
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';"
else
  sudo -u postgres psql -c "ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
fi

# --- Entorno Python ----------------------------------------------------------
echo "==> Creando entorno virtual e instalando dependencias"
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

# --- Django ------------------------------------------------------------------
echo "==> Migraciones y archivos estáticos"
cd "${APP_DIR}"
"${APP_DIR}/venv/bin/python" manage.py migrate --noinput
"${APP_DIR}/venv/bin/python" manage.py collectstatic --noinput
mkdir -p "${APP_DIR}/media"

# --- gunicorn (systemd) ------------------------------------------------------
echo "==> Instalando servicio systemd"
sed -e "s#__APP_DIR__#${APP_DIR}#g" -e "s#__APP_USER__#${APP_USER}#g" \
  "${APP_DIR}/deploy/cristorey.service" | sudo tee /etc/systemd/system/cristorey.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable cristorey
sudo systemctl restart cristorey

# --- Nginx -------------------------------------------------------------------
echo "==> Configurando Nginx"
sed -e "s#__APP_DIR__#${APP_DIR}#g" "${APP_DIR}/deploy/nginx_cristorey.conf" \
  | sudo tee /etc/nginx/sites-available/cristorey >/dev/null
sudo ln -sf /etc/nginx/sites-available/cristorey /etc/nginx/sites-enabled/cristorey
sudo rm -f /etc/nginx/sites-enabled/default
# Nginx (usuario www-data) debe poder recorrer el home para leer media/
chmod o+x "$HOME"
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx

echo
echo "==> Listo. Abre http://${IP_PUBLICA}/"
echo "    Pasos opcionales:"
echo "      ${APP_DIR}/venv/bin/python manage.py createsuperuser   # tu propio admin"
echo "      ${APP_DIR}/venv/bin/python manage.py seed_demo         # datos de demostración"
echo "    Recuerda abrir el puerto 80 (HTTP) en el Security Group de la instancia."
