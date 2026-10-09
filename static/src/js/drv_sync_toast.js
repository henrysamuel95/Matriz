/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillUnmount, onMounted } from "@odoo/owl";

patch(FormController.prototype, "Matriz.DrvSyncToast", {
    setup() {
        this._super(...arguments);
        this._drvToastOrm = useService("orm");
        this._drvToastMounted = true;
        this._drvToastPoll = null;

        onWillUnmount(() => {
            this._drvToastMounted = false;
            if (this._drvToastPoll) {
                clearInterval(this._drvToastPoll);
                this._drvToastPoll = null;
            }
            const $ = window.$;
            if ($) $('#drv_sync_toast').remove();
        });

        onMounted(async () => {
            const resModel = this.props.resModel || this.model?.config?.resModel;
            if (resModel !== 'drive.browser') return;

            const resId = this.model?.root?.resId;
            if (!resId || this.model.root.isNew) return;

            try {
                const data = await this._drvToastOrm.read(
                    "drive.browser", [resId], ["sync_state"]
                );
                if (data[0]?.sync_state === 'sincronizando') {
                    this._showDrvToast();
                    this._startDrvPolling(resId);
                }
            } catch (e) {
                console.error("[DrvSync] Error leyendo estado:", e);
            }
        });
    },

    _showDrvToast() {
        if (!this._drvToastMounted) return;
        const $ = window.$;
        if ($('#drv_sync_toast').length === 0) {
            $('body').append(`
                <div id="drv_sync_toast" style="
                    position: fixed; bottom: 20px; right: 20px; width: 310px;
                    background: white; border-radius: 8px;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.2); z-index: 9999;
                    border-left: 5px solid #2196F3; font-family: Roboto, sans-serif;
                    display: none;">
                    <div style="padding: 12px 15px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong id="drv_toast_title" style="font-size:14px; color:#333;">Documentos del Proyecto</strong>
                            <i id="drv_toast_icon" class="fa fa-circle-o-notch fa-spin text-primary"></i>
                        </div>
                        <div id="drv_toast_msg" style="font-size:12px; color:#666; margin-top:4px;">
                            Sincronizando documentos del proyecto...
                        </div>
                        <div class="progress" style="height:4px; margin-top:10px; background:#f1f1f1;">
                            <div id="drv_toast_bar" class="progress-bar bg-primary progress-bar-striped progress-bar-animated" style="width:100%;"></div>
                        </div>
                    </div>
                </div>
            `);
        }
        $('#drv_sync_toast').fadeIn();
    },

    _startDrvPolling(resId) {
        this._drvToastPoll = setInterval(async () => {
            if (!this._drvToastMounted) {
                clearInterval(this._drvToastPoll);
                return;
            }
            try {
                const data = await this._drvToastOrm.silent.read(
                    "drive.browser", [resId], ["sync_state"]
                );
                if (data[0]?.sync_state === 'completado') {
                    clearInterval(this._drvToastPoll);
                    this._drvToastPoll = null;
                    await this._onDrvSyncComplete();
                }
            } catch (e) {
                console.error("[DrvSync] Error en polling:", e);
                clearInterval(this._drvToastPoll);
                this._drvToastPoll = null;
                this._updateDrvToastError("Error al sincronizar documentos");
            }
        }, 2500);
    },

    async _onDrvSyncComplete() {
        if (!this._drvToastMounted) return;
        const $ = window.$;
        $('#drv_sync_toast').css('border-left-color', '#4CAF50');
        $('#drv_toast_title').text('Sincronización completa');
        $('#drv_toast_msg').text('Los documentos están actualizados.');
        $('#drv_toast_icon').removeClass().addClass('fa fa-check text-success');
        $('#drv_toast_bar').removeClass('bg-primary progress-bar-striped progress-bar-animated').addClass('bg-success').css('width', '100%');

        setTimeout(() => {
            if (this._drvToastMounted) {
                $('#drv_sync_toast').fadeOut(400, () => $('#drv_sync_toast').remove());
            }
        }, 2000);
    },

    _updateDrvToastError(msg) {
        if (!this._drvToastMounted) return;
        const $ = window.$;
        $('#drv_sync_toast').css('border-left-color', '#FF9800');
        $('#drv_toast_title').text('Aviso');
        $('#drv_toast_msg').text(msg);
        $('#drv_toast_icon').removeClass().addClass('fa fa-exclamation-triangle text-warning');
        $('#drv_toast_bar').removeClass('bg-primary progress-bar-striped progress-bar-animated').addClass('bg-warning').css('width', '100%');
        setTimeout(() => {
            if (this._drvToastMounted) $('#drv_sync_toast').fadeOut(400, () => $('#drv_sync_toast').remove());
        }, 5000);
    },
});
