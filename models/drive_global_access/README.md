# Lectores globales de Google Drive

Documentación del componente `drive_global_access`.

Este paquete administra una lista central de usuarios disponibles para ser asignados como **lectores de Google Drive por proyecto**, sin modificar las listas históricas de acceso de cada proyecto.

## Documentación

- [README_USUARIO.md](README_USUARIO.md): uso funcional para Soporte.
- [README_TECNICO.md](README_TECNICO.md): modelos, permisos, sincronización, trazabilidad y seguridad.

## Archivos principales

- `access_config.py`: configuración central, sincronización y auditoría.
- `access_grant.py`: trazabilidad de permisos de lector creados por esta funcionalidad.

La administración está restringida al grupo `Matriz.group_soporte`.
