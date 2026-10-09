"""Genera fichas de flujo verificables desde los modelos Odoo cargados por Matriz.

Uso: python tools/generar_readmes_modelos.py
No importa Odoo ni ejecuta lógica de negocio: inspecciona el código fuente con AST.
"""

from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
DEST = Path(os.environ.get("FLUJOS_DEST", ROOT.parent.parent / "Flujos_Modelos"))

# Modelos con etapas propias que pueden cambiar durante un trámite o proceso.
# Se excluyen filtros, estados relacionados, informes y asistentes que solo
# muestran o modifican el estado de otro registro.
FLOW_MODELS = {
    "acta_entrega_documentos",
    "anticipos",
    "cierre",
    "contrataciones_fuv",
    "contrataciones_fuv.cierre.proceso",
    "contrataciones_fuv.solicitud.poliza",
    "orden_compra_bienes",
    "orden_compra_servicios",
    "ordenes_especiales",
    "otro_si",
    "viaticos",
}

FLOW_NOTES = {
    "anticipos": """## Recorrido funcional

La solicitud nace en Coordinación. El flujo actual pasa a Ventanilla, luego a Tesorería y finalmente a Completado. Ventanilla puede devolverla a Coordinación con observaciones; después de corregir, Coordinación la retorna a Ventanilla. El valor `financiera` se conserva para anticipos históricos pendientes de migración y no es una etapa de los registros nuevos. La legalización posterior se vincula mediante `viatico_id`, pero pertenece al proceso de Legalizaciones.
""",
    "viaticos": """## Recorrido funcional

La legalización empieza en Coordinación y sigue por Auditoría Contable para retenciones, Presupuestos, Auditoría Contable para causación, Impuestos y decisión final de Auditoría. Cuando hay reintegro, la decisión envía el trámite a Tesorería antes de Completado. Si no hay reintegro pendiente, Auditoría puede cerrarlo directamente, con validaciones adicionales para un reembolso. Existen devoluciones a etapas anteriores y el valor `contabilidad` solo conserva trámites históricos.
""",
    "orden_compra_bienes": """## Recorrido funcional

La orden de bienes pasa de Coordinación a Financiera. Financiera puede devolverla a Coordinación; si la aprueba, el expediente regresa a Coordinación con la aprobación registrada. Coordinación puede devolverla a Financiera o finalizarla en Contratado. Las acciones de aprobación y devolución notifican a usuarios vinculados al proyecto.
""",
    "orden_compra_servicios": """## Recorrido funcional

La orden de servicios usa las mismas etapas principales que la de bienes: Coordinación, Financiera y Contratado. Sus acciones propias permiten enviarla a Financiera, devolverla a Coordinación, registrar la aprobación financiera y finalizar la orden. Verifique los requisitos y destinatarios en los métodos enlazados antes de diagramar cada retorno.
""",
    "contrataciones_fuv": """## Recorrido funcional

El contrato FUV tiene dos rutas según `flujo_contratacion_version`. En el flujo nuevo, una **persona jurídica** pasa por Coordinación, Ventanilla, Presupuesto, Dirección Jurídica, Jurídico 1, firma en Coordinación y Contratado. Una **persona natural o contrato laboral** pasa además por Recursos Humanos y Jurídico 2 antes de la firma. La etapa de pólizas es eventual y posterior a Contratado.

En el flujo anterior, la ruta habitual es Coordinación, Financiera, Jurídico y Contratado; persona natural puede pasar por Recursos Humanos y volver a Jurídico. Los selectores `estado_contratacion_nun` y `estado_contratacion_nun_juridica` eligen variantes de aprobación del recorrido anterior; no son el estado principal. Las devoluciones requieren conservar el área de origen y la de retorno.

Para diagramar las condiciones por tipo de persona, permisos y documentos, consulte [flujo nuevo](../../../Odoo_16/Matriz/docs/contrataciones_fuv/README_FLUJO_NUEVO.md) y [flujo anterior](../../../Odoo_16/Matriz/docs/contrataciones_fuv/README_FLUJO_ANTERIOR.md).
""",
    "contrataciones_sgr": """## Recorrido funcional

La aprobación SGR sigue el orden definido en `ESTADOS_CONTRATACION_SGR`: Coordinador, Jurídico, Coordinador, Jurídico, Jurídico SECOP, Financiero, Jurídico para acta de inicio, Coordinador para pólizas y firma, Jurídico para acta de aprobación, Coordinador para confirmación y Contratado. Los pasos repetidos tienen claves técnicas distintas.

`action_aprobar_sgr` avanza un puesto en la secuencia y comprueba el rol del área. `action_devolver_sgr` retrocede al paso anterior y exige observación. `action_retornar_ajustes_sgr` devuelve el expediente al área que solicitó el ajuste; `sgr_estado_retorno_devolucion` conserva ese destino mientras la corrección está pendiente. Consulte [flujo.py](../../../Odoo_16/Matriz/models/contrataciones_sgr/flujo.py).
""",
    "contrataciones_fuv.solicitud.poliza": """## Recorrido funcional

Coordinación o Jurídica guarda el checklist de garantías del contrato. Al llegar el contrato a Contratado se envía la solicitud inicial al contratista. Este carga cada póliza desde el portal; Jurídica aprueba cada línea o la devuelve con observación. Una devolución provoca un nuevo envío para corrección. Con todas las líneas aprobadas, se carga el acta definitiva y la solicitud termina en Finalizada. También hay ciclos posteriores por otrosí o liquidación y un circuito extraordinario de Soporte. Consulte la [guía completa](../../../Odoo_16/Matriz/models/solicitud_polizas/README.md) para condiciones y roles.
""",
    "contrataciones_fuv.cierre.proceso": """## Recorrido funcional

Se inicia desde un contrato FUV en Contratado, sin otro cierre activo. Hay tres tipos: acta de anulación, liquidación por mutuo acuerdo y liquidación unilateral. Anulación y mutuo acuerdo recorren Coordinación (solicitud) → Financiera (certificación) → Jurídica (revisión) → Coordinación (firma) → Presupuesto (liberación RFC) → Finalizado. La liquidación unilateral omite la firma de Coordinación: Jurídica formaliza el acta y pasa directamente a liberación RFC antes de Finalizado. Las devoluciones retroceden a un área concreta y conservan el destino de retorno. Es un flujo del **contrato FUV**, distinto del modelo `cierre` del proyecto. Consulte la [guía de cierre contractual](../../../Odoo_16/Matriz/models/cierre_contractual/README_USUARIO.md) para documentos y responsables de cada ruta.
""",
    "cierre": """## Recorrido funcional del proyecto

El modelo `cierre` registra el **cierre del proyecto** relacionado en `proyecto_relacionado3`. Su estado empieza en Coordinador y las aprobaciones lo llevan a Financiero, Jurídico y Finalizado (mostrado como «Cerrar proyecto»). Jurídico puede devolver a Financiero con motivo, o devolver directamente a Coordinador; en este último caso Coordinador reenvía a Jurídico tras corregir. La acción final `action_cerrar_proyecto` exige un acta de liquidación firmada y seleccionar al menos una opción de pendientes (pagos, cobros, otros o ninguno); si marca «otros», exige observaciones. Esa acción actualiza los indicadores del proyecto y notifica a Coordinación. El acta aquí es requisito del cierre del **proyecto**, no una cuarta modalidad del cierre contractual FUV. Consulte [cierre.py](../../../Odoo_16/Matriz/models/cierre.py#L27) y las [validaciones de cierre](../../../Odoo_16/Matriz/models/cierre.py#L151).
""",
    "ordenes_especiales": """## Recorrido funcional

La orden para persona jurídica pasa por Coordinación, Ventanilla, Contabilidad, Auxiliar de Tesorería, Tesorería y Finalizado. Cada área registra o revisa los soportes que le corresponden. Las acciones de devolución envían la orden a Coordinación o a la etapa anterior y guardan el estado al cual deben volver los ajustes. Consulte la [guía de órdenes](../../../Odoo_16/Matriz/models/ordenes_especiales/README_USUARIO.md) para los requisitos de cada paso.
""",
    "otro_si": """## Recorrido funcional

El otrosí modifica un contrato existente. El modelo conserva aprobaciones históricas y una ruta nueva; la versión del flujo determina qué acciones se aplican. En la ruta nueva intervienen Coordinación, Ventanilla, Presupuesto y Jurídica; según el tipo de contratista puede intervenir Recursos Humanos y una segunda revisión Jurídica. Las acciones de devolución guardan el área a la cual debe retornar el expediente después de corregirlo. Los estados de adición, prórroga, modificatorio o reinicio formalizado representan resultados distintos del trámite, no pasos consecutivos obligatorios. Verifique las condiciones en [otro_si_aprobaciones.py](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py).
""",
    "acta_entrega_documentos": """## Recorrido funcional de cuentas de cobro

El registro `cuentas_de_cobros` recoge la cuenta y sus soportes; al enviarse crea un `acta_entrega_documentos` vinculado. El **estado del recorrido entre áreas** está en el acta, no en la cuenta de origen. Pasa por Asistencia de Coordinación, Coordinación, Ventanilla, Dirección Financiera, Contabilidad, Auxiliar de Tesorería, Tesorería y Finalizado. También puede pasar a Rechazado; `estado_anterior_rechazo` conserva el punto desde el que se rechazó. La cesión de derechos y la generación del PDF unificado tienen estados propios dentro del acta y deben dibujarse como subprocesos separados. Las acciones `enviar_a_*` y `devolver_*` determinan los saltos efectivos. Consulte [la creación del acta desde la cuenta](../../../Odoo_16/Matriz/models/cuenta_de_cobro.py#L1909).
""",
}

