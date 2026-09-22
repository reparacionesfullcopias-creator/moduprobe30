# ============================================================
# gpu.py — Gestión de capas y composición gráfica
#   - 4 capas oficiales (Base, FX, Plugin Overlay, System Overlay)
#   - Composición con transparencia y snapshots para modales
#   - Soporte completo de dithering (colores 2-15) vía matriz Bayer
#   - API de dibujo completa (fill, rect, line, circle, text, blit)
#
# Principio: composición deferred, show() único punto de envío a hardware.
# ============================================================

# ------------------------------------------------------------
# CONTRATO F-20 (nombres v2.0):
#   select_draw_layer(idx) -> elige la capa de DIBUJO para las
#       llamadas siguientes. NO cambia visibilidad ni compone.
#   enable_layer(idx)      -> marca la capa como VISIBLE en la
#       composicion. No limpia nada.
#   deactivate_layer(idx)  -> saca la capa de la composicion
#       Y ADEMAS la limpia (fill 0). No es la inversa pura de
#       enable_layer: cuidado al usarla como toggle.
#   get_active_layer()     -> FrameBuffer de la capa de dibujo.
#   Capa 0 (BASE) opaca; capas 1-3: pixel 0 = transparente.
#
# COLORES (polaridad del sistema: 0 = fondo/blanco, 1 = tinta):
#   fill(c):         c<=0 LIMPIA toda la capa; c=1 solido;
#                    c>=2 dithering Bayer.
#   fill_rect(...):  c<=0 borra SOLO ese rectangulo; c=1
#                    solido; c>=2 dithering.
#   rect/hline/vline/line/fill_circle/fill_shape_dithered:
#                    c<=0 NO-OP (no dibujan nada); c=1 solido;
#                    c>=2 dithering.
#   pixel(...):      c<=0 SI dibuja (color 0, borra el pixel);
#                    c>=2 dithering.
#   text(...):       SOLO 0 o 1. IGNORA dithering (c>0 sale como 1).
#   blit(...):       sin color.
#   Dithering (c>=2): Bayer 4x4 anclado a la rejilla ABSOLUTA de
#       pantalla (F-51): todas las primitivas comparten fase y un rect
#       que se mueve no "hierves" su patron.
# ------------------------------------------------------------

import framebuf


