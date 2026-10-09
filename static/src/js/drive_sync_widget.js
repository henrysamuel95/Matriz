/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillUnmount, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";

registry.category("actions").add("matriz.trigger_update_toast", function (env, action) {
    window.dispatchEvent(new CustomEvent("matriz_update_drive", { detail: action.params.record_id }));
    return { type: 'ir.actions.act_window_close' }; 
});

patch(FormController.prototype, "Matriz.DriveSync", {
    setup() {
        this._super(...arguments);
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.isComponentMounted = true;

        this._onUpdateDriveEvent = (ev) => {
            console.log("🔘 [EVENTO] Botón de actualización presionado.");
            const resId = this.model?.root?.resId;
            if (resId && resId === ev.detail) {
                this._startDriveUpdate(resId);
            }
        };

        onWillUnmount(() => {
            this.isComponentMounted = false;
            const $ = window.$;
            if ($) $('#drive_toast').fadeOut();
            window.removeEventListener("matriz_update_drive", this._onUpdateDriveEvent);
        });

        onMounted(async () => {
            window.addEventListener("matriz_update_drive", this._onUpdateDriveEvent);

            if (this.model?.root && !this.model.root.isNew) {
                const resId = this.model.root.resId;
                const resModel = this.props.resModel || (this.model.config?.resModel);

                if (resModel === 'contrataciones_fuv' && resId) {
                    try {
                        console.log("🔍 [MONTO] Verificando estado en Base de Datos...");
                        const data = await this.orm.read("contrataciones_fuv", [resId], ["estado_sincronizacion_drive"]);
                        const estadoBD = data[0]?.estado_sincronizacion_drive;
                        console.log("📊 [ESTADO BD] Actual:", estadoBD);

                        if (estadoBD === "pendiente") {
                            console.log("⚠️ [ALERTA] Estado Pendiente detectado. Iniciando rescate...");
                            await this._checkMissingFilesOnLoad(resId);
                        }
                    } catch (e) { console.error("❌ [ERROR] Al leer estado:", e); }
                }
            }
        });
    },

    async _checkAndSync(saved, isNewRecord) {
        console.log("💾 [GUARDADO] Evaluando _checkAndSync...");
        if (!saved || !isNewRecord || !this.isComponentMounted) return;

        try {
            const resModel = this.props.resModel || (this.model && this.model.config.resModel);
            if (resModel === 'contrataciones_fuv') {
                const resId = this.model.root.resId;
                if (resId) {
                    console.log("📝 [DB] Escribiendo estado a 'pendiente'...");
                    await this.orm.write("contrataciones_fuv", [resId], { estado_sincronizacion_drive: 'pendiente' });
                    
                    if (this.isComponentMounted) {
                        await this.model.root.load();
                        this.model.notify();
                    }
                    this._startDriveSync(resId, "Sincronizando...");
                }
            }
        } catch (e) { console.error("❌ [ERROR] DriveSync Error:", e); }
    },

    // --- EL CORAZÓN DEL RESCATE Y DETECTOR DE MENTIRAS ---
    async _checkMissingFilesOnLoad(recordId) {
        console.log("🛡️ [RESCATE] Entrando a _checkMissingFilesOnLoad...");
        try {
            if (!navigator.onLine) {
                console.warn("🌐 [RED] Navegador reporta SIN INTERNET. Abortando.");
                this._updateToastError("Esperando conexión...");
                return;
            }

            console.log("📡 [CONSULTA 1] Llamando js_get_files_list (Carga Inicial)...");
            let filesInit = await this.orm.silent.call("contrataciones_fuv", "js_get_files_list", [recordId]);
            console.log("📦 [RESPUESTA 1]:", filesInit);

            if (filesInit && filesInit.status === 'error') throw new Error(filesInit.message);

            if (filesInit && Array.isArray(filesInit) && filesInit.length > 0) {
                console.log("✅ [ACCIÓN] Hay archivos iniciales. Reanudando proceso...");
                this.notification.add(`Reanudando sincronización inicial...`, { type: "success" }); // Notificación verde
                this._startDriveSync(recordId, "Reanudando...", filesInit);
                return;
            }

            console.log("📡 [CONSULTA 2] No hay iniciales. Llamando js_get_all_files (Actualización)...");
            let filesUpdate = await this.orm.silent.call("contrataciones_fuv", "js_get_all_files", [recordId]);
            console.log("📦 [RESPUESTA 2]:", filesUpdate);

            if (filesUpdate && filesUpdate.status === 'error') throw new Error(filesUpdate.message);

            if (filesUpdate && Array.isArray(filesUpdate) && filesUpdate.length > 0) {
                console.log("✅ [ACCIÓN] Hay archivos de actualización. Reanudando proceso...");
                this.notification.add(`Retomando actualización...`, { type: "success" }); // Notificación verde
                this._showToast("Actualizando", `Procesando...`, false);
                this._processQueueUpdate(recordId, filesUpdate, 0);
                return;
            }

            // ==============================================================
            // 🛑 EL DETECTOR DE MENTIRAS VUELVE (Basado en la BD) 🛑
            // ==============================================================
            console.warn("🛡️ [DETECTOR] El registro está en PENDIENTE, pero Drive devolvió 0 archivos en ambas consultas.");
            console.warn("🛑 [BLOQUEO] Esto es un falso positivo por desconexión. ¡Mando a pausar y NO completo!");
            this._updateToastError("Fallo de Drive. Protegido en Pendiente.");
            return; // ¡NO PASA A COMPLETADO! SE QUEDA AQUÍ.

        } catch (e) { 
            console.error("❌ [ERROR CRÍTICO] En recuperación:", e);
            this._updateToastError("Proceso pausado. Esperando red.");
        }
    },

    async _startDriveSync(recordId, titleToast, preLoadedFiles = null) {
        console.log("🚀 [INICIO] _startDriveSync...");
        if (!this.isComponentMounted) return;

        try {
            let files = preLoadedFiles;
            if (!files) {
                console.log("📂 [DRIVE] Buscando o creando carpeta...");
                await this.orm.silent.call("contrataciones_fuv", "buscar_carpeta", [[recordId]]);
                if (!this.isComponentMounted) return;
                
                console.log("📡 [CONSULTA] Obteniendo lista de archivos...");
                files = await this.orm.silent.call("contrataciones_fuv", "js_get_files_list", [recordId]);
                console.log("📦 [RESPUESTA]:", files);
                if (files && files.status === 'error') throw new Error(files.message);
            }

            if (!files || files.length === 0) {
                console.warn("🛡️ [DETECTOR 2] Drive envió 0 archivos en la creación. Falso positivo detectado. Congelando.");
                this._updateToastError("Drive no responde. En pendiente.");
                return; // Bloqueado, no completa.
            }

            console.log(`▶️ [COLA] Procesando ${files.length} archivos...`);
            this._showToast(titleToast, `Procesando ${files.length} archivos...`);
            this._processQueue(recordId, files, 0);
        } catch (err) { 
            console.error("❌ [ERROR] _startDriveSync:", err);
            this._updateToastError("Conexión interrumpida."); 
        }
    },

    async _processQueue(recordId, files, index, retryCount = 0) {
        if (!this.isComponentMounted) return;
        
        if (index >= files.length) {
            console.log("🏁 [FIN] Todos los archivos procesados con éxito. Marcando completado.");
            try {
                await this.orm.silent.call("contrataciones_fuv", "assign_docs_all", [recordId]);
                await this.orm.write("contrataciones_fuv", [recordId], { estado_sincronizacion_drive: 'completado' });
                if (this.isComponentMounted) {
                    await this.model.root.load();
                    this.model.notify();
                    this._updateToastSuccess("Éxito", "Sincronización completa.");
                }
            } catch (e) { console.error("❌ [ERROR] Finalizando cola:", e); }
            return;
        }
        
        const file = files[index];
        this._updateToastProgress(index + 1, files.length, file.title);
        
        try {
            const resp = await this.orm.silent.call("contrataciones_fuv", "js_copy_single_file", [file]);
            if (resp && resp.status === 'error') throw new Error(resp.message || "Error en Drive"); 
            
            this._processQueue(recordId, files, index + 1, 0);
        } catch (e) {
            console.error(`⚠️ [FALLO] Archivo ${file.title}. Reintento ${retryCount}...`);
            if (retryCount < 3) {
                setTimeout(() => { if (this.isComponentMounted) this._processQueue(recordId, files, index, retryCount + 1); }, 2500);
            } else {
                console.error("❌ [ABORTO] Se agotaron reintentos.");
                this._updateToastError("Fallo de red sostenido.");
            }
        }
    },

    async _startDriveUpdate(recordId) {
        console.log("🔄 [INICIO ACTUALIZACIÓN] Botón de actualización pulsado.");
        if (!this.isComponentMounted) return;

        if (!navigator.onLine) {
            this.notification.add("Sin conexión a internet.", { type: "success" }); // Toast nativo color verde (usando success)
            return;
        }

        try {
            console.log("📝 [DB] Forzando estado 'pendiente'...");
            await this.orm.write("contrataciones_fuv", [recordId], { estado_sincronizacion_drive: 'pendiente' });
            
            if (this.isComponentMounted) {
                await this.model.root.load(); 
                this.model.notify();          
            }
            
            console.log("📡 [CONSULTA] Llamando js_get_all_files...");
            const files = await this.orm.silent.call("contrataciones_fuv", "js_get_all_files", [recordId]);
            console.log("📦 [RESPUESTA Drive]:", files);

            if (files && files.status === 'error') throw new Error(files.message);
            
            if (!files || files.length === 0) {
                console.log("✅ [LIMPIO] Realmente no hay archivos nuevos. Completando proceso legitimamente.");
                this._showToast("Sin cambios", "No hay archivos nuevos.", false);
                await this.orm.write("contrataciones_fuv", [recordId], { estado_sincronizacion_drive: 'completado' });
                
                if (this.isComponentMounted) {
                    await this.model.root.load();
                    this.model.notify();
                }
                return;
            }
            console.log(`▶️ [COLA] Procesando ${files.length} archivos de actualización...`);
            this._showToast("Actualizando", `Procesando ${files.length} archivos...`, false);
            this._processQueueUpdate(recordId, files, 0);
        } catch (err) { 
            console.error("❌ [ERROR] Al iniciar actualización:", err);
            this._updateToastError("Proceso pausado."); 
        }
    },

    async _processQueueUpdate(recordId, files, index, retryCount = 0) {
        if (!this.isComponentMounted) return;

        if (index >= files.length) {
            console.log("🏁 [FIN] Todos los archivos actualizados con éxito. Marcando completado.");
            try {
                await this.orm.silent.call("contrataciones_fuv", "js_finish_update", [recordId]);
                await this.orm.silent.call("contrataciones_fuv", "assign_docs_all", [recordId]);
                await this.orm.write("contrataciones_fuv", [recordId], { estado_sincronizacion_drive: 'completado' });
                if (this.isComponentMounted) {
                    await this.model.root.load();
                    this.model.notify();
                    this._updateToastSuccess("¡Éxito!", "Documentos actualizados.");
                }
            } catch (e) { console.error("❌ [ERROR] Finalizando actualización:", e); }
            return;
        }
        
        const file = files[index];
        this._updateToastProgress(index + 1, files.length, file.title);
        
        try {
            const resp = await this.orm.silent.call("contrataciones_fuv", "js_update_single_file", [file]);
            if (resp && resp.status === 'error') throw new Error(resp.message || "Error en Drive"); 
            
            this._processQueueUpdate(recordId, files, index + 1, 0);
        } catch (e) {
            console.error(`⚠️ [FALLO] Archivo ${file.title}. Reintento ${retryCount}...`);
            if (retryCount < 3) {
                setTimeout(() => { if (this.isComponentMounted) this._processQueueUpdate(recordId, files, index, retryCount + 1); }, 2500);
            } else {
                console.error("❌ [ABORTO] Se agotaron reintentos de actualización.");
                this._updateToastError("Fallo de red en actualización.");
            }
        }
    },


    _showToast(title, subtitle, has_spinner = true) {
        if (!this.isComponentMounted) return;
        const $ = window.$;
        if ($('#drive_toast').length === 0) {
            $('body').append(`
                <div id="drive_toast" style="position: fixed; bottom: 20px; right: 20px; width: 320px; background: white; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.2); z-index: 9999; display: none; border-left: 5px solid #2196F3; font-family: Roboto, sans-serif;">
                    <div style="padding: 12px 15px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong id="dt_title" style="font-size: 14px; color:#333;">${title}</strong>
                            ${has_spinner ? '<i id="dt_icon" class="fa fa-circle-o-notch fa-spin text-primary"></i>' : '<i id="dt_icon" class="fa fa-folder-open text-primary"></i>'}
                        </div>
                        <div id="dt_subtitle" style="font-size: 12px; color: #666; margin-top:4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${subtitle}</div>
                        <div class="progress" style="height: 4px; margin-top: 10px; background: #f1f1f1;">
                            <div id="dt_bar" class="progress-bar bg-primary" style="width: 0%; transition: width 0.3s;"></div>
                        </div>
                    </div>
                </div>
            `);
            $('#drive_toast').data('has_spinner', has_spinner);
        }
        $('#drive_toast').fadeIn();
        this._updateToast(title, subtitle);
    },

    _updateToast(title, subtitle) {
        if (!this.isComponentMounted) return;
        const $ = window.$;
        $('#dt_title').text(title);
        $('#dt_subtitle').text(subtitle);
    },

    _updateToastProgress(current, total, fileName) {
        if (!this.isComponentMounted) return;
        const $ = window.$;
        var percent = (current / total) * 100;
        $('#dt_title').text(`Copiando ${current} de ${total}`);
        $('#dt_subtitle').text(fileName);
        $('#dt_bar').css('width', percent + '%');
    },

    _updateToastSuccess(title, subtitle) {
        if (!this.isComponentMounted) return;
        const $ = window.$;
        $('#drive_toast').css('border-left-color', '#4CAF50');
        $('#dt_title').text(title);
        $('#dt_subtitle').text(subtitle);
        $('#dt_icon').removeClass().addClass('fa fa-check text-success');
        $('#dt_bar').addClass('bg-success').css('width', '100%');
        setTimeout(() => { if (this.isComponentMounted) $('#drive_toast').fadeOut(); }, 4000);
    },

    // --- EL TOAST DE ERROR AHORA ES VERDE 🟢 ---
    _updateToastError(msg) {
        if (!this.isComponentMounted) return;
        const $ = window.$;
        $('#drive_toast').css('border-left-color', '#4CAF50'); // Borde verde
        $('#dt_title').text("Aviso de Sistema"); // Título más amigable
        $('#dt_subtitle').text(msg); 
        $('#dt_icon').removeClass().addClass('fa fa-info-circle text-success'); // Ícono verde
        $('#dt_bar').removeClass('bg-primary bg-danger').addClass('bg-success'); // Barra verde
        setTimeout(() => { if (this.isComponentMounted) $('#drive_toast').fadeOut(); }, 8000);
    },

    async saveButtonClicked(params = {}) {
        const isNewRecord = this.model.root.isNew;
        const saved = await this._super(...arguments);
        if (this.isComponentMounted) await this._checkAndSync(saved, isNewRecord);
        return saved;
    },

    async save(record, params = {}) {
        const isNewRecord = this.model.root.isNew;
        const saved = await this._super(...arguments);
        if (this.isComponentMounted && (!params || !params.fromButton)) await this._checkAndSync(saved, isNewRecord);
        return saved;
    },
});