/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillUnmount, onMounted } from "@odoo/owl";

patch(FormController.prototype, "Matriz.GDSyncToast", {
    setup() {
        this._super(...arguments);
        this._gdOrm = useService("orm");
        this._gdMounted = true;
        this._gdPollInterval = null;

        onWillUnmount(() => {
            this._gdMounted = false;
            if (this._gdPollInterval) {
                clearInterval(this._gdPollInterval);
                this._gdPollInterval = null;
            }
            const $ = window.$;
            if ($) $('#gd_sync_toast').remove();
        });

        onMounted(async () => {
            const resModel = this.props.resModel || (this.model?.config?.resModel);
            if (resModel !== 'gestion.documental.browser') return;

            const resId = this.model?.root?.resId;
            if (!resId || this.model.root.isNew) return;

            try {
                const data = await this._gdOrm.read(
                    "gestion.documental.browser", [resId], ["estado_sync_gd"]
                );
                if (data[0]?.estado_sync_gd === 'sincronizando') {
                    this._showGDToast();
                    this._startGDPolling(resId);
                }
            } catch (e) {
                console.error("[GDSync] Error leyendo estado:", e);
            }
        });
    },

    _showGDToast() {
        if (!this._gdMounted) return;
        const $ = window.$;
        if ($('#gd_sync_toast').length === 0) {
            $('body').append(`
                <div id="gd_sync_toast" style="
                    position: fixed; bottom: 20px; right: 20px; width: 310px;
                    background: white; border-radius: 8px;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.2); z-index: 9999;
                    border-left: 5px solid #2196F3; font-family: Roboto, sans-serif;
                    display: none;">
                    <div style="padding: 12px 15px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong id="gd_toast_title" style="font-size:14px; color:#333;">Gestión Documental</strong>
                            <i id="gd_toast_icon" class="fa fa-circle-o-notch fa-spin text-primary"></i>
                        </div>
                        <div id="gd_toast_msg" style="font-size:12px; color:#666; margin-top:4px;">
                            Sincronizando documentos con Drive...
                        </div>
                        <div class="progress" style="height:4px; margin-top:10px; background:#f1f1f1;">
                            <div id="gd_toast_bar" class="progress-bar bg-primary progress-bar-striped progress-bar-animated" style="width:100%;"></div>
                        </div>
                    </div>
                </div>
            `);
        }
        $('#gd_sync_toast').fadeIn();
    },

    _startGDPolling(resId) {
        this._gdPollInterval = setInterval(async () => {
            if (!this._gdMounted) {
                clearInterval(this._gdPollInterval);
                return;
            }
            try {
                const data = await this._gdOrm.silent.read(
                    "gestion.documental.browser", [resId], ["estado_sync_gd"]
                );
                if (data[0]?.estado_sync_gd === 'completado') {
                    clearInterval(this._gdPollInterval);
                    this._gdPollInterval = null;
                    await this._onGDSyncComplete();
                }
            } catch (e) {
                console.error("[GDSync] Error en polling:", e);
                clearInterval(this._gdPollInterval);
                this._gdPollInterval = null;
                this._updateGDToastError("Error al sincronizar documentos");
            }
        }, 2500);
    },

    async _onGDSyncComplete() {
        if (!this._gdMounted) return;
        const $ = window.$;
        $('#gd_sync_toast').css('border-left-color', '#4CAF50');
        $('#gd_toast_title').text('Sincronización completa');
        $('#gd_toast_msg').text('Los documentos están actualizados.');
        $('#gd_toast_icon').removeClass().addClass('fa fa-check text-success');
        $('#gd_toast_bar').removeClass('bg-primary progress-bar-striped progress-bar-animated').addClass('bg-success').css('width', '100%');

        // Cerrar toast y recargar (solo ocurre en primera sincronización)
        setTimeout(async () => {
            if (!this._gdMounted) return;
            $('#gd_sync_toast').fadeOut(400, () => $('#gd_sync_toast').remove());
            try {
                const resId = this.model?.root?.resId;
                if (resId) {
                    await this.env.services.action.doAction({
                        type: 'ir.actions.act_window',
                        res_model: 'gestion.documental.browser',
                        res_id: resId,
                        view_mode: 'form',
                        target: 'current',
                    }, { clearBreadcrumbs: true });
                }
            } catch (e) { /* silent */ }
        }, 1500);
    },

    _updateGDToastError(msg) {
        if (!this._gdMounted) return;
        const $ = window.$;
        $('#gd_sync_toast').css('border-left-color', '#FF9800');
        $('#gd_toast_title').text('Aviso');
        $('#gd_toast_msg').text(msg);
        $('#gd_toast_icon').removeClass().addClass('fa fa-exclamation-triangle text-warning');
        $('#gd_toast_bar').removeClass('bg-primary progress-bar-striped progress-bar-animated').addClass('bg-warning').css('width', '100%');
        setTimeout(() => {
            if (this._gdMounted) $('#gd_sync_toast').fadeOut(400, () => $('#gd_sync_toast').remove());
        }, 5000);
    },
});
