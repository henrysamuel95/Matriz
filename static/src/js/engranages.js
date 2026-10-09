function actualizarValoresDesdeHTML() {
    // Buscar todos los engranajes
    var engranajes = document.querySelectorAll('.engranaje');

    // Iterar sobre cada engranaje
    engranajes.forEach(function(engranaje) {
        var valorContainer = engranaje.querySelector('.valor-container');
        if (valorContainer) {
            // Obtener el valor del campo dentro del valorContainer
            var valorElemento = valorContainer.querySelector('.value');
            if (valorElemento) {
                var valor = valorElemento.textContent.trim();

                // Verificar si es el engranaje4 y el campo es cantidad_lineas_tabla_pagos
                if (engranaje.classList.contains('engranaje4') && valorElemento.querySelector('[name="cantidad_lineas_tabla_pagos"]')) {
                    // Si es el campo cantidad_lineas_tabla_pagos, no añadir $
                    valorElemento.textContent = valor;
                } else {
                    // Añadir $ para otros campos
                    valorElemento.textContent = '$' + valor.replace('$', ''); // Remover $ existente si lo hay
                }
            }
        }
    });
}

document.addEventListener('DOMContentLoaded', function() {
    actualizarValoresDesdeHTML();
});

setInterval(actualizarValoresDesdeHTML, 3000);