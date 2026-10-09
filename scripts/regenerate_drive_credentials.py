#!/usr/bin/env python3
"""Regenera config/credentials_module.json (el token OAuth de Google Drive
que usa GoogleDriveAuth en models/drive.py) desde cero.

Se corre en TU máquina (el host), no dentro del contenedor 'gofuv' — este
paso necesita abrir un navegador real para que completes el login/consentimiento
de Google, y el contenedor es headless (sin navegador), lo que hace que este
mismo flujo se cuelgue indefinidamente si se intenta ahí adentro.

Requisitos previos:
  1. Haber creado un nuevo ID de cliente OAuth tipo "App de escritorio" en
     Google Cloud Console y reemplazado config/client_secrets.json con el
     JSON descargado.
  2. Haber configurado la pantalla de consentimiento OAuth (tipo Externo,
     con tu correo agregado como "usuario de prueba").
  3. pydrive2 instalado en tu máquina: pip install pydrive2

Uso:
  python3 scripts/regenerate_drive_credentials.py

Qué hace:
  - Abre tu navegador para que inicies sesión con la cuenta de Google y
    aceptes el consentimiento.
  - get_refresh_token=True fuerza que Google SIEMPRE devuelva un refresh
    token (sin esto, una segunda autenticación con la misma cuenta puede
    no traer refresh token, que fue el problema original de esta sesión).
  - Guarda el resultado en config/credentials_module.json, sobreescribiendo
    el archivo roto/expirado.

Después de correrlo, desde el proyecto:
  chmod 666 config/credentials_module.json   # el contenedor corre con otro uid
  docker restart gofuv                       # limpia la instancia cacheada de GoogleDriveAuth
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "config")
CLIENT_SECRETS_PATH = os.path.join(CONFIG_DIR, "client_secrets.json")
CREDENTIALS_PATH = os.path.join(CONFIG_DIR, "credentials_module.json")

try:
    from pydrive2.auth import GoogleAuth
except ImportError:
    sys.exit(
        "Falta pydrive2 en esta máquina. Instálalo con:\n"
        "    pip install pydrive2\n"
        "y vuelve a correr este script."
    )

if not os.path.isfile(CLIENT_SECRETS_PATH):
    sys.exit(
        f"No se encontró {CLIENT_SECRETS_PATH}\n"
        "Descarga el JSON del nuevo ID de cliente OAuth (tipo 'App de "
        "escritorio') desde Google Cloud Console y guárdalo en esa ruta "
        "antes de correr este script."
    )

print(f"Usando client_secrets: {CLIENT_SECRETS_PATH}")
print(f"Se guardará el resultado en: {CREDENTIALS_PATH}")
print("Se va a abrir tu navegador para el login de Google...\n")

gauth = GoogleAuth(
    settings={
        "client_config_backend": "file",
        "client_config_file": CLIENT_SECRETS_PATH,
        "save_credentials": True,
        "save_credentials_backend": "file",
        "save_credentials_file": CREDENTIALS_PATH,
        "get_refresh_token": True,
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    }
)
gauth.LocalWebserverAuth()

if gauth.credentials and gauth.credentials.refresh_token:
    print("\n✅ Listo — credenciales guardadas con refresh token.")
    print(f"   {CREDENTIALS_PATH}")
    print("\nPasos que faltan (desde la raíz del proyecto):")
    print("   chmod 666 config/credentials_module.json")
    print("   docker restart gofuv")
else:
    print(
        "\n⚠️ Se guardó el archivo pero NO trae refresh_token — probablemente "
        "Google no mostró la pantalla de consentimiento de nuevo (sesión ya "
        "autorizada previamente). Revoca el acceso de la app en "
        "https://myaccount.google.com/permissions y vuelve a correr este "
        "script para forzar un consentimiento nuevo."
    )
