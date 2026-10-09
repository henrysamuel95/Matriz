/** @odoo-module **/
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
const { Component, useRef, onMounted, onPatched } = owl;

class GaugeChartWidget extends Component {
    setup() {
        this.root = useRef('GaugeChartWidget-root');
        onMounted(this.renderChart.bind(this));
        onPatched(this.renderChart.bind(this));
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy();
        }

        const canvas = this.root.el.querySelector('.gauge-chart-canvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const parsedValue = this.props.value ? JSON.parse(this.props.value) : { labels: [], datasets: [] };
        
        const data = parsedValue.datasets ? parsedValue : { datasets: [] };
        const centerValueText = parsedValue.centerText || "0.00";
        const chartLabel = parsedValue.chartLabel || "EVM";

        // Actualizar overlays de texto en el DOM
        const valueEl = this.root.el.querySelector('.gauge-value');
        const labelEl = this.root.el.querySelector('.gauge-label');
        if (valueEl) valueEl.textContent = centerValueText;
        if (labelEl) labelEl.textContent = chartLabel;

        // `statusText` es opcional: si el payload lo trae (CPI/SPI), se muestra una pastilla de
        // estado debajo del valor ("Sobrecosto del 6%", "En meta", "Sin datos técnicos"...); si no
        // viene, la pastilla se mantiene oculta (comportamiento histórico del widget).
        const statusEl = this.root.el.querySelector('.gauge-status-pill');
        if (statusEl) {
            if (parsedValue.statusText) {
                statusEl.textContent = parsedValue.statusText;
                statusEl.style.color = parsedValue.statusColor || '#0f172a';
                statusEl.style.background = parsedValue.statusBg || '#f1f5f9';
                statusEl.style.display = 'inline-block';
            } else {
                statusEl.style.display = 'none';
            }
        }

        this.chart = new Chart(ctx, {
            type: 'doughnut',
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                circumference: 180, // Media rosca
                rotation: 270, // Iniciar en la parte inferior izquierda (-90 deg / 270 deg)
                cutout: '75%', // Grosor de la rosca
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        enabled: false
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

GaugeChartWidget.template = 'GaugeChartWidget';
GaugeChartWidget.props = standardFieldProps;
GaugeChartWidget.supportedTypes = ["char", "text"];

registry.category("fields").add("gauge_chart_widget", GaugeChartWidget);
