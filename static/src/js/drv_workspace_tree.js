/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";

const drvExpandedKeys = new Set();
const drvCollapsedKeys = new Set();
let _drvPreventAutoExpand = null;

function getDepth(row) {
    const cell = row.querySelector('td[name="depth"], td[data-name="depth"]');
    if (!cell) return 0;
    const inner = cell.querySelector('span, div');
    const text = (inner || cell).textContent.trim();
    const n = parseInt(text, 10);
    return isNaN(n) ? 0 : n;
}

function rowDepth(row) {
    return row?._drv?.depth ?? getDepth(row);
}

function rowKey(row, normalizedDepth) {
    const nameCell = row.querySelector('td[name="name"], td[data-name="name"]');
    const depth = normalizedDepth ?? rowDepth(row);
    return `drv::${depth}::${nameCell ? nameCell.textContent.trim() : row.rowIndex}`;
}

function isRowSelected(row) {
    const currentBtn = row.querySelector('button.gd_ws_open_btn.is_current');
    if (currentBtn && !currentBtn.classList.contains('o_invisible_modifier')) return true;
    return row.classList.contains('o_decoration_bf') || row.classList.contains('o_decoration_info');
}

function buildTree(container) {
    const rows = Array.from(container.querySelectorAll('tbody tr.o_data_row'));
    if (!rows.length) return;

    const rawDepths = rows.map(getDepth);
    const minDepth = rawDepths.length ? Math.min(...rawDepths) : 0;

    rows.forEach((row, i) => {
        const depth = Math.max(getDepth(row) - minDepth, 0);
        row._drv = {
            depth,
            index: i,
            key: rowKey(row, depth),
            hasChildren: false,
            selected: isRowSelected(row),
        };
    });

    rows.forEach((row, i) => {
        const next = rows[i + 1];
        row._drv.hasChildren = !!(next && next._drv.depth > row._drv.depth);
    });

    container.querySelectorAll('.drv_ws_toggle').forEach(t => t.remove());

    rows.forEach(row => {
        row.classList.remove('gd_row_selected');
        row.style.display = '';

        const { depth, hasChildren, key, selected } = row._drv;
        const iconCell = Array.from(row.querySelectorAll('td')).find(
            td => td.querySelector('button.gd_ws_open_btn'));

        if (!iconCell) return;

        iconCell.style.paddingLeft = `${depth * 18 + 4}px`;

        const t = document.createElement('span');
        t.className = 'gd_ws_toggle drv_ws_toggle' + (hasChildren ? '' : ' gd_ws_toggle_empty');
        t.innerHTML = '&#9654;';
        t.style.marginRight = '8px';
        t.style.cursor = hasChildren ? 'pointer' : 'default';
        t.style.display = 'inline-block';
        t.style.transition = 'transform 0.2s';
        t.style.color = hasChildren ? '#444' : 'transparent';

        if (hasChildren) {
            t.addEventListener('mousedown', e => {
                e.preventDefault();
                e.stopPropagation();
                if (drvExpandedKeys.has(key)) {
                    drvExpandedKeys.delete(key);
                    drvCollapsedKeys.add(key);
                    _drvPreventAutoExpand = key;
                } else {
                    drvExpandedKeys.add(key);
                    drvCollapsedKeys.delete(key);
                    if (_drvPreventAutoExpand === key) _drvPreventAutoExpand = null;
                }
                applyVisibility(rows);
            });
        }

        iconCell.insertBefore(t, iconCell.firstChild);
        if (selected) row.classList.add('gd_row_selected');
    });

    const selIdx = rows.findIndex(r => r._drv.selected);
    if (selIdx >= 0) {
        const sel = rows[selIdx];
        if (sel._drv.hasChildren && !drvCollapsedKeys.has(sel._drv.key) && sel._drv.key !== _drvPreventAutoExpand) {
            drvExpandedKeys.add(sel._drv.key);
        }
        let need = sel._drv.depth - 1;
        for (let i = selIdx - 1; i >= 0 && need >= 0; i--) {
            if (rows[i]._drv.depth === need && rows[i]._drv.hasChildren) {
                drvExpandedKeys.add(rows[i]._drv.key);
                need--;
            }
        }
    }

    // La carpeta raíz de Informes/Comunicaciones debe verse como nodo principal
    // desplegable por defecto, pero respetando si el usuario la contrae.
    rows.forEach(row => {
        if (row._drv.depth === 0 && row._drv.hasChildren && !drvCollapsedKeys.has(row._drv.key)) {
            drvExpandedKeys.add(row._drv.key);
        }
    });

    applyVisibility(rows);
}

