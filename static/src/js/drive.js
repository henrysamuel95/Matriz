odoo.define('Matriz.kanban_view', function (require) {
    'use strict';

    const KanbanView = require('web.KanbanView');

    KanbanView.include({
        get_file_icon: function (fileType) {
            switch (fileType) {
                case 'pdf':
                    return 'doc_pdf';
                case 'doc':
                case 'docx':
                    return 'doc_doc';
                case 'xls':
                case 'xlsx':
                    return 'doc_xls';
                case 'txt':
                    return 'doc_txt';
                case 'ppt':
                case 'pptx':
                    return 'doc_ppt';
                case 'rar':
                case 'zip':
                    return 'doc_rar';
                case 'jpg':
                case 'jpeg':
                case 'png':
                    return 'doc_img';
                default:
                    return 'default';
            }
        },
    });

    return KanbanView;
});
