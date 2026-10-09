odoo.define('Matriz.ImageWidget', function (require) {
    'use strict';

    var KanbanRecord = require('web.KanbanRecord');
    var core = require('web.core');
    var QWeb = core.qweb;

    KanbanRecord.include({
        _render: function () {
            var self = this;
            var $el = this._super.apply(this, arguments);

            // Verificar que image_url esté definido y renderizar la imagen
            if (this.record.image_url && this.record.image_url.raw_value) {
                var imageUrl = this.record.image_url.raw_value;
                $el.find('.o_kanban_image').html(QWeb.render('ImageWidget', {
                    imageUrl: imageUrl,
                }));
            }

            return $el;
        },
    });
});
