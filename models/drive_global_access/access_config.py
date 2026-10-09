import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..drive import GoogleDriveAuth


_logger = logging.getLogger(__name__)


class DriveGlobalAccess(models.Model):
    _name = 'drive.global.access'
    _description = 'Administración de lectores de Google Drive'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Nombre',
        required=True,
        default='Lectores globales de Google Drive',
        tracking=True,
    )
    singleton_key = fields.Char(
        default='global',
        required=True,
        readonly=True,
        copy=False,
    )
    user_ids = fields.Many2many(
        'res.users',
        'drive_global_access_res_users_rel',
        'config_id',
        'user_id',
        string='Usuarios disponibles para asignar',
        domain=[('active', '=', True), ('share', '=', False)],
        tracking=True,
        help=(
            'Usuarios internos disponibles para asignarlos como lectores en cada '
            'proyecto. Estar en esta lista no da acceso automático a ningún proyecto.'
        ),
    )
    grant_ids = fields.One2many(
        'drive.global.access.grant',
        'config_id',
        string='Permisos creados por la lista global',
        readonly=True,
    )
    audit_user_id = fields.Many2one(
        'res.users',
        string='Consultar permisos de un usuario',
        domain=[('active', '=', True), ('share', '=', False)],
        help=(
            'Muestra los permisos de lector registrados para este usuario, sin '
            'consultar Google Drive ni recorrer proyectos.'
        ),
    )
    sync_project_id = fields.Many2one(
        'contratacion',
        string='Proyecto para sincronizar',
        help='Sincroniza únicamente los lectores elegidos en este proyecto.',
    )
    last_sync_at = fields.Datetime(
        string='Última sincronización',
        readonly=True,
        copy=False,
    )
    last_sync_user_id = fields.Many2one(
        'res.users',
        string='Sincronizado por',
        readonly=True,
        copy=False,
    )
    last_sync_summary = fields.Text(
        string='Resultado de la última sincronización',
        readonly=True,
        copy=False,
    )
    sync_pending = fields.Boolean(
        string='Sincronización pendiente',
        readonly=True,
        copy=False,
    )
    sync_last_project_id = fields.Integer(
        string='Último proyecto procesado',
        readonly=True,
        copy=False,
    )
    sync_total_projects = fields.Integer(
        string='Total de proyectos por revisar',
        readonly=True,
        copy=False,
    )
    sync_processed_projects = fields.Integer(
        string='Proyectos procesados',
        readonly=True,
        copy=False,
    )
    sync_added_permissions = fields.Integer(
        string='Permisos creados',
        readonly=True,
        copy=False,
    )
    sync_existing_permissions = fields.Integer(
        string='Permisos existentes',
        readonly=True,
        copy=False,
    )
    sync_missing_folders = fields.Integer(
        string='Proyectos sin carpeta',
        readonly=True,
        copy=False,
    )
    sync_removed_permissions = fields.Integer(
        string='Permisos retirados',
        readonly=True,
        copy=False,
    )
    sync_skipped_removals = fields.Integer(
        string='Retiros omitidos',
        readonly=True,
        copy=False,
    )
    sync_error_count = fields.Integer(
        string='Errores',
        readonly=True,
        copy=False,
    )
    sync_error_log = fields.Text(
        string='Detalle de errores',
        readonly=True,
        copy=False,
    )
    sync_progress_percent = fields.Float(
        string='Avance',
        compute='_compute_sync_progress',
        readonly=True,
    )
    sync_progress_label = fields.Char(
        string='Estado del avance',
        compute='_compute_sync_progress',
        readonly=True,
    )

    _sql_constraints = [
        (
            'drive_global_access_singleton',
            'unique(singleton_key)',
            'Solo puede existir una configuración de lectores globales de Drive.',
        ),
    ]

    @api.depends('sync_pending', 'sync_total_projects', 'sync_processed_projects')
    def _compute_sync_progress(self):
        for record in self:
            total = record.sync_total_projects or 0
            processed = record.sync_processed_projects or 0
            if total:
                record.sync_progress_percent = min(100.0, (processed / total) * 100.0)
            else:
                record.sync_progress_percent = 0.0

            if record.sync_pending:
                record.sync_progress_label = _(
                    'Sincronización en proceso: %(processed)s de %(total)s proyectos.'
                ) % {
                    'processed': processed,
                    'total': total,
                }
            elif total and processed >= total:
                record.sync_progress_label = _(
                    'Sincronización finalizada: %(processed)s proyectos revisados.'
                ) % {'processed': processed}
            else:
                record.sync_progress_label = _('Sin sincronización activa.')

    @api.constrains('user_ids')
    def _check_global_users(self):
        for record in self:
            invalid_users = record.user_ids.filtered(
                lambda user: not user.active or user.share or not (user.email or '').strip()
            )
            if invalid_users:
                raise ValidationError(_(
                    'Todos los lectores globales deben ser usuarios internos activos '
                    'y tener correo electrónico. Revise: %s'
                ) % ', '.join(invalid_users.mapped('name')))

    def _check_support_user(self):
        if self.env.context.get('skip_drive_global_access_support_check'):
            return
        if not self.env.user.has_group('Matriz.group_soporte'):
            raise AccessError(_(
                'Solo el grupo Soporte puede administrar los lectores globales de Drive.'
            ))

    def _try_acquire_sync_lock(self):
        """Evita que el cron y el panel web procesen el mismo lote al tiempo."""
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s)', (847321609,))
        return bool(self.env.cr.fetchone()[0])

    @staticmethod
    def _compact_error_message(error, max_chars=700):
        """Guarda errores de Drive útiles, sin llenar el log con URLs enormes."""
        message = str(error or '').replace('\n', ' ').strip()
        if len(message) > max_chars:
            return '%s...' % message[:max_chars]
        return message

    def _compact_error_log(self, errors, max_lines=30):
        compacted = [
            self._compact_error_message(error)
            for error in errors
            if error
        ]
        return compacted[-max_lines:]

    @api.model_create_multi
    def create(self, vals_list):
        self._check_support_user()
        return super().create(vals_list)

    def write(self, vals):
        self._check_support_user()
        return super().write(vals)

    def unlink(self):
        self._check_support_user()
        raise UserError(_(
            'La configuración global no se elimina. Retire los usuarios de la lista '
            'y ejecute la sincronización si desea revocar los permisos creados por ella.'
        ))

    @api.model
    def action_open_configuration(self):
        self._check_support_user()
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lectores globales de Drive'),
            'res_model': self._name,
            'res_id': config.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _project_folder_data(self, project):
        drive_folder = project.drive_folder_id
        if not drive_folder:
            drive_folder = self.env['drive_folder'].sudo().search([
                ('proyecto_relacionado', '=', project.id),
            ], limit=1, order='sequence, id')
        folder_id = False
        if drive_folder:
            folder_id = drive_folder.folder_guardar_id or drive_folder.google_drive_id
        return drive_folder, folder_id

    @api.model
    def _project_specific_emails(self, project):
        project_users = (
            project.users_with_access
            | project.users_with_access_juridico
            | project.users_with_access_financiero
        )
        return {
            email.strip().lower()
            for email in project_users.mapped('email')
            if email and email.strip()
        }

    @staticmethod
    def _permissions_by_email(folder):
        permissions = folder.GetPermissions()
        return {
            permission.get('emailAddress', '').strip().lower(): permission
            for permission in permissions
            if permission.get('emailAddress')
        }

    def _remove_obsolete_grants(self, drive, project=None, user_ids=None):
        """Retira permisos registrados que ya no están seleccionados en su proyecto."""
        removed = 0
        skipped = 0
        errors = []
        obsolete_grants = self.grant_ids
        if project:
            obsolete_grants = obsolete_grants.filtered(lambda grant: grant.project_id == project)
        if user_ids is not None:
            obsolete_grants = obsolete_grants.filtered(lambda grant: grant.user_id.id in user_ids)
        else:
            obsolete_grants = obsolete_grants.filtered(
                lambda grant: grant.user_id not in grant.project_id.drive_reader_user_ids
            )
        for grant in obsolete_grants:
            project = grant.project_id
            email = (grant.email or '').strip().lower()
            if email in self._project_specific_emails(project):
                # El usuario aún necesita acceso por una lista propia del proyecto.
                grant.unlink()
                skipped += 1
                continue
            _drive_folder, folder_id = self._project_folder_data(project)
            if not folder_id or not grant.permission_id:
                grant.unlink()
                skipped += 1
                continue
            try:
                folder = drive.CreateFile({'id': folder_id})
                current_permission = self._permissions_by_email(folder).get(email)
                if not current_permission:
                    grant.unlink()
                    skipped += 1
                    continue
                if current_permission.get('role') != 'reader':
                    # El permiso fue elevado por otro proceso; esta lista nunca lo degrada
                    # ni lo elimina, aunque el usuario ya no sea lector global.
                    grant.unlink()
                    skipped += 1
                    continue
                folder.DeletePermission(grant.permission_id)
                grant.unlink()
                removed += 1
            except Exception as error:
                _logger.exception(
                    'No fue posible retirar el lector global %s del proyecto %s.',
                    email,
                    project.id,
                )
                errors.append(self._compact_error_message(
                    '%s / %s: %s' % (project.display_name, email, error)
                ))
        return removed, skipped, errors

    def revoke_project_readers(self, project, user_ids):
        """Revoca, solo para un proyecto, permisos creados por esta funcionalidad."""
        self.ensure_one()
        if not user_ids:
            return {'removed': 0, 'skipped': 0, 'errors': []}
        drive = GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            return {
                'removed': 0,
                'skipped': 0,
                'errors': [_('No fue posible autenticar la conexión con Google Drive.')],
            }
        removed, skipped, errors = self._remove_obsolete_grants(
            drive, project=project, user_ids=set(user_ids)
        )
        return {'removed': removed, 'skipped': skipped, 'errors': errors}

    def sync_project_readers(self, project, folder_id=None, drive=None):
        """Aplica a una carpeta los lectores seleccionados en ese proyecto."""
        self.ensure_one()
        if not project or not isinstance(project.id, int):
            return {'added': 0, 'existing': 0, 'errors': []}
        # Esta ruta también se invoca al crear una carpeta. Si el proyecto no
        # tiene lectores elegidos, salir antes de autenticar o consultar Drive.
        selected_users = project.drive_reader_user_ids.filtered(
            lambda user: user.active and not user.share and (user.email or '').strip()
        )
        if not selected_users:
            return {'added': 0, 'existing': 0, 'errors': []}
        if not folder_id:
            _drive_folder, folder_id = self._project_folder_data(project)
        if not folder_id:
            return {'added': 0, 'existing': 0, 'errors': []}
        drive = drive or GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            return {
                'added': 0,
                'existing': 0,
                'errors': [_('No fue posible autenticar la conexión con Google Drive.')],
            }

        folder = drive.CreateFile({'id': folder_id})
        try:
            permissions_by_email = self._permissions_by_email(folder)
        except Exception as error:
            return {
                'added': 0,
                'existing': 0,
                'errors': [self._compact_error_message(
                    '%s: %s' % (project.display_name, error)
                )],
            }

        added = 0
        existing = 0
        errors = []
        Grant = self.env['drive.global.access.grant'].sudo()
        for user in selected_users:
            email = user.email.strip().lower()
            if email in permissions_by_email:
                # reader, writer y permisos superiores se conservan sin cambios.
                existing += 1
                continue
            try:
                permission = folder.InsertPermission({
                    'type': 'user',
                    'value': email,
                    'role': 'reader',
                })
                permission_id = permission.get('id') if permission else False
                if not permission_id:
                    refreshed = self._permissions_by_email(folder).get(email, {})
                    permission_id = refreshed.get('id')
                existing_grant = Grant.search([
                    ('config_id', '=', self.id),
                    ('project_id', '=', project.id),
                    ('user_id', '=', user.id),
                ], limit=1)
                grant_values = {
                    'email': email,
                    'folder_id': folder_id,
                    'permission_id': permission_id,
                }
                if existing_grant:
                    existing_grant.write(grant_values)
                else:
                    Grant.create(dict(
                        grant_values,
                        config_id=self.id,
                        project_id=project.id,
                        user_id=user.id,
                    ))
                permissions_by_email[email] = permission or {
                    'emailAddress': email,
                    'role': 'reader',
                }
                added += 1
            except Exception as error:
                _logger.exception(
                    'No fue posible agregar el lector global %s al proyecto %s.',
                    email,
                    project.id,
                )
                errors.append(self._compact_error_message(
                    '%s / %s: %s' % (project.display_name, email, error)
                ))
        return {'added': added, 'existing': existing, 'errors': errors}

    def action_sync_drive_readers(self):
        """Sincronización acotada a un proyecto; nunca recorre toda la base."""
        self.ensure_one()
        self._check_support_user()
        if not self.sync_project_id:
            raise UserError(_('Seleccione el proyecto que desea sincronizar.'))
        result = self.sync_project_readers(self.sync_project_id)
        if result['errors']:
            raise UserError('\n'.join(result['errors']))
        summary = _(
            'Sincronización puntual del proyecto %(project)s.\n'
            'Permisos creados: %(added)s\n'
            'Permisos existentes: %(existing)s'
        ) % {
            'project': self.sync_project_id.display_name,
            'added': result['added'],
            'existing': result['existing'],
        }
        self.with_context(skip_drive_global_access_support_check=True).write({
            'last_sync_at': fields.Datetime.now(),
            'last_sync_user_id': self.env.user.id,
            'last_sync_summary': summary,
            'sync_pending': False,
        })
        self.message_post(body=summary.replace('\n', '<br/>'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Proyecto sincronizado'),
                'message': summary.replace('\n', ' '),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_cancel_drive_reader_sync(self):
        """Detiene los siguientes lotes sin tocar permisos existentes."""
        self.ensure_one()
        self._check_support_user()
        if not self.sync_pending:
            return True
        summary = _(
            'Sincronización cancelada por %(user)s. No se realizaron más lotes ni '
            'retiros de permisos desde esta acción.'
        ) % {'user': self.env.user.display_name}
        self.with_context(skip_drive_global_access_support_check=True).write({
            'sync_pending': False,
            'last_sync_at': fields.Datetime.now(),
            'last_sync_user_id': self.env.user.id,
            'last_sync_summary': summary,
        })
        self.message_post(body=summary)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sincronización cancelada'),
                'message': _('No se procesarán más lotes de lectores.'),
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_open_user_grants(self):
        """Consulta local y acotada: no hace llamadas a Google Drive."""
        self.ensure_one()
        self._check_support_user()
        if not self.audit_user_id:
            raise UserError(_('Seleccione primero el usuario que desea consultar.'))
        domain = [
            ('config_id', '=', self.id),
            ('user_id', '=', self.audit_user_id.id),
        ]
        grants = self.env['drive.global.access.grant'].sudo().search(domain)
        if not grants:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sin permisos registrados'),
                    'message': _(
                        '%s no tiene permisos de lector registrados por esta funcionalidad.'
                    ) % self.audit_user_id.display_name,
                    'type': 'warning',
                    'sticky': False,
                },
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Permisos de Drive de %s') % self.audit_user_id.display_name,
            'res_model': 'drive.global.access.grant',
            'view_mode': 'tree,form',
            'views': [(self.env.ref('Matriz.view_drive_global_access_grant_tree').id, 'tree')],
            'domain': domain,
            'context': {'search_default_group_by_project_id': 1},
            'target': 'new',
        }

    def _sync_progress_payload(self):
        self.ensure_one()
        total = self.sync_total_projects or 0
        processed = self.sync_processed_projects or 0
        percent = (processed / total * 100.0) if total else 0.0
        if self.sync_pending:
            label = _('Procesando %(processed)s de %(total)s proyectos') % {
                'processed': processed,
                'total': total,
            }
        else:
            label = _('Sincronización finalizada: %(processed)s de %(total)s proyectos') % {
                'processed': processed,
                'total': total,
            }
        return {
            'pending': self.sync_pending,
            'processed': processed,
            'total': total,
            'percent': min(percent, 100.0),
            'label': label,
            'added': self.sync_added_permissions or 0,
            'existing': self.sync_existing_permissions or 0,
            'missing': self.sync_missing_folders or 0,
            'removed': self.sync_removed_permissions or 0,
            'skipped': self.sync_skipped_removals or 0,
            'errors': self.sync_error_count or 0,
            'busy': False,
        }

    def action_process_drive_reader_batch(self, batch_size=5):
        self.ensure_one()
        self._check_support_user()
        if self.sync_pending:
            processed = self._process_next_drive_reader_batch(batch_size=batch_size)
            payload = self._sync_progress_payload()
            if processed is False:
                payload.update({
                    'busy': True,
                    'label': _('Otro lote ya se está procesando. Esperando turno...'),
                })
            return payload
        return self._sync_progress_payload()

    def action_get_drive_sync_progress(self):
        self.ensure_one()
        self._check_support_user()
        return self._sync_progress_payload()

    def action_refresh_sync_progress(self):
        self.ensure_one()
        self._check_support_user()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lectores globales de Drive'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def cron_process_drive_reader_batches(self):
        """Procesa la sincronización global en lotes pequeños para no bloquear Odoo."""
        configs = self.sudo().search([('sync_pending', '=', True)], limit=1)
        for config in configs:
            config._process_next_drive_reader_batch(batch_size=5)

    def _process_next_drive_reader_batch(self, batch_size=5):
        self.ensure_one()
        if not self._try_acquire_sync_lock():
            _logger.info(
                'Lectores globales de Drive: se omitió un lote porque otra '
                'sincronización ya está en ejecución.'
            )
            return False

        drive = GoogleDriveAuth.get_instance().get_drive()
        if not drive:
            raise UserError(_('No fue posible autenticar la conexión con Google Drive.'))

        Project = self.env['contratacion'].sudo()
        projects = Project.search([
            ('id', '>', self.sync_last_project_id or 0),
        ], limit=batch_size, order='id')

        if not projects:
            # Los retiros se ejecutan al quitar un lector de su proyecto, no al
            # sincronizar toda la base. Así una sincronización nunca revoca en masa
            # permisos históricos que aún estén en revisión.
            removed, removal_skipped, removal_errors = 0, 0, []
            previous_errors = list(filter(None, (self.sync_error_log or '').split('\n')))
            errors_total = (self.sync_error_count or len(previous_errors)) + len(removal_errors)
            errors = self._compact_error_log(previous_errors + removal_errors)
            summary = _(
                'Sincronización finalizada por lotes.\n'
                'Proyectos revisados: %(projects)s\n'
                'Permisos de lector creados: %(added)s\n'
                'Permisos existentes conservados: %(existing)s\n'
                'Permisos retirados por proyecto: %(removed)s\n'
                'Retiros omitidos por acceso específico u otra condición: %(skipped)s\n'
                'Proyectos sin carpeta principal: %(missing)s\n'
                'Errores: %(errors)s'
            ) % {
                'projects': self.sync_processed_projects,
                'added': self.sync_added_permissions,
                'existing': self.sync_existing_permissions,
                'removed': removed,
                'skipped': removal_skipped,
                'missing': self.sync_missing_folders,
                'errors': errors_total,
            }
            self.with_context(skip_drive_global_access_support_check=True).write({
                'sync_pending': False,
                'sync_removed_permissions': removed,
                'sync_skipped_removals': removal_skipped,
                'sync_error_count': errors_total,
                'sync_error_log': '\n'.join(errors) if errors else False,
                'last_sync_at': fields.Datetime.now(),
                'last_sync_summary': summary,
            })
            self.message_post(body=summary.replace('\n', '<br/>'))
            return True

        added = 0
        existing = 0
        missing_folder = 0
        previous_errors = list(filter(None, (self.sync_error_log or '').split('\n')))
        new_errors = []

        for project in projects:
            _drive_folder, folder_id = self._project_folder_data(project)
            if not folder_id:
                missing_folder += 1
                continue
            result = self.sync_project_readers(project, folder_id=folder_id, drive=drive)
            added += result['added']
            existing += result['existing']
            new_errors.extend(result['errors'])

        processed = self.sync_processed_projects + len(projects)
        total = self.sync_total_projects or Project.search_count([])
        errors_total = (self.sync_error_count or len(previous_errors)) + len(new_errors)
        errors = self._compact_error_log(previous_errors + new_errors)
        summary = _(
            'Sincronización en proceso por lotes: %(processed)s/%(total)s proyectos.\n'
            'Último lote procesado: %(batch)s proyectos.\n'
            'Permisos creados acumulados: %(added)s\n'
            'Permisos existentes acumulados: %(existing)s\n'
            'Proyectos sin carpeta acumulados: %(missing)s\n'
            'Errores acumulados: %(errors)s'
        ) % {
            'processed': processed,
            'total': total,
            'batch': len(projects),
            'added': self.sync_added_permissions + added,
            'existing': self.sync_existing_permissions + existing,
            'missing': self.sync_missing_folders + missing_folder,
            'errors': errors_total,
        }
        self.with_context(skip_drive_global_access_support_check=True).write({
            'sync_last_project_id': projects[-1].id,
            'sync_total_projects': total,
            'sync_processed_projects': processed,
            'sync_added_permissions': self.sync_added_permissions + added,
            'sync_existing_permissions': self.sync_existing_permissions + existing,
            'sync_missing_folders': self.sync_missing_folders + missing_folder,
            'sync_error_count': errors_total,
            'sync_error_log': '\n'.join(errors) if errors else False,
            'last_sync_at': fields.Datetime.now(),
            'last_sync_summary': summary,
        })
        return True
