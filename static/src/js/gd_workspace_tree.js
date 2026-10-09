/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";

// ── Estado de expansión: sobrevive re-renders de OWL ─────────────────────────
const expandedKeys = new Set();

// Cuando el usuario colapsa deliberadamente una carpeta seleccionada,
// guardamos su key aquí para que autoExpandPath no la vuelva a abrir.
let _preventAutoExpand = null;

// ── Helpers de DOM ────────────────────────────────────────────────────────────
function getDepth(row) {
    const cell = row.querySelector('td[name="depth"], td[data-name="depth"]');
    if (!cell) return 0;
    const inner = cell.querySelector('span, div');
    const text = (inner || cell).textContent.trim();
    const n = parseInt(text, 10);
    return isNaN(n) ? 0 : n;
}

function rowKey(row) {
    const nameCell = row.querySelector('td[name="name"], td[data-name="name"]');
    const depth = getDepth(row);
    return `${depth}::${nameCell ? nameCell.textContent.trim() : row.rowIndex}`;
}

function isRowSelected(row) {
    const currentBtn = row.querySelector('button.gd_ws_open_btn.is_current');
    if (currentBtn && !currentBtn.classList.contains('o_invisible_modifier')) return true;
    return row.classList.contains('o_decoration_bf') || row.classList.contains('o_decoration_info');
}

function getIconCell(row) {
    return Array.from(row.querySelectorAll('td'))
        .find(td => td.querySelector('button.gd_ws_open_btn')) || null;
}

// ── Acordeón principal ────────────────────────────────────────────────────────
function buildTree(container) {
    const rows = Array.from(container.querySelectorAll('tbody tr.o_data_row'));
    if (!rows.length) return;

    // Calcular jerarquías
    rows.forEach((row, i) => {
        row._gd = {
            depth: getDepth(row),
            index: i,
            key: rowKey(row),
            hasChildren: false,
            selected: isRowSelected(row),
        };
    });

    rows.forEach((row, i) => {
        const next = rows[i + 1];
        row._gd.hasChildren = !!(next && next._gd.depth > row._gd.depth);
    });

    // Limpiar toggles viejos
    container.querySelectorAll('.gd_ws_toggle').forEach(t => t.remove());

    rows.forEach(row => {
        row.classList.remove('gd_row_selected');
        row.style.display = '';

        const { depth, hasChildren, key, selected } = row._gd;
        const iconCell = Array.from(row.querySelectorAll('td')).find(td => td.querySelector('button.gd_ws_open_btn'));
        
        if (!iconCell) return;

        iconCell.style.paddingLeft = `${depth * 18 + 4}px`;

        const t = document.createElement('span');
        t.className = 'gd_ws_toggle' + (hasChildren ? '' : ' gd_ws_toggle_empty');
        t.innerHTML = '&#9654;';
        t.style.marginRight = '8px';
        t.style.cursor = hasChildren ? 'pointer' : 'default';
        t.style.display = 'inline-block';
        t.style.transition = 'transform 0.2s';
        t.style.color = hasChildren ? '#444' : 'transparent'; // Oculta la flecha si no hay hijos, pero respeta el espacio

        if (hasChildren) {
            // Mousedown en lugar de click para ejecutarse antes de que Odoo bloquee el evento
            t.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
                if (expandedKeys.has(key)) {
                    expandedKeys.delete(key);
                    _preventAutoExpand = key;
                } else {
                    expandedKeys.add(key);
                    if (_preventAutoExpand === key) _preventAutoExpand = null;
                }
                applyVisibility(rows);
            });
        }

        iconCell.insertBefore(t, iconCell.firstChild);
        if (selected) row.classList.add('gd_row_selected');
    });

    const selIdx = rows.findIndex(r => r._gd.selected);
    if (selIdx >= 0) {
        const sel = rows[selIdx];
        if (sel._gd.hasChildren && sel._gd.key !== _preventAutoExpand) {
            expandedKeys.add(sel._gd.key);
        }
        let need = sel._gd.depth - 1;
        for (let i = selIdx - 1; i >= 0 && need >= 0; i--) {
            if (rows[i]._gd.depth === need && rows[i]._gd.hasChildren) {
                expandedKeys.add(rows[i]._gd.key);
                need--;
            }
        }
    }

    applyVisibility(rows);
}
function autoExpandPath(rows, selIdx) {
    const sel = rows[selIdx];

    if (sel._gd.hasChildren && sel._gd.key !== _preventAutoExpand) {
        expandedKeys.add(sel._gd.key);
    }

    let need = sel._gd.depth - 1;
    for (let i = selIdx - 1; i >= 0 && need >= 0; i--) {
        if (rows[i]._gd.depth === need && rows[i]._gd.hasChildren) {
            expandedKeys.add(rows[i]._gd.key);
            need--;
        }
    }
}

