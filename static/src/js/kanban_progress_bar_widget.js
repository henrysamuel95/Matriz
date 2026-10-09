/** @odoo-module **/
import { Component, useRef, onMounted, onPatched } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';

class KanbanProgressBarField extends Component {
    setup() {
        this.root = useRef('KanbanProgressBarField-root');
        onMounted(this.updateProgressBar.bind(this));
        onPatched(this.updateProgressBar.bind(this));
    }

    updateProgressBar() {
        const value = Math.min(Math.max(this.props.value, 0), 100);
        const roundedValue = Math.round(value);
        const progressBarInner = this.root.el.querySelector('.progress-bar-inner');
        if (progressBarInner) {
            progressBarInner.style.width = `${value}%`;
        }
        const progressNumber = this.root.el.querySelector('.progress_number');
        if (progressNumber) {
            progressNumber.textContent = `${roundedValue}%`;
        }
    }
}

KanbanProgressBarField.template = 'KanbanProgressBarField';
KanbanProgressBarField.props = standardFieldProps;
KanbanProgressBarField.supportedTypes = ['float', 'integer'];

registry.category('fields').add('kanban_progress_bar', KanbanProgressBarField);
