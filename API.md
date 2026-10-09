# API pública — Proyectos

Documentación del endpoint `GET /api/v1/proyectos`, definido en [controllers/api_proyectos.py](controllers/api_proyectos.py). Devuelve la información anidada completa de uno o todos los proyectos (`contratacion`) del sistema: contratos de personal (FUV), órdenes de compra, viáticos, presupuesto, ejecución técnica, cierre y pagos/cobros.

## Autenticación

El endpoint exige el header `api-key` con una API Key personal de Odoo. La clave
se valida contra `res.users.apikeys` y su propietario debe pertenecer al grupo
administrador `Matriz.group_soporte`.

```
api-key: <API Key personal de un usuario de Soporte>
```

> Nota: este endpoint es distinto de `/api/contrataciones_fuv` (`controllers/api_contrataciones.py`), que usa `Authorization: Bearer <token>` con un token estático — son dos mecanismos de auth distintos, no intercambiables.

### Configurar el `api-key` en Odoo

1. Iniciar sesión con el usuario que usará la integración.
2. Confirmar que el usuario pertenece a `Matriz.group_soporte`.
3. Abrir **Preferencias > Seguridad de la cuenta > Nueva API Key**.
4. Crear la clave y guardarla en el gestor de secretos de la integración. Odoo
   solo muestra el valor completo al crearla.
5. Enviar ese valor en el header `api-key` de cada petición.

La implementación acepta claves con el alcance dedicado
`matriz_api_proyectos` y, por compatibilidad con las claves creadas desde el
panel estándar, claves sin alcance. Las claves son revocables individualmente;
no se almacenan en este repositorio ni en `ir.config_parameter`.

## `GET /api/v1/proyectos`

### Parámetros (query string)

| Parámetro | Requerido | Descripción |
|---|---|---|
| `id` | No | Id interno de Odoo (`contratacion.id`) de un proyecto puntual. Si se omite, devuelve **todos** los proyectos — la respuesta puede pesar varios MB. |

### Respuesta — `200 OK`

```json
{
  "status": "success",
  "database": "odoo_local",
  "sql_count": 129,
  "total_proyectos": 129,
  "data": [ { ...proyecto... } ]
}
```

Cada elemento de `data` tiene esta forma:

