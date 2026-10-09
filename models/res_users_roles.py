from odoo import api, fields, models
from markupsafe import escape


class ResUsersRoles(models.Model):
    _inherit = 'res.users'

    _CAMPOS_USUARIO_AUDITADOS = {
        'name', 'login', 'active', 'email', 'phone', 'mobile', 'lang', 'tz',
        'company_id', 'company_ids', 'action_id', 'share',
    }

    @api.model
    def _categorias_roles_matriz(self):
        xmlids = (
            'Matriz.cat_sgo_admin_control',
            'Matriz.cat_sgo_direccion_coord',
            'Matriz.cat_sgo_financiero_tesoreria',
            'Matriz.cat_sgo_juridico_estruc',
            'Matriz.cat_sgo_apoyo_externo',
            'Matriz.module_category_gestion_documental',
        )
        return {
            categoria.id
            for categoria in (
                self.env.ref(xmlid, raise_if_not_found=False) for xmlid in xmlids
            )
            if categoria
        }

    def _nombres_roles_matriz(self):
        self.ensure_one()
        categoria_ids = self._categorias_roles_matriz()
        grupos = self.sudo().groups_id.filtered(
            lambda grupo: grupo.category_id.id in categoria_ids
        )
        return sorted(grupos.mapped('display_name'), key=str.casefold)

    def _valor_usuario_auditoria(self, field_name):
        self.ensure_one()
        field_obj = self._fields[field_name]
        value = self[field_name]
        if field_obj.type == 'many2one':
            return value.display_name if value else 'Vacío'
        if field_obj.type in ('many2many', 'one2many'):
            nombres = sorted(value.mapped('display_name'), key=str.casefold)
            return ', '.join(nombres) if nombres else 'Vacío'
        if field_obj.type == 'selection':
            opciones = dict(field_obj._description_selection(self.env))
            return opciones.get(value, value) if value else 'Vacío'
        if field_obj.type == 'boolean':
            return 'Sí' if value else 'No'
        return str(value) if value not in (False, None, '') else 'Vacío'

    def _crear_traza_usuario(self, accion, detalle_html):
        self.ensure_one()
        actor = self.env.user
        nombre = self.name or 'Usuario sin nombre'
        login = self.login or 'sin acceso'
        self.env['trazabilidad_interna'].sudo().create({
            'fecha': fields.Datetime.now(),
            'usuario_id': actor.id,
            'usuario_ejecutor': '%s (%s)' % (
                actor.name or 'Usuario sin nombre', actor.login or 'sin acceso'),
            'usuario_afectado_id': self.id if accion != 'eliminacion' else False,
            'tipo_accion': accion,
            'modelo_afectado': 'res_users',
            'nombre_registro': '%s (%s)' % (nombre, login),
            'origen_cambio': 'Administración de usuarios y roles',
            'detalle_cambios': detalle_html,
        })

    @api.model
    def create(self, vals):
        # El otro override de res.users de este módulo solo admite creación
        # individual; conservamos esa compatibilidad y soportamos listas.
        vals_list = vals if isinstance(vals, list) else [vals]
        usuarios = self.browse()
        for valores in vals_list:
            usuarios |= super(ResUsersRoles, self).create(valores)

        if not self.env.context.get('skip_trazabilidad_usuarios'):
            for usuario in usuarios:
                roles = usuario._nombres_roles_matriz()
                detalle = ["<p><b>Usuario creado.</b></p>"]
                if roles:
                    detalle.append(
                        "<p><b>Roles iniciales:</b> %s</p>" %
                        escape(', '.join(roles))
                    )
                usuario._crear_traza_usuario('creacion', ''.join(detalle))
        return usuarios

    def write(self, vals):
        if self.env.context.get('skip_trazabilidad_usuarios'):
            return super().write(vals)

        campos = sorted(self._CAMPOS_USUARIO_AUDITADOS.intersection(vals))
        estado_anterior = {
            usuario.id: {
                'roles': usuario._nombres_roles_matriz(),
                'campos': {
                    campo: usuario._valor_usuario_auditoria(campo)
                    for campo in campos
                },
            }
            for usuario in self
        }
        resultado = super().write(vals)

        for usuario in self:
            anterior = estado_anterior[usuario.id]
            lineas = []
            for campo in campos:
                valor_anterior = anterior['campos'][campo]
                valor_nuevo = usuario._valor_usuario_auditoria(campo)
                if valor_anterior != valor_nuevo:
                    etiqueta = usuario._fields[campo].string or campo
                    lineas.append(
                        '<li><b>%s:</b> %s ➔ %s</li>' % (
                            escape(etiqueta), escape(valor_anterior), escape(valor_nuevo)
                        )
                    )

            roles_nuevos = usuario._nombres_roles_matriz()
            agregados = sorted(set(roles_nuevos) - set(anterior['roles']), key=str.casefold)
            retirados = sorted(set(anterior['roles']) - set(roles_nuevos), key=str.casefold)
            if agregados:
                lineas.append(
                    '<li><b>Roles agregados:</b> %s</li>' % escape(', '.join(agregados))
                )
            if retirados:
                lineas.append(
                    '<li><b>Roles retirados:</b> %s</li>' % escape(', '.join(retirados))
                )
            if 'password' in vals:
                lineas.append('<li><b>Contraseña:</b> actualizada (contenido oculto)</li>')

            if lineas:
                usuario._crear_traza_usuario(
                    'edicion', '<p><b>Usuario editado.</b></p><ul>%s</ul>' % ''.join(lineas)
                )
        return resultado

    def unlink(self):
        if self.env.context.get('skip_trazabilidad_usuarios'):
            return super().unlink()
        for usuario in self:
            roles = usuario._nombres_roles_matriz()
            detalle = '<p><b>Usuario eliminado.</b></p>'
            if roles:
                detalle += '<p><b>Roles al eliminar:</b> %s</p>' % escape(', '.join(roles))
            usuario._crear_traza_usuario('eliminacion', detalle)
        return super().unlink()

    rol_coordinador = fields.Selection(
        [('si', 'Sí'), ('no', 'No')],
        string='Coordinador',
        compute='_compute_roles_separados',
        search='_search_rol_coordinador',
        store=False,
    )
    rol_asistente_coordinador = fields.Selection(
        [('si', 'Sí'), ('no', 'No')],
        string='Asistente coordinador',
        compute='_compute_roles_separados',
        search='_search_rol_asistente_coordinador',
        store=False,
    )
    rol_directivo = fields.Selection(
        [('si', 'Sí'), ('no', 'No')],
        string='Directivo',
        compute='_compute_roles_separados',
        search='_search_rol_directivo',
        store=False,
    )

    @api.depends('groups_id')
    def _compute_roles_separados(self):
        grupo_coordinador = self.env.ref(
            'Matriz.group_contratacion_coordinador',
            raise_if_not_found=False,
        )
        grupo_asistente = self.env.ref(
            'Matriz.group_contratacion_asis_coordinador',
            raise_if_not_found=False,
        )
        grupo_directivo = self.env.ref(
            'Matriz.group_contratacion_directivo',
            raise_if_not_found=False,
        )

        for user in self:
            user.rol_coordinador = (
                'si' if grupo_coordinador and grupo_coordinador in user.groups_id else 'no'
            )
            user.rol_asistente_coordinador = (
                'si' if grupo_asistente and grupo_asistente in user.groups_id else 'no'
            )
            user.rol_directivo = (
                'si' if grupo_directivo and grupo_directivo in user.groups_id else 'no'
            )

    @api.model
    def _domain_rol_separado(self, xmlid, operator, value):
        grupo = self.env.ref(xmlid, raise_if_not_found=False)
        if operator not in ('=', '!=') or value not in ('si', 'no'):
            return [('id', '=', 0)]

        buscar_asignados = value == 'si'
        if operator == '!=':
            buscar_asignados = not buscar_asignados
        if not grupo:
            return [('id', '=', 0)] if buscar_asignados else []
        return [('groups_id', 'in' if buscar_asignados else 'not in', [grupo.id])]

    @api.model
    def _search_rol_coordinador(self, operator, value):
        return self._domain_rol_separado(
            'Matriz.group_contratacion_coordinador', operator, value
        )

    @api.model
    def _search_rol_asistente_coordinador(self, operator, value):
        return self._domain_rol_separado(
            'Matriz.group_contratacion_asis_coordinador', operator, value
        )

    @api.model
    def _search_rol_directivo(self, operator, value):
        return self._domain_rol_separado(
            'Matriz.group_contratacion_directivo', operator, value
        )


