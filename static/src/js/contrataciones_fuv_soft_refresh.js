/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

// Refresco suave para flujos de Matriz.
//
// Problema que resuelve:
// - Los botones de aprobacion/devolucion cambian el estado en Python.
// - El chatter y el correo salen bien, pero el formulario abierto conserva en
//   memoria el estado anterior hasta que el usuario recarga la pagina.
//
// Por que existe este JS:
// - No usamos acciones tipo "tag: reload" porque recargan toda la pagina,
//   pueden alterar la miga de pan y se sienten como un F5.
// - Este parche refresca solo el registro abierto en OWL para que el badge de
//   estado, los botones y los attrs invisibles/visibles cambien al instante.
//
// Modelos cubiertos:
// - contrataciones_fuv: flujo viejo y flujo nuevo.
// - otro_si: flujo viejo y flujo nuevo.
// - contrataciones_fuv.cierre.proceso: flujos de cierre contractual.
// - contrataciones_fuv.solicitud.poliza: envío y recepción de pólizas.
// - anticipos: solo botones de envío, devolución, migración y finalización.
patch(FormController.prototype, "Matriz.ContratacionesFuvSoftRefresh", {
    async afterExecuteActionButton(clickParams) {
        // Hook nativo de Odoo 16: se ejecuta despues de cualquier boton de
        // accion en el formulario. Primero dejamos que Odoo termine la accion.
        const result = await this._super(...arguments);

        // Luego refrescamos visualmente el registro si pertenece a los flujos
        // que necesitan actualizar el badge sin recargar la vista completa.
        await this._refreshMatrizWorkflowRecord(clickParams);
        return result;
    },

    async _refreshMatrizWorkflowRecord(clickParams) {
        const modelName =
            this.props?.resModel ||
            this.model?.config?.resModel ||
            this.model?.root?.resModel;

        // Limita el comportamiento solo a los modelos del flujo. Asi evitamos
        // tocar formularios de otros modulos que tambien usen botones object.
        if (![
            "contrataciones_fuv",
            "otro_si",
            "contrataciones_fuv.cierre.proceso",
            "contrataciones_fuv.solicitud.poliza",
            "anticipos",
        ].includes(modelName)) {
            return;
        }

        // Solo se refresca despues de botones tipo object, que son los que
        // ejecutan metodos Python como aprobar, devolver, contratar, etc.
        const buttonType = clickParams?.type || clickParams?.attrs?.type;
        if (buttonType && buttonType !== "object") {
            return;
        }

        // Los botones de documentos de Anticipos abren ventanas propias y no
        // deben provocar una relectura del formulario mientras se usan.
        const buttonName = clickParams?.name || clickParams?.attrs?.name;
        if (modelName === "anticipos" && ![
            "aprueba_coordinador_anticipos",
            "migrar_anticipo_legacy_a_ventanilla",
            "aprueba_ventanilla_anticipos",
            "aprueba_tesoreria_anticipos",
            "devolver_a_coordinador_desde_ventanilla",
            "retornar_a_ventanilla",
            "devolver_ventanilla_a_coordinador",
            "rechaza_financiero_anticipos",
            "rechaza_tesoreria_anticipos",
        ].includes(buttonName)) {
            return;
        }

        // Si el registro es nuevo o no tiene ID todavia, no hay nada que releer
        // desde backend.
        const root = this.model?.root;
        if (!root || root.isNew || !root.resId) {
            return;
        }

        try {
            // root.load() vuelve a pedir a Odoo los valores actuales del mismo
            // registro abierto. No cambia de vista, no cambia URL y no crea
            // migas de pan.
            await root.load();

            // model.notify() le avisa a OWL que esos datos cambiaron, forzando
            // el re-render de widgets, badges y condiciones de visibilidad.
            this.model.notify();
        } catch (error) {
            // Si algo falla, no bloqueamos el flujo; solo dejamos evidencia en
            // consola para depuracion.
            console.warn("[MatrizWorkflowSoftRefresh] No se pudo actualizar el formulario.", error);
        }
    },
});