| Campo | Tipo | Descripción |
|---|---|---|
| `id_interno_odoo` | int | Id de `contratacion` |
| `nombre_proyecto` | string | `new_name` o `name` |
| `estado_coordinador` | string | Campo `coordinador` del proyecto |
| `url_drive` | string | Enlace a la carpeta raíz en Google Drive (**no** lista archivos — ver [Fuera de alcance](#fuera-de-alcance)) |
| `finanzas_odoo` | object | `total_compras_bienes`, `total_compras_servicios`, `suma_costos_fuv` |
| `pagos_siesa` | array | Ver [Pagos y cobros SIESA](#pagos-y-cobros-siesa) |
| `cobros_siesa` | array | Ver [Pagos y cobros SIESA](#pagos-y-cobros-siesa) |
| `presupuesto_mcb` | array | Ver [MCB](#presupuesto-mcb) |
| `ejecucion` | array | Ver [Ejecución](#ejecución) |
| `cierre` | array | Ver [Cierre del proyecto](#cierre-del-proyecto) |
| `ficha_resumen` | array | Ver [Ficha resumen](#ficha-resumen) |
| `contratos_personal_fuv` | array | Ver [FUV](#contratos-de-personal-fuv) |
| `ordenes_servicios` | array | Ver [Órdenes de compra](#órdenes-de-compra) |
| `ordenes_bienes` | array | Ver [Órdenes de compra](#órdenes-de-compra) |
| `viaticos` | array | `id_viatico`, `total_gastos` |

#### Contratos de personal (FUV)

Cada elemento de `contratos_personal_fuv` (modelo `contrataciones_fuv`):

| Campo | Descripción |
|---|---|
| `id`, `estado` (`estado_contratacion`), `valor_total`, `fecha_inicio`, `fecha_fin`, `objeto` | Datos del contrato |
| `contratista` | Objeto con `nombre`, `clase_persona` (`persona_natural`/`persona_juridica`), `identificacion` (NIT si es jurídica, cédula si es natural), `razon_social`, `representante_legal`, `correo`, `direccion`, `telefono` |
| `adiciones_otrosi` | Array de otrosí (`otro_si`): `id`, `estado`, `valor_adicion`, `fecha_inicio`, `fecha_fin` |
| `cierre_proceso` | Array de liquidaciones del contrato (`contrataciones_fuv.cierre.proceso`): `id`, `tipo_acta`, `estado`, `fecha_solicitud`, `porcentaje_ejecucion_financiera`, `saldo_por_liberar`, `rfc_liberado`, `fecha_liberacion_rfc` |

#### Órdenes de compra

`ordenes_servicios` (`orden_compra_servicios`) y `ordenes_bienes` (`orden_compra_bienes`), misma forma: `id`, `estado`, `proveedor`, `nit`, `descripcion`, `valor_total`.

#### Presupuesto MCB

Cada elemento de `presupuesto_mcb` (modelo `mcb`): `id`, `centro_costo`, `valor_total_final`, `valor_proyecto_convenio`, `suma_adicion_otrosi`, `contrapartida`, `suma_polizas`, `costos_financieros_total`, `costos_tributarios_total`, `costos_produccion_total`, `total_a_facturar`.

#### Ejecución

Cada elemento de `ejecucion` (modelo `ejecucion`, raíz del árbol de marco lógico):

```
ejecucion
├── estado
├── actividades[]        (modelo actividad — rubros presupuestales)
│     id, nombre, estado, presupuesto_total_global,
│     ejecutado_total_global, porcentaje_ejecucion
└── entregables[]         (modelo entregable)
      └── entregables_2[] (cronograma real)
            id, nombre, verificador, unidad_medida, cantidad,
            porcentaje, duracion_periodos, total_porcentaje,
            actividades_completas, actividades_en_progreso,
            actividades_retrasadas, actividades_pendientes
```

No incluye el historial de revisiones presupuestales (`new_mcb_ids`) ni las 30 sub-ventanas de periodo por `entregables_2` (`ventanaN_id`) — es detalle de auditoría, no de estado actual.

#### Cierre del proyecto

Cada elemento de `cierre` (modelo `cierre`, cierre del proyecto completo — **no** confundir con `cierre_proceso` de cada FUV, que es la liquidación individual del contrato): `id`, `estado` (`state`), `cerrado`, `fecha_info_final`, `fecha_acta_final`, `coordinador_approved`, `financiero_approved`, `juridico_approved`, `motivo_rechazo_juridico`, `motivo_rechazo_financiero`.

#### Ficha resumen

Cada elemento de `ficha_resumen` (modelo `ficharesumen`): `id`, `estado_aprobacion` (`aprobado`), `valor_contratar`, `valor_total_proyecto`, `objeto`, `modalidad_contratacion`, `fecha_aprobacion`.

#### Pagos y cobros SIESA

`pagos_siesa` (`pagos.siesa.line`): `id`, `estado`, `contratista`, `nit`, `fecha_pago`, `valor_pagado`, `neto`, `bruto`.
`cobros_siesa` (`cobros.siesa.line`): `id`, `estado`, `cliente`, `fecha_recaudo`, `por_cobrar`, `recaudado`, `pendiente`.

**Toda cifra de pago o cobro del proyecto/centro de costo viene únicamente de estas dos ramas** (fuente: SIESA/UnoEE, vía `pymssql`, sincronizada por cron o botón manual, nunca en `read()`). No se expone `cuentas_de_cobro` ni `anticipos`/`legalizaciones` (movimiento de dinero registrado solo en Odoo, no en SIESA).

### Errores

| Status | Causa | Cuerpo |
|---|---|---|
| `400` | `id` no numérico | `{"status":"error","message":"El parámetro 'id' debe ser numérico."}` |
| `401` | Falta el header `api-key` | `{"status":"error","message":"Falta el header 'api-key' en la petición."}` |
| `401` | API Key inválida o revocada | `{"status":"error","message":"API Key inválida."}` |
| `403` | La API Key es válida, pero su propietario no pertenece a Soporte | `{"status":"error","message":"El usuario asociado a esta API Key no tiene permisos de administrador."}` |
| `id` inexistente | — | `200 OK` con `"data": []` (no es un error) |

### Ejemplo

```bash
curl -H "api-key: <API Key personal>" \
  "http://localhost:8080/api/v1/proyectos?id=10"
```

## Fuera de alcance

- **Archivos/documentos**: `url_drive` solo trae el enlace a la carpeta raíz del proyecto en Google Drive; no lista los archivos de los sistemas documentales del módulo.
- **Bypass de control de acceso**: el endpoint usa `SELECT id FROM contratacion` por SQL crudo + `sudo()` para evitar el filtro de visibilidad por usuario que trae `contratacion.search()` (ver `models/contratacion.py:392-423`). Esto es intencional (la API necesita ver todos los proyectos, no solo los del usuario que configuró la `api-key`), pero implica que **no hay control de acceso por proyecto** — quien tenga la `api-key` ve todo.
