odoo.define('matriz.autoguardado_ts', function (require) {
    "use strict";

    function simulateCloudClick() {
        const cloudButton = document.querySelector('button.o_form_button_save');
        if (cloudButton) {
            cloudButton.click();
        }
    }

    function attachAutoSave(buttonSelector) {
        const btn = document.querySelector(buttonSelector);
        if (btn && !btn.dataset.autosaveAttached) {
            btn.dataset.autosaveAttached = true;
            btn.addEventListener("click", () => {
                simulateCloudClick();
            });
        }
    }

    function observeDOMChanges() {
        const observer = new MutationObserver(() => {
            attachAutoSave('button[name="action_save_and_reload"]');
            attachAutoSave('button[name="create_drive_folder"]');
        });

        observer.observe(document.body, { childList: true, subtree: true });
    }

    document.addEventListener('DOMContentLoaded', () => {
        observeDOMChanges();
    });
});
