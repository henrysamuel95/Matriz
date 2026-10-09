/** @odoo-module **/

(function () {
    'use strict';

    var pollTimer = null;
    var currentRid = 0;
    var latestContractorMessageId = 0;
    var retryTimer = null;
    var consecutiveFailures = 0;

    // Extrae el ID del registro del campo portal_chat_rid que Odoo renderiza en el DOM
    function getRecordId() {
        // Método principal: leer el campo portal_chat_rid del form view
        var ridEl = document.querySelector('.o_field_widget[name="portal_chat_rid"], [name="portal_chat_rid"]');
        if (ridEl) {
            var input = ridEl.querySelector('input');
            if (input) {
                var nInput = parseInt((input.value || '').trim(), 10);
                if (nInput > 0) return nInput;
            }
            var span = ridEl.querySelector('span');
            if (span) {
                var n = parseInt(span.textContent, 10);
                if (n > 0) return n;
            }
            var textRid = parseInt((ridEl.textContent || ridEl.value || '').trim(), 10);
            if (textRid > 0) return textRid;
        }
        // Fallback: data-rid si t-att-data-rid funcionó
        var c = document.getElementById('bchat_msgs_live');
        if (c) {
            var rid = parseInt(c.getAttribute('data-rid') || '0', 10);
            if (rid > 0) return rid;
        }
        // Fallback: algunas vistas de formulario dejan el id en data-res-id/data-id
        var form = document.querySelector('.o_form_view[data-res-id], .o_form_view[data-id]');
        if (form) {
            var formRid = parseInt(form.getAttribute('data-res-id') || form.getAttribute('data-id') || '0', 10);
            if (formRid > 0) return formRid;
        }
        // Fallback: URL path (Odoo 16: /odoo/model-name/42)
        var parts = window.location.pathname.split('/').filter(Boolean);
        for (var i = parts.length - 1; i >= 0; i--) {
            var nUrl = parseInt(parts[i], 10);
            if (!isNaN(nUrl) && String(nUrl) === parts[i] && nUrl > 0) return nUrl;
        }
        // Fallback: hash (Odoo 14/16: #id=42, #model=x&id=42, etc.)
        var hm = window.location.hash.match(/(?:^|[#?&])id=(\d+)/);
        return hm ? parseInt(hm[1], 10) : 0;
    }

    function esc(s) {
        var d = document.createElement('span');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function renderMessages(messages, container) {
        if (!messages || !messages.length) {
            container.innerHTML = '<div style="text-align:center;color:#bbb;padding:20px;font-size:13px;">' +
                '¡Aún no hay mensajes! El contratista puede escribir desde el portal.</div>';
            return;
        }
        var html = '';
        messages.forEach(function (m) {
            var isInt = m.sender_type === 'interno';
            var align = isInt ? 'flex-end' : 'flex-start';
            var bg    = isInt ? '#043484' : '#e8efff';
            var col   = isInt ? '#fff'    : '#1a2340';
            var nc    = isInt ? '#888'    : '#043484';
            var br    = isInt ? 'border-bottom-right-radius:4px' : 'border-bottom-left-radius:4px';
            html += '<div style="display:flex;flex-direction:column;align-items:' + align + ';margin-bottom:10px;">';
            html += '<span style="font-size:11px;font-weight:700;color:' + nc + ';margin-bottom:3px;">' + esc(m.sender_name) + '</span>';
            html += '<div style="background:' + bg + ';color:' + col + ';padding:10px 14px;border-radius:14px;' + br +
                    ';max-width:75%;word-break:break-word;font-size:13px;line-height:1.5;">' + esc(m.body) + '</div>';
            html += '<span style="font-size:10px;color:#aaa;margin-top:3px;">' + esc(m.date) + '</span>';
            html += '</div>';
        });
        container.innerHTML = html;
        container.dataset.bchatHasRendered = '1';
        container.scrollTop = container.scrollHeight;
    }

    function getSeenKey() {
        return currentRid ? 'bchat:last_seen_contractor:' + currentRid : '';
    }

    function getLastSeenContractorId() {
        var key = getSeenKey();
        if (!key) return 0;
        return parseInt(window.localStorage.getItem(key) || '0', 10) || 0;
    }

    function markContractorMessagesSeen() {
        var key = getSeenKey();
        if (key && latestContractorMessageId) {
            window.localStorage.setItem(key, String(latestContractorMessageId));
        }
    }

    function getLatestContractorMessageId(messages) {
        var latest = 0;
        (messages || []).forEach(function (m) {
            if (m.sender_type === 'contratista') {
                latest = Math.max(latest, parseInt(m.id || 0, 10) || 0);
            }
        });
        return latest;
    }

    function openChat(markSeen) {
        var win = document.getElementById('bchat_win');
        var fab = document.getElementById('bchat_fab');
        if (win) win.style.display = 'flex';
        if (fab) fab.style.display = 'none';
        if (markSeen) markContractorMessagesSeen();
    }

    function closeChat() {
        var win = document.getElementById('bchat_win');
        var fab = document.getElementById('bchat_fab');
        if (win) win.style.display = 'none';
        if (fab) fab.style.display = 'flex';
        markContractorMessagesSeen();
    }

    function bindChatControls() {
        var closeBtn = document.getElementById('bchat_close');
        var fab = document.getElementById('bchat_fab');
        if (closeBtn && !closeBtn.dataset.bchatBound) {
            closeBtn.dataset.bchatBound = '1';
            closeBtn.addEventListener('click', closeChat);
        }
        if (fab && !fab.dataset.bchatBound) {
            fab.dataset.bchatBound = '1';
            fab.addEventListener('click', function () {
                openChat(true);
            });
        }
    }

    function openIfThereAreUnreadContractorMessages(messages) {
        latestContractorMessageId = getLatestContractorMessageId(messages);
        if (latestContractorMessageId > getLastSeenContractorId()) {
            openChat(false);
        }
    }

    function fetchAndRender() {
        var container = document.getElementById('bchat_msgs_live');
        if (!container || !currentRid) return;
        var freshRid = getRecordId();
        if (freshRid && freshRid !== currentRid) {
            currentRid = freshRid;
            latestContractorMessageId = 0;
        }
        fetch('/web/dataset/call_kw', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({
                jsonrpc: '2.0',
                method: 'call',
                id: Date.now(),
                params: {
                    model: 'acta_entrega_documentos',
                    method: 'get_chat_messages_json',
                    args: [currentRid],
                    kwargs: {
                        context: (window.odoo && window.odoo.session_info && window.odoo.session_info.user_context) || {}
                    }
                }
            })
        })
        .then(function (r) {
            if (!r.ok) {
                throw new Error('HTTP ' + r.status + ' al consultar el chat');
            }
            return r.json();
        })
        .then(function (d) {
            var c = document.getElementById('bchat_msgs_live');
            if (!c) return;
            if (d && d.error) {
                if (window.console && console.error) {
                    console.error('Error RPC al cargar el chat de soporte:', d.error);
                }
                throw new Error('RPC error al cargar el chat');
            }
            if (!d || !d.result) return;
            consecutiveFailures = 0;
            openIfThereAreUnreadContractorMessages(d.result);
            renderMessages(d.result, c);
        })
        .catch(function (error) {
            // Dejar rastro en consola ayuda a diagnosticar diferencias entre local y producción.
            if (window.console && console.warn) {
                console.warn('No se pudieron cargar los mensajes del chat de soporte.', error);
            }
            var c = document.getElementById('bchat_msgs_live');
            consecutiveFailures += 1;
            if (c && (!c.dataset.bchatHasRendered || consecutiveFailures >= 2)) {
                c.innerHTML = '<div style="text-align:center;color:#c0392b;padding:20px;font-size:13px;">' +
                    'No se pudieron cargar los mensajes del chat.</div>';
            }
        });
    }

    function startPoll(rid) {
        stopPoll();
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        currentRid = rid;
        latestContractorMessageId = 0;
        consecutiveFailures = 0;
        bindChatControls();
        fetchAndRender();
        pollTimer = setInterval(fetchAndRender, 5000);
    }

    function stopPoll() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
        currentRid = 0;
    }

    function showMissingRecordMessage() {
        var c = document.getElementById('bchat_msgs_live');
        if (c) {
            c.innerHTML = '<div style="text-align:center;color:#bbb;padding:20px;font-size:13px;">' +
                'No se pudo identificar el acta para cargar el chat.</div>';
        }
    }

    function tryStartPollWithRetry() {
        var rid = getRecordId();
        if (rid) {
            startPoll(rid);
            return;
        }
        showMissingRecordMessage();
        if (!retryTimer) {
            retryTimer = setTimeout(function () {
                retryTimer = null;
                if (!pollTimer && document.getElementById('bchat_msgs_live')) {
                    tryStartPollWithRetry();
                }
            }, 1000);
        }
    }

    // Detecta cuando #bchat_msgs_live aparece o desaparece en el DOM
    var observer = new MutationObserver(function () {
        var c = document.getElementById('bchat_msgs_live');
        if (c && !pollTimer) {
            // Pequeño delay para que OWL termine de parchear los atributos/valores del form
            setTimeout(function () {
                if (!pollTimer) {
                    tryStartPollWithRetry();
                }
            }, 300);
        } else if (!c && pollTimer) {
            stopPoll();
        }
    });

    function init() {
        if (!document.body) {
            setTimeout(init, 100);
            return;
        }
        observer.observe(document.body, { childList: true, subtree: true });
        bindChatControls();
        var c = document.getElementById('bchat_msgs_live');
        if (c) {
            tryStartPollWithRetry();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
