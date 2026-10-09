/** @odoo-module **/ // Directiva que indica a Odoo que este archivo es un módulo JS moderno (OWL)

import { KanbanRenderer } from "@web/views/kanban/kanban_renderer"; // Importa la clase base del renderer de vistas Kanban de Odoo
import { useService } from "@web/core/utils/hooks"; // Importa el hook para acceder a servicios de Odoo (como ORM, notificaciones, etc.)
const { onWillStart, useState } = owl; // Extrae los hooks de OWL: onWillStart (ejecuta código antes de montar) y useState (estado reactivo)

export class ContratacionesFuvRenderer extends KanbanRenderer { // Define y exporta una clase que extiende el KanbanRenderer base
    setup() { // Método de inicialización del componente OWL (equivalente al constructor)
        super.setup(); // Llama al setup del padre (KanbanRenderer) para mantener su funcionalidad original
        this.orm = useService("orm"); // Inyecta el servicio ORM de Odoo para hacer consultas a la base de datos
        this.mtzStats = useState({ data: {}, loading: true, error: null }); // Crea un estado reactivo con: datos vacíos, carga activa y sin error

        onWillStart(async () => { // Registra una función asíncrona que se ejecuta antes de que el componente se monte
            await this.loadStats(); // Espera a que se carguen las estadísticas antes de renderizar
        });
    }

    async loadStats() { // Método asíncrono que carga los datos desde la base de datos
        try { // Inicia bloque de manejo de errores
            const proyectoId = this.props.list.context.active_id; // Obtiene el ID del proyecto activo desde el contexto de la vista
            
            console.log('[Sidebar] Proyecto ID:', proyectoId); // Muestra en consola el ID del proyecto para depuración

            if (!proyectoId) { // Verifica si no existe un ID de proyecto en el contexto
                console.warn('[Sidebar] No hay active_id en el contexto'); // Advierte en consola que no hay ID activo
                this.mtzStats.loading = false; // Desactiva el estado de carga
                return; // Sale del método sin cargar datos
            }

            const recordIds = await this.orm.search( // Busca en la BD y guarda los IDs de registros que cumplan los filtros
                "contrataciones_fuv", // Nombre del modelo Odoo donde se buscará
                [["proyecto_relacionado", "=", proyectoId]], // Filtro: registros cuyo proyecto_relacionado sea igual al ID activo
                { 
                    limit: 1, // Limita la búsqueda a un solo resultado
                    context: { ignore_role_restrictions: true } // Omite restricciones de rol para asegurar acceso al registro
                }
            );

            console.log('[Sidebar] Record IDs encontrados:', recordIds); // Muestra en consola los IDs encontrados

            if (recordIds && recordIds.length > 0) { // Verifica que se hayan encontrado registros
                const records = await this.orm.read( // Lee los campos específicos de los registros encontrados
                    "contrataciones_fuv", // Modelo del cual se leerán los registros
                    recordIds, // IDs de los registros a leer
                    ["saldo_disponible_produccion", "saldo_final_disponible", "saldo_disponible_personal_fuv", "suma_valor_total_contrato", "total_gastado_produccion_v2", "saldo_disponible_vco", "valor_utilizado_vco", "valor_utilizado_costos_fuv", "gasto_anticipos", "impacto_positivo_viaticos", "impacto_negativo_viaticos", "total_utilizado_viaticos", "recursos_liberados_cierre"] // Lista de campos a recuperar
                );

                console.log('[Sidebar] Datos leídos:', records); // Muestra en consola los datos recuperados

                if (records && records.length > 0) { // Verifica que la lectura retornó datos válidos
                    this.mtzStats.data = records[0]; // Guarda el primer (y único) registro en el estado reactivo
                }
            } else { // Si no se encontraron registros en la búsqueda
                console.warn('[Sidebar] No se encontró registro para proyecto:', proyectoId); // Advierte que no hay datos para ese proyecto
            }

            this.mtzStats.loading = false; // Desactiva el estado de carga al terminar exitosamente

        } catch (error) { // Captura cualquier error ocurrido durante la carga
            console.error('[Sidebar] Error cargando estadísticas:', error); // Muestra el error completo en consola
            this.mtzStats.error = error.message; // Guarda el mensaje de error en el estado reactivo
            this.mtzStats.loading = false; // Desactiva el estado de carga aunque haya fallado
        }
    }
}
