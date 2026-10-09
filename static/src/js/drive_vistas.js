odoo.define('matriz.SplitView', function (require) {
"use strict";

var AbstractAction = require('web.AbstractAction');
var core = require('web.core');

var SplitView = AbstractAction.extend({
    template: 'MatrizSplitView',

    init: function (parent, action) {
        this._super.apply(this, arguments);
        this.leftAction = action.params.left_action;
        this.rightAction = action.params.right_action;
    },

    start: function () {
        var self = this;
        return this._super.apply(this, arguments).then(function () {
            return $.when(
                self.do_action(self.leftAction, {
                    clear_breadcrumbs: true,
                    replace_last_action: true,
                    target: 'left_pane'
                }),
                self.do_action(self.rightAction, {
                    clear_breadcrumbs: true,
                    replace_last_action: true,
                    target: 'right_pane'
                })
            );
        });
    },
});

core.action_registry.add('matriz_split_view', SplitView);

return SplitView;
});