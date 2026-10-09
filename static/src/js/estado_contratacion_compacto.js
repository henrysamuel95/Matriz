/** @odoo-module **/
import { Component, useState } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';

const NEW_FLOW_STATES = [
    'coordinador',
    'ventanilla',
    'presupuesto',
    'director_juridico',
    'juridico_1',
    'recursos_humanos',
    'juridico_2',
    'firma',
    'contratado',
    'polizas',
    'finalizado',
    'liquidado',
    'anulado',
];

const NEW_FLOW_STATES_PERSONA_JURIDICA = [
    'coordinador',
    'ventanilla',
    'presupuesto',
    'director_juridico',
    'juridico_revision_contrato',
    'firma',
    'contratado',
    'polizas',
    'finalizado',
    'liquidado',
    'anulado',
];

const OTRO_SI_FINAL_STATES = [
    'contratado',
    'adicion_formalizada',
    'prorroga_formalizada',
    'adicion_prorroga_formalizada',
    'modificatorio_formalizado',
    'suspendido',
    'reinicio_formalizado',
    'suspension_prorrogada',
];

const LEGACY_FLOW_STATES = [
    'coordinador',
    'financiera',
    'juridico',
    'recursos_humanos',
    'contratado',
];

const STATE_META = {
    coordinador: { label: 'Coordinador', color: '#fd7e14' },
    financiera: { label: 'Financiera', color: '#007bff' },
    ventanilla: { label: 'Ventanilla', color: '#0d6efd' },
    presupuesto: { label: 'Presupuesto', color: '#198754' },
    director_juridico: { label: 'Dir. Jurídico', color: '#4b2e83' },
    juridico: { label: 'Jurídico', color: '#6f42c1' },
    juridico_1: { label: 'Jurídico 1 - Revisión', color: '#6f42c1' },
    juridico_revision_contrato: { label: 'Jurídico - Revisión y contrato', color: '#6f42c1' },
    recursos_humanos: { label: 'Recursos humanos', color: '#17a2b8' },
    juridico_2: { label: 'Jurídico 2 - Contrato', color: '#8f3ffc' },
    firma: { label: 'Coord. - Subir contrato firmado', color: '#20c997' },
    contratado: { label: 'Contratado', color: '#28a745' },
    polizas: { label: 'Pólizas (Eventual)', color: '#6610f2' },
    suspendido: { label: 'Suspendido', color: '#dc3545' },
    finalizado: { label: 'Finalizado', color: '#343a40' },
    liquidado: { label: 'Liquidado (Eventual)', color: '#00897b' },
    anulado: { label: 'Anulado (Eventual)', color: '#6c757d' },
    adicion_formalizada: { label: 'Adición formalizada', color: '#28a745' },
    prorroga_formalizada: { label: 'Prórroga formalizada', color: '#28a745' },
    adicion_prorroga_formalizada: { label: 'Adición/Prórroga formalizada', color: '#28a745' },
    modificatorio_formalizado: { label: 'Modificatorio formalizado', color: '#28a745' },
    reinicio_formalizado: { label: 'Reinicio formalizado', color: '#20c997' },
    suspension_prorrogada: { label: 'Suspensión prorrogada', color: '#dc3545' },
};

class EstadoContratacionCompactoField extends Component {
    setup() {
        this.ui = useState({ showFullFlow: false });
    }

    get value() {
        return this.props.value || '';
    }

    get current() {
        return this.getStateInfo(this.flowValue);
    }

    get flowStates() {
        if (this.isOtroSiNewFlow) {
            return this.getOtroSiFlowStates();
        }
        if (!this.isNewFlow) {
            return LEGACY_FLOW_STATES;
        }
        if (this.personType === 'persona_juridica') {
            return NEW_FLOW_STATES_PERSONA_JURIDICA;
        }
        return NEW_FLOW_STATES;
    }

    get flowValue() {
        if (
            this.isOtroSiNewFlow
            && this.isOtroSiJuridicoUnificadoFlow
            && ['juridico_1', 'juridico_2'].includes(this.value)
        ) {
            return 'juridico_revision_contrato';
        }
        if (
            this.isNewFlow
            && !this.isOtroSiNewFlow
            && this.personType === 'persona_juridica'
            && ['juridico_1', 'juridico_2'].includes(this.value)
        ) {
            return 'juridico_revision_contrato';
        }
        return this.value;
    }

    get personType() {
        const value = this.props.record?.data?.select_persona;
        return value?.raw_value || value || '';
    }

    get isNewFlow() {
        const data = this.props.record?.data || {};
        return Boolean(
            this.getDataValue(data, 'usa_flujo_contratacion_nuevo')
            || this.getDataValue(data, 'usa_flujo_otrosi_nuevo')
        );
    }

    get isOtroSiNewFlow() {
        return Boolean(this.getDataValue(this.props.record?.data || {}, 'usa_flujo_otrosi_nuevo'));
    }

    get isOtroSiJuridicoUnificadoFlow() {
        const data = this.props.record?.data || {};
        return Boolean(
            this.getDataValue(data, 'suspension')
            || this.getDataValue(data, 'reinicio')
            || this.getDataValue(data, 'acta_de_prorroga_de_la_suspension')
        );
    }

