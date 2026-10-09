odoo.define('Matriz.gd_breadcrumb_widget', function (require) {
    'use strict';

    // Read the breadcrumb JSON from the hidden field widget
    function readBreadcrumbJson() {
        var fieldDiv = document.querySelector('[name="breadcrumb_json"]');
        if (!fieldDiv) return [];
        var textarea = fieldDiv.querySelector('textarea');
        var raw = (textarea ? textarea.value : fieldDiv.textContent || '').trim();
        if (!raw || raw === '[]') return [];
        try { return JSON.parse(raw); } catch (e) { return []; }
    }

    function applyBreadcrumb() {
        try {
            if (!document.querySelector('.gd_browser_form')) return;

            var data = readBreadcrumbJson();
            if (!data.length) return;

            var currentPath = data.map(function(c) { return c.id + ':' + c.name; }).join('/');

            var allActive = document.querySelectorAll(
                '.o_control_panel .breadcrumb-item.active, .o_breadcrumb .active'
            );
            if (!allActive.length) return;
            var activeCrumb = allActive[allActive.length - 1];

            if (activeCrumb.getAttribute('data-gd-path') === currentPath) return;
            activeCrumb.setAttribute('data-gd-path', currentPath);
            activeCrumb.innerHTML = '';

            data.forEach(function(crumb, index) {
                var isLast = index === data.length - 1;

                if (isLast) {
                    var span = document.createElement('span');
                    span.textContent = crumb.name;
                    activeCrumb.appendChild(span);
                } else {
                    // clickable ancestor — handled by archivo_central_drop.js
                    var link = document.createElement('a');
                    link.textContent = crumb.name;
                    link.href = '#';
                    link.className = 'gd_breadcrumb_link';
                    link.setAttribute('data-ws-id', crumb.id);
                    link.style.cursor = 'pointer';
                    link.style.textDecoration = 'none';
                    link.addEventListener('mouseenter', function() { link.style.textDecoration = 'underline'; });
                    link.addEventListener('mouseleave', function() { link.style.textDecoration = 'none'; });
                    activeCrumb.appendChild(link);
                }

                if (index < data.length - 1) {
                    var sep = document.createElement('span');
                    sep.textContent = ' / ';
                    sep.style.color = '#6c757d';
                    sep.style.margin = '0 3px';
                    activeCrumb.appendChild(sep);
                }
            });

        } catch (e) {
            console.error('[GD Breadcrumb]', e);
        }
    }

    $(document).ready(function() {
        setInterval(applyBreadcrumb, 300);

        $(document).on('click', '.gd_breadcrumb_link', function(ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var crumbName = this.textContent.trim();
            var rows = document.querySelectorAll('.gd_workspace_tree tbody tr.o_data_row');
            for (var i = 0; i < rows.length; i++) {
                var nameCell = rows[i].querySelector('td[name="name"], td[data-name="name"]');
                if (nameCell && nameCell.textContent.trim() === crumbName) {
                    var btn = rows[i].querySelector(
                        'button.gd_ws_open_btn:not(.o_invisible_modifier):not([style*="display: none"])'
                    );
                    if (btn) btn.click();
                    break;
                }
            }
        });
    });
});
