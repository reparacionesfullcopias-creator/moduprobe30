# manifest.py - esqueleto de congelado 3.0 (FASE B) - placa PICO_MP30
# MicroPython importa estos modulos desde la flash: sin coste de heap
# para su bytecode ni sus constantes (blob/tuplas viven en flash).
#
# Ubicacion final: dentro del directorio de la placa del build
# (ver README_BUILD.txt). Los 4 ficheros device/*.py se copian al
# mismo directorio que este manifest.py.
#
# 22/09: FIX CRITICO. Este manifest debe EMPEZAR incluyendo la
# cadena por defecto del puerto rp2 (asi lo hace RPI_PICO_W en su
# propio manifest). Sin ese include no se congelaba _boot.py, y
# _boot.py es quien monta el filesystem interno al arrancar
# (rp2.Flash() + VfsLfs2 + vfs.mount(fs, "/"); ademas necesita
# rp2.py, tambien congelado por la cadena por defecto).
# Sintoma visto en hardware: os.listdir() -> [] (raiz virtual
# vacia, NO un disco formateado) y OSError: ENODEV al escribir
# (Thonny no podia subir ficheros). Los ficheros del usuario
# NUNCA se tocaron: no habia filesystem montado que danar.

include("$(PORT_DIR)/boards/manifest.py")

# 21/09: en v1.29.0 freeze() exige el nombre COMPLETO del fichero
# (con .py). Verificado en tools/manifestfile.py: _search usa el
# nombre tal cual y _add_file hace os.stat sin anadir extension.

freeze('.', 'fontbase3.py')
freeze('.', 'fontnokia3.py')
freeze('.', 'fontarcade3.py')
freeze('.', 'writer3.py')