NEW_CONTRACT_FLOW_README = """# Contrataciones FUV — flujo nuevo

**Modelo que conserva el estado:** `contrataciones_fuv`, campo `estado_contratacion`. Esta carpeta describe exclusivamente la ruta nueva del mismo modelo; el [README general](../contrataciones_fuv/README.md) documenta también la ruta anterior.

## Cuándo aplica

El corte predeterminado es el **24 de julio de 2026** para contratos creados desde ese día. El parámetro `Matriz.fecha_inicio_nuevo_flujo_contratacion` puede cambiarlo. Para confirmar la ruta de un registro, revise `flujo_contratacion_version = nuevo` o `usa_flujo_contratacion_nuevo = True`. Fuente: [configuración](../../../Odoo_16/Matriz/models/flujo_configuracion.py#L8) y [cálculo de versión](../../../Odoo_16/Matriz/models/contrataciones_fuv_aprobaciones.py#L470).

## Estados de la ruta normal

**Persona jurídica:** Coordinación → Ventanilla → Presupuesto → Dirección Jurídica → Jurídico 1 → Coordinación (carga del contrato firmado) → Contratado.

**Persona natural o contrato laboral:** Coordinación → Ventanilla → Presupuesto → Dirección Jurídica → Jurídico 1 → Recursos Humanos → Jurídico 2 → Coordinación (carga del contrato firmado) → Contratado.

Estas son dos ramas del mismo flujo. La Dirección Jurídica asigna el jurídico responsable. Jurídico 1 envía a firma directamente en la rama de persona jurídica; en la otra, Recursos Humanos revisa y Jurídico 2 prepara el contrato antes de la firma. La carga del contrato firmado no cambia sola el estado: Coordinación ejecuta **Contratar**.

## Condiciones y devoluciones

- Ventanilla valida los requisitos financieros aplicables antes de remitir a Presupuesto.
- Presupuesto requiere el compromiso presupuestal y las aprobaciones financieras antes de remitir a Dirección Jurídica.
- Jurídico y Recursos Humanos comprueban los documentos requeridos. El paso a Contratado exige el contrato firmado.
- Ventanilla puede devolver a Coordinación; Presupuesto, a Ventanilla o Coordinación; Dirección Jurídica y Jurídico 1, a Presupuesto o Coordinación; Recursos Humanos, a Jurídico 1 o Coordinación; Jurídico 2, a Coordinación.
- El sistema guarda el origen y el destino de retorno de una devolución. Tras corregir, Coordinación puede enviar el expediente de vuelta al área que lo devolvió.

**Después de Contratado:** Pólizas es un estado eventual, no una aprobación obligatoria de estas ramas. La [solicitud de pólizas](../contrataciones_fuv.solicitud.poliza/README.md), el [cierre contractual](../contrataciones_fuv.cierre.proceso/README.md) y el [cierre del proyecto](../cierre/README.md) están documentados por separado.

## Fuentes para revisar cada flecha

- [Guía funcional del flujo nuevo](../../../Odoo_16/Matriz/docs/contrataciones_fuv/README_FLUJO_NUEVO.md)
- [Transiciones, validaciones y devoluciones](../../../Odoo_16/Matriz/models/contrataciones_fuv_aprobaciones.py#L615)
- [Botones y visibilidad del formulario](../../../Odoo_16/Matriz/views/contrataciones_fuv.xml)

El diagrama editable está en [Flujo_estados.pptx](../../Flujos/contrataciones_fuv_flujo_nuevo/Flujo_estados.pptx).
"""

