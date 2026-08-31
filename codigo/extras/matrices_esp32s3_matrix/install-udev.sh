#!/bin/bash
# Instala las reglas udev de las matrices DPC. Requiere root (pkexec o sudo).
#
# La ruta se deduce de la ubicacion del propio script, para que funcione
# igual en la laptop que en la Pi 5 sin editar nada.
set -e
RULES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/99-microscopeos-matriz.rules"

install -m 0644 -o root -g root "$RULES" /etc/udev/rules.d/99-microscopeos-matriz.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty

echo "reglas instaladas en /etc/udev/rules.d/"
sleep 1
ls -l /dev/matriz_cam* 2>/dev/null || echo "AVISO: los symlinks no aparecieron todavia"
