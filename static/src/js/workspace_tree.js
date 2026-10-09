odoo.define('Matriz.workspace_tree', function (require) {
    'use strict';

    console.log('[WorkspaceTree] Click redirect loaded');

    document.addEventListener('click', function (event) {

        /* 🚫 EXCEPCIÓN ABSOLUTA: botones prioritarios */
        if (event.target.closest('.o_priority_click')) {
            return;
        }

        /* 🚫 Click directo en el botón de carpeta */
        if (event.target.closest('button.workspace-folder-btn')) {
            return;
        }

        /* 👉 Click SOLO en nombre */
        const nameCell = event.target.closest('.workspace td.o_data_cell');
        if (!nameCell) return;

        const row = nameCell.closest('tr');
        if (!row) return;

        const folderButton = row.querySelector('button.workspace-folder-btn');
        if (!folderButton) return;

        event.preventDefault();
        event.stopPropagation();

        folderButton.click();

    }, true);

});
