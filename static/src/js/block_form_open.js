odoo.define('Matriz.block_form_open', function (require) {
    'use strict';

    var session = require('web.session');
    var blockForConsulta = false;

    session.user_has_group('Matriz.group_consulta_coordinacion_financiera').then(function (hasGroup) {
        blockForConsulta = hasGroup;
    });

    // Usamos la fase de captura (true) para interceptar el evento ANTES que Odoo
    document.addEventListener('click', function (event) {
        if (!blockForConsulta) {
            return;
        }

        // 1. Verificamos si el clic ocurrió dentro de nuestro campo especial
        // Busca la clase "js_block_click" que pondremos en el XML
        if (!event.target.closest('.js_block_click')) {
            return; 
        }

        // 2. Verificamos si es una fila de datos
        if (!event.target.closest('.o_data_row')) {
            return;
        }

        // 3. ¡LA CLAVE! Si el clic fue en un BOTÓN o INPUT, déjalo pasar.
        // Esto permite que tu botón (+) y el folder funcionen.
        if (event.target.closest('button') || event.target.closest('a') || event.target.closest('input')) {
            return; 
        }

        // 4. Si llegamos aquí, fue un clic en el texto o espacio vacío.
        // MATAR EL EVENTO INMEDIATAMENTE.
        event.stopPropagation();
        event.stopImmediatePropagation();
        event.preventDefault();
        
        console.log('🚫 Apertura de form bloqueada por JS');

    }, true); 
});
