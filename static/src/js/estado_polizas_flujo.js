/** @odoo-module **/

import { Component, useState } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { standardFieldProps } from '@web/views/fields/standard_field_props';

/** Enum visual independiente del flujo de Contrataciones FUV. */
const EstadoPoliza = Object.freeze({
    SOLICITADA: 'solicitada',
    PENDIENTE_ENVIO: 'pendiente_envio',
    TRAMITE_APROBACION: 'tramite_aprobacion',
    CORRECCION: 'correccion',
    PENDIENTE_ACTA: 'pendiente_acta',
    FINALIZADA: 'finalizada',
});

const FLUJO_POLIZAS = Object.freeze([
    EstadoPoliza.SOLICITADA,
    EstadoPoliza.PENDIENTE_ENVIO,
    EstadoPoliza.TRAMITE_APROBACION,
    EstadoPoliza.CORRECCION,
    EstadoPoliza.PENDIENTE_ACTA,
    EstadoPoliza.FINALIZADA,
]);

const ESTADO_META = Object.freeze({
    [EstadoPoliza.SOLICITADA]: {
        label: 'Solicitud guardada',
        shortLabel: 'Guardada',
        color: '#64748b',
    },
    [EstadoPoliza.PENDIENTE_ENVIO]: {
        label: 'Póliza adicional pendiente de envío',
        shortLabel: 'Pendiente de envío',
        color: '#d97706',
    },
    [EstadoPoliza.TRAMITE_APROBACION]: {
        label: 'Trámite de aprobación de pólizas',
        shortLabel: 'En aprobación',
        color: '#2563eb',
    },
    [EstadoPoliza.CORRECCION]: {
        label: 'Corrección solicitada al contratista',
        shortLabel: 'En corrección',
        color: '#dc2626',
    },
    [EstadoPoliza.PENDIENTE_ACTA]: {
        label: 'Pólizas aprobadas - Pendiente de acta',
        shortLabel: 'Pendiente de acta',
        color: '#7c3aed',
    },
    [EstadoPoliza.FINALIZADA]: {
        label: 'Trámite de pólizas finalizado',
        shortLabel: 'Finalizado',
        color: '#15803d',
    },
});

class EstadoPolizasFlujoField extends Component {
    setup() {
        this.ui = useState({ showFullFlow: false });
    }

    get value() {
        return this.props.value || '';
    }

    get currentIndex() {
        return FLUJO_POLIZAS.indexOf(this.value);
    }

    get current() {
        return this.getStateInfo(this.value);
    }

    get previous() {
        return this.currentIndex > 0
            ? this.getStateInfo(FLUJO_POLIZAS[this.currentIndex - 1])
            : null;
    }

    get next() {
        return this.currentIndex >= 0 && this.currentIndex < FLUJO_POLIZAS.length - 1
            ? this.getStateInfo(FLUJO_POLIZAS[this.currentIndex + 1])
            : null;
    }

    get progressText() {
        return this.currentIndex >= 0
            ? `${this.currentIndex + 1}/${FLUJO_POLIZAS.length}`
            : '';
    }

    get currentStyle() {
        return `--poliza-estado-color: ${this.current.color};`;
    }

    get fullFlowSteps() {
        return FLUJO_POLIZAS.map((value, index) => ({
            ...this.getStateInfo(value),
            number: index + 1,
            isCurrent: value === this.value,
        }));
    }

    getStateInfo(value) {
        const meta = ESTADO_META[value] || {};
        return {
            value,
            label: meta.label || this.getSelectionLabel(value) || 'Sin estado',
            shortLabel: meta.shortLabel || meta.label || this.getSelectionLabel(value) || 'Sin estado',
            color: meta.color || '#64748b',
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

    closeFullFlow() {
        this.ui.showFullFlow = false;
    }
}

EstadoPolizasFlujoField.template = 'Matriz.EstadoPolizasFlujoField';
EstadoPolizasFlujoField.props = standardFieldProps;
EstadoPolizasFlujoField.supportedTypes = ['selection'];

registry.category('fields').add('estado_polizas_flujo', EstadoPolizasFlujoField);

