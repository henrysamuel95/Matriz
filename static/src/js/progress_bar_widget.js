/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
const { Component, useRef, onMounted, onPatched } = owl;

class ProgressBarWidget extends Component {
    setup() {
        this.root = useRef('ProgressBarWidget-root');
        onMounted(this.onUpdateProgressBar.bind(this));
        onPatched(this.onUpdateProgressBar.bind(this));
    }

    onUpdateProgressBar() {
        let value = Math.min(Math.max(this.props.value, 0), 100); // Asegura que el valor esté entre 0 y 100
        this.widthComplete = value;
        // Redondea el valor al entero más cercano
        let roundedValue = Math.round(this.widthComplete);
        this.root.el.querySelector('.progress-bar-inner').style.width = this.widthComplete + '%';
        this.root.el.querySelector('.progress_number').textContent = roundedValue + '%';
    }
}

ProgressBarWidget.template = 'ProgressBarWidget';
ProgressBarWidget.props = standardFieldProps;
ProgressBarWidget.supportedTypes = ["float", "integer"];

registry.category("fields").add("progress_bar_widget", ProgressBarWidget);
