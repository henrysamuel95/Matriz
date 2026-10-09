
/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillUnmount, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";

// Cuando el servidor retorna la acción client 'drive.start_renumber',
// disparamos un evento de ventana para que el FormController lo maneje.
registry.category("actions").add("drive.start_renumber", function (env, action) {
    window.dispatchEvent(new CustomEvent("drive_start_renumber", {
        detail: { browser_id: action.params.browser_id }
    }));
    return false;
});

function _drvToast($, title, subtitle) {
    if ($('#drive_toast').length === 0) {
        $('body').append(`
            <div id="drive_toast" style="
                position:fixed;bottom:20px;right:20px;width:320px;
                background:white;border-radius:8px;
                box-shadow:0 4px 15px rgba(0,0,0,.2);z-index:9999;
                display:none;border-left:5px solid #2196F3;
                font-family:Roboto,sans-serif;">
              <div style="padding:12px 15px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                  <strong id="dt_title" style="font-size:14px;color:#333;">${title}</strong>
                  <i id="dt_icon" class="fa fa-circle-o-notch fa-spin text-primary"></i>
                </div>
                <div id="dt_subtitle" style="font-size:12px;color:#666;margin-top:4px;
                     white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${subtitle}</div>
                <div class="progress" style="height:4px;margin-top:10px;background:#f1f1f1;">
                  <div id="dt_bar"
                       class="progress-bar bg-primary progress-bar-striped progress-bar-animated"
                       style="width:100%;"></div>
                </div>
              </div>
            </div>`);
    } else {
        $('#dt_title').text(title);
        $('#dt_subtitle').text(subtitle);
        $('#dt_icon').attr('class', 'fa fa-circle-o-notch fa-spin text-primary');
        $('#dt_bar').css('width', '100%')
                   .removeClass('bg-success bg-danger')
                   .addClass('bg-primary progress-bar-striped progress-bar-animated');
        $('#drive_toast').css('border-left-color', '#2196F3');
    }
    $('#drive_toast').fadeIn();
}

patch(FormController.prototype, "Matriz.DriveRenumber", {
    setup() {
        this._super(...arguments);

        // orm puede estar ya seteado por DriveSync; si no, lo obtenemos aquí
        if (!this.orm) this.orm = useService("orm");

        this._drv_renumber_active = true;

        this._onStartRenumber = async (ev) => {
            const { browser_id } = ev.detail;
            const resId = this.model?.root?.resId;
            if (!this._drv_renumber_active || !resId || resId !== browser_id) return;

            const $ = window.$;
            _drvToast($, 'Numerando...', 'Aplicando numeración jerárquica en Drive...');

            try {
                const result = await this.orm.silent.call(
                    'drive.browser', 'do_renumber_bg', [[browser_id]]
                );
                const total = (result && result.total) || 0;

                // Éxito
                $('#drive_toast').css('border-left-color', '#4CAF50');
                $('#dt_title').text('Numeración completa');
                $('#dt_subtitle').text(`${total} ítem(s) renombrado(s).`);
                $('#dt_icon').attr('class', 'fa fa-check text-success');
                $('#dt_bar').removeClass('bg-primary progress-bar-striped progress-bar-animated')
                            .addClass('bg-success').css('width', '100%');

                if (this._drv_renumber_active) {
                    await this.model.root.load();
                    this.model.notify();
                }
                setTimeout(() => {
                    if (this._drv_renumber_active) $('#drive_toast').fadeOut();
                }, 4000);

            } catch (e) {
                console.error('[DriveRenumber] Error:', e);
                $('#drive_toast').css('border-left-color', '#f44336');
                $('#dt_title').text('Error al numerar');
                $('#dt_subtitle').text(e.message || 'Error desconocido');
                $('#dt_icon').attr('class', 'fa fa-times text-danger');
                $('#dt_bar').removeClass('bg-primary progress-bar-striped progress-bar-animated')
                            .addClass('bg-danger').css('width', '100%');
                setTimeout(() => {
                    if (this._drv_renumber_active) $('#drive_toast').fadeOut();
                }, 6000);
            }
        };

        onMounted(() => {
            window.addEventListener('drive_start_renumber', this._onStartRenumber);
        });

        onWillUnmount(() => {
            this._drv_renumber_active = false;
            window.removeEventListener('drive_start_renumber', this._onStartRenumber);
        });
    }
});
