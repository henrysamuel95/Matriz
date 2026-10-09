/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

const FLOW_BUTTONS = new Set([
    "action_enviar_ventanilla",
    "action_enviar_contabilidad",
    "action_enviar_auxiliar_tesoreria",
    "action_enviar_tesoreria",
    "action_finalizar_cruce",
    "action_finalizar_pago",
    "action_devolver_ventanilla",
    "action_devolver_contabilidad",
    "action_devolver_auxiliar_tesoreria",
    "action_devolver_tesoreria",
    "action_coordinador_retornar_ajustes",
]);

// Refresca solo el registro abierto después de aprobar o devolver una orden.
// root.load() conserva la acción actual, la URL y la miga de pan.
patch(FormController.prototype, "Matriz.OrdenesEspecialesSoftRefresh", {
    async afterExecuteActionButton(clickParams) {
        const result = await this._super(...arguments);
        await this._refreshOrdenEspecialRecord(clickParams);
        return result;
    },

    async _refreshOrdenEspecialRecord(clickParams) {
        const modelName =
            this.props?.resModel ||
            this.model?.config?.resModel ||
            this.model?.root?.resModel;
        if (modelName !== "ordenes_especiales") {
            return;
        }

        const buttonType = clickParams?.type || clickParams?.attrs?.type;
        const buttonName = clickParams?.name || clickParams?.attrs?.name;
        if ((buttonType && buttonType !== "object") || !FLOW_BUTTONS.has(buttonName)) {
            return;
        }

        const root = this.model?.root;
        if (!root || root.isNew || !root.resId) {
            return;
        }

        try {
            await root.load();
            this.model.notify();
        } catch (error) {
            console.warn(
                "[OrdenesEspecialesSoftRefresh] No se pudo refrescar el registro.",
                error
            );
        }
    },
});
