/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
const { Component, useRef, onMounted, onPatched } = owl;

class HorizontalBarChartWidget extends Component {
    setup() {
        this.root = useRef('HorizontalBarChartWidget-root');
        onMounted(this.renderChart.bind(this));
        onPatched(this.renderChart.bind(this));
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy();
        }

        const ctx = this.root.el.querySelector('.chart-canvas-horizontal').getContext('2d');
        const parsedValue = this.props.value ? JSON.parse(this.props.value) : { labels: [], datasets: [] };
        // `ids` es opcional: cuando el payload trae un id de registro por barra (mismo orden que
        // `labels`), se habilita click-to-drilldown hacia el dashboard de ese registro; si no viene,
        // el gráfico se comporta igual que antes (sin click).
        const ids = parsedValue.ids || [];
        const { ids: _omitIds, ...data } = parsedValue;
        const record = this.props.record;

        this.chart = new Chart(ctx, {
            type: 'bar', // Tipo de gráfico: Barra horizontal
            data: data,
            options: {
                indexAxis: 'y', // Eje horizontal
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        beginAtZero: true
                    }
                },
                onClick: (event, elements) => {
                    if (!elements.length || !ids.length || !record) {
                        return;
                    }
                    const projectId = ids[elements[0].index];
                    if (!projectId) {
                        return;
                    }
                    const projectName = data.labels ? data.labels[elements[0].index] : undefined;
                    record.update({
                        seleccionador_graficas: '02',
                        nombre_proyecto: [projectId, projectName],
                    });
                },
                onHover: (event, elements) => {
                    event.native.target.style.cursor = (elements.length && ids.length) ? 'pointer' : 'default';
                }
            }
        });
    }

    willUnmount() {
        if (this.chart) {
            this.chart.destroy();
        }
    }
}

HorizontalBarChartWidget.template = 'HorizontalBarChartWidget';
HorizontalBarChartWidget.props = standardFieldProps;
HorizontalBarChartWidget.supportedTypes = ["char", "text"]; // Tipo de dato soportado

registry.category("fields").add("horizontal_bar_chart_widget", HorizontalBarChartWidget);