function applyVisibility(rows) {
    rows.forEach(row => {
        const { depth, key } = row._drv;
        row.style.display = (depth === 0 || isVisible(rows, row)) ? '' : 'none';
        const t = row.querySelector('.drv_ws_toggle:not(.gd_ws_toggle_empty)');
        if (t) t.style.transform = drvExpandedKeys.has(key) ? 'rotate(90deg)' : 'rotate(0deg)';
    });
}

function isVisible(rows, row) {
    let currentDepth = row._drv.depth;
    const idx = row._drv.index;
    for (let i = idx - 1; i >= 0; i--) {
        if (rows[i]._drv.depth === currentDepth - 1) {
            if (!drvExpandedKeys.has(rows[i]._drv.key)) return false;
            currentDepth--;
            if (currentDepth <= 0) break;
        }
    }
    return true;
}

// ── Dropdown fix: posiciona el menú junto al botón real de tres puntos ──
document.addEventListener('shown.bs.dropdown', (e) => {
    const dropdown = e.target.closest?.('.gd_folder_menu, .gd_file_menu');
    if (!dropdown) return;
    // En Informes/Comunicaciones el contenedor ya deja espacio; no forzar fixed
    // porque el menú salta hacia la barra superior y se ve fuera de la tarjeta.
    if (dropdown.closest('.infcom-card-container')) {
        const menu = dropdown.querySelector('.dropdown-menu');
        if (menu) menu.removeAttribute('style');
        return;
    }
    const toggle = dropdown.querySelector('.dropdown-toggle');
    if (!toggle) return;
    // setTimeout(0) espera a que Bootstrap/Popper terminen de posicionar
    setTimeout(() => {
        const menu = dropdown.querySelector('.dropdown-menu');
        if (!menu || !menu.classList.contains('show')) return;

        const rect = toggle.getBoundingClientRect();
        const topBar = document.querySelector('.o_main_navbar');
        const viewportTop = Math.max(
            8,
            topBar ? topBar.getBoundingClientRect().bottom + 8 : 8
        );
        const viewportBottom = window.innerHeight - 8;
        const viewportRight = window.innerWidth - 8;
        const availableHeight = Math.max(120, viewportBottom - viewportTop);
        const menuH = Math.min(menu.scrollHeight || menu.offsetHeight || 260, availableHeight);
        const menuW = menu.offsetWidth || 200;
        const spaceBelow = viewportBottom - rect.bottom;
        const spaceAbove = rect.top - viewportTop;
        const preferredTop = (spaceBelow < menuH && spaceAbove > spaceBelow)
            ? rect.top - menuH - 4
            : rect.bottom + 4;
        const top = Math.min(
            Math.max(viewportTop, preferredTop),
            viewportBottom - menuH
        );
        const left = Math.min(
            Math.max(8, rect.right - menuW),
            viewportRight - menuW
        );

        // Limpiar todo lo que puso Popper y aplicar fixed
        menu.style.cssText = '';
        menu.style.setProperty('position', 'fixed', 'important');
        // Sobre las tarjetas y paneles, pero debajo de la barra superior.
        menu.style.setProperty('z-index',  '900',  'important');
        menu.style.top       = top + 'px';
        menu.style.left      = left + 'px';
        menu.style.right     = 'auto';
        menu.style.bottom    = 'auto';
        menu.style.transform = 'none';
        menu.style.margin    = '0';
        menu.style.maxHeight = availableHeight + 'px';
        menu.style.overflowY = 'auto';
        if ('inset' in menu.style) menu.style.inset = 'auto';
    }, 0);
});

document.addEventListener('hide.bs.dropdown', (e) => {
    const toggle = e.target;
    if (!toggle.closest('.gd_folder_menu, .gd_file_menu')) return;
    const menu = toggle.closest('.dropdown')?.querySelector('.dropdown-menu');
    if (menu) menu.style.cssText = '';
});

let _drvClickBusy = false;