function applyVisibility(rows) {
    rows.forEach(row => {
        const { depth, key } = row._gd;
        row.style.display = (depth === 0 || isVisible(rows, row)) ? '' : 'none';
        const t = row.querySelector('.gd_ws_toggle:not(.gd_ws_toggle_empty)');
        if (t) t.style.transform = expandedKeys.has(key) ? 'rotate(90deg)' : 'rotate(0deg)';
    });
}

function isVisible(rows, row) {
    let currentDepth = row._gd.depth;
    const idx = row._gd.index;
    for (let i = idx - 1; i >= 0; i--) {
        if (rows[i]._gd.depth === currentDepth - 1) {
            if (!expandedKeys.has(rows[i]._gd.key)) return false;
            currentDepth--; 
            if (currentDepth <= 0) break;
        }
    }
    return true;
}

// ── Click en fila ─────────────────────────────────────────────────────────────
// IMPORTANTE: No usar row._gd porque OWL puede haber creado nuevos <tr> después
// del último buildTree, dejando _gd indefinido. Calculamos todo del DOM fresco.
let _gdClickBusy = false;

document.addEventListener('click', e => {
    if (e.target.closest('.gd_ws_toggle'))                               return;
    if (_gdClickBusy)                                                    return;
    if (e.target.closest('button.gd_ws_open_btn, button.gd_ws_info_btn')) return;

    const cell = e.target.closest('.gd_workspace_tree td');
    if (!cell) return;

    const row = cell.closest('tr');
    const btn = row?.querySelector(
        'button.gd_ws_open_btn:not(.o_invisible_modifier):not([style*="display: none"])'
    );
    if (!btn) return;

    // Calcular todo directo del DOM (sin depender de row._gd)
    const key            = rowKey(row);
    const depth          = getDepth(row);
    const alreadySelected = isRowSelected(row);

    // Determinar si tiene hijos comparando depth con el siguiente <tr>
    const tbody = row.closest('tbody');
    let hasChildren = false;
    let allRows     = [];
    let rowIndex    = -1;
    if (tbody) {
        allRows  = Array.from(tbody.querySelectorAll('tr.o_data_row'));
        rowIndex = allRows.indexOf(row);
        if (rowIndex >= 0 && rowIndex + 1 < allRows.length) {
            hasChildren = getDepth(allRows[rowIndex + 1]) > depth;
        }
    }

    if (hasChildren) {
        // Usar el estado real del DOM (más fiable que expandedKeys ante resets de OWL)
        const childrenVisible = rowIndex + 1 < allRows.length &&
                                allRows[rowIndex + 1].style.display !== 'none';

        if (alreadySelected && childrenVisible) {
            // ── COLAPSAR: 100% cliente, sin llamar btn.click() ──────────────
            // Si llamamos btn.click() con la misma carpeta ya seleccionada,
            // el servidor retorna True (no-op) y OWL no re-renderiza nunca.
            // Actuamos directamente sobre el DOM y salimos.
            expandedKeys.delete(key);
            _preventAutoExpand = key;
            const container = row.closest('.gd_workspace_tree');
            if (container) buildTree(container);
            return;
        }

        // ── EXPANDIR ────────────────────────────────────────────────────────
        expandedKeys.add(key);
        if (_preventAutoExpand === key) _preventAutoExpand = null;

        let need = depth - 1;
        for (let i = rowIndex - 1; i >= 0 && need >= 0; i--) {
            const d     = getDepth(allRows[i]);
            const nextD = (i + 1 < allRows.length) ? getDepth(allRows[i + 1]) : -1;
            if (d === need) {
                if (nextD > d) expandedKeys.add(rowKey(allRows[i]));
                need--;
            }
        }
    } else {
        if (_preventAutoExpand !== null) _preventAutoExpand = null;
    }

    _gdClickBusy = true;
    btn.click();
    _gdClickBusy = false;
}, true);

// ── Patch FormController ───────────────────────────────────────────────────────
patch(FormController.prototype, "Matriz.WorkspaceTree", {
    setup() {
        this._super(...arguments);

        const isGD = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return m === 'gestion.documental.browser';
        };

        const trigger = () => {
            if (!isGD()) return;
            const container = document.querySelector('.gd_workspace_tree');
            if (container) buildTree(container);
        };

        const reload = async () => {
            try { await this.model.root.load(); this.model.notify(); } catch (_) {}
        };

        onMounted(() => {
            trigger();
            if (!isGD()) return;
            this._gdVisibility = () => { if (!document.hidden) reload(); };
            this._gdInterval = setInterval(reload, 60000);
            document.addEventListener("visibilitychange", this._gdVisibility);
            // Timers tempranos para capturar cuando el hilo de sync termina
            this._gdSyncTimers = [2000, 5000, 10000, 20000].map(
                ms => setTimeout(reload, ms)
            );
        });

        onPatched(trigger);

        onWillUnmount(() => {
            if (!isGD()) return;
            document.removeEventListener("visibilitychange", this._gdVisibility);
            clearInterval(this._gdInterval);
            (this._gdSyncTimers || []).forEach(t => clearTimeout(t));
        });
    },
});
