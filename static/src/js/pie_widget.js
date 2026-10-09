/* @odoo-module */
import { registry } from "@web/core/registry"; // Importa el registro de módulos de Odoo
import { standardFieldProps } from "@web/views/fields/standard_field_props"; // Importa las propiedades estándar de los campos
const { Component, useRef, onMounted, onPatched } = owl; // Importa funciones y componentes necesarios de OWL

class PieChartWidget extends Component { // Define la clase PieChartWidget que extiende Component
    setup() {
        this.root = useRef('PieChartWidget-root'); // Crea una referencia para el elemento raíz del widget
        onMounted(this.renderChart.bind(this)); // Vincula la función renderChart para ejecutarse cuando el componente se monte
        onPatched(this.renderChart.bind(this)); // Vincula la función renderChart para ejecutarse cuando el componente se actualice
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy(); // Destruye el gráfico existente si hay uno
        }

        const ctx = this.root.el.querySelector('.pie-chart-canvas').getContext('2d'); // Obtiene el contexto 2D del canvas donde se renderizará el gráfico
        const parsedValue = this.props.value ? JSON.parse(this.props.value) : { labels: [], datasets: [] };
        // `cutout` y `legendPosition` son opcionales: si el payload los trae (ej. estado del
        // portafolio), se dibuja como doughnut con leyenda abajo; si no vienen, se mantiene el
        // comportamiento histórico de torta llena con leyenda por defecto de Chart.js.
        const cutout = parsedValue.cutout || '0%';
        const legendPosition = parsedValue.legendPosition || undefined;
        const { cutout: _omitCutout, legendPosition: _omitLegend, ...data } = parsedValue;

        this.chart = new Chart(ctx, { // Crea una nueva instancia de Chart.js con los datos proporcionados
            type: 'pie', // Tipo de gráfico cambiado a 'pie' para gráfico de torta
            data: data, // Datos para el gráfico
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: cutout,
                plugins: {
                    legend: legendPosition ? { position: legendPosition } : undefined,
                    // Configuración para gráficos 3D, se necesita el plugin correspondiente
                    chartJsPlugin3d: {
                        enabled: true,
                        perspective: 30, // Ángulo de perspectiva 3D
                        depth: 50, // Profundidad del gráfico
                        alpha: true // Habilitar transparencia
                    }
                },
                // Se pueden añadir opciones adicionales aquí
            }
        });
    }

    willUnmount() {
        if (this.chart) {
            this.chart.destroy(); // Destruye el gráfico cuando el componente se desmonte
        }
    }
}

PieChartWidget.template = 'PieChartWidget'; // Asigna la plantilla del widget
PieChartWidget.props = standardFieldProps; // Asigna las propiedades estándar de los campos
PieChartWidget.supportedTypes = ["char", "text"]; // Define los tipos de campos soportados (ajustar según sea necesario)

registry.category("fields").add("pie_chart_widget", PieChartWidget); // Añade el widget al registro de campos de Odoo
