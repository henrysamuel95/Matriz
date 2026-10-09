odoo.define('matriz.acta_conditional_actions', function (require) {
    "use strict";

    const ListController = require('web.ListController');

    function selectedRecordsFromState(controller, state) {
        const selectedIds = controller.getSelectedIds ? controller.getSelectedIds() : [];
        const selectedIdSet = new Set(selectedIds.map(function (id) {
            return String(id);
        }));
        if (!selectedIds.length || !state || !state.data) {
            return [];
        }
        return state.data.filter(function (record) {
            const recordId = record.res_id || record.id;
            return selectedIdSet.has(String(recordId));
        });
    }

    function selectionIsOnlyState(records, selectedCount, estado) {
        return (
            selectedCount > 0 &&
            records.length === selectedCount &&
            records.every(function (record) {
                return record.data && rawValue(record.data.estado) === estado;
            })
        );
    }

    function selectionMatches(records, selectedCount, predicate) {
        return (
            selectedCount > 0 &&
            records.length === selectedCount &&
            records.every(function (record) {
                return record.data && predicate(record.data);
            })
        );
    }

    function rawValue(value) {
        if (value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'raw_value')) {
            return value.raw_value;
        }
        return value;
    }

    ListController.include({
        _getActionMenuItems: function (state) {
            const actionMenus = this._super.apply(this, arguments);
            if (
                this.modelName !== 'acta_entrega_documentos' ||
                !actionMenus ||
                !actionMenus.items ||
                !actionMenus.items.other
            ) {
                return actionMenus;
            }

            const selectedIds = this.getSelectedIds ? this.getSelectedIds() : [];
            const records = selectedRecordsFromState(this, state);
            const selectedCount = selectedIds.length;

            const visibleByAction = {
                'Enviar masivamente a Tesorería': selectionIsOnlyState(records, selectedCount, 'au_tesoreria'),
                'Firmar masivamente como Coordinador': selectionMatches(records, selectedCount, function (data) {
                    return (
                        rawValue(data.estado) === 'coordinador' &&
                        rawValue(data.pendiente_firma_coordinador) === true
                    );
                }),
                'Firmar certificación como Director Financiero': selectionMatches(records, selectedCount, function (data) {
                    return (
                        rawValue(data.estado) === 'director_financiero' &&
                        rawValue(data.pendiente_firma_director_financiero) === true
                    );
                }),
            };

            actionMenus.items.other = actionMenus.items.other.filter(function (item) {
                if (!Object.prototype.hasOwnProperty.call(visibleByAction, item.description)) {
                    return true;
                }
                return visibleByAction[item.description];
            });

            return actionMenus;
        },
    });
});