class ResGroupsRolesAudit(models.Model):
    _inherit = 'res.groups'

    def write(self, vals):
        if self.env.context.get('skip_trazabilidad_usuarios') or 'users' not in vals:
            return super().write(vals)

        usuarios_antes = self.sudo().mapped('users')
        roles_antes = {
            usuario.id: usuario._nombres_roles_matriz()
            for usuario in usuarios_antes
        }
        resultado = super().write(vals)
        usuarios_despues = self.sudo().mapped('users')
        usuarios = usuarios_antes | usuarios_despues

        for usuario in usuarios.exists():
            anteriores = roles_antes.get(usuario.id, [])
            nuevos = usuario._nombres_roles_matriz()
            agregados = sorted(set(nuevos) - set(anteriores), key=str.casefold)
            retirados = sorted(set(anteriores) - set(nuevos), key=str.casefold)
            lineas = []
            if agregados:
                lineas.append(
                    '<li><b>Roles agregados:</b> %s</li>' % escape(', '.join(agregados))
                )
            if retirados:
                lineas.append(
                    '<li><b>Roles retirados:</b> %s</li>' % escape(', '.join(retirados))
                )
            if lineas:
                usuario._crear_traza_usuario(
                    'edicion',
                    '<p><b>Roles modificados desde la configuración del grupo.</b></p>'
                    '<ul>%s</ul>' % ''.join(lineas),
                )
        return resultado
