/** @odoo-module **/

// Selecciona al abrir el cierre la pestaña correspondiente a su estado,
// conservando visibles las pestañas anteriores para consulta del historial.
import { onMounted, onPatched } from "@odoo/owl";
import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

patch(FormController.prototype, "Matriz.CierreContractualActiveTab", {
    setup() {
        this._super(...arguments);
        this._cierreEstadoPestana = null;

        const seleccionarPestanaActual = () => {
            const modelName =
                this.props?.resModel ||
                this.model?.config?.resModel ||
                this.model?.root?.resModel;
            if (modelName !== "contrataciones_fuv.cierre.proceso") {
                return;
            }

            const estado = this.model?.root?.data?.estado;
            if (!estado || estado === this._cierreEstadoPestana) {
                return;
            }

            const pestanaPorEstado = {
                financiera: "Financiera",
                juridica: "Jurídica",
                firma_coordinador: "Cierre",
                liberacion_rfc: "Cierre",
                finalizado: "Cierre",
                cancelado: "Cierre",
            };
            const etiqueta = pestanaPorEstado[estado];
            if (!etiqueta) {
                return;
            }

            this._cierreEstadoPestana = estado;
            requestAnimationFrame(() => {
                const formulario = document.querySelector(".fc-cierre-proceso-form");
                if (!formulario) {
                    return;
                }
                const pestana = [...formulario.querySelectorAll(".o_notebook .nav-link")]
                    .find((elemento) => elemento.textContent.trim() === etiqueta);
                if (pestana && !pestana.classList.contains("active")) {
                    pestana.click();
                }
            });
        };

        onMounted(seleccionarPestanaActual);
        onPatched(seleccionarPestanaActual);
    },
});