NEW_OTHERSI_FLOW_README = """# Otrosí — flujo nuevo

**Modelo y estado:** `otro_si.estado_contratacion`. Este README describe solo el flujo nuevo. Consulte el [flujo anterior](../otro_si_flujo_anterior/README.md) o la [ficha general del modelo](../otro_si/README.md) por separado.

## Cómo se elige la versión

Si el otrosí está vinculado a un contrato FUV, hereda `usa_flujo_contratacion_nuevo` del contrato. Sin ese dato, compara su fecha de creación con el corte configurado para contrataciones. Confirme en el registro `flujo_otrosi_version = nuevo` o `usa_flujo_otrosi_nuevo = True`. Fuente: [cálculo de versión](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L262).

## Primer desvío: ¿pasa por Ventanilla y Presupuesto?

- **Sí:** adición, adición con prórroga y modificatorio que cambia valor o valor y plazo. Coordinación → Ventanilla → Presupuesto → Dirección Jurídica.
- **No:** los demás tipos van de Coordinación directamente a Dirección Jurídica. Aquí se encuentran, entre otros, prórroga sola, suspensión, reinicio y prórroga de suspensión.

La [regla de selección](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L402) decide esta rama; Ventanilla y Presupuesto no son pasos obligatorios de todo otrosí nuevo.

## Segundo desvío: ¿pasa por Recursos Humanos?

Desde Dirección Jurídica, un jurídico asignado recibe el expediente en Jurídico 1. Persona jurídica, suspensión, reinicio y prórroga de suspensión pasan de Jurídico 1 directamente a Firma. Los demás casos pasan por Recursos Humanos → Jurídico 2 → Firma. Fuente: [regla de Recursos Humanos](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L410) y [aprobaciones jurídicas](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L970).

En **Firma**, Coordinación carga el documento firmado y finaliza. El resultado depende del tipo: contratado, suspendido, reinicio formalizado, suspensión prorrogada, adición formalizada, prórroga formalizada, adición con prórroga formalizada o modificatorio formalizado. Son **resultados alternativos**, no etapas consecutivas. Fuente: [resultado por tipo](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L424) y [acción final](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L1196).

## Devoluciones

Ventanilla, Presupuesto, Dirección Jurídica, Jurídico 1, Recursos Humanos y Jurídico 2 pueden devolver a Coordinación. Presupuesto también puede devolver a Ventanilla; Dirección Jurídica puede devolver a Presupuesto. El sistema guarda el área que pidió la corrección, para que Coordinación pueda reenviar allí el expediente. Fuentes: [devoluciones](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L1017) y [retorno de ajustes](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L1150).

El [PowerPoint editable](../../Flujos/otro_si_flujo_nuevo/Flujo_estados.pptx) dibuja ambos desvíos y la finalización.
"""

