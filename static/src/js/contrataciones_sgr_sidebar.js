/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";

const { onWillStart, onWillUpdateProps, useState } = owl;

class ContratacionesSgrRenderer extends KanbanRenderer {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.sgrPanel = useState({ balances: null, loading: false, error: false, collapsed: false });
        this.sgrRequestId = 0;
        this.sgrLoadedProjectId = null;
        onWillStart(() => this.loadSgrBalances());
        onWillUpdateProps((nextProps) => this.loadSgrBalances(nextProps));
    }

    projectId(props = this.props) {
        return props.list.context.default_proyecto_relacionado || props.list.context.active_id;
    }

    async loadSgrBalances(props = this.props) {
        const projectId = this.projectId(props);
        const requestId = ++this.sgrRequestId;
        if (projectId !== this.sgrLoadedProjectId) {
            this.sgrPanel.balances = null;
        }
        this.sgrPanel.error = false;
        if (!projectId) {
            this.sgrPanel.loading = false;
            return;
        }
        this.sgrPanel.loading = !this.sgrPanel.balances;
        try {
            const balances = await this.orm.call(
                "contrataciones_sgr", "get_sgr_saldos_proyecto", [projectId]
            );
            if (requestId === this.sgrRequestId) {
                this.sgrPanel.balances = balances;
                this.sgrLoadedProjectId = projectId;
            }
        } catch (error) {
            if (requestId === this.sgrRequestId) {
                this.sgrPanel.error = true;
            }
        } finally {
            if (requestId === this.sgrRequestId) {
                this.sgrPanel.loading = false;
            }
        }
    }

    formatSgrAmount(amount) {
        return (amount || 0).toLocaleString("es-CO", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    hasSgrAmount(amount) {
        return Math.abs(amount || 0) >= 0.005;
    }

    hasVisibleSgrAmounts() {
        const balances = this.sgrPanel.balances;
        return balances && [
            balances.es_produccion,
            balances.produccion_utilizada,
            balances.es_personal_fuv,
            balances.contratos_vco,
        ].some((amount) => this.hasSgrAmount(amount));
    }
}

registry.category("views").add("contrataciones_sgr_kanban", {
    ...kanbanView,
    Renderer: ContratacionesSgrRenderer,
});
