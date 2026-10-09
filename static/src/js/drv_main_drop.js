/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

// ── Floating progress panel (Google Drive style) ─────────────────────────────

function drvGetPanel() {
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
    return panel;
}

function drvPanelShow(msg, progress = 0) {
    const panel = drvGetPanel();
    panel.classList.remove("drv_up_hidden", "drv_up_success", "drv_up_error");
    panel.querySelector(".drv_up_title").textContent = "Subiendo archivos";
    panel.querySelector(".drv_up_msg").textContent = msg;
    panel.querySelector(".drv_up_progress_fill").style.width = `${progress}%`;
}

function drvPanelSuccess(msg) {
    const panel = drvGetPanel();
    panel.classList.add("drv_up_success");
    panel.querySelector(".drv_up_title").textContent = "Listo";
    panel.querySelector(".drv_up_msg").textContent = msg;
    panel.querySelector(".drv_up_progress_fill").style.width = "100%";
    // Auto-ocultar después de 3 segundos
    setTimeout(() => panel.classList.add("drv_up_hidden"), 3000);
}

function drvPanelError(msg) {
    const panel = drvGetPanel();
    panel.classList.add("drv_up_error");
    panel.querySelector(".drv_up_title").textContent = "Error";
    panel.querySelector(".drv_up_msg").textContent = msg;
}

// ── FormController patch ──────────────────────────────────────────────────────

