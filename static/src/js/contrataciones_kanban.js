/** @odoo-module */
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { useService } from "@web/core/utils/hooks";
import { ContratacionesFuvRenderer } from "./kanban_sidebar_fuv";

export class ContratacionesKanbanController extends KanbanController {
    setup() {
        super.setup();
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.notification = useService("notification"); 
    }

    // Acción para abrir "Orden de Bienes"
    onOrdenBienesClick() {
        if (!this.actionService) {
            console.error("Error: actionService no está definido.");
            return;
        }

        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: "Orden de compra de bienes",
            res_model: "orden_compra_bienes",
            view_mode: "kanban,tree,form",
            views: [
                [false, "kanban"],
                [false, "tree"],
                [false, "form"]
            ],
            target: "current",
            context: { default_proyecto_relacionado_c: this.props.context.active_id || false },
            domain: [["proyecto_relacionado_c", "=", this.props.context.active_id || false]],
        }).catch((error) => {
            console.error("Error al ejecutar la acción de Orden de Bienes:", error);
        });
    }

    // Acción para abrir "Orden de Servicios"
    onOrdenServiciosClick() {
        if (!this.actionService) {
            console.error("Error: actionService no está definido.");
            return;
        }

        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: "Orden de compra de servicios",
            res_model: "orden_compra_servicios",
            view_mode: "kanban,tree,form",
            views: [
                [false, "kanban"],
                [false, "tree"],
                [false, "form"]
            ],
            target: "current",
            context: { default_proyecto_relacionado_s: this.props.context.active_id || false },
            domain: [["proyecto_relacionado_s", "=", this.props.context.active_id || false]],
        }).catch((error) => {
            console.error("Error al ejecutar la acción de Orden de Servicios:", error);
        });
    }

    onOrdenesEspecialesClick() {
        if (!this.actionService) {
            console.error("Error: actionService no está definido.");
            return;
        }

        const proyectoId = this.props.context.active_id || false;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: "Órdenes especiales",
            res_model: "ordenes_especiales",
            view_mode: "kanban,tree,form",
            views: [
                [false, "kanban"],
                [false, "tree"],
                [false, "form"],
            ],
            target: "current",
            context: proyectoId
                ? {
                    default_proyecto_relacionado_s: proyectoId,
                    ocultar_polizas_ordenes_especiales: true,
                }
                : { ocultar_polizas_ordenes_especiales: true },
            domain: proyectoId
                ? [["proyecto_relacionado_s", "=", proyectoId]]
                : [],
        }).catch((error) => {
            console.error("Error al abrir Órdenes especiales:", error);
        });
    }

    // Acción para abrir "Viáticos" (SIN CLAVE)
    onViaticosClick() {
        if (!this.actionService) {
            console.error("Error: actionService no está definido.");
            return;
        }

        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: "Legalizaciones",
            res_model: "viaticos",
            view_mode: "tree,form",
            views: [
                [false, "kanban"],
                [false, "tree"],
                [false, "form"]
            ],
            target: "current",
            context: { default_proyecto_relacionado_viaticos: this.props.context.active_id || false },
            domain: [["proyecto_relacionado_viaticos", "=", this.props.context.active_id || false]],
        }).catch((error) => {
            console.error("Error al ejecutar la acción de Viáticos:", error);
        });
    }

    // Acción para abrir "Anticipos" (SIN CLAVE)
    onAnticiposClick() {
        if (!this.actionService) {
            console.error("Error: actionService no está definido.");
            return;
        }

        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: "Anticipos",
            res_model: "anticipos",
            view_mode: "kanban",
            views: [
                [false, "kanban"],
                [false, "tree"],        
                [false, "form"]
            ],
            target: "current",
            context: { default_proyecto_id: this.props.context.active_id || false },
            domain: [["proyecto_id", "=", this.props.context.active_id || false]],
        }).catch((error) => {
            console.error("Error al ejecutar la acción de Anticipos:", error);
        });
    }

    onNotificacionesClick() {
        if (!this.actionService) {
            console.error("actionService no está definido.");
            return;
        }

        const activeId = this.props.context.active_id || false;

        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: "Solicitud de actualización de documentos",
            res_model: "contratistas.notificacion.wizard",
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
            context: {
                default_proyecto_relacionado: activeId,
            },
        }).catch((error) => {
            console.error("Error al abrir el wizard de notificaciones:", error);
        });
    }

    // ACCESO HISTÓRICO DEL KANBAN:
    // Esta acción llama a contratacion.Cierre() y abre/crea el modelo antiguo
    // "cierre" del proyecto ("4. Cierre"). El botón que la invocaba quedó
    // oculto en contrataciones_buttons_kanban.xml para evitar confundir este
    // cierre general del proyecto con el flujo contractual vigente que se
    // inicia desde cada contrato mediante "Solicitar cierre / finalizar".
    // Se conserva el método por trazabilidad y posible reactivación futura.
    onCierreContratoClick() {
        if (!this.actionService || !this.orm) {
            console.error("Error: servicios de acción u ORM no disponibles.");
            return;
        }

        const activeId = this.props.context.active_id || false;
        if (!activeId) {
            this.notification.add("Debes abrir las contrataciones desde un proyecto para acceder al cierre.", {
                type: "warning",
            });
            return;
        }

        this.orm.call("contratacion", "Cierre", [[activeId]]).then((action) => {
            this.actionService.doAction(action);
        }).catch((error) => {
            console.error("Error al abrir el cierre del proyecto:", error);
            this.notification.add("No se pudo abrir el cierre del proyecto.", {
                type: "danger",
            });
        });
    }
}

// Registro de la vista
registry.category("views").add("contrataciones_kanban", {
    ...kanbanView,
    Controller: ContratacionesKanbanController,
    Renderer: ContratacionesFuvRenderer,
    buttonTemplate: "contrataciones.KanbanView.Buttons",
});
