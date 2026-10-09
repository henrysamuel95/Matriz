# -*- coding: utf-8 -*-
import json
import threading
import time
from datetime import timedelta

from odoo import api, fields, models, _


_PDF_LOCK_NAMESPACE = 290926
_PDF_UPLOAD_STALE_SECONDS = 300
_PDF_GENERAL_STALE_SECONDS = 900


class ActaEntregaDocumentosPdfLock(models.Model):
    _inherit = 'acta_entrega_documentos'

    def _pdf_unificado_try_lock(self):
        """Bloqueo transaccional para impedir dos generaciones simultáneas.

        El doble clic o dos RPC casi al mismo tiempo podían arrancar dos hilos
        sobre la misma cuenta. Ambos actualizaban progreso/estado y PostgreSQL
        terminaba con: could not serialize access due to concurrent update.
        """
        self.ensure_one()
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (_PDF_LOCK_NAMESPACE, int(self.id)),
        )
        row = self.env.cr.fetchone()
        return bool(row and row[0])

    def _pdf_unificado_proceso_estancado(self):
        """Detecta una generación que dejó de escribir progreso.

        En el 96%% ya terminó el armado local y está subiendo a Google Drive.
        Esa fase normalmente es corta; si no hay ninguna escritura durante cinco
        minutos se considera colgada. En las demás fases damos quince minutos.
        """
        self.ensure_one()
        if self.pdf_unificado_estado != 'procesando':
            return False

        ultima_escritura = self.write_date
        if not ultima_escritura:
            return False

        limite_segundos = (
            _PDF_UPLOAD_STALE_SECONDS
            if (self.pdf_unificado_progreso or 0.0) >= 96.0
            else _PDF_GENERAL_STALE_SECONDS
        )
        return fields.Datetime.now() - ultima_escritura > timedelta(seconds=limite_segundos)

    def _pdf_unificado_liberar_si_estancado(self):
        """Convierte un proceso huérfano en error para permitir reintento seguro."""
        self.ensure_one()
        self.invalidate_cache([
            'pdf_unificado_estado',
            'pdf_unificado_progreso',
            'pdf_unificado_mensaje',
            'write_date',
        ])
        if not self._pdf_unificado_proceso_estancado():
            return False

        progreso = self.pdf_unificado_progreso or 0.0
        mensaje = (
            'La carga del PDF a Google Drive dejó de responder. Puede intentar generar nuevamente.'
            if progreso >= 96.0
            else 'La generación del PDF dejó de responder. Puede intentar generar nuevamente.'
        )
        self.sudo().with_context(
            skip_drive_sync=True,
            skip_integrity=True,
            mail_notrack=True,
            tracking_disable=True,
        ).write({
            'pdf_unificado_estado': 'error',
            'pdf_unificado_mensaje': mensaje,
        })
        return True

    @staticmethod
    def _pdf_unificado_watchdog(db_name, uid, model_name, acta_id):
        """Evita que una cuenta quede eternamente en 96%%/Procesando."""
        from odoo.modules.registry import Registry

        # El worker real arranca en postcommit. Damos tiempo antes de la primera revisión.
        time.sleep(60)
        while True:
            try:
                with Registry(db_name).cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    acta = env[model_name].browse(acta_id).exists()
                    if not acta:
                        return
                    acta.invalidate_cache([
                        'pdf_unificado_estado',
                        'pdf_unificado_progreso',
                        'pdf_unificado_mensaje',
                        'write_date',
                    ])
                    if acta.pdf_unificado_estado != 'procesando':
                        return
                    if acta._pdf_unificado_proceso_estancado():
                        acta._pdf_unificado_liberar_si_estancado()
                        cr.commit()
                        return
            except Exception:
                # El watchdog no debe tumbar la generación principal por una lectura aislada.
                pass
            time.sleep(30)

    def _pdf_unificado_iniciar_watchdog_postcommit(self):
        self.ensure_one()
        db_name = self.env.cr.dbname
        uid = self.env.uid
        model_name = self._name
        acta_id = self.id

        def _start():
            hilo = threading.Thread(
                target=self._pdf_unificado_watchdog,
                args=(db_name, uid, model_name, acta_id),
                name='PDFUnificadoWatchdog-%s' % acta_id,
                daemon=True,
            )
            hilo.start()

        self.env.cr.postcommit.add(_start)

    def action_generar_pdf_unificado(self):
        self.ensure_one()

        # Si esta misma transacción ya tomó el lock (por ejemplo desde el wizard),
        # PostgreSQL permite volver a solicitarlo desde la misma sesión.
        if not self._pdf_unificado_try_lock():
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('La generación ya fue iniciada. Espere a que termine.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        # Releer el estado después de obtener el lock para no trabajar con caché vieja.
        self.invalidate_cache([
            'pdf_unificado_estado',
            'pdf_unificado_progreso',
            'pdf_unificado_mensaje',
            'write_date',
        ])

        # Recupera automáticamente registros que quedaron huérfanos en "Procesando".
        self._pdf_unificado_liberar_si_estancado()
        self.invalidate_cache(['pdf_unificado_estado'])

        if self.pdf_unificado_estado == 'procesando':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('La generación ya está en proceso.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        result = super().action_generar_pdf_unificado()
        # Si realmente arrancó una generación, vigilar que no se quede eternamente
        # en Google Drive o en otra etapa sin actualizar el progreso.
        self.invalidate_cache(['pdf_unificado_estado'])
        if self.pdf_unificado_estado == 'procesando':
            self._pdf_unificado_iniciar_watchdog_postcommit()
        return result


class ActaPdfUnificadoOrdenWizardLock(models.TransientModel):
    _inherit = 'acta.pdf.unificado.orden.wizard'

    def action_aplicar_y_generar(self):
        self.ensure_one()
        acta = self.acta_id.exists()
        if not acta:
            return {'type': 'ir.actions.act_window_close'}

        # Tomar el lock ANTES de escribir el orden manual. Así dos clics rápidos
        # tampoco compiten escribiendo pdf_unificado_orden_manual en la misma fila.
        if not acta._pdf_unificado_try_lock():
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('La generación ya fue iniciada. Espere a que termine.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        acta.invalidate_cache([
            'pdf_unificado_estado',
            'pdf_unificado_progreso',
            'pdf_unificado_mensaje',
            'write_date',
        ])
        acta._pdf_unificado_liberar_si_estancado()
        acta.invalidate_cache(['pdf_unificado_estado'])
        if acta.pdf_unificado_estado == 'procesando':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('La generación ya está en proceso.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        lineas = self.line_ids.filtered('incluir').sorted(
            lambda linea: (linea.sequence, linea.id)
        )
        if not lineas:
            return {'type': 'ir.actions.act_window_close'}

        acta.sudo().write({
            'pdf_unificado_orden_manual': json.dumps(lineas.mapped('drive_id').ids),
            'pdf_unificado_usar_orden_manual': True,
        })
        return acta.action_generar_pdf_unificado()

    def action_usar_orden_base(self):
        self.ensure_one()
        acta = self.acta_id.exists()
        if not acta:
            return {'type': 'ir.actions.act_window_close'}

        if not acta._pdf_unificado_try_lock():
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('La generación ya fue iniciada. Espere a que termine.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        acta.invalidate_cache([
            'pdf_unificado_estado',
            'pdf_unificado_progreso',
            'pdf_unificado_mensaje',
            'write_date',
        ])
        acta._pdf_unificado_liberar_si_estancado()
        acta.invalidate_cache(['pdf_unificado_estado'])
        if acta.pdf_unificado_estado == 'procesando':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('PDF unificado'),
                    'message': _('La generación ya está en proceso.'),
                    'type': 'warning',
                    'sticky': False,
                },
            }

        acta.sudo().write({
            'pdf_unificado_orden_manual': False,
            'pdf_unificado_usar_orden_manual': False,
        })
        return acta.action_generar_pdf_unificado()
