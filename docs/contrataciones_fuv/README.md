# Contrataciones FUV

Esta carpeta reúne la documentación de los dos recorridos que conviven en el
modelo `contrataciones_fuv`:

1. [README 1: Flujo nuevo](README_FLUJO_NUEVO.md): aplica desde el **24 de julio
   de 2026** por defecto. Incluye Ventanilla, Presupuesto y Dirección Jurídica.
2. [README 2: Flujo viejo o anterior](README_FLUJO_ANTERIOR.md): aplica a los
   registros anteriores a esa fecha por defecto. Incluye Financiera y las
   rutas históricas de Jurídico y Recursos Humanos.

## Cómo saber cuál aplica

El campo calculado `flujo_contratacion_version` indica **Flujo nuevo** o
**Flujo anterior**. `usa_flujo_contratacion_nuevo` es su equivalente booleano.
El cálculo compara `create_date` (o `fch_creacion` si no existe) con la fecha
configurada en `Matriz.fecha_inicio_nuevo_flujo_contratacion`. Sin configuración
específica, el corte predeterminado es el **24 de julio de 2026**. En un
registro aún sin fecha se usa la fecha actual como respaldo.

El campo `estado_contratacion` es compartido, pero las acciones y validaciones
dependen de la versión del flujo y del tipo de persona. No debe inferirse el
flujo únicamente por el nombre del estado.

**Fuentes principales:**
[`contrataciones_fuv_aprobaciones.py`](../../models/contrataciones_fuv_aprobaciones.py),
[`flujo_configuracion.py`](../../models/flujo_configuracion.py),
[`contrataciones_fuv.py`](../../models/contrataciones_fuv.py) y
[`contrataciones_fuv.xml`](../../views/contrataciones_fuv.xml).

Las guías describen el recorrido normal de contratación; el cierre contractual,
la finalización normal, las pólizas y los otrosíes tienen reglas adicionales.
