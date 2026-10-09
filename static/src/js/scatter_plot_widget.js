/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
const { Component, useRef, onMounted, onPatched } = owl;

class LineChartWidget extends Component {
    setup() {
        this.root = useRef('LineChartWidget-root');
        onMounted(this.renderChart.bind(this));
        onPatched(this.renderChart.bind(this));
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy();
        }

        const ctx = this.root.el.querySelector('.chart-canvas-line').getContext('2d');
        const data = this.props.value ? JSON.parse(this.props.value) : { labels: [], datasets: [] };

        this.chart = new Chart(ctx, {
            type: 'line', // Tipo de gráfico: línea
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                // Opciones adicionales aquí
            }
        });
    }

    willUnmount() {
        if (this.chart) {
            this.chart.destroy();
        }
    }
}

LineChartWidget.template = 'LineChartWidget';
LineChartWidget.props = standardFieldProps;
LineChartWidget.supportedTypes = ["char", "text"]; // Tipo de dato soportado

registry.category("fields").add("line_chart_widget", LineChartWidget);
