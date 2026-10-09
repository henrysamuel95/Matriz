/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

patch(FormController.prototype, "Matriz.DrvUpload", {
    setup() {
        this._super(...arguments);
        this._drvOrm = useService("orm");
        this._drvPendingItems = null;
        this._drvUploading    = false;
        this._drvDidUpload    = false; // solo true si hubo subida real completada

        const isUpload = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === "drive.browser.upload";
        };
        const isInfCom = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === "informes_drive" || m === "comunicaciones_drive";
        };
        const isNewMcb = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === "new_mcb" || m === "new_mcb_otrosi";
        };
        const uploadModeValue = () => this.model?.root?.data?.upload_mode || 'file';
        const uploadModeBase = () => String(uploadModeValue()).split('|')[0] || 'file';
        const shouldRefreshParent = () => String(uploadModeValue()).includes('infcom_reload');
        const reloadParentView = () => {
            if (shouldRefreshParent()) {
                window.dispatchEvent(new CustomEvent('drv_upload_complete'));
            }
        };

        this._drvInfcomUploadCompleteHandler = async () => {
            if (!isInfCom()) return;
            await this._drvRefreshInfcomView();
        };

        this._drvNewMcbUploadCompleteHandler = async () => {
            if (!isNewMcb()) return;
            try {
                await this.model.root.load();
                this.model.notify();
            } catch (err) {
                console.warn("[DrvUpload] new_mcb refresh after upload failed", err);
            }
        };

        // ── drag & drop ──────────────────────────────────────────
        this._drvDragOver = (e) => {
            e.preventDefault();
            e.stopPropagation();
            document.getElementById("drv_drop_zone")?.classList.add("drv_drop_active");
        };

        this._drvDragLeave = (e) => {
            if (document.getElementById("drv_drop_zone")?.contains(e.relatedTarget)) return;
            e.preventDefault();
            document.getElementById("drv_drop_zone")?.classList.remove("drv_drop_active");
        };

        this._drvOnDrop = async (e) => {
            e.preventDefault();
            e.stopPropagation();
            document.getElementById("drv_drop_zone")?.classList.remove("drv_drop_active");

            const mode = uploadModeBase();
            const items = Array.from(e.dataTransfer.items || []);
            const hasDir = items.some(i => i.webkitGetAsEntry?.()?.isDirectory);

            if (mode === 'folder') {
                const collected = [];
                for (const item of items) {
                    const entry = item.webkitGetAsEntry?.();
                    if (entry) await this._drvReadEntry(entry, "", collected);
                }
                this._drvPendingItems = collected;
                this._drvSetActiveLabel(`${collected.length} archivo(s) listos — haz clic en Subir`);
            } else {
                if (!hasDir && items.length === 1) {
                    this._drvFeedBinary(e.dataTransfer.files[0]);
                } else {
                    const collected = [];
                    for (const item of items) {
                        const entry = item.webkitGetAsEntry?.();
                        if (entry) await this._drvReadEntry(entry, "", collected);
                    }
                    this._drvPendingItems = collected;
                    this._drvSetActiveLabel(`${collected.length} archivo(s) listos — haz clic en Subir`);
                }
            }
        };

        // ── file / folder pickers ────────────────────────────────
        this._drvFilesSelected = async (e) => {
            const files = Array.from(e.target.files || []);
            if (!files.length) return;
            if (files.length === 1) {
                this._drvFeedBinary(files[0]);
            } else {
                this._drvPendingItems = files.map(f => ({ path: f.name, file: f }));
                this._drvSetActiveLabel(`${files.length} archivo(s) listos — haz clic en Subir`);
            }
            e.target.value = "";
        };

        this._drvFolderSelected = async (e) => {
            const files = Array.from(e.target.files || []);
            if (!files.length) {
                const emptyName = window.prompt("La carpeta está vacía. Escribe el nombre de la carpeta para crearla:");
                if (!emptyName) return;
                this._drvPendingItems = [{ path: emptyName.trim(), empty_folder: true }];
                this._drvSetActiveLabel(`Carpeta vacía "${emptyName.trim()}" lista — haz clic en Subir`);
                e.target.value = "";
                return;
            }
            this._drvPendingItems = files.map(f => ({ path: f.webkitRelativePath || f.name, file: f }));
            this._drvSetActiveLabel(`${this._drvPendingItems.length} elemento(s) listos — haz clic en Subir`);
            e.target.value = "";
        };

        this._drvClickFiles  = () => document.getElementById("drv_files_input")?.click();
        this._drvClickFolder = () => document.getElementById("drv_folder_input")?.click();

        this._drvInfcomDragOver = (e) => {
            if (!isInfCom()) return;
            const zone = e.currentTarget;
            e.preventDefault();
            e.stopPropagation();
            zone?.classList.add("drv_infcom_drag_over");
        };

        this._drvInfcomDragLeave = (e) => {
            if (!isInfCom()) return;
            const zone = e.currentTarget;
            if (zone?.contains(e.relatedTarget)) return;
            e.preventDefault();
            e.stopPropagation();
            zone?.classList.remove("drv_infcom_drag_over");
        };

        this._drvInfcomDrop = async (e) => {
            if (!isInfCom()) return;
            const zone = e.currentTarget;
            e.preventDefault();
            e.stopPropagation();
            zone?.classList.remove("drv_infcom_drag_over");
            const mode = zone?.dataset?.drvDropMode || "file";
            const items = Array.from(e.dataTransfer.items || []);
            const files = Array.from(e.dataTransfer.files || []);
            const collected = [];

            zone?.classList.add("drv_infcom_uploading");
            try {
                if (items.length) {
                    for (const item of items) {
                        const entry = item.webkitGetAsEntry?.();
                        if (entry) {
                            await this._drvReadEntry(entry, "", collected);
                        }
                    }
                }
                if (!collected.length && files.length) {
                    for (const file of files) {
                        collected.push({ path: file.name, file });
                    }
                }
                if (!collected.length) return;
                await this._drvSendInfcomDrop(collected, mode, zone);
            } finally {
                zone?.classList.remove("drv_infcom_uploading");
            }
        };

        // ── Intercepción global (capture) — corre ANTES que Odoo ──
        this._drvDocCapture = (e) => {
            if (!isUpload()) return;
            if (!e.target.closest('.drv_btn_upload_submit')) return;
            const mode = uploadModeBase();
            if (!this._drvPendingItems?.length) {
                if (mode !== 'folder') return;
                e.preventDefault();
                e.stopImmediatePropagation();
                e.stopPropagation();
                const emptyName = window.prompt("La carpeta está vacía. Escribe el nombre de la carpeta para crearla:");
                if (!emptyName || !emptyName.trim()) return;
                this._drvPendingItems = [{ path: emptyName.trim(), empty_folder: true }];
                this._drvSetActiveLabel(`Carpeta vacía "${emptyName.trim()}" lista para subir`);
            }
            e.preventDefault();
            e.stopImmediatePropagation();
            e.stopPropagation();
            this._drvSendStructure(this._drvPendingItems);
        };

        onMounted(() => {
            if (isNewMcb()) {
                window.addEventListener("drv_upload_complete", this._drvNewMcbUploadCompleteHandler);
            }
            if (isInfCom()) {
                window.addEventListener("drv_upload_complete", this._drvInfcomUploadCompleteHandler);
                setTimeout(() => this._drvBindInfcomDropZones(), 300);
            }
            if (!isUpload()) return;
            document.addEventListener('click', this._drvDocCapture, true);
            setTimeout(() => {
                const zone = document.getElementById("drv_drop_zone");
                if (zone) {
                    zone.addEventListener("dragover",  this._drvDragOver);
                    zone.addEventListener("dragleave", this._drvDragLeave);
                    zone.addEventListener("drop",      this._drvOnDrop);
                }
                document.getElementById("drv_files_input")
                    ?.addEventListener("change", this._drvFilesSelected);
                document.getElementById("drv_folder_input")
                    ?.addEventListener("change", this._drvFolderSelected);
                document.querySelector(".drv_pick_files_btn")
                    ?.addEventListener("click", this._drvClickFiles);
                document.querySelector(".drv_pick_folder_btn")
                    ?.addEventListener("click", this._drvClickFolder);

                const mode = uploadModeBase();
                const isFolder = mode === 'folder';
                const show = (el, v) => { if (el) el.style.display = v ? '' : 'none'; };
                show(document.querySelector(".drv_pick_files_btn"),    !isFolder);
                show(document.querySelector(".drv_pick_folder_btn"),   isFolder);
                show(document.getElementById("drv_drop_label"),        !isFolder);
                show(document.getElementById("drv_drop_label_folder"), isFolder);
            }, 300);
        });

        onWillUnmount(() => {
            window.removeEventListener("drv_upload_complete", this._drvNewMcbUploadCompleteHandler);
            window.removeEventListener("drv_upload_complete", this._drvInfcomUploadCompleteHandler);
            // Solo recargar si hubo una subida real completada
            // (no en cancelaciones ni cierres sin subir)
            if (isUpload() && this._drvDidUpload && !this._drvUploading) {
                window.dispatchEvent(new CustomEvent('drv_upload_complete'));
                reloadParentView();
            }
            document.removeEventListener('click', this._drvDocCapture, true);
            const zone = document.getElementById("drv_drop_zone");
            if (zone) {
                zone.removeEventListener("dragover",  this._drvDragOver);
                zone.removeEventListener("dragleave", this._drvDragLeave);
                zone.removeEventListener("drop",      this._drvOnDrop);
            }
            document.getElementById("drv_files_input")
                ?.removeEventListener("change", this._drvFilesSelected);
            document.getElementById("drv_folder_input")
                ?.removeEventListener("change", this._drvFolderSelected);
            document.querySelector(".drv_pick_files_btn")
                ?.removeEventListener("click", this._drvClickFiles);
            document.querySelector(".drv_pick_folder_btn")
                ?.removeEventListener("click", this._drvClickFolder);
            this._drvUnbindInfcomDropZones();
        });
    },

    _drvBindInfcomDropZones() {
        this._drvInfcomZones = Array.from(document.querySelectorAll(".infcom-folder-cards, .infcom-file-cards"));
        for (const zone of this._drvInfcomZones) {
            if (zone.dataset.drvInfcomBound === "1") continue;
            zone.dataset.drvInfcomBound = "1";
            zone.dataset.drvDropMode = zone.classList.contains("infcom-folder-cards") ? "folder" : "file";
            if (!zone.querySelector(":scope > .drv_infcom_overlay")) {
                const overlay = document.createElement("div");
                overlay.className = "drv_infcom_overlay";
                overlay.innerHTML = `
                    <i class="fa fa-cloud-upload"></i>
                    <span>${zone.dataset.drvDropMode === "folder" ? "Suelta aquí la carpeta" : "Suelta aquí el documento"}</span>`;
                zone.appendChild(overlay);
            }
            zone.addEventListener("dragover", this._drvInfcomDragOver);
            zone.addEventListener("dragleave", this._drvInfcomDragLeave);
            zone.addEventListener("drop", this._drvInfcomDrop);
        }
    },

    _drvUnbindInfcomDropZones() {
        for (const zone of this._drvInfcomZones || []) {
            zone.removeEventListener("dragover", this._drvInfcomDragOver);
            zone.removeEventListener("dragleave", this._drvInfcomDragLeave);
            zone.removeEventListener("drop", this._drvInfcomDrop);
            delete zone.dataset.drvInfcomBound;
        }
        this._drvInfcomZones = [];
    },

    async _drvSendInfcomDrop(collected, mode, zone) {
        const recordId = this.model?.root?.resId || this.props?.resId;
        const modelName = this.props.resModel || this.model?.config?.resModel;
        if (!recordId || !modelName) return;

        const panel = this._drvGetPanel();
        const setPnl = (msg, pct = 0) => {
            const el = panel.querySelector(".drv_up_msg");
            const fill = panel.querySelector(".drv_up_progress_fill");
            if (el) el.textContent = msg;
            if (fill) fill.style.width = `${Math.min(pct, 100)}%`;
        };

        panel.querySelector(".drv_up_title").textContent = mode === "folder" ? "Subiendo carpeta" : "Subiendo documento";
        setPnl("Preparando archivos…", 5);

        const items = [];
        for (let i = 0; i < collected.length; i++) {
            const { path, file, empty_folder } = collected[i];
            if (empty_folder || !file) {
                items.push({ path, empty_folder: true });
                continue;
            }
            const nombre = (file.name || path).split("/").pop().slice(0, 60);
            setPnl(`Preparando ${i + 1} de ${collected.length}: ${nombre}`, Math.max(5, Math.round(((i + 1) / collected.length) * 25)));
            const data = await new Promise(ok => {
                const fr = new FileReader();
                fr.onload = ev => ok(ev.target.result.split(",")[1]);
                fr.readAsDataURL(file);
            });
            items.push({ path, data });
        }

        setPnl(`Enviando ${items.length} elemento(s) a Google Drive…`, 30);
        try {
            const result = await this._drvOrm.call(
                modelName,
                "action_upload_drop_items",
                [[recordId]],
                { folder_data: { items, upload_mode: mode } }
            );

            if (result && result.background && result.browser_id) {
                const uploadToken = Date.now();
                window.__drvInfcomUploadToken = uploadToken;
                const total = result.total || items.length;
                const browserId = result.browser_id;
                const readState = () => fetch('/web/dataset/call_kw', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        jsonrpc: '2.0',
                        method: 'call',
                        id: Date.now(),
                        params: {
                            model: 'drive.browser',
                            method: 'read',
                            args: [[browserId], ['sync_state', 'upload_progress']],
                            kwargs: { context: {} },
                        }
                    })
                }).then(r => r.json()).then(d => (d?.result || [])[0]);

                const poll = async () => {
                    if (window.__drvInfcomUploadToken !== uploadToken) return;
                    try {
                        const rec = await readState();
                        if (!rec || rec.sync_state !== "sincronizando") {
                            panel.classList.add("drv_up_success");
                            panel.querySelector(".drv_up_title").textContent = "Listo";
                            setPnl(`${total} elemento(s) subido(s) correctamente`, 100);
                            zone?.classList.add("drv_infcom_success");
                            await this._drvRefreshInfcomView(zone);
                            this._drvAutoHidePanel(panel);
                        } else {
                            const prog = rec.upload_progress || "";
                            let pct = 35;
                            let msg = "Subiendo en segundo plano…";
                            if (prog) {
                                const [counter, fileName] = prog.split("|");
                                const [done, totalProgress] = counter.split("/").map(Number);
                                if (totalProgress) pct = Math.round((done / totalProgress) * 95);
                                msg = `Subiendo ${done} de ${totalProgress}: ${fileName || ""}`;
                            }
                            setPnl(msg, pct);
                            setTimeout(poll, 1500);
                        }
                    } catch (err) {
                        console.warn("[DrvUpload infcom poll]", err);
                        setTimeout(poll, 2500);
                    }
                };
                setTimeout(poll, 1000);
            } else {
                panel.classList.add("drv_up_success");
                panel.querySelector(".drv_up_title").textContent = "Listo";
                setPnl(`${items.length} elemento(s) subido(s) correctamente`, 100);
                zone?.classList.add("drv_infcom_success");
                await this._drvRefreshInfcomView(zone);
                this._drvAutoHidePanel(panel);
            }
        } catch (err) {
            panel.classList.add("drv_up_error");
            panel.querySelector(".drv_up_title").textContent = "Error";
            panel.querySelector(".drv_up_msg").textContent =
                err.message?.slice(0, 120) || "No se pudo subir. Revisa la carpeta seleccionada.";
            console.error("[DrvUpload infcom]", err);
        }
    },

    async _drvRefreshInfcomView(zone) {
        try {
            if (this.model?.root?.load) {
                await this.model.root.load();
                this.model.notify();
            }
            setTimeout(() => this._drvBindInfcomDropZones(), 250);
            if (zone) {
                setTimeout(() => zone.classList.remove("drv_infcom_success"), 1800);
            }
        } catch (err) {
            console.warn("[DrvUpload infcom refresh]", err);
        }
    },

    // Actualiza el label visible correcto (modo archivo o carpeta)
    _drvSetActiveLabel(text) {
        const folderLbl = document.getElementById("drv_drop_label_folder");
        const fileLbl   = document.getElementById("drv_drop_label");
        const active = (folderLbl && folderLbl.style.display !== 'none') ? folderLbl : fileLbl;
        if (active) active.textContent = text;
    },

    // Obtiene o crea el panel flotante de progreso (reutiliza el de drv_main_drop)
    _drvGetPanel() {
        let panel = document.getElementById("drv_upload_panel");
        if (!panel) {
            panel = document.createElement("div");
            panel.id = "drv_upload_panel";
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
    },

    _drvAutoHidePanel(panel, delay = 3500) {
        if (!panel) return;
        const token = Date.now();
        panel.dataset.drvAutoHideToken = String(token);
        setTimeout(() => {
            if (panel.dataset.drvAutoHideToken !== String(token)) return;
            panel.classList.add("drv_up_hidden");
        }, delay);
    },

    // Feed a single file into the hidden Odoo binary field
    _drvFeedBinary(file) {
        const input = document.querySelector(".drv_file_input input[type='file']");
        if (!input) return;
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
        this._drvSetActiveLabel(`${file.name} — listo para subir`);
    },

    // Lee recursivamente un FileSystemEntry
    async _drvReadEntry(entry, base, out) {
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
                if (batch.length) hasChildren = true;
                for (const child of batch) await this._drvReadEntry(child, path, out);
            } while (batch.length);
            if (!hasChildren) {
                out.push({ path, empty_folder: true });
            }
        }
    },

    // Codifica y sube al servidor
    async _drvSendStructure(collected) {
        if (!collected.length) return;

        this._drvUploading = true;

        const zone      = document.getElementById("drv_drop_zone");
        const submitBtn = document.querySelector(".drv_btn_upload_submit");

        // Overlay en el dialog durante la fase de codificación
        document.getElementById("drv_upload_overlay")?.remove();
        const overlay = document.createElement("div");
        overlay.id = "drv_upload_overlay";
        overlay.style.cssText = [
            "position:absolute;inset:0;background:rgba(255,255,255,0.94);",
            "display:flex;flex-direction:column;align-items:center;justify-content:center;",
            "border-radius:8px;z-index:20;gap:12px;",
        ].join("");
        overlay.innerHTML = `
            <i class="fa fa-spinner fa-spin" style="font-size:38px;color:#2e6da4;"></i>
            <div id="drv_ol_main" style="font-size:14px;font-weight:700;color:#1e3a5f;text-align:center;padding:0 20px;"></div>
            <div id="drv_ol_sub"  style="font-size:12px;color:#555;text-align:center;padding:0 20px;"></div>`;
        if (zone) { zone.style.position = "relative"; zone.appendChild(overlay); }
        if (submitBtn) { submitBtn.disabled = true; submitBtn.style.opacity = "0.5"; }

        const setMsg = (main, sub = "") => {
            const m = document.getElementById("drv_ol_main");
            const s = document.getElementById("drv_ol_sub");
            if (m) m.textContent = main;
            if (s) s.textContent = sub;
        };

        const formData    = this.model?.root?.data;
        const browserId   = formData?.browser_id?.id   ?? formData?.browser_id?.[0]   ?? formData?.browser_id;
        const workspaceId = formData?.workspace_id?.id ?? formData?.workspace_id?.[0] ?? formData?.workspace_id;

        if (!browserId || !workspaceId) {
            this._drvSetActiveLabel("Error: no se pudo determinar la carpeta destino.");
            overlay.remove();
            if (submitBtn) { submitBtn.disabled = false; submitBtn.style.opacity = ""; }
            this._drvUploading = false;
            return;
        }

        // Fase 1: codificar archivos con progreso visible
        const items = [];
        for (let i = 0; i < collected.length; i++) {
            const { path, file } = collected[i];
            if (!file) {
                items.push({ path, empty_folder: true });
                continue;
            }
            const nombre = (file.name || path).split("/").pop().slice(0, 50);
            setMsg(`Preparando archivos… (${i + 1} de ${collected.length})`, nombre);
            const data = await new Promise(ok => {
                const fr = new FileReader();
                fr.onload = ev => ok(ev.target.result.split(",")[1]);
                fr.readAsDataURL(file);
            });
            items.push({ path, data });
        }

        setMsg(`Enviando ${collected.length} elemento(s) a Google Drive…`, "Conectando…");

        try {
            const uploadToken = Date.now();
            window.__drvUploadToken = uploadToken;
            if (window.__drvUploadPoll) {
                clearTimeout(window.__drvUploadPoll);
                window.__drvUploadPoll = null;
            }
            const actaComiteValue = this.model?.root?.data?.new_mcb_acta_comite_id;
            const actaComiteId = Array.isArray(actaComiteValue)
                ? actaComiteValue[0]
                : (actaComiteValue && typeof actaComiteValue === "object"
                    ? actaComiteValue.resId || actaComiteValue.id
                    : actaComiteValue);
            const actaComiteContextId =
                this.props?.context?.default_new_mcb_acta_comite_id ||
                this.model?.config?.context?.default_new_mcb_acta_comite_id ||
                this.model?.root?.context?.default_new_mcb_acta_comite_id;
            const uploadMode = this.model?.root?.data?.upload_mode || "";
            const actaComiteModeMatch = String(uploadMode).match(/new_mcb_acta_comite:(\d+)/);
            const actaComiteModeId = actaComiteModeMatch ? parseInt(actaComiteModeMatch[1], 10) : false;
            const finalActaComiteId = actaComiteId || actaComiteContextId || actaComiteModeId;
            const folderData = { workspace_id: workspaceId, items, upload_mode: uploadMode };
            if (finalActaComiteId) {
                folderData.new_mcb_acta_comite_id = finalActaComiteId;
            }

            const result = await this._drvOrm.call(
                "drive.browser",
                "action_upload_folder_structure",
                [[browserId]],
                { folder_data: folderData }
            );

            this._drvPendingItems = null;
            overlay.remove();

            if (result && result.background) {
                // Cerrar el dialog — la subida sigue en segundo plano
                document.querySelector(
                    ".o_dialog footer .btn-secondary, .o_dialog .modal-footer .btn-secondary"
                )?.click();

                // Panel flotante de progreso
                const panel  = this._drvGetPanel();
                const setPnl = (msg, pct = 0) => {
                    const el = panel.querySelector(".drv_up_msg");
                    const fill = panel.querySelector(".drv_up_progress_fill");
                    if (el)   el.textContent = msg;
                    if (fill) fill.style.width = `${Math.min(pct, 100)}%`;
                };
                setPnl(`Subiendo ${result.total} elemento(s)…`, 5);

                // Polling con fetch directo (no depende del componente OWL desmontado)
                const _bid   = browserId;
                const _total = result.total;
                const _self  = this;

                const _readState = () => fetch('/web/dataset/call_kw', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        jsonrpc: '2.0', method: 'call', id: Date.now(),
                        params: {
                            model: 'drive.browser', method: 'read',
                            args: [[_bid], ['sync_state', 'upload_progress']],
                            kwargs: { context: {} },
                        }
                    })
                }).then(r => r.json()).then(d => (d?.result || [])[0]);

                const poll = async () => {
                    if (window.__drvUploadToken !== uploadToken) return;
                    try {
                        const rec = await _readState();
                        if (!rec || rec.sync_state !== "sincronizando") {
                            // Completado
                            panel.classList.add("drv_up_success");
                            panel.querySelector(".drv_up_title").textContent = "Listo";
                            setPnl(`${_total} elemento(s) subido(s) correctamente`, 100);
                            setTimeout(() => panel.classList.add("drv_up_hidden"), 4000);
                            _self._drvUploading = false;
                            _self._drvDidUpload = true;
                            window.dispatchEvent(new CustomEvent("drv_upload_complete"));
                        } else {
                            const prog = rec.upload_progress || "";
                            let pct = 5, msg = "Subiendo en segundo plano…";
                            if (prog) {
                                const [counter, fileName] = prog.split("|");
                                const [done, total] = counter.split("/").map(Number);
                                if (total) pct = Math.round((done / total) * 95);
                                msg = `Subiendo ${done} de ${total}: ${fileName || ""}`;
                            }
                            setPnl(msg, pct);
                            window.__drvUploadPoll = setTimeout(poll, 1500);
                        }
                    } catch (_e) {
                        console.warn("[DrvUpload poll]", _e);
                        window.__drvUploadPoll = setTimeout(poll, 3000);
                    }
                };
                window.__drvUploadPoll = setTimeout(poll, 1500);

            } else {
                // Subida síncrona completada (sin background)
                this._drvUploading = false;
                this._drvDidUpload = true;
                this._drvSetActiveLabel(`✓ ${collected.length} archivo(s) subido(s) correctamente`);
                zone?.classList.add("drv_drop_success");
                window.dispatchEvent(new CustomEvent("drv_upload_complete"));
                setTimeout(() => {
                    document.querySelector(
                        ".o_dialog footer .btn-secondary, .o_dialog .modal-footer .btn-secondary"
                    )?.click();
                }, 1200);
            }

        } catch (err) {
            overlay.remove();
            this._drvUploading = false;
            const panel = this._drvGetPanel();
            panel.classList.add("drv_up_error");
            panel.querySelector(".drv_up_title").textContent = "Error";
            panel.querySelector(".drv_up_msg").textContent =
                err.message?.slice(0, 100) || "Error al subir. Revisa la consola.";
            if (submitBtn) { submitBtn.disabled = false; submitBtn.style.opacity = ""; }
            console.error("[DrvUpload]", err);
        }
    },
});
