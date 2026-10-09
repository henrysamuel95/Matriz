/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

// ── Floating progress panel ───────────────────────────────────────────────────

function acGetPanel() {
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
    return panel;
}

function acPanelShow(msg, progress = 0) {
    const panel = acGetPanel();
    panel.classList.remove("drv_up_hidden", "drv_up_success", "drv_up_error");
    panel.querySelector(".drv_up_title").textContent = "Subiendo archivos";
    panel.querySelector(".drv_up_msg").textContent = msg;
    panel.querySelector(".drv_up_progress_fill").style.width = `${progress}%`;
}

function acPanelSuccess(msg) {
    const panel = acGetPanel();
    panel.classList.add("drv_up_success");
    panel.querySelector(".drv_up_title").textContent = "Listo";
    panel.querySelector(".drv_up_msg").textContent = msg;
    panel.querySelector(".drv_up_progress_fill").style.width = "100%";
    setTimeout(() => panel.classList.add("drv_up_hidden"), 3000);
}

function acPanelError(msg) {
    const panel = acGetPanel();
    panel.classList.add("drv_up_error");
    panel.querySelector(".drv_up_title").textContent = "Error";
    panel.querySelector(".drv_up_msg").textContent = msg;
}

// ── AC Workspace Tree: expand / collapse ─────────────────────────────────────

const acExpandedKeys = new Set();
let _acPreventAutoExpand = null;

function acGetDepth(row) {
    const cell = row.querySelector("td[name='depth']");
    if (!cell) return 0;
    const inner = cell.querySelector("span, div");
    const text = (inner || cell).textContent.trim();
    const n = parseInt(text, 10);
    return isNaN(n) ? 0 : n;
}

function acRowKey(row) {
    const id = row.getAttribute("data-id");
    if (id) return "ac::" + id;
    const cell = row.querySelector("td[name='name_indented']");
    const name = (cell ? cell.textContent : "").replace(/\xa0/g, "").trim();
    return "ac::" + acGetDepth(row) + "::" + name;
}

function acIsSelected(row) {
    const openBtn = row.querySelector("button.gd_ws_open_btn.is_current");
    if (openBtn && !openBtn.classList.contains("o_invisible_modifier")) return true;
    return row.classList.contains("o_decoration_bf") || row.classList.contains("o_decoration_info");
}

function acIsVisibleRow(rows, row) {
    let depth = row._ac.depth;
    const idx  = row._ac.index;
    for (let i = idx - 1; i >= 0; i--) {
        if (rows[i]._ac.depth === depth - 1) {
            if (!acExpandedKeys.has(rows[i]._ac.key)) return false;
            depth--;
            if (depth <= 0) break;
        }
    }
    return true;
}

function applyAcVisibility(rows) {
    rows.forEach(row => {
        const { depth, key } = row._ac;
        row.style.display = (depth === 0 || acIsVisibleRow(rows, row)) ? "" : "none";
        const t = row.querySelector(".ac_ws_toggle:not(.ac_ws_toggle_empty)");
        if (t) t.style.transform = acExpandedKeys.has(key) ? "rotate(90deg)" : "rotate(0deg)";
    });
}

function buildAcTree(container) {
    const rows = Array.from(container.querySelectorAll("tbody tr.o_data_row"));
    if (!rows.length) return;

    rows.forEach((row, i) => {
        row._ac = {
            depth:       acGetDepth(row),
            index:       i,
            key:         acRowKey(row),
            hasChildren: false,
            selected:    acIsSelected(row),
        };
    });
    rows.forEach((row, i) => {
        const next = rows[i + 1];
        row._ac.hasChildren = !!(next && next._ac.depth > row._ac.depth);
    });

    // ── Update toggles in-place; only CREATE if missing (prevents flash) ──────
    rows.forEach(row => {
        row.style.display = "";
        const { hasChildren, key } = row._ac;

        const iconCell = Array.from(row.querySelectorAll("td")).find(
            td => td.querySelector("button.gd_ws_open_btn")
        );
        if (!iconCell) return;

        let t = iconCell.querySelector(".ac_ws_toggle");
        if (!t) {
            // First time: create the span and attach listeners once
            t = document.createElement("span");
            t.innerHTML = "&#9654;";
            t.style.cssText =
                "margin-right:6px;display:inline-block;transition:transform .2s;font-size:10px;";

            t.addEventListener("click", e => {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
            });
            t.addEventListener("mousedown", e => {
                e.preventDefault();
                e.stopPropagation();
                const theKey = t.getAttribute("data-ac-key");
                if (!theKey) return;
                if (acExpandedKeys.has(theKey)) {
                    acExpandedKeys.delete(theKey);
                    _acPreventAutoExpand = theKey;
                } else {
                    acExpandedKeys.add(theKey);
                    if (_acPreventAutoExpand === theKey) _acPreventAutoExpand = null;
                }
                const cont = t.closest(".ac_workspace_tree");
                if (cont) buildAcTree(cont);
            });

            iconCell.insertBefore(t, iconCell.firstChild);
        }

        // Always update state (works for both new and existing toggles)
        t.className = "ac_ws_toggle" + (hasChildren ? "" : " ac_ws_toggle_empty");
        t.setAttribute("data-ac-key", key);
        t.style.cursor = hasChildren ? "pointer" : "default";
        t.style.color  = hasChildren ? "#555"    : "transparent";
    });

    // Auto-expand ancestors of the selected folder
    const selIdx = rows.findIndex(r => r._ac.selected);
    if (selIdx >= 0) {
        const sel = rows[selIdx];
        if (sel._ac.hasChildren && sel._ac.key !== _acPreventAutoExpand)
            acExpandedKeys.add(sel._ac.key);
        let need = sel._ac.depth - 1;
        for (let i = selIdx - 1; i >= 0 && need >= 0; i--) {
            if (rows[i]._ac.depth === need && rows[i]._ac.hasChildren) {
                acExpandedKeys.add(rows[i]._ac.key);
                need--;
            }
        }
    }

    applyAcVisibility(rows);
}

