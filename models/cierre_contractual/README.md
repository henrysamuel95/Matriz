# Cierre contractual

Documentación del componente de cierre contractual de `contrataciones_fuv`.

Este paquete administra tres modalidades de cierre:

- Acta de anulación.
- Liquidación por mutuo acuerdo.
- Liquidación unilateral.

El cierre siempre corresponde al **FC completo**. Los otrosí formalizados pueden participar en la fotografía del valor contractual, pero no se cierran de forma independiente.

## Documentación

- [README_USUARIO.md](README_USUARIO.md): operación del flujo por Coordinación, Financiera, Jurídica, Presupuesto y Soporte.
- [README_TECNICO.md](README_TECNICO.md): modelos, estados, permisos, Drive, devoluciones, reintegros y compatibilidad técnica.

## Código principal

- `cierre_proceso.py`
- `cierre_wizard.py`
- `cierre_drive.py`
- `documentos_anulacion.py`
- `documentos_mutuo_acuerdo.py`
- `documentos_unilateral.py`
- `contrataciones_fuv_extension.py`
- `otrosi_extension.py`
- `soporte.py`
- `constantes.py`
