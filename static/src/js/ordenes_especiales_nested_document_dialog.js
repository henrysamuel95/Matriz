/** @odoo-module **/

import { ViewButton } from "@web/views/view_button/view_button";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(ViewButton.prototype, "Matriz.OrdenesEspecialesNestedDocumentDialog", {
    setup() {
        this._super(...arguments);
        this._oeNestedDialog = useService("dialog");
        this._oeNestedOrm = useService("orm");
    },

    async onClick(ev) {
        const record = this.props.record;
        const clickParams = this.clickParams || {};
        const isOrdenEspecialDocumento =
            record?.resModel === "ordenes_especiales.documento";
        const isVerDocumento = clickParams.name === "action_ver_documento";
        const isEliminarDocumento = clickParams.name === "action_eliminar_documento";

        if (!isOrdenEspecialDocumento || (!isVerDocumento && !isEliminarDocumento)) {
            return this._super(...arguments);
        }

        ev.preventDefault();
        ev.stopPropagation();

        if (isEliminarDocumento) {
            const confirmado = window.confirm(
                "¿Está seguro de eliminar este documento?"
            );
            if (!confirmado) {
                return;
            }

            await this._oeNestedOrm.call(
                "ordenes_especiales.documento",
                "action_eliminar_documento",
                [[record.resId]],
                {}
            );

            const model = record.model;
            if (model?.root?.load) {
                await model.root.load();
                model.notify();
            }
            return;
        }

        const action = await this._oeNestedOrm.call(
            "ordenes_especiales.documento",
            "action_ver_documento",
            [[record.resId]],
            {}
        );

        if (!action?.res_model || !action?.res_id) {
            return;
        }

        this._oeNestedDialog.add(FormViewDialog, {
            title: action.name || "Documento",
            resModel: action.res_model,
            resId: action.res_id,
            context: action.context || {},
            mode: "readonly",
            preventCreate: true,
            preventEdit: true,
            size: "xl",
        });
    },
});