OLD_OTHERSI_FLOW_README = """# Otrosí — flujo anterior

**Modelo y estado:** `otro_si.estado_contratacion`. Este README describe el recorrido histórico. Consulte el [flujo nuevo](../otro_si_flujo_nuevo/README.md) o la [ficha general del modelo](../otro_si/README.md) por separado.

## Cómo se reconoce

El campo `flujo_otrosi_version = anterior` o `usa_flujo_otrosi_nuevo = False` confirma la versión. Si está vinculado a un contrato FUV, toma la versión del contrato; de lo contrario, usa el corte por fecha configurado. Fuente: [cálculo de versión](../../../Odoo_16/Matriz/models/otro_si_aprobaciones.py#L262).

## Recorrido habitual

Coordinación → Financiera → Jurídico → Coordinación para firma/finalización → Contratado. Para algunos casos de persona natural o contrato laboral, Jurídico envía a Recursos Humanos y el expediente vuelve a Jurídico antes de regresar a Coordinación. Fuente: [aprobación jurídica](../../../Odoo_16/Matriz/models/otro_si.py#L1697), [aprobación de Recursos Humanos](../../../Odoo_16/Matriz/models/otro_si.py#L2583) y [marcar Contratado](../../../Odoo_16/Matriz/models/otro_si.py#L3427).

## Variantes históricas

No hay una única secuencia obligatoria para todos los registros. En persona natural o contrato laboral, `estado_contratacion_nun` selecciona desde Coordinación: `1` hacia Financiera, `2` hacia Jurídico o `3` hacia Recursos Humanos. En persona jurídica, `estado_contratacion_nun_juridica` selecciona `1` hacia Financiera o `2` hacia Jurídico. Estos campos son **selectores del botón/ruta histórica**, no el estado principal. Las acciones de «No aprobado» de Financiera, Jurídico y Recursos Humanos pueden devolver a Coordinación. Fuentes: [selectores](../../../Odoo_16/Matriz/models/otro_si.py#L1969), [rutas de Coordinación](../../../Odoo_16/Matriz/models/otro_si.py#L1995) y [devoluciones](../../../Odoo_16/Matriz/models/otro_si.py#L2990).

Esta versión utiliza `financiera` y `juridico` en el recorrido ordinario; los estados `ventanilla`, `presupuesto`, `director_juridico`, `juridico_1`, `juridico_2` y `firma` corresponden al flujo nuevo.

El [PowerPoint editable](../../Flujos/otro_si_flujo_anterior/Flujo_estados.pptx) distingue el recorrido habitual de las variantes de entrada y devolución.
"""


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return None


