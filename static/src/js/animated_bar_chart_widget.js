/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
const { Component, useRef, onMounted, onPatched } = owl;

class AnimatedBarChartWidget extends Component {
    setup() {
        this.root = useRef('AnimatedBarChartWidget-root');
        onMounted(this.renderChart.bind(this));
        onPatched(this.renderChart.bind(this));
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy();
        }

        const ctx = this.root.el.querySelector('.chart-canvas-animated').getContext('2d');
        const data = JSON.parse(this.props.value); // Asume que el valor es una cadena JSON

        this.chart = new Chart(ctx, {
            type: 'bar',
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: {
                    duration: 2000,
                    easing: 'easeOutBounce'
                },
                scales: {
                    y: {
                        beginAtZero: true
                    }
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

AnimatedBarChartWidget.template = 'AnimatedBarChartWidget';
AnimatedBarChartWidget.props = standardFieldProps;
AnimatedBarChartWidget.supportedTypes = ["char"]; // Tipo de dato soportado

registry.category("fields").add("animated_bar_chart_widget", AnimatedBarChartWidget);
