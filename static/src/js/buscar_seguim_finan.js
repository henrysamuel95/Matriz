/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { X2ManyField } from "@web/views/fields/x2many/x2many_field";
import { useService } from "@web/core/utils/hooks";

const { onWillStart, useState, onRendered, onWillUnmount } = owl;

patch(X2ManyField.prototype, "Matriz.SearchableO2M", {
    setup() {
        this._super(...arguments);

        this.orm = useService("orm");

        this.state = useState({
            searchQuery: "",
            selectedResponsable: "",
            availableResponsables: [],
            responsablesByActividad: {},
            allLineas: [],
            loading: true,
            stickyHeader: false,
            filteredTotal: 0,
            filteredPercentage: 0,
        });
        this.footerStatsRequest = 0;

        onWillStart(async () => {
            if (this._isSeguimientoFinancieraLineas()) {
                await this._loadResponsablesFromDB();
            }
        });

        onRendered(() => {
            if (this._isSeguimientoFinancieraLineas()) {
                this._applyClientFilter();
                requestAnimationFrame(() => this._setupFinancialTableTools());
            }
        });

        onWillUnmount(() => {
            this._destroyFinancialTableTools();
        });
    },

    _isSeguimientoFinancieraLineas() {
        const record = this.props && this.props.record;
        return this.props.name === "linea_ids"
            && record
            && record.resModel === "seguimiento_financiera";
    },

    _getParentResId() {
        return this.props && this.props.record && this.props.record.resId;
    },

    async _loadResponsablesFromDB() {
        try {
            const resId = this._getParentResId();
            if (!resId) {
                this.state.loading = false;
                return;
            }

            const resumen = await this.orm.call(
                "seguimiento_financiera_linea",
                "obtener_filtro_resumen",
                [resId, "", ""]
            );

            const lineas = await this.orm.searchRead(
                "seguimiento_financiera_linea",
                [["seguimiento_id", "=", resId]],
                [
                    "semana",
                    "actividad",
                    "estado",
                    "aprobado",
                    "fecha_cronograma",
                    "fecha_realizado",
                    "observaciones_financiera",
                    "observacion_final",
                    "responsables_ids",
                ],
                { limit: false }
            );

            const userIds = new Set();
            lineas.forEach((linea) => {
                (linea.responsables_ids || []).forEach((id) => userIds.add(id));
            });

            const usersById = {};
            if (userIds.size) {
                const users = await this.orm.read("res.users", Array.from(userIds), ["display_name"]);
                users.forEach((user) => {
                    usersById[user.id] = user.display_name || "";
                });
            }

            const responsables = new Set();
            const responsablesByActividad = {};
            const allLineas = lineas.map((linea) => {
                const names = (linea.responsables_ids || [])
                    .map((id) => usersById[id])
                    .filter(Boolean);
                const responsablesText = names.join(" ");

                names.forEach((name) => responsables.add(name));
                if (linea.actividad) {
                    responsablesByActividad[this._normalizeSearch(linea.actividad)] = responsablesText;
                }
                return {
                    ...linea,
                    responsablesText,
                    searchText: this._normalizeSearch([
                        linea.semana,
                        linea.actividad,
                        linea.estado,
                        linea.fecha_cronograma,
                        linea.fecha_realizado,
                        linea.observaciones_financiera,
                        linea.observacion_final,
                        responsablesText,
                    ].join(" ")),
                };
            });

            this.state.availableResponsables = (resumen.responsables || Array.from(responsables))
                .sort((a, b) => a.localeCompare(b));
            this.state.responsablesByActividad = responsablesByActividad;
            this.state.allLineas = allLineas;
            this.state.loading = false;
        } catch (error) {
            console.error("Error cargando responsables:", error);
            this.state.loading = false;
        }
    },

    _normalizeSearch(value) {
        return String(value || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .trim();
    },

    _getUniqueResponsables() {
        return this.state.availableResponsables;
    },

    _onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        this._applyClientFilter();
    },

    _onResponsableChange(ev) {
        this.state.selectedResponsable = ev.target.value;
        this._applyClientFilter();
    },

    _clearSearch() {
        this.state.searchQuery = "";
        this.state.selectedResponsable = "";
        this._applyClientFilter();
    },

    _onExportClick() {
        const container = this._getContainer();
        if (container) {
            this._exportVisibleRowsToExcel(container);
        }
    },

    _getContainer() {
        return document.querySelector(`[name="${this.props.name}"]`);
    },

    _getHorizontalScroller(container) {
        const candidates = [
            container.querySelector(".o_list_renderer"),
            container.querySelector(".table-responsive"),
            container,
        ].filter(Boolean);
        return candidates.find((element) => element.scrollWidth > element.clientWidth)
            || candidates[0]
            || null;
    },

    _getTopScrollbar(container) {
        const parent = container && container.parentElement;
        return (parent && parent.querySelector(".o_sf_top_scroll"))
            || document.querySelector(".o_sf_top_scroll");
    },

    _setupFinancialTableTools() {
        const container = this._getContainer();
        if (!container) return;

        const scroller = this._getHorizontalScroller(container);
        const topScrollbar = this._getTopScrollbar(container);
        if (!scroller || !topScrollbar) return;

        const topContent = topScrollbar.querySelector(".o_sf_top_scroll_content");
        if (!topContent) return;

        if (this._financialScroller !== scroller) {
            this._destroyFinancialTableTools();
            this._financialScroller = scroller;
            this._financialTopScrollbar = topScrollbar;
            this._financialBottomScrollHandler = () => {
                if (!this._syncingFinancialScroll && this._financialTopScrollbar) {
                    this._syncingFinancialScroll = true;
                    this._financialTopScrollbar.scrollLeft = scroller.scrollLeft;
                    this._syncingFinancialScroll = false;
                }
            };
            scroller.addEventListener("scroll", this._financialBottomScrollHandler, { passive: true });
            this._financialResizeObserver = new ResizeObserver(() => {
                this._updateFinancialTopScrollbar();
            });
            this._financialResizeObserver.observe(scroller);
            const table = scroller.querySelector("table");
            if (table) this._financialResizeObserver.observe(table);
        }

        container.classList.toggle("o_sf_sticky_header", this.state.stickyHeader);
        this._updateFinancialTopScrollbar();
    },

    _updateFinancialTopScrollbar() {
        const scroller = this._financialScroller;
        const topScrollbar = this._financialTopScrollbar;
        if (!scroller || !topScrollbar) return;

        const topContent = topScrollbar.querySelector(".o_sf_top_scroll_content");
        if (!topContent) return;

        topContent.style.width = `${scroller.scrollWidth}px`;
        topScrollbar.classList.toggle(
            "o_sf_top_scroll--visible",
            scroller.scrollWidth > scroller.clientWidth + 1
        );
        topScrollbar.scrollLeft = scroller.scrollLeft;
    },

    _destroyFinancialTableTools() {
        if (this._financialScroller && this._financialBottomScrollHandler) {
            this._financialScroller.removeEventListener("scroll", this._financialBottomScrollHandler);
        }
        if (this._financialResizeObserver) {
            this._financialResizeObserver.disconnect();
        }
        this._financialScroller = null;
        this._financialTopScrollbar = null;
        this._financialBottomScrollHandler = null;
        this._financialResizeObserver = null;
    },

    _onTopScrollbarScroll(ev) {
        if (this._syncingFinancialScroll || !this._financialScroller) return;
        this._syncingFinancialScroll = true;
        this._financialScroller.scrollLeft = ev.currentTarget.scrollLeft;
        this._syncingFinancialScroll = false;
    },

    _onToggleStickyHeader() {
        this.state.stickyHeader = !this.state.stickyHeader;
        requestAnimationFrame(() => this._setupFinancialTableTools());
    },

    _getCellText(row, fieldName) {
        const cell = row.querySelector(`[name="${fieldName}"], [data-name="${fieldName}"]`);
        return cell ? cell.innerText : "";
    },

    _getRowResponsables(row) {
        const visibleResponsables = this._getCellText(row, "responsables_ids");
        if (visibleResponsables) {
            return visibleResponsables;
        }

        const actividad = this._normalizeSearch(this._getCellText(row, "actividad"));
        if (actividad && this.state.responsablesByActividad[actividad]) {
            return this.state.responsablesByActividad[actividad];
        }

        const rowText = this._normalizeSearch(row.innerText);
        for (const [actividadKey, responsables] of Object.entries(this.state.responsablesByActividad)) {
            if (actividadKey && rowText.includes(actividadKey)) {
                return responsables;
            }
        }
        return "";
    },

    _isRowVisible(row) {
        return row.style.display !== "none";
    },

    async _getBackendFilterStats() {
        const resId = this._getParentResId();
        if (!resId) {
            return null;
        }
        return await this.orm.call(
            "seguimiento_financiera_linea",
            "obtener_filtro_resumen",
            [resId, this.state.searchQuery, this.state.selectedResponsable]
        );
    },

    _updateFilterStats(total, porcentaje) {
        if (this.state.filteredTotal !== total) {
            this.state.filteredTotal = total;
        }
        if (this.state.filteredPercentage !== porcentaje) {
            this.state.filteredPercentage = porcentaje;
        }
    },

    _exportVisibleRowsToExcel(container) {
        const headers = Array.from(container.querySelectorAll("thead th"))
            .filter((th) => !th.classList.contains("o_list_record_selector") && !th.classList.contains("o_handle_cell"))
            .map((th) => th.innerText.trim() || th.getAttribute("data-name") || "");

        const rows = Array.from(container.querySelectorAll("tbody tr.o_data_row"))
            .filter((row) => this._isRowVisible(row))
            .map((row) => Array.from(row.querySelectorAll("td"))
                .filter((td) => !td.classList.contains("o_list_record_selector") && !td.classList.contains("o_handle_cell"))
                .map((td) => td.innerText.trim())
            );

        if (!rows.length) {
            return;
        }

        const escapeCsv = (value) => {
            const text = String(value || "").replace(/\r?\n|\r/g, " ").trim();
            return `"${text.replace(/"/g, '""')}"`;
        };
        const csv = [headers, ...rows]
            .map((row) => row.map(escapeCsv).join(";"))
            .join("\r\n");

        const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "seguimiento_financiera_filtrado.csv";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    },

    async _applyClientFilter() {
        const query = this._normalizeSearch(this.state.searchQuery);
        const respFilter = this._normalizeSearch(this.state.selectedResponsable);
        const container = this._getContainer();
        if (!container) return;

        const rows = container.querySelectorAll(".o_data_row");
        let totalVisibles = 0;
        let totalCompletadas = 0;

        rows.forEach((row) => {
            const rowText = this._normalizeSearch(row.innerText);
            const responsablesText = this._normalizeSearch(this._getRowResponsables(row));
            const matchesSearch = !query || rowText.includes(query) || responsablesText.includes(query);
            const matchesResp = !respFilter || responsablesText.includes(respFilter);

            if (matchesSearch && matchesResp) {
                row.style.setProperty("display", "", "important");
                totalVisibles++;
                if (rowText.includes("completo")) {
                    totalCompletadas++;
                }
            } else {
                row.style.setProperty("display", "none", "important");
            }
        });

        const porcentajePagina = totalVisibles ? Math.round((totalCompletadas / totalVisibles) * 100) : 0;

        const requestId = ++this.footerStatsRequest;
        try {
            const stats = await this._getBackendFilterStats();
            if (requestId !== this.footerStatsRequest) {
                return;
            }
            if (stats) {
                this._updateFilterStats(stats.total || 0, stats.porcentaje || 0);
            } else {
                this._updateFilterStats(totalVisibles, porcentajePagina);
            }
        } catch (error) {
            console.error("Error calculando avance global de seguimiento financiera:", error);
            if (requestId === this.footerStatsRequest) {
                this._updateFilterStats(totalVisibles, porcentajePagina);
            }
        }
    },
});
