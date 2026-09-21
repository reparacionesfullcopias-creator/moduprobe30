# ============================================================
# writer3.py — Motor de texto sobre GPU (Writer) — rama 3.0
# Base: writer.py I37 (produccion 2.0, intacto). Diferencias 3.0-B
# marcadas con "3.0-B". El camino caliente (text()/render_line())
# NO cambia: la unica diferencia es de donde sale el glifo.
#
#   - Render glifo a glifo via FrameBuffer + blit con transparencia
#   - Fuentes de ancho variable (glifos recortados)
#   - wrap() con particion de palabras largas, text_line() por linea
#   - Modo color 0 (invertido) y color 1 (normal)
#
# Formatos de fuente aceptados:
#   3.0-B (nuevo): _BLOB + _CP + _OFF + metadatos HEIGHT/BYTE/
#                  TRACKING/LINE_HEIGHT. Sin dict, sin qstrs de clave.
#   Legado 2.x:    dict _font {char: bytes/lista} + mismos metadatos
#                  (mantenido para compat y verificacion A/B).
#   Layout PLANAR, MONO_VLSB.
#
# Principio: dibuja en la capa activa de la GPU; NUNCA llama a show().
# F-11: soporte de glifos grandes (entrada de cache a tamano exacto).
# F-23/F-24: glifos normalizados a bytes + cache de FrameBuffer por
# ancho -> cero allocs por caracter en el camino caliente.
# 3.0-B: en modo blob el glifo llega como slice del blob (1 alloc
# transitorio por caracter, clase igual a los slices de render_line;
# se elimina en iteracion 3 con writer en C).
# ============================================================

import framebuf
import gc

