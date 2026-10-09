console.log("Iniciar Modulo")
odoo.define('nombre_modulo.custom_statusbar', function (define) {
    "use strict";

    var KanbanController = require('web.KanbanController');

    KanbanController.include({
        renderButtons: function () {
            this._super.apply(this, arguments);

            // Obtén el valor inicial del campo estado_proyecto
            var estadoProyecto = this.renderer.state.data.estado_proyecto;

            // Establece el color inicial del widget statusbar
            $('.o_statusbar_style').css('background-color', estadoProyecto);

            // Escucha el cambio en el widget selection y actualiza el color del widget statusbar
            this.$('.o_kanban_quick_create .o_field_widget[name="estado_proyecto"]').on('change', function(){
                var nuevoEstado = $(this).find('option:selected').data('color');
                $('.o_statusbar_style').css('background-color', nuevoEstado);
            });
        },
    });
});
