/** @odoo-module **/
import { Component, useRef, onMounted, onPatched } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';

class StatusBarField extends Component {
    setup() {
        this.root = useRef('StatusBarField-root');
        onMounted(this.updateStatusBar.bind(this));
        onPatched(this.updateStatusBar.bind(this));
    }

    updateStatusBar() {
        const value = this.props.value || 'red'; // Default value to 'red' if undefined
        const items = this.root.el.querySelectorAll('.o_status_bar_item');
        items.forEach(item => {
            const status = item.dataset.status;
            if (status === value) {
                item.classList.add('o_status_bar_item_active');
            } else {
                item.classList.remove('o_status_bar_item_active');
            }
        });
    }
}

StatusBarField.template = 'StatusBarField';
StatusBarField.props = standardFieldProps;
StatusBarField.supportedTypes = ['selection'];

registry.category('fields').add('status_bar', StatusBarField);