class Writer:
    def __init__(self, device, font):
        self.device = device
        self.font = font

        # Metadatos (comunes a ambos formatos; por defecto si es antigua).
        self.font_height = getattr(font, 'HEIGHT', 8)
        self.bytes_per_col = getattr(font, 'BYTE', 1)
        self.tracking = getattr(font, 'TRACKING', 1)
        self.line_height = getattr(font, 'LINE_HEIGHT', self.font_height + 2)

        # 3.0-B: glifo en blanco preallocado (1 alloc en el boot; antes
        # se alocaba una lista en cada _get_glyph fallido).
        self._blank = bytes(self.bytes_per_col)

        # 3.0-B: deteccion de formato por presencia de _BLOB.
        self._blob = getattr(font, '_BLOB', None)

        if self._blob is not None:
            # ================= MODO BLOB (3.0) =================
            self._font_dict = None
            self._cp = font._CP
            self._off = font._OFF
            n_g = len(self._cp)

            # F-11: buffer unico al glifo mas grande (desde offsets).
            max_len = 32
            for i in range(n_g):
                gl = self._off[i + 1] - self._off[i]
                if gl > max_len:
                    max_len = gl
            self._buf = bytearray(max_len)

            # F-24b: cache de FrameBuffer por ANCHO, derivada de offsets.
            self._fb_cache = {}
            _mv = memoryview(self._buf)
            for i in range(n_g):
                w = (self._off[i + 1] - self._off[i]) // self.bytes_per_col
                if w > 0 and w not in self._fb_cache:
                    _mvw = _mv[:w * self.bytes_per_col]
                    self._fb_cache[w] = (_mvw, framebuf.FrameBuffer(
                        _mvw, w, self.font_height, framebuf.MONO_VLSB))
            del _mv
        else:
            # ================= MODO DICT (legado 2.x) =================
            # La arquitectura oficial usa _font.
            # Si por alguna razon una fuente tuviera font, tambien funciona.
            self._font_dict = getattr(font, '_font', None)
            used_attr = '_font' if self._font_dict is not None else 'font'

            if self._font_dict is None:
                self._font_dict = getattr(font, 'font', {})

            # F-11: dimensionado al glifo mas grande de la fuente (min. 32).
            max_len = 32
            for g in self._font_dict.values():
                if len(g) > max_len:
                    max_len = len(g)
            self._buf = bytearray(max_len)

            # F-24 (legado): normalizar glifos de list a bytes. Idempotente.
            converted = {}
            convirtio = False
            for k, v in self._font_dict.items():
                if type(v) is list:
                    converted[k] = bytes(v)
                    convirtio = True
                else:
                    converted[k] = v
            if convirtio:
                setattr(font, used_attr, converted)
                self._font_dict = converted
                gc.collect()

            # F-24b: cache de wrappers por ancho (construida en el boot).
            self._fb_cache = {}
            _mv = memoryview(self._buf)
            for g in self._font_dict.values():
                w = len(g) // self.bytes_per_col
                if w > 0 and w not in self._fb_cache:
                    _mvw = _mv[:w * self.bytes_per_col]
                    self._fb_cache[w] = (_mvw, framebuf.FrameBuffer(
                        _mvw, w, self.font_height, framebuf.MONO_VLSB))
            del _mv

        # F-39 v2 (comun a ambos formatos): buffer de linea preallocado
        # + bytes de limpieza. Cero objetos en caliente. La limpieza usa
        # memcpy (zeros), NO fb.fill() (lento en v3).
        self._line_w = getattr(self.device, 'width', 132)
        self._line_pages = (self.font_height + 7) // 8
        self._line_buf = bytearray(self._line_pages * self._line_w)
        self._line_fb = framebuf.FrameBuffer(
            self._line_buf,
            self._line_w,
            self.font_height,
            framebuf.MONO_VLSB
        )
        self._line_zeros = bytes(self._line_pages * self._line_w)

    def _get_glyph(self, char):
        """Devuelve (bytes_del_glifo, ancho_en_pixels).

        3.0-B modo blob: busqueda binaria de ord(char) sobre _CP
        (requiere _CP ordenado: garantizado por convert_fonts.py).
        Fallo -> glifo blanco preallocado (antes: lista por llamada)."""
        if self._blob is not None:
            cp = ord(char)
            cp_t = self._cp
            off_t = self._off
            lo = 0
            hi = len(cp_t) - 1
            while lo <= hi:
                mid = (lo + hi) >> 1
                m = cp_t[mid]
                if m == cp:
                    start = off_t[mid]
                    end = off_t[mid + 1]
                    if start == end:
                        return self._blank, 1
                    return (self._blob[start:end],
                            (end - start) // self.bytes_per_col)
                if m < cp:
                    lo = mid + 1
                else:
                    hi = mid - 1
            return self._blank, 1

        glyph = self._font_dict.get(char)

        if glyph is None:
            return self._blank, 1    # 3.0-B: antes [0x00]*bpc por llamada

        width = len(glyph) // self.bytes_per_col

        if width == 0:
            return self._blank, 1

        return glyph, width

    def text_width(self, string):
        """Devuelve el ancho aproximado del texto en pixels."""
        total = 0

        for char in string:
            glyph, width = self._get_glyph(char)
            total += width + self.tracking

        if total > 0:
            total -= self.tracking

        return total

    def _fit(self, word, max_width):
        """Parte una palabra demasiado larga para que entre en max_width."""
        if self.text_width(word) <= max_width:
            return word, ''

        for i in range(len(word), 0, -1):
            part = word[:i]

            if self.text_width(part) <= max_width:
                return part, word[i:]

        # Si ni siquiera un caracter entra, forzamos uno.
        return word[:1], word[1:]

    def wrap(self, string, max_width):
        """Ajusta texto a un ancho maximo. Devuelve lista de lineas."""
        if string is None:
            return ['']

        if max_width <= 0:
            return [string]

        lines = []

        for raw in string.split('\n'):
            words = raw.split(' ')
            line = ''

            for word in words:
                if word == '':
                    continue

                # Palabras demasiado largas se parten.
                while self.text_width(word) > max_width and len(word) > 0:
                    part, word = self._fit(word, max_width)

                    if line:
                        lines.append(line)
                        line = ''

                    lines.append(part)

                if len(word) == 0:
                    continue

                if not line:
                    line = word
                else:
                    test = line + ' ' + word

                    if self.text_width(test) <= max_width:
                        line = test
                    else:
                        lines.append(line)
                        line = word

            lines.append(line)

        if not lines:
            lines = ['']

        return lines

    def text(self, string, x, y, color=1):
        buf = self._buf
        buf_len = len(buf)
        cache = self._fb_cache

        for char in string:
            char_bytes, width = self._get_glyph(char)
            n_bytes = len(char_bytes)

            # FIX (F-11): red de seguridad en runtime (p.ej. fuente mutada
            # tras el init). Con el escaneo de __init__ no deberia dispararse.
            # Si dispara, la cache se invalida: los wrappers apuntaban al
            # buffer anterior.
            if n_bytes > buf_len:
                buf = bytearray(n_bytes)
                self._buf = buf
                buf_len = n_bytes
                cache = {}
                self._fb_cache = cache

            # F-24b: wrapper cacheado por ancho. Cero allocs por caracter
            # en el camino caliente (el buffer _buf se rellena y se blit).
            entry = cache.get(width)
            if entry is None:
                mv = memoryview(buf)[:n_bytes]
                entry = (mv, framebuf.FrameBuffer(
                    mv, width, self.font_height, framebuf.MONO_VLSB))
                cache[width] = entry
            char_fbuf = entry[1]

            # Logica de color invertido (color == 0)
            if color == 0:
                for i in range(n_bytes):
                    buf[i] = (~char_bytes[i]) & 0xFF

                # El '1' actua como transparente y dibuja los '0'.
                self.device.blit(char_fbuf, x, y, 1)

            # Modo normal (color != 0)
            else:
                for i in range(n_bytes):
                    buf[i] = char_bytes[i]

                # El '0' actua como transparente y dibuja los '1'.
                self.device.blit(char_fbuf, x, y, 0)

            x += width + self.tracking

    def text_line(self, string, x, line_number, color=1):
        # Calcula la coordenada Y automaticamente basandose en la linea.
        y = line_number * self.line_height
        self.text(string, x, y, color)

    def render_line(self, string, x, y, color=1):
        """F-39 v2: una linea completa = UN blit. Cero allocs en caliente.
        Inversion (color=0) in-place del buffer COMPLETO: el fondo queda
        transparente en todo el ancho -> sin artefactos (leccion I19)."""
        buf = self._line_buf
        W = self._line_w
        bpc = self.bytes_per_col
        buf[:] = self._line_zeros
        px = 0

        for char in string:
            glyph, width = self._get_glyph(char)
            if width <= 0 or px + width > W:
                break
            for p in range(bpc):
                dst = p * W + px
                buf[dst:dst + width] = glyph[p * width:(p + 1) * width]
            px += width + self.tracking

        inv = (color == 0)
        if inv:
            for i in range(len(buf)):
                buf[i] ^= 0xFF

        self.device.blit(self._line_fb, x, y, 1 if inv else 0)
