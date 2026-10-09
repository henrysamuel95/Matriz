/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

function acUploadGetPanel() {
    let panel = document.getElementById("ac_upload_panel");
    if (!panel) {
        panel = document.createElement("div");
        panel.id = "ac_upload_panel";
        panel.className = "drv_upload_panel";
        panel.innerHTML = `
            <div class="drv_up_header">
                <i class="fa fa-cloud-upload drv_up_cloud_icon"></i>
                <span class="drv_up_title">Subiendo archivos</span>
                <button class="drv_up_close" title="Cerrar">&#x2715;</button>
            </div>
            <div class="drv_up_body">
                <div class="drv_up_progress_bar"><div class="drv_up_progress_fill"></div></div>
                <div class="drv_up_msg">Preparando…</div>
            </div>`;
        document.body.appendChild(panel);
        panel.querySelector(".drv_up_close").addEventListener("click", () => {
            panel.classList.add("drv_up_hidden");
        });
    }
    panel.classList.remove("drv_up_hidden", "drv_up_success", "drv_up_error");
    panel.querySelector(".drv_up_title").textContent = "Subiendo archivos";
    return panel;
}

function acUploadSetPanel(panel, msg, pct = 0) {
    panel.classList.remove("drv_up_hidden", "drv_up_success", "drv_up_error");
    const title = panel.querySelector(".drv_up_title");
    if (title) title.textContent = "Subiendo archivos";
    const text = panel.querySelector(".drv_up_msg");
    const fill = panel.querySelector(".drv_up_progress_fill");
    if (text) text.textContent = msg;
    if (fill) fill.style.width = `${Math.min(pct, 100)}%`;
}

function acUploadPanelDone(panel, msg) {
    panel.classList.add("drv_up_success");
    panel.querySelector(".drv_up_title").textContent = "Listo";
    acUploadSetPanel(panel, msg, 100);
    setTimeout(() => panel.classList.add("drv_up_hidden"), 4000);
}

