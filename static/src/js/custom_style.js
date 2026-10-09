odoo.define('matriz.valor_sobre_costo_style', function (require) {
    "use strict";

    const core = require('web.core');
    const _t = core._t;

    // Intentar encontrar el campo por su nombre o clases específicas
    function findField() {
        const field = document.querySelector('input[name="valor_sobre_costo"], div.o_field_widget[name="valor_sobre_costo"] input');

        return field;
    }

    // Aplicar el estilo dinámico al campo
    function applyStyle(field) {
        const value = parseFloat(field.value || field.innerText) || 0; // Tomar el valor del campo o su texto
        if (value !== 0) {
            field.style.backgroundColor = "#EFF1F7"; // Fondo rojo
            field.style.color = "red";
        } else {
            field.style.backgroundColor = "#EFF1F7"; // Fondo normal
            field.style.color = "black";
        }
    }

    // Escuchar cambios en tiempo real
    function attachListeners(field) {
        field.addEventListener('change', function () {
            applyStyle(field);
        });

        field.addEventListener('input', function () {
            applyStyle(field);
        });
    }

    // Inicialización
    function init() {
        const field = findField();

        if (field) {
            applyStyle(field);
            attachListeners(field);
        } else {
            setTimeout(init, 1000); // Reintentar después de 1 segundo
        }
    }

    // Ejecutar
    init();
});
