# ============================================================
# fonts.py — Registro central de fuentes (Fonts)
#   - Registro de Writers ya construidos: nombre -> writer
#   - Exposicion como atributo: fonts.base5.text(...)
#   - Acceso por string (get), metadatos (info), lista (list)
#   - Seleccion por altura (closest)
#
# Principio: main.py crea los Writer(lcd, modulo_fuente) y los
# registra aqui; las tools los consumen via Tool(lcd, fonts).
# ============================================================

class Fonts:
    """Registro de fuentes disponibles para las herramientas."""
    
    def __init__(self):
        self._registry = {}
    
    def add(self, name, writer, font_module):
        """Registra una fuente con su writer ya creado."""
        self._registry[name] = {
            'writer': writer,
            'height': getattr(font_module, 'HEIGHT', 8),
            'line_height': getattr(font_module, 'LINE_HEIGHT', 10),
            'tracking': getattr(font_module, 'TRACKING', 1),
            'bytes_per_col': getattr(font_module, 'BYTE', 1),
        }
        # Exponer como atributo para acceso cómodo: fonts.base5.text(...)
        setattr(self, name, writer)
    
    def get(self, name):
        """Acceso por string: fonts.get('base5')"""
        return self._registry[name]['writer']
    
    def list(self):
        """Lista de nombres disponibles: ['base5', 'nokia8', 'arcade10']"""
        return list(self._registry.keys())
    
    def info(self, name):
        """Metadatos: altura, line_height, etc."""
        return self._registry.get(name, {})
    
    def closest(self, target_height):
        """Devuelve la fuente con altura más cercana a la pedida."""
        best_name = None
        best_diff = 9999
        for name, data in self._registry.items():
            diff = abs(data['height'] - target_height)
            if diff < best_diff:
                best_diff = diff
                best_name = name
        return self._registry[best_name]['writer'] if best_name else None