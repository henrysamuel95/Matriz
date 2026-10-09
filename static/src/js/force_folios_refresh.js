odoo.define('matriz.force_folios_refresh', function (require) {
    "use strict";

    const FormRenderer = require('web.FormRenderer');

    FormRenderer.include({

        _renderView() {
            const res = this._super.apply(this, arguments);

            // Solo para informes_drive
            if (this.state?.model !== 'informes_drive') {
                return res;
            }

            // Esperar a que el DOM esté estable
            setTimeout(() => {
                const btn = document.querySelector(
                    'button[name="action_select_workspace"].o_selected'
                ) || document.querySelector(
                    'button[name="action_select_workspace"]'
                );

                if (btn) {
                    console.log('[FOLIOS] Refresh workspace post-save');
                    btn.click();
                }
            }, 1200);

            return res;
        },

    });
});
