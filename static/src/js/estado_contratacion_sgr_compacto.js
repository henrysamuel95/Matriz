/** @odoo-module **/

import { Component, useState } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';

const FLOW = [
    'coordinador_1', 'juridico_1', 'coordinador_2', 'juridico_2',
    'juridico_secop', 'financiero', 'juridico_acta_inicio',
    'coordinador_polizas_firma', 'juridico_acta_aprobacion',
    'coordinador_confirmacion', 'contratado',
];

const LEGACY_STEP = {
    coordinador: 'coordinador_1',
    juridico: 'juridico_1',
    financiera: 'financiero',
};

const LABELS = {
    coordinador_1: 'Coordinador',
    juridico_1: 'Jurídico',
    coordinador_2: 'Coordinador',
    juridico_2: 'Jurídico',
    juridico_secop: 'Jurídico SECOP',
    financiero: 'Financiero',
    juridico_acta_inicio: 'Jurídico - Acta de inicio',
    coordinador_polizas_firma: 'Coordinador - Pólizas y firma',
    juridico_acta_aprobacion: 'Jurídico - Acta de aprobación',
    coordinador_confirmacion: 'Coordinador - Confirmación',
    contratado: 'Contratado',
};

const DESCRIPTIONS = {
    coordinador_1: 'Envía la solicitud de contratación y diligencia el formulario.',
    juridico_1: 'Recibe la solicitud, asigna quién realiza el proceso y prepara el estudio previo y el contrato.',
    coordinador_2: 'Recoge las firmas y solicita las pólizas de acuerdo con el contrato.',
    juridico_2: 'Genera el acta de aprobación.',
    juridico_secop: 'Publica el proceso en SECOP y registra el ID y el enlace.',
    financiero: 'Genera el RPC o registro presupuestal.',
};

class EstadoContratacionSgrCompacto extends Component {
    setup() {
        this.ui = useState({ showFullFlow: false });
    }

    get flowStates() { return FLOW; }
    get isNewFlow() { return true; }
    get value() { return this.props.value || ''; }
    get flowValue() { return LEGACY_STEP[this.value] || this.value || ''; }
    get current() { return this.getStateInfo(this.flowValue); }

    get currentStyle() {
        const color = this.flowValue === 'contratado' ? '#28a745'
            : this.flowValue.startsWith('juridico') ? '#6f42c1'
            : this.flowValue === 'financiero' ? '#007bff' : '#fd7e14';
        return `--estado-color: ${color};`;
    }

    getStateInfo(value) {
        const fieldName = this.props.name || 'estado_contratacion';
        const selection = this.props.record?.fields?.[fieldName]?.selection || [];
        const option = selection.find(([key]) => key === value);
        return {
            value,
            label: LABELS[value] || option?.[1] || 'Sin estado',
            description: DESCRIPTIONS[value] || '',
        };
    }

    get previous() {
        const index = FLOW.indexOf(this.flowValue);
        return index > 0 ? this.getStateInfo(FLOW[index - 1]) : null;
    }

    get next() {
        const index = FLOW.indexOf(this.flowValue);
        return index >= 0 && index < FLOW.length - 1
            ? this.getStateInfo(FLOW[index + 1]) : null;
    }

    get showNextStep() { return this.flowValue !== 'contratado'; }

    get progressText() {
        const index = FLOW.indexOf(this.flowValue);
        return index >= 0 ? `${index + 1}/${FLOW.length}` : '';
    }

    get fullFlowSteps() {
        return FLOW.map((value, index) => ({
            ...this.getStateInfo(value), number: index + 1, isCurrent: value === this.flowValue,
        }));
    }

    toggleFullFlow(event) {
        event.preventDefault();
        event.stopPropagation();
        this.ui.showFullFlow = !this.ui.showFullFlow;
    }

    closeFullFlow() { this.ui.showFullFlow = false; }
}

EstadoContratacionSgrCompacto.template = 'EstadoContratacionCompactoField';
EstadoContratacionSgrCompacto.props = standardFieldProps;
EstadoContratacionSgrCompacto.supportedTypes = ['selection'];

registry.category('fields').add('estado_contratacion_sgr_compacto', EstadoContratacionSgrCompacto);

class EstadoContratacionSgrCompactoKanban extends EstadoContratacionSgrCompacto {
    get value() {
        return this.props.record?.data?.estado_contratacion || '';
    }
}

EstadoContratacionSgrCompactoKanban.template = EstadoContratacionSgrCompacto.template;
EstadoContratacionSgrCompactoKanban.props = ['record', 'readonly?'];

registry.category('view_widgets').add(
    'estado_contratacion_sgr_compacto_kanban',
    EstadoContratacionSgrCompactoKanban
);