// ── FormController patch ──────────────────────────────────────────────────────

patch(FormController.prototype, "Matriz.ArchivoCentralDrop", {
    setup() {
        this._super(...arguments);
        this._acOrm    = useService("orm");
        this._acNotif  = useService("notification");
        this._acZoneListeners = [];
        this._acDragCounter  = {};

        const isAC = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === "archivo.central";
        };

        this._acUploadCompleteHandler = async () => {
            try {
                await this.model.root.load();
                this.model.notify();
            } catch (_) {}
        };

        // AC tree row click — navigate to selected workspace (replaces drv_workspace_tree.js handler)
        this._acTreeRowClick = (e) => {
            if (e.target.closest(".ac_ws_toggle")) return;
            if (e.target.closest("button.gd_ws_open_btn")) return;
            const cell = e.target.closest(".ac_workspace_tree td");
            if (!cell) return;
            const row = cell.closest("tr");
            if (!row) return;
            const btn = row.querySelector(
                "button.gd_ws_open_btn:not(.o_invisible_modifier):not([style*='display: none'])"
            );
            if (btn) {
                // Stop the original click from reaching OWL's ListRenderer
                // (prevents row-selection re-render which causes toggle flicker)
                e.stopPropagation();
                e.stopImmediatePropagation();
                btn.click();
            }
        };

        // breadcrumb link click — navigate to ancestor workspace without page reload
        this._acBreadcrumbClick = async (e) => {
            const link = e.target.closest(".gd_breadcrumb_link");
            if (!link) return;
            e.preventDefault();
            e.stopPropagation();
            const wsId = parseInt(link.getAttribute("data-ws-id"), 10);
            if (!wsId) return;
            try {
                await this._acOrm.call(
                    "archivo.central.workspace",
                    "action_select_workspace",
                    [[wsId]]
                );
                await this.model.root.load();
                this.model.notify();
            } catch (err) {
                console.error("[GD Breadcrumb] navigation error:", err);
            }
        };

        // ── client-side search filters ────────────────────────────────────────
        this._acSearchObserver = null;
        let _sidebarQ = "";
        let _contentQ = "";
        let _debounceTimer = null;

        const _applyContent = (q) => {
            document.querySelectorAll(".gd_folder_card").forEach(card => {
                const text = (card.querySelector(".gd_folder_name")?.textContent || "").toLowerCase();
                const wrap = card.closest(".o_kanban_record");
                if (wrap) wrap.style.display = (!q || text.includes(q)) ? "" : "none";
            });
            document.querySelectorAll(".gd_file_card").forEach(card => {
                const text = (card.querySelector(".gd_file_name")?.textContent || "").toLowerCase();
                const wrap = card.closest(".o_kanban_record");
                if (wrap) wrap.style.display = (!q || text.includes(q)) ? "" : "none";
            });
        };

        const _applySidebar = (q) => {
            document.querySelectorAll(".ac_workspace_tree .o_data_row").forEach(row => {
                const raw = row.querySelector("[name='name_indented']")?.textContent || "";
                const text = raw.replace(/\xa0/g, "").toLowerCase();
                row.style.display = (!q || text.includes(q)) ? "" : "none";
            });
        };

        const _applyTreeIndent = () => {
            const rows = document.querySelectorAll(".ac_workspace_tree .o_data_row");
            if (!rows.length) return;
            rows.forEach(row => {
                // ocultar columna depth y leer su valor
                const depthCell = row.querySelector("td[name='depth']");
                if (depthCell) {
                    depthCell.style.cssText = "width:0!important;max-width:0!important;padding:0!important;overflow:hidden!important;opacity:0!important;";
                    const depth = parseInt(depthCell.textContent?.trim() || "0", 10);
                    if (depth > 0) {
                        const nameCell = row.querySelector("td[name='name']");
                        if (nameCell) nameCell.style.paddingLeft = `${depth * 20}px`;
                    }
                }
            });
        };

        // re-apply using stored query values (not reading DOM input, which may be recreated)
        this._acReapplyFilters = () => {
            if (_sidebarQ) _applySidebar(_sidebarQ);
            if (_contentQ) _applyContent(_contentQ);
            _applyTreeIndent();
        };

        this._acSearchInput = null; // replaced by polling

        onPatched(() => {
            if (!isAC()) return;
            const acTree = document.querySelector(".gd_browser_form .ac_workspace_tree");
            if (acTree) buildAcTree(acTree);
        });

        onMounted(() => {
            if (!isAC()) return;
            window.addEventListener("ac_upload_complete", this._acUploadCompleteHandler);
            document.addEventListener("click", this._acBreadcrumbClick, true);
            document.addEventListener("click", this._acTreeRowClick, true);

            // Poll search inputs every 200ms
            let _contentDebounce = null;
            this._acSearchPoll = setInterval(() => {
                const newSQ = (document.getElementById("ac_sidebar_search")?.value || "").toLowerCase().trim();
                if (newSQ !== _sidebarQ) { _sidebarQ = newSQ; _applySidebar(_sidebarQ); }

                const newCQ = (document.getElementById("ac_content_search")?.value || "").trim();
                if (newCQ !== _contentQ) {
                    _contentQ = newCQ;
                    clearTimeout(_contentDebounce);
                    _contentDebounce = setTimeout(async () => {
                        const browserId = this.model.root.resId;
                        if (!browserId) return;
                        try {
                            await this._acOrm.call("archivo.central", "write",
                                [[browserId], { search_query: _contentQ }]);
                            await this.model.root.load();
                            this.model.notify();
                        } catch (_) {}
                    }, 350);
                }
            }, 200);

            // Clear any persisted search_query from previous session
            const browserId = this.model.root.resId;
            if (browserId) {
                this._acOrm.call("archivo.central", "write",
                    [[browserId], { search_query: "" }]).catch(() => {});
            }

            setTimeout(() => {
                _applyTreeIndent();
                const acTree = document.querySelector(".gd_browser_form .ac_workspace_tree");
                if (acTree) buildAcTree(acTree);

                // re-apply filter + indent whenever DOM changes
                const watchTarget = document.querySelector(".gd_layout_container") || document.body;
                if (watchTarget) {
                    this._acSearchObserver = new MutationObserver(() => {
                        clearTimeout(_debounceTimer);
                        _debounceTimer = setTimeout(() => this._acReapplyFilters(), 150);
                    });
                    this._acSearchObserver.observe(watchTarget, { childList: true, subtree: true });
                }

                document.querySelectorAll(".drv_main_zone").forEach(zone => {
                    const zoneKey = zone.classList.contains("drv_folders_zone") ? "folder" : "file";
                    this._acDragCounter[zoneKey] = 0;

                    const onDragOver  = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; };
                    const onDragEnter = (e) => {
                        e.preventDefault();
                        this._acDragCounter[zoneKey] = (this._acDragCounter[zoneKey] || 0) + 1;
                        zone.classList.add("drv_drag_over");
                    };
                    const onDragLeave = () => {
                        this._acDragCounter[zoneKey] = Math.max(0, (this._acDragCounter[zoneKey] || 1) - 1);
                        if (this._acDragCounter[zoneKey] === 0) zone.classList.remove("drv_drag_over");
                    };
                    const onDrop = async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        this._acDragCounter[zoneKey] = 0;
                        zone.classList.remove("drv_drag_over");
                        await this._acHandleDrop(e, zoneKey);
                    };

                    zone.addEventListener("dragover",  onDragOver);
                    zone.addEventListener("dragenter", onDragEnter);
                    zone.addEventListener("dragleave", onDragLeave);
                    zone.addEventListener("drop",      onDrop);
                    this._acZoneListeners.push({ zone, handlers: { dragover: onDragOver, dragenter: onDragEnter, dragleave: onDragLeave, drop: onDrop } });
                });
            }, 600);
        });

        onWillUnmount(() => {
            window.removeEventListener("ac_upload_complete", this._acUploadCompleteHandler);
            document.removeEventListener("click", this._acBreadcrumbClick, true);
            document.removeEventListener("click", this._acTreeRowClick, true);
            clearInterval(this._acSearchPoll);
            this._acSearchObserver?.disconnect();
            this._acSearchObserver = null;
            this._acZoneListeners.forEach(({ zone, handlers }) =>
                Object.entries(handlers).forEach(([ev, fn]) => zone.removeEventListener(ev, fn))
            );
            this._acZoneListeners = [];
        });
    },

    _acGetM2oId(val) {
        if (!val) return null;
        if (Array.isArray(val)) return val[0] || null;
        return val.id || null;
    },

    async _acHandleDrop(e, zoneType) {
        const formData    = this.model?.root?.data;
        const browserId   = this.model?.root?.resId || this.props.resId;
        const workspaceId = this._acGetM2oId(formData?.selected_workspace_id);

        if (!browserId) {
            this._acNotif.add("No se pudo determinar el registro. Recarga la página.", { type: "danger" });
            return;
        }
        if (!workspaceId) {
            this._acNotif.add("Selecciona una carpeta antes de arrastrar.", { type: "warning", title: "Sin carpeta destino" });
            return;
        }

        const dtItems   = Array.from(e.dataTransfer.items || []);
        const fsEntries = dtItems.map(i => i.webkitGetAsEntry?.()).filter(Boolean);
        const hasDir    = fsEntries.some(en => en.isDirectory);

        if (zoneType === "file" && hasDir) {
            this._acNotif.add("Para subir carpetas, arrástralas a la sección 'Carpetas'.", { type: "warning" });
            return;
        }
        if (zoneType === "folder" && !hasDir) {
            this._acNotif.add("Para subir documentos, arrástralos a la sección 'Documentos'.", { type: "warning" });
            return;
        }

        acPanelShow("Preparando archivos…", 5);

        const collected = [];
        try {
            if (fsEntries.length) {
                for (const entry of fsEntries) await this._acReadEntry(entry, "", collected);
            } else {
                for (const file of Array.from(e.dataTransfer.files || [])) collected.push({ path: file.name, file });
            }
        } catch (readErr) {
            acPanelError("Error leyendo archivos: " + readErr.message);
            return;
        }

        if (!collected.length) { acPanelError("No se encontraron archivos."); return; }

        acPanelShow(`Codificando ${collected.length} elemento(s)…`, 20);

        const payload = [];
        for (let i = 0; i < collected.length; i++) {
            const { path, file } = collected[i];
            if (!file) {
                payload.push({ path, empty_folder: true });
                acPanelShow(`Preparando ${i + 1}/${collected.length}…`, 20 + Math.round(40 * (i + 1) / collected.length));
                continue;
            }
            const data = await new Promise(ok => {
                const fr = new FileReader();
                fr.onload = ev => ok(ev.target.result.split(",")[1]);
                fr.readAsDataURL(file);
            });
            payload.push({ path, data });
            acPanelShow(`Codificando ${i + 1}/${collected.length}…`, 20 + Math.round(40 * (i + 1) / collected.length));
        }

        acPanelShow(`Subiendo ${collected.length} elemento(s)…`, 65);

        try {
            const uploadToken = Date.now();
            window.__acMainUploadToken = uploadToken;
            if (window.__acMainUploadPoll) {
                clearTimeout(window.__acMainUploadPoll);
                window.__acMainUploadPoll = null;
            }
            const result = await this._acOrm.call(
                "archivo.central",
                "action_upload_folder_structure",
                [[browserId]],
                { folder_data: { workspace_id: workspaceId, items: payload } }
            );

            if (result && result.background) {
                acPanelShow(`Subiendo ${result.total} elemento(s) en segundo plano…`, 70);
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
                    if (window.__acMainUploadToken !== uploadToken) return;
                    try {
                        const rec = await readState();
                        if (!rec || rec.sync_state !== "sincronizando") {
                            acPanelSuccess(`${result.total} elemento(s) subido(s) correctamente`);
                            setTimeout(() => window.dispatchEvent(new CustomEvent("ac_upload_complete")), 500);
                            return;
                        }
                        const prog = rec.upload_progress || "";
                        if (prog) {
                            const [counter, fileName] = prog.split("|");
                            const [done, total] = counter.split("/").map(Number);
                            const pct = total ? Math.round(70 + 28 * done / total) : 70;
                            acPanelShow(`Subiendo ${done} de ${total}: ${fileName || ""}`, pct);
                        }
                        window.__acMainUploadPoll = setTimeout(poll, 1500);
                    } catch (pollError) {
                        console.warn("[ArchivoCentralDrop poll]", pollError);
                        window.__acMainUploadPoll = setTimeout(poll, 3000);
                    }
                };
                window.__acMainUploadPoll = setTimeout(poll, 1500);
                return;
            }

            acPanelSuccess(`${collected.length} elemento(s) subido(s) correctamente`);
            setTimeout(() => window.dispatchEvent(new CustomEvent("ac_upload_complete")), 1800);
        } catch (err) {
            acPanelError("Error al subir. Revisa la consola.");
            console.error("[ArchivoCentralDrop]", err);
        }
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
});
