# manifest.py - esqueleto de congelado 3.0 (FASE B)
# MicroPython importa estos modulos desde la flash: sin coste de heap
# para su bytecode ni sus constantes (blob/tuplas viven en flash).
#
# Ubicacion final: dentro del directorio de la placa del build
# (ver README_BUILD.txt). Los 4 ficheros device/*.py se copian al
# mismo directorio que este manifest.py.

freeze('.', 'fontbase3')
freeze('.', 'fontnokia3')
freeze('.', 'fontarcade3')
freeze('.', 'writer3')
