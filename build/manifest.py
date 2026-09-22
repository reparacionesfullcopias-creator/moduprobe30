# manifest.py - congelado 3.0 - placa PICO_MP30
# iter1 (21-22/09): 4 modulos 3.0-B. iter2 (22/09): + los 10
# modulos de disco (regla del propietario, NOTA 22/09-II: los .py
# que queden en disco pasan a .mpy; se ejecuta como congelado).
# services.py lleva el hardening MODULOS_RESERVADOS (+7 nombres,
# FICHA_ITER2 seccion 9).
# include() primero: sin _boot.py no hay filesystem (fix critico
# 22/09 de iter1; sintaxis y nota de nombres completos: ver
# comentarios de iter1 y tools/manifestfile.py).

include("$(PORT_DIR)/boards/manifest.py")

# iter1
freeze('.', 'fontbase3.py')
freeze('.', 'fontnokia3.py')
freeze('.', 'fontarcade3.py')
freeze('.', 'writer3.py')

# iter2
freeze('.', 'buttons_pio.py')
freeze('.', 'fonts.py')
freeze('.', 'fpsmeter.py')
freeze('.', 'gpu.py')
freeze('.', 'log.py')
freeze('.', 'screens.py')
freeze('.', 'sd_browse.py')
freeze('.', 'sdcard.py')
freeze('.', 'services.py')
freeze('.', 'st7567.py')