    getDataValue(data, name) {
        const value = data?.[name];
        return value && typeof value === 'object' && 'raw_value' in value
            ? value.raw_value
            : value;
    }

    getOtroSiFlowStates() {
        const data = this.props.record?.data || {};
        const flow = ['coordinador'];
        const tieneValor = Boolean(
            this.getDataValue(data, 'adicion')
            || this.getDataValue(data, 'adicion_prorroga')
            || (
                this.getDataValue(data, 'otro_si_modificatorio')
                && ['valor', 'ambos'].includes(this.getDataValue(data, 'tipo_modificacion_modificatorio'))
            )
        );
        const tipoPersona = this.getDataValue(data, 'select_persona') || '';
        const esJuridicoUnificado = Boolean(
            this.getDataValue(data, 'suspension')
            || this.getDataValue(data, 'reinicio')
            || this.getDataValue(data, 'acta_de_prorroga_de_la_suspension')
        );
        const omiteRrhh = tipoPersona === 'persona_juridica';

        if (tieneValor) {
            flow.push('ventanilla', 'presupuesto');
        }
        flow.push('director_juridico');
        // Suspensiones, reinicios y prorrogas de suspension no tienen paso de
        // RRHH ni doble juridico: juridico revisa y prepara el documento en una sola etapa.
        if (esJuridicoUnificado) {
            flow.push('juridico_revision_contrato', 'firma', this.getOtroSiFinalState(data));
            return flow;
        }
        flow.push('juridico_1');
        if (!omiteRrhh) {
            flow.push('recursos_humanos');
        }
        flow.push('juridico_2', 'firma', this.getOtroSiFinalState(data));
        return flow;
    }

    getOtroSiFinalState(data) {
        if (this.getDataValue(data, 'suspension')) {
            return 'suspendido';
        }
        if (this.getDataValue(data, 'reinicio')) {
            return 'reinicio_formalizado';
        }
        if (this.getDataValue(data, 'acta_de_prorroga_de_la_suspension')) {
            return 'suspension_prorrogada';
        }
        if (this.getDataValue(data, 'adicion_prorroga')) {
            return 'adicion_prorroga_formalizada';
        }
        if (this.getDataValue(data, 'adicion')) {
            return 'adicion_formalizada';
        }
        if (this.getDataValue(data, 'prorroga')) {
            return 'prorroga_formalizada';
        }
        if (this.getDataValue(data, 'otro_si_modificatorio')) {
            return 'modificatorio_formalizado';
        }
        if (OTRO_SI_FINAL_STATES.includes(this.value)) {
            return this.value;
        }
        return 'contratado';
    }

    get fullFlowSteps() {
        return this.flowStates.map((value, index) => ({
            ...this.getStateInfo(value),
            number: index + 1,
            isCurrent: value === this.flowValue,
        }));
    }

    get previous() {
        const index = this.flowStates.indexOf(this.flowValue);
        return index > 0 ? this.getStateInfo(this.flowStates[index - 1]) : null;
    }

    get next() {
        const index = this.flowStates.indexOf(this.flowValue);
        return index >= 0 && index < this.flowStates.length - 1
            ? this.getStateInfo(this.flowStates[index + 1])
            : null;
    }

    get showNextStep() {
        return true;
    }

    get progressText() {
        const index = this.flowStates.indexOf(this.flowValue);
        return index >= 0 ? `${index + 1}/${this.flowStates.length}` : '';
    }

    get currentStyle() {
        return `--estado-color: ${this.current.color};`;
    }

    getStateInfo(value) {
        const selectionLabel = this.getSelectionLabel(value);
        const meta = STATE_META[value] || {};
        return {
            value,
            label: meta.label || selectionLabel || 'Sin estado',
            color: meta.color || '#6c757d',
        };
    }

    getSelectionLabel(value) {
        const selection = this.props.record?.fields?.[this.props.name]?.selection || [];
        const option = selection.find(([optionValue]) => optionValue === value);
        return option ? option[1] : '';
    }

    toggleFullFlow(event) {
        event.preventDefault();
        event.stopPropagation();
        this.ui.showFullFlow = !this.ui.showFullFlow;
    }

    // Cierra la lista completa al salir del widget para no obligar al usuario a pulsar "Ver menos".
    closeFullFlow() {
        this.ui.showFullFlow = false;
    }
}

EstadoContratacionCompactoField.template = 'EstadoContratacionCompactoField';
EstadoContratacionCompactoField.props = standardFieldProps;
EstadoContratacionCompactoField.supportedTypes = ['selection'];

registry.category('fields').add('estado_contratacion_compacto', EstadoContratacionCompactoField);

class EstadoContratacionCompactoKanban extends EstadoContratacionCompactoField {
    get value() {
        return this.props.record?.data?.estado_contratacion || '';
    }

    getSelectionLabel(value) {
        const selection = this.props.record?.fields?.estado_contratacion?.selection || [];
        const option = selection.find(([optionValue]) => optionValue === value);
        return option ? option[1] : '';
    }
}

EstadoContratacionCompactoKanban.template = EstadoContratacionCompactoField.template;
EstadoContratacionCompactoKanban.props = ['record', 'readonly?'];

registry.category('view_widgets').add(
    'estado_contratacion_compacto_kanban',
    EstadoContratacionCompactoKanban
);
