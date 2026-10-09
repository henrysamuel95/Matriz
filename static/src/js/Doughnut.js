/* @odoo-module */
import { registry } from "@web/core/registry"; // Importa el registro de módulos de Odoo
import { standardFieldProps } from "@web/views/fields/standard_field_props"; // Importa las propiedades estándar de los campos
const { Component, useRef, onMounted, onPatched } = owl; // Importa funciones y componentes necesarios de OWL

class PolarAreaChartWidget extends Component { // Define la clase PolarAreaChartWidget que extiende Component
    setup() {
        this.root = useRef('PolarAreaChartWidget-root'); // Crea una referencia para el elemento raíz del widget
        onMounted(this.renderChart.bind(this)); // Vincula la función renderChart para ejecutarse cuando el componente se monte
        onPatched(this.renderChart.bind(this)); // Vincula la función renderChart para ejecutarse cuando el componente se actualice
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy(); // Destruye el gráfico existente si hay uno
        }

        const ctx = this.root.el.querySelector('.polar-area-chart-canvas').getContext('2d'); // Obtiene el contexto 2D del canvas donde se renderizará el gráfico
        const data = this.props.value ? JSON.parse(this.props.value) : { labels: [], datasets: [] };

        this.chart = new Chart(ctx, { // Crea una nueva instancia de Chart.js con los datos proporcionados
            type: 'polarArea', // Tipo de gráfico cambiado a 'polarArea' para gráfico de área polar
            data: data, // Datos para el gráfico
            options: {
                responsive: true,
                maintainAspectRatio: false,
                // Configuración adicional del gráfico puede añadirse aquí
            }
        });
    }

    willUnmount() {
        if (this.chart) {
            this.chart.destroy(); // Destruye el gráfico cuando el componente se desmonte
        }
    }
}

PolarAreaChartWidget.template = 'PolarAreaChartWidget'; // Asigna la plantilla del widget
PolarAreaChartWidget.props = standardFieldProps; // Asigna las propiedades estándar de los campos
PolarAreaChartWidget.supportedTypes = ["char", "text"]; // Define los tipos de campos soportados (ajustar según sea necesario)

registry.category("fields").add("polar_area_chart_widget", PolarAreaChartWidget); // Añade el widget al registro de campos de Odoo
