# Servicio `drive.firma.service`

`drive.firma.service` es el servicio reutilizable para solicitar y completar
firmas electrónicas públicas sobre documentos del modelo `drive`. Es un
`models.AbstractModel`, no crea una tabla propia y se obtiene desde el entorno
ORM de Odoo.

El servicio recibe un documento `drive` y los datos explícitos del firmante.
No descubre contratistas, no decide si un documento es firmable para un flujo
de negocio y no convierte Word, Excel o imágenes a PDF. El documento debe ser
un PDF legible disponible en Google Drive.

## Uso básico

El modelo que inicia la firma debe validar sus permisos y reglas de negocio:

```python
servicio = self.env["drive.firma.service"]
solicitud = servicio.solicitar_firma(
    drive_record=documento_drive,
    email_firmante="persona@example.com",
    nombre_firmante="Nombre de la persona",
    horas_vigencia=72,
)
```

El resultado es un registro `drive.firma.solicitud` con token, vencimiento y
snapshot de identidad. El correo con el enlace se envía durante la operación.

Un documento independiente de contratación también es válido:

```python
documento = self.env["drive"].browse(documento_id).exists()
if not documento:
    raise UserError("El documento no existe.")

solicitud = self.env["drive.firma.service"].solicitar_firma(
    documento, persona.email, persona.name,
)
```

El llamador debe comprobar que el usuario puede iniciar la firma, que el
documento está en estado final y que el firmante corresponde al documento. En
contratación FUV, esa comprobación continúa en
`drive.action_enviar_para_firmar()` mediante `_CAMPOS_FIRMABLES`.

## API interna

Los métodos siguientes están marcados con `@api.private`: se usan desde
Python/ORM y no se exponen como métodos RPC públicos.

### `solicitar_firma`

```python
solicitud = servicio.solicitar_firma(
    drive_record,
    email_firmante,
    nombre_firmante,
    horas_vigencia=72,
    enviado_por=None,
)
```

Valida un único registro existente de `drive`, archivo remoto, PDF legible,
correo y nombre. Bloquea el documento, cancela solicitudes pendientes previas
y crea una solicitud con token aleatorio. `enviado_por` permite indicar el
usuario emisor; si se omite se usa el usuario actual. La vigencia permitida es
mayor que cero y como máximo 8760 horas.

### `reenviar_solicitud` y `revocar_solicitud`

```python
servicio.reenviar_solicitud(solicitud, horas_vigencia=72)
servicio.revocar_solicitud(solicitud)
```

El reenvío conserva el token, renueva el vencimiento y vuelve a enviar el
correo. La revocación cambia el estado a `cancelado` y devuelve `True`.
Ambas operaciones requieren una solicitud pendiente.

### `validar_acceso_publico`

```python
solicitud = servicio.validar_acceso_publico(
    solicitud_id,
    token,
    bloquear=False,
    exigir_pendiente=True,
)
```

Compara el token de forma constante y devuelve la solicitud válida o un
recordset vacío. No revela si falló el ID o el token. `bloquear=True` bloquea
documento y solicitud antes de una operación irreversible, invalida la caché y
revalida estado y vencimiento. Una solicitud vencida pasa a `expirado`.

`exigir_pendiente=False` permite mostrar el estado terminal en el formulario
público; no habilita preview ni firma.

### `obtener_pdf_preview`

```python
pdf_bytes = servicio.obtener_pdf_preview(solicitud)
```

Requiere una solicitud pendiente y vigente, descarga el archivo actual de
Drive y valida el PDF. Devuelve bytes; el controller construye la respuesta
HTTP.

### `procesar_firma`

```python
solicitud = servicio.procesar_firma(
    solicitud,
    imagen_b64,
    consentimiento_aceptado=True,
)
```

El consentimiento debe ser exactamente `True`. La imagen debe ser PNG o JPEG
válida, con máximo 5 MiB decodificados y 16 millones de píxeles.

El método bloquea la solicitud, valida PDF e imagen, estampa la firma, sube el
PDF con `skip_drive_sync=True`, comprueba el booleano de
`upload_to_drive()`, guarda estado, consentimiento y hash SHA-256, crea la
trazabilidad y envía la copia firmada.

La firma se coloca sobre la última aparición global de
`{{FIRMA_CONTRATISTA}}`; si no existe, se usa la zona inferior de la última
página. Se conservan páginas rotadas y CropBox no estándar.

Si falla la descarga, validación o subida, la solicitud permanece pendiente y
el token puede reintentarse. Si falla la persistencia de Odoo después de subir,
se intenta restaurar el PDF original en Drive. Odoo y Drive no comparten una
transacción atómica. Un fallo del correo de copia se registra sin deshacer una
firma ya subida.

### `verificar_integridad` y `expirar_solicitudes`

```python
integro = servicio.verificar_integridad(solicitud)
cantidad = servicio.expirar_solicitudes()
```

`verificar_integridad()` compara el SHA-256 del PDF actual con
`documento_hash` y devuelve un booleano. `expirar_solicitudes()` cambia a
`expirado` las solicitudes pendientes vencidas y devuelve la cantidad afectada.
El cron existente lo llama mediante el adaptador del modelo persistente.

## Límites y errores

| Recurso | Límite |
|---|---:|
| PDF remoto | 25 MiB |
| Imagen decodificada | 5 MiB |
| Píxeles de imagen | 16.000.000 |
| Vigencia máxima | 8760 horas |

El PDF se valida con PyPDF2 y se estampa con PyMuPDF cuando está disponible.
Sin PyMuPDF se usa el fallback PyPDF2/ReportLab. Los errores esperables se
expresan como `odoo.exceptions.UserError`.

## Integración HTTP

El controller conserva las rutas:

```text
GET  /public/firma_documento/<solicitud_id>/<token>
GET  /public/firma_documento/<solicitud_id>/<token>/documento.pdf
POST /public/firma_documento/submit
```

El controller solo parsea HTTP, valida acceso, obtiene preview, procesa la
firma y renderiza QWeb. No debe incluir SQL, acceso directo a Drive, hashing ni
transiciones de negocio.

## Compatibilidad

`drive.firma.solicitud` conserva tabla, campos, vistas, ACL y métodos históricos
como adaptadores. También se mantienen las acciones de `drive`:

```text
action_enviar_para_firmar
action_reenviar_solicitud_firma
action_revocar_solicitud_firma
action_verificar_integridad_firma
```

No se deben crear solicitudes manualmente ni duplicar lógica del controller,
Drive o los templates. Para compatibilidad, el modelo persistente permite:

```python
solicitud.procesar_firma(imagen_b64, consentimiento_aceptado=True)
```

## Pruebas

La cobertura está en `tests/test_drive_firma_service.py` (contrato genérico,
concurrencia, PDF, Drive, correo e integridad),
`tests/test_drive_firma_solicitud.py` (flujo FUV),
`tests/test_firma_documento_controller.py` (adaptador HTTP) y
`tests/test_firma_documento_http.py` (rutas HTTP reales).

Validación focalizada:

```bash
docker compose run --rm --no-deps gofuv odoo \
  -d odoo_local --db_host=fuvdb --db_user=odoo --db_password=odoo \
  -u Matriz --test-enable \
  --test-tags /Matriz:TestDriveFirmaSolicitud,/Matriz:TestDriveFirmaService,/Matriz:TestFirmaDocumentoController,/Matriz:TestFirmaDocumentoHttp \
  --stop-after-init --http-port=8079 --max-cron-threads=0 --log-level=test
```

