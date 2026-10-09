/** @odoo-module **/
import { Component, useRef, onMounted, onPatched } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';

class EstadoContratacionField extends Component {
    setup() {
        this.root = useRef('EstadoContratacionField-root');
        onMounted(this.updateEstadoContratacion.bind(this));
        onPatched(this.updateEstadoContratacion.bind(this));
    }

    updateEstadoContratacion() {
        const value = this.props.value || '';
        const estadoContratacionItem = this.root.el.querySelector(`.estado-contratacion-item[data-status="${value}"]`);
        if (estadoContratacionItem) {
            estadoContratacionItem.classList.add('estado-contratacion-item-active');
        }
    }
}

EstadoContratacionField.template = 'EstadoContratacionField';
EstadoContratacionField.props = standardFieldProps;
EstadoContratacionField.supportedTypes = ['selection'];

registry.category('fields').add('estado_contratacion', EstadoContratacionField);
