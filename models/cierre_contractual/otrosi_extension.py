# Mejora únicamente la identificación de los otrosí dentro del selector del cierre contractual.
from odoo import _, models
from odoo.tools import formatLang


class OtroSiCierreContractual(models.Model):
    _inherit = 'otro_si'

    def name_get(self):
        """Muestra tipo, número, estado y valor solo cuando lo pide el cierre."""
        nombres_originales = super().name_get()
        if not self.env.context.get('etiqueta_otrosi_cierre'):
            return nombres_originales

        etiquetas_estado = dict(self._fields['estado_contratacion'].selection)
        resultado = []
        for record in self:
            if record.adicion_prorroga or (record.adicion and record.prorroga):
                tipo = _('Adición y prórroga')
                numero = record.adicion_prorroga_number
            elif record.adicion:
                tipo = _('Adición')
                numero = record.adicion_number
            else:
                tipo = _('Otrosí modificatorio con valor')
                numero = record.otro_si_modificatorio_number

            valor = formatLang(
                self.env,
                record.valor_total_contrato or 0.0,
                currency_obj=record.currency_id,
            )
            estado = etiquetas_estado.get(
                record.estado_contratacion,
                record.estado_contratacion or '',
            )
            resultado.append((
                record.id,
                _('%(tipo)s N.° %(numero)s - %(estado)s - %(valor)s') % {
                    'tipo': tipo,
                    'numero': numero or record.id,
                    'estado': estado,
                    'valor': valor,
                },
            ))
        return resultado
