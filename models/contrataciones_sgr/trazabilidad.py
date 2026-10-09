from markupsafe import Markup, escape

from odoo import api, models


_AUDITABLE_TYPES = {
    'boolean', 'char', 'date', 'datetime', 'float', 'html', 'integer',
    'many2many', 'many2one', 'monetary', 'one2many', 'selection', 'text',
}
_AUDIT_EXCLUDED = {
    'estado_sincronizacion_drive', 'drive_file_id', 'documento_ids',
    'documentos_natural_ids', 'documentos_juridica_ids', 'documentos_soporte_ids',
}


class ContratacionesSgrTrazabilidad(models.Model):
    _inherit = 'contrataciones_sgr'

    def _campos_auditoria_sgr(self, names, incluir_tracking=False):
        return [
            name for name in dict.fromkeys(names)
            if name in self._fields
            and name not in _AUDIT_EXCLUDED
            and not name.startswith(('message_', 'activity_'))
            and self._fields[name].type in _AUDITABLE_TYPES
            and self._fields[name].store
            and not self._fields[name].compute
            and not self._fields[name].related
            and (incluir_tracking or not getattr(self._fields[name], 'tracking', False))
        ]

    def _valor_auditoria_sgr(self, name):
        self.ensure_one()
        field = self._fields[name]
        value = self[name]
        if field.type == 'boolean':
            return 'Sí' if value else 'No'
        if field.type == 'many2one':
            return value.display_name if value else 'Vacío'
        if field.type in ('many2many', 'one2many'):
            labels = value[:30].mapped('display_name')
            suffix = ', … (%s registros)' % len(value) if len(value) > 30 else ''
            return ', '.join(labels) + suffix if value else 'Vacío'
        if field.type == 'selection' and value:
            selection = field._description_selection(self.env)
            return dict(selection).get(value, value)
        return str(value) if value is not False and value is not None and value != '' else 'Vacío'

    def _publicar_cambios_sgr(self, changes, titulo):
        self.ensure_one()
        if not changes:
            return
        rows = Markup('').join(
            Markup('<li><b>%s:</b> %s</li>') % (escape(label), escape(value))
            for label, value in changes
        )
        self.with_context(sgr_skip_field_audit=True).message_post(
            body=Markup('<b>%s</b><ul>%s</ul>') % (escape(titulo), rows),
            subtype_xmlid='mail.mt_note',
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record, vals in zip(records, vals_list):
            names = record._campos_auditoria_sgr(
                list(vals) + ['estado_contratacion'], incluir_tracking=True,
            )
            record._publicar_cambios_sgr([
                (record._fields[name].string or name, record._valor_auditoria_sgr(name))
                for name in names
            ], 'Datos iniciales de la contratación')
        return records

    def write(self, vals):
        if self.env.context.get('sgr_skip_field_audit'):
            return super().write(vals)
        names = self._campos_auditoria_sgr(vals)
        before = {
            record.id: {name: record._valor_auditoria_sgr(name) for name in names}
            for record in self
        }
        result = super().write(vals)
        for record in self:
            changes = [
                (record._fields[name].string or name,
                 '%s → %s' % (before[record.id][name], after))
                for name in names
                if (after := record._valor_auditoria_sgr(name)) != before[record.id][name]
            ]
            record._publicar_cambios_sgr(changes, 'Cambios en la contratación')
        return result
