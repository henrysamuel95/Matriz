/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useEffect } from "@odoo/owl";

patch(FormController.prototype, "Matriz.PDFUnificadoProgress", {
    setup() {
        this._super(...arguments);
        this._pdfOrm = useService("orm");
        this._pdfPoll = null;
        this._pdfLastState = null;
        this._pdfReloadedDocumentId = null;
        this._pdfGeneration = 0;

        // El formulario principal escucha cuando la generación se inicia desde
        // el modal de ORDENAR PDF. Así el seguimiento continúa aunque el wizard
        // se cierre y la barra del formulario se actualiza sola.
        useEffect(() => {
            const handler = (ev) => {
                const model = this.props.resModel || this.model?.config?.resModel;
                const resId = this.model?.root?.resId;
                const actaId = ev?.detail?.actaId;
                if (model !== "acta_entrega_documentos" || !resId || resId !== actaId) return;
                this._pdfLastState = null;
                this._pdfReloadedDocumentId = null;
                this._startPDFUnificadoPoll(actaId);
            };
            window.addEventListener("sgo:pdf-unificado-start", handler);
            return () => window.removeEventListener("sgo:pdf-unificado-start", handler);
        }, () => []);

        useEffect(() => {
            const model = this.props.resModel || this.model?.config?.resModel;
            if (model !== "acta_entrega_documentos") return;
            const resId = this.model?.root?.resId;
            if (!resId || this.model.root.isNew) return;
            this._pdfLastState = null;
            this._pdfReloadedDocumentId = null;
            this._startPDFUnificadoPoll(resId);
            return () => {
                this._pdfGeneration++;
                window.clearTimeout(this._pdfPoll);
                this._pdfPoll = null;
                window.$?.("#sgo_pdf_unificado_progress").remove();
            };
        }, () => [this.model?.root?.resId]);
    },

    _pdfGetMany2OneId(value) {
        if (!value) return null;
        if (Array.isArray(value)) return value[0] || null;
        if (typeof value === "number") return value;
        if (typeof value === "object") return value.resId || value.id || value[0] || null;
        return null;
    },

    async afterExecuteActionButton(clickParams) {
        const modelBefore = this.props.resModel || this.model?.config?.resModel;
        const rootBefore = this.model?.root;
        let actaIdFromWizard = null;

        if (modelBefore === "acta.pdf.unificado.orden.wizard") {
            actaIdFromWizard = this._pdfGetMany2OneId(rootBefore?.data?.acta_id);
        }

        const result = await this._super(...arguments);

        if (
            clickParams.name === "action_generar_pdf_unificado" &&
            modelBefore === "acta_entrega_documentos"
        ) {
            const actaId = rootBefore?.resId;
            if (actaId) this._startPDFUnificadoPoll(actaId);
        }

        if (
            modelBefore === "acta.pdf.unificado.orden.wizard" &&
            ["action_aplicar_y_generar", "action_usar_orden_base"].includes(clickParams.name) &&
            actaIdFromWizard
        ) {
            // No dejamos el seguimiento amarrado al transient que se va a cerrar.
            // Se lo entregamos al formulario principal del acta.
            window.dispatchEvent(new CustomEvent("sgo:pdf-unificado-start", {
                detail: {actaId: actaIdFromWizard},
            }));
        }

        return result;
    },

    _startPDFUnificadoPoll(resId) {
        window.clearTimeout(this._pdfPoll);
        const generation = ++this._pdfGeneration;
        this._pdfPoll = window.setTimeout(
            () => this._pollPDFUnificado(resId, generation),
            250
        );
    },

    async _syncActaFormProgress(resId) {
        const currentModel = this.props.resModel || this.model?.config?.resModel;
        const currentId = this.model?.root?.resId;
        if (currentModel !== "acta_entrega_documentos" || currentId !== resId) return;
        try {
            await this.model.root.load();
            this.model.notify();
        } catch (error) {
            console.debug("[PDF unificado] No fue posible refrescar el formulario", error);
        }
    },

    async _pollPDFUnificado(resId, generation, failures = 0, waitingStarts = 0) {
        if (generation !== this._pdfGeneration) return;
        try {
            const rows = await this._pdfOrm.silent.read(
                "acta_entrega_documentos",
                [resId],
                ["pdf_unificado_estado", "pdf_unificado_progreso", "pdf_unificado_mensaje", "documento_unificado_id"]
            );
            if (generation !== this._pdfGeneration) return;
            const data = rows[0];
            if (!data) return;

            if (data.pdf_unificado_estado === "procesando") {
                this._pdfReloadedDocumentId = null;
                this._showPDFUnificadoProgress(data);
                // Antes esta barra interna quedaba congelada hasta F5. Ahora se
                // sincroniza automáticamente con cada lectura del progreso.
                await this._syncActaFormProgress(resId);
            } else if (data.pdf_unificado_estado === "completado") {
                const documento = data.documento_unificado_id;
                const documentoId = Array.isArray(documento) ? documento[0] : documento;
                const cambioACompletado = this._pdfLastState === "procesando";

                if (cambioACompletado) this._showPDFUnificadoDone();

                if (
                    cambioACompletado &&
                    documentoId &&
                    this._pdfReloadedDocumentId !== documentoId
                ) {
                    this._pdfReloadedDocumentId = documentoId;
                    await this._syncActaFormProgress(resId);
                }
            } else if (data.pdf_unificado_estado === "error") {
                this._showPDFUnificadoError(data.pdf_unificado_mensaje || "No fue posible generar el PDF.");
                await this._syncActaFormProgress(resId);
            }

            const estadoAnterior = this._pdfLastState;
            this._pdfLastState = data.pdf_unificado_estado;

            if (data.pdf_unificado_estado === "procesando" && generation === this._pdfGeneration) {
                this._pdfPoll = window.setTimeout(
                    () => this._pollPDFUnificado(resId, generation),
                    1000
                );
            } else if (
                generation === this._pdfGeneration &&
                waitingStarts < 12 &&
                estadoAnterior === null
            ) {
                this._pdfPoll = window.setTimeout(
                    () => this._pollPDFUnificado(resId, generation, failures, waitingStarts + 1),
                    500
                );
            }
        } catch (error) {
            if (generation !== this._pdfGeneration) return;
            console.error("[PDF unificado] Error consultando el progreso", error);
            if (failures < 5) {
                this._pdfPoll = window.setTimeout(
                    () => this._pollPDFUnificado(resId, generation, failures + 1, waitingStarts),
                    3000
                );
            }
        }
    },

    _ensurePDFToast() {
        const $ = window.$;
        if (!$ || $("#sgo_pdf_unificado_progress").length) return;
        $("body").append(`
            <div id="sgo_pdf_unificado_progress" style="position:fixed;right:22px;bottom:22px;width:330px;background:#fff;border:1px solid #cfe0ff;border-left:5px solid #043484;border-radius:10px;box-shadow:0 8px 24px rgba(4,52,132,.20);padding:14px 16px;z-index:10000;font-family:Roboto,sans-serif;display:none;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                    <strong id="sgo_pdf_title" style="color:#043484;font-size:14px;">Unificando documentos</strong>
                    <i id="sgo_pdf_icon" class="fa fa-circle-o-notch fa-spin" style="color:#043484;"></i>
                </div>
                <div id="sgo_pdf_message" style="font-size:12px;color:#52647e;margin-top:6px;white-space:normal;"></div>
                <div style="height:7px;background:#e6edf7;border-radius:8px;overflow:hidden;margin-top:10px;">
                    <div id="sgo_pdf_bar" style="height:100%;width:0;background:#0b4aa2;transition:width .3s ease;"></div>
                </div>
                <div id="sgo_pdf_percent" style="font-size:11px;text-align:right;color:#52647e;margin-top:4px;">0%</div>
            </div>`);
    },

    _showPDFUnificadoProgress(data) {
        const $ = window.$;
        this._ensurePDFToast();
        const progress = Math.max(0, Math.min(100, Math.round(data.pdf_unificado_progreso || 0)));
        $("#sgo_pdf_unificado_progress").show().css("border-left-color", "#043484");
        $("#sgo_pdf_title").text("Unificando documentos");
        $("#sgo_pdf_message").text(data.pdf_unificado_mensaje || "Preparando documentos...");
        $("#sgo_pdf_icon").attr("class", "fa fa-circle-o-notch fa-spin").css("color", "#043484");
        $("#sgo_pdf_bar").css({width: `${progress}%`, background: "#0b4aa2"});
        $("#sgo_pdf_percent").text(`${progress}%`);
    },

    _showPDFUnificadoDone() {
        const $ = window.$;
        this._ensurePDFToast();
        $("#sgo_pdf_unificado_progress").show().css("border-left-color", "#22a35a");
        $("#sgo_pdf_title").text("PDF unificado listo");
        $("#sgo_pdf_message").text("El documento quedó guardado en Google Drive.");
        $("#sgo_pdf_icon").attr("class", "fa fa-check-circle").css("color", "#22a35a");
        $("#sgo_pdf_bar").css({width: "100%", background: "#22a35a"});
        $("#sgo_pdf_percent").text("100%");
        window.setTimeout(() => $("#sgo_pdf_unificado_progress").fadeOut(), 4500);
    },

    _showPDFUnificadoError(message) {
        const $ = window.$;
        this._ensurePDFToast();
        $("#sgo_pdf_unificado_progress").show().css("border-left-color", "#c2410c");
        $("#sgo_pdf_title").text("No se pudo unificar");
        $("#sgo_pdf_message").text(message);
        $("#sgo_pdf_icon").attr("class", "fa fa-exclamation-triangle").css("color", "#c2410c");
        $("#sgo_pdf_bar").css({width: "100%", background: "#c2410c"});
        $("#sgo_pdf_percent").text("Error");
    },
});
