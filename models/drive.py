import base64
import os
import shutil
import tempfile
import uuid

import requests
import PyPDF2
import io
import re
from docx import Document  # Asegúrate de importar correctamente python-docx
import openpyxl  # Biblioteca para manejar archivos Excel
from io import BytesIO
import zipfile
import time
import pydrive2
from PyPDF2 import PdfReader
from concurrent.futures import ThreadPoolExecutor
from httplib2 import Credentials
from odoo import exceptions
from oauth2client import service_account
from odoo import models, fields, api, SUPERUSER_ID, _
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from datetime import datetime, timedelta
from odoo.exceptions import AccessError, UserError
import mimetypes
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from oauth2client.file import Storage
from odoo import registry as odoo_registry
import threading
import httplib2
from odoo.api import Environment
import logging

_logger = logging.getLogger(__name__)

# GOOGLE_DRIVE_LOCAL_DEV (ver GoogleDriveAuth.__init__ más abajo) se lee del
# .env local — mismo patrón que ODOO_DISABLE_RECAPTCHA (controllers/main.py)
# y MAILPIT_LOCAL_DEV (models/mailpit_local_dev.py): cada consumidor carga
# su propio .env en vez de depender del orden de import de otro archivo.
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
try:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_env_path)
except ImportError:
    _logger.warning(
        "Matriz: python-dotenv no está instalado; GOOGLE_DRIVE_LOCAL_DEV solo "
        "se leerá de variables de entorno reales del proceso, no del .env"
    )

_document_cleanup_lock = threading.Lock()


def _google_drive_local_dev_enabled():
    """Indica si está habilitado el aislamiento local de Google Drive."""
    return os.environ.get("GOOGLE_DRIVE_LOCAL_DEV", "").lower() in ("1", "true")


def _get_firma_test_drive_folder_id(env):
    """Obtiene el override de carpeta solo en desarrollo local.

    El parámetro puede quedar accidentalmente creado en una base compartida;
    por sí solo nunca debe modificar el destino de documentos reales.
    """
    if not _google_drive_local_dev_enabled():
        return False
    return (
        env["ir.config_parameter"]
        .sudo()
        .get_param("matriz.firma_test_drive_folder_id")
    )


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    _drive_temp_marker = "__drive_temp_download__"

    @api.model
    def _drive_create_temp_download(self, vals, hours=72):
        """Crea un adjunto solo para entregar una descarga al navegador.

        No reemplaza documentos oficiales ni archivos subidos a Google Drive:
        únicamente marca como temporal el binario local que Odoo necesita para
        responder /web/content/<attachment>.
        """
        clean_vals = dict(vals or {})
        expires_at = fields.Datetime.now() + timedelta(hours=hours)
        description = clean_vals.get("description") or ""
        marker = "%s expires=%s" % (
            self._drive_temp_marker,
            fields.Datetime.to_string(expires_at),
        )
        clean_vals["description"] = ("%s\n%s" % (description, marker)).strip()
        return self.sudo().create(clean_vals)

    @api.model
    def _cron_cleanup_drive_temp_downloads(self, legacy_days=7, batch_size=200):
        """Inicia la limpieza en segundo plano y devuelve el control enseguida."""
        if not _document_cleanup_lock.acquire(blocking=False):
            _logger.info("La limpieza documental ya se encuentra en ejecución.")
            return True

        dbname = self.env.cr.dbname
        uid = self.env.uid
        try:
            worker = threading.Thread(
                target=self._run_document_cleanup_worker,
                args=(dbname, uid, legacy_days, batch_size),
                name="MatrizAttachmentCleanup",
                daemon=True,
            )
            worker.start()
        except Exception:
            _document_cleanup_lock.release()
            raise
        _logger.info("Limpieza documental iniciada en segundo plano.")
        return True

    @staticmethod
    def _run_document_cleanup_worker(dbname, uid, legacy_days, batch_size):
        document_mimetypes = [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.oasis.opendocument.text",
            "application/vnd.oasis.opendocument.spreadsheet",
            "application/rtf",
            "text/rtf",
        ]
        try:
            # Permite que Odoo termine y libere la ejecución manual del ir.cron
            # antes de actualizar sus campos de progreso desde otro cursor.
            time.sleep(1)
            registry = odoo_registry(dbname)
            with registry.cursor() as cr:
                env = Environment(cr, uid, {})
                Attachment = env["ir.attachment"].sudo()
                cron = env.ref(
                    "Matriz.ir_cron_cleanup_drive_temp_downloads",
                    raise_if_not_found=False,
                )
                cutoff = fields.Datetime.subtract(
                    fields.Datetime.now(), days=legacy_days
                )
                domain = [
                    ("mimetype", "in", document_mimetypes),
                    ("create_date", "<", cutoff),
                ]
                total = Attachment.search_count(domain)
                _logger.info(
                    "Limpieza documental: %s documentos encontrados. "
                    "Procesando en lotes de %s.",
                    total,
                    batch_size,
                )
                if cron:
                    cron.sudo().write(
                        {
                            "cleanup_running": True,
                            "cleanup_progress": 0.0,
                            "cleanup_done": 0,
                            "cleanup_total": total,
                            "cleanup_state": (
                                "Eliminando documentos..."
                                if total
                                else "Sin documentos por eliminar"
                            ),
                        }
                    )
                    cr.commit()

                done = 0
                while True:
                    documents = Attachment.search(domain, order="id", limit=batch_size)
                    if not documents:
                        break
                    deleted = len(documents)
                    documents.unlink()
                    done += deleted
                    progress = min(
                        100.0,
                        (done * 100.0 / total) if total else 100.0,
                    )
                    if cron:
                        cron.sudo().write(
                            {
                                "cleanup_done": done,
                                "cleanup_progress": progress,
                                "cleanup_state": "Eliminando documentos...",
                            }
                        )
                    cr.commit()
                    _logger.info(
                        "Limpieza documental en progreso: %s/%s eliminados (%.2f%%).",
                        done,
                        total,
                        progress,
                    )
                    env.clear()

                if cron:
                    cron.sudo().write(
                        {
                            "cleanup_running": False,
                            "cleanup_done": done,
                            "cleanup_progress": 100.0 if total else 0.0,
                            "cleanup_state": (
                                "Finalizada: %s documentos eliminados" % done
                                if total
                                else "Sin documentos para eliminar"
                            ),
                        }
                    )
                    cr.commit()
                _logger.info(
                    "Limpieza documental finalizada: %s documentos eliminados; "
                    "assets e imágenes conservados.",
                    done,
                )
        except Exception as error:
            _logger.exception("Error durante la limpieza documental en segundo plano")
            try:
                registry = odoo_registry(dbname)
                with registry.cursor() as cr:
                    env = Environment(cr, SUPERUSER_ID, {})
                    cron = env.ref(
                        "Matriz.ir_cron_cleanup_drive_temp_downloads",
                        raise_if_not_found=False,
                    )
                    if cron:
                        cron.sudo().write(
                            {
                                "cleanup_running": False,
                                "cleanup_state": "Error: %s" % str(error)[:200],
                            }
                        )
                        cr.commit()
            except Exception:
                _logger.exception("No fue posible registrar el error de la limpieza")
        finally:
            _document_cleanup_lock.release()


class IrCron(models.Model):
    _inherit = "ir.cron"

    is_document_cleanup_cron = fields.Boolean(
        string="Es limpieza documental",
        compute="_compute_is_document_cleanup_cron",
    )
    cleanup_running = fields.Boolean(string="Limpieza en ejecución", readonly=True)
    cleanup_state = fields.Char(string="Estado de la limpieza", readonly=True)
    cleanup_state_display = fields.Char(
        string="Estado visible",
        compute="_compute_cleanup_state_display",
    )
    cleanup_total = fields.Integer(string="Documentos encontrados", readonly=True)
    cleanup_done = fields.Integer(string="Documentos eliminados", readonly=True)
    cleanup_progress = fields.Float(string="Progreso", readonly=True)
    cleanup_progress_display = fields.Float(
        string="Progreso visible",
        compute="_compute_cleanup_progress_display",
    )

    @api.depends("code")
    def _compute_is_document_cleanup_cron(self):
        for cron in self:
            cron.is_document_cleanup_cron = bool(
                cron.code and "_cron_cleanup_drive_temp_downloads" in cron.code
            )

    @api.depends("cleanup_total", "cleanup_done", "cleanup_running", "cleanup_progress")
    def _compute_cleanup_progress_display(self):
        for cron in self:
            if not cron.cleanup_total:
                cron.cleanup_progress_display = 0.0
            elif cron.cleanup_running:
                cron.cleanup_progress_display = min(
                    100.0,
                    cron.cleanup_done * 100.0 / cron.cleanup_total,
                )
            else:
                cron.cleanup_progress_display = cron.cleanup_progress

    @api.depends("cleanup_state", "cleanup_total", "cleanup_running")
    def _compute_cleanup_state_display(self):
        for cron in self:
            if not cron.cleanup_running and not cron.cleanup_total:
                cron.cleanup_state_display = "Sin documentos para eliminar"
            else:
                cron.cleanup_state_display = cron.cleanup_state

    def action_refresh_cleanup_progress(self):
        for cron in self:
            if not cron.cleanup_running and not cron.cleanup_total:
                cron.sudo().write(
                    {
                        "cleanup_progress": 0.0,
                        "cleanup_done": 0,
                        "cleanup_state": "Sin documentos para eliminar",
                    }
                )
        return {"type": "ir.actions.client", "tag": "reload"}


def _get_next_prefix_for_folder(drive, parent_folder_id, parent_name=None):
    """Retorna el siguiente prefijo secuencial para un nuevo ítem dentro de parent_folder_id."""
    if parent_name is None:
        try:
            pf = drive.CreateFile({"id": parent_folder_id})
            pf.FetchMetadata(fields="title")
            parent_name = pf.get("title") or ""
        except Exception:
            parent_name = ""
    # Extraer prefijo numérico del nombre del padre (ej. "3.1 Nombre" → "3.1")
    m = re.match(r"^(\d+(?:\.\d+)*)", parent_name or "")
    parent_prefix = m.group(1) if m else ""
    try:
        items = drive.ListFile(
            {"q": f"'{parent_folder_id}' in parents and trashed=false"}
        ).GetList()
    except Exception:
        items = []
    max_seq = 0
    if parent_prefix:
        pattern = re.compile(r"^" + re.escape(parent_prefix) + r"\.(\d+)(?:\s|_|\.|$)")
    else:
        pattern = re.compile(r"^(\d+)(?:\s|_|\.|$)")
    for item in items:
        item_name = item.get("title") or item.get("name") or ""
        mm = pattern.match(item_name)
        if mm:
            try:
                max_seq = max(max_seq, int(mm.group(1)))
            except ValueError:
                pass
    next_seq = max_seq + 1
    return f"{parent_prefix}.{next_seq}" if parent_prefix else str(next_seq)


class GoogleDriveAuth:
    _instance = None
    _credentials = None
    _gauth = None
    _drive = None

    @classmethod
    def get_instance(cls):
        start_time = time.time()
        if cls._instance is None:
            _logger.info("Creando nueva instancia de GoogleDriveAuth")
            cls._instance = cls()
        _logger.info(
            f"Tiempo de obtención de instancia: {time.time() - start_time:.4f} segundos"
        )
        return cls._instance

    def __init__(self, http_timeout_seconds=None):
        start_time = time.time()
        self.http_timeout_seconds = http_timeout_seconds
        self.current_dir = os.path.dirname(os.path.abspath(__file__))
        _config_dir = os.path.join(os.path.dirname(self.current_dir), "config")

        # GOOGLE_DRIVE_LOCAL_DEV=1 en el .env local (mismo patrón que
        # MAILPIT_LOCAL_DEV, ver models/mailpit_local_dev.py) hace que se
        # usen credenciales separadas para desarrollo local
        # (client_secrets.local.json / credentials_module.local.json, ambas
        # gitignoreadas — ver .gitignore) en vez de las de
        # config/client_secrets.json / config/credentials_module.json. Así
        # las pruebas con una cuenta personal de Google nunca sobrescriben
        # ni dependen de las credenciales reales que se usarían en
        # producción. Sin esta variable (el caso por defecto, incluida
        # producción), el comportamiento es exactamente el de siempre.
        if _google_drive_local_dev_enabled():
            self.client_secrets_path = os.path.join(
                _config_dir, "client_secrets.local.json"
            )
            local_credentials_source = os.path.join(
                _config_dir, "credentials_module.local.json"
            )
            # El addon está montado desde el host y el usuario `odoo` del
            # contenedor normalmente no puede reescribir ese archivo cuando
            # oauth2client renueva el token. Usar una copia privada dentro
            # del HOME persistente de Odoo evita permisos 666 sobre secretos.
            runtime_config_dir = os.path.join(
                os.path.expanduser("~"), ".config", "Matriz"
            )
            os.makedirs(runtime_config_dir, mode=0o700, exist_ok=True)
            self.credentials_path = os.path.join(
                runtime_config_dir, "credentials_module.local.json"
            )
            if not os.path.exists(self.credentials_path):
                shutil.copyfile(local_credentials_source, self.credentials_path)
                os.chmod(self.credentials_path, 0o600)
        else:
            self.client_secrets_path = os.path.join(_config_dir, "client_secrets.json")
            self.credentials_path = os.path.join(_config_dir, "credentials_module.json")
        _logger.info(
            f"Tiempo de inicialización: {time.time() - start_time:.4f} segundos"
        )

    def _enter_(self):
        return self

    def _exit_(self, exc_type, exc_val, exc_tb):
        start_time = time.time()
        if self._drive:
            self._drive = None
        if self._gauth:
            self._gauth = None
        _logger.info(
            f"Tiempo de limpieza de recursos: {time.time() - start_time:.4f} segundos"
        )

    def get_gauth(self):
        start_time = time.time()
        if self._gauth is None:
            _logger.info("Iniciando nueva autenticación")
            self._gauth = self._get_new_gauth()
        _logger.info(
            f"Tiempo de obtención de autenticación: {time.time() - start_time:.4f} segundos"
        )
        return self._gauth

    def _get_new_gauth(self):
        start_time = time.time()
        try:
            gauth = GoogleAuth()
            gauth.LoadClientConfigFile(self.client_secrets_path)

            storage = Storage(self.credentials_path)
            credentials = storage.get()

            if credentials is None or credentials.invalid:
                _logger.info("Realizando nueva autenticación local")
                gauth.LocalWebserverAuth()
                gauth.SaveCredentialsFile(self.credentials_path)
            else:
                _logger.info("Usando credenciales existentes")
                gauth.credentials = credentials

            # El timeout afecta solo a esta instancia, no al cliente compartido.
            if self.http_timeout_seconds:
                def _get_http_object_with_timeout():
                    active_credentials = gauth.credentials
                    if (
                        hasattr(active_credentials, "access_token_expired")
                        and active_credentials.access_token_expired
                    ):
                        gauth.Refresh()
                        active_credentials = gauth.credentials
                    return active_credentials.authorize(
                        httplib2.Http(timeout=self.http_timeout_seconds)
                    )

                gauth.Get_Http_Object = _get_http_object_with_timeout

            auth_time = time.time() - start_time
            _logger.info(f"Tiempo de nueva autenticación: {auth_time:.4f} segundos")
            return gauth

        except Exception as e:
            _logger.error(f"Error en la autenticación: {str(e)}")
            raise

    def get_drive(self):
        start_time = time.time()
        if not self._drive:
            _logger.info("Creando nueva instancia de Google Drive")
            self._drive = GoogleDrive(self.get_gauth())
        _logger.info(
            f"Tiempo de obtención de Drive: {time.time() - start_time:.4f} segundos"
        )
        return self._drive

    def refresh_auth(self):
        start_time = time.time()
        _logger.info("Renovando autenticación")
        self._gauth = None
        self._drive = None
        result = self.get_gauth()
        _logger.info(
            f"Tiempo de renovación de autenticación: {time.time() - start_time:.4f} segundos"
        )
        return result

    def get_cache_status(self):
        """Método para verificar el estado del caché"""
        return {
            "has_gauth": self._gauth is not None,
            "has_drive": self._drive is not None,
        }


def ejemplo_uso():
    """Función de ejemplo que muestra cómo usar la clase"""
    start_time = time.time()

    try:
        # Usar el contexto manager para asegurar la limpieza de recursos
        with GoogleDriveAuth.get_instance() as drive_auth:
            # Obtener la instancia de drive
            drive = drive_auth.get_drive()

            # Mostrar el estado del caché
            cache_status = drive_auth.get_cache_status()
            _logger.info(f"Estado del caché: {cache_status}")

            # Ejemplo de renovación de autenticación
            drive_auth.refresh_auth()

            # Aquí irían tus operaciones con Google Drive
            # Por ejemplo:
            # file_list = drive.ListFile({'q': "'root' in parents"}).GetList()

    except Exception as e:
        _logger.error(f"Error durante la ejecución: {str(e)}")
        raise
    finally:
        total_time = time.time() - start_time
        _logger.info(f"Tiempo total de ejecución: {total_time:.4f} segundos")


if __name__ == "__main__":
    # Configurar el formato de logging para incluir timestamps
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
    )

    # Ejecutar el ejemplo
    ejemplo_uso()


class DriveTrace(models.Model):
    _name = "drive.trace"
    _description = "Trazabilidad de Documentos de Drive"

    drive_id = fields.Many2one(
        "drive", string="Documento", required=True, ondelete="cascade"
    )
    action_type = fields.Selection(
        [
            ("create", "Creación"),
            ("update", "Actualización"),
            ("delete", "Eliminación"),
            ("share", "Compartido"),
            ("download", "Descarga"),
            ("rename", "Cambio de nombre"),
        ],
        string="Tipo de Acción",
        required=True,
    )
    action_date = fields.Datetime(string="Fecha de Acción", default=fields.Datetime.now)
    user_id = fields.Many2one("res.users", string="Usuario", required=True)
    notes = fields.Text(string="Notas")


###################################Cambio QAP ###############################################


def _count_pdf_pages_raw(pdf_bytes):
    """
    Cuenta páginas escaneando la estructura binaria del PDF.
    No requiere pycryptodome — funciona incluso en PDFs con cifrado AES.
    Usa lookahead negativo para no confundir /Type /Page con /Type /Pages.
    """
    try:
        import re as _re

        # /Type /Page o /Type/Page, pero NO seguido de 's' (/Pages = nodo árbol)
        count = len(_re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))
        if count == 0:
            # Fallback: buscar /Count N en el árbol de páginas raíz
            match = _re.search(rb"/Count\s+(\d+)", pdf_bytes)
            if match:
                return int(match.group(1))
        return count
    except Exception:
        return 0


class Drive(models.Model):
    _name = "drive"
    _description = "Esto conecta Odoo con Drive"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    def message_post(self, **kwargs):
        portal_login_id = self.env.context.get("portal_chatter_login_id")
        if portal_login_id and not kwargs.get("author_id"):
            login = self.env["login"].sudo().browse(portal_login_id)
            if login.exists():
                partner = login._get_portal_chatter_partner()
                if partner:
                    kwargs["author_id"] = partner.id
        return super(Drive, self).message_post(**kwargs)

    def _log_document_activity(self, action_type, notes):
        for record in self:
            message = notes or ""
            record.message_post(body=message)
            self.env["drive.trace"].sudo().create(
                {
                    "drive_id": record.id,
                    "action_type": action_type,
                    "user_id": self.env.user.id,
                    "notes": message,
                }
            )
            if (
                record.informes_id
                and record.informes_id.selected_drive_id.id == record.id
            ):
                record.informes_id.selected_drive_activity_html = (
                    record.informes_id._activity_html_for_drive(record)
                )
            if (
                record.comunicaciones_id
                and record.comunicaciones_id.selected_drive_id.id == record.id
            ):
                record.comunicaciones_id.selected_drive_activity_html = (
                    record.comunicaciones_id._activity_html_for_drive(record)
                )

    def _drive_direct_download_action(
        self, target_id, final_name=None, fmt="original", mime_type=None
    ):
        """Abre la descarga desde Google Drive sin crear un ir.attachment local."""
        if not target_id:
            raise UserError("No se encontró el archivo en Google Drive.")

        fmt = fmt or "original"
        mime_type = mime_type or ""

        if fmt == "pdf":
            if mime_type == "application/vnd.google-apps.document":
                url = (
                    f"https://docs.google.com/document/d/{target_id}/export?format=pdf"
                )
            elif mime_type == "application/vnd.google-apps.spreadsheet":
                url = f"https://docs.google.com/spreadsheets/d/{target_id}/export?format=pdf"
            elif mime_type == "application/vnd.google-apps.presentation":
                url = f"https://docs.google.com/presentation/d/{target_id}/export/pdf"
            else:
                url = f"https://drive.google.com/uc?export=download&id={target_id}"
        elif fmt == "word":
            if mime_type == "application/vnd.google-apps.document":
                url = (
                    f"https://docs.google.com/document/d/{target_id}/export?format=docx"
                )
            elif mime_type == "application/vnd.google-apps.spreadsheet":
                url = f"https://docs.google.com/spreadsheets/d/{target_id}/export?format=xlsx"
            elif mime_type == "application/vnd.google-apps.presentation":
                url = f"https://docs.google.com/presentation/d/{target_id}/export/pptx"
            else:
                url = f"https://drive.google.com/uc?export=download&id={target_id}"
        else:
            url = f"https://drive.google.com/uc?export=download&id={target_id}"

        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "self",
        }

    # LEGALIZACIONES
    viaticos_otros_id = fields.Many2one("viaticos", string="Contrato Relacionado")
    is_folder = fields.Boolean(string="Es carpeta", default=False)
    ###################################Nuevo campos informes (Teylor) ###############################################

    observaion_coodinador = fields.Text(string="Comentario")
    informes_id = fields.Many2one("informes_drive", string="Informes")
    comunicaciones_id = fields.Many2one("comunicaciones_drive", string="Comunicaciones")
    descrpcion_info = fields.Text(string="Descripción")
    fecha = fields.Date(string="Fecha")
    invisible_todo = fields.Boolean(default=False)

    page_range = fields.Char(
        "Rango de Páginas",
        readonly=True,
        help="Rango completo de páginas del PDF (ej. 1-12).",
    )
    page_total = fields.Integer(
        "Total de Páginas",
        readonly=True,
        help="Rango completo de páginas del PDF (ej. 1-12).",
    )

    # Nuevo campo booleano
    ejecutar_toggle = fields.Boolean(string="Ejecutar Toggle Sidebar", default=False)
    ejecutar_inf = fields.Boolean(string="Ejecutar Toggle Sidebar")
    ejecutar_inf_carpeta = fields.Boolean(
        string="Ejecutar Toggle Sidebar", default=False
    )

    def action_open_drive(self):
        # Si es una carpeta, navegar a su contenido
        if self.is_folder and self.folder_id:
            sub_file = self.env["drive.file"].search(
                [("folder_id2", "=", self.folder_id)], limit=1
            )
            if sub_file:
                return sub_file.action_open_drive()

            # Sin drive.file asociado: abrir kanban drive filtrado por folder_id
            return {
                "type": "ir.actions.act_window",
                "name": self.file_name or "Carpeta",
                "res_model": "drive",
                "view_mode": "kanban,tree,form",
                "target": "current",
                "domain": [
                    ("folder_id", "=", self.folder_id),
                    ("is_folder", "=", False),
                ],
                "context": {
                    "default_folder_id": self.folder_id,
                    "default_stage2_id": self.stage2_id.id if self.stage2_id else False,
                },
            }

        return {
            "name": self.name,
            "type": "ir.actions.act_window",
            "res_model": "drive",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def action_save_and_process(self):
        for record in self:
            _logger.error("=== ACTION_SAVE_AND_PROCESS | START ===")
            _logger.error("Drive ID: %s", record.id)
            _logger.error("Inicial page_range: %s", record.page_range)
            _logger.error(
                "Informes ID: %s", record.informes_id.id if record.informes_id else None
            )
            _logger.error(
                "Comunicaciones ID: %s",
                record.comunicaciones_id.id if record.comunicaciones_id else None,
            )

            # 1) Gate duro
            if not (record.informes_id or record.comunicaciones_id):
                raise UserError("Solo aplica para Informes o Comunicaciones.")

            if not record.file_data or not record.file_name:
                raise UserError("Adjunte un archivo antes de procesar.")

            # 2) Asegurar stage2_id desde carpeta seleccionada
            stage_id = False
            if record.informes_id and record.informes_id.selected_workspace_id:
                stage_id = record.informes_id.selected_workspace_id.drive_file_id.id

            if (
                record.comunicaciones_id
                and record.comunicaciones_id.selected_workspace_id
            ):
                stage_id = (
                    record.comunicaciones_id.selected_workspace_id.drive_file_id.id
                )

            if stage_id:
                record.with_context(skip_drive_sync=True, skip_integrity=True).write(
                    {"stage2_id": stage_id}
                )

            # 3) Conteo páginas (SOLO metadata, NO sube)
            if record.file_name and record.file_name.lower().endswith(".pdf"):
                paginas = record._get_pdf_page_count(record.file_data)
                _logger.error("PDF pages => %s", paginas)

                if paginas > 0:
                    record.with_context(
                        skip_drive_sync=True, skip_integrity=True
                    ).write(
                        {
                            "page_total": paginas,
                            "page_range": f"1-{paginas}",
                        }
                    )
                else:
                    # PDF encriptado (AES) u otro caso sin conteo — se sube igual sin folios
                    record.with_context(
                        skip_drive_sync=True, skip_integrity=True
                    ).write(
                        {
                            "page_total": 0,
                            "page_range": False,
                        }
                    )
            else:
                record.with_context(skip_drive_sync=True, skip_integrity=True).write(
                    {
                        "page_total": 0,
                        "page_range": False,
                    }
                )

            _logger.error(
                "POST page_range write => page_total=%s page_range=%s",
                record.page_total,
                record.page_range,
            )

            # 4) Subida 1 sola vez
            _logger.error("UPLOAD_TO_DRIVE | START")
            record.upload_to_drive()
            _logger.error("UPLOAD_TO_DRIVE | END")

            # 5) Forzar recompute en padre
            if record.informes_id:
                before = record.informes_id.sync_version
                record.informes_id.write({"sync_version": before + 1})
                val = record.informes_id.read(["folios"])[0].get("folios")
                _logger.error("INFORMES folios LEIDOS => %s", val)

            if record.comunicaciones_id:
                before = record.comunicaciones_id.sync_version
                record.comunicaciones_id.write({"sync_version": before + 1})
                val = record.comunicaciones_id.read(["folios"])[0].get("folios")
                _logger.error("COMUNICACIONES folios LEIDOS => %s", val)

            _logger.error("=== ACTION_SAVE_AND_PROCESS | END ===")

        return {"type": "ir.actions.act_window_close"}

    def _get_pdf_page_count(self, file_data_b64):
        """
        Recibe el binario en base64, lo decodifica y cuenta páginas.
        Retorna el número de páginas (int).
        Lanza excepción si falla.
        """
        try:
            file_content = base64.b64decode(file_data_b64)
            with io.BytesIO(file_content) as stream:
                try:
                    reader = PyPDF2.PdfReader(stream)
                except Exception as e:
                    err = str(e)
                    if (
                        "PyCryptodome" in err
                        or "AES" in err
                        or "encrypt" in err.lower()
                    ):
                        # PDF encriptado con AES — no se puede contar páginas sin pycryptodome.
                        # Retornamos 0 para no bloquear la subida del archivo.
                        return 0
                    raise

                # Validación extra: PDF encriptado sin contraseña vacía
                if reader.is_encrypted:
                    try:
                        reader.decrypt("")
                    except Exception as e:
                        err = str(e)
                        if "PyCryptodome" in err or "AES" in err:
                            return _count_pdf_pages_raw(file_content)
                        raise UserError(
                            "El PDF está protegido con contraseña. Quítesela antes de subirlo."
                        )

                try:
                    return len(reader.pages)
                except Exception as e:
                    err = str(e)
                    if (
                        "PyCryptodome" in err
                        or "AES" in err
                        or "encrypt" in err.lower()
                    ):
                        # Fallback: contar páginas escaneando la estructura binaria del PDF
                        return _count_pdf_pages_raw(file_content)
                    raise
        except UserError:
            raise
        except Exception as e:
            raise UserError(
                f"Error al leer el archivo PDF. Asegúrese de que es un PDF válido.\nDetalle: {str(e)}"
            )

    def action_save_and_close_modal(self):
        """
        Este método se llama al dar clic en 'Guardar'.
        Como el botón es type="object", Odoo YA EJECUTÓ 'create' o 'write' automáticamente
        antes de entrar aquí. Por tanto, el conteo y la subida YA OCURRIERON gracias
        a los overrides de arriba.
        Aquí solo nos encargamos de refrescar la vista.
        """
        for record in self:
            # Actualizar folios en el informe padre
            if record.informes_id:
                record.informes_id.compute_folios_preview()

            if record.comunicaciones_id:
                record.comunicaciones_id.compute_folios_preview()

        return {"type": "ir.actions.act_window_close"}

    def action_open_drive_if(self):
        """Abre el formulario del registro actual y establece invisible_todo en True
        ADEMÁS: Ejecuta el conteo de páginas del PDF antes de abrir.
        """
        return {
            "name": self.name or "Abrir Drive",
            "type": "ir.actions.act_window",
            "res_model": "drive",  # Asegúrate que este sea el nombre técnico correcto de tu modelo
            "view_mode": "form",
            "res_id": self.id,  # ID del registro actual
            "target": "new",
            "context": {"default_invisible_todo": True},
        }

    def toggle_sidebar_info(self):
        """
        Activa el detalle del archivo seleccionado.
        La marca visual debe ser única dentro de Informes o Comunicaciones.
        """
        self.ensure_one()
        if self.informes_id:
            self.env["drive"].search(
                [
                    ("informes_id", "=", self.informes_id.id),
                    ("id", "!=", self.id),
                    ("ejecutar_inf", "=", True),
                ]
            ).write({"ejecutar_inf": False})
        if self.comunicaciones_id:
            self.env["drive"].search(
                [
                    ("comunicaciones_id", "=", self.comunicaciones_id.id),
                    ("id", "!=", self.id),
                    ("ejecutar_inf", "=", True),
                ]
            ).write({"ejecutar_inf": False})

        self.ejecutar_inf = True
        if self.informes_id:
            self.informes_id.update_detalles(
                self.id
            )  # Pasamos el ID del registro para obtener su info
        if self.comunicaciones_id:
            self.comunicaciones_id.update_detalles(self.id)

    ###################################Nuevo ###############################################
    otrosi_bolean = fields.Boolean(string="Otro Sí", default=False)

    # Relaciones
    def action_save_and_close(self):
        # =========================================================
        # 0. VARIABLES COMUNES E INICIALIZACIÓN
        # =========================================================
        tipo = self._context.get("tipo_documento_upload")
        notification_message = False

        if tipo in (
            "Documento_De_Causacion_legalizacion",
            "Documento_De_Causacion_pj_legalizacion",
        ):
            if not self.viaticos_id:
                raise AccessError(
                    "La causación debe estar asociada a una legalización."
                )
            self.viaticos_id._validar_carga_causacion()
        for legalizacion in (
            self.viaticos_otros_docs_causacion_pn_id,
            self.viaticos_otros_docs_causacion_pj_id,
        ):
            if legalizacion:
                legalizacion._validar_carga_causacion()

        if tipo in (
            "RPC_Documento_legalizacion",
            "RPC_PJ_Documento_legalizacion",
        ):
            if not self.env.user.has_group("Matriz.group_presupuestos"):
                raise AccessError(
                    "Solo Presupuestos puede cargar el documento RPC de una legalización."
                )

        if tipo == "acta_rpc_regalias_documento":
            if not self.env.user.has_group('Matriz.group_contratacion_asis_coordinador'):
                raise AccessError(_(
                    'Solo el Asistente de Coordinación puede subir el RPC de Regalías.'
                ))
            if (
                not self.contratistas_provedores
                or self.contratistas_provedores.origen_cuenta != 'regalias'
            ):
                raise AccessError(_(
                    'El RPC manual solo se puede asociar a una cuenta de Regalías.'
                ))

        # 1. Obtenemos el nombre del archivo (asegurándonos de que exista)
        # Usamos self.file_name como pediste. Si por alguna razón está vacío, ponemos 'Sin nombre'.
        nombre_archivo = self.file_name or "Sin nombre"

        # 3. Construimos el mensaje incluyendo el nombre del archivo
        mensaje_drive = f"✅ Documento: <b>{nombre_archivo}</b>) se ha subido correctamente a Google Drive."

        # =========================================================
        # SECCIÓN 1: CONTRATACIÓN FUV
        # =========================================================
        field_map_contratacion = {
            "propuesta_pj": ("propuesta_pj_doc_visible", "propuesta_pj_doc_id"),
            "anexo_tributario_juridico": ("anexo_tributario_juridico_doc_visible", "anexo_tributario_juridico_doc_id"),
            "cdp_juridico": ("cdp_juridico_doc_visible", "cdp_juridico_doc_id"),
            "aprueba_j_h_contrato_doc": ("aprueba_j_h_contrato_doc_visible", "aprueba_j_h_contrato_doc_id"),
            "aprueba_j_h_firmado": ("aprueba_j_h_contrato_firmado_doc_visible", "aprueba_j_h_contrato_firmado_doc_id"),
            "camara_comercio": ("camara_comercio_doc_visible", "camara_comercio_doc_id"),
            "rut_pj": ("rut_pj_doc_visible", "rut_pj_doc_id"),
            "rup": ("rup_doc_visible", "rup_doc_id"),
            "cert_contraloria": ("cert_contraloria_doc_visible", "cert_contraloria_doc_id"),
            "cert_procuraduria": ("cert_procuraduria_doc_visible", "cert_procuraduria_doc_id"),
            "cedula_rep_legal": ("cedula_rep_legal_doc_visible", "cedula_rep_legal_doc_id"),
            "cert_contraloria_rep_legal": ("cert_contraloria_rep_legal_doc_visible", "cert_contraloria_rep_legal_doc_id"),
            "cert_procuraduria_rep_legal": ("aprueba_j_h_contraloria_rep_lega_doc_visible", "cert_procuraduria_rep_legal_doc_id"),
            "estados_financieros_juridico": ("estados_financieros_juridico_doc_visible", "estados_financieros_juridico_doc_id"),
            "formato_fuv_ju": ("formato_fuv_ju_doc_visible", "formato_fuv_ju_doc_id"),
            "cert_policia_judicial": ("cert_policia_judicial_doc_visible", "cert_policia_judicial_doc_id"),
            "cert_inhabilidades": ("cert_inhabilidades_doc_visible", "cert_inhabilidades_doc_id"),
            "resolucion_facturacion": ("resolucion_facturacion_doc_visible", "resolucion_facturacion_doc_id"),
            "cert_pago_parafiscales": ("certificado_pago_parafiscales_doc_visible", "certificado_pago_parafiscales_doc_id"),
            "planilla_seguridad_social": ("planilla_seguridad_social_doc_visible", "planilla_seguridad_social_doc_id"),
            "cert_junta_contadores": ("certificado_junta_contadores_doc_visible", "certificado_junta_contadores_doc_id"),
            "cedula_revisor_fiscal": ("cedula_revisor_fiscal_doc_visible", "cedula_revisor_fiscal_doc_id"),
            "certificado_bancario": ("certificado_bancario_doc_visible", "certificado_bancario_doc_id"),
            "aprueba_j_h_bdd": ("aprueba_j_h_bdd_doc_visible", "aprueba_j_h_bdd_doc_id"),
            "tarjeta_profesional_rf": ("Tarjeta_profesional_revisor_fiscal_doc_visible", "Tarjeta_profesional_revisor_fiscal_doc_id"),
            "polizas": ("polizas_visible", "poliza_id"),
            "documento_identidad_contador": ("Documento_de_identidad_contador_doc_visible", "Documento_de_identidad_contador_doc_id"),
            "tarjeta_profesional_contador": ("Tarjeta_profesional_contador_doc_visible", "Tarjeta_profesional_contador_doc_id"),
            "hoja_vida_juridica": ("hoja_vida_juridica_doc_visible", "hoja_vida_juridica_doc_id"),
            "hoja_vida_rep_legal": ("hoja_vida_rep_legal_doc_visible", "hoja_vida_rep_legal_doc_id"),
            "experiencia_laboral": ("experiencia_laboral_doc_visible", "experiencia_laboral_doc_id"),
            # P/N
            "documento_identidad_pn": ("documento_identidad_pn_doc_visible", "documento_identidad_id"),
            "rut_pn": ("rut_pn_doc_visible", "rut_id"),
            "hoja_vida_pn": ("hoja_vida_pn_doc_visible", "hoja_vida_id"),
            "certificados_estudio": ("certificados_estudio_doc_visible", "certificados_estudio_id"),
            "certificados_laborales": ("certificados_laborales_doc_visible", "certificados_laborales_id"),
            "certificado_arl": ("certificado_arl_doc_visible", "certificado_arl_id"),
            "otra_certificado_arl": ("otra_certificado_arl_doc_visible", "otra_certificado_arl_id"),
            "certificacion_salud": ("certificacion_salud_doc_visible", "certificacion_salud_id"),
            "certificacion_pension": ("certificacion_pension_doc_visible", "certificacion_pension_id"),
            "certificacion_examen_preocupacional": ("certificacion_examen_preocupacional_doc_visible", "certificacion_examen_preocupacional_id"),
            "su_cdp": ("su_cdp_doc_visible", "su_cdp_id"),
            "resolucion_facturacion_ju": ("resolucion_facturacion_ju_doc_visible", "resolucion_facturacion_ju_id"),
            "bdd_ju": ("bdd_ju_doc_visible", "bdd_ju_id"),
            "formato_fuv": ("formato_fuv_doc_visible", "formato_fuv_doc_id"),
            "certificacion_contraloria": ("certificacion_contraloria_doc_visible", "certificacion_contraloria_id"),
            "certificacion_procuraduria": ("certificacion_procuraduria_doc_visible", "certificacion_procuraduria_id"),
            "certificacion_policia_judicial": ("certificacion_policia_judicial_doc_visible", "certificacion_policia_judicial_id"),
            "certificacion_medidas_cautelares": ("certificacion_medidas_cautelares_doc_visible", "certificacion_medidas_cautelares_id"),
            "certificacion_inhabilidades": ("certificacion_inhabilidades_doc_visible", "certificacion_inhabilidades_id"),
            "certificacion_libreta_militar": ("certificacion_libreta_militar_doc_visible", "certificacion_libreta_militar_id"),
            "resolucion_pensionado": ("resolucion_pensionado_doc_visible", "resolucion_pensionado_id"),
            "certificacion_bancaria": ("certificacion_bancaria_doc_visible", "certificacion_bancaria_id"),
            "anexo_tributario": ("anexo_tributario_doc_visible", "anexo_tributario_id"),
            "poliza_pn": ("polizas_pn_visible", "poliza_pn_id"),
            "declaracion_juramentada": ("declaracion_juramentada_doc_visible", "declaracion_juramentada_id"),
            "tarjeta_profesional": ("tarjeta_profesional_doc_visible", "tarjeta_profesional_doc_id"),
            "contratos": ("contratos_doc_visible", "contratos_id"),
            "contratos_firmado": ("contratos_firmado_doc_visible", "contratos_firmado_id"),
        }

        # LOGICA 1: Contratación FUV
        if self.contratacion_fuv_documentos_id and tipo in field_map_contratacion:
            _logger.info(
                "Entrando a LOGICA 1 (Contratación FUV). Registro ID: %s",
                self.contratacion_fuv_documentos_id.id,
            )
            visible_field, id_field = field_map_contratacion[tipo]

            update_vals = {
                visible_field: False,
                id_field: self.id,
            }
            self.contratacion_fuv_documentos_id.write(update_vals)

            if (
                tipo in ('anexo_tributario', 'anexo_tributario_juridico')
                and self.env.context.get('gestion_at_posterior_presupuesto')
            ):
                _logger.info(
                    '[AT-PRESUPUESTO-FLUJO-NUEVO] Archivo AT registrado en '
                    'el FC. fc_id=%s estado=%s drive_id=%s tipo=%s '
                    'usuario_id=%s usuario=%s',
                    self.contratacion_fuv_documentos_id.id,
                    self.contratacion_fuv_documentos_id.estado_contratacion,
                    self.id,
                    tipo,
                    self.env.user.id,
                    self.env.user.name,
                )

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.contratacion_fuv_documentos_id.message_post(body=mensaje_drive)
            # ------------------------------------------

            _logger.info("Campos actualizados en contratacion_fuv: %s", update_vals)

            # --- DISPARO DE NOTIFICACIÓN ---
            if tipo == "contratos":
                _logger.info("Detectado tipo 'contratos'. Intentando ejecutar notificacion_contratos_doc...")
                self.contratacion_fuv_documentos_id.notificacion_contratos_doc()
                _logger.info("Ejecución de notificacion_contratos_doc completada con éxito.")

            if tipo == "contratos_firmado":
                _logger.info("Detectado tipo 'contratos_firmado'. Intentando ejecutar notificacion_contrato_firmado...")
                self.contratacion_fuv_documentos_id.notificacion_contrato_firmado()
                _logger.info("Ejecución de notificacion_contrato_firmado completada con éxito.")

            if tipo == "aprueba_j_h_contrato_doc":
                _logger.info("Detectado tipo 'aprueba_j_h_contrato_doc'. Intentando ejecutar notificacion_contratos_ju_doc...")
                self.contratacion_fuv_documentos_id.notificacion_contratos_ju_doc()
                _logger.info("Ejecución de notificacion_contratos_ju_doc completada con éxito.")

            if tipo == "aprueba_j_h_firmado":
                _logger.info("Detectado tipo 'aprueba_j_h_firmado'. Intentando ejecutar notificacion_contrato_juridico_firmado...")
                self.contratacion_fuv_documentos_id.notificacion_contrato_juridico_firmado()
                _logger.info("Ejecución de notificacion_contrato_juridico_firmado completada con éxito.")

            _logger.info("Campos actualizados en contratacion_fuv: %s", update_vals)


        # =========================================================
        # SECCIÓN 2: OTRO SÍ
        # =========================================================
        field_map_otrosi = {
            "documento_arl": ("su_arl_doc_visible", "su_arl_doc_id"),
            "propuesta_otrosi": ("propuesta_doc_visible", "propuesta_doc_id"),
            "su_cdp": ("su_cdp_doc_visible", "su_cdp_id"),
            "contratos_otro": ("contratos_otro_doc_visible", "contratos_otro_id"),
            "contratos_firmado_natu": ("contratos_firmado_natu_doc_visible", "contratos_firmado_id"),
            "cdp_juridico_otrosi": ("cdp_juridico_doc_visible", "cdp_juridico_doc_id"),
            "contratos_otro_juridico": ("contratos_otro_juridico_doc_visible", "contratos_otro_juridico_id"),
            "contratos_firmado_juridico": ("contratos_firmado_juridico_doc_visible", "contratos_firmado_juridico_id"),
            "polizas_otrosi": ("polizas_visible", "poliza_id"),
        }

        # LOGICA 2: Ejecutar si viene de Otro Sí
        if self.otrosi_documentos_id and tipo in field_map_otrosi:
            _logger.info(
                "Entrando a LOGICA 2 (Otro Sí). Registro ID: %s",
                self.otrosi_documentos_id.id,
            )
            visible_field, id_field = field_map_otrosi[tipo]
            update_vals = {visible_field: False, id_field: self.id}
            if tipo == "propuesta_otrosi":
                update_vals["propuesta"] = True
            self.otrosi_documentos_id.write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.otrosi_documentos_id.message_post(body=mensaje_drive)
            # ------------------------------------------

            _logger.info("Campos actualizados en otrosi: %s", update_vals)

            if tipo == "contratos_otro":
                _logger.info("Detectado tipo 'contratos_otro'. Intentando ejecutar notificacion_contratos_doc...")
                notification_message = self.otrosi_documentos_id.notificacion_contratos_doc()
                _logger.info("Ejecución de notificacion_contratos_doc completada con éxito.")

            if tipo == "contratos_firmado_natu":
                _logger.info("Detectado tipo 'contratos_firmado_natu'. Intentando ejecutar notificacion_contrato_firmado...")
                self.otrosi_documentos_id.notificacion_contrato_firmado()
                _logger.info("Ejecución de notificacion_contrato_firmado completada con éxito.")

            if tipo == "contratos_otro_juridico":
                _logger.info("Detectado tipo 'contratos_otro_juridico'. Intentando ejecutar notificacion_contratos_juridico_doc...")
                notification_message = self.otrosi_documentos_id.notificacion_contratos_juridico_doc()
                _logger.info(
                    "Ejecución de notificacion_contratos_juridico_doc completada con éxito."
                )

            if tipo == "contratos_firmado_juridico":
                _logger.info("Detectado tipo 'contratos_firmado_juridico'. Intentando ejecutar notificacion_contrato_firmado_juridico...")
                self.otrosi_documentos_id.notificacion_contrato_firmado_juridico()
                _logger.info("Ejecución de notificacion_contrato_firmado_juridico completada con éxito.")


        # =========================================================
        # SECCIÓN 3: BIENES
        # =========================================================
        fied_map_bienes = {
            "rut_natural": ("rut_pn_doc_visible", "rut_pn_doc_id"),
            "planilla_natural": ("planilla_doc_visible", "planilla_doc_id"),
            "factura_o_cuenta": ("factura_cuenta_doc_visible", "factura_cuenta_doc_id"),
            "documento_identidad_pn": ("documento_identidad_doc_visible", "documento_identidad_doc_id"),
            "certificado_bancario_pn": ("certificado_bancario_doc_visible", "certificado_bancario_doc_id"),
            "Anexo_tributario_pn": ("Anexo_tributario_doc_pn_visible", "Anexo_tributario_doc_pn_id"),
            "documento_rpc_natural": ("documento_rpc_doc_visible", "documento_rpc_doc_id"),
            "rut_pj_documento": ("rut_pj_doc_visible", "rut_pj_doc_id"),
            "camara_comercio_pj_documento": ("camara_comercio_doc_visible", "camara_comercio_doc_id"),
            "planilla_ss_pj_documento": ("planilla_ss_doc_visible", "planilla_ss_doc_id"),
            "certificado_parafiscales_pj_documento": ("cert_parafiscales_doc_visible", "cert_parafiscales_doc_id"),
            "factura_cuenta_pj_documento": ("factura_cuenta_pj_doc_visible", "factura_cuenta_pj_doc_id"),
            "certificado_junta_contadores_pj_documento": ("certificado_junta_contadores_doc_visible", "certificado_junta_contadores_doc_id"),
            "tp_contador_pj_documento": ("tp_contador_doc_visible", "tp_contador_doc_id"),
            "certificado_bancario_pj_documento": ("certificado_bancario_pj_doc_visible", "certificado_bancario_pj_doc_id"),
            "Anexo_tributario_pj_documento": ("Anexo_tributario_doc_visible", "Anexo_tributario_doc_id"),
            "resolucion_facturacion_pj_documento": ("resolucion_facturacion_doc_visible", "resolucion_facturacion_doc_id"),
            "documento_rpc_pj_documento": ("documento_rpc_pj_doc_visible", "documento_rpc_pj_doc_id"),
        }

        # LOGICA 3: Ejecutar si viene de Bienes
        if self.bienes_id and tipo in fied_map_bienes:
            visible_field, id_field = fied_map_bienes[tipo]

            update_vals = {visible_field: False, id_field: self.id}
            self.bienes_id.write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.bienes_id.message_post(body=mensaje_drive)
            # ------------------------------------------


        # =========================================================
        # SECCIÓN 4: CARPETA PRINCIPAL
        # =========================================================
        fied_map_carpeta_principal = {
            "carta_invitacion": ("carta_invitacion_visible", "carta_invitacion_id"),
            "cdp_documento": ("cdp_visible", "cdp_id"),
            "estudios_previos": ("estudios_previos_visible", "estudios_previos_id"),
            "propuesta_documento": ("propuesta_visible", "propuesta_id"),
            "oficio_remisorio_propuesta_documento": ("oficio_remisorio_propuesta_visible", "oficio_remisorio_propuesta_id"),
            "resolucion_contratacion_documento": ("resolucion_contratacion_visible", "resolucion_contratacion_id"),
            "contrato_convenio_componente_documento": ("contrato_convenio_componente_visible", "contrato_convenio_componente_id"),
            "polizas_documento": ("polizas_visible", "polizas_id"),
            "aceptacion_polizas_documento": ("aceptacion_polizas_visible", "aceptacion_polizas_id"),
            "acta_inicio_documento": ("acta_inicio_visible", "acta_inicio_id"),
            "rpc_documento": ("rpc_visible", "rpc_id"),
            "otrosi_modificatorios_documento": ("otrosi_modificatorios_visible", "otrosi_modificatorios_id"),
            "matriz_de_riesgos": ("matriz_de_riesgos_visible", "matriz_de_riesgos_id"),
            "polizas_modificatorios_documento": ("polizas_modificatorios_visible", "polizas_modificatorios_id"),
            "designacion_supervisor_documento": ("designacion_supervisor_visible", "designacion_supervisor_id"),
            "facturacion_documento": ("facturacion_visible", "facturacion_id"),
            "actas_seguimiento_documento": ("actas_seguimiento_visible", "actas_seguimiento_id"),
            "informes_avance_documento": ("informes_avance_visible", "informes_avance_id"),
            "actas_supervisor_parciales_documento": ("actas_supervisor_parciales_visible", "actas_supervisor_parciales_id"),
            "informe_final_documento": ("informe_final_visible", "informe_final_id"),
            "informe_de_invertentoria_documento": ("informe_de_invertentoria_visible", "informe_de_invertentoria_id"),
            "acta_final_supervisor_documento": ("acta_final_supervisor_visible", "acta_final_supervisor_id"),
            "acta_terminacion_documento": ("acta_terminacion_visible", "acta_terminacion_id"),
            "acta_final_pago_documento": ("acta_final_pago_visible", "acta_final_pago_id"),
            "acta_liquidacion_documento": ("acta_liquidacion_visible", "acta_liquidacion_id"),
            "certificado_cumplimiento_documento": ("certificado_cumplimiento_visible", "certificado_cumplimiento_id"),
            "m_g_a_documento": ("m_g_a_visible", "m_g_a_id"),
        }

        # LOGICA 4: Ejecutar si viene de Carpeta Principal
        if self.carpeta_principal_id and tipo in fied_map_carpeta_principal:
            visible_field, id_field = fied_map_carpeta_principal[tipo]

            update_vals = {visible_field: False, id_field: self.id}
            self.carpeta_principal_id.with_context(_cp_check_required=True).write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.carpeta_principal_id.message_post(body=mensaje_drive)
            # ------------------------------------------


        # =========================================================
        # SECCIÓN 5: SERVICIOS
        # =========================================================
        fied_map_servicios = {
            "rut_natural_doc": ("rut_pn_doc_visible", "rut_pn_doc_id"),
            "planilla_natural_doc": ("planilla_doc_visible", "planilla_doc_id"),
            "factura_o_cuenta_doc": ("factura_cuenta_doc_visible", "factura_cuenta_doc_id"),
            "documento_identidad_doc": ("documento_identidad_doc_visible", "documento_identidad_doc_id"),
            "certificado_bancario_doc": ("certificado_bancario_doc_visible", "certificado_bancario_doc_id"),
            "Anexo_tributario_doc": ("Anexo_tributario_doc_pn_visible", "Anexo_tributario_doc_pn_id"),
            "documento_rpc_natural_doc": ("documento_rpc_doc_visible", "documento_rpc_doc_id"),
            "rut_pj_documento_doc": ("rut_pj_doc_visible", "rut_pj_doc_id"),
            "camara_comercio_pj_documento_doc": ("camara_comercio_doc_visible", "camara_comercio_doc_id"),
            "planilla_ss_pj_documento_doc": ("planilla_ss_doc_visible", "planilla_ss_doc_id"),
            "certificado_parafiscales_pj_documento_doc": ("cert_parafiscales_doc_visible", "cert_parafiscales_doc_id"),
            "factura_cuenta_pj_documento_doc": ("factura_cuenta_pj_doc_visible", "factura_cuenta_pj_doc_id"),
            "certificado_junta_contadores_pj_documento_doc": ("certificado_junta_contadores_doc_visible", "certificado_junta_contadores_doc_id"),
            "tp_contador_pj_documento_doc": ("tp_contador_doc_visible", "tp_contador_doc_id"),
            "certificado_bancario_pj_documento_doc": ("certificado_bancario_pj_doc_visible", "certificado_bancario_pj_doc_id"),
            "Anexo_tributario_pj_documento_doc": ("Anexo_tributario_doc_visible", "Anexo_tributario_doc_id"),
            "resolucion_facturacion_pj_documento_doc": ("resolucion_facturacion_doc_visible", "resolucion_facturacion_doc_id"),
            "documento_rpc_pj_documento_doc": ("documento_rpc_pj_doc_visible", "documento_rpc_pj_doc_id"),
        }

        # LOGICA 5: Ejecutar si viene de Servicios
        if self.servicios_id and tipo in fied_map_servicios:
            visible_field, id_field = fied_map_servicios[tipo]

            update_vals = {visible_field: False, id_field: self.id}
            self.servicios_id.write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.servicios_id.message_post(body=mensaje_drive)
            # ------------------------------------------


        # =========================================================
        # SECCIÓN 6: CIERRE
        # =========================================================
        fied_map_cierre = {
            "archivo_acta_liquidacion_documento": ("archivo_acta_liquidacion_visible", "archivo_acta_liquidacion_id"),
            "paz_y_salvo_documento": ("paz_y_salvo_visible", "paz_y_salvo_id"),
            "acta_liquidacion_firmada_documento": ("acta_liquidacion_firmada_visible", "acta_liquidacion_firmada_id"),
        }

        # LOGICA 6: Ejecutar si viene de Cierre
        if self.carpeta_principal_cierre_id and tipo in fied_map_cierre:
            visible_field, id_field = fied_map_cierre[tipo]

            update_vals = {visible_field: False, id_field: self.id}
            self.carpeta_principal_cierre_id.write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.carpeta_principal_cierre_id.message_post(body=mensaje_drive)
            # ------------------------------------------

         # =========================================================
        # SECCIÓN 6: ANTICIPOS
        # =========================================================

        fied_map_anticipos = {
            "Rut_PN_Documento": ("rut_pn_document_visible", "rut_pn_document_id"),
            "Doc_Identidad_Documento": ("doc_identidad_document_visible", "doc_identidad_document_id"),
            "Cert_Bancaria_PN_Documento": ("cert_bancaria_pn_document_visible", "cert_bancaria_pn_document_id"),
            "RPC_Documento": ("rpc_document_visible", "rpc_document_id"),
            "AT_Documento": ("at_document_visible", "at_document_id"),
            "Comprobante_Pago_Documento": ("comprobante_pago_document_visible", "comprobante_pago_document_id"),
            "Soporte_De_Pago_Documento": ("soporte_de_pago_document_visible", "soporte_de_pago_document_id"),
            "Rut_PJ_Documento": ("rut_pj_document_visible", "rut_pj_document_id",),
            "Camara_Comercio_Documento": ("camara_comercio_document_visible", "camara_comercio_document_id",),
            "Cert_Bancaria_PJ_Documento": ("cert_bancaria_pj_document_visible", "cert_bancaria_pj_document_id",),
            "rpc_PJ_Documento": ("rpc_pj_document_visible", "rpc_pj_document_id",),
            "AT_PJ_Documento": ("at_pj_document_visible", "at_pj_document_id",),
            "Comprobante_Pago_PJ_Documento": ("comprobante_pago_pj_document_visible", "comprobante_pago_pj_document_id",),
            "Soporte_De_Pago_PJ_Documento": ("soporte_de_pago_pj_document_visible", "soporte_de_pago_pj_document_id",),
            "Compromiso_Legalizacion_PN_Documento": ("compromiso_legalizacion_pn_document_visible", "compromiso_legalizacion_pn_document_id",),
            "Cuenta_Cobro_PJ_Documento": ("cuenta_cobro_pj_document_visible", "cuenta_cobro_pj_document_id",),
        }

        # LOGICA 7: Ejecutar si viene de Anticipos
        if self.anticipos_id and tipo in fied_map_anticipos:
            visible_field, id_field = fied_map_anticipos[tipo]

            update_vals = {visible_field: False, id_field: self.id}
            self.anticipos_id.write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.anticipos_id.message_post(body=mensaje_drive)
            # ------------------------------------------

        fied_map_viaticos = {
            "Rut_PN_Documento_legalizacion": ("rut_pn_document_visible", "rut_pn_document_id"),
            "Doc_Identidad_Documento_legalizacion": ("doc_identidad_document_visible", "doc_identidad_document_id"),
            "Cert_Bancaria_PN_Documento_legalizacion": ("cert_bancaria_pn_document_visible", "cert_bancaria_pn_document_id"),
            "orden_Pago_Documento_legalizacion": ("orden_pago_document_visible", "orden_pago_document_id"),
            "Comprobante_Pago_Documento_legalizacion": ("comprobante_pago_document_visible", "comprobante_pago_document_id"),
            "Soporte_De_Pago_Documento_legalizacion": ("soporte_de_pago_document_visible", "soporte_de_pago_document_id"),
            "Documento_De_Causacion_legalizacion": ("documento_de_causacion_visible", "documento_de_causacion_id",),
            "Rut_PJ_Documento_legalizacion": ("rut_pj_document_visible", "rut_pj_document_id",),
            "Camara_Comercio_Documento_legalizacion": ("camara_comercio_document_visible", "camara_comercio_document_id",),
            "Cert_Bancaria_PJ_Documento_legalizacion": ("cert_bancaria_pj_document_visible", "cert_bancaria_pj_document_id",),
            "orden_Pago_pj_Documento_legalizacion": ("orden_pago_pj_document_visible", "orden_pago_pj_document_id"),
            "Comprobante_Pago_PJ_Documento_legalizacion": ("comprobante_pago_pj_document_visible", "comprobante_pago_pj_document_id",),
            "Soporte_De_Pago_PJ_Documento_legalizacion": ("soporte_de_pago_pj_document_visible", "soporte_de_pago_pj_document_id",),
            "Documento_De_Causacion_pj_legalizacion": ("documento_de_causacion_pj_visible", "documento_de_causacion_pj_id",),
            "RPC_Documento_legalizacion": ("rpc_legalizacion_document_visible", "rpc_legalizacion_document_id",),
            "RPC_PJ_Documento_legalizacion": ("rpc_pj_legalizacion_document_visible", "rpc_pj_legalizacion_document_id",),
        }

        # LOGICA 7: Ejecutar si viene de Anticipos
        if self.viaticos_id and tipo in fied_map_viaticos:
            visible_field, id_field = fied_map_viaticos[tipo]

            update_vals = {visible_field: False, id_field: self.id}
            self.viaticos_id.write(update_vals)

            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            self.viaticos_id.message_post(body=mensaje_drive)
            # ------------------------------------------
        # LOGICA 8: Ejecutar si viene de viaticos_linea
        viaticos_linea ={
            "Factura_legalizacion_linea": ("factura_visible", "factura_id"),
        }
        if self.viaticos_linea_id and tipo in viaticos_linea:
            visible_field, id_field = viaticos_linea[tipo]
            # --- MESSAGE POST CON NOMBRE DE ARCHIVO ---
            update_vals = {visible_field: False, id_field: self.id}
            self.viaticos_linea_id.write(update_vals)

            self.viaticos_linea_id.message_post(body=mensaje_drive)
            # ------------------------------------------

        # =========================================================
        # SECCIÓN 5.1: ACTA ENTREGA DOCUMENTOS
        # =========================================================
        field_map_acta_entrega = {
            "acta_rpc_regalias_documento": (
                "rpc_regalias_visible",
                "rpc_regalias_id",
                "rpc_regalias",
            ),
            "acta_causacion_documento": ("causacion_visible", "causacion_id", "causacion"),
            "acta_retencion_documento": ("retencion_visible", "retencion_id", "retencion"),
            "acta_articulos_documento": ("articulos_visible", "articulos_id", "Articulos"),
            "acta_documento_liquidacion_documento": ("documento_liquidacion_visible", "documento_liquidacion_id", "documento_liquidacion"),
            "acta_documento_cruce_documento": ("documento_cruce_visible", "documento_cruce_id", "documento_cruce"),
            "acta_reporte_liquidacion_documento": ("reporte_liquidacion_visible", "reporte_liquidacion_id", "reporte_liquidacion"),
            "acta_documentos_adicionales_contabilidad_documento": (
                "documentos_adicionales_contabilidad_visible",
                "documentos_adicionales_contabilidad_id",
                "documentos_adicionales_contabilidad",
            ),
            "acta_soporte_rechazo_documento": (
                "soporte_rechazo_visible",
                "soporte_rechazo_id",
                "soporte_rechazo",
            ),
            "acta_orden_de_pago_documento": ("orden_de_pago_visible", "orden_de_pago_id", "orden_de_oago"),
            "acta_comprobante_de_pago_documento": ("comprobante_de_pago_visible", "comprobante_de_pago_id", "comprobante_de_pago"),
            "acta_comprobante_bancario_documento": ("comprobante_bancario_visible", "comprobante_bancario_id", "comprobante_bancario"),
        }

        if self.contratistas_provedores and tipo in field_map_acta_entrega:
            visible_field, id_field, boolean_field = field_map_acta_entrega[tipo]
            update_vals = {
                visible_field: False,
                id_field: self.id,
                boolean_field: True,
            }
            self.contratistas_provedores.write(update_vals)
            self.contratistas_provedores.message_post(body=mensaje_drive)

        # Cerramos la ventana emergente
        _logger.info("Finalizando action_save_and_close exitosamente.")

        if notification_message:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Documento enviado a coordinador",
                    "message": notification_message,
                    "type": "success",
                    "sticky": False,
                    "next": {"type": "ir.actions.act_window_close"},
                },
            }

        return {"type": "ir.actions.act_window_close"}

    # =======================================================================
    # NUEVO MÉTODO: RENOMBRADO AUTOMÁTICO AL SUBIR EL ARCHIVO
    # =======================================================================
    @api.onchange("file_data")
    def _onchange_file_data(self):
        """
        Renombra el archivo usando el contexto.
        Si el tipo no está en la lista, usa el nombre original.
        """
        if self.file_data and self.file_name:
            # 1. Recuperamos el valor desde el contexto
            tipo = self._context.get("tipo_documento_upload")

            # ── Validación documentos obligatorios en Carpeta Principal ──
            cp = self.carpeta_principal_id
            if cp and tipo:
                from .mcb import _CP_REQUIRED_VISIBLE, _CP_REQUIRED_LABELS

                is_required = tipo in {
                    "carta_invitacion",
                    "propuesta_documento",
                    "polizas_documento",
                    "aceptacion_polizas_documento",
                    "acta_inicio_documento",
                }
                if not is_required:
                    missing = [
                        label
                        for field, label in _CP_REQUIRED_LABELS.items()
                        if getattr(cp, field, True)
                    ]
                    if missing:
                        self.file_data = False
                        self.file_name = False
                        return {
                            "warning": {
                                "title": "Documentos obligatorios pendientes",
                                "message": (
                                    "Debe subir primero los documentos obligatorios (*):\n\n- "
                                    + "\n- ".join(missing)
                                ),
                            }
                        }
            # ── fin validación ──

            # 2. Desglosamos el nombre actual
            name_part, extension = os.path.splitext(self.file_name)

            # 3. Lógica del prefijo: POR DEFECTO usamos el nombre original
            # Si no entra en ningún IF de abajo, se queda con name_part
            prefix = name_part

            # --- Contrataciones FV ---
            # P/N
            if tipo == "documento_identidad_pn":
                prefix = "1_Documento de identidad_"
            elif tipo == "rut_pn":
                prefix = "2_RUT_"
            elif tipo == "hoja_vida_pn":
                prefix = "3_Hoja de vida_"
            elif tipo == "certificados_estudio":
                prefix = "4_Certificados de estudio_"
            elif tipo == "certificados_laborales":
                prefix = "5_Certificados laborales_"
            elif tipo == "certificado_arl":
                prefix = "6_Certificado ARL_"
            elif tipo == "otra_certificado_arl":
                prefix = "6B_Novedad ARL_"
            elif tipo == "certificacion_salud":
                prefix = "7_Cert_afiliación salud_"
            elif tipo == "certificacion_pension":
                prefix = "8_Cert_afiliación a pensión_"
            elif tipo == "certificacion_examen_preocupacional":
                prefix = "9_Cert_examen preocupacional_"
            elif tipo == "resolucion_facturacion_ju":
                prefix = "11_Resolución de facturación electrónica_"
            elif tipo == "certificacion_contraloria":
                prefix = "13_Cert_contraloría_"
            elif tipo == "certificacion_procuraduria":
                prefix = "14_Cert_procuraduría_"
            elif tipo == "certificacion_policia_judicial":
                prefix = "15_Cert_policía judicial_"
            elif tipo == "certificacion_medidas_cautelares":
                prefix = "16_Cert_medidas correctivas_"
            elif tipo == "certificacion_inhabilidades":
                prefix = "17_Cert_inhabilidades_"
            elif tipo == "certificacion_libreta_militar":
                prefix = "18_Cert_libreta militar_"
            elif tipo == "resolucion_pensionado":
                prefix = "19_Resolución de pensionado vigente_"
            elif tipo == "certificacion_bancaria":
                prefix = "20_Certificado bancario_"
            elif tipo == "tarjeta_profesional":
                prefix = "21_Tarjeta profesional_"
            elif tipo == "declaracion_juramentada":
                prefix = "22_Declaración juramentada_"
            elif tipo == "contratos":
                prefix = "23_Contrato_"
            elif tipo == "contratos_firmado":
                prefix = "24_Contrato firmado_"

            # P/J
            elif tipo == "propuesta_pj":
                prefix = "1_Propuesta_"
            elif tipo == "camara_comercio":
                prefix = "2_Cámara de comercio_"
            elif tipo == "rut_pj":
                prefix = "3_RUT_"
            elif tipo == "rup":
                prefix = "4_RUP_"
            elif tipo == "hoja_vida_juridica":
                prefix = "5_Hoja de vida jurídica_"
            elif tipo == "cert_contraloria":
                prefix = "6_Certificado contraloría_"
            elif tipo == "cert_procuraduria":
                prefix = "7_Certificado procuraduría_"
            elif tipo == "cedula_rep_legal":
                prefix = "8_Cédula representante legal_"
            elif tipo == "hoja_vida_rep_legal":
                prefix = "9_Hoja de vida representante legal_"
            elif tipo == "cert_contraloria_rep_legal":
                prefix = "10_Cert_contraloría representante legal_"
            elif tipo == "cert_procuraduria_rep_legal":
                prefix = "11_Cert_procuraduría representante legal_"
            elif tipo == "estados_financieros_juridico":
                prefix = "13_Estados financieros_"
            elif tipo == "cert_policia_judicial":
                prefix = "14_Cert_policía judicial_medidas cautelares_"
            elif tipo == "cert_inhabilidades":
                prefix = "15_Certificado inhabilidades_"
            elif tipo == "resolucion_facturacion":
                prefix = "16_Resolución de facturación electrónica_"
            elif tipo == "experiencia_laboral":
                prefix = "17_Experiencia laboral_"
            elif tipo == "cert_pago_parafiscales":
                prefix = "18_Certificado pago parafiscales_"
            elif tipo == "planilla_seguridad_social":
                prefix = "19_Planilla de seguridad social_"
            elif tipo == "cert_junta_contadores":
                prefix = "20_Certificado junta de contadores_"
            elif tipo == "cedula_revisor_fiscal":
                prefix = "21_Documento de identidad revisor fiscal_"
            elif tipo == "certificado_bancario":
                prefix = "22_Certificado bancario vigente_"
            elif tipo == "tarjeta_profesional_rf":
                prefix = "Tarjeta profesional revisor fiscal_"
            elif tipo == "documento_identidad_contador":
                prefix = "Documento de identidad contador_"
            elif tipo == "tarjeta_profesional_contador":
                prefix = "Tarjeta profesional contador_"
            elif tipo == "aprueba_j_h_contrato_doc":
                prefix = "23_Contrato_"
            elif tipo == "aprueba_j_h_firmado":
                prefix = "24_Contrato firmado_"

            # ---------------otroSì PJ --------------
            elif tipo == "contratos_otro_juridico":
                prefix = "Contrato_"
            elif tipo == "contratos_firmado_juridico":
                prefix = "Contrato firmado_"
            # ---------------otroSì PN --------------
            elif tipo == "contratos_otro":
                prefix = "Contrato_"
            elif tipo == "contratos_firmado_natu":
                prefix = "Contrato firmado_"

            # --------------- Carpeta Principal (posición fija, 27 ítems) ---------------
            # Columna izquierda: 1.1–1.13
            elif tipo == "carta_invitacion":
                prefix = "1.1_"
            elif tipo == "cdp_documento":
                prefix = "1.2_"
            elif tipo == "estudios_previos":
                prefix = "1.3_"
            elif tipo == "propuesta_documento":
                prefix = "1.4_"
            elif tipo == "oficio_remisorio_propuesta_documento":
                prefix = "1.5_"
            elif tipo == "resolucion_contratacion_documento":
                prefix = "1.6_"
            elif tipo == "contrato_convenio_componente_documento":
                prefix = "1.7_"
            elif tipo == "polizas_documento":
                prefix = "1.8_"
            elif tipo == "aceptacion_polizas_documento":
                prefix = "1.9_"
            elif tipo == "acta_inicio_documento":
                prefix = "1.10_"
            elif tipo == "rpc_documento":
                prefix = "1.11_"
            elif tipo == "otrosi_modificatorios_documento":
                prefix = "1.12_"
            elif tipo == "matriz_de_riesgos":
                prefix = "1.13_"
            # Columna derecha: 1.14–1.27
            elif tipo == "polizas_modificatorios_documento":
                prefix = "1.14_"
            elif tipo == "designacion_supervisor_documento":
                prefix = "1.15_"
            elif tipo == "facturacion_documento":
                prefix = "1.16_"
            elif tipo == "actas_seguimiento_documento":
                prefix = "1.17_"
            elif tipo == "informes_avance_documento":
                prefix = "1.18_"
            elif tipo == "actas_supervisor_parciales_documento":
                prefix = "1.19_"
            elif tipo == "informe_final_documento":
                prefix = "1.20_"
            elif tipo == "informe_de_invertentoria_documento":
                prefix = "1.21_"
            elif tipo == "acta_final_supervisor_documento":
                prefix = "1.22_"
            elif tipo == "acta_terminacion_documento":
                prefix = "1.23_"
            elif tipo == "acta_final_pago_documento":
                prefix = "1.24_"
            elif tipo == "acta_liquidacion_documento":
                prefix = "1.25_"
            elif tipo == "certificado_cumplimiento_documento":
                prefix = "1.26_"
            elif tipo == "m_g_a_documento":
                prefix = "1.27_"

            # 4. Generamos el nombre final
            # Formato: Prefijo + UUID + Extensión
            new_unique_name = f"{prefix}{uuid.uuid4().hex}{extension}"

            # 5. Asignamos
            self.file_name = new_unique_name
            self.name = new_unique_name

    ###################################################################################################################

    contratacion_fuv_documentos_id = fields.Many2one(
        "contrataciones_fuv", string="Contrato Relacionado"
    )
    carpeta_principal_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpeta_principal_otros_facturacion_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpeta_principal_otros_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpeta_principal_nueva_relacion_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpetas_principales_otra_relacion_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    bienes_id = fields.Many2one("orden_compra_bienes", string="Contrato Relacionado")
    servicios_id = fields.Many2one(
        "orden_compra_servicios", string="Contrato Relacionado"
    )
    otrosi_documentos_id = fields.Many2one("otro_si", string="Contrato Relacionado")
    carpeta_principal_cierre_id = fields.Many2one(
        "cierre", string="Contrato Relacionado"
    )
    anticipos_id = fields.Many2one("anticipos", string="Contrato Relacionado")
    viaticos_id = fields.Many2one("viaticos", string="Contrato Relacionado")
    viaticos_otros_id = fields.Many2one("viaticos", string="Contrato Relacionado")
    viaticos_linea_id = fields.Many2one("viaticos_linea", string="Contrato Relacionado")
    viaticos_otros_docs_causacion_pn_id = fields.Many2one(
        "viaticos", string="Otros docs causación PN"
    )
    viaticos_otros_docs_causacion_pj_id = fields.Many2one(
        "viaticos", string="Otros docs causación PJ"
    )

    contratacion_fuv_documentos_id = fields.Many2one(
        "contrataciones_fuv", string="Contrato Relacionado"
    )
    carpeta_principal_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpeta_principal_otros_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpeta_principal_nueva_relacion_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    carpetas_principales_otra_relacion_id = fields.Many2one(
        "carpeta_principal", string="Contrato Relacionado"
    )
    bienes_id = fields.Many2one("orden_compra_bienes", string="Contrato Relacionado")
    servicios_id = fields.Many2one(
        "orden_compra_servicios", string="Contrato Relacionado"
    )
    otrosi_documentos_id = fields.Many2one("otro_si", string="Contrato Relacionado")
    carpeta_principal_cierre_id = fields.Many2one(
        "cierre", string="Contrato Relacionado"
    )
    contratistas_provedores = fields.Many2one(
        "acta_entrega_documentos", string="Acta entrega documentos", tracking=True
    )

    original_file_id = fields.Char("ID Archivo Original")

    file_name = fields.Char("Nombre del archivo", readonly=False, tracking=True)
    name = fields.Char(
        string="Nombre", related="file_name", readonly=True, tracking=False
    )

    def buscar_carpeta_en_segundo_plano(self):
        logging.info("Iniciando en segundo plano.")
        thread = threading.Thread(target=self._thread_buscar_carpeta)
        thread.daemon = True
        thread.start()

    def _thread_buscar_carpeta(self):
        _logger.info("Hilo iniciado.")
        new_cr = None
        try:
            new_cr = self.env.registry.cursor()  # Crear un nuevo cursor
            env = api.Environment(new_cr, self.env.uid, self.env.context)
            self_with_new_env = self.with_env(env)

            # Medir el tiempo de búsqueda
            start_time = time.time()
            self_with_new_env._action_update_all_filenames()  # Ejecutar la búsqueda de carpeta
            _logger.info(f"finalizada en {time.time() - start_time:.2f} segundos.")

            new_cr.commit()  # Confirmar los cambios en la base de datos
        except Exception as e:
            _logger.error(f"Error en el hilo de búsqueda de carpetas: {str(e)}")
            if new_cr:
                new_cr.rollback()  # Rollback en caso de error
        finally:
            if new_cr:  # Verifica si el cursor fue creado antes de intentar cerrarlo
                new_cr.close()  # Cerrar el cursor solo si fue creado
                print("Cerro el hilo")

    def _action_update_all_filenames(self):
        """
        Actualiza TODOS los archivos relacionados con stage6_id en Odoo y Google Drive.
        Se genera un UUID único por cada archivo.
        """
        for record in self:
            if record.stage6_id:
                related_files = self.search([("stage6_id", "=", record.stage6_id.id)])

                # Obtener instancia de autenticación en Google Drive
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()

                for file_record in related_files:
                    if file_record.file_name:
                        # Generar un UUID único para cada archivo
                        unique_id = str(uuid.uuid4())[:8]
                        file_parts = file_record.file_name.rsplit(
                            ".", 1
                        )  # Separar nombre y extensión

                        if len(file_parts) == 2:
                            new_name = f"{file_parts[0]}_{unique_id}.{file_parts[1]}"  # Nombre + UUID + Extensión
                        else:
                            new_name = (
                                f"{file_record.file_name}_{unique_id}"  # Sin extensión
                            )

                        # Buscar el archivo en Google Drive y renombrarlo
                        file_list = drive.ListFile(
                            {"q": f"title='{file_record.file_name}'"}
                        ).GetList()
                        for file in file_list:
                            file["title"] = new_name
                            file.Upload()  # Subir cambios a Google Drive

                        # Actualizar el nombre en Odoo después de renombrarlo en Google Drive
                        file_record.file_name = new_name

    tag_ids = fields.Many2many("documents.tag", string="Etiquetas", tracking=True)
    tags_html = fields.Char("Etiquetas HTML", compute="_compute_tags_html", store=True)
    proyecto_relacionado = fields.Many2one(
        "contratacion", string="Proyecto relacionado"
    )

    @api.depends("tag_ids")
    def _compute_tags_html(self):
        for record in self:
            # Generar el HTML para las etiquetas
            tags_html = ""
            for tag in record.tag_ids:
                tags_html += f'<span class="tag">{tag.name}</span> '
            record.tags_html = tags_html

    user_id = fields.Many2one(
        "res.users",
        string="Creador",
        default=lambda self: self.env.user,
        track_visibility="always",
    )
    fch_creacion = fields.Datetime(
        string="Fecha creacion",
        readonly=True,
        copy=False,
        default=lambda self: fields.Datetime.now(),
    )

    buscador = fields.Many2one(
        "otrosi",
        string="Proyecto relacionado",
        compute="_compute_buscador",
    )

    def _compute_buscador(self):
        """Asigna automáticamente el primer registro de 'otrosi' al campo 'buscador'."""
        primer_registro = self.env["otrosi"].search([], limit=1)
        for record in self:
            record.buscador = primer_registro.id if primer_registro else False

    preview_url = fields.Char("Preview URL", readonly=True)
    edit_url = fields.Char("Edit documento", readonly=True)
    preview_html = fields.Text("Preview HTML", readonly=True)
    edit_html = fields.Text("Preview HTML", readonly=True)

    def asegurar_visor_drive(self):
        """Completa el visor para archivos de Drive que ya tienen ID remoto."""
        for record in self:
            google_id = record.file_id or record.drive_file_id
            if not google_id:
                continue
            preview_url = f"https://drive.google.com/file/d/{google_id}/preview"
            edit_url = f"https://drive.google.com/file/d/{google_id}/view"
            vals = {}
            if record.preview_url != preview_url:
                vals["preview_url"] = preview_url
            if record.edit_url != edit_url:
                vals["edit_url"] = edit_url
            preview_html = (
                f'<iframe src="{preview_url}" width="100%" height="600px" '
                'loading="lazy" frameborder="0" allowfullscreen></iframe>'
            )
            edit_html = (
                f'<iframe src="{edit_url}" width="100%" height="600px" '
                'loading="lazy" frameborder="0"></iframe>'
            )
            if record.preview_html != preview_html:
                vals["preview_html"] = preview_html
            if record.edit_html != edit_html:
                vals["edit_html"] = edit_html
            if vals:
                record.with_context(skip_drive_sync=True, skip_integrity=True).write(
                    vals
                )
        return True

    def _asegurar_lector_drive_usuario_actual(self):
        """Garantiza acceso de lectura al usuario interno que abre el visor."""
        usuario = self.env.user
        if usuario.share:
            return True

        correo = (usuario.email or usuario.partner_id.email or '').strip().lower()
        if not correo:
            return True

        try:
            drive = GoogleDriveAuth.get_instance().get_drive()
        except Exception:
            _logger.exception(
                "No fue posible autenticar Drive al verificar acceso de %s.",
                correo,
            )
            return True

        dominio_correo = correo.rsplit('@', 1)[-1] if '@' in correo else ''
        for record in self:
            google_id = record.file_id or record.drive_file_id
            if not google_id:
                continue
            try:
                archivo = drive.CreateFile({'id': google_id})
                permisos = archivo.GetPermissions()
                tiene_acceso = any(
                    (
                        (permiso.get('emailAddress') or '').strip().lower() == correo
                        or permiso.get('type') == 'anyone'
                        or (
                            permiso.get('type') == 'domain'
                            and dominio_correo
                            and (permiso.get('domain') or '').strip().lower() == dominio_correo
                        )
                    )
                    for permiso in permisos
                )
                if not tiene_acceso:
                    archivo.InsertPermission({
                        'type': 'user',
                        'value': correo,
                        'role': 'reader',
                    })
            except Exception:
                # El visor conserva su comportamiento actual aunque Drive no
                # permita consultar o crear el permiso en ese momento.
                _logger.exception(
                    "No fue posible garantizar permiso lector para %s en %s.",
                    correo,
                    google_id,
                )
        return True


    @api.model
    def _completar_valores_visor(self, vals):
        """Añade el visor estándar cuando el documento ya tiene ID en Drive."""
        google_id = vals.get("file_id") or vals.get("drive_file_id")
        if not google_id:
            return vals
        preview_url = f"https://drive.google.com/file/d/{google_id}/preview"
        edit_url = f"https://drive.google.com/file/d/{google_id}/view"
        vals.setdefault("preview_url", preview_url)
        vals.setdefault("edit_url", edit_url)
        vals.setdefault(
            "preview_html",
            f'<iframe src="{preview_url}" width="100%" height="600px" '
            'loading="lazy" frameborder="0" allowfullscreen></iframe>',
        )
        vals.setdefault(
            "edit_html",
            f'<iframe src="{edit_url}" width="100%" height="600px" '
            'loading="lazy" frameborder="0"></iframe>',
        )
        return vals

    def read(self, fields=None, load='_classic_read'):
        """Permite visualizar también adjuntos antiguos sin visor guardado."""
        result = super(Drive, self).read(fields=fields, load=load)
        if not result:
            return result
        campos_visor = {'preview_url', 'edit_url', 'preview_html', 'edit_html'}
        if (
            self.env.context.get('ensure_drive_reader')
            and not self.env.context.get('skip_auto_drive_reader')
            and (fields is None or campos_visor.intersection(fields))
        ):
            self._asegurar_lector_drive_usuario_actual()
        records = {record.id: record for record in self}
        for values in result:
            record = records.get(values.get('id'))
            google_id = (
                values.get('file_id')
                or values.get('drive_file_id')
                or (record and (record.file_id or record.drive_file_id))
            )
            if not google_id:
                continue
            preview_url = f"https://drive.google.com/file/d/{google_id}/preview"
            edit_url = f"https://drive.google.com/file/d/{google_id}/view"
            if fields is None or 'preview_url' in fields:
                values['preview_url'] = values.get('preview_url') or preview_url
            if fields is None or 'edit_url' in fields:
                values['edit_url'] = values.get('edit_url') or edit_url
            if fields is None or 'preview_html' in fields:
                values['preview_html'] = values.get('preview_html') or (
                    f'<iframe src="{preview_url}" width="100%" height="600px" '
                    'loading="lazy" frameborder="0" allowfullscreen></iframe>'
                )
            if fields is None or 'edit_html' in fields:
                values['edit_html'] = values.get('edit_html') or (
                    f'<iframe src="{edit_url}" width="100%" height="600px" '
                    'loading="lazy" frameborder="0"></iframe>'
                )
        return result

    contratacion_fuv_id = fields.Many2one(
        "contrataciones_fuv", string="Contrato Relacionado"
    )
    otrosi_id = fields.Many2one("otro_si", string="Contrato Relacionado")

    correo_electrinico = fields.Char(string="Correo electrónico")
    observacion = fields.Text(string="Comentario")

    file_data_visible = fields.Boolean(default=True)
    file_data = fields.Binary("File Data")

    all_folders_url = fields.Char("Ver carpeta", compute="_compute_all_folders_url")
    all_folders_url_html = fields.Text("Ver carpeta")
    create_date = fields.Datetime(
        "Fecha de creación", readonly=True, default=fields.Datetime.now
    )
    file_url = fields.Char(string="File URL")
    file_id = fields.Char(
        string="File ID",
    )

    file_id_contratista = fields.Char(string="Archivo original contratista")

    document_ids = fields.One2many("drive.document", "drive_id", string="Documentos")
    permission_email = fields.Char(
        "Email", help="Email del usuario al que se le va a dar acceso"
    )
    permission_role = fields.Selection(
        [
            ("reader", "Lector"),
            ("commenter", "Comentador"),
            ("writer", "Editor"),
        ],
        string="Rol",
        default="reader",
        help="Tipo de permiso que se otorgará",
    )
    permissions_info = fields.Text(string="Información de Permisos")
    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)
    hiden = fields.Char(string="hiden")
    drive_file_id = fields.Char("Google Drive File ID", readonly=True)
    folder_id = fields.Char(string="Google Drive Folder ID")

    stage3_id = fields.Many2one("drive_stage", string="Ubicación", tracking=True)
    stage4_id = fields.Many2one("cuenta_de_cobros", string="Ubicación", tracking=True)
    stage5_id = fields.Many2one("otrosi_drive", string="Ubicación", tracking=True)
    stage2_id = fields.Many2one("drive.file", string="Ubicación", tracking=True)
    acta_contabilidad_adicional_id = fields.Many2one(
        "acta_entrega_documentos",
        string="Acta contabilidad adicional",
        tracking=True,
    )
    stage6_id = fields.Many2one(
        "drive_stage_provedor", string="Ubicación", tracking=True
    )

    # Nuevo campo booleano
    ejecutar_toggle = fields.Boolean(string="Ejecutar Toggle Sidebar", default=False)

    actualizar_visible = fields.Boolean(default=False)
    actualizar_visible2 = fields.Boolean(default=False)

    solo_lectura_cuenta_finalizada = fields.Boolean(
        string="Solo lectura por cuenta finalizada",
        compute="_compute_solo_lectura_cuenta_finalizada",
    )

    es_certificacion_conjunta_generada = fields.Boolean(
        string="Es certificación conjunta generada",
        compute="_compute_es_certificacion_conjunta_generada",
    )


    @api.depends(
        "file_name",
        "stage4_id.contratistas_provedores.certificacion_conjunta_id",
        "stage4_id.contratistas_provedores.certificacion_conjunta_juridica_id",
        "contratistas_provedores.certificacion_conjunta_id",
        "contratistas_provedores.certificacion_conjunta_juridica_id",
    )
    def _compute_es_certificacion_conjunta_generada(self):
        for record in self:
            acta = (
                record.contratistas_provedores
                or record.stage4_id.contratistas_provedores
            )
            vinculada_como_generada = bool(
                acta
                and record.id
                and record.id in {
                    acta.certificacion_conjunta_id.id,
                    acta.certificacion_conjunta_juridica_id.id,
                }
            )
            nombre = (record.file_name or "").lower()
            nombre_generado = bool(
                re.match(r"^[12]_certificaci[oó]n conjunta_", nombre)
            )
            record.es_certificacion_conjunta_generada = (
                vinculada_como_generada or nombre_generado
            )


    @api.depends_context("uid", "force_drive_readonly")
    @api.depends(
        "stage4_id.contratistas_provedores.estado",
        "stage4_id.contratistas_provedores.usuarios_contratacion",
        "stage4_id.contratistas_provedores.usuarios_supervision",
        "stage4_id.contratistas_provedores.usuarios_contabilidad",
        "stage4_id.contratistas_provedores.usuarios_auxiliar_tesoreria",
        "stage4_id.contratistas_provedores.usuarios_tesoreria",
        "acta_contabilidad_adicional_id.estado",
        "acta_contabilidad_adicional_id.usuarios_contratacion",
        "acta_contabilidad_adicional_id.usuarios_supervision",
        "acta_contabilidad_adicional_id.usuarios_contabilidad",
        "acta_contabilidad_adicional_id.usuarios_auxiliar_tesoreria",
        "acta_contabilidad_adicional_id.usuarios_tesoreria",
    )
    def _compute_solo_lectura_cuenta_finalizada(self):
        usuario_editor_drive = (
            self.env.su
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("Matriz.group_soporte")
        )

        force_readonly = bool(
            self.env.context.get("force_drive_readonly")
        )

        for record in self:
            acta = (
                record.acta_contabilidad_adicional_id
                or record.stage4_id.contratistas_provedores
            )

            documento_cuenta_cobro = bool(acta)

            puede_editar_acta = bool(
                acta
                and getattr(
                    acta,
                    "puede_editar_estado_actual",
                    False
                )
            )

            # LOG para identificar si el documento pertenece o no
            # a una cuenta de cobro
            if documento_cuenta_cobro:
                _logger.info(
                    "[DRIVE_CUENTA_COBRO] Drive ID %s: ES cuenta de cobro | "
                    "Modelo relacionado: %s | ID relacionado: %s | Estado: %s",
                    record.id,
                    acta._name,
                    acta.id,
                    getattr(acta, "estado", False),
                )
            else:
                _logger.info(
                    "[DRIVE_CUENTA_COBRO] Drive ID %s: NO ES cuenta de cobro",
                    record.id,
                )

            record.solo_lectura_cuenta_finalizada = bool(
                not usuario_editor_drive
                and (
                    force_readonly
                    or (
                        documento_cuenta_cobro
                        and not puede_editar_acta
                    )
                )
            )

    token = fields.Char("Access Token", readonly=True)  # Token único para acceso

    nombre_contratista = fields.Char(string="Nombre del contratista")

    # Relaciones
    contratciones_fuv_ids = fields.One2many(
        "contrataciones_fuv", "documento_identidad_id", string="Contrataciones FUV"
    )

    def _generate_access_token(self):
        """Genera un token único para el formulario público."""
        return str(uuid.uuid4())

    firma_publica_disponible = fields.Boolean(
        compute="_compute_firma_publica_disponible"
    )
    firma_solicitud_pendiente_id = fields.Many2one(
        "drive.firma.solicitud", compute="_compute_firma_solicitud_pendiente_id"
    )
    firma_solicitud_firmada_id = fields.Many2one(
        "drive.firma.solicitud", compute="_compute_firma_solicitud_firmada_id"
    )
    firma_grupo_visible = fields.Boolean(
        compute="_compute_firma_grupo_visible",
        help="True si al menos uno de los botones del desplegable de firma "
        "electrónica (Enviar/Reenviar/Revocar/Verificar) aplicaría — "
        "sirve para ocultar el desplegable completo en vez de mostrarlo "
        "vacío.",
    )

    @api.depends(
        "contratacion_fuv_documentos_id",
        "contratacion_fuv_documentos_id.correo",
        "contratacion_fuv_documentos_id.contratos_id",
    )
    def _compute_firma_publica_disponible(self):
        # _CAMPOS_FIRMABLES vive en contrataciones_fuv.py (dueño de esos
        # campos Many2one('drive', ...)) — import local: contrataciones_fuv.py
        # importa de este módulo a nivel de módulo (GoogleDriveAuth), así que
        # un import a nivel de módulo aquí de vuelta hacia contrataciones_fuv.py
        # fallaría (módulo a medio cargar). Mismo patrón que el import de
        # _CP_REQUIRED_VISIBLE más abajo en este archivo.
        from .contrataciones_fuv import _CAMPOS_FIRMABLES

        for rec in self:
            contratacion = rec.contratacion_fuv_documentos_id
            slots_firmables = {
                getattr(contratacion, campo).id
                for campo in _CAMPOS_FIRMABLES
                if getattr(contratacion, campo, False)
            }
            rec.firma_publica_disponible = bool(
                contratacion
                and contratacion.correo
                and rec.id in slots_firmables
            )

    @api.depends("contratacion_fuv_documentos_id")
    def _compute_firma_solicitud_pendiente_id(self):
        # Un solo search() para todo el recordset (agrupado en Python) en
        # vez de uno por registro — importante en listas/kanban con muchos
        # documentos, donde este compute se evalúa para cada fila visible.
        Solicitud = self.env["drive.firma.solicitud"].sudo()
        solicitudes = Solicitud.search(
            [
                ("drive_id", "in", self.ids),
                ("estado", "=", "pendiente"),
            ]
        )
        por_drive = {s.drive_id.id: s for s in solicitudes}
        for rec in self:
            rec.firma_solicitud_pendiente_id = por_drive.get(rec.id, False)

    @api.depends("contratacion_fuv_documentos_id")
    def _compute_firma_solicitud_firmada_id(self):
        # Mismo motivo que arriba: un solo search() batcheado. El orden
        # 'fecha_envio desc' + setdefault() se queda con la más reciente
        # por drive_id (equivalente al limit=1 por registro de antes).
        Solicitud = self.env["drive.firma.solicitud"].sudo()
        solicitudes = Solicitud.search(
            [
                ("drive_id", "in", self.ids),
                ("estado", "=", "firmado"),
            ],
            order="fecha_envio desc",
        )
        por_drive = {}
        for s in solicitudes:
            por_drive.setdefault(s.drive_id.id, s)
        for rec in self:
            rec.firma_solicitud_firmada_id = por_drive.get(rec.id, False)

    @api.depends(
        "firma_publica_disponible",
        "firma_solicitud_pendiente_id",
        "firma_solicitud_firmada_id",
        "solo_lectura_cuenta_finalizada",
    )
    def _compute_firma_grupo_visible(self):
        # Una firma completada mantiene visible el indicador y Verificar.
        # Sin firma, basta con que aplique alguna acción de envío o gestión.
        for rec in self:
            rec.firma_grupo_visible = bool(
                rec.firma_solicitud_firmada_id
                or (
                    not rec.solo_lectura_cuenta_finalizada
                    and (
                        rec.firma_publica_disponible or rec.firma_solicitud_pendiente_id
                    )
                )
            )

    # Vigencia del enlace público de firma. Vive como constante de clase
    # (no como número suelto en 2 sitios) para que el cálculo real del
    # vencimiento (usado al crear/reenviar la solicitud) y el texto del
    # toast que se lo informa al usuario no puedan desincronizarse.
    _HORAS_VIGENCIA_FIRMA = 72

    def _mensaje_vigencia(self, token_expira):
        """Texto legible con cuánto falta para que venza un enlace de
        firma, en la zona horaria del usuario que ve el toast."""
        expira_local = fields.Datetime.context_timestamp(self, token_expira)
        return (
            f"Vence en {self._HORAS_VIGENCIA_FIRMA} horas "
            f"({expira_local.strftime('%d/%m/%Y %H:%M')})."
        )

    @staticmethod
    def _notificacion_firma(title, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_enviar_para_firmar(self):
        """Envía al contratista un link público de un solo uso para que
        dibuje su firma y quede estampada sobre este documento."""
        self.ensure_one()
        contratacion = self.contratacion_fuv_documentos_id
        if not contratacion:
            raise UserError(
                _("Este documento no está asociado a una contratación FUV.")
            )
        email = contratacion.correo
        if not email:
            raise UserError(
                _("El contratista no tiene un correo electrónico registrado.")
            )

        # Resguardo defensivo: el botón que llama a esta acción ya se oculta
        # en la vista para tipos de documento no firmables (ver
        # firma_publica_disponible / _CAMPOS_FIRMABLES en contrataciones_fuv.py),
        # pero al ser una acción "object" es invocable directamente por
        # RPC/ORM sin pasar por esa vista.
        from .contrataciones_fuv import _CAMPOS_FIRMABLES

        slots_firmables = {
            getattr(contratacion, campo).id
            for campo in _CAMPOS_FIRMABLES
            if getattr(contratacion, campo, False)
        }
        if self.id not in slots_firmables:
            raise UserError(_("Este tipo de documento no admite firma electrónica."))

        solicitud = self.env["drive.firma.service"].solicitar_firma(
            self, email, contratacion.name,
            horas_vigencia=self._HORAS_VIGENCIA_FIRMA,
        )
        token_expira = solicitud.token_expira

        return self._notificacion_firma(
            "Solicitud enviada",
            f"Solicitud de firma enviada a {email}. {self._mensaje_vigencia(token_expira)}",
        )

    def action_reenviar_solicitud_firma(self):
        """Reenvía el correo de la solicitud de firma pendiente actual,
        reusando el mismo link/token (no crea una solicitud nueva) y
        extendiendo su vencimiento 72h desde ahora."""
        self.ensure_one()
        solicitud = self.firma_solicitud_pendiente_id.sudo()
        if not solicitud:
            raise UserError(
                _("No hay ninguna solicitud de firma pendiente para reenviar.")
            )

        self.env["drive.firma.service"].reenviar_solicitud(
            solicitud, horas_vigencia=self._HORAS_VIGENCIA_FIRMA,
        )
        token_expira = solicitud.token_expira

        return self._notificacion_firma(
            "Solicitud reenviada",
            f"Se reenvió el correo con el mismo enlace a {solicitud.email_contratista}. "
            f"{self._mensaje_vigencia(token_expira)}",
        )

    def action_revocar_solicitud_firma(self):
        """Invalida la solicitud de firma pendiente: el link que ya recibió
        el contratista deja de funcionar de inmediato."""
        self.ensure_one()
        solicitud = self.firma_solicitud_pendiente_id.sudo()
        if not solicitud:
            raise UserError(
                _("No hay ninguna solicitud de firma pendiente para revocar.")
            )

        self.env["drive.firma.service"].revocar_solicitud(solicitud)

        return self._notificacion_firma(
            "Solicitud revocada", "La solicitud de firma fue revocada."
        )

    def action_verificar_integridad_firma(self):
        """Compara el archivo actual de Drive contra el hash guardado al
        momento de la última firma completada de este documento."""
        self.ensure_one()
        solicitud = self.firma_solicitud_firmada_id.sudo()
        if not solicitud:
            raise UserError(
                _("Este documento no tiene ninguna firma completada para verificar.")
            )
        return solicitud.action_verificar_integridad()

    def send_update_email(self):
        """Envía un correo al contratista con la URL para actualizar el documento."""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")

        for record in self:
            if not record.correo_electrinico:
                _logger.error(
                    f"Correo electrónico no encontrado para el registro {record.id}"
                )
                continue  # Si no hay correo, salta este registro

            record.token = record._generate_access_token()

            # Construir URL pública
            public_url = f"{base_url}/document/update/{record.id}/{record.token}"
            _logger.info(f"Generated public URL: {public_url}")

            # Crear contenido del correo
            subject = f"Actualización de documento requerido - {record.nombre_contratista or 'Contratista'}"
            body = f"""
            <p>Estimado/a {record.nombre_contratista or ''},</p>
            <p>El documento <strong>{record.name}</strong> necesita ser actualizado.</p>
            <p>Para actualizar el documento, haga clic en el siguiente enlace:</p>
            <p><a href="{public_url}">Actualizar documento</a></p>
            <p>Gracias,</p>
            <p>SGO</p>
            """

            # Enviar correo usando `sudo()` para evitar problemas de permisos
            mail_values = {
                "subject": subject,
                "body_html": body,
                "email_to": record.correo_electrinico,
                "email_from": self.env.user.email or "no-reply@tudominio.com",
            }

            mail = self.env["mail.mail"].sudo().create(mail_values)
            try:
                mail.sudo().send()
                _logger.info(f"Correo enviado a {record.correo_electrinico} con éxito.")
                trace_message = (
                    f"Solicitud de actualización enviada a {record.correo_electrinico} "
                    f"para el documento {record.name or record.file_name}."
                )
                record.sudo().message_post(body=trace_message)
                self.env["drive.trace"].sudo().create(
                    {
                        "drive_id": record.id,
                        "action_type": "update",
                        "user_id": self.env.user.id,
                        "notes": trace_message,
                    }
                )
            except Exception as e:
                _logger.error(
                    f"Error al enviar el correo a {record.correo_electrinico}: {str(e)}"
                )

    def send_chat_notification(self, action):
        print("Enviando notificación de chat...")

        bot_user = (
            self.env.ref("base.user_root", raise_if_not_found=False) or self.env.user
        )
        print(f"Usuario bot encontrado: {bot_user.name}")

        for record in self:
            subject = f"📢 Notificación: Documento {record.name} {action}"
            message = (
                f"El documento <strong>{record.name}</strong> ha sido {action} correctamente.<br/>"
                f"📝 <strong>Comentario:</strong> {record.observacion or 'Sin comentario'}"
            )

            print(f"Mensaje a enviar: {message}")

            # Obtener correos de seguidores
            follower_emails = [
                follower.partner_id.email
                for follower in record.message_follower_ids
                if follower.partner_id.email
            ]

            print(
                f"Notificación enviada a los correos: {', '.join(follower_emails) or 'Ninguno'}"
            )

            # Enviar mensaje al chatter con un asunto
            msg = record.message_post(
                subject=subject,
                body=message,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
                author_id=bot_user.id,
            )

            print(f"Mensaje a enviar a: {msg}")
            print(
                f"Mensaje enviado exitosamente con ID: {msg.id if msg else 'No enviado'}"
            )

    def toggle_sidebar_information(self):
        self.ejecutar_toggle = True
        self._set_detalles_to_false()
        for record in self:
            # Establecer detalles a True para el registro
            # Buscar el registro de 'otrosi' relacionado con este 'drive'
            otrosi_record = self.env["otrosi"].search(
                [("documentos_filtrados", "=", record.id)], limit=1
            )

            if otrosi_record:
                # Usar directamente folder_id (el ID de la carpeta)
                folder_id = (
                    record.folder_id
                )  # Aquí folder_id es simplemente el ID de la carpeta (cadena)

                # Obtener el nombre de la carpeta de Google Drive usando la API
                folder_name = self._get_folder_name_from_drive(folder_id)

                # Actualizar las variables en el modelo 'otrosi' con los valores del registro 'drive'
                otrosi_record.write(
                    {
                        "file_name": record.file_name,
                        "user_id": record.user_id.id,  # Asignar el ID del usuario
                        "carpeta_id": folder_name,  # Asignar el nombre de la carpeta
                        "tags_html": record.tags_html,  # Asignar las etiquetas (Many2many)
                    }
                )

                # Evitar modificar custom_tags_ids
                # Si no deseas tocar custom_tags_ids, no debes hacer nada con él.

    # Función para obtener el nombre de la carpeta de Google Drive
    def _get_folder_name_from_drive(self, folder_id):
        """Obtener el nombre de una carpeta en Google Drive usando su ID."""

        if not folder_id:
            _logger.warning("ID de carpeta vacío.")
            return "ID de carpeta inválido"

        # Obtener la instancia autenticada de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            # Crear el objeto de archivo/carpeta
            file = drive.CreateFile({"id": folder_id})
            file.FetchMetadata()  # Obtener metadatos

            if "title" in file:
                _logger.info(f"Carpeta encontrada: {file['title']}")
                return file["title"]
            else:
                _logger.warning(f"No se encontró la carpeta con ID {folder_id}.")
                return "Carpeta no encontrada"

        except pydrive2.files.FileNotUploadedError:
            _logger.error(
                f"El archivo/carpeta con ID {folder_id} no existe en Google Drive."
            )
            return "Carpeta no encontrada"

        except Exception as e:
            _logger.error(f"Error inesperado al obtener metadatos: {str(e)}")
            return "Error al obtener metadatos"

    def _set_detalles_to_false(self):
        for record in self:
            # Buscar todos los registros relacionados en el modelo 'drive' usando 'env'
            drive_records = self.env["otrosi"].search(
                [("documentos_filtrados", "=", record.id)]
            )

            # Recorrer todos los registros de 'drive' encontrados
            for drive_record in drive_records:
                # Establecer el campo 'detalles' a False en todos los registros encontrados
                drive_record.write(
                    {"detalles": True}  # Cambiar el valor del campo detalles a False
                )

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    def set_drive_permission(self):
        for record in self:
            try:
                # Obtén la instancia de autenticación de Google Drive
                drive_auth = (
                    GoogleDriveAuth.get_instance()
                )  # Obtener la instancia única de GoogleDriveAuth
                drive = (
                    drive_auth.get_drive()
                )  # Usar el método para obtener la instancia de Google Drive autenticada

                # Obtener el archivo de Google Drive
                gdrive_file = drive.CreateFile({"id": record.drive_file_id})
                gdrive_file.FetchMetadata()

                # Mapeo de roles de permiso
                role_map = {
                    "reader": "Lector",
                    "commenter": "Comentador",
                    "writer": "Editor",
                }

                if record.permission_role not in role_map:
                    raise ValueError(
                        f"Rol de permiso inválido: {record.permission_role}"
                    )

                # Añadir el permiso
                permission = {
                    "type": "user",  # O puede ser 'group', 'domain', 'anyone' dependiendo del caso
                    "value": record.permission_email,
                    "role": record.permission_role,
                    "withLink": False,
                }

                # Insertar permiso
                gdrive_file.InsertPermission(permission)

                # Actualizar el campo de permisos
                existing_permissions_info = record.permissions_info or ""
                permission_role_translated = role_map.get(
                    record.permission_role, "Desconocido"
                )

                # Construir el nuevo contenido del campo permissions_info
                permissions_dict = {}
                for line in existing_permissions_info.split("\n"):
                    if ":" in line:
                        email, role = line.split(":", 1)
                        permissions_dict[email.strip()] = role.strip()

                # Actualizar o agregar el nuevo permiso
                permissions_dict[record.permission_email] = permission_role_translated

                # Formatear el contenido para el campo permissions_info
                new_permissions_info = "\n".join(
                    f"{email}: {role}" for email, role in permissions_dict.items()
                )
                record.permissions_info = new_permissions_info

                record.message_post(
                    body=f"Permiso de {permission_role_translated} otorgado a {record.permission_email} para el archivo {record.file_name}."
                )
                self.env["drive.trace"].sudo().create(
                    {
                        "drive_id": record.id,
                        "action_type": "share",
                        "user_id": self.env.user.id,
                        "notes": (
                            f"Permiso de {permission_role_translated} otorgado a "
                            f"{record.permission_email} para {record.file_name}."
                        ),
                    }
                )
                print(
                    f"Permiso de {permission_role_translated} otorgado a {record.permission_email} para el archivo {record.file_name}."
                )

                # Limpiar el campo permission_email
                record.permission_email = ""

            except Exception as e:
                print(f"Error al establecer el permiso en Google Drive: {e}")
                record.message_post(body=f"Error al establecer el permiso: {e}")

    def move_to_folder_popup(self):
        return {
            "name": self.file_name,
            "view_mode": "form",
            "res_model": "drive.move.to.folder.wizard",
            "type": "ir.actions.act_window",
            "target": "new",
            "context": {"default_drive_id": self.id},
        }

    def papelera(self):
        # Llamar al método fetch_trashed_files del modelo papelera
        papelera_model = self.env["papelera"]
        papelera_model.fetch_trashed_files()

        # Mostrar una notificación de éxito
        return {
            "type": "ir.actions.act_window",
            "name": "Papelera",
            "view_mode": "kanban,tree,form",
            "res_model": "papelera",
            "type": "ir.actions.act_window",
            "target": "current",
        }

    def drive_share(self):
        proyecto = self.proyecto_relacionado
        user_ids = proyecto.users_with_access.ids if proyecto else []
        return {
            "name": "Compartir documento",
            "view_mode": "form",
            "res_model": "drive.document",
            "type": "ir.actions.act_window",
            "target": "new",
            "context": {
                "default_drive_id": self.id,
                "default_name": self.name or self.file_name or "",
                "default_project_user_ids": [[6, 0, user_ids]],
                "allowed_user_ids": user_ids,
            },
        }

    def action_view_history(self):
        return {
            "name": "Trazabilidad",
            "view_mode": "tree",
            "res_model": "drive.trace",
            "type": "ir.actions.act_window",
            "target": "current",
        }

    @api.depends("folder_id")
    def _compute_all_folders_url(self):
        base_url = "https://drive.google.com/drive/folders/"
        self.all_folders_url = base_url + (self.folder_id or "root")

    def _get_drive_auth(self):
        _config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"
        )
        client_secrets_path = os.path.join(_config_dir, "client_secrets.json")

        gauth = GoogleAuth()
        gauth.LoadClientConfigFile(client_secrets_path)

        try:
            credentials_path = os.path.join(_config_dir, "credentials_module.json")
            gauth.LoadCredentialsFile(credentials_path)
        except Exception as e:
            print(f"Error cargando el archivo de credenciales: {e}")
            gauth.LocalWebserverAuth()
            gauth.SaveCredentialsFile(credentials_path)

        return gauth

    view_mode = fields.Selection(
        [
            ("preview", "Visualizar"),
            ("edit", "Editar"),
        ],
        string="View Mode",
        default="preview",
        store=False,
    )

    # Función optimizada para obtener las carpetas, solo si es necesario
    def _get_folder_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        # Obtener la instancia de autenticación de Google Drive
        drive_auth = (
            GoogleDriveAuth.get_instance()
        )  # Obtener la instancia única de GoogleDriveAuth
        drive = (
            drive_auth.get_drive()
        )  # Usar el método para obtener la instancia de Google Drive autenticada

        # Calcular la fecha de hace 7 días
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        # Función para listar carpetas de manera recursiva
        def list_folders(folder_id):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                # Recursivamente listar carpetas dentro de esta carpeta
                options.extend(list_folders(folder["id"]))
            return options

        # Listar carpetas desde la raíz ('root')
        root_folders = list_folders("root")

        return root_folders

    def open_url(self):
        return {
            "type": "ir.actions.act_url",
            "url": self.preview_url,
            "target": "new",
        }

    def open_documento(self):
        return {
            "type": "ir.actions.act_url",
            "url": self.edit_url,
            "target": "new",
        }

    def open_carpeta(self):
        return {
            "type": "ir.actions.act_url",
            "url": self.all_folders_url,
            "target": "new",
        }

    def _validar_comprobante_reembolso_auditoria_drive(self, legalizacion):
        # El permiso histórico de Tesorería no cambia. Para Auditoría, la
        # condición se comprueba también al guardar, por si cambió la etapa.
        if (
            legalizacion
            and not self.env.su
            and self.env.user.has_group("Matriz.group_contabilidad")
            and not self.env.user.has_group("Matriz.group_tesoreria")
        ):
            legalizacion._validar_comprobante_reembolso_auditoria()

    @api.model
    def create(self, vals):
        """
        Orquesta la creación asegurando calidad de datos,
        limpieza y persistencia.
        """
        legalizacion_id = vals.get(
            "viaticos_otros_id",
            self.env.context.get("default_viaticos_otros_id"),
        )
        if legalizacion_id:
            self._validar_comprobante_reembolso_auditoria_drive(
                self.env["viaticos"].browse(legalizacion_id)
            )
        # 1. Validación y Preparación de Metadata
        self._prepare_pdf_metadata(vals)
        self._completar_valores_visor(vals)

        # Conservar el documento anterior durante una corrección del portal.
        if not self.env.context.get("skip_drive_duplicate_cleanup"):
            self._handle_duplicate_cleanup(vals)

        # 3. Persistencia (Core Odoo)
        record = super(Drive, self).create(vals)

        # 4. Sincronización con Drive
        if vals.get("file_data") and not self.env.context.get("from_process_button"):
            # En desarrollo, los contratos pueden pertenecer a carpetas de
            # producción que la cuenta Google local no puede consultar. El
            # mismo override usado al guardar el PDF firmado permite cargar
            # primero el documento que se enviará a firma sin alterar la
            # carpeta configurada en producción.
            folder_override = False
            if self.env.context.get("tipo_documento_upload") in {
                "contratos",
                "contratos_firmado",
            }:
                folder_override = _get_firma_test_drive_folder_id(self.env)

            uploaded = record.upload_to_drive(
                new_folder_id=folder_override or None
            )
            if not uploaded and self.env.context.get("tipo_documento_upload"):
                # La excepción hace rollback del create en la petición RPC.
                # Así action_save_and_close() nunca alcanza a vincular un
                # registro sin file_id ni a enviar una notificación falsa.
                raise UserError(
                    _(
                        "No se pudo subir el documento a Google Drive. "
                        "No se guardó ni se envió ninguna notificación. "
                        "Verifique el acceso a la carpeta destino e intente nuevamente."
                    )
                )

        return record

    def write(self, vals):
        """
        Orquesta la actualización manejando cambios de archivo,
        movimientos en Drive, trazabilidad y reparaciones automáticas.
        """
        vals = dict(vals)
        if {
            "file_data",
            "file_name",
            "file_id",
            "drive_file_id",
            "viaticos_otros_id",
        }.intersection(vals):
            for record in self:
                record._validar_comprobante_reembolso_auditoria_drive(
                    record.viaticos_otros_id
                )
            if vals.get("viaticos_otros_id"):
                self._validar_comprobante_reembolso_auditoria_drive(
                    self.env["viaticos"].browse(vals["viaticos_otros_id"])
                )
        if vals.get('file_id') or vals.get('drive_file_id'):
            self._completar_valores_visor(vals)

        campos_visualizacion_consulta = {
            "correo_electrinico",
            "actualizar_visible",
            "file_data_visible",
            "preview_url",
            "edit_url",
            "preview_html",
            "edit_html",
        }
        puede_gestionar_correcciones = (
            self.env.su
            or self.env.user.has_group("Matriz.group_contratacion_asis_coordinador")
            or self.env.user.has_group("Matriz.group_soporte")
        )
        if (
            (
                self.env.user.has_group("Matriz.group_consulta_coordinacion_financiera")
                or self.env.user.has_group("Matriz.group_consulta_global_cuentas_cobro")
            )
            and not puede_gestionar_correcciones
            and set(vals).issubset(campos_visualizacion_consulta)
        ):
            # Los botones de consulta intentan sincronizar estos datos técnicos
            # al abrir el visor. Para un lector no hace falta persistirlos y se
            # evita conceder permiso real de escritura sobre Drive.
            return True

        campos_documento = {
            "file_data", "file_name", "file_id", "drive_file_id", "folder_id",
            "stage2_id", "stage3_id", "stage4_id", "stage5_id", "stage6_id",
            "acta_contabilidad_adicional_id", "contratistas_provedores",
        }
        if (
            not self.env.su
            and campos_documento.intersection(vals)
            and any(record.solo_lectura_cuenta_finalizada for record in self)
        ):
            raise exceptions.AccessError(_(
                "Este documento está en modo de solo lectura y únicamente puede visualizarse o descargarse."
            ))

        _logger.info(
            "DRIVE WRITE | Iniciando actualización en %s registros. Vals keys: %s",
            len(self),
            list(vals.keys()),
        )

        # Persistencia (Core Odoo)
        result = super(Drive, self).write(vals)

        # Sync name to browser entries when file_name changes
        if 'file_name' in vals and vals.get('file_name'):
            entries = self.env['drive.browser.entry'].search([('drive_id', 'in', self.ids)])
            if entries:
                entries.with_context(skip_drive_sync=True).write({'name': vals['file_name']})

        # Post-sync solo cuando aplique
        if not self.env.context.get("skip_drive_sync"):
            for record in self:
                record._handle_post_write_sync(vals)

        return result

    @api.model
    def apply_renumber_map(self, gid_name_map):
        """Recibe {google_file_id: new_name} y actualiza file_name donde file_id coincida."""
        if not gid_name_map:
            return
        for rec in self.search([("file_id", "in", list(gid_name_map.keys()))]):
            new_name = gid_name_map.get(rec.file_id)
            if new_name and rec.file_name != new_name:
                rec.with_context(skip_drive_sync=True).write({"file_name": new_name})

    # -------------------------------------------------------------------------
    # PRIVATE METHODS (BUSINESS LOGIC & HELPERS)
    # -------------------------------------------------------------------------
    def _prepare_pdf_metadata(self, vals, is_update=False):
        """
        Valida el contenido del PDF y calcula páginas antes de guardar en BD.
        Modifica 'vals' por referencia.
        """
        fname = vals.get("file_name") or (
            self.file_name if is_update and len(self) == 1 else ""
        )
        fdata = vals.get("file_data")

        is_pdf = bool(fdata and fname and fname.lower().endswith(".pdf"))

        if is_pdf:
            vals.setdefault("page_total", 0)
            vals.setdefault("page_range", False)
        else:
            vals["page_total"] = 0
            vals["page_range"] = False

    def _handle_duplicate_cleanup(self, vals):
        """
        Busca archivos idénticos en la misma carpeta lógica y los elimina.

        Los documentos cargados desde el portal pueden conservar el mismo
        nombre original (por ejemplo, ``documento de prueba.pdf``), aunque
        correspondan a campos distintos. En ese caso el tipo documental y el
        acta forman parte de la identidad del archivo; de lo contrario subir
        la Resolución podía eliminar la Factura de la misma carpeta.
        """
        folder_id = vals.get("folder_id")
        file_name = vals.get("file_name")

        if not folder_id or not file_name:
            return

        # Buscamos duplicados (excluyendo el registro actual si existiera un contexto raro)
        domain = [
            ("file_name", "=", file_name),
            ("folder_id", "=", folder_id),
        ]

        portal_document_type = vals.get("portal_acta_document_type")
        if portal_document_type:
            domain.append(("portal_acta_document_type", "=", portal_document_type))
            parent_id = vals.get("contratistas_provedores")
            if parent_id:
                domain.append(("contratistas_provedores", "=", parent_id))

        # Limitamos a 1 para eficiencia, asumimos que no hay masivos
        duplicate = self.search(domain, limit=1)
        if duplicate:
            _logger.warning(
                "DRIVE DUPLICATE | Eliminando registro anterior ID: %s para sobreescritura.",
                duplicate.id,
            )
            duplicate.unlink()

    def _handle_post_write_sync(self, vals):
        """
        Maneja la lógica de Drive (Mover/Subir) y genera Trazabilidad.
        """
        if self.env.context.get("from_process_button"):
            return

        trace_notes = []

        # A. Lógica Drive
        if "folder_id" in vals:
            # Si cambiamos de carpeta, movemos el archivo en Drive
            if self.file_id:
                self.move_in_drive(vals["folder_id"])
                trace_notes.append(f"Archivo movido a carpeta ID {vals['folder_id']}")

        if "file_data" in vals and vals["file_data"]:
            # Si cambió el binario, subimos (o actualizamos) en Drive
            # Nota: upload_to_drive debería manejar internamente si es create o update en API Drive
            self.upload_to_drive()
            trace_notes.append("Contenido del binario actualizado.")

        # B. Trazabilidad
        if trace_notes:
            self.env["drive.trace"].create(
                {
                    "drive_id": self.id,
                    "action_type": "update",
                    "user_id": self.env.user.id,
                    "notes": " | ".join(trace_notes),
                }
            )

    # ==========================================================================
    # DICCIONARIO MAESTRO DE RELACIONES
    # ==========================================================================
    RELACIONES_A_ACTUALIZAR = {
        'acta_entrega_documentos': [
            ('certificacion_conjunta_id', 'certificacion_conjunta_documento', 'certificacion_conjunta_visible', 'certificacion_conjunta_name'),
            ('documento_equivalente_id', 'documento_equivalente_documento', 'documento_equivalente_visible', 'documento_equivalente_name'),
            ('informe_de_actividades_id', 'informe_de_actividades_documento', 'informe_de_actividades_visible', 'informe_de_actividades_name'),
            ('declaracion_juramentada_y_soportes_id', 'declaracion_juramentada_y_soportes_documento', 'declaracion_juramentada_y_soportes_visible', 'declaracion_juramentada_y_soportes_name'),
            ('rut_actualizado_id', 'rut_actualizado_documento', 'rut_actualizado_visible', 'rut_actualizado_name'),
            ('anexo_tributario_2_id', 'anexo_tributario_documento', 'anexo_tributario_visible', 'anexo_tributario_name'),
            ('factura_id', 'factura_documento', 'factura_visible', 'factura_name', 'factura'),
            ('resolucion_facturacion_electronica_2_id', 'resolucion_facturacion_electronica_documento', 'resolucion_facturacion_electronica_visible', 'resolucion_facturacion_electronica_name'),
            ('planilla_de_aportes_a_seguridad_social_id', 'planilla_de_aportes_a_seguridad_social_documento', 'planilla_de_aportes_a_seguridad_social_visible', 'planilla_de_aportes_a_seguridad_social_name'),
            ('certificado_de_expensa_id', 'certificado_de_expensa_documento', 'certificado_de_expensa_visible', 'certificado_de_expensa_name'),
            ('copia_contrato_2_id', 'copia_contrato_documento', 'copia_contrato_visible', 'copia_contrato_name'),
            ('copia_cedula_2_id', 'copia_cedula_documento', 'copia_cedula_visible', 'copia_cedula_name'),
            ('regalias_copia_contrato_id', 'regalias_copia_contrato_documento', False, 'regalias_copia_contrato_name'),
            ('regalias_copia_cedula_id', 'regalias_copia_cedula_documento', False, 'regalias_copia_cedula_name'),
            ('certificacion_bancaria_2_id', 'certificacion_bancaria_documento', 'certificacion_bancaria_visible', 'certificacion_bancaria_name'),
            ('registro_de_proveedores_2_id', 'registro_de_proveedores_documento', 'registro_de_proveedores_visible', 'registro_de_proveedores_name'),
            ('planilla_de_aportes_a_seguridad_social_ju_id', 'planilla_de_aportes_a_seguridad_social_ju_documento', 'planilla_de_aportes_a_seguridad_social_ju_visible', 'planilla_de_aportes_a_seguridad_social_ju_name'),
            ('causacion_id', 'causacion_documento', 'causacion_visible', 'causacion_name', 'causacion'),
            ('retencion_id', 'retencion_documento', 'retencion_visible', 'retencion_name', 'retencion'),
            ('articulos_id', 'articulos_documento', 'articulos_visible', 'articulos_name', 'Articulos'),
            ('documento_liquidacion_id', 'documento_liquidacion_documento', 'documento_liquidacion_visible', 'documento_liquidacion_name', 'documento_liquidacion'),
            ('documento_cruce_id', 'documento_cruce_documento', 'documento_cruce_visible', 'documento_cruce_name', 'documento_cruce'),
            ('reporte_liquidacion_id', 'reporte_liquidacion_documento', 'reporte_liquidacion_visible', 'reporte_liquidacion_name', 'reporte_liquidacion'),
            ('documentos_adicionales_contabilidad_id', 'documentos_adicionales_contabilidad_documento', 'documentos_adicionales_contabilidad_visible', 'documentos_adicionales_contabilidad_name', 'documentos_adicionales_contabilidad'),
            ('orden_de_pago_id', 'orden_de_pago_documento', 'orden_de_pago_visible', 'orden_de_pago_name', 'orden_de_oago'),
            ('comprobante_de_pago_id', 'comprobante_de_pago_documento', 'comprobante_de_pago_visible', 'comprobante_de_pago_name', 'comprobante_de_pago'),
            ('comprobante_bancario_id', 'comprobante_bancario_documento', 'comprobante_bancario_visible', 'comprobante_bancario_name', 'comprobante_bancario'),
            ('factura_electronica_juridica_id', 'factura_electronica_juridica_doc', 'factura_electronica_juridica_visible', 'factura_electronica_juridica_doc_name'),
            ('certificacion_conjunta_juridica_id', 'certificacion_conjunta_juridica_documento', 'certificacion_conjunta_juridica_visible', 'certificacion_conjunta_juridica_name'),
            ('registro_proveedores_juridica_id', 'registro_proveedores_juridica_documento', 'registro_proveedores_juridica_visible', 'registro_proveedores_juridica_name'),
            ('rut_actualizado_juridica_id', 'rut_actualizado_juridica_documento', 'rut_actualizado_juridica_visible', 'rut_actualizado_juridica_name'),
            ('resolucion_facturacion_electronica_juridica_2_id', 'resolucion_facturacion_electronica_juridica_documento', False, 'resolucion_facturacion_electronica_juridica_name', 'resolucion_facturacion_electronica_juridica'),
            ('camara_comercio_juridica_id', 'camara_comercio_juridica_documento', 'camara_comercio_juridica_visible', 'camara_comercio_juridica_name'),
            ('certificado_pago_parafiscales_juridica_id', 'certificado_pago_parafiscales_juridica_documento', 'certificado_pago_parafiscales_juridica_visible', 'certificado_pago_parafiscales_juridica_name'),
            ('tarjeta_profesional_revisor_fiscal_juridica_id', 'tarjeta_profesional_revisor_fiscal_juridica_documento', 'tarjeta_profesional_revisor_fiscal_juridica_visible', 'tarjeta_profesional_revisor_fiscal_juridica_name'),
            ('cedula_representante_legal_juridica_id', 'cedula_representante_legal_juridica_documento', 'cedula_representante_legal_juridica_visible', 'cedula_representante_legal_juridica_name'),
            ('cedula_revisor_fiscal_juridica_id', 'cedula_revisor_fiscal_juridica_documento', 'cedula_revisor_fiscal_juridica_visible', 'cedula_revisor_fiscal_juridica_name', 'cedula_revisor_fiscal_juridica'),
            ('cedula_representante_legal_de_contador_juridica_id', 'cedula_representante_legal_de_contador_juridica_documento', 'cedula_representante_legal_de_contador_juridica_visible', 'cedula_representante_legal_de_contador_juridica_name', 'cedula_representante_legal_de_contador_juridica'),
            ('Tarjeta_profesional_contador_juridica_id', 'Tarjeta_profesional_contador_juridica_documento', 'Tarjeta_profesional_contador_juridica_visible', 'Tarjeta_profesional_contador_juridica_name', 'Tarjeta_profesional_contador_juridica'),
            ('certificado_junta_contadores_juridica_id', 'certificado_junta_contadores_juridica_documento', 'certificado_junta_contadores_juridica_visible', 'certificado_junta_contadores_juridica_name'),
            ('rut_representante_legal_juridica_id', 'rut_representante_legal_juridica_documento', 'rut_representante_legal_juridica_visible', 'rut_representante_legal_juridica_name'),
            ('certificacion_bancaria_juridica_2_id', 'certificacion_bancaria_juridica_documento', 'certificacion_bancaria_juridica_visible', 'certificacion_bancaria_juridica_name'),
            ('anexo_tributario_juridica_2_id', 'anexo_tributario_juridica_documento', 'anexo_tributario_juridica_visible', 'anexo_tributario_juridica_name'),
            ('copia_contrato_juridica_2_id', 'copia_contrato_juridica_documento', 'copia_contrato_juridica_visible', 'copia_contrato_juridica_name'),
            ('certificacion_conjunta_firmado_id', 'certificacion_conjunta_firmado_documento', 'certificacion_conjunta_firmado_visible', 'certificacion_conjunta_firmado_name'),
            ('certificacion_conjunta_firmado_juridica_id', 'certificacion_conjunta_firmado_juridica_documento', 'certificacion_conjunta_firmado_juridica_visible', 'certificacion_conjunta_firmado_juridica_name'),
            ('cedula_de_contador_juridica_id', 'cedula_de_contador_juridica_documento', 'cedula_de_contador_juridica_visible', 'cedula_de_contador_juridica_name'),
        ],
        "orden_compra_servicios": [
            ("rut_pj_doc_id", "rut_pj_doc", "rut_pj_doc_visible", "rut_pj_name"),
            ("Anexo_tributario_doc_id", "Anexo_tributario", "Anexo_tributario_doc_visible", "Anexo_tributario_name"),
            ("Anexo_tributario_doc_pn_id", "Anexo_tributario_pn", "Anexo_tributario_doc_pn_visible", "Anexo_tributario_name_pn"),
            ("camara_comercio_doc_id", "camara_comercio_doc", "camara_comercio_doc_visible", "camara_comercio_name"),
            ("planilla_ss_doc_id", "planilla_ss_doc", "planilla_ss_doc_visible", "planilla_ss_name"),
            ("cert_parafiscales_doc_id", "cert_parafiscales_doc", "cert_parafiscales_doc_visible", "cert_parafiscales_name"),
            ("certificado_junta_contadores_doc_id", "junta_contadores_doc", "certificado_junta_contadores_doc_visible", "junta_contadores_name"),
            ("tp_contador_doc_id", "tp_contador_doc", "tp_contador_doc_visible", "tp_contador_name"),
            ("certificado_bancario_pj_doc_id", "certificado_bancario_pj", "certificado_bancario_pj_doc_visible", "certificado_bancario_pj_name"),
            ("resolucion_facturacion_doc_id", "resolucion_facturacion_doc", "resolucion_facturacion_doc_visible", "resolucion_facturacion_name"),
            ("documento_rpc_pj_doc_id", "documento_rpc_pj", "documento_rpc_pj_doc_visible", "documento_rpc_pj_name"),
            ("factura_cuenta_pj_doc_id", "factura_cuenta_pj", "factura_cuenta_pj_doc_visible", "factura_cuenta_pj_name"),
            ("rut_pn_doc_id", "rut_pn", "rut_pn_doc_visible", "rut_pn_name"),
            ("documento_identidad_doc_id", "documento_identidad", "documento_identidad_doc_visible", "documento_identidad_name"),
            ("planilla_doc_id", "planilla", "planilla_doc_visible", "planilla_name"),
            ("certificado_bancario_doc_id", "certificado_bancario", "certificado_bancario_doc_visible", "certificado_bancario_name"),
            ("documento_rpc_doc_id", "documento_rpc", "documento_rpc_doc_visible", "documento_rpc_name"),
            ("factura_cuenta_doc_id", "factura_cuenta", "factura_cuenta_doc_visible", "factura_cuenta_name"),
        ],
        "orden_compra_bienes": [
            ("rut_pj_doc_id", "rut_pj_doc", "rut_pj_doc_visible", "rut_pj_name"),
            ("Anexo_tributario_doc_id", "Anexo_tributario", "Anexo_tributario_doc_visible", "Anexo_tributario_name"),
            ("Anexo_tributario_doc_pn_id", "Anexo_tributario_pn", "Anexo_tributario_doc_pn_visible", "Anexo_tributario_name_pn"),
            ("camara_comercio_doc_id", "camara_comercio_doc", "camara_comercio_doc_visible", "camara_comercio_name"),
            ("planilla_ss_doc_id", "planilla_ss_doc", "planilla_ss_doc_visible", "planilla_ss_name"),
            ("cert_parafiscales_doc_id", "cert_parafiscales_doc", "cert_parafiscales_doc_visible", "cert_parafiscales_name"),
            ("certificado_junta_contadores_doc_id", "junta_contadores_doc", "certificado_junta_contadores_doc_visible", "junta_contadores_name"),
            ("tp_contador_doc_id", "tp_contador_doc", "tp_contador_doc_visible", "tp_contador_name"),
            ("certificado_bancario_pj_doc_id", "certificado_bancario_pj", "certificado_bancario_pj_doc_visible", "certificado_bancario_pj_name"),
            ("resolucion_facturacion_doc_id", "resolucion_facturacion_doc", "resolucion_facturacion_doc_visible", "resolucion_facturacion_name"),
            ("documento_rpc_pj_doc_id", "documento_rpc_pj", "documento_rpc_pj_doc_visible", "documento_rpc_pj_name"),
            ("factura_cuenta_pj_doc_id", "factura_cuenta_pj", "factura_cuenta_pj_doc_visible", "factura_cuenta_pj_name"),
            ("rut_pn_doc_id", "rut_pn", "rut_pn_doc_visible", "rut_pn_name"),
            ("documento_identidad_doc_id", "documento_identidad", "documento_identidad_doc_visible", "documento_identidad_name"),
            ("planilla_doc_id", "planilla", "planilla_doc_visible", "planilla_name"),
            ("certificado_bancario_doc_id", "certificado_bancario", "certificado_bancario_doc_visible", "certificado_bancario_name"),
            ("documento_rpc_doc_id", "documento_rpc", "documento_rpc_doc_visible", "documento_rpc_name"),
            ("factura_cuenta_doc_id", "factura_cuenta", "factura_cuenta_doc_visible", "factura_cuenta_name"),
        ],
        "contratista_proveedor": [
            ("documento_identidad_c_id", "documento_identidad", "documento_identidad_visible", "documento_identidad_name"),
            ("tarjeta_profesional_id", "tarjeta_profesional", "tarjeta_profesional_visible", "tarjeta_profesional_name"),
            ("rut_actualizado_id", "rut_actualizado", "rut_actualizado_visible", "rut_actualizado_name"),
            ("hoja_vida_id", "hoja_vida", "hoja_vida_visible", "hoja_vida_name"),
            ("certificado_estudio_id", "certificado_estudio", "certificado_estudio_visible", "certificado_estudio_name"),
            ("certificado_laborales_id", "certificado_laborales", "certificado_laborales_visible", "certificado_laborales_name"),
            ("cert_arl_id", "cert_arl", "cert_arl_visible", "cert_arl_name"),
            ("cert_afiliacion_salud_id", "cert_afiliacion_salud", "cert_afiliacion_salud_visible", "cert_afiliacion_salud_name"),
            ("cert_afiliacion_pension_id", "cert_afiliacion_pension", "cert_afiliacion_pension_visible", "cert_afiliacion_pension_name"),
            ("cert_examen_preocupacional_id", "cert_examen_preocupacional", "cert_examen_preocupacional_visible", "cert_examen_preocupacional_name"),
            ("resolucion_facturacion_electronica_id", "resolucion_facturacion_electronica", "resolucion_facturacion_electronica_visible", "resolucion_facturacion_electronica_name"),
            ("cert_contraloria_id", "cert_contraloria", "cert_contraloria_visible", "cert_contraloria_name"),
            ("cert_procuraduria_id", "cert_procuraduria", "cert_procuraduria_visible", "cert_procuraduria_name"),
            ("cert_policia_judicial_id", "cert_policia_judicial", "cert_policia_judicial_visible", "cert_policia_judicial_name"),
            ("cert_medidas_cautelares_id", "cert_medidas_cautelares", "cert_medidas_cautelares_visible", "cert_medidas_cautelares_name"),
            ("cert_inhabilidades_id", "cert_inhabilidades", "cert_inhabilidades_visible", "cert_inhabilidades_name"),
            ("cert_libreta_militar_id", "cert_libreta_militar", "cert_libreta_militar_visible", "cert_libreta_militar_name"),
            ("resolucion_pensionado_vigente_id", "resolucion_pensionado_vigente", "resolucion_pensionado_vigente_visible", "resolucion_pensionado_vigente_name"),
            ("certificado_bancario_vigente_id", "certificado_bancario_vigente", "certificado_bancario_vigente_visible", "certificado_bancario_vigente_name"),
            ("declaracion_juramentada_id", "declaracion_juramentada", "declaracion_juramentada_visible", "declaracion_juramentada_name"),
            ("propuesta_j_id", "propuesta_j", "propuesta_j_visible", "propuesta_j_name"),
            ("camara_c_actualizada_j_id", "camara_c_actualizada_j", "camara_c_actualizada_j_visible", "camara_c_actualizada_j_name"),
            ("rut_actualizado_j_id", "rut_actualizado_j", "rut_actualizado_j_visible", "rut_actualizado_j_name"),
            ("rup_j_id", "rup_j", "rup_j_visible", "rup_j_name"),
            ("hoja_vida_j_id", "hoja_vida_j", "hoja_vida_j_visible", "hoja_vida_j_name"),
            ("cert_contraloria_j_id", "cert_contraloria_j", "cert_contraloria_j_visible", "cert_contraloria_j_name"),
            ("cert_procuraduria_j_id", "cert_procuraduria_j", "cert_procuraduria_j_visible", "cert_procuraduria_j_name"),
            ("cert_medidas_cautelares_j_id", "cert_medidas_cautelares_j", "cert_medidas_cautelares_j_visible", "cert_medidas_cautelares_j_name"),
            ("cert_inhabilidades_j_id", "cert_inhabilidades_j", "cert_inhabilidades_j_visible", "cert_inhabilidades_j_name"),
            ("resolucion_fact_elect_j_id", "resolucion_fact_elect_j", "resolucion_fact_elect_j_visible", "resolucion_fact_elect_j_name"),
            ("experiencia_laboral_j_id", "experiencia_laboral_j", "experiencia_laboral_j_visible", "experiencia_laboral_j_name"),
            ("cert_pago_paraf_j_id", "cert_pago_paraf_j", "cert_pago_paraf_j_visible", "cert_pago_paraf_j_name"),
            ("planilla_seguridad_social_j_id", "planilla_seguridad_social_j", "planilla_seguridad_social_j_visible", "planilla_seguridad_social_j_name"),
            ("cert_junta_contadores_j_id", "cert_junta_contadores_j", "cert_junta_contadores_j_visible", "cert_junta_contadores_j_name"),
            ("cedula_revisor_fiscal_contador_j_id", "cedula_revisor_fiscal_contador_j", "cedula_revisor_fiscal_contador_j_visible", "cedula_revisor_fiscal_contador_j_name"),
            ("documento_de_identidad_contador_j_id", "documento_de_identidad_contador_j", "documento_de_identidad_contador_j_visible", "documento_de_identidad_contador_j_name"),
            ("Tarjeta_profesional_revisor_fiscal_j_id", "Tarjeta_profesional_revisor_fiscal_j", "Tarjeta_profesional_revisor_fiscal_j_visible", "Tarjeta_profesional_revisor_fiscal_j_name"),
            ("Tarjeta_profesional_contador_j_id", "Tarjeta_profesional_contador_j", "Tarjeta_profesional_contador_j_visible", "Tarjeta_profesional_contador_j_name"),
            ("certificado_bancario_vigente_j_id", "certificado_bancario_vigente_j", "certificado_bancario_vigente_j_visible", "certificado_bancario_vigente_j_name"),
            ("cedula_representante_legal_id", "cedula_representante_legal", "cedula_representante_legal_visible", "cedula_representante_legal_name"),
            ("hoja_v_representante_legal_id", "hoja_v_representante_legal", "hoja_v_representante_legal_visible", "hoja_v_representante_legal_name"),
            ("cert_contraloria_repre_legal_id", "cert_contraloria_repre_legal", "cert_contraloria_repre_legal_visible", "cert_contraloria_repre_legal_name"),
            ("cert_procuraduria_repre_legal_id", "cert_procuraduria_repre_legal", "cert_procuraduria_repre_legal_visible", "cert_procuraduria_repre_legal_name"),
            ("estados_financieros2_id", "estados_financieros2", "estados_financieros2_visible", "estados_financieros2_name"),
        ],
        "contrataciones_fuv": [
            ("documento_identidad_id", "documento_identidad_pn_doc", "documento_identidad_pn_doc_visible", "documento_identidad_pn_doc_fname"),
            ("rut_id", "rut_pn_doc", "rut_pn_doc_visible", "rut_pn_doc_fname"),
            ("hoja_vida_id", "hoja_vida_pn_doc", "hoja_vida_pn_doc_visible", "hoja_vida_pn_doc_fname"),
            ("certificados_estudio_id", "certificados_estudio_doc", "certificados_estudio_doc_visible", "certificados_estudio_doc_fname"),
            ("certificados_laborales_id", "certificados_laborales_doc", "certificados_laborales_doc_visible", "certificados_laborales_doc_fname"),
            ("certificado_arl_id", "certificado_arl_doc", "certificado_arl_doc_visible", "certificado_arl_doc_fname"),
            ("otra_certificado_arl_id", "otra_certificado_arl_doc", "otra_certificado_arl_doc_visible", "otra_certificado_arl_doc_fname"),
            ("certificacion_salud_id", "certificacion_salud_doc", "certificacion_salud_doc_visible", "certificacion_salud_doc_fname"),
            ("certificacion_pension_id", "certificacion_pension_doc", "certificacion_pension_doc_visible", "certificacion_pension_doc_fname"),
            ("certificacion_examen_preocupacional_id", "certificacion_examen_preocupacional_doc", "certificacion_examen_preocupacional_doc_visible", "certificacion_examen_preocupacional_doc_fname"),
            ("su_cdp_id", "su_cdp_doc", "su_cdp_doc_visible", "su_cdp_doc_fname"),
            ("poliza_id", "polizas", "polizas_visible", "polizas_fname"),
            ("tarjeta_profesional_doc_id", "tarjeta_profesional_doc", "tarjeta_profesional_doc_visible", "tarjeta_profesional_doc_fname"),
            ("formato_fuv_ju_doc_id", "formato_fuv_ju_doc", "formato_fuv_ju_doc_visible", "formato_fuv_ju_doc_fname"),
            ("resolucion_facturacion_ju_id", "resolucion_facturacion_ju_doc", "resolucion_facturacion_ju_doc_visible", "resolucion_facturacion_ju_doc_fname"),
            ("bdd_ju_id", "bdd_ju_doc", "bdd_ju_doc_visible", "bdd_ju_doc_fname"),
            ("formato_fuv_doc_id", "formato_fuv_doc", "formato_fuv_doc_visible", "formato_fuv_doc_fname"),
            ("certificacion_contraloria_id", "certificacion_contraloria_doc", "certificacion_contraloria_doc_visible", "certificacion_contraloria_doc_fname"),
            ("certificacion_procuraduria_id", "certificacion_procuraduria_doc", "certificacion_procuraduria_doc_visible", "certificacion_procuraduria_doc_fname"),
            ("certificacion_policia_judicial_id", "certificacion_policia_judicial_doc", "certificacion_policia_judicial_doc_visible", "certificacion_policia_judicial_doc_fname"),
            ("certificacion_medidas_cautelares_id", "certificacion_medidas_cautelares_doc", "certificacion_medidas_cautelares_doc_visible", "certificacion_medidas_cautelares_doc_fname"),
            ("certificacion_inhabilidades_id", "certificacion_inhabilidades_doc", "certificacion_inhabilidades_doc_visible", "certificacion_inhabilidades_doc_fname"),
            ("certificacion_libreta_militar_id", "certificacion_libreta_militar_doc", "certificacion_libreta_militar_doc_visible", "certificacion_libreta_militar_doc_fname"),
            ("resolucion_pensionado_id", "resolucion_pensionado_doc", "resolucion_pensionado_doc_visible", "resolucion_pensionado_doc_fname"),
            ("certificacion_bancaria_id", "certificacion_bancaria_doc", "certificacion_bancaria_doc_visible", "certificacion_bancaria_doc_fname"),
            ("anexo_tributario_id", "anexo_tributario_doc", "anexo_tributario_doc_visible", "anexo_tributario_doc_fname"),
            ("declaracion_juramentada_id", "declaracion_juramentada_doc", "declaracion_juramentada_doc_visible", "declaracion_juramentada_doc_fname"),
            ("contratos_id", "contratos_doc", "contratos_doc_visible", "contratos_doc_fname"),
            ("aprueba_j_h_contrato_firmado_doc_id", "aprueba_j_h_contrato_firmado_doc", "aprueba_j_h_contrato_firmado_doc_visible", "aprueba_j_h_contrato_firmado_doc_fname"),
            ("poliza_pn_id", "polizas2_pn", "polizas_pn_visible", "polizas__pn_fname"),
            ("contratos_firmado_id", "contratos_firmado_doc", "contratos_firmado_doc_visible", "contratos_firmado_doc_fname"),
            ("propuesta_pj_doc_id", "propuesta_pj_doc", "propuesta_pj_doc_visible", "propuesta_pj_doc_fname"),
            ("camara_comercio_doc_id", "camara_comercio_doc", "camara_comercio_doc_visible", "camara_comercio_doc_fname"),
            ("rut_pj_doc_id", "rut_pj_doc", "rut_pj_doc_visible", "rut_pj_doc_fname"),
            ("rup_doc_id", "rup_doc", "rup_doc_visible", "rup_doc_fname"),
            ("hoja_vida_juridica_doc_id", "hoja_vida_juridica_doc", "hoja_vida_juridica_doc_visible", "hoja_vida_juridica_doc_fname"),
            ("cert_contraloria_doc_id", "cert_contraloria_doc", "cert_contraloria_doc_visible", "cert_contraloria_doc_fname"),
            ("cert_procuraduria_doc_id", "cert_procuraduria_doc", "cert_procuraduria_doc_visible", "cert_procuraduria_doc_fname"),
            ("cedula_rep_legal_doc_id", "cedula_rep_legal_doc", "cedula_rep_legal_doc_visible", "cedula_rep_legal_doc_fname"),
            ("hoja_vida_rep_legal_doc_id", "hoja_vida_rep_legal_doc", "hoja_vida_rep_legal_doc_visible", "hoja_vida_rep_legal_doc_fname"),
            ("cert_contraloria_rep_legal_doc_id", "cert_contraloria_rep_legal_doc", "cert_contraloria_rep_legal_doc_visible", "cert_contraloria_rep_legal_doc_fname"),
            ("cert_procuraduria_rep_legal_doc_id", "aprueba_j_h_contraloria_rep_lega_doc", "aprueba_j_h_contraloria_rep_lega_doc_visible", "aprueba_j_h_contraloria_rep_lega_doc_fname"),
            ("cdp_juridico_doc_id", "cdp_juridico_doc", "cdp_juridico_doc_visible", "cdp_doc_fname"),
            ("estados_financieros_juridico_doc_id", "estados_financieros_juridico_doc", "estados_financieros_juridico_doc_visible", "estados_financieros_doc_fname"),
            ("cert_policia_judicial_doc_id", "cert_policia_judicial_doc", "cert_policia_judicial_doc_visible", "cert_policia_judicial_doc_fname"),
            ("cert_inhabilidades_doc_id", "cert_inhabilidades_doc", "cert_inhabilidades_doc_visible", "cert_inhabilidades_doc_fname"),
            ("resolucion_facturacion_doc_id", "resolucion_facturacion_doc", "resolucion_facturacion_doc_visible", "resolucion_facturacion_doc_fname"),
            ("experiencia_laboral_doc_id", "experiencia_laboral_doc", "experiencia_laboral_doc_visible", "experiencia_laboral_doc_fname"),
            ("certificado_pago_parafiscales_doc_id", "certificado_pago_parafiscales_doc", "certificado_pago_parafiscales_doc_visible", "certificado_pago_parafiscales_doc_fname"),
            ("planilla_seguridad_social_doc_id", "planilla_seguridad_social_doc", "planilla_seguridad_social_doc_visible", "planilla_seguridad_social_doc_fname"),
            ("certificado_junta_contadores_doc_id", "certificado_junta_contadores_doc", "certificado_junta_contadores_doc_visible", "certificado_junta_contadores_doc_fname"),
            ("cedula_revisor_fiscal_doc_id", "cedula_revisor_fiscal_doc", "cedula_revisor_fiscal_doc_visible", "cedula_revisor_fiscal_doc_fname"),
            ("Documento_de_identidad_contador_doc_id", "Documento_de_identidad_contador", "Documento_de_identidad_contador_doc_visible", "Documento_de_identidad_contador_doc_fname"),
            ("Tarjeta_profesional_revisor_fiscal_doc_id", "Tarjeta_profesional_revisor_fiscal_doc", "Tarjeta_profesional_revisor_fiscal_doc_visible", "Tarjeta_profesional_revisor_fiscal_doc_fname"),
            ("Tarjeta_profesional_contador_doc_id", "Tarjeta_profesional_contador_doc", "Tarjeta_profesional_contador_doc_visible", "Tarjeta_profesional_contador_fname"),
            ("certificado_bancario_doc_id", "certificado_bancario_doc", "certificado_bancario_doc_visible", "certificado_bancario_doc_fname"),
            ("anexo_tributario_juridico_doc_id", "anexo_tributario_juridico_doc", "anexo_tributario_juridico_doc_visible", "anexo_tributario_juridico_doc_fname"),
            ("aprueba_j_h_contrato_doc_id", "aprueba_j_h_contrato_doc", "aprueba_j_h_contrato_doc_visible", "aprueba_j_h_contrato_doc_fname"),
            ("aprueba_j_h_bdd_doc_id", "aprueba_j_h_bdd_doc", "aprueba_j_h_bdd_doc_visible", "aprueba_j_h_bdd_doc_fname"),
        ],
        "carpeta_principal": [
            ("carta_invitacion_id", "carta_invitacion", "carta_invitacion_visible", "carta_invitacion_char"),
            ("cdp_id", "cdp", "cdp_visible", "cdp_char"),
            ("estudios_previos_id", "estudios_previos", "estudios_previos_visible", "estudios_previos_char"),
            ("propuesta_id", "propuesta", "propuesta_visible", "propuesta_char"),
            ("oficio_remisorio_propuesta_id", "oficio_remisorio_propuesta", "oficio_remisorio_propuesta_visible", "oficio_remisorio_propuesta_char"),
            ("resolucion_contratacion_id", "resolucion_contratacion", "resolucion_contratacion_visible", "resolucion_contratacion_char"),
            ("contrato_convenio_componente_id", "contrato_convenio_componente", "contrato_convenio_componente_visible", "contrato_convenio_componente_char"),
            ("polizas_id", "polizas", "polizas_visible", "polizas_char"),
            ("aceptacion_polizas_id", "aceptacion_polizas", "aceptacion_polizas_visible", "aceptacion_polizas_char"),
            ("acta_inicio_id", "acta_inicio", "acta_inicio_visible", "acta_inicio_char"),
            ("rpc_id", "rpc", "rpc_visible", "rpc_char"),
            ("otrosi_modificatorios_id", "otrosi_modificatorios", "otrosi_modificatorios_visible", "otrosi_modificatorios_char"),
            ("polizas_modificatorios_id", "polizas_modificatorios", "polizas_modificatorios_visible", "polizas_modificatorios_char"),
            ("designacion_supervisor_id", "designacion_supervisor", "designacion_supervisor_visible", "designacion_supervisor_char"),
            ("facturacion_id", "facturacion", "facturacion_visible", "facturacion_char"),
            ("actas_seguimiento_id", "actas_seguimiento", "actas_seguimiento_visible", "actas_seguimiento_char"),
            ("informes_avance_id", "informes_avance", "informes_avance_visible", "informes_avance_char"),
            ("actas_supervisor_parciales_id", "actas_supervisor_parciales", "actas_supervisor_parciales_visible", "actas_supervisor_parciales_char"),
            ("informe_final_id", "informe_final", "informe_final_visible", "informe_final_char"),
            ("acta_final_supervisor_id", "acta_final_supervisor", "acta_final_supervisor_visible", "acta_final_supervisor_char"),
            ("acta_terminacion_id", "acta_terminacion", "acta_terminacion_visible", "acta_terminacion_char"),
            ("acta_final_pago_id", "acta_final_pago", "acta_final_pago_visible", "acta_final_pago_char"),
            ("acta_liquidacion_id", "acta_liquidacion", "acta_liquidacion_visible", "acta_liquidacion_char"),
            ("certificado_cumplimiento_id", "certificado_cumplimiento", "certificado_cumplimiento_visible", "certificado_cumplimiento_char"),
            ("matriz_de_riesgos_id", "matriz_de_riesgos", "matriz_de_riesgos_visible", "matriz_de_riesgos_char"),
            ("informe_de_invertentoria_id", "informe_de_invertentoria", "informe_de_invertentoria_visible", "informe_de_invertentoria_char"),
            ("m_g_a_id", "m_g_a", "m_g_a_visible", "m_g_a_char"),
        ],
        "cierre": [
            ("archivo_acta_liquidacion_id", "archivo_acta_liquidacion", "archivo_acta_liquidacion_visible", "archivo_acta_liquidacion_name"),
            ("paz_y_salvo_id", "paz_y_salvo", "paz_y_salvo_visible", "paz_y_salvo_name"),
            ("acta_liquidacion_firmada_id", "acta_liquidacion_firmada", "acta_liquidacion_firmada_visible", "acta_liquidacion_firmada_name"),
        ],
        "otro_si": [
            ("cdp_juridico_doc_id", "cdp_juridico_doc", "cdp_juridico_doc_visible", "cdp_doc_juridico_fname"),
            ("contratos_otro_juridico_id", "contratos_otro_jurdico_doc", "contratos_otro_juridico_doc_visible", "contratos_otro_doc_juridico_fname"),
            ("poliza_id", "polizas", "polizas_visible", "polizas_fname"),
            ("contratos_firmado_juridico_id", "contratos_firmado_juridico_doc", "contratos_firmado_juridico_doc_visible", "contratos_firmado_juridico_doc_fname"),
            ("rut_doc_fname", "rut_juridico_doc", "rut_juridico_doc_visible", "rut_doc_fname"),
            ("aprueba_j_h_otro_doc_fname", "aprueba_j_h_otro_doc", "aprueba_j_h_otro_doc_visible", "aprueba_j_h_otro_doc_fname"),
            ("propuesta_doc_id", "propuesta_doc", "propuesta_doc_visible", "propuesta_doc_fname"),
            ("su_cdp_id", "su_cdp_doc", "su_cdp_doc_visible", "su_cdp_doc_fname"),
            ("su_contrato_natu_doc_fname", "su_contrato_natu_doc", "su_contrato_natu_doc_visible", "su_contrato_natu_doc_fname"),
            ("su_rut_doc_fname", "su_rut_doc", "su_rut_doc_visible", "su_rut_doc_fname"),
            ("su_arl_doc_id", "su_arl_doc", "su_arl_doc_visible", "su_arl_doc_fname"),
            ("contratos_otro_id", "contratos_otro_doc", "contratos_otro_doc_visible", "contratos_otro_doc_fname"),
            ("contratos_firmado_id", "contratos_firmado_natu_doc", "contratos_firmado_natu_doc_visible", "contratos_firmado_natu_doc_fname"),
            ("contraloria_otro_doc_fname", "contraloria_otro_doc", "contraloria_otro_doc_visible", "contraloria_otro_doc_fname"),
        ],
        'anticipos': [
                ('rut_pn_document_id', 'rut_pn_document', 'rut_pn_document_visible', 'rut_pn_filename'),
                ('doc_identidad_document_id', 'doc_identidad_document', 'doc_identidad_document_visible', 'doc_identidad_filename'),
                ('cert_bancaria_pn_document_id', 'cert_bancaria_pn_document', 'cert_bancaria_pn_document_visible', 'cert_bancaria_pn_filename'),
                ('rpc_document_id', 'cert_bancaria_pn_document', 'rpc_document_visible', 'cert_bancaria_pn_filename'),
                ('at_document_id', 'at_document', 'at_document_visible', 'at_filename'),
                ('comprobante_pago_document_id', 'comprobante_pago_document', 'comprobante_pago_document_visible', 'comprobante_pago_filename'),
                ('soporte_de_pago_document_id', 'soporte_de_pago', 'soporte_de_pago_document_visible', 'soporte_de_pago_filename'),
                ('rut_pj_document_id', 'rut_pj_document', 'rut_pj_document_visible', 'rut_pj_filename'),
                ('camara_comercio_document_id', 'camara_comercio_document', 'camara_comercio_document_visible', 'camara_comercio_filename'),
                ('cert_bancaria_pj_document_id', 'cert_bancaria_pj_document', 'cert_bancaria_pj_document_visible', 'cert_bancaria_pj_filename'),
                ('rpc_pj_document_id', 'rpc_pj_document', 'rpc_pj_document_visible', 'rpc_pj_filename'),
                ('at_pj_document_id', 'at_pj_document', 'at_pj_document_visible', 'at_pj_filename'),
                ('comprobante_pago_pj_document_id', 'comprobante_pago_pj_document', 'comprobante_pago_pj_document_visible', 'comprobante_pago_pj_filename'),
                ('soporte_de_pago_pj_document_id', 'soporte_de_pago_pj', 'soporte_de_pago_pj_document_visible', 'soporte_de_pago_pj_filename'),
                ('compromiso_legalizacion_pn_document_id', 'compromiso_legalizacion_pn_document', 'compromiso_legalizacion_pn_document_visible', 'compromiso_legalizacion_pn_filename'),
                ('cuenta_cobro_pj_document_id', 'cuenta_cobro_pj_document', 'cuenta_cobro_pj_document_visible', 'cuenta_cobro_pj_filename'),
        ],
        'contrataciones_fuv.close.wizard':[
            ('archivo_acta_id', 'archivo_acta', 'archivo_acta_visible', 'archivo_acta_nombre'),
        ],
        'viaticos': [
                ('rut_pn_document_id', 'rut_pn_document', 'rut_pn_document_visible', 'rut_pn_filename'),
                ('doc_identidad_document_id', 'doc_identidad_document', 'doc_identidad_document_visible', 'doc_identidad_filename'),
                ('cert_bancaria_pn_document_id', 'cert_bancaria_pn_document', 'cert_bancaria_pn_document_visible', 'cert_bancaria_pn_filename'),
                ('orden_pago_document_id', 'orden_pago_document', 'orden_pago_document_visible', 'orden_pago_filename'),
                ('comprobante_pago_document_id', 'comprobante_pago_document', 'comprobante_pago_document_visible', 'comprobante_pago_filename'),
                ('soporte_de_pago_document_id', 'soporte_de_pago', 'soporte_de_pago_document_visible', 'soporte_de_pago_filename'),
                ('documento_de_causacion_id', 'documento_de_causacion', 'documento_de_causacion_visible', 'documento_de_causacion_filename'),
                ('rut_pj_document_id', 'rut_pj_document', 'rut_pj_document_visible', 'rut_pj_filename'),
                ('camara_comercio_document_id', 'camara_comercio_document', 'camara_comercio_document_visible', 'camara_comercio_filename'),
                ('cert_bancaria_pj_document_id', 'cert_bancaria_pj_document', 'cert_bancaria_pj_document_visible', 'cert_bancaria_pj_filename'),
                ('orden_pago_pj_document_id', 'orden_pago_pj_document', 'orden_pago_pj_document_visible', 'orden_pago_pj_filename'),
                ('comprobante_pago_pj_document_id', 'comprobante_pago_pj_document', 'comprobante_pago_pj_document_visible', 'comprobante_pago_pj_filename'),
                ('soporte_de_pago_pj_document_id', 'soporte_de_pago_pj', 'soporte_de_pago_pj_document_visible', 'soporte_de_pago_pj_filename'),
                ('documento_de_causacion_pj_id', 'documento_de_causacion_pj', 'documento_de_causacion_pj_visible', 'documento_de_causacion_pj_filename'),
                ('rpc_legalizacion_document_id', 'rpc_legalizacion', 'rpc_legalizacion_document_visible', 'rpc_legalizacion_filename'),
                ('rpc_pj_legalizacion_document_id', 'rpc_pj_legalizacion', 'rpc_pj_legalizacion_document_visible', 'rpc_pj_legalizacion_filename'),
        ],
        'viaticos_linea': [
            ('factura_id', 'factura', 'factura_visible', 'factura_filename'),
        ]
    }

    # ==========================================================================
    # LÓGICA CORE: OPTIMIZACIÓN EXTREMA PARA CASCADA 🚀
    # ==========================================================================

    def unlink(self):
        """
        Borrado optimizado:

        - Permite eliminar el registro de Odoo.
        - Si el registro corresponde a una certificación conjunta generada
        por el sistema, NO elimina su archivo de Google Drive.
        - Los demás documentos mantienen el comportamiento normal:
        eliminación del archivo de Google Drive + eliminación del registro.
        - Mantiene la validación de documentos en solo lectura.
        """

        # ---------------------------------------------------------
        # 1. Validaciones de permisos
        # ---------------------------------------------------------
        self.check_access_rights('unlink')
        self.check_access_rule('unlink')

        es_soporte = self.env.user.has_group('Matriz.group_soporte')

        if not self.env.su and not es_soporte and any(
            record.solo_lectura_cuenta_finalizada for record in self
        ):
            raise exceptions.AccessError(_(
                "Este documento está en modo de solo lectura y no puede eliminarse."
            ))

        # ---------------------------------------------------------
        # 2. Separar documentos protegidos de documentos normales
        # ---------------------------------------------------------
        registros_protegidos = self.filtered(
            lambda r: r.es_certificacion_conjunta_generada
        )

        registros_normales = self - registros_protegidos

        if registros_protegidos:
            _logger.info(
                ">>> DRIVE: %s certificación(es) conjunta(s) generada(s) "
                "serán eliminadas del registro de Odoo, pero se conservarán "
                "sus archivos en Google Drive.",
                len(registros_protegidos)
            )

        # ---------------------------------------------------------
        # 3. SOLO los documentos normales se eliminan de Google Drive
        # ---------------------------------------------------------
        records_with_file = registros_normales.filtered(
            lambda r: r.file_id
        )

        google_ids_to_delete = records_with_file.mapped('file_id')
        files_names = records_with_file.mapped('file_name')

        total_files = len(google_ids_to_delete)

        # ---------------------------------------------------------
        # 4. Notificación + borrado de Google Drive
        #    SOLO para documentos normales
        # ---------------------------------------------------------
        if total_files > 0:

            self._send_ui_notification(
                total_files,
                files_names
            )

            _logger.info(
                ">>> DRIVE BG: Iniciando hilo masivo para %s archivos...",
                total_files
            )

            thread = threading.Thread(
                target=self._borrar_en_google_thread,
                args=(google_ids_to_delete,)
            )

            thread.start()

        # ---------------------------------------------------------
        # 5. Limpieza de padres
        # ---------------------------------------------------------
        #
        # Para certificaciones generadas NO hacemos limpieza externa
        # relacionada con el archivo de Drive, porque queremos conservarlo.
        #
        # Para documentos normales se mantiene el comportamiento anterior.
        #
        if len(registros_normales) <= 10:

            for record in registros_normales:

                if record.exists():
                    self._limpiar_padre(record)

        elif len(registros_normales) > 10:

            _logger.info(
                ">>> DRIVE OPTIMIZACIÓN: Saltando limpieza de padres "
                "para %s registros (Modo Cascada).",
                len(registros_normales)
            )

        # ---------------------------------------------------------
        # 6. Finalmente eliminar TODOS los registros de Odoo
        # ---------------------------------------------------------
        return super(Drive, self).unlink()

    def _send_ui_notification(self, qty, names):
        """Envía la señal al JS para mostrar el cuadro bonito"""
        try:
            display_names = ", ".join(names[:2])  # Solo los 2 primeros
            detalle = f"{display_names} y {qty - 2} más" if qty > 2 else display_names

            payload = {
                "title": f"Eliminando {qty} archivos 🗑️",
                "message": f"Optimizando borrado en segundo plano",
                "type": "delete",
            }
            # Enviar al bus del usuario actual
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id, "drive_custom_delete_alert", payload
            )
        except Exception as e:
            _logger.warning(f"Error enviando señal UI: {e}")

    def _borrar_en_google_thread(self, file_ids):
        """Hilo silencioso que habla con Google"""
        try:
            # Importa o usa tu clase Auth aquí
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            if not drive:
                return

            for f_id in file_ids:
                try:
                    g_file = drive.CreateFile({"id": f_id})
                    g_file.Trash()
                    # Log menos ruidoso para masivos
                    _logger.info(f" >>> DRIVE BG: Borrado {f_id}")
                except Exception as e:
                    _logger.warning(f" >>> DRIVE BG Error {f_id}: {e}")

        except Exception as e:
            _logger.error(f" >>> DRIVE BG CRITICAL: {e}")

    def _limpiar_padre(self, record):
        """
        Limpieza Nuclear 🚀 (Versión a prueba de fallos).
        Usa SQL puro + Savepoints para evitar InFailedSqlTransaction.
        """
        record_id = record.id

        def _reset_target_document(target, fields_tuple, source_label):
            id_field, bin_field, vis_field, name_field = fields_tuple[:4]
            boolean_field = fields_tuple[4] if len(fields_tuple) > 4 else None
            if not target or not target.exists():
                return False
            missing = [
                field_name for field_name in (id_field, bin_field, vis_field, name_field, boolean_field)
                if field_name
                if field_name not in target._fields
            ]
            if missing:
                return False
            target = target.sudo()
            if target._name == 'acta_entrega_documentos':
                # La eliminación autorizada desde Drive debe limpiar la
                # relación del acta aunque esta ya esté en una etapa bloqueada.
                # El contexto se usa solo durante esta limpieza interna.
                target = target.with_context(allow_person_document_write=True)
            try:
                target.message_post(body=f"🗑️ Documento eliminado: <b>{record.file_name}</b>")
            except Exception as msg_err:
                _logger.debug("No se pudo publicar mensaje de borrado en %s: %s", source_label, msg_err)
            try:
                with self.env.cr.savepoint():
                    reset_vals = {id_field: False}
                    if bin_field:
                        reset_vals[bin_field] = False
                    if name_field:
                        reset_vals[name_field] = False
                    if vis_field:
                        reset_vals[vis_field] = True
                    if boolean_field:
                        reset_vals[boolean_field] = False
                    target.write(reset_vals)
                _logger.info(
                    " >>> PADRE LIMPIADO DIRECTO: %s (ID: %s) por campo %s",
                    source_label, target.id, id_field
                )
                return True
            except Exception as clean_err:
                _logger.debug(
                    "No se pudo limpiar %s.%s para drive %s: %s",
                    source_label, id_field, record.id, clean_err
                )
                return False

        # Primero usar las relaciones directas del archivo. Esto evita que el
        # borrado dependa del rol o de que el campo sea localizable por SQL.
        direct_parents = [
            record.contratacion_fuv_documentos_id,
            record.otrosi_documentos_id,
            record.bienes_id,
            record.servicios_id,
            record.carpeta_principal_id,
            record.carpeta_principal_cierre_id,
            record.contratistas_provedores,
            record.stage4_id.contratistas_provedores,
            record.anticipos_id,
            record.viaticos_id,
            record.viaticos_otros_id,
            record.viaticos_linea_id,
        ]
        for parent in direct_parents:
            if not parent or not parent.exists():
                continue
            fields_list = self.RELACIONES_A_ACTUALIZAR.get(parent._name, [])
            for fields_tuple in fields_list:
                id_field = fields_tuple[0]
                if id_field not in parent._fields:
                    continue
                linked = parent[id_field]
                if linked and linked.id == record_id:
                    if _reset_target_document(parent, fields_tuple, parent._name):
                        return

        for model_name, fields_list in self.RELACIONES_A_ACTUALIZAR.items():
            if model_name not in self.env: continue

            model_obj = self.env[model_name].sudo()
            table_name = model_obj._table  # Nombre de la tabla real

            for fields_tuple in fields_list:
                id_field, bin_field, vis_field, name_field = fields_tuple[:4]
                # Si Odoo ni siquiera conoce el campo, lo saltamos
                if id_field not in model_obj._fields:
                    continue

                query = f"SELECT id FROM {table_name} WHERE {id_field} = %s LIMIT 1"

                try:
                    # 🛡️ EL SECRETO: Esto evita que un error SQL bloquee toda la base de datos
                    with self.env.cr.savepoint():
                        self.env.cr.execute(query, (record_id,))
                        result = self.env.cr.fetchone()

                        if result:
                            # ¡Encontramos el registro en la BD!
                            target_id = result[0]
                            target = model_obj.browse(target_id).sudo()

                            if _reset_target_document(target, fields_tuple, model_name):
                                return # Salimos porque ya lo resolvimos

                except Exception as e:
                    # Si el campo no es una columna real en BD, el savepoint absorbe el golpe
                    # y el ciclo continúa sin romper el sistema.
                    _logger.debug(f"Saltando búsqueda SQL para {id_field} en {table_name}: no es una columna de BD.")

        _logger.warning(f" >>> ALERTA: No se encontró ningún padre en BD para limpiar el archivo {record_id}")

    # ==========================================================================

    def action_share_entry(self):
        self.ensure_one()
        return self.drive_share()

    def action_download_pdf_entry(self):
        self.ensure_one()
        return self.download_from_drive()

    def action_download_word_entry(self):
        self.ensure_one()
        return self.download_from_drive_as_word()

    def action_download_original_entry(self):
        self.ensure_one()
        return self.download_from_drive_original()

    def action_delete_entry(self):
        self.ensure_one()
        self.unlink()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    @api.model
    def js_get_delete_info(self, ids):
        records = self.browse(ids)
        return [{"id": r.id, "name": r.file_name or "Sin nombre"} for r in records]

    @api.model
    def js_delete_individual(self, record_id):
        # 4. Inyectamos .sudo() aquí para que los roles Jurídico y Financiero
        # puedan ejecutar el borrado sin que Odoo les lance un Access Error oculto.
        record = self.browse(record_id).sudo()
        if record.exists():
            return record.unlink()
        return True

    def upload_to_drive(self, new_folder_id=None):
        """Devuelve True si el archivo quedó subido/actualizado en Drive,
        False en cualquier fallo (auth, archivo, red, reintentos agotados).
        Todas las excepciones se siguen atrapando internamente (no se
        relanzan, por compatibilidad con los callers existentes que no
        revisan el retorno) — el booleano es aditivo, para que un caller
        nuevo (ej. drive_firma_solicitud.procesar_firma()) SÍ pueda
        detectar el fallo en vez de asumir éxito."""
        self.ensure_one()

        try:
            folder_id = new_folder_id or self.folder_id or "root"

            if not self.file_data:
                _logger.warning("No hay datos de archivo para procesar.")
                return False
            if not self.file_name:
                _logger.error("El nombre del archivo no está definido.")
                return False

            # Auth Drive
            try:
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()
            except Exception as auth_error:
                _logger.error("Error al autenticar con Google Drive: %s", auth_error)
                return False

            # ── Auto-numeración de archivos ──
            # Si la carpeta destino tiene prefijo numérico (ej. "4.1 Informe mensual")
            # y el archivo aún no empieza con dígito, se le asigna "4.1.1", "4.1.2", etc.
            if not re.match(r"^\d", self.file_name or ""):
                # Obtener nombre del padre: stage3_id (carpeta_principal) o stage2_id (informes/comunicaciones)
                _parent_name = None
                _fallback_folder_id = None
                if self.stage3_id and self.stage3_id.folder_name:
                    _parent_name = self.stage3_id.folder_name
                    _fallback_folder_id = self.stage3_id.folder_id or None
                elif self.stage2_id and self.stage2_id.folder_name:
                    _parent_name = self.stage2_id.folder_name
                    _fallback_folder_id = self.stage2_id.folder_id2 or None

                # Folder ID real: preferir el del registro, si no hay usar el del stage
                _num_folder_id = folder_id
                if not _num_folder_id or _num_folder_id == "root":
                    _num_folder_id = _fallback_folder_id

                if _parent_name and _num_folder_id and _num_folder_id != "root":
                    from .drive_soporte import (
                        _extract_numeric_prefix,
                        _get_next_prefix_for_folder,
                    )

                    _par_prefix = _extract_numeric_prefix(_parent_name)
                    if _par_prefix:
                        _num_prefix = _get_next_prefix_for_folder(
                            drive, _num_folder_id, parent_name=_parent_name
                        )
                        _new_fname = f"{_num_prefix} {self.file_name}"
                        self.with_context(skip_drive_sync=True).write(
                            {"file_name": _new_fname}
                        )
                        self.invalidate_recordset(["file_name"])
            # ── fin auto-numeración ──

            # Buscar si ya existe
            safe_file_name = (self.file_name or "").replace("\\", "\\\\").replace("'", "\\'")
            query = f"title='{safe_file_name}' and '{folder_id}' in parents and trashed=false"
            try:
                existing_files = drive.ListFile({"q": query}).GetList()
            except Exception as query_error:
                _logger.error(
                    "Error al consultar archivos existentes en Google Drive: %s",
                    query_error,
                )
                return False

            if existing_files:
                gdrive_file = existing_files[0]
                _logger.info(
                    "El archivo ya existe en Google Drive: %s (ID: %s)",
                    self.file_name,
                    gdrive_file.get("id"),
                )
                temp_file_path = None
                try:
                    file_content = base64.b64decode(self.file_data)
                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=os.path.splitext(self.file_name)[1] or ".bin",
                    ) as temp_file:
                        temp_file.write(file_content)
                        temp_file_path = temp_file.name

                    gdrive_file.SetContentFile(temp_file_path)
                    gdrive_file.Upload()
                    _logger.info(
                        "Contenido del archivo existente actualizado en Google Drive: %s (ID: %s)",
                        self.file_name,
                        gdrive_file.get("id"),
                    )
                finally:
                    if temp_file_path and os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
            else:
                # Crear temp y subir
                try:
                    file_content = base64.b64decode(self.file_data)

                    # Directorio temporal del sistema (tempfile), no una
                    # carpeta dentro del propio addon — evita la fragilidad
                    # de permisos de 'models/temp/' (uid del proceso de Odoo
                    # vs. dueño del código montado). Mismo patrón que ya usa
                    # la rama de "actualizar archivo existente" arriba.
                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=os.path.splitext(self.file_name)[1] or ".bin",
                    ) as temp_file:
                        temp_file.write(file_content)
                        temp_file_path = temp_file.name

                    gdrive_file = drive.CreateFile(
                        {"title": self.file_name, "parents": [{"id": folder_id}]}
                    )
                    gdrive_file.SetContentFile(temp_file_path)

                    for attempt in range(3):
                        try:
                            gdrive_file.Upload()
                            break
                        except Exception as upload_error:
                            _logger.warning(
                                "Intento %s fallido al subir el archivo: %s",
                                attempt + 1,
                                upload_error,
                            )
                            time.sleep(2)
                    else:
                        _logger.error(
                            "No se pudo subir el archivo después de varios intentos."
                        )
                        return False

                    gdrive_file.InsertPermission(
                        {"type": "anyone", "value": "anyone", "role": "reader"}
                    )

                    try:
                        os.remove(temp_file_path)
                    except Exception:
                        pass

                except Exception as file_error:
                    _logger.error(
                        "Error al procesar el archivo temporal: %s", file_error
                    )
                    return False

            # Preparar vals (SIN asignaciones directas)
            preview_url = gdrive_file.get("embedLink") or gdrive_file.get(
                "webContentLink"
            )
            edit_url = gdrive_file.get("alternateLink")
            google_drive_id = gdrive_file.get("id")

            vals = {
                "preview_url": preview_url,
                "edit_url": edit_url,
                "file_id": google_drive_id,  # id real de Google
                "drive_file_id": google_drive_id,
                "preview_html": (
                    f'<iframe src="{preview_url}" width="100%" height="600px" loading="lazy" frameborder="0" allowfullscreen></iframe>'
                    if preview_url
                    else False
                ),
                "edit_html": (
                    f'<iframe src="{edit_url}" width="100%" height="600px" loading="lazy" frameborder="0"></iframe>'
                    if edit_url
                    else False
                ),
                # Limpieza local: TODO en el MISMO write (una sola vez)
                "file_data": False,
                "file_data_visible": False,
                "actualizar_visible": False,
                "actualizar_visible2": False,
            }

            # ✅ Un solo write. Context para evitar cualquier “sync” extra que tengas colgado.
            self.with_context(skip_drive_sync=True, skip_integrity=True).write(vals)

            _logger.info("Archivo subido exitosamente. Edit URL: %s", edit_url)
            self.message_post(
                body="Archivo subido a Google Drive exitosamente y eliminado localmente."
            )
            return True

        except Exception as e:
            _logger.error("Error inesperado al subir el archivo a Google Drive: %s", e)
            return False

    def move_in_drive(self, new_folder_id):
        try:
            # Obtener instancia autenticada de Google Drive (ya manejada)
            drive_auth = GoogleDriveAuth.get_instance()
            drive_service = drive_auth.get_drive()

            # Verificar que el ID del archivo y la carpeta sean válidos
            if not self.drive_file_id:
                print("ID del archivo no especificado.")
                return

            # Obtener información sobre el archivo actual
            file = (
                drive_service.files()
                .get(fileId=self.drive_file_id, fields="parents")
                .execute()
            )

            # Extraer la carpeta actual del archivo
            current_parents = file.get("parents", [])
            if not current_parents:
                print("El archivo no tiene carpetas actuales.")
                return

            current_folder_id = current_parents[0]

            # Mover el archivo si la carpeta actual no es la misma que la nueva
            if current_folder_id != new_folder_id:
                # Actualizar el archivo para moverlo
                drive_service.files().update(
                    fileId=self.drive_file_id,
                    addParents=new_folder_id,
                    removeParents=current_folder_id,
                    fields="id, parents",
                ).execute()

                self.folder_id = new_folder_id
                self.message_post(
                    body="Archivo movido a nueva carpeta en Google Drive."
                )
            else:
                print("El archivo ya está en la carpeta especificada.")

        except Exception as e:
            print(f"Error al mover el archivo en Google Drive: {e}")

    def _drive_temp_path(self, suffix=""):
        """Ruta temporal única para descargas/exportaciones de Drive."""
        return os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}{suffix}")

    def _drive_safe_remove_temp(self, temp_path):
        """Elimina un temporal sin fallar si ya no existe."""
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                _logger.warning(
                    "No se pudo eliminar el archivo temporal de Drive: %s", temp_path
                )

    def _drive_get_content_file(self, gdrive_file, temp_path, mimetype=None):
        """Descarga/exporta desde Drive y valida que el temporal haya quedado creado."""
        self._drive_safe_remove_temp(temp_path)
        if mimetype:
            gdrive_file.GetContentFile(temp_path, mimetype=mimetype)
        else:
            gdrive_file.GetContentFile(temp_path)
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
            raise UserError(
                "Google Drive no entregó el archivo solicitado. "
                "Intente nuevamente y verifique que el archivo exista, no esté en papelera y tenga permisos de lectura."
            )
        return temp_path

    def _drive_read_temp_as_base64(self, temp_path):
        """Lee un temporal descargado desde Drive y siempre intenta limpiarlo."""
        try:
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 0:
                raise UserError(
                    "Google Drive no entregó el archivo solicitado. "
                    "Intente nuevamente y verifique que el archivo exista, no esté en papelera y tenga permisos de lectura."
                )
            with open(temp_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        finally:
            self._drive_safe_remove_temp(temp_path)

    def _drive_service_v3(self, drive_auth=None):
        """Servicio Drive v3 con credenciales vigentes."""
        drive_auth = drive_auth or GoogleDriveAuth.get_instance()
        gauth = drive_auth.get_gauth()
        if (
            hasattr(gauth.credentials, "access_token_expired")
            and gauth.credentials.access_token_expired
        ):
            gauth.Refresh()
        return build(
            "drive", "v3", credentials=gauth.credentials, cache_discovery=False
        )

    def _drive_download_bytes(
        self, file_id, mime_type=None, export_mime=None, drive_auth=None,
        max_bytes=None,
    ):
        """Descarga/exporta un archivo de Drive directamente en memoria, sin depender de /tmp."""
        svc = self._drive_service_v3(drive_auth)
        if export_mime or (mime_type or "").startswith("application/vnd.google-apps."):
            request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
        else:
            request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)

        buffer = BytesIO()
        downloader = MediaIoBaseDownload(
            buffer, request, chunksize=min(max_bytes, 1024 * 1024)
        ) if max_bytes else MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
            if max_bytes and buffer.tell() > max_bytes:
                raise UserError("El documento supera el tamaño máximo permitido para firma.")

        data = buffer.getvalue()
        if not data:
            raise UserError(
                "Google Drive no entregó contenido para este archivo. "
                "Verifique que exista, no esté en papelera y tenga permisos de lectura."
            )
        return data

    def _drive_bytes_to_base64(self, data):
        if not data:
            raise UserError(
                "Google Drive no entregó contenido para este archivo. "
                "Verifique que exista, no esté en papelera y tenga permisos de lectura."
            )
        return base64.b64encode(data).decode("utf-8")

    def download_from_drive(self):
        """Descarga el archivo como PDF. Soporta Google Docs, DOCX subidos y PDFs."""
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        temp_path = None
        try:
            target_id = self.file_id or self.drive_file_id
            drive_auth = GoogleDriveAuth.get_instance()
            drive_obj = drive_auth.get_drive()

            if not target_id:
                search_name = self.file_name or self.name or "Archivo Sin Nombre"
                file_list = drive_obj.ListFile({'q': f"title = '{search_name}' and trashed = false"}).GetList()
                if file_list:
                    target_id = file_list[0]['id']
                    self.sudo().write({'file_id': target_id, 'drive_file_id': target_id})
                else:
                    raise UserError(f"No se encontró el archivo '{search_name}' en Drive.")

            gdrive_file = drive_obj.CreateFile({"id": target_id})
            gdrive_file.FetchMetadata()

            mime_type = gdrive_file['mimeType']
            original_title = gdrive_file['title']
            odoo_name = os.path.splitext(self.file_name or self.name or original_title)[0]

            _WORD_MIMES = {
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/msword',
            }

            final_name = f"{odoo_name}.pdf"

            temp_path = self._drive_temp_path()
            export_mime = 'application/pdf'

            if mime_type == 'application/vnd.google-apps.document':
                # Google Doc nativo → exportar directamente como PDF
                self._drive_get_content_file(gdrive_file, temp_path, mimetype='application/pdf')

            elif mime_type == 'application/vnd.google-apps.spreadsheet':
                # El botón PDF conserva el formato solicitado también para Sheets.
                self._drive_get_content_file(gdrive_file, temp_path, mimetype=export_mime)

            elif mime_type == 'application/vnd.google-apps.presentation':
                self._drive_get_content_file(gdrive_file, temp_path, mimetype=export_mime)

            elif mime_type in _WORD_MIMES:
                # DOCX/DOC subido a Drive → copiar como Google Doc en la nube y exportar como PDF
                gauth = drive_auth.get_gauth()
                if hasattr(gauth.credentials, 'access_token_expired') and gauth.credentials.access_token_expired:
                    gauth.Refresh()
                svc = build('drive', 'v3', credentials=gauth.credentials, cache_discovery=False)
                copied = svc.files().copy(
                    fileId=target_id,
                    body={'name': '_tmp_pdf_export_', 'mimeType': 'application/vnd.google-apps.document'},
                    supportsAllDrives=True,
                ).execute()
                tmp_gdoc_id = copied.get('id')
                try:
                    pdf_bytes = svc.files().export(
                        fileId=tmp_gdoc_id,
                        mimeType='application/pdf',
                    ).execute()
                finally:
                    try:
                        svc.files().delete(fileId=tmp_gdoc_id).execute()
                    except Exception:
                        pass
                with open(temp_path, 'wb') as f:
                    f.write(pdf_bytes)

            elif mime_type == 'application/pdf':
                # Ya es PDF → descargar tal cual
                self._drive_get_content_file(gdrive_file, temp_path)

            else:
                # Otro formato → descargar con su extensión original
                orig_ext = os.path.splitext(original_title)[1]
                final_name = f"{odoo_name}{orig_ext}" if orig_ext else (self.file_name or original_title)
                export_mime = mime_type
                self._drive_get_content_file(gdrive_file, temp_path)

            file_base64 = self._drive_read_temp_as_base64(temp_path)

            attachment = self.env["ir.attachment"]._drive_create_temp_download({
                "name": final_name,
                "type": "binary",
                "datas": file_base64,
                "mimetype": export_mime,
                "res_model": self._name,
                "res_id": self.id,
            })
            self._log_document_activity(
                "download",
                "⬇️ Documento descargado en formato PDF: <b>%s</b>" % final_name,
            )
            return {
                "type": "ir.actions.act_url",
                "url": f"/web/content/{attachment.id}?download=true",
                "target": "self",
            }

        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error en descarga PDF desde Drive")
            raise UserError("No fue posible descargar el archivo desde Google Drive. Intente nuevamente y verifique que el archivo exista y tenga permisos de lectura.")
        finally:
            self._drive_safe_remove_temp(temp_path)

    def download_from_drive_original(self):
        """Descarga el archivo original sin convertir cuando Drive lo permite."""
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        temp_path = None
        try:
            target_id = self.file_id or self.drive_file_id
            drive_auth = GoogleDriveAuth.get_instance()
            drive_obj = drive_auth.get_drive()

            if not target_id:
                search_name = self.file_name or self.name or "Archivo Sin Nombre"
                file_list = drive_obj.ListFile({'q': f"title = '{search_name}' and trashed = false"}).GetList()
                if file_list:
                    target_id = file_list[0]['id']
                    self.sudo().write({'file_id': target_id, 'drive_file_id': target_id})
                else:
                    raise UserError(f"No se encontró el archivo '{search_name}' en Drive.")

            gdrive_file = drive_obj.CreateFile({"id": target_id})
            gdrive_file.FetchMetadata()

            mime_type = gdrive_file.get('mimeType')
            original_title = gdrive_file.get('title') or self.file_name or self.name or 'documento'
            final_name = self.file_name or self.name or original_title
            download_mime = mime_type or mimetypes.guess_type(final_name)[0] or 'application/octet-stream'

            google_exports = {
                'application/vnd.google-apps.document': (
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.docx',
                ),
                'application/vnd.google-apps.spreadsheet': (
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.xlsx',
                ),
                'application/vnd.google-apps.presentation': (
                    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    '.pptx',
                ),
                'application/vnd.google-apps.drawing': ('image/png', '.png'),
            }

            temp_path = self._drive_temp_path()
            if mime_type in google_exports:
                download_mime, export_ext = google_exports[mime_type]
                base_name = os.path.splitext(final_name or original_title)[0]
                final_name = f"{base_name}{export_ext}"
                self._drive_get_content_file(gdrive_file, temp_path, mimetype=download_mime)
            else:
                if not os.path.splitext(final_name)[1] and os.path.splitext(original_title)[1]:
                    final_name = f"{final_name}{os.path.splitext(original_title)[1]}"
                self._drive_get_content_file(gdrive_file, temp_path)

            file_base64 = self._drive_read_temp_as_base64(temp_path)

            attachment = self.env["ir.attachment"]._drive_create_temp_download({
                "name": final_name,
                "type": "binary",
                "datas": file_base64,
                "mimetype": download_mime,
                "res_model": self._name,
                "res_id": self.id,
            })
            self._log_document_activity(
                "download",
                "⬇️ Documento original descargado: <b>%s</b>" % final_name,
            )
            return {
                "type": "ir.actions.act_url",
                "url": f"/web/content/{attachment.id}?download=true",
                "target": "self",
            }

        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error en descarga original desde Drive")
            raise UserError("No fue posible descargar el archivo original desde Google Drive. Intente nuevamente y verifique que el archivo exista y tenga permisos de lectura.")
        finally:
            self._drive_safe_remove_temp(temp_path)

    def download_from_drive_as_word(self):
        """Descarga el archivo desde Drive en formato Word (.docx).
        - Google Docs nativos: exporta directamente como DOCX.
        - PDFs u otros archivos: extrae el texto con PyPDF2 y construye un DOCX.
        """
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        temp_path = temp_orig = temp_docx = None
        try:
            target_id = self.file_id or self.drive_file_id
            drive_auth = GoogleDriveAuth.get_instance()
            drive = drive_auth.get_drive()

            if not target_id:
                search_name = self.file_name or self.name or "Archivo Sin Nombre"
                query = f"title = '{search_name}' and trashed = false"
                file_list = drive.ListFile({'q': query}).GetList()
                if file_list:
                    target_id = file_list[0]['id']
                    self.sudo().write({'file_id': target_id, 'drive_file_id': target_id})
                else:
                    raise UserError(f"No se encontró el archivo '{search_name}' en Drive.")

            gdrive_file = drive.CreateFile({"id": target_id})
            gdrive_file.FetchMetadata()

            mime_type = gdrive_file['mimeType']
            original_title = gdrive_file['title']
            # Usar el nombre limpio de Odoo para el archivo descargado
            clean_name = os.path.splitext(self.name or self.file_name or original_title)[0]
            final_name = f"{clean_name}.docx"
            word_mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            word_mime_types = {
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/msword',
            }

            if mime_type == 'application/vnd.google-apps.document':
                # Google Doc nativo → exportar directo como DOCX
                temp_path = self._drive_temp_path('.docx')
                self._drive_get_content_file(gdrive_file, temp_path, mimetype=word_mime)
                file_base64 = self._drive_read_temp_as_base64(temp_path)
            elif mime_type in word_mime_types:
                # Ya es un DOCX/DOC → descargar tal cual
                if mime_type == 'application/msword':
                    final_name = f"{clean_name}.doc"
                    word_mime = mime_type
                temp_path = self._drive_temp_path('.docx')
                self._drive_get_content_file(gdrive_file, temp_path)
                file_base64 = self._drive_read_temp_as_base64(temp_path)
            else:
                # PDF u otro → extraer texto con PyPDF2 y construir DOCX
                temp_orig = self._drive_temp_path('_orig')
                self._drive_get_content_file(gdrive_file, temp_orig)

                doc = Document()
                text_extracted = False
                try:
                    with open(temp_orig, 'rb') as f:
                        reader = PdfReader(f)
                        for i, page in enumerate(reader.pages):
                            text = page.extract_text() or ""
                            lines = [l.strip() for l in text.splitlines() if l.strip()]
                            for line in lines:
                                doc.add_paragraph(line)
                                text_extracted = True
                            if i < len(reader.pages) - 1:
                                doc.add_page_break()
                except Exception as _pdf_err:
                    _logger.warning("PyPDF2 no pudo extraer texto: %s", _pdf_err)

                if not text_extracted:
                    doc.add_paragraph(f"Documento: {final_name}")
                    doc.add_paragraph("(El archivo original es un PDF sin texto extraíble. Descárgalo en PDF para verlo correctamente.)")

                self._drive_safe_remove_temp(temp_orig)
                temp_docx = self._drive_temp_path('.docx')
                doc.save(temp_docx)
                file_base64 = self._drive_read_temp_as_base64(temp_docx)

            attachment = self.env["ir.attachment"]._drive_create_temp_download({
                "name": final_name,
                "type": "binary",
                "datas": file_base64,
                "mimetype": word_mime,
                "res_model": self._name,
                "res_id": self.id,
            })
            self._log_document_activity(
                "download",
                "⬇️ Documento descargado en formato Word: <b>%s</b>" % final_name,
            )

            return {
                "type": "ir.actions.act_url",
                "url": f"/web/content/{attachment.id}?download=true",
                "target": "self",
            }

        except UserError:
            raise
        except Exception as e:
            _logger.exception("Error en descarga Word desde Drive")
            raise UserError("No fue posible descargar el archivo como Word desde Google Drive. Intente nuevamente y verifique que el archivo exista y tenga permisos de lectura.")
        finally:
            for path in (temp_path, temp_orig, temp_docx):
                self._drive_safe_remove_temp(path)

    def open_download_format_wizard(self):
        """Abre el wizard para seleccionar el formato de descarga."""
        self.ensure_one()
        wizard = self.env["drive.download.format.wizard"].create({"drive_id": self.id})
        return {
            "name": "Descargar como",
            "type": "ir.actions.act_window",
            "res_model": "drive.download.format.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def open_change_name_popup(self):
        return {
            "name": "Cambiar Nombre del Archivo",
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "change_name_wizard",
            "target": "new",
            "context": {
                "default_drive_id": self.id,
                "default_file_name": self.file_name,
            },
        }

    def create_copy(self):
        try:
            # Generar un nuevo nombre de archivo con "(copia)" añadido antes de la extensión
            base_name, extension = os.path.splitext(self.file_name)
            new_file_name = f"{base_name} (copia){extension}"

            # Crear los valores para la nueva copia
            vals = {
                "file_data": self.file_data,
                "file_name": new_file_name,
                "folder_id": self.folder_id,
            }

            # Crear la nueva copia en Odoo
            copy_record = self.create(vals)

            if copy_record:
                # Subir la nueva copia a Google Drive solo si no tiene un ID de archivo de Drive
                if not copy_record.drive_file_id:
                    copy_record.upload_to_drive()

                return True
            else:
                return False

        except Exception as e:
            print(f"Error al crear la copia: {e}")
            return False

    # Default stage method
    @api.model
    def _default_stage(self):
        return self.env["drive_stage"].search([], limit=1)


class DriveDownloadFormatWizard(models.TransientModel):
    _name = "drive.download.format.wizard"
    _description = "Seleccionar formato de descarga"

    drive_id = fields.Many2one("drive", string="Documento", required=True)

    def action_download_pdf(self):
        self.ensure_one()
        return self.drive_id.download_from_drive()

    def action_download_word(self):
        self.ensure_one()
        return self.drive_id.download_from_drive_as_word()

    def action_download_original(self):
        self.ensure_one()
        return self.drive_id.download_from_drive_original()


class ChangeNameWizard(models.TransientModel):
    _name = "change_name_wizard"
    _description = "Wizard para cambiar el nombre del archivo"

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # RELACIONES
    # ------------------------------------------------------------------------------------------------------------------------------------------

    contratacion_fuv_documentos_id = fields.Many2one(
        "contrataciones_fuv",
        string="Contrato Relacionado",
        related="drive_id.contratacion_fuv_documentos_id",
    )
    carpeta_principal_id = fields.Many2one(
        "carpeta_principal",
        string="Contrato Relacionado",
        related="drive_id.carpeta_principal_id",
    )
    carpeta_principal_otros_id = fields.Many2one(
        "carpeta_principal",
        string="Contrato Relacionado",
        related="drive_id.carpeta_principal_otros_id",
    )
    carpeta_principal_nueva_relacion_id = fields.Many2one(
        "carpeta_principal",
        string="Contrato Relacionado",
        related="drive_id.carpeta_principal_nueva_relacion_id",
    )
    carpetas_principales_otra_relacion_id = fields.Many2one(
        "carpeta_principal",
        string="Contrato Relacionado",
        related="drive_id.carpetas_principales_otra_relacion_id",
    )
    bienes_id = fields.Many2one(
        "orden_compra_bienes",
        string="Contrato Relacionado",
        related="drive_id.bienes_id",
    )
    anticipos_id = fields.Many2one(
        "anticipos", string="Contrato Relacionado", related="drive_id.anticipos_id"
    )
    servicios_id = fields.Many2one(
        "orden_compra_servicios",
        string="Contrato Relacionado",
        related="drive_id.servicios_id",
    )
    otrosi_documentos_id = fields.Many2one(
        "otro_si",
        string="Contrato Relacionado",
        related="drive_id.otrosi_documentos_id",
    )
    carpeta_principal_cierre_id = fields.Many2one(
        "cierre",
        string="Contrato Relacionado",
        related="drive_id.carpeta_principal_cierre_id",
    )
    viaticos_id = fields.Many2one(
        "viaticos", string="Contrato Relacionado", related="drive_id.viaticos_id"
    )
    drive_id = fields.Many2one("drive", string="Archivo Original", readonly=True)

    # ------------------------------------------------------------------------------------------------------------------------------------------
    # VARIABLE PARA CAMBIAR EL NOMBRE
    # ------------------------------------------------------------------------------------------------------------------------------------------

    new_name = fields.Char("Nuevo Nombre del Archivo", required=True)

    @api.model
    def default_get(self, fields):
        res = super(ChangeNameWizard, self).default_get(fields)
        drive_record = self.env["drive"].browse(self._context.get("active_id"))
        if "new_name" in fields:
            res.update({"new_name": drive_record.file_name})
        return res

    def change_file_name(self):
        # --- PARTE 1: Actualización de Google Drive y Nombre Base ---
        drive_record = self.drive_id or self.env["drive"].browse(
            self._context.get("active_id")
        )

        old_name = drive_record.file_name
        old_extension = old_name.split(".")[-1] if "." in old_name else ""

        if "." not in self.new_name:
            if old_extension:
                self.new_name = f"{self.new_name}.{old_extension}"
        elif self.new_name.split(".")[-1].lower() != old_extension.lower():
            self.new_name = f"{self.new_name}.{old_extension}"

        drive_record.write({"file_name": self.new_name})

        # Autenticación y subida a Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive_service = drive_auth.get_drive()

        try:
            if drive_record.drive_file_id:
                gdrive_file = drive_service.CreateFile(
                    {"id": drive_record.drive_file_id}
                )
            else:
                file_list = drive_service.ListFile(
                    {"q": f"title='{old_name}' and trashed=false"}
                ).GetList()
                if not file_list:
                    # Fallback suave: si no está en Drive, seguimos procesando Odoo
                    gdrive_file = None
                else:
                    gdrive_file = file_list[0]

            if gdrive_file:
                gdrive_file["title"] = self.new_name
                gdrive_file.Upload()

            # --- PARTE 2: Actualización Masiva de Campos Char ---
            models_map = {
                "orden_compra_servicios": [
                    ("rut_pj_doc_id", "rut_pj_name"),
                    ("Anexo_tributario_doc_id", "Anexo_tributario_name"),
                    ("Anexo_tributario_doc_pn_id", "Anexo_tributario_name_pn"),
                    ("camara_comercio_doc_id", "camara_comercio_name"),
                    ("planilla_ss_doc_id", "planilla_ss_name"),
                    ("cert_parafiscales_doc_id", "cert_parafiscales_name"),
                    ("certificado_junta_contadores_doc_id", "junta_contadores_name"),
                    ("tp_contador_doc_id", "tp_contador_name"),
                    ("certificado_bancario_pj_doc_id", "certificado_bancario_pj_name"),
                    ("resolucion_facturacion_doc_id", "resolucion_facturacion_name"),
                    ("documento_rpc_pj_doc_id", "documento_rpc_pj_name"),
                    ("factura_cuenta_pj_doc_id", "factura_cuenta_pj_name"),
                    ("rut_pn_doc_id", "rut_pn_name"),
                    ("documento_identidad_doc_id", "documento_identidad_name"),
                    ("planilla_doc_id", "planilla_name"),
                    ("certificado_bancario_doc_id", "certificado_bancario_name"),
                    ("documento_rpc_doc_id", "documento_rpc_name"),
                    ("factura_cuenta_doc_id", "factura_cuenta_name"),
                ],
                "orden_compra_bienes": [
                    ("rut_pj_doc_id", "rut_pj_name"),
                    ("Anexo_tributario_doc_id", "Anexo_tributario_name"),
                    ("Anexo_tributario_doc_pn_id", "Anexo_tributario_name_pn"),
                    ("camara_comercio_doc_id", "camara_comercio_name"),
                    ("planilla_ss_doc_id", "planilla_ss_name"),
                    ("cert_parafiscales_doc_id", "cert_parafiscales_name"),
                    ("certificado_junta_contadores_doc_id", "junta_contadores_name"),
                    ("tp_contador_doc_id", "tp_contador_name"),
                    ("certificado_bancario_pj_doc_id", "certificado_bancario_pj_name"),
                    ("resolucion_facturacion_doc_id", "resolucion_facturacion_name"),
                    ("documento_rpc_pj_doc_id", "documento_rpc_pj_name"),
                    ("factura_cuenta_pj_doc_id", "factura_cuenta_pj_name"),
                    ("rut_pn_doc_id", "rut_pn_name"),
                    ("documento_identidad_doc_id", "documento_identidad_name"),
                    ("planilla_doc_id", "planilla_name"),
                    ("certificado_bancario_doc_id", "certificado_bancario_name"),
                    ("documento_rpc_doc_id", "documento_rpc_name"),
                    ("factura_cuenta_doc_id", "factura_cuenta_name"),
                ],
                "contratista_proveedor": [
                    ("documento_identidad_c_id", "documento_identidad_name"),
                    ("rut_actualizado_id", "rut_actualizado_name"),
                    ("hoja_vida_id", "hoja_vida_name"),
                    ("certificado_estudio_id", "certificado_estudio_name"),
                    ("certificado_laborales_id", "certificado_laborales_name"),
                    ("cert_arl_id", "cert_arl_name"),
                    ("cert_afiliacion_salud_id", "cert_afiliacion_salud_name"),
                    ("cert_afiliacion_pension_id", "cert_afiliacion_pension_name"),
                    (
                        "cert_examen_preocupacional_id",
                        "cert_examen_preocupacional_name",
                    ),
                    (
                        "resolucion_facturacion_electronica_id",
                        "resolucion_facturacion_electronica_name",
                    ),
                    ("cert_contraloria_id", "cert_contraloria_name"),
                    ("cert_procuraduria_id", "cert_procuraduria_name"),
                    ("cert_policia_judicial_id", "cert_policia_judicial_name"),
                    ("cert_medidas_cautelares_id", "cert_medidas_cautelares_name"),
                    ("cert_inhabilidades_id", "cert_inhabilidades_name"),
                    ("cert_libreta_militar_id", "cert_libreta_militar_name"),
                    (
                        "resolucion_pensionado_vigente_id",
                        "resolucion_pensionado_vigente_name",
                    ),
                    (
                        "certificado_bancario_vigente_id",
                        "certificado_bancario_vigente_name",
                    ),
                    ("declaracion_juramentada_id", "declaracion_juramentada_name"),
                    ("propuesta_j_id", "propuesta_j_name"),
                    ("camara_c_actualizada_j_id", "camara_c_actualizada_j_name"),
                    ("rut_actualizado_j_id", "rut_actualizado_j_name"),
                    ("rup_j_id", "rup_j_name"),
                    ("hoja_vida_j_id", "hoja_vida_j_name"),
                    ("cert_contraloria_j_id", "cert_contraloria_j_name"),
                    ("cert_procuraduria_j_id", "cert_procuraduria_j_name"),
                    ("cert_medidas_cautelares_j_id", "cert_medidas_cautelares_j_name"),
                    ("cert_inhabilidades_j_id", "cert_inhabilidades_j_name"),
                    ("resolucion_fact_elect_j_id", "resolucion_fact_elect_j_name"),
                    ("experiencia_laboral_j_id", "experiencia_laboral_j_name"),
                    ("cert_pago_paraf_j_id", "cert_pago_paraf_j_name"),
                    (
                        "planilla_seguridad_social_j_id",
                        "planilla_seguridad_social_j_name",
                    ),
                    ("cert_junta_contadores_j_id", "cert_junta_contadores_j_name"),
                    (
                        "cedula_revisor_fiscal_contador_j_id",
                        "cedula_revisor_fiscal_contador_j_name",
                    ),
                    (
                        "documento_de_identidad_contador_j_id",
                        "documento_de_identidad_contador_j_name",
                    ),
                    (
                        "Tarjeta_profesional_revisor_fiscal_j_id",
                        "Tarjeta_profesional_revisor_fiscal_j_name",
                    ),
                    (
                        "Tarjeta_profesional_contador_j_id",
                        "Tarjeta_profesional_contador_j_name",
                    ),
                    (
                        "certificado_bancario_vigente_j_id",
                        "certificado_bancario_vigente_j_name",
                    ),
                    (
                        "cedula_representante_legal_id",
                        "cedula_representante_legal_name",
                    ),
                    (
                        "hoja_v_representante_legal_id",
                        "hoja_v_representante_legal_name",
                    ),
                    (
                        "cert_contraloria_repre_legal_id",
                        "cert_contraloria_repre_legal_name",
                    ),
                    (
                        "cert_procuraduria_repre_legal_id",
                        "cert_procuraduria_repre_legal_name",
                    ),
                    ("estados_financieros2_id", "estados_financieros2_name"),
                ],
                "contrataciones_fuv": [
                    ("documento_identidad_id", "documento_identidad_pn_doc_fname"),
                    ("rut_id", "rut_pn_doc_fname"),
                    ("hoja_vida_id", "hoja_vida_pn_doc_fname"),
                    ("certificados_estudio_id", "certificados_estudio_doc_fname"),
                    ("certificados_laborales_id", "certificados_laborales_doc_fname"),
                    ("certificado_arl_id", "certificado_arl_doc_fname"),
                    ("otra_certificado_arl_id", "otra_certificado_arl_doc_fname"),
                    ("certificacion_salud_id", "certificacion_salud_doc_fname"),
                    ("certificacion_pension_id", "certificacion_pension_doc_fname"),
                    (
                        "certificacion_examen_preocupacional_id",
                        "certificacion_examen_preocupacional_doc_fname",
                    ),
                    ("su_cdp_id", "su_cdp_doc_fname"),
                    ("poliza_id", "polizas_fname"),
                    ("tarjeta_profesional_doc_id", "tarjeta_profesional_doc_fname"),
                    ("formato_fuv_ju_doc_id", "formato_fuv_ju_doc_fname"),
                    (
                        "resolucion_facturacion_ju_id",
                        "resolucion_facturacion_ju_doc_fname",
                    ),
                    ("bdd_ju_id", "bdd_ju_doc_fname"),
                    ("formato_fuv_doc_id", "formato_fuv_doc_fname"),
                    (
                        "certificacion_contraloria_id",
                        "certificacion_contraloria_doc_fname",
                    ),
                    (
                        "certificacion_procuraduria_id",
                        "certificacion_procuraduria_doc_fname",
                    ),
                    (
                        "certificacion_policia_judicial_id",
                        "certificacion_policia_judicial_doc_fname",
                    ),
                    (
                        "certificacion_medidas_cautelares_id",
                        "certificacion_medidas_cautelares_doc_fname",
                    ),
                    (
                        "certificacion_inhabilidades_id",
                        "certificacion_inhabilidades_doc_fname",
                    ),
                    (
                        "certificacion_libreta_militar_id",
                        "certificacion_libreta_militar_doc_fname",
                    ),
                    ("resolucion_pensionado_id", "resolucion_pensionado_doc_fname"),
                    ("certificacion_bancaria_id", "certificacion_bancaria_doc_fname"),
                    ("anexo_tributario_id", "anexo_tributario_doc_fname"),
                    ("declaracion_juramentada_id", "declaracion_juramentada_doc_fname"),
                    ("contratos_id", "contratos_doc_fname"),
                    (
                        "aprueba_j_h_contrato_firmado_doc_id",
                        "aprueba_j_h_contrato_firmado_doc_fname",
                    ),
                    ("poliza_pn_id", "polizas__pn_fname"),
                    ("contratos_firmado_id", "contratos_firmado_doc_fname"),
                    ("propuesta_pj_doc_id", "propuesta_pj_doc_fname"),
                    ("camara_comercio_doc_id", "camara_comercio_doc_fname"),
                    ("rut_pj_doc_id", "rut_pj_doc_fname"),
                    ("rup_doc_id", "rup_doc_fname"),
                    ("hoja_vida_juridica_doc_id", "hoja_vida_juridica_doc_fname"),
                    ("cert_contraloria_doc_id", "cert_contraloria_doc_fname"),
                    ("cert_procuraduria_doc_id", "cert_procuraduria_doc_fname"),
                    ("cedula_rep_legal_doc_id", "cedula_rep_legal_doc_fname"),
                    ("hoja_vida_rep_legal_doc_id", "hoja_vida_rep_legal_doc_fname"),
                    (
                        "cert_contraloria_rep_legal_doc_id",
                        "cert_contraloria_rep_legal_doc_fname",
                    ),
                    (
                        "cert_procuraduria_rep_legal_doc_id",
                        "aprueba_j_h_contraloria_rep_lega_doc_fname",
                    ),
                    ("cdp_juridico_doc_id", "cdp_doc_fname"),
                    (
                        "estados_financieros_juridico_doc_id",
                        "estados_financieros_doc_fname",
                    ),
                    ("cert_policia_judicial_doc_id", "cert_policia_judicial_doc_fname"),
                    ("cert_inhabilidades_doc_id", "cert_inhabilidades_doc_fname"),
                    (
                        "resolucion_facturacion_doc_id",
                        "resolucion_facturacion_doc_fname",
                    ),
                    ("experiencia_laboral_doc_id", "experiencia_laboral_doc_fname"),
                    (
                        "certificado_pago_parafiscales_doc_id",
                        "certificado_pago_parafiscales_doc_fname",
                    ),
                    (
                        "planilla_seguridad_social_doc_id",
                        "planilla_seguridad_social_doc_fname",
                    ),
                    (
                        "certificado_junta_contadores_doc_id",
                        "certificado_junta_contadores_doc_fname",
                    ),
                    ("cedula_revisor_fiscal_doc_id", "cedula_revisor_fiscal_doc_fname"),
                    (
                        "Documento_de_identidad_contador_doc_id",
                        "Documento_de_identidad_contador_doc_fname",
                    ),
                    (
                        "Tarjeta_profesional_revisor_fiscal_doc_id",
                        "Tarjeta_profesional_revisor_fiscal_doc_fname",
                    ),
                    (
                        "Tarjeta_profesional_contador_doc_id",
                        "Tarjeta_profesional_contador_fname",
                    ),
                    ("certificado_bancario_doc_id", "certificado_bancario_doc_fname"),
                    (
                        "anexo_tributario_juridico_doc_id",
                        "anexo_tributario_juridico_doc_fname",
                    ),
                    ("aprueba_j_h_contrato_doc_id", "aprueba_j_h_contrato_doc_fname"),
                    ("aprueba_j_h_bdd_doc_id", "aprueba_j_h_bdd_doc_fname"),
                ],
                "carpeta_principal": [
                    ("carta_invitacion_id", "carta_invitacion_char"),
                    ("cdp_id", "cdp_char"),
                    ("estudios_previos_id", "estudios_previos_char"),
                    ("propuesta_id", "propuesta_char"),
                    (
                        "oficio_remisorio_propuesta_id",
                        "oficio_remisorio_propuesta_char",
                    ),
                    ("resolucion_contratacion_id", "resolucion_contratacion_char"),
                    (
                        "contrato_convenio_componente_id",
                        "contrato_convenio_componente_char",
                    ),
                    ("polizas_id", "polizas_char"),
                    ("aceptacion_polizas_id", "aceptacion_polizas_char"),
                    ("acta_inicio_id", "acta_inicio_char"),
                    ("rpc_id", "rpc_char"),
                    ("otrosi_modificatorios_id", "otrosi_modificatorios_char"),
                    ("polizas_modificatorios_id", "polizas_modificatorios_char"),
                    ("designacion_supervisor_id", "designacion_supervisor_char"),
                    ("facturacion_id", "facturacion_char"),
                    ("actas_seguimiento_id", "actas_seguimiento_char"),
                    ("informes_avance_id", "informes_avance_char"),
                    (
                        "actas_supervisor_parciales_id",
                        "actas_supervisor_parciales_char",
                    ),
                    ("informe_final_id", "informe_final_char"),
                    ("acta_final_supervisor_id", "acta_final_supervisor_char"),
                    ("acta_terminacion_id", "acta_terminacion_char"),
                    ("acta_final_pago_id", "acta_final_pago_char"),
                    ("acta_liquidacion_id", "acta_liquidacion_char"),
                    ("certificado_cumplimiento_id", "certificado_cumplimiento_char"),
                    ("matriz_de_riesgos_id", "matriz_de_riesgos_char"),
                    ("informe_de_invertentoria_id", "informe_de_invertentoria_char"),
                    ("m_g_a_id", "m_g_a_char"),
                ],
                "cierre": [
                    ("archivo_acta_liquidacion_id", "archivo_acta_liquidacion_name"),
                    ("paz_y_salvo_id", "paz_y_salvo_name"),
                    ("acta_liquidacion_firmada_id", "acta_liquidacion_firmada_name"),
                ],
                "otro_si": [
                    ("cdp_juridico_doc_id", "cdp_doc_juridico_fname"),
                    ("contratos_otro_juridico_id", "contratos_otro_doc_juridico_fname"),
                    ("poliza_id", "polizas_fname"),
                    (
                        "contratos_firmado_juridico_id",
                        "contratos_firmado_juridico_doc_fname",
                    ),
                    ("rut_doc_fname", "rut_doc_fname"),
                    ("aprueba_j_h_otro_doc_fname", "aprueba_j_h_otro_doc_fname"),
                    ("propuesta_doc_id", "propuesta_doc_fname"),
                    ("su_cdp_id", "su_cdp_doc_fname"),
                    ("su_contrato_natu_doc_fname", "su_contrato_natu_doc_fname"),
                    ("su_rut_doc_fname", "su_rut_doc_fname"),
                    ("su_arl_doc_id", "su_arl_doc_fname"),
                    ("contratos_otro_id", "contratos_otro_doc_fname"),
                    ("contratos_firmado_id", "contratos_firmado_natu_doc_fname"),
                    ("contraloria_otro_doc_fname", "contraloria_otro_doc_fname"),
                ],
                "anticipos": [
                    ("rut_pn_document_id", "rut_pn_filename"),
                    ("doc_identidad_document_id", "doc_identidad_filename"),
                    ("cert_bancaria_pn_document_id", "cert_bancaria_pn_filename"),
                    ("rpc_document_id", "rpc_filename"),
                    ("at_document_id", "at_filename"),
                    ("comprobante_pago_document_id", "comprobante_pago_filename"),
                    ("rut_pj_document_id", "rut_pj_filename"),
                    ("camara_comercio_document_id", "camara_comercio_filename"),
                    ("cert_bancaria_pj_document_id", "cert_bancaria_pj_filename"),
                ],
                "viaticos": [
                    ("rut_pn_document_id", "rut_pn_filename"),
                    ("doc_identidad_document_id", "doc_identidad_filename"),
                    ("cert_bancaria_pn_document_id", "cert_bancaria_pn_filename"),
                    ("orden_pago_document_id", "orden_pago_filename"),
                    ("comprobante_pago_document_id", "comprobante_pago_filename"),
                    ("soporte_de_pago_document_id", "soporte_de_pago_filename"),
                    ("documento_de_causacion_id", "documento_de_causacion_filename"),
                    ("rut_pj_document_id", "rut_pj_filename"),
                    ("camara_comercio_document_id", "camara_comercio_filename"),
                    ("cert_bancaria_pj_document_id", "cert_bancaria_pj_filename"),
                    ("orden_pago_pj_document_id", "orden_pago_pj_filename"),
                    ("comprobante_pago_pj_document_id", "comprobante_pago_pj_filename"),
                    ("soporte_de_pago_pj_document_id", "soporte_de_pago_pj_filename"),
                    (
                        "documento_de_causacion_pj_id",
                        "documento_de_causacion_pj_filename",
                    ),
                ],
            }

            for model_name, field_tuples in models_map.items():
                model_obj = self.env.get(model_name)
                if not model_obj:
                    continue
                for field_id_name, field_char_name in field_tuples:
                    records_to_update = model_obj.search(
                        [(field_id_name, "=", drive_record.id)]
                    )
                    if records_to_update:
                        records_to_update.write({field_char_name: self.new_name})

            # --- PARTE 3: Trazabilidad (Chatter) ---
            # Lista de campos relacionales definidos en el wizard
            relational_fields = [
                "contratacion_fuv_documentos_id",
                "carpeta_principal_id",
                "carpeta_principal_otros_id",
                "carpeta_principal_nueva_relacion_id",
                "carpetas_principales_otra_relacion_id",
                "bienes_id",
                "anticipos_id",
                "servicios_id",
                "viaticos_id",
                "otrosi_documentos_id",
                "carpeta_principal_cierre_id",
            ]

            # --- CAMBIO AQUÍ: Mensaje con el "Antes" y "Después" ---
            msg_body = f"✏️ Nombre de documento actualizado: de <b>{old_name}</b> a <b>{self.new_name}</b>"
            _logger.info(msg_body)
            drive_record._log_document_activity("rename", msg_body)

            for field_name in relational_fields:
                # Obtenemos el registro relacionado (active_record)
                parent_record = getattr(self, field_name, False)

                # Si existe un registro vinculado y tiene chatter, ponemos el mensaje
                if parent_record and hasattr(parent_record, "message_post"):
                    parent_record.message_post(body=msg_body)

            return {
                "type": "ir.actions.act_window_close",
                "tag": "display_notification",
                "params": {
                    "title": "Éxito",
                    "message": f"Nombre actualizado a {self.new_name} y notificado en chatter.",
                    "sticky": False,
                },
            }

        except Exception as e:
            return {
                "type": "ir.actions.act_window_close",
                "tag": "display_notification",
                "params": {
                    "title": "Error",
                    "message": f"Error al actualizar: {str(e)}",
                    "sticky": True,
                },
            }


class DriveStageTrace(models.Model):
    _name = "drive_stage.trace"
    _description = "Trazabilidad de carpetas Drive"

    drive_stage_id = fields.Many2one(
        "drive_stage", string="Carpeta relacionada", required=True, ondelete="cascade"
    )
    action_type = fields.Selection(
        [
            ("create", "Creación"),
            ("update", "Actualización"),
            ("delete", "Eliminación"),
        ],
        string="Tipo de acción",
        required=True,
    )
    user_id = fields.Many2one(
        "res.users", string="Usuario", required=True, default=lambda self: self.env.user
    )
    notes = fields.Text(string="Notas")
    action_date = fields.Datetime(
        string="Fecha de acción",
        default=lambda self: fields.Datetime.now(),
        readonly=True,
    )


class DriveStage(models.Model):
    _name = "drive_stage"
    _description = "Sub carpetas"
    _order = "sequence, id"

    file_ids = fields.One2many("drive", "stage3_id", string="Files")
    proyecto_relacionado = fields.Many2one(
        "contratacion", string="Proyecto relacionado"
    )
    contrataciones_carpeta_id = fields.Many2one(
        "contratacion", string="Contratación", related="proyecto_relacionado"
    )
    name = fields.Char(string="Carpetas", related="folder_name")
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one(
        "res.users",
        string="Creador",
        default=lambda self: self.env.user,
        track_visibility="always",
    )
    real_creator_id = fields.Many2one(
        "res.users",
        string="Creador real",
        compute="_compute_real_creator_stage",
        store=False,
    )

    @api.depends("proyecto_relacionado", "user_id")
    def _compute_real_creator_stage(self):
        for rec in self:
            if rec.proyecto_relacionado and rec.proyecto_relacionado.create_uid:
                rec.real_creator_id = rec.proyecto_relacionado.create_uid
            else:
                rec.real_creator_id = rec.user_id or rec.create_uid

    fch_creacion = fields.Datetime(
        string="Fecha creacion",
        readonly=True,
        copy=False,
        default=lambda self: fields.Datetime.now(),
    )
    folder_name = fields.Char(string="Nombre de la carpeta")
    drive_id = fields.Many2one("drive_folder", string="Carpetas")
    google_drive_id = fields.Char(string="Google Drive ID")
    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)
    hiden = fields.Char(string="hiden")

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    @api.model
    def create(self, vals):
        # Iniciar el contador de tiempo
        start_time = datetime.now()

        # Crear el registro
        record = super(DriveStage, self).create(vals)

        # Registrar el tiempo transcurrido
        elapsed_time = (datetime.now() - start_time).total_seconds()
        print(f"DriveStage create tomó {elapsed_time:.2f} segundos")

        # Crear un registro de trazabilidad
        self.env["drive_stage.trace"].create(
            {
                "drive_stage_id": record.id,
                "action_type": "create",
                "user_id": self.env.user.id,
                "notes": _("Carpeta creada: %s") % record.folder_name,
            }
        )
        print("Logica crear")
        return record

    def write(self, vals):
        # Guardar el resultado de la escritura
        result = super(DriveStage, self).write(vals)

        # Crear un registro de trazabilidad si se actualizan ciertos campos clave
        if "folder_name" in vals or "folder_id" in vals or "folder_guardar_id" in vals:
            self.env["drive_stage.trace"].create(
                {
                    "drive_stage_id": self.id,
                    "action_type": "update",
                    "user_id": self.env.user.id,
                    "notes": _("Carpeta actualizada: %s") % self.folder_name,
                }
            )

        return result

    # Cambiar los campos a Char para poder almacenar el ID de las carpetas
    folder_id = fields.Char("Google Drive Folder ID")
    folder_guardar_id = fields.Char("Google Drive Folder Guardar ID")

    # Función para obtener las opciones de carpeta, solo si es necesario
    def _get_folder_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        def list_folders(folder_id):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                options.extend(list_folders(folder["id"]))
            return options

        # Obtener todas las carpetas dentro de la carpeta root
        return list_folders("root")

    def _get_carpeta_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Filtrar las carpetas en Google Drive
        folder_list = drive.ListFile(
            {
                "q": "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            }
        ).GetList()

        return [(folder["id"], folder["title"]) for folder in folder_list]

    def create_drive_folder(self):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        parent_folder_id = self.folder_guardar_id
        # Solo agregar prefijo si el nombre no tiene ya un número al inicio
        if not re.match(r"^\d", self.folder_name or ""):
            prefix = _get_next_prefix_for_folder(drive, parent_folder_id)
            folder_title = (
                f"{prefix} {self.folder_name}" if prefix else self.folder_name
            )
        else:
            folder_title = self.folder_name

        folder_metadata = {
            "title": folder_title,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": parent_folder_id}],
        }

        try:
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            self.write({"folder_id": folder["id"]})

            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        except Exception as e:
            print(f"Error creando la carpeta en Google Drive: {e}")
            return False

    _drive_cache = {}
    _file_cache = {}

    def action_open_drive(self):
        # Nombres que abren el modelo 'drive' (CARPETA PRINCIPAL, con y sin número)
        drive_model_names = {"CARPETA PRINCIPAL", "1. CARPETA PRINCIPAL"}
        # Nombres que abren 'drive.file' sin filtro adicional (con y sin número)
        informe_model_names = {
            "INFORMES",
            "COMUNICACIONES",
            "2. COMUNICACIONES",
            "4. INFORMES",
        }

        # --- Lógica para CARPETA PRINCIPAL (modelo 'drive') ---
        if self.name in drive_model_names:
            if self.id in self.env.context.get("drive_cache", {}):
                drive_records = self.env.context["drive_cache"][self.id]
                _logger.info(
                    f"Usando caché para drive ID {self.id}, drive records: {drive_records}"
                )
            else:
                drive_records = self.env["drive"].search([("stage3_id", "=", self.id)])
                drive_cache = self.env.context.get("drive_cache", {})
                drive_cache[self.id] = drive_records
                self.env.context = dict(self.env.context, drive_cache=drive_cache)
                _logger.info(
                    f"Se encontraron {len(drive_records)} drives para el ID {self.id}"
                )

            return {
                "type": "ir.actions.act_window",
                "name": self.name,
                "res_model": "drive",
                "view_mode": "kanban,tree,form",
                "target": "current",
                "domain": [("stage3_id", "=", self.id)],
                "context": {
                    "default_stage3_id": self.id,
                    "default_folder_id": self.folder_id,
                },
            }

        # --- Lógica para INFORMES (modelo 'drive.file' SIN filtro) ---
        elif self.name in informe_model_names:
            if self.id in self.env.context.get("file_cache", {}):
                file_records = self.env.context["file_cache"][self.id]
                _logger.info(
                    f"Usando caché para file ID {self.id}, file records: {file_records}"
                )
            else:
                file_records = self.env["drive.file"].search(
                    [("drive_id", "=", self.id), ("parent_folder_id", "=", False)]
                )
                file_records.action_actualizar_select_persona()
                file_cache = self.env.context.get("file_cache", {})
                file_cache[self.id] = file_records
                self.env.context = dict(self.env.context, file_cache=file_cache)
                _logger.info(
                    f"Se encontraron {len(file_records)} archivos para el drive ID {self.id}"
                )

            return {
                "type": "ir.actions.act_window",
                "name": self.name,
                "res_model": "drive.file",
                "view_mode": "kanban,tree,form",
                "target": "current",
                "domain": [
                    ("drive_id", "=", self.id),
                    ("parent_folder_id", "=", False),
                ],
                "context": {
                    "default_drive_id": self.id,
                    "default_folder_id2": self.folder_id,
                    "default_folder_guardar_id": self.folder_id
                    or self.folder_guardar_id,
                },
            }

        # --- Lógica para CUALQUIER OTRA CARPETA (modelo 'drive.file' CON filtro) ---
        else:
            if self.id in self.env.context.get("file_cache", {}):
                file_records = self.env.context["file_cache"][self.id]
                _logger.info(
                    f"Usando caché para file ID {self.id}, file records: {file_records}"
                )
            else:
                file_records = self.env["drive.file"].search(
                    [("drive_id", "=", self.id), ("parent_folder_id", "=", False)]
                )
                file_records.action_actualizar_select_persona()
                file_cache = self.env.context.get("file_cache", {})
                file_cache[self.id] = file_records
                self.env.context = dict(self.env.context, file_cache=file_cache)
                _logger.info(
                    f"Se encontraron {len(file_records)} archivos para el drive ID {self.id}"
                )

            return {
                "type": "ir.actions.act_window",
                "name": self.name,
                "res_model": "drive.file",
                "view_mode": "kanban,tree,form",
                "target": "current",
                "domain": [
                    ("drive_id", "=", self.id),
                    ("parent_folder_id", "=", False),
                ],
                "context": {
                    "default_drive_id": self.id,
                    "default_folder_id2": self.folder_id,
                    "default_folder_guardar_id": self.folder_id
                    or self.folder_guardar_id,
                    "search_default_select_persona": 1,
                },
            }

    # @api.model
    # def list_files_in_folder(self):
    #     with api.Environment.manage():
    #         new_cr = self.pool.cursor()
    #         self = self.with_env(self.env(cr=new_cr))
    #         try:
    #             self._list_files_in_folder_impl()
    #             new_cr.commit()
    #         except Exception as e:
    #             new_cr.rollback()
    #             _logger.error(f"Error in list_files_in_folder: {e}")
    #         finally:
    #             new_cr.close()

    def _list_files_in_folder_impl(self):
        if not self.folder_id:
            _logger.error("Folder ID is not defined.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        file_list = drive.ListFile(
            {"q": f"'{self.folder_id}' in parents and trashed=false"}
        ).GetList()

        for file in file_list:
            try:
                existing_file = self.env["drive"].search(
                    [("drive_file_id", "=", file["id"]), ("stage3_id", "=", self.id)]
                )

                if existing_file:
                    # Solo actualiza si hay cambios en el nombre o URL del archivo
                    if (
                        existing_file.file_name != file["title"]
                        or existing_file.file_url != file["alternateLink"]
                    ):
                        existing_file.write(
                            {
                                "file_name": file["title"],
                                "file_url": file["alternateLink"],
                                "folder_id": self.folder_id,
                            }
                        )
                        _logger.info(
                            f"File updated in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                    else:
                        _logger.info(
                            f"File already up-to-date in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                else:
                    # Solo crea el archivo si no existe en Odoo
                    file.GetContentFile("/tmp/file_to_import")
                    with open("/tmp/file_to_import", "rb") as f:
                        file_data = f.read()

                    new_file = self.env["drive"].create(
                        {
                            "file_name": file["title"],
                            "file_url": file["alternateLink"],
                            "drive_file_id": file["id"],
                            "stage3_id": self.id,
                            "file_data": base64.b64encode(file_data),
                            "folder_id": self.folder_id,
                        }
                    )
                    _logger.info(
                        f"New file created in Odoo: {new_file.file_name} (ID: {new_file.id})"
                    )

            except Exception as e:
                _logger.error(f"Error processing file {file['title']}: {e}")

        # Asegurarse de no eliminar archivos existentes
        # Evitar la eliminación de archivos en Odoo que no se encuentren en Google Drive
        existing_files_in_odoo = self.env["drive"].search([("stage3_id", "=", self.id)])
        drive_file_ids = [file["id"] for file in file_list]

        for odoo_file in existing_files_in_odoo:
            if odoo_file.drive_file_id not in drive_file_ids:
                _logger.info(
                    f"File in Odoo but not in Drive (not deleting): {odoo_file.file_name} (ID: {odoo_file.id})"
                )

    @api.model
    def list_folders_in_selected_drive(self):
        # Obtén el folder_id del registro actual
        folder_id = self.folder_id

        if not folder_id:
            _logger.error("No folder ID selected.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            # Consulta para obtener carpetas dentro del folder_id seleccionado
            folder_list = drive.ListFile(
                {
                    "q": f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                }
            ).GetList()

            _logger.info(f"Folders in the selected Google Drive folder ({folder_id}):")
            for folder in folder_list:
                _logger.info(
                    f"Folder Name: {folder['title']}, Folder ID: {folder['id']}"
                )

                # Check if the folder already exists in Odoo by folder name
                existing_folder = self.env["drive.file"].search(
                    [("folder_name", "=", folder["title"])], limit=1
                )

                if not existing_folder:
                    # Create a new folder record in Odoo if it doesn't exist
                    self.env["drive.file"].create(
                        {
                            "folder_name": folder["title"],
                            "drive_id": self.id,  # Link to the current DriveStage record
                            "folder_id2": folder_id,  # Link to the current folder in Odoo
                        }
                    )
                    _logger.info(
                        f"Created new folder record in Odoo: {folder['title']}"
                    )

        except Exception as e:
            _logger.error(f"Error retrieving folders from Google Drive: {e}")

    def _find_existing_file_id(self, drive, folder_id, file_name):
        try:
            file_list = drive.ListFile(
                {
                    "q": f"'{folder_id}' in parents and title='{file_name}'",
                    "fields": "items(id)",
                }
            ).GetList()

            if file_list:
                return file_list[0]["id"]
            return None
        except Exception as e:
            _logger.error(
                f"Error buscando el archivo '{file_name}' en Google Drive: {e}"
            )
            raise exceptions.UserError(
                f"Error buscando el archivo '{file_name}' en Google Drive: {e}"
            )

    def download_drive_folder(self):
        if not self.folder_id:
            raise UserError("El ID de la carpeta no está definido.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre de la carpeta
        folder = drive.CreateFile({"id": self.folder_id})
        folder.FetchMetadata(fields="title")
        folder_name = folder["title"]

        # Función para descargar archivos en una carpeta
        def download_files_in_folder(folder_id, zip_obj, path=""):
            query = f"'{folder_id}' in parents and trashed=false"
            file_list = drive.ListFile({"q": query}).GetList()

            if not file_list:
                # Si la carpeta está vacía, crear una estructura de carpeta vacía en el ZIP
                zip_obj.writestr(
                    os.path.join(path, "leerme.txt"), "Carpeta vacía"
                )  # Puedes cambiar o eliminar este archivo si no lo deseas
            else:
                for file in file_list:
                    if file["mimeType"] == "application/vnd.google-apps.folder":
                        subfolder_name = file["title"]
                        new_path = os.path.join(path, subfolder_name)
                        download_files_in_folder(file["id"], zip_obj, new_path)
                    else:
                        # Guardar archivo binario
                        file_path = "/tmp/file_to_download"
                        file.GetContentFile(
                            file_path
                        )  # Descargar archivo al sistema temporal
                        with open(file_path, "rb") as f:
                            file_data = f.read()
                        zip_path = os.path.join(path, file["title"])
                        zip_obj.writestr(zip_path, file_data)

        # Crear un archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            download_files_in_folder(self.folder_id, zip_file)

        # Preparar el archivo para descarga
        zip_buffer.seek(0)
        zip_filename = f"{folder_name}.zip"
        attachment = self.env["ir.attachment"]._drive_create_temp_download(
            {
                "name": zip_filename,
                "datas": base64.b64encode(zip_buffer.read()),
                "type": "binary",
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/zip",
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def unlink(self):
        # Guardar IDs de registros que vamos a eliminar
        ids_to_delete = self.ids

        for record in self:
            # Crear un registro de trazabilidad antes de eliminar
            self.env["drive_stage.trace"].create(
                {
                    "drive_stage_id": record.id,
                    "action_type": "delete",
                    "user_id": self.env.user.id,
                    "notes": _("Carpeta eliminada: %s") % record.folder_name,
                }
            )

            # Eliminar los registros de trazabilidad asociados antes de eliminar el registro de la carpeta
            self.env["drive_stage.trace"].search(
                [("drive_stage_id", "in", [record.id])]
            ).unlink()

            # Eliminar registros relacionados en 'drive.file'
            related_files = self.env["drive.file"].search(
                [("drive_id", "=", record.id)]
            )
            related_files.unlink()

            if record.folder_id:
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()

                try:
                    # Obtener la carpeta de Google Drive
                    folder = drive.CreateFile({"id": record.folder_id})
                    folder.Trash()  # Mover la carpeta a la papelera
                    _logger.info(
                        f"Folder moved to trash in Google Drive: {record.folder_id}"
                    )
                except Exception as e:
                    _logger.error(f"Error moving folder to trash in Google Drive: {e}")

        # Eliminar el registro de la carpeta
        return super(DriveStage, self).unlink()

    def action_view_history(self):
        return {
            "name": "Trazabilidad",
            "view_mode": "tree",
            "res_model": "drive_stage.trace",
            "type": "ir.actions.act_window",
            "target": "current",
        }


class DriveFolderTrace(models.Model):
    _name = "drive_folder.trace"
    _description = "Trazabilidad de Carpeta Drive"
    _order = "date desc"

    drive_folder_id = fields.Many2one("drive_folder", string="Carpeta Drive")
    action = fields.Selection(
        [("create", "Creación"), ("write", "Modificación"), ("unlink", "Eliminación")],
        string="Acción",
        required=True,
    )
    user_id = fields.Many2one(
        "res.users", string="Usuario", default=lambda self: self.env.user
    )
    date = fields.Datetime(string="Fecha", default=fields.Datetime.now)
    details = fields.Text(string="Detalles")


class DriveFolder(models.Model):
    _name = "drive_folder"
    _description = "Carpeta"
    _order = "sequence, id"

    proyecto_relacionado = fields.Many2one(
        "contratacion", string="Proyecto relacionado"
    )
    name = fields.Char(string="Carpetas", related="folder_name")
    sequence = fields.Integer(default=10)
    folder_name = fields.Char(string="Nombre de la carpeta")
    user_id = fields.Many2one(
        "res.users",
        string="Creador",
        default=lambda self: self.env.user,
        track_visibility="always",
    )
    fch_creacion = fields.Datetime(
        string="Fecha creacion",
        readonly=True,
        copy=False,
        default=lambda self: fields.Datetime.now(),
    )
    folder_id = fields.Selection(
        selection="_get_folder_options", string="Google Drive Folder"
    )
    google_drive_id = fields.Char(string="Google Drive ID")
    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    @api.model
    def create(self, vals):
        record = super(DriveFolder, self).create(vals)
        self._log_trace(record, "create")
        return record

    def write(self, vals):
        result = super(DriveFolder, self).write(vals)
        if result:
            self._log_trace(self, "write")
        return result

    def _log_trace(self, record, action):
        for folder in record:
            details = f"ID: {folder.id}, Nombre: {folder.folder_name}"
            self.env["drive_folder.trace"].create(
                {"drive_folder_id": folder.id, "action": action, "details": details}
            )

    def action_view_history(self):
        return {
            "name": "Trazabilidad",
            "view_mode": "tree",
            "res_model": "drive_folder.trace",
            "type": "ir.actions.act_window",
            "target": "current",
        }

    def download_drive_folder(self):
        if not self.folder_guardar_id:
            raise UserError(
                "El ID de la carpeta de Google Drive no está definido. Por favor, selecciona una carpeta de Google Drive."
            )

        # Obtener la autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre de la carpeta
        folder = drive.CreateFile({"id": self.folder_guardar_id})
        folder.FetchMetadata(fields="title")
        folder_name = folder["title"]

        # Función para descargar archivos en una carpeta
        def download_files_in_folder(folder_id, zip_obj, path=""):
            query = f"'{folder_id}' in parents and trashed=false"
            file_list = drive.ListFile({"q": query}).GetList()

            if not file_list:
                # Si la carpeta está vacía, añadir la estructura vacía al ZIP
                zip_obj.writestr(
                    os.path.join(path, "leerme.txt"), "Carpeta vacía"
                )  # O puedes añadir simplemente la carpeta vacía
            else:
                for file in file_list:
                    if file["mimeType"] == "application/vnd.google-apps.folder":
                        subfolder_name = file["title"]
                        new_path = os.path.join(path, subfolder_name)
                        download_files_in_folder(file["id"], zip_obj, new_path)
                    else:
                        # Guardar archivo binario
                        file_path = "/tmp/file_to_download"
                        file.GetContentFile(
                            file_path
                        )  # Descargar archivo al sistema temporal
                        with open(file_path, "rb") as f:
                            file_data = f.read()
                        zip_path = os.path.join(path, file["title"])
                        zip_obj.writestr(zip_path, file_data)

        # Crear un archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            download_files_in_folder(self.folder_guardar_id, zip_file)

        # Preparar el archivo para descarga
        zip_buffer.seek(0)
        zip_filename = f"{folder_name}.zip"
        attachment = self.env["ir.attachment"]._drive_create_temp_download(
            {
                "name": zip_filename,
                "datas": base64.b64encode(zip_buffer.read()),
                "type": "binary",
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/zip",
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def create_drive_carpeta(self):
        start_time = time.time()
        timeout = 10  # segundos de espera máxima

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        parent_folder_id = self.folder_id or None
        if parent_folder_id:
            prefix = _get_next_prefix_for_folder(drive, parent_folder_id)
            folder_title = (
                f"{prefix} {self.folder_name}" if prefix else self.folder_name
            )
        else:
            folder_title = self.folder_name

        folder_metadata = {
            "title": folder_title,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_folder_id:
            folder_metadata["parents"] = [{"id": parent_folder_id}]

        try:
            # Crear la carpeta en Google Drive
            folder = drive.CreateFile(folder_metadata)

            # Mide el tiempo transcurrido y cancela si excede el timeout
            if (time.time() - start_time) > timeout:
                _logger.error("Timeout alcanzado al crear la carpeta en Google Drive")
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Error",
                        "message": "Timeout alcanzado al crear la carpeta.",
                        "type": "danger",
                        "sticky": True,
                    },
                }

            folder.Upload()

            # Asegúrate de que folder_guardar_id sea un campo Char
            folder_id = folder["id"]
            _logger.info(f"ID de la carpeta: {folder_id}")

            # Verifica que el ID de la carpeta sea una cadena válida
            if not isinstance(folder_id, str):
                raise ValueError("El ID de la carpeta no es una cadena válida")

            # Asigna el ID de la carpeta al campo folder_guardar_id
            self.write({"folder_guardar_id": folder_id})

            # Retorna una acción combinada para mostrar la notificación y cerrar la ventana
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Éxito",
                    "message": "La carpeta ha sido creada correctamente.",
                    "type": "success",
                    "sticky": False,
                    "next": {"type": "ir.actions.act_window_close"},
                },
            }

        except Exception as e:
            _logger.error(f"Error creando la carpeta en Google Drive: {e}")
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Error",
                    "message": f"Error al crear la carpeta en Google Drive: {str(e)}",
                    "type": "danger",
                    "sticky": True,
                },
            }

    # Cambia el campo a Char para almacenar el ID de la carpeta de Google Drive
    folder_guardar_id = fields.Char("Google Drive Folder ID")

    def _get_folder_options(self):
        # Solo ejecutar si hay un contexto específico que lo requiera
        if not self.env.context.get("enable_folder_lookup", False):
            return []
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        folder_list = drive.ListFile(
            {
                "q": "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            }
        ).GetList()
        return [(folder["id"], folder["title"]) for folder in folder_list]

    def _get_carpeta_options(self):
        return self._get_folder_options()

    # Variable para almacenar el cache de carpetas
    _carpetas_cache = {}

    def action_open_carpetas(self):
        # Verificar si ya tenemos un cache para este drive_id en la sesión
        if self.id in self.env.context.get("carpetas_cache", {}):
            carpetas = self.env.context["carpetas_cache"][self.id]
            _logger.info(f"Usando cache para drive ID {self.id}, carpetas: {carpetas}")
        else:
            # Si no hay cache, hacer la búsqueda (ACTAS oculto)
            domain = [("drive_id", "=", self.id), ("folder_name", "!=", "ACTAS")]
            carpetas = self.env["drive_stage"].search(domain)

            # Migrar nombres (legacy sin número o con número incorrecto) al nombre correcto
            _RENAME_MAP = {
                "CARPETA PRINCIPAL": "1. CARPETA PRINCIPAL",
                "COMUNICACIONES": "2. COMUNICACIONES",
                "2. CONTRATOS PS": "3. CONTRATOS PS",  # corrección de orden previo
                "CONTRATOS PS": "3. CONTRATOS PS",
                "3. COMUNICACIONES": "2. COMUNICACIONES",  # corrección de orden previo
                "INFORMES": "4. INFORMES",
            }
            for folder in carpetas:
                nuevo = _RENAME_MAP.get(folder.folder_name)
                if (
                    nuevo
                    and self.proyecto_relacionado.suspension_state != "suspended"
                ):
                    folder.write({"folder_name": nuevo})

            # Guardar el resultado en cache en la sesión
            carpetas_cache = self.env.context.get("carpetas_cache", {})
            carpetas_cache[self.id] = carpetas
            self.env.context = dict(self.env.context, carpetas_cache=carpetas_cache)
            _logger.info(
                f"Se encontraron {len(carpetas)} carpetas para el drive ID {self.id}"
            )

        return {
            "type": "ir.actions.act_window",
            "name": "Sub carpetas",
            "res_model": "drive_stage",
            "view_mode": "kanban,tree,form",
            "target": "current",
            "domain": [("id", "in", carpetas.ids)],
            "order": "folder_name asc",
            "context": {
                "default_drive_id": self.id,
                "default_folder_guardar_id": self.folder_guardar_id,
            },
        }

    # @api.model
    # def list_folders_in_selected_drive(self):
    #     with api.Environment.manage():
    #         new_cr = self.pool.cursor()
    #         self = self.with_env(self.env(cr=new_cr))
    #         try:
    #             self._list_folders_in_selected_drive_impl()
    #             new_cr.commit()
    #         except Exception as e:
    #             new_cr.rollback()
    #             _logger.error(f"Error in list_folders_in_selected_drive: {e}")
    #         finally:
    #             new_cr.close()

    def list_folders_in_selected_drive_impl(self):
        folder_id = (
            self.folder_guardar_id
        )  # Aquí se obtiene el ID directamente del campo Selection

        if not folder_id:
            _logger.error("No folder ID selected.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            # Realiza la búsqueda de carpetas en Google Drive
            folder_list = drive.ListFile(
                {
                    "q": f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                }
            ).GetList()

            for folder in folder_list:
                # Busca si la carpeta ya existe en Odoo
                existing_folder = self.env["drive_stage"].search(
                    [
                        ("folder_name", "=", folder["title"]),
                        (
                            "folder_guardar_id",
                            "=",
                            folder_id,
                        ),  # Utiliza folder_id directamente
                    ],
                    limit=1,
                )

                # Si la carpeta no existe, la crea
                if not existing_folder:
                    self.env["drive_stage"].create(
                        {
                            "folder_name": folder["title"],
                            "drive_id": self.id,  # Asegúrate de que `drive_id` corresponde al registro actual
                            "folder_id": folder["id"],
                            "folder_guardar_id": folder_id,
                        }
                    )
                    _logger.info(
                        f"Created new folder record in Odoo: {folder['title']}"
                    )

        except Exception as e:
            _logger.error(f"Error retrieving folders from Google Drive: {e}")

    progress = fields.Float(string="Progreso (%)", required=True, default=0.0)

    def create_subcarpetas(self):
        """Inicia la creación de subcarpetas en un hilo separado y retorna la notificación inicial."""
        _logger.warning(
            "create_subcarpetas LLAMADO — record id=%s db=%s uid=%s",
            self.id,
            self.env.cr.dbname,
            self.env.uid,
        )
        start_time = time.time()  # Inicio del contador de tiempo

        # Crear y comenzar el hilo
        thread = threading.Thread(
            target=self._thread_create_subcarpetas,
            args=(self.env.cr.dbname, self.env.uid, self.env.context),
        )
        thread.daemon = True  # El hilo se cerrará cuando el programa principal termine
        thread.start()

        # Fin del contador de tiempo
        elapsed_time = time.time() - start_time
        print(f"create_subcarpetas tomó {elapsed_time:.2f} segundos en iniciarse")

        # Retornar notificación inicial
        return self._send_notification(
            title="Proceso iniciado",
            message="Se ha iniciado la creación de carpetas...",
            notif_type="info",
            sticky=False,
        )

    def _thread_create_subcarpetas(self, dbname, uid, context):
        """Ejecuta la lógica de creación de subcarpetas en un nuevo hilo."""
        import traceback as _tb
        import logging as _logging

        _tlog = _logging.getLogger(__name__)
        _tlog.warning(
            "_thread_create_subcarpetas INICIADO — db=%s uid=%s record=%s",
            dbname,
            uid,
            self.id,
        )
        start_time = time.time()
        carpetas_creadas = 0
        carpetas_existentes = 0

        try:
            registry = self.pool  # self.env.registry — no depende del cursor
            record_id = self.id

            # Paso 1: crear carpetas y commit en su propia transacción
            with registry.cursor() as new_cr:
                env = api.Environment(new_cr, uid, context)
                self_new = env[self._name].browse(record_id)
                _tlog.warning(
                    "_thread_create_subcarpetas — llamando _create_subcarpetas_logic"
                )
                carpetas_creadas, carpetas_existentes = (
                    self_new._create_subcarpetas_logic()
                )
                new_cr.commit()
                _tlog.warning(
                    "_thread_create_subcarpetas — COMMIT OK creadas=%s existentes=%s",
                    carpetas_creadas,
                    carpetas_existentes,
                )

        except Exception:
            _tlog.warning(
                "ERROR en _thread_create_subcarpetas (creación):\n%s", _tb.format_exc()
            )
            return

        # Paso 2: notificar en transacción separada — fallo aquí no afecta las carpetas ya guardadas
        try:
            with self.pool.cursor() as notify_cr:
                env_notify = api.Environment(notify_cr, uid, context)
                self_notify = env_notify[self._name].browse(self.id)
                self_notify._bus_notify_carpetas(
                    carpetas_creadas, carpetas_existentes, uid
                )
                notify_cr.commit()
        except Exception:
            print(
                f"Aviso: notificación bus falló (carpetas ya guardadas):\n{_tb.format_exc()}"
            )

        print(
            f"_thread_create_subcarpetas TOTAL {time.time() - start_time:.2f} segundos"
        )

    def _create_subcarpetas_logic(self):
        start_time = time.time()

        # Mapeo nombre numerado → nombres legacy que deben migrarse a ese nombre
        _SUBCARPETAS_MAP = [
            ("1. CARPETA PRINCIPAL", ["CARPETA PRINCIPAL"]),
            ("2. COMUNICACIONES", ["COMUNICACIONES"]),
            ("3. CONTRATOS PS", ["CONTRATOS PS"]),
            ("4. INFORMES", ["INFORMES"]),
        ]
        # ACTAS ya no se crea; los registros existentes se ocultan en la vista

        subcarpetas_nuevos = [nuevo for nuevo, _ in _SUBCARPETAS_MAP]
        todos_los_nombres = subcarpetas_nuevos + [
            viejo for _, viejos in _SUBCARPETAS_MAP for viejo in viejos
        ]

        drive_stage_obj = self.env["drive_stage"]

        # Busca carpetas existentes con nombres nuevos o legacy
        existing_folders = drive_stage_obj.search(
            [
                ("proyecto_relacionado", "=", self.proyecto_relacionado.id),
                ("folder_name", "in", todos_los_nombres),
                ("drive_id", "=", self.id),
                ("folder_guardar_id", "=", self.folder_guardar_id),
            ]
        )

        # Renombrar carpetas con nombres legacy al nombre numerado y registrar las ya cubiertas
        existing_numbered_names = set()
        for folder in existing_folders:
            for nuevo, viejos in _SUBCARPETAS_MAP:
                if folder.folder_name in viejos:
                    folder.write({"folder_name": nuevo})
                    existing_numbered_names.add(nuevo)
                    break
                elif folder.folder_name == nuevo:
                    existing_numbered_names.add(nuevo)
                    break

        # Crear solo las carpetas numeradas que aún no existen
        new_subcarpetas = [
            {
                "folder_name": nuevo,
                "proyecto_relacionado": self.proyecto_relacionado.id,
                "drive_id": self.id,
                "folder_guardar_id": self.folder_guardar_id,
            }
            for nuevo in subcarpetas_nuevos
            if nuevo not in existing_numbered_names
        ]

        carpetas_creadas = 0

        if new_subcarpetas:
            created_folders = drive_stage_obj.create(new_subcarpetas)

            for folder in created_folders:
                result = folder.create_drive_folder()
                if result:
                    print(f"Subcarpeta '{folder.folder_name}' creada en Google Drive")
                    carpetas_creadas += 1
                else:
                    print(
                        f"Error al crear la subcarpeta '{folder.folder_name}' en Google Drive"
                    )

        carpetas_existentes = len(existing_folders)
        elapsed_time = time.time() - start_time
        print(f"_create_subcarpetas_logic tomó {elapsed_time:.2f} segundos")

        return carpetas_creadas, carpetas_existentes

    def _bus_notify_carpetas(self, carpetas_creadas, carpetas_existentes, uid):
        """Envía la notificación de resultado al usuario vía bus (funciona desde hilos)."""
        message = f"Se han creado {carpetas_creadas} carpetas nuevas."
        if carpetas_existentes > 0:
            message += f" {carpetas_existentes} carpetas ya existían."
        partner = self.env["res.users"].browse(uid).partner_id
        self.env["bus.bus"]._sendone(
            partner,
            "display_notification",
            {
                "title": "Proceso completado",
                "message": message,
                "type": "success",
                "sticky": False,
            },
        )

    def _send_notification(self, title, message, notif_type, sticky):
        """Envía una notificación al cliente."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notif_type,
                "sticky": sticky,
            },
        }

    def unlink(self):
        self._log_trace(self, "unlink")
        for record in self:
            # Eliminar registros relacionados en 'drive_stage'
            related_drives = self.env["drive_stage"].search(
                [("drive_id", "=", record.id)]
            )
            related_drives.unlink()

            if record.folder_guardar_id:
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()

                try:
                    folder = drive.CreateFile({"id": record.folder_guardar_id})
                    folder.Trash()  # Mover la carpeta a la papelera
                    print(
                        f"Folder moved to trash in Google Drive: {record.folder_guardar_id}"
                    )
                except Exception as e:
                    print(f"Error moving folder to trash in Google Drive: {e}")

        return super(DriveFolder, self).unlink()


class DriveMoveToFolderWizard(models.TransientModel):
    _name = "drive.move.to.folder.wizard"
    _description = "Mover"

    folder_id = fields.Selection(
        selection="_get_folder_options", string="Ubicación actual:"
    )

    def _get_folder_options(self):
        # Obtener la instancia de autenticación de Google Drive
        drive_auth = (
            GoogleDriveAuth.get_instance()
        )  # Obtener la instancia única de GoogleDriveAuth
        drive = (
            drive_auth.get_drive()
        )  # Usar el método para obtener la instancia de Google Drive autenticada

        # Calcular la fecha de hace 7 días
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        # Función para listar carpetas en el tercer nivel de profundidad
        def list_folders_at_depth(folder_id, depth=0):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            if depth == 2:  # Solo listamos carpetas en el tercer nivel de profundidad
                for folder in folder_list:
                    options.append((folder["id"], folder["title"]))
            elif depth < 2:  # Solo profundizamos si estamos en niveles menos profundos
                for folder in folder_list:
                    options.extend(list_folders_at_depth(folder["id"], depth + 1))
            return options

        # Listar carpetas desde la raíz ('root') para obtener las que están en el tercer nivel
        third_level_folders = list_folders_at_depth("root", 0)

        return third_level_folders

    def move_to_folder(self):
        drive_record = self.env["drive"].browse(self._context.get("default_drive_id"))
        new_folder_id = self.folder_id
        try:
            drive_record.move_in_drive(new_folder_id)
        except UserError as e:
            raise UserError(e)
        except Exception as e:
            raise UserError(f"Error moving file to selected folder: {e}")

        return {"type": "ir.actions.act_window_close"}


class DriveDocument(models.Model):
    _name = "drive.document"
    _description = "Documentos en Google Drive"

    name = fields.Char("Nombre del Documento")
    drive_file_id = fields.Char("ID del archivo en Google Drive")
    drive_id = fields.Many2one("drive", string="Drive", help="El Drive relacionado")
    permissions_info = fields.Text(
        "Permissions Info", readonly=True, related="drive_id.permissions_info"
    )

    permission_email = fields.Char(
        "Email", help="Email del usuario al que se le va a dar acceso"
    )
    permission_role = fields.Selection(
        [
            ("reader", "Lector"),
            ("commenter", "Comentador"),
            ("writer", "Editor"),
        ],
        string="Rol",
        default="reader",
        help="Tipo de permiso que se otorgará",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Buscar usuario",
    )
    project_user_ids = fields.Many2many(
        "res.users",
        "drive_doc_proj_users_rel",
        "doc_id",
        "user_id",
        string="Usuarios del proyecto",
    )

    @api.onchange("user_id")
    def _onchange_user_id(self):
        if self.user_id and self.user_id.email:
            self.permission_email = self.user_id.email

    def _get_drive_auth(self):
        _config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"
        )
        client_secrets_path = os.path.join(_config_dir, "client_secrets.json")

        gauth = GoogleAuth()
        gauth.LoadClientConfigFile(client_secrets_path)

        try:
            credentials_path = os.path.join(_config_dir, "credentials_module.json")
            gauth.LoadCredentialsFile(credentials_path)
        except Exception as e:
            print(f"Error cargando el archivo de credenciales: {e}")
            gauth.LocalWebserverAuth()
            gauth.SaveCredentialsFile(credentials_path)

        return gauth

    def set_drive(self):
        for record in self:
            if (
                record.user_id
                and record.project_user_ids
                and record.user_id not in record.project_user_ids
            ):
                raise UserError(
                    "Solo puedes compartir con usuarios permitidos en este proyecto."
                )
            if not record.permission_email:
                raise UserError(
                    "Selecciona un usuario del proyecto o escribe un correo electrónico."
                )
            if record.drive_id:
                # Actualizar el registro de Drive con el correo y el rol
                drive_record = self.env["drive"].browse(record.drive_id.id)
                if drive_record:
                    drive_record.write(
                        {
                            "permission_email": record.permission_email,
                            "permission_role": record.permission_role,
                        }
                    )
                    # Llamar al método `set_drive_permission` para establecer permisos
                    drive_record.set_drive_permission()
                else:
                    raise UserError("El registro de Drive relacionado no se encontró.")
            else:
                raise UserError("El registro de Drive relacionado no está definido.")


class DriveFileTrace(models.Model):
    _name = "drive.file.trace"
    _description = "Trazabilidad de archivos Drive"

    drive_file_id = fields.Many2one("drive.file", string="Archivo de Drive")
    action_type = fields.Selection(
        [
            ("create", "Create"),
            ("update", "Update"),
            ("delete", "Delete"),
            ("download", "Download"),
        ],
        string="Acción",
    )
    user_id = fields.Many2one("res.users", string="Usuario")
    notes = fields.Text(string="Notas")


class DriveFile(models.Model):
    _name = "drive.file"
    _description = "Carpetas"
    _order = "sequence, id"

    parent_folder_id = fields.Many2one(
        "drive.file", string="Carpeta Padre", index=True, ondelete="set null"
    )
    sub_folder_ids = fields.One2many(
        "drive.file", "parent_folder_id", string="Subcarpetas"
    )

    ################################Nuevo Campo informes ##############################
    informes_id = fields.Many2one("informes_drive", string="Informes")
    comunicaciones_id = fields.Many2one("comunicaciones_drive", string="Comunicaciones")

    descrpcion_info = fields.Text(string="Descripción")
    fecha = fields.Date(string="Fecha")
    invisible_todo = fields.Boolean(default=False)
    can_delete_from_form = fields.Boolean(
        string="Puede eliminar desde formulario",
        compute="_compute_can_delete_from_form",
    )
    invetrio_ids = fields.One2many("documento_archivo", "inventario", string="Files")

    # Cambiar los campos a Char para poder almacenar el ID de las carpetas
    folder_id2 = fields.Char("Google Drive Folder ID 2")
    folder_guardar_id = fields.Char("Google Drive Folder Guardar ID")

    file_ids = fields.One2many("drive", "stage2_id", string="Files")
    proyecto_relacionado = fields.Many2one(
        "contratacion", string="Proyecto relacionado"
    )
    name = fields.Char(string="Stage Name", related="folder_name")
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one(
        "res.users",
        string="Creador",
        default=lambda self: self.env.user,
        track_visibility="always",
    )
    fch_creacion = fields.Datetime(
        string="Fecha creacion",
        readonly=True,
        copy=False,
        default=lambda self: fields.Datetime.now(),
    )
    folder_name = fields.Char(string="Nombre de la carpeta")
    drive_id = fields.Many2one("drive_stage", string="Carpetas")
    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)
    hiden = fields.Char(string="hiden")
    buscador = fields.Many2one(
        "otrosi",
        string="Proyecto relacionado",
        default=lambda self: self.env["otrosi"].search([], order="id asc", limit=1).id
        or False,
    )

    contros_fuv_ids = fields.One2many(
        "contrataciones_fuv", "drive_file_id", string="Contratos"
    )

    select_persona = fields.Selection(
        [
            ("persona_natural", "Persona natural"),
            ("persona_juridica", "Persona jurídica"),
            ("contrato_laboral", "Contrato laboral"),
            ("bienes_y_sevicios", "Bienes y servicios"),
            ("anticipos", "Anticipos y Legalizaciones"),
        ],
        string="Tipo de Persona",
    )

    tipo_persona = fields.Selection(
        [
            ("persona_natural", "Persona natural"),
            ("persona_juridica", "Persona jurídica"),
            ("contrato_laboral", "Contrato laboral"),
        ],
        string="Tipo de Persona",
        related="contros_fuv_ids.select_persona",
    )

    def action_actualizar_select_persona(self):
        for record in self:
            if record.tipo_persona:
                record.select_persona = record.tipo_persona

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    def action_view_history(self):
        return {
            "name": "Trazabilidad",
            "view_mode": "tree",
            "res_model": "drive.file.trace",
            "type": "ir.actions.act_window",
            "target": "current",
        }

    @api.model
    def create(self, vals):
        # Crear el registro
        record = super(DriveFile, self).create(vals)

        # Crear un registro de trazabilidad
        self.env["drive.file.trace"].create(
            {
                "drive_file_id": record.id,
                "action_type": "create",
                "user_id": self.env.user.id,
                "notes": _("Archivo creado: %s") % record.folder_name,
            }
        )

        return record

    def write(self, vals):
        result = super(DriveFile, self).write(vals)

        # Crear un registro de trazabilidad si se actualizan ciertos campos clave
        if "folder_name" in vals or "folder_id2" in vals or "folder_guardar_id" in vals:
            self.env["drive.file.trace"].create(
                {
                    "drive_file_id": self.id,
                    "action_type": "update",
                    "user_id": self.env.user.id,
                    "notes": _("Archivo actualizado: %s") % self.folder_name,
                }
            )

        return result

    def _crear_carpeta_informes(self):
        """Crea la subcarpeta 'Informes' en Google Drive y sus registros en Odoo (idempotente)."""
        if not self.folder_id2:
            return
        try:
            # 1. Comprobar si el registro drive ya existe en Odoo (evita llamada a Drive API)
            existing_rec = self.env["drive"].search(
                [
                    ("stage2_id", "=", self.id),
                    ("is_folder", "=", True),
                    ("file_name", "=", "Informes"),
                ],
                limit=1,
            )
            if existing_rec and existing_rec.folder_id:
                # Ya está completo: solo aseguramos el nodo de workspace
                informes_gd_id = existing_rec.folder_id
            else:
                # Necesitamos verificar/crear en Google Drive
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()
                query = (
                    f"'{self.folder_id2}' in parents and title = 'Informes' "
                    f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                )
                existing_list = drive.ListFile({"q": query}).GetList()
                if existing_list:
                    informes_gd_id = existing_list[0]["id"]
                else:
                    informes_folder = drive.CreateFile(
                        {
                            "title": "Informes",
                            "mimeType": "application/vnd.google-apps.folder",
                            "parents": [{"id": self.folder_id2}],
                        }
                    )
                    informes_folder.Upload()
                    informes_gd_id = informes_folder["id"]
                    _logger.info(
                        f"Subcarpeta 'Informes' creada en Drive con ID: {informes_gd_id}"
                    )

                # Crear registro drive con is_folder=True si no existe
                if not existing_rec:
                    self.env["drive"].create(
                        {
                            "file_name": "Informes",
                            "is_folder": True,
                            "stage2_id": self.id,
                            "folder_id": informes_gd_id,
                        }
                    )

            # 2. Crear nodo drive.browser.workspace si el browser del proyecto existe
            proyecto = self.drive_id.proyecto_relacionado if self.drive_id else False
            if proyecto:
                browser = self.env["drive.browser"].search(
                    [("project_id", "=", proyecto.id)], limit=1
                )
                if browser:
                    parent_ws = self.env["drive.browser.workspace"].search(
                        [
                            ("browser_id", "=", browser.id),
                            ("drive_file_id", "=", self.id),
                        ],
                        limit=1,
                    )
                    if not parent_ws:
                        parent_ws = self.env["drive.browser.workspace"].search(
                            [
                                ("browser_id", "=", browser.id),
                                ("folder_google_id", "=", self.folder_id2),
                            ],
                            limit=1,
                        )
                    if parent_ws:
                        existing_ws = self.env["drive.browser.workspace"].search(
                            [
                                ("browser_id", "=", browser.id),
                                ("folder_google_id", "=", informes_gd_id),
                            ],
                            limit=1,
                        )
                        if not existing_ws:
                            self.env["drive.browser.workspace"].create(
                                {
                                    "name": "Informes",
                                    "browser_id": browser.id,
                                    "folder_google_id": informes_gd_id,
                                    "parent_folder_google_id": self.folder_id2,
                                    "parent_id": parent_ws.id,
                                }
                            )
        except Exception as e:
            _logger.error(f"Error creando subcarpeta Informes: {e}")

    @api.model
    def apply_renumber_map(self, gid_name_map):
        """Recibe {google_folder_id: new_name} y actualiza folder_name donde folder_id2 coincida."""
        if not gid_name_map:
            return
        for rec in self.search([("folder_id2", "in", list(gid_name_map.keys()))]):
            new_name = gid_name_map.get(rec.folder_id2)
            if new_name and rec.folder_name != new_name:
                rec.write({"folder_name": new_name})

    def download_drive_folder(self):
        if not self.folder_id2:
            raise UserError("Folder ID is not defined.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre de la carpeta
        folder = drive.CreateFile({"id": self.folder_id2})
        folder.FetchMetadata(fields="title")
        folder_name = folder["title"]

        # Función para descargar archivos en una carpeta
        def download_files_in_folder(folder_id2, zip_obj, path=""):
            query = f"'{folder_id2}' in parents and trashed=false"
            file_list = drive.ListFile({"q": query}).GetList()

            for file in file_list:
                if file["mimeType"] == "application/vnd.google-apps.folder":
                    folder_name = file["title"]
                    new_path = os.path.join(path, folder_name)
                    download_files_in_folder(file["id"], zip_obj, new_path)
                else:
                    # Guardar archivo binario
                    file_path = "/tmp/file_to_download"
                    file.GetContentFile(
                        file_path
                    )  # Descargar archivo al sistema temporal
                    with open(file_path, "rb") as f:
                        file_data = f.read()
                    zip_path = os.path.join(path, file["title"])
                    zip_obj.writestr(zip_path, file_data)

        # Crear un archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            download_files_in_folder(self.folder_id2, zip_file)

        # Preparar el archivo para descarga
        zip_buffer.seek(0)
        zip_filename = f"{folder_name}.zip"
        attachment = self.env["ir.attachment"]._drive_create_temp_download(
            {
                "name": zip_filename,
                "datas": base64.b64encode(zip_buffer.read()),
                "type": "binary",
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/zip",
            }
        )
        self.env["drive.file.trace"].sudo().create(
            {
                "drive_file_id": self.id,
                "action_type": "download",
                "user_id": self.env.user.id,
                "notes": _("Carpeta descargada: %s") % zip_filename,
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    # Función para obtener las opciones de carpeta, solo si es necesario
    def _get_folder_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        def list_folders(folder_id2):
            query = f"'{folder_id2}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                options.extend(list_folders(folder["id"]))
            return options

        # Obtener todas las carpetas dentro de la carpeta root
        return list_folders("root")

    def _get_carpeta_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        def list_folders(folder_guardar_id):
            query = f"'{folder_guardar_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                options.extend(list_folders(folder["id"]))
            return options

        # Obtener todas las carpetas dentro de la carpeta root
        return list_folders("root")

    def create_drive_folder(self):
        """
        Crea la carpeta física en Drive y actualiza el sidebar correspondiente.
        """
        # Validar carpeta padre
        parent_folder = self.folder_guardar_id or self.folder_id2
        if not parent_folder:
            raise UserError(
                "No hay carpeta seleccionada para guardar la nueva carpeta."
            )

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        import re as _re_local

        if not _re_local.match(r"^\d", self.folder_name or ""):
            prefix = _get_next_prefix_for_folder(drive, parent_folder)
            folder_title = (
                f"{prefix} {self.folder_name}" if prefix else self.folder_name
            )
        else:
            folder_title = self.folder_name

        folder_metadata = {
            "title": folder_title,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": parent_folder}],
        }

        try:
            start_time = time.time()
            timeout = 10

            # 1. Acción Principal: Crear en Drive
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            # 2. Validación de Timeout
            if (time.time() - start_time) > timeout:
                _logger.error("⏱ Timeout alcanzado al crear la carpeta en Google Drive")
                return self._return_notification(
                    "Error", "Timeout alcanzado.", "danger", True
                )

            # 3. Guardar ID y nombre numerado
            self.write({"folder_id2": folder["id"], "folder_name": folder_title})

            # 4. Actualizar UI (AQUÍ ESTÁ EL CAMBIO IMPORTANTE)

            # Si es de INFORMES
            if self.informes_id:
                self._update_informes_workspace()

            # Si es de COMUNICACIONES (AGREGAR ESTO)
            if self.comunicaciones_id:
                self._update_comunicaciones_workspace()

            return self._return_notification(
                "Éxito", "Carpeta creada correctamente.", "success", False, close=True
            )

        except Exception as e:
            _logger.error(f"❌ Error creando la carpeta en Google Drive: {e}")
            raise UserError(f"Error creando la carpeta en Google Drive: {e}")

    def _update_comunicaciones_workspace(self):
        """
        Actualiza el árbol del sidebar en Comunicaciones.
        """
        Workspace = self.env["comunicaciones.workspace"]

        # Intentamos obtener el padre desde el contexto
        parent_workspace_id = self.env.context.get("default_parent_workspace_id")

        # Lógica de respaldo: Si no vino en el contexto, lo buscamos manualmente
        if not parent_workspace_id and (self.folder_guardar_id or self.folder_id2):
            parent_folder_id = self.folder_guardar_id or self.folder_id2
            drive_padre = self.env["drive.file"].search(
                [("folder_id2", "=", parent_folder_id)], limit=1
            )

            if drive_padre:
                ws_padre = Workspace.search(
                    [
                        ("drive_file_id", "=", drive_padre.id),
                        ("comunicaciones_id", "=", self.comunicaciones_id.id),
                    ],
                    limit=1,
                )
                if ws_padre:
                    parent_workspace_id = ws_padre.id

        # Crear el nodo visual si no existe
        domain = [
            ("drive_file_id", "=", self.id),
            ("comunicaciones_id", "=", self.comunicaciones_id.id),
        ]
        if not Workspace.search(domain, limit=1):
            Workspace.create(
                {
                    "name": self.folder_name,
                    "comunicaciones_id": self.comunicaciones_id.id,
                    "drive_file_id": self.id,
                    "parent_id": parent_workspace_id,  # Esto crea la indentación visual
                }
            )

    def _update_informes_workspace(self):
        """
        Actualiza el árbol del sidebar en Odoo.
        RESPONSABILIDAD: Solo lógica de base de datos interna de Odoo (Workspaces).
        """
        Workspace = self.env["informes.workspace"]

        # Intentamos obtener el padre desde el contexto (pasado desde informes.py)
        parent_workspace_id = self.env.context.get("default_parent_workspace_id")

        # Lógica de respaldo: Si no vino en el contexto, lo buscamos manualmente (Tu lógica original mejorada)
        if not parent_workspace_id and (self.folder_guardar_id or self.folder_id2):
            parent_folder_id = self.folder_guardar_id or self.folder_id2
            drive_padre = self.env["drive.file"].search(
                [("folder_id2", "=", parent_folder_id)], limit=1
            )

            if drive_padre:
                ws_padre = Workspace.search(
                    [
                        ("drive_file_id", "=", drive_padre.id),
                        ("informes_id", "=", self.informes_id.id),
                    ],
                    limit=1,
                )
                if ws_padre:
                    parent_workspace_id = ws_padre.id

        # Crear el nodo visual si no existe
        domain = [
            ("drive_file_id", "=", self.id),
            ("informes_id", "=", self.informes_id.id),
        ]
        if not Workspace.search(domain, limit=1):
            Workspace.create(
                {
                    "name": self.folder_name,
                    "informes_id": self.informes_id.id,
                    "drive_file_id": self.id,
                    "parent_id": parent_workspace_id,  # Esto crea la indentación visual
                }
            )

    def _return_notification(self, title, message, type, sticky, close=False):
        """Helper simple para reducir ruido en el método principal"""
        action = {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": type,
                "sticky": sticky,
            },
        }
        if close:
            action["params"]["next"] = {"type": "ir.actions.act_window_close"}
        return action

    def action_update_folder_info(self):
        self.ensure_one()

        # 1. ACTUALIZAR EN GOOGLE DRIVE
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        drive_folder_id = self.folder_id2

        if drive_folder_id:
            try:
                drive_file = drive.CreateFile({"id": drive_folder_id})
                drive_file.FetchMetadata(fields="title, id")
                nuevo_nombre = self.folder_name
                if drive_file["title"] != nuevo_nombre:
                    drive_file["title"] = nuevo_nombre
                    drive_file.Upload()
            except Exception:
                pass

        # 2. ACTUALIZAR EN ODOO (Informes y Comunicaciones)
        # Esto es lo que garantiza que el sidebar se actualice
        informe = self.env["informes.workspace"].search(
            [("drive_file_id", "=", self.id)], limit=1
        )
        if informe:
            informe.name = self.folder_name

        comunicacion = self.env["comunicaciones.workspace"].search(
            [("drive_file_id", "=", self.id)], limit=1
        )
        if comunicacion:
            comunicacion.name = self.folder_name

        # 3. CERRAR VENTANA (CORREGIDO)
        # Usamos act_window_close. Al cerrarse, Odoo refresca la vista padre
        # porque detecta que hubo escrituras (writes) en los registros.
        return {"type": "ir.actions.act_window_close"}

    def action_delete_from_form(self):
        """
        Elimina la carpeta de Drive y el registro asociado en Odoo (Informe o Comunicación).
        """
        self.ensure_one()

        if (
            self._is_informes_comunicaciones_root_folder()
            and not self.env.user.has_group("Matriz.group_soporte")
        ):
            raise UserError(
                _(
                    "La carpeta raíz de Informes/Comunicaciones solo puede moverse a papelera por soporte."
                )
            )

        # 1. ELIMINAR DE GOOGLE DRIVE (Nube)
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        if self.folder_id2:
            try:
                # Opción A: Mover a la papelera (Más seguro)
                file_to_del = drive.CreateFile({"id": self.folder_id2})
                file_to_del.Trash()

                # Opción B: Eliminar permanentemente (Si prefieres esto, descomenta)
                # file_to_del.Delete()
            except Exception as e:
                # Logueamos pero no detenemos el proceso en Odoo
                pass

        # 2. ELIMINAR EL REGISTRO EN ODOO (Padre e Hijo)

        # Buscamos si pertenece a Informes y lo borramos
        informe = self.env["informes.workspace"].search(
            [("drive_file_id", "=", self.id)], limit=1
        )
        if informe:
            informe.unlink()  # Al borrar el padre, usualmente se borra el link

        # Buscamos si pertenece a Comunicaciones y lo borramos
        comunicacion = self.env["comunicaciones.workspace"].search(
            [("drive_file_id", "=", self.id)], limit=1
        )
        if comunicacion:
            comunicacion.unlink()

        # Por si acaso quedó el registro de drive.file vivo (si no hubo cascade delete), lo rematamos
        if self.exists():
            self.unlink()

        # 3. CERRAR MODAL
        return {"type": "ir.actions.act_window_close"}

    def _is_informes_comunicaciones_root_folder(self):
        self.ensure_one()
        folder_name = " ".join(
            (self.folder_name or self.name or "").strip().upper().split()
        )
        return not self.parent_folder_id and folder_name in {
            "2. COMUNICACIONES",
            "COMUNICACIONES",
            "4. INFORMES",
            "INFORMES",
        }

    def _compute_can_delete_from_form(self):
        soporte = self.env.user.has_group("Matriz.group_soporte")
        for record in self:
            record.can_delete_from_form = (
                soporte or not record._is_informes_comunicaciones_root_folder()
            )

    _drive_cache = {}

    def action_open_drive(self):
        # Crear subcarpeta Informes si aún no existe (idempotente, rápido si ya fue creada)
        if self.proyecto_relacionado.suspension_state != "suspended":
            self._crear_carpeta_informes()

        if self.id in self.env.context.get("drive_cache", {}):
            drive_records = self.env.context["drive_cache"][self.id]
            _logger.info(
                f"Usando caché para drive ID {self.id}, drive records: {drive_records}"
            )
        else:
            drive_records = self.env["drive"].search([("stage2_id", "=", self.id)])
            drive_cache = self.env.context.get("drive_cache", {})
            drive_cache[self.id] = drive_records
            self.env.context = dict(self.env.context, drive_cache=drive_cache)
            _logger.info(
                f"Se encontraron {len(drive_records)} drives para el ID {self.id}"
            )

        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "drive",
            "view_mode": "kanban,tree,form",
            "target": "current",
            "domain": [("stage2_id", "=", self.id), ("is_folder", "=", False)],
            "context": {
                "default_stage2_id": self.id,
                "default_folder_id": self.folder_id2,
            },
        }

    # @api.model
    # def list_files_in_folder(self):
    #     with api.Environment.manage():
    #         new_cr = self.pool.cursor()
    #         self = self.with_env(self.env(cr=new_cr))
    #         try:
    #             self._list_files_in_folder_impl()
    #             new_cr.commit()
    #         except Exception as e:
    #             new_cr.rollback()
    #             _logger.error(f"Error in list_files_in_folder: {e}")
    #         finally:
    #             new_cr.close()

    def list_files_in_folder(self):
        thread = threading.Thread(target=self._thread_list_files)
        thread.daemon = True
        thread.start()

    def _thread_list_files(self):
        try:
            # Crear un nuevo cursor para este hilo
            new_cr = self.env.registry.cursor()
            env = api.Environment(new_cr, self.env.uid, self.env.context)

            # Obtener una nueva instancia del registro actual
            self_with_new_env = self.with_env(env)

            try:
                self_with_new_env._list_files_impl()
                new_cr.commit()  # Commit los cambios
            except Exception as e:
                new_cr.rollback()  # Rollback en caso de error
                logging.error(f"Error en el hilo de listado de archivos: {str(e)}")
            finally:
                new_cr.close()  # Siempre cerrar el cursor
        except Exception as e:
            logging.error(f"Error al crear el ambiente en el hilo: {str(e)}")

    def _list_files_impl(self):
        folder_id = self.folder_id2  # Obtener el ID de la carpeta seleccionada
        if not folder_id:
            logging.error("Folder ID is not defined.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            # Listar archivos en la carpeta de Google Drive especificada
            file_list = drive.ListFile(
                {"q": f"'{folder_id}' in parents and trashed=false"}
            ).GetList()

            for file in file_list:
                try:
                    existing_file = self.env["drive"].search(
                        [
                            ("drive_file_id", "=", file["id"]),
                            ("stage2_id", "=", self.id),
                        ],
                        limit=1,
                    )

                    if existing_file:
                        # Actualizar solo si hay cambios en el nombre o URL del archivo
                        if (
                            existing_file.file_name != file["title"]
                            or existing_file.file_url != file["alternateLink"]
                        ):
                            existing_file.write(
                                {
                                    "file_name": file["title"],
                                    "file_url": file["alternateLink"],
                                    "folder_id": folder_id,
                                }
                            )
                            logging.info(
                                f"File updated in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                            )
                        else:
                            logging.info(
                                f"File already up-to-date in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                            )
                    else:
                        # Crear el archivo solo si no existe en Odoo
                        file.GetContentFile("/tmp/file_to_import")
                        with open("/tmp/file_to_import", "rb") as f:
                            file_data = f.read()

                        new_file = self.env["drive"].create(
                            {
                                "file_name": file["title"],
                                "file_url": file["alternateLink"],
                                "drive_file_id": file["id"],
                                "stage2_id": self.id,
                                "file_data": base64.b64encode(file_data),
                                "folder_id": folder_id,
                            }
                        )
                        logging.info(
                            f"New file created in Odoo: {new_file.file_name} (ID: {new_file.id})"
                        )

                except Exception as e:
                    logging.error(f"Error processing file {file['title']}: {e}")

            # Asegurarse de no eliminar archivos existentes
            existing_files_in_odoo = self.env["drive"].search(
                [("stage2_id", "=", self.id)]
            )
            drive_file_ids = [file["id"] for file in file_list]

            for odoo_file in existing_files_in_odoo:
                if odoo_file.drive_file_id not in drive_file_ids:
                    logging.info(
                        f"File in Odoo but not in Drive (not deleting): {odoo_file.file_name} (ID: {odoo_file.id})"
                    )

        except Exception as e:
            logging.error(f"Error retrieving files from Google Drive: {e}")

    def unlink(self):
        for record in self:
            # Crear un registro de trazabilidad antes de eliminar
            self.env["drive.file.trace"].create(
                {
                    "drive_file_id": record.id,
                    "action_type": "delete",
                    "user_id": self.env.user.id,
                    "notes": _("Archivo eliminado: %s") % record.folder_name,
                }
            )

            # Eliminar registros relacionados en 'drive'
            related_drives = self.env["drive"].search([("stage2_id", "=", record.id)])
            related_drives.unlink()

            # Eliminar registros relacionados en 'drive.file'
            related_files = self.env["cuenta_de_cobros"].search(
                [("drive_id", "=", record.id)]
            )
            related_files.unlink()

            if record.folder_id2 and not self.env.context.get('sgr_carpeta_drive_en_papelera'):
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()

                try:
                    folder = drive.CreateFile({"id": record.folder_id2})
                    folder.Trash()  # Mover la carpeta a la papelera
                    _logger.info(
                        f"Folder moved to trash in Google Drive: {record.folder_id2}"
                    )
                except Exception as e:
                    _logger.error(f"Error moving folder to trash in Google Drive: {e}")

        return super(DriveFile, self).unlink()

    # Variable de cache para cuenta_de_cobros
    _cobros_cache = {}

    def cuenta_de_cobros(self):
        # Verificar si el resultado ya está en caché
        if self.id in self._cobros_cache:
            # Usar el cache si ya se ha buscado anteriormente
            cobros_records = self._cobros_cache[self.id]
            _logger.info(
                f"Usando caché para cuenta_de_cobros con drive ID {self.id}, registros: {cobros_records}"
            )
        else:
            # Si no está en el cache, hacer la búsqueda y almacenarla
            cobros_records = self.env["cuenta_de_cobros"].search(
                [("drive_id", "=", self.id)]
            )
            self._cobros_cache[self.id] = cobros_records
            _logger.info(
                f"Se encontraron {len(cobros_records)} registros para cuenta_de_cobros con drive ID {self.id}"
            )

        # Retornar la acción con los datos encontrados
        return {
            "name": self.name,
            "view_mode": "kanban,tree,form",
            "res_model": "cuenta_de_cobros",
            "type": "ir.actions.act_window",
            "target": "current",
            "domain": [("drive_id", "=", self.id)],
            "context": {
                "default_drive_id": self.id,
                "default_folder_id": self.folder_id2,
                "default_folder_guardar_id": self.folder_id2 or self.folder_guardar_id,
            },
        }

    # Variable de cache para otrosi_drive
    _otrosi_cache = {}

    def otrosi_drive(self):
        # Verificar si el resultado ya está en caché
        if self.id in self._otrosi_cache:
            # Usar el cache si ya se ha buscado anteriormente
            otrosi_records = self._otrosi_cache[self.id]
            _logger.info(
                f"Usando caché para otrosi_drive con drive ID {self.id}, registros: {otrosi_records}"
            )
        else:
            # Si no está en el cache, hacer la búsqueda y almacenarla
            otrosi_records = self.env["otrosi_drive"].search(
                [("drive_id", "=", self.id)]
            )
            self._otrosi_cache[self.id] = otrosi_records
            _logger.info(
                f"Se encontraron {len(otrosi_records)} registros para otrosi_drive con drive ID {self.id}"
            )

        # Retornar la acción con los datos encontrados
        return {
            "name": self.name,
            "view_mode": "kanban,tree,form",
            "res_model": "otrosi_drive",
            "type": "ir.actions.act_window",
            "target": "current",
            "domain": [("drive_id", "=", self.id)],
            "context": {
                "default_drive_id": self.id,
                "default_folder_id": self.folder_id2,
                "default_folder_guardar_id": self.folder_id2 or self.folder_guardar_id,
            },
        }


class CuentaDeCobrosTrace(models.Model):
    _name = "cuenta_de_cobros.trace"
    _description = "Trazabilidad de las acciones en CuentaDeCobros"

    cuenta_de_cobros_id = fields.Many2one("cuenta_de_cobros", string="Cuenta de Cobros")
    action = fields.Selection(
        [("create", "Creación"), ("write", "Actualización"), ("unlink", "Eliminación")],
        string="Acción",
    )
    user_id = fields.Many2one("res.users", string="Usuario")
    timestamp = fields.Datetime(string="Fecha y Hora", default=fields.Datetime.now)
    details = fields.Text(string="Detalles")


class CuentaDeCobrosTrace(models.Model):
    _name = "cuenta_de_cobros.trace"
    _description = "Trazabilidad de las acciones en CuentaDeCobros"

    cuenta_de_cobros_id = fields.Many2one("cuenta_de_cobros", string="Cuenta de Cobros")
    action = fields.Selection(
        [("create", "Creación"), ("write", "Actualización"), ("unlink", "Eliminación")],
        string="Acción",
    )
    user_id = fields.Many2one("res.users", string="Usuario")
    timestamp = fields.Datetime(string="Fecha y Hora", default=fields.Datetime.now)
    details = fields.Text(string="Detalles")


class CuentaDeCobros(models.Model):
    _name = "cuenta_de_cobros"
    _description = "Gestión de cuentas y carpetas en Google Drive"

    users_with_access = fields.Many2many(
        "res.users",
        string="Permitir acceso de usuarios al proyecto",
        related="contratistas_provedores.users_with_access",
        readonly=True,
    )
    create_uid = fields.Many2one(
        "res.users",
        string="Creador del proyecto",
        related="contratistas_provedores.create_uid",
        readonly=True,
    )
    creator_user_id = fields.Many2one(
        "res.users",
        string="Creador del proyecto (relacionado)",
        related="contratistas_provedores.creator_user_id",
        readonly=True,
    )

    name = fields.Char(string="Stage Name", related="folder_name")
    contratistas_provedores = fields.Many2one(
        "acta_entrega_documentos", string="acta_entrega_documentos"
    )
    folder_name = fields.Char(string="Nombre de la carpeta")
    fch_creacion = fields.Datetime(
        string="Fecha de Creación",
        readonly=True,
        default=lambda self: fields.Datetime.now(),
    )
    user_id = fields.Many2one(
        "res.users", string="Responsable", default=lambda self: self.env.user
    )
    drive_id = fields.Many2one("drive.file", string="Ubicación")
    otrosi_drive_id = fields.Many2one("otrosi_drive", string="Ubicación Otrosi")
    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)
    real_creator_id = fields.Many2one(
        "res.users",
        string="Creador real",
        compute="_compute_real_creator_cuenta_cobros",
        store=False,
    )

    @api.depends("contratistas_provedores", "drive_id", "user_id")
    def _compute_real_creator_cuenta_cobros(self):
        for rec in self:
            if rec.contratistas_provedores and rec.contratistas_provedores.create_uid:
                rec.real_creator_id = rec.contratistas_provedores.create_uid
            elif rec.drive_id and rec.drive_id.user_id:
                rec.real_creator_id = rec.drive_id.user_id
            else:
                rec.real_creator_id = rec.user_id or rec.create_uid

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    def action_view_history(self):
        return {
            "name": "Trazabilidad",
            "view_mode": "tree",
            "res_model": "cuenta_de_cobros.trace",
            "type": "ir.actions.act_window",
            "target": "current",
        }

    @api.model
    def create(self, vals):
        record = super(CuentaDeCobros, self).create(vals)
        self._log_trace(record.id, "create", "Registro creado")
        return record

    def write(self, vals):
        res = super(CuentaDeCobros, self).write(vals)
        for record in self:
            self._log_trace(record.id, "write", "Registro actualizado")
        return res

    def _log_trace(self, record_id, action, details):
        self.env["cuenta_de_cobros.trace"].create(
            {
                "cuenta_de_cobros_id": record_id,
                "action": action,
                "user_id": self.env.user.id,
                "details": details,
            }
        )

    def download_drive_folder(self):
        if not self.folder_id:
            raise UserError("Folder ID is not defined.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre de la carpeta
        folder = drive.CreateFile({"id": self.folder_id})
        folder.FetchMetadata(fields="title")
        folder_name = folder["title"]

        # Función para descargar archivos en una carpeta
        def download_files_in_folder(folder_id, zip_obj, path=""):
            query = f"'{folder_id}' in parents and trashed=false"
            file_list = drive.ListFile({"q": query}).GetList()

            for file in file_list:
                if file["mimeType"] == "application/vnd.google-apps.folder":
                    folder_name = file["title"]
                    new_path = os.path.join(path, folder_name)
                    download_files_in_folder(file["id"], zip_obj, new_path)
                else:
                    # Guardar archivo binario
                    file_path = "/tmp/file_to_download"
                    file.GetContentFile(
                        file_path
                    )  # Descargar archivo al sistema temporal
                    with open(file_path, "rb") as f:
                        file_data = f.read()
                    zip_path = os.path.join(path, file["title"])
                    zip_obj.writestr(zip_path, file_data)

        # Crear un archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            download_files_in_folder(self.folder_id, zip_file)

        # Preparar el archivo para descarga
        zip_buffer.seek(0)
        zip_filename = f"{folder_name}.zip"
        attachment = self.env["ir.attachment"]._drive_create_temp_download(
            {
                "name": zip_filename,
                "datas": base64.b64encode(zip_buffer.read()),
                "type": "binary",
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/zip",
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def upload_file_to_drive(self, file_path):
        """Uploads a file to the specified Google Drive folder."""
        if not self.folder_id:
            raise UserError("No hay carpeta seleccionada para subir el archivo.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            file_metadata = {
                "title": os.path.basename(file_path),
                "parents": [{"id": self.folder_id}],
            }
            file_to_upload = drive.CreateFile(file_metadata)
            file_to_upload.SetContentFile(file_path)
            file_to_upload.Upload()

            return {
                "name": file_to_upload["title"],
                "url": file_to_upload["alternateLink"],
            }
        except Exception as e:
            raise UserError(f"Error subiendo el archivo a Google Drive: {e}")

    # Actualizamos los campos a Char
    folder_id = fields.Char(
        "Google Drive Folder ID"
    )  # Ahora es un campo Char, no un Selection
    folder_guardar_id = fields.Char(
        "Google Drive Folder ID para Guardar"
    )  # También cambiamos este campo a Char

    # Función para obtener las carpetas (optimizada)
    def _get_folder_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        # Obtener la instancia de autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Calcular la fecha de hace 7 días
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        # Función para listar carpetas de manera recursiva
        def list_folders(folder_id):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                # Recursivamente listar carpetas dentro de esta carpeta
                options.extend(list_folders(folder["id"]))
            return options

        # Listar carpetas desde la raíz ('root')
        root_folders = list_folders("root")

        return root_folders

    # Función para obtener las carpetas (sin incluir la restricción de fecha)
    def _get_carpeta_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        # Obtener la instancia de autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Listar carpetas desde la raíz ('root')
        folder_list = drive.ListFile(
            {
                "q": "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            }
        ).GetList()

        return [(folder["id"], folder["title"]) for folder in folder_list]

    def create_drive_folder(self):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Verificar si existe la carpeta "Contratistas" en Google Drive
        contratistas_folder_id = None
        try:
            query = "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and title='Contratistas'"
            folders = drive.ListFile({"q": query}).GetList()
            if folders:
                contratistas_folder_id = folders[0]["id"]
            else:
                # Crear la carpeta "Contratistas" si no existe
                contratistas_folder_metadata = {
                    "title": "Contratistas",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [{"id": "root"}],
                }
                contratistas_folder = drive.CreateFile(contratistas_folder_metadata)
                contratistas_folder.Upload()
                contratistas_folder_id = contratistas_folder["id"]
        except Exception as e:
            print(f"Error al verificar o crear la carpeta 'Contratistas': {e}")
            return False

        # Crear la carpeta específica dentro de "Contratistas"
        folder_metadata = {
            "title": self.folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": contratistas_folder_id}],
        }

        try:
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            # Actualizar los campos en el modelo con los IDs de las carpetas
            self.write(
                {
                    "folder_id": folder["id"],
                    "folder_guardar_id": contratistas_folder_id,  # Guardar el ID de "Contratistas"
                }
            )

            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        except Exception as e:
            print(f"Error creando la carpeta en Google Drive: {e}")
            return False

    _drive_cache_stage4 = {}

    def action_open_drive(self):
        # Verificar si ya tenemos un cache para este ID en la sesión
        if self.id in self.env.context.get("drive_cache_stage4", {}):
            drive_records = self.env.context["drive_cache_stage4"][self.id]
            _logger.info(
                f"Usando caché para drive ID {self.id}, drive records: {drive_records}"
            )
        else:
            # Si no hay cache, hacer la búsqueda
            drive_records = self.env["drive"].search([("stage4_id", "=", self.id)])

            # Guardar el resultado en cache en la sesión
            drive_cache_stage4 = self.env.context.get("drive_cache_stage4", {})
            drive_cache_stage4[self.id] = drive_records
            self.env.context = dict(
                self.env.context, drive_cache_stage4=drive_cache_stage4
            )
            _logger.info(
                f"Se encontraron {len(drive_records)} drives para el ID {self.id}"
            )

        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "drive",
            "view_mode": "kanban,tree,form",
            "target": "current",
            "domain": [("stage4_id", "=", self.id)],
            "context": {
                "default_stage4_id": self.id,
                "default_folder_id": self.folder_id,
            },
        }

    # @api.model
    # def list_files_in_folder(self):
    #     with api.Environment.manage():
    #         new_cr = self.pool.cursor()
    #         self = self.with_env(self.env(cr=new_cr))
    #         try:
    #             self._list_files_in_folder_impl()
    #             new_cr.commit()
    #         except Exception as e:
    #             new_cr.rollback()
    #             _logger.error(f"Error in list_files_in_folder: {e}")
    #         finally:
    #             new_cr.close()

    def list_files_in_folder_impl(self):
        if not self.folder_id:
            _logger.error("Folder ID is not defined.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        file_list = drive.ListFile(
            {"q": f"'{self.folder_id}' in parents and trashed=false"}
        ).GetList()

        for file in file_list:
            try:
                existing_file = self.env["drive"].search(
                    [("drive_file_id", "=", file["id"]), ("stage4_id", "=", self.id)]
                )

                if existing_file:
                    # Solo actualiza si hay cambios en el nombre o URL del archivo
                    if (
                        existing_file.file_name != file["title"]
                        or existing_file.file_url != file["alternateLink"]
                    ):
                        existing_file.write(
                            {
                                "file_name": file["title"],
                                "file_url": file["alternateLink"],
                                "folder_id": self.folder_id,
                            }
                        )
                        _logger.info(
                            f"File updated in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                    else:
                        _logger.info(
                            f"File already up-to-date in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                else:
                    # Solo crea el archivo si no existe en Odoo
                    file.GetContentFile("/tmp/file_to_import")
                    with open("/tmp/file_to_import", "rb") as f:
                        file_data = f.read()

                    new_file = self.env["drive"].create(
                        {
                            "file_name": file["title"],
                            "file_url": file["alternateLink"],
                            "drive_file_id": file["id"],
                            "stage4_id": self.id,
                            "file_data": base64.b64encode(file_data),
                            "folder_id": self.folder_id,
                        }
                    )
                    _logger.info(
                        f"New file created in Odoo: {new_file.file_name} (ID: {new_file.id})"
                    )

            except Exception as e:
                _logger.error(f"Error processing file {file['title']}: {e}")

        # Asegurarse de no eliminar archivos existentes
        # Evitar la eliminación de archivos en Odoo que no se encuentren en Google Drive
        existing_files_in_odoo = self.env["drive"].search([("stage4_id", "=", self.id)])
        drive_file_ids = [file["id"] for file in file_list]

        for odoo_file in existing_files_in_odoo:
            if odoo_file.drive_file_id not in drive_file_ids:
                _logger.info(
                    f"File in Odoo but not in Drive (not deleting): {odoo_file.file_name} (ID: {odoo_file.id})"
                )

    def unlink(self):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        for record in self:
            # 🔍 Registrar trazabilidad local (opcional, si ya implementaste el logger)
            self._log_trace(record.id, "unlink", "Registro eliminado")

            # 🗑️ Eliminar registros en el modelo "drive"
            related_drives = (
                self.env["drive"].sudo().search([("stage4_id", "=", record.id)])
            )
            related_drives.unlink()

            # 🗑️ Eliminar carpeta de Google Drive (mover a papelera)
            if record.folder_id:
                try:
                    folder = drive.CreateFile({"id": record.folder_id})
                    folder.FetchMetadata()  # Asegura que existe antes de intentar
                    folder.Trash()  # Mueve a la papelera
                    _logger.info(
                        f"Carpeta movida a la papelera en Google Drive: {record.folder_id}"
                    )
                except Exception as e:
                    _logger.warning(
                        f"No se pudo mover la carpeta a la papelera en Google Drive (ID: {record.folder_id}): {e}"
                    )

        return super(CuentaDeCobros, self).unlink()


class OtroSiDriveTrace(models.Model):
    _name = "otrosi_drive.trace"
    _description = "Trazabilidad de las acciones en otrosí"

    otrosi_id = fields.Many2one("otrosi_drive", string="Otrosí")
    action = fields.Selection(
        [("create", "Creación"), ("write", "Actualización"), ("unlink", "Eliminación")],
        string="Acción",
    )
    user_id = fields.Many2one("res.users", string="Usuario")
    timestamp = fields.Datetime(string="Fecha y Hora", default=fields.Datetime.now)
    details = fields.Text(string="Detalles")


class OtroSiDrive(models.Model):
    _name = "otrosi_drive"
    _description = "Gestión de cuentas y carpetas en Google Drive"

    name = fields.Char(string="Stage Name", related="folder_name")
    folder_name = fields.Char(string="Nombre de la carpeta")
    fch_creacion = fields.Datetime(
        string="Fecha de Creación",
        readonly=True,
        default=lambda self: fields.Datetime.now(),
    )
    user_id = fields.Many2one(
        "res.users", string="Responsable", default=lambda self: self.env.user
    )
    drive_id = fields.Many2one("drive.file", string="Ubicación")
    real_creator_id = fields.Many2one(
        "res.users",
        string="Creador real",
        compute="_compute_real_creator_otrosi",
        store=False,
    )

    @api.depends("contratacion_fuv_en_id", "drive_id", "user_id")
    def _compute_real_creator_otrosi(self):
        for rec in self:
            if rec.contratacion_fuv_en_id and rec.contratacion_fuv_en_id.create_uid:
                rec.real_creator_id = rec.contratacion_fuv_en_id.create_uid
            elif rec.drive_id and rec.drive_id.user_id:
                rec.real_creator_id = rec.drive_id.user_id
            else:
                rec.real_creator_id = rec.user_id or rec.create_uid

    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)
    folder_pending_creation = fields.Boolean(
        string="Creación de carpeta pendiente", default=False
    )
    is_plantilla = fields.Boolean(
        string="Es plantilla",
        default=False,
        help="Si está activo, esta carpeta no aparece en el Drive Browser (es una plantilla interna).",
    )

    # Campo para almacenar el contador de registros relacionados
    registro_counter = fields.Integer(string="Contador de Registros")
    contratacion_fuv_en_id = fields.Many2one(
        "contrataciones_fuv", string="Contratación FUV"
    )

    # Relación con el modelo padre

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    def action_view_history(self):
        return {
            "name": "Trazabilidad",
            "view_mode": "tree",
            "res_model": "otrosi_drive.trace",
            "type": "ir.actions.act_window",
            "target": "current",
        }

    @api.model
    def create(self, vals):
        record = super(OtroSiDrive, self).create(vals)
        self._log_trace(record.id, "create", "Registro creado")
        return record

    def write(self, vals):
        res = super(OtroSiDrive, self).write(vals)
        for record in self:
            self._log_trace(record.id, "write", "Registro actualizado")
        return res

    def _log_trace(self, record_id, action, details):
        self.env["otrosi_drive.trace"].create(
            {
                "otrosi_id": record_id,
                "action": action,
                "user_id": self.env.user.id,
                "details": details,
            }
        )

    def download_drive_folder(self):
        if not self.folder_id:
            raise UserError("Folder ID is not defined.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre de la carpeta
        folder = drive.CreateFile({"id": self.folder_id})
        folder.FetchMetadata(fields="title")
        folder_name = folder["title"]

        # Función para descargar archivos en una carpeta
        def download_files_in_folder(folder_id, zip_obj, path=""):
            query = f"'{folder_id}' in parents and trashed=false"
            file_list = drive.ListFile({"q": query}).GetList()

            for file in file_list:
                if file["mimeType"] == "application/vnd.google-apps.folder":
                    folder_name = file["title"]
                    new_path = os.path.join(path, folder_name)
                    download_files_in_folder(file["id"], zip_obj, new_path)
                else:
                    # Guardar archivo binario
                    file_path = "/tmp/file_to_download"
                    file.GetContentFile(
                        file_path
                    )  # Descargar archivo al sistema temporal
                    with open(file_path, "rb") as f:
                        file_data = f.read()
                    zip_path = os.path.join(path, file["title"])
                    zip_obj.writestr(zip_path, file_data)

        # Crear un archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            download_files_in_folder(self.folder_id, zip_file)

        # Preparar el archivo para descarga
        zip_buffer.seek(0)
        zip_filename = f"{folder_name}.zip"
        attachment = self.env["ir.attachment"]._drive_create_temp_download(
            {
                "name": zip_filename,
                "datas": base64.b64encode(zip_buffer.read()),
                "type": "binary",
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/zip",
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def upload_file_to_drive(self, file_path):
        """Uploads a file to the specified Google Drive folder."""
        if not self.folder_id:
            raise UserError("No hay carpeta seleccionada para subir el archivo.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            file_metadata = {
                "title": os.path.basename(file_path),
                "parents": [{"id": self.folder_id}],
            }
            file_to_upload = drive.CreateFile(file_metadata)
            file_to_upload.SetContentFile(file_path)
            file_to_upload.Upload()

            return {
                "name": file_to_upload["title"],
                "url": file_to_upload["alternateLink"],
            }
        except Exception as e:
            raise UserError(f"Error subiendo el archivo a Google Drive: {e}")

    # Actualizamos los campos a Char en lugar de Selection
    folder_id = fields.Char("Google Drive Folder ID")  # Cambiar a Char
    folder_guardar_id = fields.Char(
        "Google Drive Folder ID para Guardar"
    )  # Cambiar a Char

    # Función para obtener las carpetas desde Google Drive (optimizada)
    def _get_folder_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        # Obtener la instancia de autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Calcular la fecha de hace 7 días
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        # Función recursiva para listar carpetas
        def list_folders(folder_id):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                # Recursivamente listar carpetas dentro de esta carpeta
                options.extend(list_folders(folder["id"]))
            return options

        # Listar carpetas desde la raíz ('root')
        root_folders = list_folders("root")

        return root_folders

    # Función para obtener las carpetas sin filtrar por fecha
    def _get_carpeta_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        # Obtener la instancia de autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Listar carpetas desde la raíz ('root') sin filtrar por fecha
        folder_list = drive.ListFile(
            {
                "q": "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            }
        ).GetList()

        return [(folder["id"], folder["title"]) for folder in folder_list]

    def create_drive_folder(self):
        """Crea una carpeta en Google Drive dentro de la carpeta especificada por `folder_guardar_id`."""
        if not self.folder_guardar_id:
            raise UserError(
                "No hay carpeta seleccionada para guardar la nueva carpeta."
            )

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Metadatos para crear la carpeta en Google Drive
        folder_metadata = {
            "title": self.folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [
                {"id": self.folder_guardar_id}
            ],  # Especificamos la carpeta donde se guardará
        }

        try:
            # Crear la carpeta en Google Drive
            folder = drive.CreateFile(folder_metadata)

            # Mide el tiempo transcurrido y cancela si excede el timeout
            start_time = time.time()
            timeout = 10  # segundos de espera máxima

            folder.Upload()

            if (time.time() - start_time) > timeout:
                _logger.error("Timeout alcanzado al crear la carpeta en Google Drive")
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Error",
                        "message": "Timeout alcanzado al crear la carpeta.",
                        "type": "danger",
                        "sticky": True,
                    },
                }

            # Guardar el ID de la carpeta recién creada en Odoo
            self.write({"folder_id": folder["id"]})

            # Retorna una acción combinada para mostrar la notificación y cerrar la ventana
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Éxito",
                    "message": "La carpeta ha sido creada correctamente en Google Drive.",
                    "type": "success",
                    "sticky": False,
                    "next": {
                        "type": "ir.actions.act_window_close"  # Cerrar la ventana
                    },
                },
            }
        except Exception as e:
            # Imprimir el error en la consola para depuración
            print(f"Error creando la carpeta en Google Drive: {e}")
            raise UserError(f"Error creando la carpeta en Google Drive: {e}")

    # Variable de caché para los drives
    _drive_cache = {}

    def action_open_drive(self):
        # Verificar si ya tenemos un cache para este ID en la sesión
        if self.id in self.env.context.get("drive_cache_stage5", {}):
            drive_records = self.env.context["drive_cache_stage5"][self.id]
            _logger.info(
                f"Usando caché para action_open_drive con stage5 ID {self.id}, registros: {drive_records}"
            )
        else:
            # Si no hay cache, hacer la búsqueda
            drive_records = self.env["drive"].search([("stage5_id", "=", self.id)])

            # Guardar el resultado en cache en la sesión
            drive_cache_stage5 = self.env.context.get("drive_cache_stage5", {})
            drive_cache_stage5[self.id] = drive_records
            self.env.context = dict(
                self.env.context, drive_cache_stage5=drive_cache_stage5
            )
            _logger.info(
                f"Se encontraron {len(drive_records)} drives para el stage5 ID {self.id}"
            )

        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "drive",
            "view_mode": "kanban,tree,form",
            "target": "current",
            "domain": [("stage5_id", "=", self.id)],
            "context": {
                "default_stage5_id": self.id,
                "default_folder_id": self.folder_id,
            },
        }

    #
    # @api.model
    # def list_files_in_folder(self):
    #     with api.Environment.manage():
    #         new_cr = self.pool.cursor()
    #         self = self.with_env(self.env(cr=new_cr))
    #         try:
    #             self._list_files_in_folder_impl()
    #             new_cr.commit()
    #         except Exception as e:
    #             new_cr.rollback()
    #             _logger.error(f"Error in list_files_in_folder: {e}")
    #         finally:
    #             new_cr.close()

    def list_files_in_folder_impl(self):
        if not self.folder_id:
            _logger.error("Folder ID is not defined.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        file_list = drive.ListFile(
            {"q": f"'{self.folder_id}' in parents and trashed=false"}
        ).GetList()

        for file in file_list:
            try:
                existing_file = self.env["drive"].search(
                    [("drive_file_id", "=", file["id"]), ("stage5_id", "=", self.id)]
                )

                if existing_file:
                    # Solo actualiza si hay cambios en el nombre o URL del archivo
                    if (
                        existing_file.file_name != file["title"]
                        or existing_file.file_url != file["alternateLink"]
                    ):
                        existing_file.write(
                            {
                                "file_name": file["title"],
                                "file_url": file["alternateLink"],
                                "folder_id": self.folder_id,
                            }
                        )
                        _logger.info(
                            f"File updated in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                    else:
                        _logger.info(
                            f"File already up-to-date in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                else:
                    # Solo crea el archivo si no existe en Odoo
                    file.GetContentFile("/tmp/file_to_import")
                    with open("/tmp/file_to_import", "rb") as f:
                        file_data = f.read()

                    new_file = self.env["drive"].create(
                        {
                            "file_name": file["title"],
                            "file_url": file["alternateLink"],
                            "drive_file_id": file["id"],
                            "stage5_id": self.id,
                            "file_data": base64.b64encode(file_data),
                            "folder_id": self.folder_id,
                        }
                    )
                    _logger.info(
                        f"New file created in Odoo: {new_file.file_name} (ID: {new_file.id})"
                    )

            except Exception as e:
                _logger.error(f"Error processing file {file['title']}: {e}")

        # Asegurarse de no eliminar archivos existentes
        # Evitar la eliminación de archivos en Odoo que no se encuentren en Google Drive
        existing_files_in_odoo = self.env["drive"].search([("stage5_id", "=", self.id)])
        drive_file_ids = [file["id"] for file in file_list]

        for odoo_file in existing_files_in_odoo:
            if odoo_file.drive_file_id not in drive_file_ids:
                _logger.info(
                    f"File in Odoo but not in Drive (not deleting): {odoo_file.file_name} (ID: {odoo_file.id})"
                )

    def unlink(self):
        for record in self:
            self._log_trace(record.id, "unlink", "Registro eliminado")
            # Eliminar registros relacionados en 'drive'
            related_drives = self.env["drive"].search([("stage5_id", "=", record.id)])
            related_drives.unlink()

            if record.folder_id:
                drive_auth = GoogleDriveAuth.get_instance()
                drive = drive_auth.get_drive()

                try:
                    folder = drive.CreateFile({"id": record.folder_id})
                    folder.Trash()  # Mover la carpeta a la papelera
                    print(f"Folder moved to trash in Google Drive: {record.folder_id}")
                except Exception as e:
                    print(f"Error moving folder to trash in Google Drive: {e}")

        return super(OtroSiDrive, self).unlink()

    # Variable de cache para cuenta_de_cobros
    _cobros_cache = {}

    def cuenta_de_cobros(self):
        # Verificar si el resultado ya está en caché
        if self.id in self._cobros_cache:
            # Usar el cache si ya se ha buscado anteriormente
            cobros_records = self._cobros_cache[self.id]
            _logger.info(
                f"Usando caché para cuenta_de_cobros con drive ID {self.id}, registros: {cobros_records}"
            )
        else:
            # Si no está en el cache, hacer la búsqueda y almacenarla
            cobros_records = self.env["cuenta_de_cobros"].search(
                [("otrosi_drive_id", "=", self.id)]
            )
            self._cobros_cache[self.id] = cobros_records
            _logger.info(
                f"Se encontraron {len(cobros_records)} registros para cuenta_de_cobros con drive ID {self.id}"
            )

        # Retornar la acción con los datos encontrados
        return {
            "name": self.name,
            "view_mode": "kanban,tree,form",
            "res_model": "cuenta_de_cobros",
            "type": "ir.actions.act_window",
            "target": "current",
            "domain": [("otrosi_drive_id", "=", self.id)],
            "context": {
                "default_otrosi_drive_id": self.id,
                "default_folder_id": self.folder_id,
                "default_folder_guardar_id": self.folder_id or self.folder_guardar_id,
            },
        }


class Papelera(models.Model):
    _name = "papelera"
    _description = "Papelera de Google Drive"

    name = fields.Char("Nombre del Archivo")
    drive_file_id = fields.Char("ID del Archivo en Google Drive")
    mime_type = fields.Char("Tipo de Archivo")
    user_id = fields.Many2one(
        "res.users",
        string="Creador",
        default=lambda self: self.env.user,
        track_visibility="always",
    )
    fch_creacion = fields.Datetime(
        string="Fecha creacion",
        readonly=True,
        copy=False,
        default=lambda self: fields.Datetime.now(),
    )
    folder_id = fields.Selection(
        selection="_get_folder_options", string="Google Drive Folder"
    )
    stage2_id = fields.Many2one("drive.file", string="Ubicación", tracking=True)

    @api.model
    def fetch_trashed_files(self):
        # Obtener instancia de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive_service = drive_auth.get_drive()

        try:
            # Buscar archivos en la papelera (trashed)
            file_list = drive_service.ListFile({"q": "trashed=true"}).GetList()
            existing_file_ids = {file["id"] for file in file_list}

            # Obtener registros actuales en Odoo
            current_records = self.search([])
            current_file_ids = {record.drive_file_id for record in current_records}

            # Crear o actualizar registros en el modelo papelera
            for file in file_list:
                file_id = file["id"]
                file_name = file["title"]
                file_mime_type = file["mimeType"]

                if file_id not in current_file_ids:
                    self.create(
                        {
                            "name": file_name,
                            "drive_file_id": file_id,
                            "mime_type": file_mime_type,
                        }
                    )

            # Eliminar registros en Odoo que ya no están en Google Drive
            for record in current_records:
                if record.drive_file_id not in existing_file_ids:
                    try:
                        record.unlink()
                        _logger.info(
                            "Archivo eliminado de la papelera de Odoo: %s", record.name
                        )
                    except exceptions.ValidationError as e:
                        _logger.error(
                            "Error al eliminar archivo de la papelera de Odoo: %s", e
                        )
                    except Exception as e:
                        _logger.error(
                            "Error inesperado al eliminar archivo de la papelera de Odoo: %s",
                            e,
                        )

        except Exception as e:
            _logger.error(
                "Error al obtener archivos de la papelera de Google Drive: %s", e
            )
            raise

    def unlink(self):
        drive_auth = GoogleDriveAuth.get_instance()
        drive_service = drive_auth.get_drive()

        for record in self:
            try:
                if record.drive_file_id:
                    # Verificar si el archivo aún existe en Google Drive
                    try:
                        file_to_delete = drive_service.CreateFile(
                            {"id": record.drive_file_id}
                        )
                        file_to_delete.FetchMetadata()  # Verifica si el archivo existe
                        file_to_delete.Delete()  # Eliminar el archivo de Google Drive
                        _logger.info(
                            "Archivo eliminado de Google Drive: %s",
                            record.drive_file_id,
                        )
                    except Exception as e:
                        _logger.warning(
                            "El archivo con ID %s no existe en Google Drive: %s",
                            record.drive_file_id,
                            e,
                        )

                # Eliminar el registro de Odoo
                super(Papelera, self).unlink()

            except Exception as e:
                _logger.error("Error al eliminar archivo de Google Drive: %s", e)
                raise exceptions.UserError(
                    f"Error al eliminar el archivo de Google Drive: {e}"
                )

    @api.model
    def _get_folder_options(self):
        # Obtener la instancia de autenticación de Google Drive
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Calcular la fecha de hace 7 días
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        # Función para listar carpetas en el tercer nivel de profundidad
        def list_folders_at_depth(folder_id, depth=0):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            if depth == 2:  # Solo listamos carpetas en el tercer nivel de profundidad
                for folder in folder_list:
                    options.append((folder["id"], folder["title"]))
            elif depth < 2:  # Solo profundizamos si estamos en niveles menos profundos
                for folder in folder_list:
                    options.extend(list_folders_at_depth(folder["id"], depth + 1))
            return options

        # Listar carpetas desde la raíz ('root') para obtener las que están en el tercer nivel
        third_level_folders = list_folders_at_depth("root", 0)

        return third_level_folders


class DriveStageProvedor(models.Model):
    _name = "drive_stage_provedor"
    _description = "carpeta"
    _order = "sequence, id"

    contratistas_provedores = fields.Many2one(
        "contratista_proveedor", string="contratista proveedor"
    )
    file_ids = fields.One2many("drive", "stage6_id", string="Ubicacion")
    proyecto_relacionado = fields.Many2one(
        "contratacion", string="Proyecto relacionado"
    )

    name = fields.Char(string="Carpetas", related="folder_name")
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one(
        "res.users",
        string="Creador",
        default=lambda self: self.env.user,
        track_visibility="always",
    )
    fch_creacion = fields.Datetime(
        string="Fecha creacion",
        readonly=True,
        copy=False,
        default=lambda self: fields.Datetime.now(),
    )
    folder_name = fields.Char(string="Nombre de la carpeta")
    google_drive_id = fields.Char(string="Google Drive ID")
    show_on_dashboard = fields.Boolean(string="Mostrar en el Dashboard", default=False)
    hiden = fields.Char(string="hiden")
    # Cambiar los campos a Char para poder almacenar el ID de las carpetas
    folder_id = fields.Char("Google Drive Folder ID")
    folder_guardar_id = fields.Char("Google Drive Folder Guardar ID")

    def toggle_favorite(self):
        for record in self:
            record.show_on_dashboard = not record.show_on_dashboard

    # Variable de caché para los drives
    _drive_cache = {}

    def action_open_drive(self):
        # Verificar si ya tenemos un cache para este ID en la sesión
        if self.id in self.env.context.get("drive_cache_stage6", {}):
            drive_records = self.env.context["drive_cache_stage6"][self.id]
            _logger.info(
                f"Usando caché para action_open_drive con stage5 ID {self.id}, registros: {drive_records}"
            )
        else:
            # Si no hay cache, hacer la búsqueda
            drive_records = self.env["drive"].search([("stage5_id", "=", self.id)])

            # Guardar el resultado en cache en la sesión
            drive_cache_stage6 = self.env.context.get("drive_cache_stage5", {})
            drive_cache_stage6[self.id] = drive_records
            self.env.context = dict(
                self.env.context, drive_cache_stage5=drive_cache_stage6
            )
            _logger.info(
                f"Se encontraron {len(drive_records)} drives para el stage6 ID {self.id}"
            )

        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "drive",
            "view_mode": "kanban,tree,form",
            "target": "current",
            "domain": [("stage6_id", "=", self.id)],
            "context": {
                "default_stage6_id": self.id,
                "default_folder_id": self.folder_id,
            },
        }

    # Función para obtener las opciones de carpeta, solo si es necesario
    def _get_folder_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()

        def list_folders(folder_id):
            query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and modifiedDate > '{seven_days_ago}'"
            folder_list = drive.ListFile({"q": query}).GetList()
            options = []
            for folder in folder_list:
                options.append((folder["id"], folder["title"]))
                options.extend(list_folders(folder["id"]))
            return options

        # Obtener todas las carpetas dentro de la carpeta root
        return list_folders("root")

    def _get_carpeta_options(self):
        # Ejecutar solo si el contexto 'enable_folder_lookup' está activado
        if not self.env.context.get("enable_folder_lookup", False):
            return []

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Filtrar las carpetas en Google Drive
        folder_list = drive.ListFile(
            {
                "q": "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            }
        ).GetList()

        return [(folder["id"], folder["title"]) for folder in folder_list]

    def create_drive_folder(self):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Verificar si existe la carpeta "Contratistas" en Google Drive
        contratistas_folder_id = None
        try:
            query = "'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false and title='Contratistas'"
            folders = drive.ListFile({"q": query}).GetList()
            if folders:
                contratistas_folder_id = folders[0]["id"]
            else:
                # Crear la carpeta "Contratistas" si no existe
                contratistas_folder_metadata = {
                    "title": "Contratistas",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [{"id": "root"}],
                }
                contratistas_folder = drive.CreateFile(contratistas_folder_metadata)
                contratistas_folder.Upload()
                contratistas_folder_id = contratistas_folder["id"]
        except Exception as e:
            print(f"Error al verificar o crear la carpeta 'Contratistas': {e}")
            return False

        # Crear la carpeta específica dentro de "Contratistas"
        folder_metadata = {
            "title": self.folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [{"id": contratistas_folder_id}],
        }

        try:
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()

            # Actualizar los campos en el modelo con los IDs de las carpetas
            self.write(
                {
                    "folder_id": folder["id"],
                    "folder_guardar_id": contratistas_folder_id,  # Guardar el ID de "Contratistas"
                }
            )

            return {
                "type": "ir.actions.client",
                "tag": "reload",
            }
        except Exception as e:
            print(f"Error creando la carpeta en Google Drive: {e}")
            return False

    # @api.model
    # def list_files_in_folder(self):
    #     with api.Environment.manage():
    #         new_cr = self.pool.cursor()
    #         self = self.with_env(self.env(cr=new_cr))
    #         try:
    #             self._list_files_in_folder_impl()
    #             new_cr.commit()
    #         except Exception as e:
    #             new_cr.rollback()
    #             _logger.error(f"Error in list_files_in_folder: {e}")
    #         finally:
    #             new_cr.close()

    def _list_files_in_folder_impl(self):
        if not self.folder_id:
            _logger.error("Folder ID is not defined.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()
        file_list = drive.ListFile(
            {"q": f"'{self.folder_id}' in parents and trashed=false"}
        ).GetList()

        for file in file_list:
            try:
                existing_file = self.env["drive"].search(
                    [("drive_file_id", "=", file["id"]), ("stage6_id", "=", self.id)]
                )

                if existing_file:
                    # Solo actualiza si hay cambios en el nombre o URL del archivo
                    if (
                        existing_file.file_name != file["title"]
                        or existing_file.file_url != file["alternateLink"]
                    ):
                        existing_file.write(
                            {
                                "file_name": file["title"],
                                "file_url": file["alternateLink"],
                                "folder_id": self.folder_id,
                            }
                        )
                        _logger.info(
                            f"File updated in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                    else:
                        _logger.info(
                            f"File already up-to-date in Odoo: {existing_file.file_name} (ID: {existing_file.id})"
                        )
                else:
                    # Solo crea el archivo si no existe en Odoo
                    file.GetContentFile("/tmp/file_to_import")
                    with open("/tmp/file_to_import", "rb") as f:
                        file_data = f.read()

                    new_file = self.env["drive"].create(
                        {
                            "file_name": file["title"],
                            "file_url": file["alternateLink"],
                            "drive_file_id": file["id"],
                            "stage6_id": self.id,
                            "file_data": base64.b64encode(file_data),
                            "folder_id": self.folder_id,
                        }
                    )
                    _logger.info(
                        f"New file created in Odoo: {new_file.file_name} (ID: {new_file.id})"
                    )

            except Exception as e:
                _logger.error(f"Error processing file {file['title']}: {e}")

        # Asegurarse de no eliminar archivos existentes
        # Evitar la eliminación de archivos en Odoo que no se encuentren en Google Drive
        existing_files_in_odoo = self.env["drive"].search([("stage6_id", "=", self.id)])
        drive_file_ids = [file["id"] for file in file_list]

        for odoo_file in existing_files_in_odoo:
            if odoo_file.drive_file_id not in drive_file_ids:
                _logger.info(
                    f"File in Odoo but not in Drive (not deleting): {odoo_file.file_name} (ID: {odoo_file.id})"
                )

    @api.model
    def list_folders_in_selected_drive(self):
        # Obtén el folder_id del registro actual
        folder_id = self.folder_id

        if not folder_id:
            _logger.error("No folder ID selected.")
            return

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        try:
            # Consulta para obtener carpetas dentro del folder_id seleccionado
            folder_list = drive.ListFile(
                {
                    "q": f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                }
            ).GetList()

            _logger.info(f"Folders in the selected Google Drive folder ({folder_id}):")
            for folder in folder_list:
                _logger.info(
                    f"Folder Name: {folder['title']}, Folder ID: {folder['id']}"
                )

                # Check if the folder already exists in Odoo by folder name
                existing_folder = self.env["drive.file"].search(
                    [("folder_name", "=", folder["title"])], limit=1
                )

                if not existing_folder:
                    # Create a new folder record in Odoo if it doesn't exist
                    self.env["drive.file"].create(
                        {
                            "folder_name": folder["title"],
                            "drive_id": self.id,  # Link to the current DriveStage record
                            "folder_id2": folder_id,  # Link to the current folder in Odoo
                        }
                    )
                    _logger.info(
                        f"Created new folder record in Odoo: {folder['title']}"
                    )

        except Exception as e:
            _logger.error(f"Error retrieving folders from Google Drive: {e}")

    def _find_existing_file_id(self, drive, folder_id, file_name):
        try:
            file_list = drive.ListFile(
                {
                    "q": f"'{folder_id}' in parents and title='{file_name}'",
                    "fields": "items(id)",
                }
            ).GetList()

            if file_list:
                return file_list[0]["id"]
            return None
        except Exception as e:
            _logger.error(
                f"Error buscando el archivo '{file_name}' en Google Drive: {e}"
            )
            raise exceptions.UserError(
                f"Error buscando el archivo '{file_name}' en Google Drive: {e}"
            )

    def download_drive_folder(self):
        if not self.folder_id:
            raise UserError("El ID de la carpeta no está definido.")

        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        # Obtener el nombre de la carpeta
        folder = drive.CreateFile({"id": self.folder_id})
        folder.FetchMetadata(fields="title")
        folder_name = folder["title"]

        # Función para descargar archivos en una carpeta
        def download_files_in_folder(folder_id, zip_obj, path=""):
            query = f"'{folder_id}' in parents and trashed=false"
            file_list = drive.ListFile({"q": query}).GetList()

            if not file_list:
                # Si la carpeta está vacía, crear una estructura de carpeta vacía en el ZIP
                zip_obj.writestr(
                    os.path.join(path, "leerme.txt"), "Carpeta vacía"
                )  # Puedes cambiar o eliminar este archivo si no lo deseas
            else:
                for file in file_list:
                    if file["mimeType"] == "application/vnd.google-apps.folder":
                        subfolder_name = file["title"]
                        new_path = os.path.join(path, subfolder_name)
                        download_files_in_folder(file["id"], zip_obj, new_path)
                    else:
                        # Guardar archivo binario
                        file_path = "/tmp/file_to_download"
                        file.GetContentFile(
                            file_path
                        )  # Descargar archivo al sistema temporal
                        with open(file_path, "rb") as f:
                            file_data = f.read()
                        zip_path = os.path.join(path, file["title"])
                        zip_obj.writestr(zip_path, file_data)

        # Crear un archivo ZIP en memoria
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            download_files_in_folder(self.folder_id, zip_file)

        # Preparar el archivo para descarga
        zip_buffer.seek(0)
        zip_filename = f"{folder_name}.zip"
        attachment = self.env["ir.attachment"]._drive_create_temp_download(
            {
                "name": zip_filename,
                "datas": base64.b64encode(zip_buffer.read()),
                "type": "binary",
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/zip",
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def unlink(self):
        drive_auth = GoogleDriveAuth.get_instance()
        drive = drive_auth.get_drive()

        for record in self:
            # Eliminar la carpeta asociada en Google Drive
            if record.folder_id:
                try:
                    folder = drive.CreateFile({"id": record.folder_id})
                    folder.Delete()  # Eliminar la carpeta en Google Drive
                    _logger.info(
                        f"Carpeta eliminada en Google Drive con ID: {record.folder_id}"
                    )
                except Exception as e:
                    _logger.error(
                        f"Error al eliminar la carpeta en Google Drive con ID {record.folder_id}: {e}"
                    )

            # Eliminar los registros relacionados en file_ids
            if record.file_ids:
                try:
                    record.file_ids.unlink()
                    _logger.info(
                        f"Registros relacionados eliminados en 'drive': {record.file_ids.ids}"
                    )
                except Exception as e:
                    _logger.error(
                        f"Error al eliminar registros relacionados en 'drive': {e}"
                    )

        # Llamar al método unlink original para eliminar el registro
        return super(DriveStageProvedor, self).unlink()
