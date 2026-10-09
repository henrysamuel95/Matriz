from odoo import models, fields, api, _
from odoo.modules.module import get_module_resource
from odoo.exceptions import UserError
import os
import base64

class MatrizPlantillaWord(models.Model):
    _name = 'matriz.plantilla.word'
    _description = 'Gestor de plantillas word'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Nombre de la Plantilla', required=True, tracking=True)
    nombre_archivo = fields.Char(string='Nombre del Archivo Físico', help="Ej: acta_de_prorroga.docx", required=True, tracking=True)
    archivo_word = fields.Binary(string='Archivo Word (.docx)', required=True, tracking=True)
    descripcion = fields.Text(string='Descripción', tracking=True)
    ultima_actualizacion = fields.Datetime(string='Última Sincronización', readonly=True, tracking=True)

    @api.model
    def search(self, args, offset=0, limit=None, order=None, count=False):
        """
        EL TRUCO: Cada vez que Odoo va a mostrar la lista en pantalla,
        primero mandamos a escanear la carpeta automáticamente.
        """
        try:
            self._escanear_carpeta_automatico()
        except Exception:
            pass # Si hay algún error leyendo la carpeta, no bloqueamos la vista

        return super(MatrizPlantillaWord, self).search(args, offset=offset, limit=limit, order=order, count=count)

    @api.model
    def _escanear_carpeta_automatico(self):
        """ Lee físicamente la ruta y crea los registros que falten """
        path_base = get_module_resource('Matriz', 'static/src/docx')

        if not path_base or not os.path.exists(path_base):
            return

        for archivo in os.listdir(path_base):
            # Solo procesamos Word y que no sean los que ya rellenaste ('rendered_')
            if archivo.endswith('.docx') and not archivo.startswith('rendered_'):
                # Usamos super().search para evitar un bucle infinito
                existe = super(MatrizPlantillaWord, self).search([('nombre_archivo', '=', archivo)], limit=1)

                if not existe:
                    ruta_completa = os.path.join(path_base, archivo)

                    with open(ruta_completa, 'rb') as f:
                        archivo_binario = base64.b64encode(f.read())

                    self.create({
                        'name': archivo.replace('.docx', '').replace('_', ' ').title(),
                        'nombre_archivo': archivo,
                        'archivo_word': archivo_binario,
                    })

    def action_actualizar_plantilla_servidor(self):
        """
        Toma el archivo binario de Odoo y lo escribe en la carpeta static/src/docx
        sobrescribiendo el archivo existente.
        """
        self.ensure_one()
        if not self.archivo_word:
            raise UserError(_("No hay ningún archivo cargado para sincronizar."))

        path_base = get_module_resource('Matriz', 'static/src/docx')

        if not path_base:
            path_modulo = get_module_resource('Matriz')
            path_base = os.path.join(path_modulo, 'static', 'src', 'docx')
            os.makedirs(path_base, exist_ok=True)

        ruta_final = os.path.join(path_base, self.nombre_archivo)

        try:
            with open(ruta_final, 'wb') as f:
                f.write(base64.b64decode(self.archivo_word))

            self.ultima_actualizacion = fields.Datetime.now()

            return {
                'effect': {
                    'fadeout': 'slow',
                    'message': 'Plantilla actualizada en el servidor con éxito',
                    'type': 'rainbow_man',
                }
            }
        except Exception as e:
            raise UserError(_("Error al escribir el archivo en el servidor: %s") % str(e))