patch(FormController.prototype, "Matriz.DrvMainDrop", {
    setup() {
        this._super(...arguments);
        this._drvMainOrm   = useService("orm");
        this._drvMainNotif = useService("notification");
        this._drvZoneListeners = [];
        this._drvDragCounter   = {};

        const isMainBrowser = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === "drive.browser";
        };

        // Escucha el evento que dispara el wizard cuando termina de subir
        this._drvUploadCompleteHandler = async () => {
            try {
                const browserId = this.model?.root?.resId || this.props.resId;
                if (browserId) {
                    await this._drvMainOrm.call(
                        "drive.browser",
                        "action_after_upload_refresh",
                        [[browserId]]
                    );
                }
                await this.model.root.load();
                this.model.notify();
            } catch (err) {
                console.warn("[DrvMainDrop] refresh after upload failed", err);
            }
        };

        this._drvBreadcrumbClickHandler = async (e) => {
            if (!isMainBrowser()) return;
            const link = e.target.closest(".gd_breadcrumb_link");
            if (!link) return;
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            const wsId = parseInt(link.getAttribute("data-ws-id"), 10);
            if (!wsId) return;
            try {
                await this._drvMainOrm.call(
                    "drive.browser.workspace",
                    "action_select_workspace",
                    [[wsId]]
                );
                await this.model.root.load();
                this.model.notify();
            } catch (err) {
                console.error("[DrvMainDrop] breadcrumb navigation error", err);
            }
        };

        onMounted(() => {
            if (!isMainBrowser()) return;
            window.addEventListener('drv_upload_complete', this._drvUploadCompleteHandler);
            document.addEventListener("click", this._drvBreadcrumbClickHandler, true);
            setTimeout(() => {
                document.querySelectorAll(".drv_main_zone").forEach(zone => {
                    const zoneKey = zone.classList.contains("drv_folders_zone") ? "folder" : "file";
                    this._drvDragCounter[zoneKey] = 0;

                    const _isWsLocked = () => {
                        const ind = document.getElementById('drv_ws_locked_indicator');
                        return ind && !ind.classList.contains('o_invisible_modifier');
                    };
                    const onDragOver  = (e) => {
                        if (_isWsLocked()) return;
                        e.preventDefault();
                        e.dataTransfer.dropEffect = "copy";
                    };
                    const onDragEnter = (e) => {
                        if (_isWsLocked()) return;
                        e.preventDefault();
                        this._drvDragCounter[zoneKey] = (this._drvDragCounter[zoneKey] || 0) + 1;
                        zone.classList.add("drv_drag_over");
                    };
                    const onDragLeave = (e) => {
                        this._drvDragCounter[zoneKey] = Math.max(0, (this._drvDragCounter[zoneKey] || 1) - 1);
                        if (this._drvDragCounter[zoneKey] === 0) zone.classList.remove("drv_drag_over");
                    };
                    const onDrop = async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        this._drvDragCounter[zoneKey] = 0;
                        zone.classList.remove("drv_drag_over");
                        await this._drvMainHandleDrop(e, zoneKey);
                    };

                    zone.addEventListener("dragover",  onDragOver);
                    zone.addEventListener("dragenter", onDragEnter);
                    zone.addEventListener("dragleave", onDragLeave);
                    zone.addEventListener("drop",      onDrop);
                    this._drvZoneListeners.push({ zone, handlers: { dragover: onDragOver, dragenter: onDragEnter, dragleave: onDragLeave, drop: onDrop } });
                });
            }, 300);
        });

        onWillUnmount(() => {
            window.removeEventListener('drv_upload_complete', this._drvUploadCompleteHandler);
            document.removeEventListener("click", this._drvBreadcrumbClickHandler, true);
            this._drvZoneListeners.forEach(({ zone, handlers }) =>
                Object.entries(handlers).forEach(([ev, fn]) => zone.removeEventListener(ev, fn))
            );
            this._drvZoneListeners = [];
        });
    },

    // resolve Many2one value regardless of whether it's [id,name] or {id,display_name}
    _drvGetM2oId(val) {
        if (!val) return null;
        if (Array.isArray(val)) return val[0] || null;
        return val.id || null;
    },

    async _drvMainHandleDrop(e, zoneType) {
        const formData    = this.model?.root?.data;
        // resId is the authoritative record ID in Odoo 17 OWL
        const browserId   = this.model?.root?.resId || this.props.resId;
        const workspaceId = this._drvGetM2oId(formData?.selected_workspace_id);

        console.log("[DrvMainDrop] browserId:", browserId, "| workspaceId:", workspaceId, "| ws raw:", formData?.selected_workspace_id);

        if (!browserId) {
            this._drvMainNotif.add("No se pudo determinar el registro Drive. Recarga la página.", { type: "danger" });
            return;
        }

        if (!workspaceId) {
            this._drvMainNotif.add(
                "Selecciona una carpeta en el árbol antes de arrastrar archivos.",
                { type: "warning", title: "Sin carpeta destino" }
            );
            return;
        }

        // Collect entries via FileSystem API, fallback to dataTransfer.files
        const dtItems   = Array.from(e.dataTransfer.items || []);
        const fsEntries = dtItems.map(i => i.webkitGetAsEntry?.()).filter(Boolean);
        const hasDir    = fsEntries.some(en => en.isDirectory);

        console.log("[DrvMainDrop] items:", dtItems.length, "| fsEntries:", fsEntries.length, "| hasDir:", hasDir);

        if (zoneType === "file" && hasDir) {
            this._drvMainNotif.add("Para subir carpetas, arrástralas a la sección 'Carpetas'.", { type: "warning" });
            return;
        }
        if (zoneType === "folder" && !hasDir) {
            this._drvMainNotif.add("Para subir documentos, arrástralos a la sección 'Archivos'.", { type: "warning" });
            return;
        }

        drvPanelShow("Preparando archivos…", 5);

        const collected = [];
        try {
            if (fsEntries.length) {
                for (const entry of fsEntries) {
                    await this._drvMainReadEntry(entry, "", collected);
                }
            } else {
                // fallback: plain File objects (no folder support)
                for (const file of Array.from(e.dataTransfer.files || [])) {
                    collected.push({ path: file.name, file });
                }
            }
        } catch (readErr) {
            drvPanelError("Error leyendo archivos: " + readErr.message);
            console.error("[DrvMainDrop] readEntry error:", readErr);
            return;
        }

        console.log("[DrvMainDrop] collected:", collected.length, collected.map(c => c.path));

        if (!collected.length) {
            drvPanelError("No se encontraron archivos para subir.");
            return;
        }

        drvPanelShow(`Codificando ${collected.length} elemento(s)…`, 20);

        const payload = [];
        for (let i = 0; i < collected.length; i++) {
            const { path, file } = collected[i];
            if (!file) {
                payload.push({ path, empty_folder: true });
                drvPanelShow(`Preparando ${i + 1}/${collected.length}…`, 20 + Math.round(40 * (i + 1) / collected.length));
                continue;
            }
            const data = await new Promise(ok => {
                const fr = new FileReader();
                fr.onload = ev => ok(ev.target.result.split(",")[1]);
                fr.readAsDataURL(file);
            });
            payload.push({ path, data });
            drvPanelShow(`Codificando ${i + 1}/${collected.length}…`, 20 + Math.round(40 * (i + 1) / collected.length));
        }

        drvPanelShow(`Subiendo ${collected.length} elemento(s) a Drive…`, 65);

        try {
            const uploadToken = Date.now();
            window.__drvMainUploadToken = uploadToken;
            if (window.__drvMainUploadPoll) {
                clearTimeout(window.__drvMainUploadPoll);
                window.__drvMainUploadPoll = null;
            }
            const result = await this._drvMainOrm.call(
                "drive.browser",
                "action_upload_folder_structure",
                [[browserId]],
                { folder_data: { workspace_id: workspaceId, items: payload } }
            );

            if (result && result.background) {
                // Subida en segundo plano: hacer polling hasta completar
                drvPanelShow(`Subiendo ${result.total} elemento(s) en Drive…`, 70);

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
                        },
                    }),
                }).then(r => r.json()).then(d => (d?.result || [])[0]);

                const poll = async () => {
                    if (window.__drvMainUploadToken !== uploadToken) return;
                    try {
                        const rec = await readState();
                        if (rec && rec.sync_state !== "sincronizando") {
                            drvPanelSuccess(`${result.total} elemento(s) subido(s) correctamente`);
                            setTimeout(() => {
                                window.dispatchEvent(new CustomEvent('drv_upload_complete'));
                            }, 500);
                        } else {
                            const prog = rec?.upload_progress;
                            if (prog) {
                                const [done, total] = prog.split("/").map(Number);
                                const pct = total ? Math.round(70 + 28 * done / total) : 70;
                                drvPanelShow(`Subiendo ${prog} archivos…`, pct);
                            }
                            window.__drvMainUploadPoll = setTimeout(poll, 3000);
                        }
                    } catch (_) {
                        window.__drvMainUploadPoll = setTimeout(poll, 3000);
                    }
                };
                window.__drvMainUploadPoll = setTimeout(poll, 3000);
            } else {
                drvPanelSuccess(`${collected.length} elemento(s) subido(s) correctamente`);
                setTimeout(() => {
                    window.dispatchEvent(new CustomEvent('drv_upload_complete'));
                }, 1800);
            }

        } catch (err) {
            drvPanelError("Error al subir. Revisa la consola.");
            console.error("[DrvMainDrop]", err);
        }
    },

    async _drvMainReadEntry(entry, base, out) {
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
                for (const child of batch) await this._drvMainReadEntry(child, path, out);
            } while (batch.length);
            if (!hasChildren) {
                out.push({ path, empty_folder: true });
            }
        }
    },
});
