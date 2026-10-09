from markupsafe import Markup, escape

from odoo import _, fields, models
from odoo.exceptions import UserError

from .constantes import POLIZA_SELECTION, EstadoSolicitudPoliza


class AgregarPolizaWizard(models.TransientModel):
    _name = 'contrataciones_fuv.agregar.poliza.wizard'
    _description = 'Agregar una póliza a la solicitud'

    solicitud_id = fields.Many2one(
        'contrataciones_fuv.solicitud.poliza',
        string='Solicitud',
        required=True,
        readonly=True,
    )
    tipo_poliza = fields.Selection(
        selection='_selection_polizas_disponibles',
        string='Póliza a agregar',
        required=True,
    )

    def _selection_polizas_disponibles(self):
        solicitud_id = self.env.context.get('default_solicitud_id')
        solicitud = self.env['contrataciones_fuv.solicitud.poliza'].browse(
            solicitud_id
        ).exists()
        seleccionadas = set(solicitud.linea_ids.mapped('tipo_poliza'))
        return [
            (codigo, etiqueta)
            for codigo, etiqueta in POLIZA_SELECTION
            if codigo not in seleccionadas
        ]

    def action_agregar_poliza(self):
        self.ensure_one()
        solicitud = self.solicitud_id.exists()
        if not solicitud:
            raise UserError(_('No se encontró la solicitud de pólizas.'))
        if solicitud.estado == EstadoSolicitudPoliza.FINALIZADA.value:
            raise UserError(_(
                'No se pueden agregar pólizas a una solicitud finalizada.'
            ))
        if not solicitud._usuario_puede_agregar_poliza():
            raise UserError(_(
                'No tiene permisos para agregar pólizas a esta solicitud.'
            ))
        if self.tipo_poliza in solicitud.linea_ids.mapped('tipo_poliza'):
            raise UserError(_('Esta póliza ya se encuentra en la solicitud.'))

        # Asistencia y Soporte conservan permisos generales de solo lectura.
        # Después de validar el rol, se eleva únicamente esta operación acotada.
        actor = self.env.user
        solicitud_operacion = solicitud.sudo().with_context(
            solicitud_poliza_actor_id=actor.id,
        )

        # Compatibilidad con solicitudes anteriores al control de envío por línea:
        # todo lo existente antes del último correo ya había sido solicitado.
        if solicitud.fecha_envio:
            solicitud_operacion.linea_ids.filtered(
                lambda linea: not linea.fecha_envio_contratista
            ).write({'fecha_envio_contratista': solicitud.fecha_envio})

        self.env['contrataciones_fuv.solicitud.poliza.linea'].sudo().create({
            'solicitud_id': solicitud.id,
            'tipo_poliza': self.tipo_poliza,
        })
        if solicitud.fecha_envio:
            solicitud_operacion.write({
                'estado': EstadoSolicitudPoliza.PENDIENTE_ENVIO.value,
            })
        etiqueta = dict(POLIZA_SELECTION).get(self.tipo_poliza, self.tipo_poliza)
        solicitud_operacion.message_post(
            body=Markup('<p><strong>Póliza agregada:</strong> %s</p>') % escape(etiqueta),
            subtype_xmlid='mail.mt_note',
            author_id=actor.partner_id.id,
        )
        # El wizard se abrió encima de la solicitud. Cerrarlo conserva la miga
        # actual y permite que Odoo actualice el formulario padre sin abrir una
        # segunda acción idéntica.
        return {'type': 'ir.actions.act_window_close'}
