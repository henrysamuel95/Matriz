/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";

registry.category("actions").add("matriz.sgr_sync_documents", (env, action) => {
    window.dispatchEvent(new CustomEvent("matriz_sgr_sync", {
        detail: action.params.record_id,
    }));
    return { type: "ir.actions.act_window_close" };
});

patch(FormController.prototype, "Matriz.SgrDriveSync", {
    setup() {
        this._super(...arguments);
        this.sgrOrm = useService("orm");
        this.sgrSyncMounted = true;
        this.sgrSyncRunning = false;
        this.sgrSyncChecking = false;

        this.sgrSyncEvent = (event) => {
            if (this._sgrIsCurrentForm(event.detail)) {
                this._sgrRunSync(event.detail);
            }
        };
        onMounted(() => {
            window.addEventListener("matriz_sgr_sync", this.sgrSyncEvent);
            const recordId = this.model?.root?.resId;
            if (this._sgrIsCurrentForm(recordId)) {
                this._sgrMaybeSync(recordId);
            }
        });
        onWillUnmount(() => {
            this.sgrSyncMounted = false;
            window.removeEventListener("matriz_sgr_sync", this.sgrSyncEvent);
            document.getElementById("sgr-drive-toast")?.remove();
        });
    },

    _sgrIsCurrentForm(recordId) {
        const model = this.props.resModel || this.model?.config?.resModel;
        return model === "contrataciones_sgr" && recordId &&
            this.model?.root?.resId === recordId;
    },

    _sgrShowToast(title, detail, completed = 0, total = 0, state = "loading") {
        if (!this.sgrSyncMounted) return;
        let toast = document.getElementById("sgr-drive-toast");
        if (!toast) {
            toast = document.createElement("div");
            toast.id = "sgr-drive-toast";
            toast.innerHTML = '<strong class="sgr-drive-toast__title"></strong>' +
                '<span class="sgr-drive-toast__detail"></span>' +
                '<span class="sgr-drive-toast__track"><span></span></span>';
            document.body.appendChild(toast);
        }
        toast.dataset.state = state;
        toast.querySelector(".sgr-drive-toast__title").textContent = title;
        toast.querySelector(".sgr-drive-toast__detail").textContent = detail;
        toast.querySelector(".sgr-drive-toast__track span").style.width =
            `${total ? Math.round(completed * 100 / total) : 0}%`;
        if (state === "success") {
            setTimeout(() => {
                if (toast.dataset.state === "success") toast.remove();
            }, 4000);
        }
    },

    async _sgrMaybeSync(recordId) {
        if (!this._sgrIsCurrentForm(recordId) || this.sgrSyncRunning || this.sgrSyncChecking) return;
        this.sgrSyncChecking = true;
        try {
            const [record] = await this.sgrOrm.read("contrataciones_sgr", [recordId], [
                "estado_sincronizacion_drive", "contratista_proveedor_relacionado", "drive_file_id",
            ]);
            if (record && record.contratista_proveedor_relacionado && record.drive_file_id &&
                record.estado_sincronizacion_drive !== "completado" && this.sgrSyncMounted) {
                this._sgrRunSync(recordId);
            }
        } catch (error) {
            console.error("No se pudo comprobar la sincronización SGR", error);
        } finally {
            this.sgrSyncChecking = false;
        }
    },

    async _sgrRunSync(recordId) {
        if (!this._sgrIsCurrentForm(recordId) || this.sgrSyncRunning) return;
        this.sgrSyncRunning = true;
        this._sgrShowToast("Documentos SGR", "Buscando archivos del proveedor...");
        try {
            if (!navigator.onLine) throw new Error("Sin conexión a internet.");
            const jobs = await this.sgrOrm.silent.call(
                "contrataciones_sgr", "js_sgr_get_pending_files", [recordId]
            );
            if (!this.sgrSyncMounted) return;
            for (let index = 0; index < jobs.length; index++) {
                const job = jobs[index];
                let success = false;
                for (let attempt = 0; attempt < 3 && !success; attempt++) {
                    this._sgrShowToast(
                        `Copiando ${index + 1} de ${jobs.length}`, job.title,
                        index, jobs.length
                    );
                    try {
                        const response = await this.sgrOrm.silent.call(
                            "contrataciones_sgr", "js_sgr_copy_single_file",
                            [recordId, job.documento_id, job.source_id]
                        );
                        if (response?.status !== "success") {
                            throw new Error(response?.message || "Drive no confirmó la copia.");
                        }
                        success = true;
                    } catch (error) {
                        if (attempt === 2) throw error;
                        await new Promise((resolve) => setTimeout(resolve, 2000));
                    }
                }
                if (!this.sgrSyncMounted) return;
                this._sgrShowToast(
                    `Copiando ${index + 1} de ${jobs.length}`, job.title,
                    index + 1, jobs.length
                );
            }
            await this.sgrOrm.silent.call("contrataciones_sgr", "js_sgr_finish_sync", [recordId]);
            if (this.sgrSyncMounted && this._sgrIsCurrentForm(recordId)) {
                const dirty = this.model.root.isDirty;
                const hasEdits = typeof dirty === "function" ?
                    await dirty.call(this.model.root) : Boolean(dirty);
                if (!hasEdits) {
                    await this.model.root.load();
                    this.model.notify();
                }
                this._sgrShowToast(
                    "Documentos SGR", jobs.length ? "Copia completada." : "No hay archivos nuevos.",
                    1, 1, "success"
                );
            }
        } catch (error) {
            console.error("Error al copiar documentos SGR", error);
            this._sgrShowToast("Sincronización pendiente", "No se pudieron copiar todos los archivos.",
                0, 1, "error");
        } finally {
            this.sgrSyncRunning = false;
        }
    },

    async saveButtonClicked(params = {}) {
        const saved = await this._super(...arguments);
        const recordId = this.model?.root?.resId;
        if (saved && this.sgrSyncMounted && this._sgrIsCurrentForm(recordId)) {
            this._sgrMaybeSync(recordId);
        }
        return saved;
    },

    async save(record, params = {}) {
        const saved = await this._super(...arguments);
        const recordId = this.model?.root?.resId;
        if (saved && this.sgrSyncMounted && this._sgrIsCurrentForm(recordId) &&
            (!params || !params.fromButton)) {
            this._sgrMaybeSync(recordId);
        }
        return saved;
    },
});
