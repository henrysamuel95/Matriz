# Cierre contractual - Guía de usuario

## ¿Qué hace?

El módulo permite cerrar un contrato FUV mediante uno de estos procesos:

1. **Acta de anulación:** para un contrato que no inició ejecución.
2. **Liquidación por mutuo acuerdo:** cuando las partes aceptan la liquidación.
3. **Liquidación unilateral:** cuando el contratista no firma o no acepta la liquidación.

El cierre se realiza sobre el **contrato FUV completo**. No se cierra un otrosí por separado.

## Guía dentro del proceso

En la cabecera de cualquier proceso de cierre aparece el botón **Guía de usuario**.
Puede abrirlo cualquier usuario interno que tenga acceso al proceso, sin importar
su rol dentro del flujo. La ventana resume las etapas, los responsables, los
documentos, las devoluciones, los accesos y las notificaciones. Consultarla no
modifica el estado ni los datos del cierre.

## ¿Quién puede iniciar un cierre?

Pueden iniciar el proceso **Coordinación**, **Jurídica** o **Dirección Jurídica**.

Después de creada la solicitud, también **Asistencia de Coordinación** puede abrir
**Ver cierre** junto con los roles anteriores. Asistencia no inicia una solicitud
nueva.

El contrato debe estar en estado **Contratado** y no puede tener otro cierre activo.

Al crear la solicitud se registran las razones del cierre y el sistema conserva una fotografía de los valores contractuales vigentes en ese momento.

## Flujo normal

En la etapa de certificación, la persona responsable debe tener el rol
**Financiera cierre contrato**. No necesita el rol general **Financiero** ni
estar vinculada en las listas de acceso del proyecto.

Dirección Jurídica puede asignar como responsable a cualquier usuario activo
con el rol **Jurídico**; tampoco es necesario que esté en la lista del proyecto.
Al enviar el proceso a Financiera o asignarlo a Jurídica, el sistema concede
acceso lector a la carpeta **Cierre contractual**. Si el usuario ya tiene acceso,
no crea un permiso duplicado.

La persona de Financiera que carga el certificado o envía el proceso a Jurídica
queda registrada como **Responsable de Financiera**. Cuando Coordinación o
Jurídica carga el acta firmada, esa persona recibe un correo informativo con un
botón para abrir el mismo proceso de cierre.

### Acta de anulación

```text
Coordinación
    ↓
Financiera
    ↓
Jurídica
    ↓
Coordinación - firma
    ↓
Presupuesto - liberación RFC
    ↓
Finalizado
```

En la anulación se parte de que no existió ejecución. El sistema utiliza como referencia el valor del contrato y prepara la liberación correspondiente.

### Liquidación por mutuo acuerdo

```text
Coordinación
    ↓
Financiera
    ↓
Jurídica
    ↓
Coordinación - firma
    ↓
Presupuesto - liberación RFC
    ↓
Finalizado
```

Financiera certifica la ejecución y el saldo por liberar. Jurídica prepara el acta y Coordinación gestiona el documento firmado antes de la liberación.

### Liquidación unilateral

```text
Coordinación
    ↓
Financiera
    ↓
Jurídica
    ↓
Liberación RFC
    ↓
Finalizado
```

La liquidación unilateral no pasa por la etapa normal de firma de Coordinación. Jurídica formaliza el acta, el sistema notifica al contratista y el proceso continúa a liberación.

## Observaciones

Cada etapa utiliza el campo **Observaciones de la etapa**.

- Enviar, aprobar o finalizar: la observación es opcional.
- Devolver: la observación es obligatoria.
- La información queda registrada en el chatter.
- Después de una acción realizada correctamente, el campo se limpia.

## Devoluciones

El sistema conserva la etapa que originó la devolución para que una corrección pueda regresar al punto correspondiente.

Ejemplos:

- Financiera puede devolver a Coordinación.
- Jurídica puede devolver a Financiera o a Coordinación.
- Coordinación, durante firma, puede devolver a Jurídica.
- Presupuesto puede devolver a la etapa anterior.
- En una liquidación unilateral, la devolución desde liberación regresa a Jurídica.

Las devoluciones quedan registradas en la trazabilidad del cierre.

## Documentos

Todos los documentos del proceso se guardan en la subcarpeta:

```text
Carpeta del contratista
└── Cierre contractual
```

La carga depende del tipo de cierre y de la etapa actual.

### Anulación

Entre los documentos gestionados se encuentran:

- Solicitud de anulación.
- Certificado financiero.
- Borrador del acta.
- Acta de anulación firmada.
- ARC / soporte de liberación.

### Mutuo acuerdo

Entre los documentos gestionados se encuentran:

- Soporte de solicitud.
- Certificado financiero.
- Acta preparada para firma.
- Acta firmada.
- Soporte de liberación de saldos.

### Unilateral

Entre los documentos gestionados se encuentran:

- Soporte de solicitud.
- Certificado financiero.
- Acta unilateral.
- Soporte de liberación RFC.

El sistema valida que el documento se cargue en la etapa correcta y por un usuario autorizado.

## Recursos liberados

Al finalizar el cierre se registra el valor efectivamente liberado.

El sistema:

- no permite valores negativos;
- no permite liberar más que el valor de referencia permitido;
- en mutuo acuerdo y unilateral también controla el saldo certificado por Financiera;
- conserva el fondo original del contrato;
- reintegra el valor liberado al mismo fondo del que salió el recurso.

Los fondos contemplados son Producción, Costos FUV y Contratos VCO.

## Otrosí

El cierre **no selecciona ni finaliza un otrosí individual**.

Los otrosí formalizados que modifican valor se utilizan para construir la fotografía contractual y conservar información histórica del contrato. Al guardar una solicitud nueva, el proceso fuerza el objeto del cierre al contrato completo.

## Soporte

El grupo **Soporte** cuenta con herramientas extraordinarias para recuperar procesos con inconsistencias.

Puede:

- cambiar un cierre entre etapas operativas;
- reparar la relación con la carpeta `Cierre contractual`;
- limpiar una ruta de devolución atascada;
- abrir el FC relacionado;
- consultar un diagnóstico básico de inconsistencias.

Cada corrección exige un motivo y deja trazabilidad tanto en el cierre como en el contrato.

Soporte **no puede forzar** un cierre a `Finalizado` o `Cancelado`, ni reabrir procesos que ya llegaron a esos estados, porque pueden existir efectos contractuales y financieros.

## Antes de finalizar

Revise que:

- los documentos obligatorios del tipo de cierre estén cargados;
- Financiera haya certificado el saldo cuando aplique;
- el acta correspondiente esté preparada y/o firmada;
- el RFC esté marcado como liberado;
- el valor liberado sea correcto;
- no exista una devolución pendiente sin resolver.

Una vez finalizado, el valor liberado queda registrado en el cierre y en la trazabilidad del FC.
