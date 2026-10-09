FROM odoo:16

# Cambiamos temporalmente a usuario root para poder hacer instalaciones
USER root

# Copiamos tu archivo de requerimientos dentro del contenedor
COPY ./requirements.txt /opt/requirements.txt

# Actualizamos pip, instalamos/actualizamos pyOpenSSL, cryptography y PyPDF2 con versiones compatibles ignorando las del sistema, y luego instalamos los demás requerimientos
RUN pip3 install --no-cache-dir --upgrade pip     && pip3 install --no-cache-dir --upgrade --ignore-installed "pyOpenSSL<=22.0.0" "cryptography<=38.0.4" PyPDF2     && pip3 install --no-cache-dir -r /opt/requirements.txt

# Volvemos a cambiar al usuario odoo por seguridad
USER odoo