def resolve_expr(node, symbols):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [resolve_expr(x, symbols) for x in node.elts]
        return values if all(x is not None for x in values) else None
    if isinstance(node, ast.Name):
        return symbols.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr == "value" and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
        return symbols.get(f"{node.value.value.id}.{node.value.attr}")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = resolve_expr(node.left, symbols), resolve_expr(node.right, symbols)
        if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            return list(left) + list(right)
    return None


def source_symbols(paths):
    symbols = {}
    assignments_to_resolve = []
    for path in paths:
        tree = ast.parse(source(path), filename=str(path))
        for child in tree.body:
            if isinstance(child, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == "Enum" for base in child.bases):
                for key, value, _ in assignments(child):
                    result = literal(value)
                    if isinstance(result, str):
                        symbols[f"{child.name}.{key}"] = result
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments_to_resolve.append((target.id, child.value))
    for _ in range(5):
        for key, expression in assignments_to_resolve:
            value = resolve_expr(expression, symbols)
            if value is not None:
                symbols[key] = value
    return symbols


def assignments(node: ast.ClassDef):
    for child in node.body:
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    yield target.id, child.value, child.lineno


def imported_files() -> set[Path]:
    active = set()
    pending = [MODELS / "__init__.py"]
    while pending:
        path = pending.pop()
        if path in active or not path.is_file():
            continue
        active.add(path)
        tree = ast.parse(source(path), filename=str(path))
        for child in tree.body:
            if not isinstance(child, ast.ImportFrom) or not child.level:
                continue
            base = path.parent
            for _ in range(child.level - 1):
                base = base.parent
            module_path = base / child.module.replace(".", "/") if child.module else base
            candidates = []
            if child.module:
                candidates += [module_path.with_suffix(".py"), module_path / "__init__.py"]
            for name in child.names:
                candidates += [module_path / f"{name.name}.py", module_path / name.name / "__init__.py"]
            for candidate in candidates:
                if candidate.is_file() and candidate.is_relative_to(MODELS):
                    pending.append(candidate)
    return active


