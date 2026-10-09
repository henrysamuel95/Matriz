# Lectores globales de Google Drive - Guía de usuario

## ¿Qué hace?

Este componente permite que **Soporte** mantenga una lista central de usuarios que pueden ser seleccionados como lectores de Google Drive en proyectos específicos.

Estar incluido en la lista global **no da acceso automático** a todos los proyectos.

El acceso real se otorga cuando el usuario es seleccionado como lector dentro de un proyecto concreto.

## ¿Quién puede usarlo?

Solo usuarios del grupo **Soporte** pueden administrar esta configuración.

Ruta funcional:

```text
Lectores globales de Drive
```

## Lista de usuarios disponibles

La configuración global permite seleccionar usuarios internos activos.

Para ser válidos deben:

- estar activos;
- ser usuarios internos;
- tener correo electrónico configurado.

La lista funciona como catálogo de usuarios disponibles para asignación.

## Asignar un lector a un proyecto

En el proyecto, Soporte puede usar el campo:

**Lectores de Drive (solo este proyecto)**

Allí solo aparecen usuarios incluidos previamente en la lista global.

Al agregar un usuario:

1. el sistema identifica la carpeta principal de Drive del proyecto;
2. consulta los permisos existentes;
3. si el correo ya tiene acceso, no crea otro permiso;
4. si no tiene acceso, crea un permiso `reader`;
5. registra localmente la trazabilidad del permiso creado.

## Qué pasa si el usuario ya tiene acceso

Si el usuario ya tiene permiso en Google Drive:

- el sistema lo conserva;
- no crea un duplicado;
- no reduce permisos superiores.

Por ejemplo, si ya es `writer`, no se cambia a `reader`.

## Retirar un lector

Al retirar un usuario de **Lectores de Drive (solo este proyecto)**, el sistema intenta eliminar únicamente el permiso de lector que fue creado por esta funcionalidad.

No se elimina el acceso si:

- el usuario todavía pertenece a una lista específica del proyecto;
- el permiso actual fue elevado a un nivel superior;
- el permiso ya no existe en Drive;
- falta información suficiente para identificarlo de forma segura.

En esos casos se elimina o ajusta únicamente la trazabilidad local correspondiente.

## Listas que no modifica

Esta funcionalidad no sustituye ni modifica las listas existentes del proyecto:

- usuarios con acceso general;
- usuarios de Jurídica;
- usuarios de Financiera.

Si un usuario sigue necesitando acceso por una de esas listas, el permiso no se elimina.

## Sincronización manual de un proyecto

Desde la configuración global puede seleccionarse un proyecto específico y ejecutar una sincronización manual.

La sincronización manual:

- procesa solo ese proyecto;
- crea permisos faltantes;
- conserva permisos existentes;
- no recorre toda la base;
- informa cuántos permisos se crearon y cuántos ya existían.

## Consulta de permisos por usuario

Soporte puede seleccionar un usuario y consultar los permisos registrados por esta funcionalidad.

La consulta:

- usa la trazabilidad guardada en Odoo;
- no recorre todos los proyectos;
- no realiza una consulta masiva a Google Drive;
- muestra los proyectos donde existe un permiso registrado.

Desde esa vista también puede retirarse un permiso concreto del proyecto.

## Sincronización global por lotes

El código conserva soporte para una sincronización global por lotes.

Cuando existe una sincronización pendiente:

- los proyectos se procesan en lotes pequeños;
- se registra el avance;
- se contabilizan permisos creados, existentes, errores y proyectos sin carpeta;
- un bloqueo evita que dos lotes trabajen al mismo tiempo.

La sincronización puede cancelarse antes de continuar con nuevos lotes. Cancelarla no elimina permisos ya existentes.

## Qué no hace

El componente no:

- da acceso automático a todos los proyectos por estar en la lista global;
- convierte lectores en editores;
- reduce permisos `writer` a `reader`;
- elimina accesos pertenecientes a otros mecanismos del proyecto;
- borra la configuración global.

## Recomendaciones de uso

Antes de asignar lectores:

1. confirme que el usuario tenga correo correcto;
2. confirme que el proyecto tenga carpeta principal de Drive;
3. agregue solo los usuarios que realmente necesiten consulta;
4. use la auditoría por usuario para revisar accesos históricos;
5. retire el lector desde el proyecto cuando deje de necesitar acceso.

Si una sincronización devuelve errores de autenticación o permisos de Drive, revise primero las credenciales y permisos del entorno antes de modificar código.
