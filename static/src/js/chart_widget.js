/* @odoo-module */
import { registry } from "@web/core/registry"; // Importa el registro de módulos de Odoo
import { standardFieldProps } from "@web/views/fields/standard_field_props"; // Importa las propiedades estándar de los campos
const { Component, useRef, onMounted, onPatched } = owl; // Importa funciones y componentes necesarios de OWL

class ChartWidget extends Component { // Define la clase ChartWidget que extiende Component
    setup() {
        this.root = useRef('ChartWidget-root'); // Crea una referencia para el elemento raíz del widget
        onMounted(this.renderChart.bind(this)); // Vincula la función renderChart para ejecutarse cuando el componente se monte
        onPatched(this.renderChart.bind(this)); // Vincula la función renderChart para ejecutarse cuando el componente se actualice
    }

    renderChart() {
        if (this.chart) {
            this.chart.destroy(); // Destruye el gráfico existente si hay uno
        }

        const ctx = this.root.el.querySelector('.chart-canvas').getContext('2d'); // Obtiene el contexto 2D del canvas donde se renderizará el gráfico
        const parsedValue = this.props.value ? JSON.parse(this.props.value) : { labels: [], datasets: [] };
        // `scales` es opcional: si el payload lo trae (ej. un combo con eje Y secundario a la
        // derecha), se agrega a las opciones del gráfico; si no viene, se mantiene el comportamiento
        // histórico (un solo eje, tal como lo decide Chart.js por defecto).
        const scales = parsedValue.scales || undefined;
        const { scales: _omitScales, ...data } = parsedValue;

        this.chart = new Chart(ctx, { // Crea una nueva instancia de Chart.js con los datos proporcionados
            type: 'bar', // Tipo de gráfico (puede ser cambiado según se necesite)
            data: data, // Datos para el gráfico
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: scales,
                plugins: {
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

ChartWidget.template = 'ChartWidget'; // Asigna la plantilla del widget
ChartWidget.props = standardFieldProps; // Asigna las propiedades estándar de los campos
ChartWidget.supportedTypes = ["char", "text"]; // Define los tipos de campos soportados (ajustar según sea necesario)

registry.category("fields").add("chart_widget", ChartWidget); // Añade el widget al registro de campos de Odoo