patch(FormController.prototype, "Matriz.AcUpload", {
    setup() {
        this._super(...arguments);
        this._acOrm = useService("orm");
        this._acPendingItems = null;

        const isAcUpload = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === "archivo.central.upload";
        };

        // ── drag & drop ──────────────────────────────────────────
        this._acDragOver = (e) => {
            e.preventDefault();
            e.stopPropagation();
            document.getElementById("drv_drop_zone")?.classList.add("drv_drop_active");
        };

        this._acDragLeave = (e) => {
            if (document.getElementById("drv_drop_zone")?.contains(e.relatedTarget)) return;
            e.preventDefault();
            document.getElementById("drv_drop_zone")?.classList.remove("drv_drop_active");
        };

        this._acOnDrop = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            document.getElementById("drv_drop_zone")?.classList.remove("drv_drop_active");

            const items = Array.from(e.dataTransfer.items || []);
            const hasDir = items.some(i => i.webkitGetAsEntry?.()?.isDirectory);
            const files = Array.from(e.dataTransfer.files || []);

            if (!hasDir && files.length === 1) {
                this._acFeedBinary(files[0]);
            } else {
                const collected = [];
                for (const item of items) {
                    const entry = item.webkitGetAsEntry?.();
                    if (entry) await this._acReadEntry(entry, "", collected);
                }
                this._acPendingItems = collected;
                const label = document.getElementById("drv_drop_label");
                if (label) label.textContent = `${collected.length} archivo(s) listos — haz clic en Subir`;
            }
        };

        // ── file / folder pickers ────────────────────────────────
        this._acFilesSelected = async (e) => {
            const files = Array.from(e.target.files || []);
            if (!files.length) return;
            if (files.length === 1) {
                this._acFeedBinary(files[0]);
            } else {
                this._acPendingItems = files.map(f => ({ path: f.name, file: f }));
                const label = document.getElementById("drv_drop_label");
                if (label) label.textContent = `${files.length} archivo(s) listos — haz clic en Subir`;
            }
            e.target.value = "";
        };

        this._acFolderSelected = async (e) => {
            const files = Array.from(e.target.files || []);
            if (!files.length) {
                const label = document.getElementById("drv_drop_label");
                if (label) label.textContent = "Para subir una carpeta vacía, arrástrala aquí.";
                e.target.value = "";
                return;
            }
            this._acPendingItems = files.map(f => ({ path: f.webkitRelativePath || f.name, file: f }));
            const label = document.getElementById("drv_drop_label");
            if (label) label.textContent = `${this._acPendingItems.length} elemento(s) listos — haz clic en Subir`;
            e.target.value = "";
        };

        this._acClickFiles  = () => document.getElementById("drv_files_input")?.click();
        this._acClickFolder = async () => {
            if (!window.showDirectoryPicker) {
                document.getElementById("drv_folder_input")?.click();
                return;
            }
            try {
                const handle = await window.showDirectoryPicker();
                const collected = [];
                await this._acReadDirectoryHandle(handle, "", collected);
                this._acPendingItems = collected;
                const label = document.getElementById("drv_drop_label");
                if (label) label.textContent = `${collected.length} elemento(s) listos — haz clic en Subir`;
            } catch (err) {
                if (err && err.name !== "AbortError") {
                    console.warn("[AcUpload directory picker]", err);
                }
            }
        };

        // ── intercept Subir for multi-file/folder uploads ────────
        this._acSubmit = async (e) => {
            if (!this._acPendingItems?.length) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                const label = document.getElementById("drv_drop_label");
                if (label) label.textContent = "Arrastra una carpeta vacía o selecciona una carpeta con documentos.";
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            await this._acSendStructure(this._acPendingItems);
        };

        onMounted(() => {
            if (!isAcUpload()) return;
            setTimeout(() => {
                const zone = document.getElementById("drv_drop_zone");
                if (zone) {
                    zone.addEventListener("dragover",  this._acDragOver);
                    zone.addEventListener("dragleave", this._acDragLeave);
                    zone.addEventListener("drop",      this._acOnDrop);
                }
                document.getElementById("drv_files_input")
                    ?.addEventListener("change", this._acFilesSelected);
                document.getElementById("drv_folder_input")
                    ?.addEventListener("change", this._acFolderSelected);
                document.querySelector(".drv_pick_files_btn")
                    ?.addEventListener("click", this._acClickFiles);
                document.querySelector(".drv_pick_folder_btn")
                    ?.addEventListener("click", this._acClickFolder);

                const submitBtn = document.querySelector(".drv_btn_upload_submit");
                submitBtn?.addEventListener("click", this._acSubmit, true);
            }, 300);
        });

        onWillUnmount(() => {
            if (!isAcUpload()) return;
            const zone = document.getElementById("drv_drop_zone");
            if (zone) {
                zone.removeEventListener("dragover",  this._acDragOver);
                zone.removeEventListener("dragleave", this._acDragLeave);
                zone.removeEventListener("drop",      this._acOnDrop);
            }
            document.getElementById("drv_files_input")
                ?.removeEventListener("change", this._acFilesSelected);
            document.getElementById("drv_folder_input")
                ?.removeEventListener("change", this._acFolderSelected);
            document.querySelector(".drv_pick_files_btn")
                ?.removeEventListener("click", this._acClickFiles);
            document.querySelector(".drv_pick_folder_btn")
                ?.removeEventListener("click", this._acClickFolder);
            document.querySelector(".drv_btn_upload_submit")
                ?.removeEventListener("click", this._acSubmit, true);
        });
    },

    async _acReadEntry(entry, base, out) {
        const path = base ? `${base}/${entry.name}` : entry.name;
        if (entry.isFile) {
            const file = await new Promise((ok, err) => entry.file(ok, err));
            out.push({ path, file });
        } else if (entry.isDirectory) {
            const reader = entry.createReader();
            let batch;
            let hasChildren = false;
            do {
                batch = await new Promise((ok, err) => reader.readEntries(ok, err));
                for (const child of batch) {
                    hasChildren = true;
                    await this._acReadEntry(child, path, out);
                }
            } while (batch.length);
            if (!hasChildren) {
                out.push({ path, empty_folder: true });
            }
        }
    },

    async _acReadDirectoryHandle(handle, base, out) {
        const path = base ? `${base}/${handle.name}` : handle.name;
        let hasChildren = false;
        for await (const child of handle.values()) {
            hasChildren = true;
            if (child.kind === "file") {
                const file = await child.getFile();
                out.push({ path: `${path}/${file.name}`, file });
            } else if (child.kind === "directory") {
                await this._acReadDirectoryHandle(child, path, out);
            }
        }
        if (!hasChildren) {
            out.push({ path, empty_folder: true });
        }
    },

    _acFeedBinary(file) {
        const input = document.querySelector(".drv_file_input input[type='file']");
        if (!input) return;
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        const label = document.getElementById("drv_drop_label");
        if (label) label.textContent = `${file.name} — listo para subir`;
    },

    async _acSendStructure(collected) {
        if (!collected.length) return;
        const label = document.getElementById("drv_drop_label");
        const zone  = document.getElementById("drv_drop_zone");
        const setLabel = t => { if (label) label.textContent = t; };

        const formData    = this.model?.root?.data;
        // Many2one puede ser [id, name] (legado) o {id, display_name} (OWL)
        const browserId   = formData?.browser_id?.id   ?? formData?.browser_id?.[0]   ?? formData?.browser_id;
        const workspaceId = formData?.workspace_id?.id ?? formData?.workspace_id?.[0] ?? formData?.workspace_id;

        if (!browserId || !workspaceId) {
            setLabel("Error: no se pudo determinar la carpeta destino.");
            return;
        }

        setLabel(`Preparando ${collected.length} elemento(s)…`);
        zone?.classList.add("drv_uploading");

        const items = [];
        for (const { path, file } of collected) {
            if (!file) {
                items.push({ path, empty_folder: true });
                continue;
            }
            const data = await new Promise(ok => {
                const fr = new FileReader();
                fr.onload = e => ok(e.target.result.split(",")[1]);
                fr.readAsDataURL(file);
            });
            items.push({ path, data });
        }

        setLabel(`Subiendo ${collected.length} elemento(s) a Drive…`);
        try {
            const uploadToken = Date.now();
            window.__acUploadToken = uploadToken;
            if (window.__acUploadPoll) {
                clearTimeout(window.__acUploadPoll);
                window.__acUploadPoll = null;
            }
            const result = await this._acOrm.call(
                "archivo.central",
                "action_upload_folder_structure",
                [[browserId]],
                { folder_data: { workspace_id: workspaceId, items } }
            );

            if (result && result.background) {
                const panel = acUploadGetPanel();
                acUploadSetPanel(panel, `Subiendo ${result.total} elemento(s) en segundo plano…`, 5);
                this._acPendingItems = null;
                zone?.classList.remove("drv_uploading");
                document.querySelector(
                    ".o_dialog footer .btn-secondary, .o_dialog .modal-footer .btn-secondary"
                )?.click();

                const readState = () => fetch("/web/dataset/call_kw", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
                    credentials: "same-origin",
                    body: JSON.stringify({
                        jsonrpc: "2.0",
                        method: "call",
                        id: Date.now(),
                        params: {
                            model: "archivo.central",
                            method: "read",
                            args: [[browserId], ["sync_state", "upload_progress"]],
                            kwargs: { context: {} },
                        },
                    }),
                }).then(r => r.json()).then(d => (d?.result || [])[0]);

                const poll = async () => {
                    if (window.__acUploadToken !== uploadToken) return;
                    try {
                        const rec = await readState();
                        if (!rec || rec.sync_state !== "sincronizando") {
                            acUploadPanelDone(panel, `${result.total} elemento(s) subido(s) correctamente`);
                            window.dispatchEvent(new CustomEvent("ac_upload_complete"));
                            return;
                        }
                        const prog = rec.upload_progress || "";
                        let pct = 10;
                        let msg = "Subiendo en segundo plano…";
                        if (prog) {
                            const [counter, fileName] = prog.split("|");
                            const [done, total] = counter.split("/").map(Number);
                            if (total) pct = Math.round((done / total) * 95);
                            msg = `Subiendo ${done} de ${total}: ${fileName || ""}`;
                        }
                        acUploadSetPanel(panel, msg, pct);
                        window.__acUploadPoll = setTimeout(poll, 1500);
                    } catch (pollError) {
                        console.warn("[AcUpload poll]", pollError);
                        window.__acUploadPoll = setTimeout(poll, 3000);
                    }
                };
                window.__acUploadPoll = setTimeout(poll, 1500);
                return;
            }

            setLabel(`✓ ${collected.length} elemento(s) subido(s) correctamente`);
            zone?.classList.remove("drv_uploading");
            zone?.classList.add("drv_drop_success");
            this._acPendingItems = null;
            window.dispatchEvent(new CustomEvent("ac_upload_complete"));
            setTimeout(() => {
                const closeBtn = document.querySelector(
                    ".o_dialog footer .btn-secondary, .o_dialog .modal-footer .btn-secondary"
                );
                closeBtn?.click();
            }, 1400);
        } catch (err) {
            setLabel("Error al subir. Revisa la consola para más detalles.");
            zone?.classList.remove("drv_uploading");
            console.error("[AcUpload]", err);
        }
    },
});
