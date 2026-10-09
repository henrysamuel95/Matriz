odoo.define('Matriz.block_workspace_click', function (require) {
    'use strict';

    // Fase de captura (true) para interceptar antes que Odoo
    document.addEventListener('click', function (event) {

        // 1. ¿Estamos dentro de tu div .workspace?
        if (!event.target.closest('.workspace')) {
            return;
        }

        // 2. ¿Es un clic en una fila de datos?
        if (!event.target.closest('.o_data_row')) {
            return;
        }

        // 3. LA REGLA DE ORO:
        // Si el clic es en tus botones específicos, ¡DEJAR PASAR!
        if (event.target.closest('.btn-purple-square') || 
            event.target.closest('.btn-white-add') || 
            event.target.closest('button')) {
            return; // Es un botón válido, el clic procede.
        }

        // 4. Si no fue un botón (fue nombre o espacio vacío), ¡BLOQUEAR!
        event.stopPropagation();
        event.stopImmediatePropagation();
        event.preventDefault();
        
        console.log('🛑 Clic bloqueado: Solo se permiten los botones.');

    }, true);
});