odoo.define('Matriz.filtered_files_widget', function (require) {
    "use strict";

    const basic_fields = require('web.basic_fields');
    const core = require('web.core');

    const FilteredFilesWidget = basic_fields.FieldChar.extend({
        template: 'FilteredFilesWidget',
        init: function () {
            this._super.apply(this, arguments);
        },
        _render: function () {
            const files = JSON.parse(this.value || '[]');
            const $list = $('<ul/>');
            files.forEach(file => {
                $list.append($('<li/>').text(file.Nombre));
            });
            this.$el.empty().append($list);
        },
    });

    core.form_widget_registry.add('filtered_files_widget', FilteredFilesWidget);
});
