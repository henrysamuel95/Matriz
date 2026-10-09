import json
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class SgoMassJob(models.Model):
    _name = 'sgo.mass.job'
    _description = 'Proceso masivo en segundo plano'
    _order = 'create_date desc, id desc'

    name = fields.Char(string='Proceso', required=True)
    action_type = fields.Selection([
        ('tesoreria', 'Enviar a Tesorería'),
        ('coordinador', 'Firma del Coordinador'),
        ('director_financiero', 'Firma del Director Financiero'),
    ], string='Acción', required=True, index=True)
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('running', 'Procesando'),
        ('done', 'Terminado'),
        ('done_errors', 'Terminado con errores'),
        ('failed', 'Fallido'),
    ], string='Estado', default='pending', required=True, index=True)
    user_id = fields.Many2one(
        'res.users', string='Solicitado por', required=True,
        default=lambda self: self.env.user, index=True,
    )
    total = fields.Integer(string='Total', default=0)
    processed = fields.Integer(string='Procesados', default=0)
    success_count = fields.Integer(string='Correctos', default=0)
    error_count = fields.Integer(string='Con error', default=0)
    started_at = fields.Datetime(string='Inicio')
    finished_at = fields.Datetime(string='Finalización')
    last_error = fields.Text(string='Último error')
    line_ids = fields.One2many('sgo.mass.job.line', 'job_id', string='Registros')

    @api.model
    def create_mass_job(self, action_type, name, items):
        items = [item for item in (items or []) if item.get('acta_id')]
        if not items:
            raise UserError(_('No hay cuentas seleccionadas para procesar.'))

        acta_ids = list({int(item['acta_id']) for item in items})
        duplicate = self.env['sgo.mass.job.line'].sudo().search([
            ('acta_id', 'in', acta_ids),
            ('job_id.user_id', '=', self.env.user.id),
            ('job_id.action_type', '=', action_type),
            ('job_id.state', 'in', ('pending', 'running')),
        ], limit=1)
        if duplicate:
            raise UserError(_(
                'Una de las cuentas seleccionadas ya está incluida en un proceso '
                'masivo activo de este mismo tipo.'
            ))

        job = self.create({
            'name': name,
            'action_type': action_type,
            'user_id': self.env.user.id,
            'total': len(items),
            'line_ids': [(0, 0, {
                'acta_id': int(item['acta_id']),
                'payload': json.dumps(item, ensure_ascii=False),
            }) for item in items],
        })
        cron = self.env.ref('Matriz.ir_cron_sgo_mass_jobs', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return job

    @api.model
    def get_my_job_progress(self):
        now = fields.Datetime.now()
        cutoff = now - timedelta(days=1)
        recent_cutoff = now - timedelta(minutes=5)
        jobs = self.sudo().search([
            ('user_id', '=', self.env.user.id),
            '|',
            ('state', 'in', ('pending', 'running')),
            ('create_date', '>=', cutoff),
        ], order='create_date desc, id desc', limit=5)
        return [{
            'id': job.id,
            'name': job.name,
            'state': job.state,
            'total': job.total,
            'processed': job.processed,
            'success_count': job.success_count,
            'error_count': job.error_count,
            'last_error': job.last_error or '',
            'is_recent': bool(job.create_date and job.create_date >= recent_cutoff),
            'finished_at': fields.Datetime.to_string(job.finished_at) if job.finished_at else False,
        } for job in jobs]

    @api.model
    def _cron_process_jobs(self):
        jobs = self.sudo().search([
            ('state', 'in', ('pending', 'running')),
        ], order='create_date, id', limit=4)
        retrigger = False
        for job in jobs:
            if job.state == 'pending':
                job.write({'state': 'running', 'started_at': fields.Datetime.now()})
                self.env.cr.commit()

            lines = job.line_ids.filtered(lambda line: line.state == 'pending')[:5]
            for line in lines:
                line_id = line.id
                acta_id = line.acta_id.id
                try:
                    # Las acciones de firma/documentos realizan commits propios.
                    # Un savepoint exterior queda inválido después de esos commits
                    # y deja el trabajo eternamente en 0/N.
                    job._process_line(line)
                    line.sudo().write({'state': 'done', 'error_message': False})
                    self.env.cr.commit()
                except Exception as exc:  # cada cuenta falla de forma independiente
                    self.env.cr.rollback()
                    message = str(exc)[:1500]
                    _logger.exception('Error en proceso masivo %s, cuenta %s', job.id, acta_id)
                    failed_line = self.env['sgo.mass.job.line'].sudo().browse(line_id).exists()
                    if failed_line:
                        failed_line.write({'state': 'error', 'error_message': message})
                    self.sudo().browse(job.id).write({'last_error': message})
                    self.env.cr.commit()

            job.invalidate_cache()
            job._refresh_counts()
            if job.line_ids.filtered(lambda line: line.state == 'pending'):
                retrigger = True
            else:
                job.write({
                    'state': 'done_errors' if job.error_count else 'done',
                    'finished_at': fields.Datetime.now(),
                })

        if retrigger:
            cron = self.env.ref('Matriz.ir_cron_sgo_mass_jobs', raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return True

    def _refresh_counts(self):
        for job in self:
            success = len(job.line_ids.filtered(lambda line: line.state == 'done'))
            errors = len(job.line_ids.filtered(lambda line: line.state == 'error'))
            job.write({
                'processed': success + errors,
                'success_count': success,
                'error_count': errors,
            })

    def _process_line(self, line):
        self.ensure_one()
        payload = json.loads(line.payload or '{}')
        acta = line.acta_id.exists().with_user(self.user_id)
        if not acta:
            raise UserError(_('La cuenta de cobro ya no existe.'))

        context = dict(self.env.context, sgo_mass_background=True)
        if self.action_type == 'tesoreria':
            Wizard = self.env['acta.envio.masivo.tesoreria.wizard'].with_user(self.user_id).with_context(context)
            wizard = Wizard.create({
                'acta_ids': [(6, 0, acta.ids)],
                'observaciones_tesoreria': payload.get('observaciones') or False,
                'line_ids': [(0, 0, {
                    'acta_id': acta.id,
                    'seleccionado': True,
                    'usuarios_tesoreria': [(6, 0, payload.get('user_ids') or [])],
                })],
            })
            wizard.action_enviar()
            return

        if self.action_type == 'coordinador':
            Wizard = self.env['acta.firma.masiva.coordinador.wizard'].with_user(self.user_id).with_context(context)
            wizard = Wizard.create({
                'acta_ids': [(6, 0, acta.ids)],
                'coordinador_nombre_id': payload.get('firmante_id') or self.user_id.id,
                'fecha_firma': payload.get('fecha_firma'),
                'observaciones_ventanilla': payload.get('observaciones') or False,
                'line_ids': [(0, 0, {
                    'acta_id': acta.id,
                    'seleccionado': True,
                    'firma_certificacion': bool(payload.get('firma_certificacion')),
                    'firma_vobo': bool(payload.get('firma_vobo')),
                    'datos_certificacion_ok': bool(payload.get('datos_certificacion_ok')),
                    'cargo_id': payload.get('cargo_id') or False,
                    'usuarios_supervision': [(6, 0, payload.get('user_ids') or [])],
                })],
            })
            wizard.action_firmar()
            return

        if self.action_type == 'director_financiero':
            Wizard = self.env['acta.firma.masiva.director.financiero.wizard'].with_user(self.user_id).with_context(context)
            line_vals = Wizard._prepare_line_vals(acta)
            line_vals.update({'acta_id': acta.id, 'seleccionado': True})
            wizard = Wizard.create({
                'acta_ids': [(6, 0, acta.ids)],
                'nombre_financiera_id': payload.get('firmante_id'),
                'fecha_firma': payload.get('fecha_firma'),
                'line_ids': [(0, 0, line_vals)],
            })
            wizard.action_firmar()
            return

        raise UserError(_('Tipo de proceso masivo no soportado.'))


class SgoMassJobLine(models.Model):
    _name = 'sgo.mass.job.line'
    _description = 'Registro de proceso masivo'
    _order = 'id'

    job_id = fields.Many2one(
        'sgo.mass.job', string='Proceso', required=True,
        ondelete='cascade', index=True,
    )
    acta_id = fields.Many2one(
        'acta_entrega_documentos', string='Cuenta de cobro',
        required=True, ondelete='cascade', index=True,
    )
    state = fields.Selection([
        ('pending', 'Pendiente'),
        ('done', 'Correcto'),
        ('error', 'Error'),
    ], string='Estado', default='pending', required=True, index=True)
    payload = fields.Text(string='Datos del proceso', required=True)
    error_message = fields.Text(string='Error')
