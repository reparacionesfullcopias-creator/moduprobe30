# manifest.py - esqueleto de congelado 3.0 (FASE B)
# MicroPython importa estos modulos desde la flash: sin coste de heap
# para su bytecode ni sus constantes (blob/tuplas viven en flash).
#
# Ubicacion final: dentro del directorio de la placa del build
# (ver README_BUILD.txt). Los 4 ficheros device/*.py se copian al
# mismo directorio que este manifest.py.

# 21/09: en v1.29.0 freeze() exige el nombre COMPLETO del fichero
# (con .py). Verificado en tools/manifestfile.py: _search usa el
# nombre tal cual y _add_file hace os.stat sin anadir extension.

freeze('.', 'fontbase3.py')
freeze('.', 'fontnokia3.py')
freeze('.', 'fontarcade3.py')
freeze('.', 'writer3.py')