def display(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def code_ref(path: Path, line: int, depth: int = 4) -> str:
    relative = path.relative_to(ROOT).as_posix()
    return f"[{relative}]({'../' * (depth - 1)}Odoo_16/Matriz/{relative}#L{line})"


def pretty(identifier: str) -> str:
    return identifier.replace("_", " ").replace(".", " · ").strip().capitalize()


def field_info(node, symbols):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "fields":
        return None
    kind = node.func.attr
    kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    label = literal(kwargs.get("string")) if "string" in kwargs else None
    relation = literal(node.args[0]) if node.args and kind in {"Many2one", "One2many", "Many2many"} else None
    default = literal(kwargs.get("default")) if "default" in kwargs else None
    related = literal(kwargs.get("related")) if "related" in kwargs else None
    selection = []
    selection_expr = ""
    selection_add = []
    if kind == "Selection":
        selection_node = node.args[0] if node.args else kwargs.get("selection")
        value = resolve_expr(selection_node, symbols) if selection_node else None
        if isinstance(value, (tuple, list)):
            selection = [(str(pair[0]), str(pair[1])) for pair in value if isinstance(pair, (tuple, list)) and len(pair) == 2]
        elif selection_node is not None:
            selection_expr = ast.unparse(selection_node)
        extra = resolve_expr(kwargs["selection_add"], symbols) if "selection_add" in kwargs else None
        if isinstance(extra, (tuple, list)):
            selection_add = [(str(pair[0]), str(pair[1])) for pair in extra if isinstance(pair, (tuple, list)) and len(pair) == 2]
    return {
        "kind": kind, "label": display(label), "relation": relation,
        "default": default, "related": related,
        "selection": selection, "selection_expr": selection_expr, "selection_add": selection_add,
    }


def transitions(method, state_fields: set[str]):
    """Recoge asignaciones explícitas sin deducir recorridos dinámicos."""
    found = set()
    for item in ast.walk(method):
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == "write":
            if item.args and isinstance(item.args[0], ast.Dict):
                for key, value in zip(item.args[0].keys, item.args[0].values):
                    k = literal(key)
                    if k in state_fields:
                        found.add((k, ast.unparse(value), item.lineno))
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in state_fields:
                    found.add((target.attr, ast.unparse(item.value), item.lineno))
    return sorted(found, key=lambda x: x[2])


def main():
    active = imported_files()
    # Incluye constantes importadas por clases activas, sin mezclar enums de
    # archivos de ejemplo que no se cargan (por ejemplo, estate_news.py).
    constants = {
        MODELS / "contrataciones_sgr" / "estados.py",
        MODELS / "cierre_contractual" / "constantes.py",
        MODELS / "solicitud_polizas" / "constantes.py",
    }
    symbols = source_symbols(sorted(active | constants))
    definitions = defaultdict(list)
    extensions = defaultdict(list)
    for path in sorted(active):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(source(path), filename=str(path))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            attrs = {key: literal(val) for key, val, _ in assignments(cls) if key.startswith("_")}
            name = attrs.get("_name")
            inherit = attrs.get("_inherit")
            entry = (path, cls, attrs)
            if isinstance(name, str):
                definitions[name].append(entry)
            elif isinstance(inherit, str):
                extensions[inherit].append(entry)

    missing_flow_models = FLOW_MODELS - set(definitions)
    if missing_flow_models:
        raise RuntimeError(f"Modelos de flujo no encontrados: {sorted(missing_flow_models)}")
    definitions = {name: items for name, items in definitions.items() if name in FLOW_MODELS}
    for name, defs in sorted(definitions.items()):
        chunks = defs + extensions.get(name, [])
        all_fields = []
        all_methods = []
        for path, cls, attrs in chunks:
            for field_name, value, line in assignments(cls):
                info = field_info(value, symbols)
                if info:
                    all_fields.append((field_name, info, path, line))
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    all_methods.append((method, path))
        fields_by_name = {}
        for field_name, info, path, line in all_fields:
            fields_by_name[field_name] = (info, path, line)
        state_fields = {
            key for key, info, _, _ in all_fields
            if info["kind"] == "Selection" and re.search(r"(^|_)(estado|state|status|etapa|fase)(_|$)", key, re.I)
        }
        state_fields |= {key for key in fields_by_name if key in {"state", "estado"}}
        title = next((display(x[2].get("_description")) for x in defs if x[2].get("_description")), "") or pretty(name)
        slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
        out = DEST / "Documentacion_MD" / slug / "README.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        primary_path, primary_cls, primary_attrs = defs[0]
        base_kind = ast.unparse(primary_cls.bases[0]) if primary_cls.bases else "models.Model"
        role = "asistente temporal" if "TransientModel" in base_kind else "modelo persistente"
        if "AbstractModel" in base_kind:
            role = "modelo abstracto de apoyo"
        lines = [f"# {title} (`{name}`)", "", f"**Para qué sirve:** Mantiene la información de {title.lower()} en Matriz. Tipo: {role}.", ""]
        if name == "contrataciones_fuv":
            lines += ["**Flujo nuevo:** abra la [carpeta exclusiva de Contrataciones FUV — flujo nuevo](../contrataciones_fuv_flujo_nuevo/README.md), con su diagrama para persona jurídica y para persona natural o contrato laboral.", ""]
        if name == "otro_si":
            lines += ["**Versiones separadas:** consulte [Otrosí — flujo nuevo](../otro_si_flujo_nuevo/README.md) y [Otrosí — flujo anterior](../otro_si_flujo_anterior/README.md), cada uno con su propio diagrama.", ""]
        doc = ast.get_docstring(primary_cls)
        if doc:
            lines += ["## Descripción del código", "", display(doc), ""]
        lines += ["## Dónde se implementa", ""]
        for path, cls, _ in chunks:
            label = "Definición" if (path, cls, _) in defs else "Extensión"
            lines.append(f"- {label}: {code_ref(path, cls.lineno)} (`{cls.name}`)")
        lines += ["", "## Estados y controles", ""]
        if state_fields:
            for key in sorted(state_fields):
                declarations = [(info, path, line) for field_name, info, path, line in all_fields if field_name == key]
                if "retorno" in key or "ultimo_estado" in key or "anterior_rechazo" in key:
                    lines.append(f"- **Control auxiliar:** `{key}` guarda el origen o destino de una devolución/rechazo.")
                elif "_nun" in key:
                    lines.append(f"- **Control auxiliar:** `{key}` selecciona una variante del flujo anterior.")
                elif "sincronizacion" in key or key.startswith("pdf_unificado_estado"):
                    lines.append(f"- **Subproceso técnico:** `{key}` indica el progreso de una operación externa.")
                elif key.endswith("_2"):
                    lines.append(f"- **Control auxiliar:** `{key}` no sustituye el estado principal del expediente.")
                for info, path, line in declarations:
                    if info["related"]:
                        lines += [f"- `{key}` refleja `{info['related']}`; el estado pertenece al registro relacionado. Fuente: {code_ref(path, line)}"]
                    elif info["selection"]:
                        options = ", ".join(f"**{display(label)}** (`{display(code)}`)" for code, label in info["selection"])
                        lines += [f"- `{key}` permite: {options}. Fuente: {code_ref(path, line)}"]
                    elif info["selection_expr"]:
                        lines += [f"- `{key}` usa `{info['selection_expr']}`; revisar la definición enlazada para sus opciones. Fuente: {code_ref(path, line)}"]
                    elif info["selection_add"]:
                        options = ", ".join(f"**{display(label)}** (`{display(code)}`)" for code, label in info["selection_add"])
                        lines += [f"- La extensión de `{key}` añade: {options}. Fuente: {code_ref(path, line)}"]
                    else:
                        lines += [f"- `{key}` figura en el modelo. Fuente: {code_ref(path, line)}"]
                    if info["default"] is not None:
                        lines.append(f"  - Valor inicial declarado: `{display(info['default'])}`.")
        else:
            lines += ["Este modelo no declara un campo de estado propio. Su recorrido consiste en crear, consultar o actualizar datos, o en ejecutar una acción sobre otro modelo; no hay etapas que deban dibujarse como estados propios."]
        if name in FLOW_NOTES:
            lines += ["", FLOW_NOTES[name].strip(), ""]
        observations = []
        for method, path in all_methods:
            for key, value, line in transitions(method, state_fields):
                observations.append((method.name, key, value, path, line))
        if observations:
            lines += ["", "### Cambios de estado escritos explícitamente", "", "Estos son destinos observados en el código; las condiciones y rutas dinámicas deben leerse en el método enlazado.", "", "| Acción o método | Campo | Valor escrito | Código |", "|---|---|---|---|"]
            for method, key, value, path, line in observations[:60]:
                lines.append(f"| `{method}` | `{key}` | `{display(value)}` | {code_ref(path, line)} |")
            if len(observations) > 60:
                lines.append(f"| … | … | {len(observations) - 60} escrituras adicionales; consultar el código fuente | … |")
        actions = []
        for method, path in all_methods:
            name_m = method.name
            if name_m.startswith(("action_", "button_")) or re.match(r"^(enviar|aprobar|rechazar|devolver|confirmar|cancelar|finalizar|generar|solicitar|cargar|registrar|procesar)_", name_m):
                actions.append((name_m, path, method.lineno, ast.get_docstring(method)))
        if actions:
            lines += ["", "## Acciones disponibles en el código", ""]
            grouped = defaultdict(list)
            for action in actions:
                group = "Carga de documentos" if any(x in action[0] for x in ("upload", "subir", "cargar_documento")) else "Otras acciones"
                grouped[group].append(action)
            for group, items in grouped.items():
                lines.append(f"### {group}")
                lines.append("")
                if group == "Carga de documentos" and len(items) > 15:
                    lines.append(f"Hay {len(items)} acciones de carga. Se listan para distinguirlas de las aprobaciones o cambios de estado:")
                    lines.append("")
                for action, path, line, mdoc in items:
                    desc = f" — {display(mdoc.splitlines()[0])}" if mdoc else ""
                    lines.append(f"- `{action}`{desc} ({code_ref(path, line)})")
                lines.append("")
        relations = [(key, info, path, line) for key, (info, path, line) in fields_by_name.items() if info["relation"]]
        if relations:
            lines += ["## Datos relacionados", "", "| Campo | Relación | Modelo vinculado |", "|---|---|---|"]
            for key, info, path, line in relations[:35]:
                lines.append(f"| `{key}` | {info['kind']} | `{display(info['relation'])}` |")
            if len(relations) > 35:
                lines.append(f"| … | … | {len(relations) - 35} relaciones adicionales en el código fuente |")
            lines.append("")
        other_fields = [(key, info) for key, (info, _, _) in fields_by_name.items() if key not in state_fields and not info["relation"] and info["kind"] not in {"Binary", "Html"}]
        if other_fields:
            lines += ["## Datos principales", "", "| Campo | Uso indicado por el modelo |", "|---|---|"]
            for key, info in other_fields[:18]:
                lines.append(f"| `{key}` | {display(info['label']) or pretty(key)} |")
            if len(other_fields) > 18:
                lines.append(f"| … | {len(other_fields) - 18} campos adicionales en el código fuente |")
            lines.append("")
        lines += ["## Para el diagrama", "", "Represente este modelo como entidad o subproceso. Dibuje los estados solo si aparecen arriba; las relaciones enlazan con otros modelos y las acciones son posibles puntos de transición. Verifique permisos, condiciones y efectos en el método fuente antes de fijar una flecha.", ""]
        out.write_text("\n".join(lines), encoding="utf-8")

    new_flow_dir = DEST / "Documentacion_MD" / "contrataciones_fuv_flujo_nuevo"
    new_flow_dir.mkdir(parents=True, exist_ok=True)
    (new_flow_dir / "README.md").write_text(NEW_CONTRACT_FLOW_README, encoding="utf-8")
    for folder_name, body in (
        ("otro_si_flujo_nuevo", NEW_OTHERSI_FLOW_README),
        ("otro_si_flujo_anterior", OLD_OTHERSI_FLOW_README),
    ):
        flow_dir = DEST / "Documentacion_MD" / folder_name
        flow_dir.mkdir(parents=True, exist_ok=True)
        (flow_dir / "README.md").write_text(body, encoding="utf-8")

    index = [
        "# Flujos solicitados de Matriz", "",
        "Los PowerPoint para subir a Drive están en la carpeta vecina `Flujos`; esta carpeta conserva solo la documentación Markdown. Contrataciones FUV y otrosí tienen recorridos separados por versión. El cierre contractual FUV y el cierre del proyecto son procesos distintos.", "",
        "1. [Anticipos](modelos/anticipos/README.md) — `anticipos`.",
        "2. [Legalizaciones](modelos/viaticos/README.md) — `viaticos`.",
        "3. **[Contrataciones FUV — flujo nuevo](modelos/contrataciones_fuv_flujo_nuevo/README.md)** — [diagrama PPTX](modelos/contrataciones_fuv_flujo_nuevo/Flujo_estados.pptx); rutas para persona jurídica y para persona natural o contrato laboral.",
        "4. [Contrataciones FUV — modelo general](modelos/contrataciones_fuv/README.md) — `contrataciones_fuv`; incluye la comparación con el flujo anterior.",
        "5. **[Otrosí — flujo nuevo](modelos/otro_si_flujo_nuevo/README.md)** — [diagrama PPTX](modelos/otro_si_flujo_nuevo/Flujo_estados.pptx); dos desvíos según tipo y revisión de Recursos Humanos.",
        "6. **[Otrosí — flujo anterior](modelos/otro_si_flujo_anterior/README.md)** — [diagrama PPTX](modelos/otro_si_flujo_anterior/Flujo_estados.pptx); ruta histórica y variantes de Coordinación.",
        "7. [Otrosí — modelo general](modelos/otro_si/README.md) — `otro_si`; referencia técnica de los estados compartidos.",
        "8. [Órdenes de bienes](modelos/orden_compra_bienes/README.md) — `orden_compra_bienes`.",
        "9. [Órdenes de servicios](modelos/orden_compra_servicios/README.md) — `orden_compra_servicios`.",
        "10. [Órdenes especiales](modelos/ordenes_especiales/README.md) — `ordenes_especiales`.",
        "11. [Cuentas de cobro](modelos/acta_entrega_documentos/README.md) — la cuenta se crea en `cuentas_de_cobros`; el recorrido de estados entre áreas está en `acta_entrega_documentos`.",
        "12. [Solicitud de pólizas](modelos/contrataciones_fuv.solicitud.poliza/README.md) — checklist, envío, revisión jurídica, correcciones y acta definitiva.",
        "13. [Cierre contractual FUV](modelos/contrataciones_fuv.cierre.proceso/README.md) — anulación, liquidación por mutuo acuerdo o liquidación unilateral del contrato.",
        "14. [Cierre del proyecto](modelos/cierre/README.md) — aprobaciones de Coordinación, Financiera y Jurídica antes de cerrar el proyecto.",
        "", "## Lectura para los diagramas", "",
        "Las opciones de un campo de estado muestran valores posibles, no todos los saltos permitidos. Las tablas de cambios recogen asignaciones visibles en el código; los métodos enlazados contienen las condiciones, permisos y devoluciones que deben acompañar cada flecha.", "",
    ]
    index = [
        re.sub(
            r"modelos/([^/]+)/README\.md",
            r"\1/README.md",
            re.sub(
                r"modelos/([^/]+)/Flujo_estados\.pptx",
                r"../Flujos/\1/Flujo_estados.pptx",
                line,
            ),
        )
        for line in index
    ]
    documentation_dir = DEST / "Documentacion_MD"
    documentation_dir.mkdir(parents=True, exist_ok=True)
    (documentation_dir / "README.md").write_text("\n".join(index), encoding="utf-8")
    print(f"Generados {len(definitions)} README de modelo, 3 README de rutas y 1 índice en {documentation_dir}")


if __name__ == "__main__":
    main()