document.addEventListener('click', e => {
    if (e.target.closest('.drv_ws_toggle'))                                return;
    if (_drvClickBusy)                                                     return;
    if (e.target.closest('button.gd_ws_open_btn, button.gd_ws_info_btn')) return;

    const cell = e.target.closest('.drv_workspace_tree td');
    if (!cell) return;

    const row = cell.closest('tr');
    const btn = row?.querySelector(
        'button.gd_ws_open_btn:not(.o_invisible_modifier):not([style*="display: none"])'
    );
    if (!btn) return;

    const key           = rowKey(row);
    const depth         = rowDepth(row);
    const alreadySelected = isRowSelected(row);

    const tbody = row.closest('tbody');
    let hasChildren = false;
    let allRows     = [];
    let rowIndex    = -1;
    if (tbody) {
        allRows  = Array.from(tbody.querySelectorAll('tr.o_data_row'));
        rowIndex = allRows.indexOf(row);
        if (rowIndex >= 0 && rowIndex + 1 < allRows.length) {
            hasChildren = rowDepth(allRows[rowIndex + 1]) > depth;
        }
    }

    if (hasChildren) {
        const childrenVisible = rowIndex + 1 < allRows.length &&
                                allRows[rowIndex + 1].style.display !== 'none';

        if (alreadySelected && childrenVisible) {
            drvExpandedKeys.delete(key);
            drvCollapsedKeys.add(key);
            _drvPreventAutoExpand = key;
            const container = row.closest('.drv_workspace_tree');
            if (container) buildTree(container);
            return;
        }

        drvExpandedKeys.add(key);
        drvCollapsedKeys.delete(key);
        if (_drvPreventAutoExpand === key) _drvPreventAutoExpand = null;

        let need = depth - 1;
        for (let i = rowIndex - 1; i >= 0 && need >= 0; i--) {
            const d     = rowDepth(allRows[i]);
            const nextD = (i + 1 < allRows.length) ? rowDepth(allRows[i + 1]) : -1;
            if (d === need) {
                if (nextD > d) drvExpandedKeys.add(rowKey(allRows[i]));
                need--;
            }
        }
    } else {
        if (_drvPreventAutoExpand !== null) _drvPreventAutoExpand = null;
    }

    // Limpiar búsqueda al navegar a otra carpeta
    _drvSidebarQuery = '';
    const _searchInput = document.querySelector('.drv_search_box_sidebar input');
    if (_searchInput) { _searchInput.value = ''; _searchInput._drvHooked = false; }

    _drvClickBusy = true;
    btn.click();
    _drvClickBusy = false;
}, true);

// Filtrado JS del sidebar — no genera round-trip al servidor
let _drvSidebarQuery = '';

function applyDrvSidebarSearch(container, query) {
    _drvSidebarQuery = query;
    const rows = Array.from(container.querySelectorAll('tbody tr.o_data_row'));
    if (!query) {
        rows.forEach(r => { r.style.display = ''; });
        buildTree(container);
        return;
    }
    const q = query.toLowerCase();
    const visibleRows = new Set();
    rows.forEach(row => {
        const nameCell = row.querySelector('td[name="name"], td[data-name="name"]');
        const text = nameCell ? nameCell.textContent.trim().toLowerCase() : '';
        if (text.includes(q)) {
            visibleRows.add(row);
            // mostrar ancestros
            let depth = rowDepth(row);
            if (depth > 0) {
                for (let i = rows.indexOf(row) - 1; i >= 0; i--) {
                    const d = rowDepth(rows[i]);
                    if (d < depth) { visibleRows.add(rows[i]); depth = d; }
                    if (depth === 0) break;
                }
            }
        }
    });
    rows.forEach(r => { r.style.display = visibleRows.has(r) ? '' : 'none'; });
}

function hookDrvSidebarSearch(container) {
    const input = document.querySelector('.drv_search_box_sidebar [name="workspace_search"] input, .drv_search_box_sidebar input');
    if (!input || input._drvHooked) return;
    input._drvHooked = true;
    input.addEventListener('input', () => applyDrvSidebarSearch(container, input.value));
    // Re-aplicar query guardada si existe
    if (_drvSidebarQuery) {
        input.value = _drvSidebarQuery;
        applyDrvSidebarSearch(container, _drvSidebarQuery);
    }
}

patch(FormController.prototype, "Matriz.DrvWorkspaceTree", {
    setup() {
        this._super(...arguments);

        const isMainBrowser = () => {
            const m = this.props.resModel || this.model?.config?.resModel;
            return ['drive.browser', 'informes_drive', 'comunicaciones_drive'].includes(m);
        };

        const trigger = () => {
            if (!isMainBrowser()) return;
            const containers = document.querySelectorAll('.drv_workspace_tree');
            if (!containers.length) return;
            containers.forEach(container => {
                buildTree(container);
                hookDrvSidebarSearch(container);
                if (_drvSidebarQuery) applyDrvSidebarSearch(container, _drvSidebarQuery);
            });
        };

        onMounted(() => {
            trigger();
        });

        onPatched(trigger);
    },
});