class GPU:
    # Arquitectura oficial de capas de FlexProbe.
    LAYER_BASE = 0
    LAYER_FX = 1
    LAYER_PLUGIN_OVERLAY = 2
    LAYER_SYSTEM_OVERLAY = 3

    # Matriz de Bayer 4x4 para simular niveles de gris (umbrales 0 a 15).
    BAYER = [
        [ 0,  8,  2, 10],
        [12,  4, 14,  6],
        [ 3, 11,  1,  9],
        [15,  7, 13,  5]
    ]

    def __init__(self, display, width=132, height=64):
        self.display = display
        self.width = width
        self.height = height
        self.buffer_size = width * (height // 8)

        self.layers = []
        self.raw_buffers = []
        self.active_flags = [False, False, False, False]
        self.active_layer_idx = 0

        for _ in range(4):
            buf = bytearray(self.buffer_size)
            self.raw_buffers.append(buf)
            self.layers.append(
                framebuf.FrameBuffer(
                    buf,
                    self.width,
                    self.height,
                    framebuf.MONO_VLSB
                )
            )

        self.final_raw = bytearray(self.buffer_size)
        self.final_buffer = framebuf.FrameBuffer(
            self.final_raw,
            self.width,
            self.height,
            framebuf.MONO_VLSB
        )

        # Capa 0 es opaca en la composición final.
        # Capas 1, 2 y 3 usan pixel 0 como transparente.
        self.transparency_keys = [-1, 0, 0, 0]

        # Snapshots para modales.
        self.snap_plugin = bytearray(self.buffer_size)
        self.snap_system = bytearray(self.buffer_size)

        self.snap_plugin_fb = framebuf.FrameBuffer(
            self.snap_plugin,
            self.width,
            self.height,
            framebuf.MONO_VLSB
        )

        self.snap_system_fb = framebuf.FrameBuffer(
            self.snap_system,
            self.width,
            self.height,
            framebuf.MONO_VLSB
        )

        # Pila de modales.
        self._modal_stack = []

        # ==================================================
        # v2.0: Tiles Bayer 4x4 pre-calculados para dithering rápido
        # ==================================================
        self._bayer_tiles = []
        for level in range(16):
            buf = bytearray(4)  # 4 columnas × 1 byte (4 filas en MONO_VLSB)
            for col in range(4):
                byte = 0
                for row in range(4):
                    if level > self.BAYER[row][col]:
                        byte |= (1 << row)
                buf[col] = byte
            fb = framebuf.FrameBuffer(buf, 4, 4, framebuf.MONO_VLSB)
            self._bayer_tiles.append(fb)

        # ==================================================
        # v2.0: Buffer temporal para máscaras de figuras irregulares
        # ==================================================
        self._mask_raw = bytearray(self.buffer_size)
        self._mask_fb = framebuf.FrameBuffer(
            self._mask_raw,
            self.width,
            self.height,
            framebuf.MONO_VLSB
        )

        self.enable_layer(0)
        self.select_draw_layer(0)

    # ==================================================
    # CAPAS
    # ==================================================

    def select_draw_layer(self, idx):
        self.active_layer_idx = idx

    def get_active_layer(self):
        return self.layers[self.active_layer_idx]

    def enable_layer(self, idx):
        self.active_flags[idx] = True

    def deactivate_layer(self, idx):
        self.active_flags[idx] = False
        self.layers[idx].fill(0)

    def clear_layer(self, idx):
        self.layers[idx].fill(0)

    # ==================================================
    # HELPERS DE MODALES
    # ==================================================

    def top_modal(self):
        return self._modal_stack[-1] if self._modal_stack else None

    def in_system_modal(self):
        return self.top_modal() == self.LAYER_SYSTEM_OVERLAY

    def in_plugin_modal(self):
        return self.top_modal() == self.LAYER_PLUGIN_OVERLAY

    def _refresh_active_layer(self):
        top = self.top_modal()

        if top is None:
            self.select_draw_layer(self.LAYER_BASE)
        else:
            self.select_draw_layer(top)

    def _capture_visible(self, dest):
        self._compose_visible()
        dest[:] = self.final_raw

    def _modal_cut_rect(self, rect):
        """Devuelve el rect de recorte del snapshot."""
        x, y, w, h = rect

        x0 = x - 1
        y0 = y - 1
        x1 = x + w + 1
        y1 = y + h + 1

        if x0 < 0:
            x0 = 0

        if y0 < 0:
            y0 = 0

        if x1 > self.width:
            x1 = self.width

        if y1 > self.height:
            y1 = self.height

        if x1 <= x0 or y1 <= y0:
            return (0, 0, 0, 0)

        return (x0, y0, x1 - x0, y1 - y0)

    # ==================================================
    # MODALES
    # ==================================================

    def open_plugin_modal(self, rect=None):
        """Abre un modal de plugin sobre la capa 2."""
        if self._modal_stack:
            return False

        self._capture_visible(self.snap_plugin)

        if rect is not None:
            cx, cy, cw, ch = self._modal_cut_rect(rect)

            if cw > 0 and ch > 0:
                self.snap_plugin_fb.fill_rect(cx, cy, cw, ch, 0)

        self.clear_layer(self.LAYER_PLUGIN_OVERLAY)
        self.enable_layer(self.LAYER_PLUGIN_OVERLAY)
        self._modal_stack.append(self.LAYER_PLUGIN_OVERLAY)
        self._refresh_active_layer()

        return True

    def close_plugin_modal(self, refresh=True):
        """Cierra el modal de plugin si está encima."""
        if self.top_modal() == self.LAYER_PLUGIN_OVERLAY:
            self._modal_stack.pop()
            self.deactivate_layer(self.LAYER_PLUGIN_OVERLAY)
            self._refresh_active_layer()

            if refresh:
                self.show()

            return True

        return False

    def open_system_modal(self, rect=None):
        """Abre un modal del sistema sobre la capa 3."""
        if self.in_system_modal():
            return False

        self._capture_visible(self.snap_system)

        if rect is not None:
            cx, cy, cw, ch = self._modal_cut_rect(rect)

            if cw > 0 and ch > 0:
                self.snap_system_fb.fill_rect(cx, cy, cw, ch, 0)

        self.clear_layer(self.LAYER_SYSTEM_OVERLAY)
        self.enable_layer(self.LAYER_SYSTEM_OVERLAY)
        self._modal_stack.append(self.LAYER_SYSTEM_OVERLAY)
        self._refresh_active_layer()

        return True

    def close_system_modal(self, refresh=True):
        """Cierra el modal del sistema si está encima."""
        if self.top_modal() == self.LAYER_SYSTEM_OVERLAY:
            self._modal_stack.pop()
            self.deactivate_layer(self.LAYER_SYSTEM_OVERLAY)
            self._refresh_active_layer()

            if refresh:
                self.show()

            return True

        return False

    def reset_modals(self):
        """Resetea todo el estado modal."""
        self._modal_stack = []
        self.deactivate_layer(self.LAYER_PLUGIN_OVERLAY)
        self.deactivate_layer(self.LAYER_SYSTEM_OVERLAY)
        self.select_draw_layer(self.LAYER_BASE)

    # ==================================================
    # COMPOSICIÓN Y SHOW
    # ==================================================

    def _compose_visible(self):
        top = self._modal_stack[-1] if self._modal_stack else None

        if top == self.LAYER_SYSTEM_OVERLAY:
            self.final_raw[:] = self.snap_system

            if self.active_flags[self.LAYER_SYSTEM_OVERLAY]:
                self.final_buffer.blit(
                    self.layers[self.LAYER_SYSTEM_OVERLAY],
                    0,
                    0,
                    0
                )

        elif top == self.LAYER_PLUGIN_OVERLAY:
            self.final_raw[:] = self.snap_plugin

            if self.active_flags[self.LAYER_PLUGIN_OVERLAY]:
                self.final_buffer.blit(
                    self.layers[self.LAYER_PLUGIN_OVERLAY],
                    0,
                    0,
                    0
                )

        else:
            # F-36: fast-path — única capa BASE activa, sin FX ni overlay de
            # plugin. Equivale a fill(0) + blit completo de la capa 0 (opaca,
            # key=-1), pero como memcpy en C en vez de 8448 px en Python.

            af = self.active_flags
            if (af[self.LAYER_BASE]
                    and not af[self.LAYER_FX]
                    and not af[self.LAYER_PLUGIN_OVERLAY]):
                self.final_raw[:] = self.raw_buffers[self.LAYER_BASE]
                return

            self.final_buffer.fill(0)

            # En modo normal solo componemos capas 0, 1 y 2.
            for i in range(3):
                if self.active_flags[i]:
                    self.final_buffer.blit(
                        self.layers[i],
                        0,
                        0,
                        self.transparency_keys[i]
                    )

    def show(self):
        self._compose_visible()
        self.display.buffer[:] = self.final_raw
        self.display.show()

    # ==================================================
    # DITHERING: RUTA MEDIA (acceso raw al buffer)
    # ==================================================

    def _dither_pixel(self, x, y, c):
        """Dithering de un solo píxel con acceso raw (sin overhead de layer.pixel)."""
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            return

        buf = self.raw_buffers[self.active_layer_idx]
        page = y >> 3
        bit = y & 7
        idx = page * self.width + x

        if c > self.BAYER[y & 3][x & 3]:
            buf[idx] |= (1 << bit)
        else:
            buf[idx] &= ~(1 << bit)

    def _dither_span(self, y, x1, x2, c):
        """Rellena una fila horizontal [x1, x2] con dithering vía acceso raw.
        Mucho más rápido que iterar _dither_pixel()."""
        if x1 > x2:
            return
        if x1 < 0:
            x1 = 0
        if x2 >= self.width:
            x2 = self.width - 1
        if y < 0 or y >= self.height:
            return

        buf = self.raw_buffers[self.active_layer_idx]
        page = y >> 3
        bit = y & 7
        base = page * self.width
        mask_on = 1 << bit
        mask_off = (~mask_on) & 0xFF
        row_bayer = self.BAYER[y & 3]

        for px in range(x1, x2 + 1):
            if c > row_bayer[px & 3]:
                buf[base + px] |= mask_on
            else:
                buf[base + px] &= mask_off

    # ==================================================
    # DIBUJO
    # ==================================================

    def fill(self, c):
        """Rellena la capa activa.

        - c <= 0: limpia la capa (color 0 nativo).
        - c == 1: relleno blanco sólido (color 1 nativo).
        - c >= 2: relleno con patrón de dithering (colores 2 a 15).
        """
        if c <= 0:
            self.layers[self.active_layer_idx].fill(0)
            return
        if c == 1:
            self.layers[self.active_layer_idx].fill(1)
            return

        # Para colores intermedios, delegamos en fill_rect que ya implementa el dithering.
        self.fill_rect(0, 0, self.width, self.height, c)

    def fill_rect(self, x, y, w, h, c):
        layer = self.layers[self.active_layer_idx]

        # c=0 borra: necesario para fondos limpios y recortes.
        if c <= 0:
            layer.fill_rect(x, y, w, h, 0)
            return

        if c == 1:
            layer.fill_rect(x, y, w, h, 1)
        else:
            # v2.0: Dithering por tiles Bayer 4x4 + blit() nativo
            # F-51: fase CONSISTENTE entre primitivas. Los tiles se
            # alinean a la rejilla ABSOLUTA de pantalla (multiplos de 4) y
            # las franjas de cabecera/cola van por _dither_span, que ya usa
            # BAYER[y&3][x&3] absoluto. Antes los tiles se anclaban al
            # origen del rect (fase relativa) y los bordes caian en span
            # (fase absoluta): costura interna cuando x%4 o y%4 != 0, y
            # patron que saltaba de fase al mover el rect entre frames.
            # Rects alineados (x%4==0 e y%4==0, incl. fill() completo)
            # recorren el mismo camino rapido de tiles que antes.
            tile = self._bayer_tiles[c]
            x1 = x + w
            y1 = y + h

            tx0 = (x + 3) & ~3          # primer inicio de tile alineado
            ty0 = (y + 3) & ~3
            nx = ((x1 - 4) - tx0) // 4 + 1 if x1 - 4 >= tx0 else 0
            ny = ((y1 - 4) - ty0) // 4 + 1 if y1 - 4 >= ty0 else 0
            tx_end = tx0 + nx * 4
            ty_end = ty0 + ny * 4

            # Cabecera vertical: filas por encima de la rejilla
            for py in range(y, min(ty0, y1)):
                self._dither_span(py, x, x1 - 1, c)

            # Bandas centrales alineadas: margen izq + tiles + cola der
            for ty in range(ty0, ty_end, 4):
                if tx0 > x:
                    for py in range(ty, ty + 4):
                        self._dither_span(py, x, min(tx0, x1) - 1, c)
                for tx in range(tx0, tx_end, 4):
                    layer.blit(tile, tx, ty)
                if tx_end < x1:
                    for py in range(ty, ty + 4):
                        self._dither_span(py, tx_end, x1 - 1, c)

            # Cola vertical: filas restantes bajo la rejilla
            for py in range(max(ty_end, y), y1):
                self._dither_span(py, x, x1 - 1, c)

    def rect(self, x, y, w, h, c):
        if c <= 0:
            return

        if c == 1:
            self.layers[self.active_layer_idx].rect(x, y, w, h, 1)
        else:
            # v2.0: Usar hline/vline optimizados en lugar de pixel()
            self.hline(x, y, w, c)
            self.hline(x, y + h - 1, w, c)
            self.vline(x, y, h, c)
            self.vline(x + w - 1, y, h, c)

    def hline(self, x, y, w, c):
        if c <= 0:
            return

        layer = self.layers[self.active_layer_idx]

        if c == 1:
            layer.hline(x, y, w, 1)
        else:
            # v2.0: Un solo span raw en lugar de bucle de pixels
            self._dither_span(y, x, x + w - 1, c)

    def vline(self, x, y, h, c):
        if c <= 0:
            return

        layer = self.layers[self.active_layer_idx]

        if c == 1:
            layer.vline(x, y, h, 1)
        else:
            # v2.0: Acceso raw vertical optimizado
            buf = self.raw_buffers[self.active_layer_idx]
            y1 = y + h
            if y < 0:
                y = 0
            if y1 > self.height:
                y1 = self.height
            if x < 0 or x >= self.width:
                return

            for py in range(y, y1):
                page = py >> 3
                bit = py & 7
                idx = page * self.width + x
                if c > self.BAYER[py & 3][x & 3]:
                    buf[idx] |= (1 << bit)
                else:
                    buf[idx] &= ~(1 << bit)

    def line(self, x1, y1, x2, y2, c):
        if c <= 0:
            return

        layer = self.layers[self.active_layer_idx]

        if c == 1:
            layer.line(x1, y1, x2, y2, 1)
        else:
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            sx = 1 if x1 < x2 else -1
            sy = 1 if y1 < y2 else -1
            err = dx - dy

            while True:
                self._dither_pixel(x1, y1, c)

                if x1 == x2 and y1 == y2:
                    break

                e2 = 2 * err

                if e2 > -dy:
                    err -= dy
                    x1 += sx

                if e2 < dx:
                    err += dx
                    y1 += sy

    def fill_circle(self, x0, y0, r, c):
        if c <= 0:
            return

        layer = self.layers[self.active_layer_idx]

        if c == 1:
            r2 = r * r

            for y in range(-r, r + 1):
                x_lim = int((r2 - y * y) ** 0.5)
                layer.hline(x0 - x_lim, y0 + y, 2 * x_lim + 1, 1)
        else:
            # v2.0: Un span por fila en lugar de bucle de pixels
            r2 = r * r

            for dy in range(-r, r + 1):
                py = y0 + dy
                if py < 0 or py >= self.height:
                    continue
                x_lim = int((r2 - dy * dy) ** 0.5)
                self._dither_span(py, x0 - x_lim, x0 + x_lim, c)

    def fill_shape_dithered(self, draw_fn, c, bbox):
        """Para figuras arbitrarias (polígonos, estrellas, etc.).

        draw_fn: función que recibe un FrameBuffer y dibuja la figura en color 1.
        bbox: (x, y, w, h) área a escanear.
        """
        if c <= 0:
            return
        if c == 1:
            draw_fn(self.layers[self.active_layer_idx])
            return

        x, y, w, h = bbox

        # Clip a pantalla
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x + w > self.width:
            w = self.width - x
        if y + h > self.height:
            h = self.height - y
        if w <= 0 or h <= 0:
            return

        # 1. Limpiar máscara
        self._mask_raw[:] = b'\x00' * self.buffer_size

        # 2. Dibujar figura en máscara (esto corre en C, es instantáneo)
        draw_fn(self._mask_fb)

        # 3. Aplicar dithering solo donde la máscara tenga píxeles
        buf = self.raw_buffers[self.active_layer_idx]
        mask = self._mask_raw

        for py in range(y, y + h):
            page = py >> 3
            bit = py & 7
            mask_on = 1 << bit
            mask_off = (~mask_on) & 0xFF
            base = page * self.width
            row_bayer = self.BAYER[py & 3]

            for px in range(x, x + w):
                idx = base + px
                if mask[idx] & mask_on:
                    if c > row_bayer[px & 3]:
                        buf[idx] |= mask_on
                    else:
                        buf[idx] &= mask_off

    def text(self, s, x, y, c=1):
        self.layers[self.active_layer_idx].text(s, x, y, 1 if c > 0 else 0)

    def blit(self, source, x, y, key=0):
        self.layers[self.active_layer_idx].blit(source, x, y, key)

    def pixel(self, x, y, c=1):
        if c <= 0:
            self.layers[self.active_layer_idx].pixel(x, y, 0)
        elif c == 1:
            self.layers[self.active_layer_idx].pixel(x, y, 1)
        else:
            self._dither_pixel(x, y, c)

    def __getattr__(self, name):
        return getattr(self.display, name)