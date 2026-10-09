"""Presentación compartida para los correos del trámite de pólizas."""

from markupsafe import Markup, escape


def plantilla_correo_polizas(titulo, contenido):
    """Envuelve contenido HTML seguro con la tarjeta usada en Contrataciones FUV.

    Las tablas y los estilos en línea mantienen la presentación en clientes de
    correo que descartan CSS externo, incluidos Gmail y Outlook.
    """
    return Markup(
        '<table role="presentation" width="100%%" cellspacing="0" cellpadding="0" '
        'style="width:100%%;background:#f3f6fa;padding:24px 12px;'
        'font-family:Arial,sans-serif;color:#263238">'
        '<tr><td align="center">'
        '<table role="presentation" width="620" cellspacing="0" cellpadding="0" '
        'style="width:100%%;max-width:620px;background:#ffffff;border:1px solid #dbe3ef;'
        'border-radius:12px;overflow:hidden">'
        '<tr><td style="padding:22px 26px;background:#043484;color:#ffffff">'
        '<div style="font-size:24px;font-weight:700">&#128276; %s</div>'
        '</td></tr>'
        '<tr><td style="padding:26px;line-height:1.55">%s</td></tr>'
        '<tr><td style="padding:14px 26px;background:#f8fafc;'
        'border-top:1px solid #e5eaf1;color:#64748b;font-size:11px;'
        'text-align:center">Este es un mensaje automático.</td></tr>'
        '</table></td></tr></table>'
    ) % (escape(titulo), escape(contenido))
