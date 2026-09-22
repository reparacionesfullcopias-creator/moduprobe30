# ============================================================
# screens.py — Dibujado de pantallas de ModuProbe
#   - Renderizado de dashboard, opciones, lista SD, errores
#   - Pantalla de información de tools (info_tool)
#   - Sistema oficial de modales (Modals)
#   - NO llama a lcd.show() en métodos de renderizado parcial
#
# Principio: render() recibe 'modo' como argumento (AppState),
# no lee de nav.modo. El show() final es responsabilidad de main.py.
#
# F-20: set_active_layer renombrado a select_draw_layer (contrato en gpu.py).
# ============================================================

import time


class Screens:
    def __init__(self, lcd, fonts):
        self.lcd = lcd
        self.fonts = fonts

    def marco(self):
        self.lcd.fill(0)
        self.lcd.rect(0, 0, 132, 64, 1)

    def info_tool(self, meta, lines, scroll):
        """Dibuja la pantalla de información de una tool con scroll.

        Diseño:
        - Barra de título invertida con nombre + versión
        - Descripción scrolleable en el centro
        - Copyright al pie
        - Indicadores de scroll (triángulos) si hay más contenido
        """
        lcd = self.lcd
        lcd.select_draw_layer(lcd.LAYER_SYSTEM_OVERLAY)
        lcd.clear_layer(lcd.LAYER_SYSTEM_OVERLAY)

        # === BORDE PRINCIPAL ===
        lcd.rect(0, 0, 132, 64, 1)

        # === BARRA DE TÍTULO (invertida) ===
        name = meta.get("name", "Tool")
        version = meta.get("version", "")

        lcd.fill_rect(1, 1, 130, 11, 1)

        # Nombre a la izquierda (texto blanco sobre negro)
        name_trunc = self._truncate_to_width(name, self.fonts.nokia8, 90)
        self.fonts.nokia8.text(name_trunc, 4, 2, 0)

        # Versión a la derecha
        if version:
            ver_text = "v" + version
            ver_w = self.fonts.base5.text_width(ver_text)
            self.fonts.base5.text(ver_text, 126 - ver_w, 3, 0)

        # === SEPARADOR BAJO EL TÍTULO ===
        lcd.hline(1, 12, 130, 1)

        # === ÁREA DE DESCRIPCIÓN (scrollable) ===
        # FIX v2.0: 4 líneas visibles para evitar superposición con copyright
        visible_lines = 4
        desc_y_start = 15
        line_h = self.fonts.base5.line_height

        # Calcular líneas visibles
        end_idx = min(scroll + visible_lines, len(lines))
        visible = lines[scroll:end_idx]

        # Dibujar líneas visibles
        y = desc_y_start
        for line in visible:
            self.fonts.base5.text(line, 5, y, 1)
            y += line_h

        # === INDICADORES DE SCROLL ===
        has_more_above = scroll > 0
        has_more_below = end_idx < len(lines)

        if has_more_above:
            # Triángulo arriba (derecha)
            self._draw_triangle_up(124, 15)

        if has_more_below:
            # Triángulo abajo (derecha)
            self._draw_triangle_down(124, 15 + (visible_lines - 1) * line_h + 2)

        # === COPYRIGHT AL PIE ===
        copyright_text = meta.get("copyright", "")
        if copyright_text:
            # FIX v2.0: Línea separadora bajada 1 píxel (52 -> 53).
            # No subir de 53: la 4a línea de descripción (y=45, caja 8px)
            # llega hasta la fila 52.
            lcd.hline(4, 53, 124, 1)
            # Copyright subido 1 píxel (56 -> 55): aire contra el borde.
            # El separador queda en 53 (1 fila de aire con la leyenda).
            cop_w = self.fonts.base5.text_width(copyright_text)
            cop_x = (132 - cop_w) // 2
            self.fonts.base5.text(copyright_text, cop_x, 55, 1)

        # === HINT DE NAVEGACIÓN ===
        if has_more_above or has_more_below:
            hint = "Up/Down: scroll"
        else:
            hint = "Back: volver"
        hint_w = self.fonts.base5.text_width(hint)
        # Solo mostrar hint si no hay copyright (para no saturar)
        if not copyright_text:
            self.fonts.base5.text(hint, (132 - hint_w) // 2, 55, 1)

    def _truncate_to_width(self, text, writer, max_w):
        """Trunca texto para que quepa en max_w píxeles."""
        if writer.text_width(text) <= max_w:
            return text
        while len(text) > 0 and writer.text_width(text + "...") > max_w:
            text = text[:-1]
        return text + "..." if text else ""

    def _draw_triangle_up(self, x, y):
        """Dibuja un triángulo apuntando arriba (indicador de scroll)."""
        lcd = self.lcd
        lcd.pixel(x + 2, y, 1)
        lcd.hline(x + 1, y + 1, 3, 1)
        lcd.hline(x, y + 2, 5, 1)

    def _draw_triangle_down(self, x, y):
        """Dibuja un triángulo apuntando abajo (indicador de scroll)."""
        lcd = self.lcd
        lcd.hline(x, y, 5, 1)
        lcd.hline(x + 1, y + 1, 3, 1)
        lcd.pixel(x + 2, y + 2, 1)

    def dashboard(self, items, icons, seleccion, inicio=0):
        """F-42: dashboard por ventana. 'items' es la VENTANA activa (no la
        lista completa); 'inicio' es la posicion absoluta del primer
        elemento de la ventana y 'seleccion' la posicion absoluta del
        cursor. El icono SI se indexa en absoluto: 'icons' cubre la lista
        virtual completa (refs compartidas: ~8 B por tool).
        F-53: slot None = sin resolver (blank). No se dibuja nada en el
        panel derecho hasta que core 0 resuelva el icono on demand
        (FrameBuffer custom o ICON_NO_TOOL como fallback resuelto)."""
        lcd = self.lcd

        lcd.fill(0)
        lcd.vline(78, 15, 49, 1)

        self.fonts.arcade10.text("ModuProbe", 40, 0)

        _icono = icons[seleccion] if 0 <= seleccion < len(icons) else None
        if _icono is not None:
            lcd.blit(_icono, 84, 16, 0)

        visibles = 5
        pos_central = 2
        rel = seleccion - inicio  # posicion del cursor dentro de la ventana
        offset = rel - pos_central

        y = 15

        for i in range(visibles):
            idx = offset + i

            if idx < 0 or idx >= len(items):
                y += self.fonts.nokia8.line_height
                continue

            texto = items[idx]

            if idx == rel:
                lcd.fill_rect(0, y - 1, 77, self.fonts.nokia8.line_height, 1)
                self.fonts.nokia8.text(texto, 8, y, color=0)
            else:
                self.fonts.nokia8.text(texto, 2, y, color=1)

            y += self.fonts.nokia8.line_height

    def opciones(self, volumen, contraste, seleccion, con_min=45, con_max=63):
        lcd = self.lcd
        self.marco()

        self.fonts.base5.text("Opciones", 6, 4)
        lcd.hline(0, 14, 132, 1)

        # === FILA 0: VOLUMEN ===
        if seleccion == 0:
            w = self.fonts.nokia8
            prefijo = ">"
        else:
            w = self.fonts.base5
            prefijo = " "

        w.text(prefijo + "Volumen", 6, 18)

        # Barra de volumen (posición fija)
        lcd.rect(6, 27, 120, 6, 1)
        ancho_vol = int((volumen / 100) * 118)
        lcd.fill_rect(7, 28, ancho_vol, 4, 1)

        # Valor numérico alineado a la derecha con la misma fuente
        val_str = str(volumen)
        val_w = w.text_width(val_str)
        w.text(val_str, 126 - val_w, 18)

        # === FILA 1: CONTRASTE ===
        if seleccion == 1:
            w = self.fonts.nokia8
            prefijo = ">"
        else:
            w = self.fonts.base5
            prefijo = " "

        w.text(prefijo + "Contraste", 6, 35)

        # Barra de contraste (posición fija)
        lcd.rect(6, 44, 120, 6, 1)
        ancho_con = int(((contraste - con_min) / float(con_max - con_min)) * 118)
        lcd.fill_rect(7, 45, ancho_con, 4, 1)

        # Valor numérico alineado a la derecha con la misma fuente
        val_str = str(contraste)
        val_w = w.text_width(val_str)
        w.text(val_str, 126 - val_w, 35)

        # === FILA 2: VOLCAR LOG (F-18; accion, sin barra ni valor) ===
        if seleccion == 2:
            w = self.fonts.nokia8
            prefijo = ">"
        else:
            w = self.fonts.base5
            prefijo = " "

        w.text(prefijo + "Volcar log", 6, 52)

    def lista(self, nav):
        lcd = self.lcd

        self.marco()

        if len(nav.lista_items) == 0:
            self.fonts.nokia8.text("SD Vacia", 41, 28)
            return

        y = 4

        for i in range(nav.visibles):
            idx = nav.offset + i

            if idx >= len(nav.lista_items):
                break

            nombre, es_dir = nav.lista_items[idx]
            prefijo = ">" if idx == nav.seleccion else " "

            if es_dir:
                texto = prefijo + nombre[:18] + "/"
            else:
                texto = prefijo + nombre[:19]

            if idx == nav.seleccion:
                lcd.fill_rect(1, y - 1, 130, self.fonts.nokia8.line_height - 1, 1)
                self.fonts.nokia8.render_line(texto, 6, y, 0)
            else:
                self.fonts.nokia8.render_line(texto, 6, y, 1)

            y += self.fonts.nokia8.line_height

    def error_sd(self, mensaje=""):
        self.marco()
        self.fonts.nokia8.text("Error SD", 6, 4)

        if mensaje:
            self.fonts.base5.text(mensaje[:20], 6, 16)

    def aviso_sd_extraida(self):
        lcd = self.lcd

        self.marco()

        self.fonts.nokia8.text("Tarjeta SD", 36, 12)
        self.fonts.nokia8.text("extraída", 42, 24)

        lcd.hline(6, 40, 120, 1)

        self.fonts.base5.text("Pulsa para continuar", 6, 48)

    def render(self, modo, nav, items, icons, seleccion, opciones_sel, volumen, contraste, con_min=45, con_max=63, inicio=0):
        """Renderiza la pantalla según el modo actual.

        'modo' viene ahora como argumento (AppState),
        no se lee de nav.modo (que ya no existe en SDBrowser).
        F-19: con_min/con_max viajan desde main.py (fuente: services).
        F-42: 'inicio' es la posicion absoluta del primer elemento de la
        ventana del dashboard (items = ventana, seleccion = absoluta).
        """
        # F-09: contrato de capa de dibujo. Todo render normal dibuja en la
        # capa BASE, sin importar el estado residual que dejen llamadas
        # anteriores (info_tool selecciona SYSTEM_OVERLAY y no lo restaura).

        self.lcd.select_draw_layer(self.lcd.LAYER_BASE)
        try:
            if modo == "dashboard":
                self.dashboard(items, icons, seleccion, inicio)

            elif modo == "opciones":
                self.opciones(volumen, contraste, opciones_sel, con_min, con_max)

            elif modo == "lista":
                self.lista(nav)

            elif modo == "error":
                self.error_sd(getattr(nav, "error_sd", ""))

            elif modo == "tool":
                pass

        except Exception as e:
            print("[UI] Error dibujando:", e)

            try:
                self.lcd.fill(0)
                self.lcd.rect(0, 0, 132, 64, 1)
                self.fonts.base5.text("UI ERROR", 6, 4)
                self.lcd.show()
            except Exception:
                pass

#============================================================
#Nuevo sistema oficial de modales
#============================================================


class Modals:
    INFO_TIMEOUT_MS = 3000

    BTN_H = 12
    BTN_PAD_X = 4
    SIDE_PAD = 4
    VERT_PAD_INFO = 3
    GAP = 4
    CONFIRM_BOTTOM_PAD = 4

    def __init__(self, lcd, fonts):
        self.lcd = lcd
        self.fonts = fonts
        self._info_owner = None
        self._info_opened = 0
        self._info_timeout = self.INFO_TIMEOUT_MS

    # ==================================================
    # HELPERS
    # ==================================================

    def _truncate_width(self, writer, text, max_width):
        if max_width <= 0:
            return ""

        if writer.text_width(text) <= max_width:
            return text

        ellipsis = "..."

        while len(text) > 0 and writer.text_width(text + ellipsis) > max_width:
            text = text[:-1]

        if not text:
            return ""

        return text + ellipsis

    # ==================================================
    # LAYOUT CONFIRM
    # ==================================================

    def layout_confirm(self, title):
        title_h = self.fonts.nokia8.line_height

        max_title_w = 132 - (self.SIDE_PAD * 2) - 2
        title = self._truncate_width(self.fonts.nokia8, title, max_title_w)
        title_w = self.fonts.nokia8.text_width(title)

        cancel_label = "Cancelar"
        accept_label = "Aceptar"

        cancel_label_w = self.fonts.base5.text_width(cancel_label)
        accept_label_w = self.fonts.base5.text_width(accept_label)

        cancel_w = cancel_label_w + (self.BTN_PAD_X * 2)
        accept_w = accept_label_w + (self.BTN_PAD_X * 2)

        buttons_w = cancel_w + accept_w + self.GAP
        content_w = max(title_w, buttons_w)

        w = content_w + (self.SIDE_PAD * 2) + 2

        if w > 132:
            w = 132

        h = 1 + title_h + self.GAP + self.BTN_H + self.CONFIRM_BOTTOM_PAD + 1

        x = (132 - w) // 2
        y = (64 - h) // 2

        title_x = x + (w - title_w) // 2

        if title_x < x + 2:
            title_x = x + 2

        total_btn_w = cancel_w + accept_w + self.GAP
        btn_x = x + (w - total_btn_w) // 2

        if btn_x < x + 1:
            btn_x = x + 1

        btn_y = y + 1 + title_h + self.GAP

        buttons = [
            {
                'label': cancel_label,
                'x': btn_x,
                'y': btn_y,
                'w': cancel_w,
                'h': self.BTN_H,
                'text_x': btn_x + (cancel_w - cancel_label_w) // 2,
                'text_y': btn_y + 2
            },
            {
                'label': accept_label,
                'x': btn_x + cancel_w + self.GAP,
                'y': btn_y,
                'w': accept_w,
                'h': self.BTN_H,
                'text_x': btn_x + cancel_w + self.GAP + (accept_w - accept_label_w) // 2,
                'text_y': btn_y + 2
            }
        ]

        return {
            'type': 'confirm',
            'rect': (x, y, w, h),
            'title': title,
            'title_x': title_x,
            'title_y': y + 2,
            'buttons': buttons
        }

    # ==================================================
    # LAYOUT INFO
    # ==================================================

    def layout_info(self, text):
        line_h = self.fonts.base5.line_height

        max_w = 132 - (self.SIDE_PAD * 2) - 2
        max_lines = (64 - (self.VERT_PAD_INFO * 2) - 2) // line_h

        if max_lines < 1:
            max_lines = 1

        lines = self.fonts.base5.wrap(text, max_w)

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._truncate_width(
                self.fonts.base5,
                lines[-1] + "...",
                max_w
            )

        if not lines:
            lines = ['']

        max_line_w = 0

        for line in lines:
            lw = self.fonts.base5.text_width(line)

            if lw > max_line_w:
                max_line_w = lw

        w = max_line_w + (self.SIDE_PAD * 2) + 2

        if w > 132:
            w = 132

        h = len(lines) * line_h + (self.VERT_PAD_INFO * 2) + 2

        x = (132 - w) // 2
        y = (64 - h) // 2

        return {
            'type': 'info',
            'rect': (x, y, w, h),
            'lines': lines
        }

    # ==================================================
    # LAYOUT ALERT
    # ==================================================

    def layout_alert(self, title, content):
        title_h = self.fonts.nokia8.line_height

        max_title_w = 132 - 8
        title = self._truncate_width(self.fonts.nokia8, title, max_title_w)

        content_x = self.SIDE_PAD
        content_y = 1 + title_h + 4

        btn_label = "Aceptar"
        btn_label_w = self.fonts.base5.text_width(btn_label)

        btn_w = btn_label_w + (self.BTN_PAD_X * 2)
        btn_h = self.BTN_H
        btn_x = (132 - btn_w) // 2
        btn_y = 50

        max_content_w = 132 - (self.SIDE_PAD * 2) - 2
        max_content_bottom = btn_y - 2

        max_lines = (max_content_bottom - content_y) // self.fonts.base5.line_height

        if max_lines < 0:
            max_lines = 0

        lines = self.fonts.base5.wrap(content, max_content_w)

        if len(lines) > max_lines:
            lines = lines[:max_lines]

            if max_lines > 0:
                lines[-1] = self._truncate_width(
                    self.fonts.base5,
                    lines[-1] + "...",
                    max_content_w
                )

        return {
            'type': 'alert',
            'rect': (0, 0, 132, 64),
            'title': title,
            'content_x': content_x,
            'content_y': content_y,
            'lines': lines,
            'button': {
                'label': btn_label,
                'x': btn_x,
                'y': btn_y,
                'w': btn_w,
                'h': btn_h,
                'text_x': btn_x + (btn_w - btn_label_w) // 2,
                'text_y': btn_y + 2
            }
        }

    # ==================================================
    # DRAW CONFIRM
    # ==================================================

    def draw_confirm(self, spec, sel, layer):
        lcd = self.lcd

        lcd.select_draw_layer(layer)
        lcd.clear_layer(layer)

        x, y, w, h = spec['rect']

        # Borde obligatorio.
        lcd.rect(x, y, w, h, 1)

        # Barra de título sólida + texto invertido.
        title_h = self.fonts.nokia8.line_height
        lcd.fill_rect(x + 1, y + 1, w - 2, title_h, 1)
        self.fonts.nokia8.text(spec['title'], spec['title_x'], spec['title_y'], 0)

        # Botones.
        for i in range(len(spec['buttons'])):
            btn = spec['buttons'][i]

            lcd.rect(btn['x'], btn['y'], btn['w'], btn['h'], 1)

            if i == sel:
                lcd.fill_rect(
                    btn['x'] + 1,
                    btn['y'] + 1,
                    btn['w'] - 2,
                    btn['h'] - 2,
                    1
                )
                self.fonts.base5.text(
                    btn['label'],
                    btn['text_x'],
                    btn['text_y'],
                    0
                )
            else:
                self.fonts.base5.text(
                    btn['label'],
                    btn['text_x'],
                    btn['text_y'],
                    1
                )

    # ==================================================
    # DRAW INFO
    # ==================================================

    def draw_info(self, spec, layer):
        lcd = self.lcd

        lcd.select_draw_layer(layer)
        lcd.clear_layer(layer)

        x, y, w, h = spec['rect']

        # Borde obligatorio.
        lcd.rect(x, y, w, h, 1)

        line_h = self.fonts.base5.line_height
        ty = y + self.VERT_PAD_INFO + 1

        for line in spec['lines']:
            lw = self.fonts.base5.text_width(line)
            lx = x + (w - lw) // 2

            self.fonts.base5.text(line, lx, ty, 1)
            ty += line_h

    # ==================================================
    # DRAW ALERT
    # ==================================================

    def draw_alert(self, spec, layer):
        lcd = self.lcd

        lcd.select_draw_layer(layer)
        lcd.clear_layer(layer)

        # Borde pantalla completa.
        lcd.rect(0, 0, 132, 64, 1)

        # Barra de título.
        title_h = self.fonts.nokia8.line_height
        lcd.fill_rect(1, 1, 130, title_h, 1)
        self.fonts.nokia8.text(spec['title'], 4, 2, 0)

        # Contenido.
        ty = spec['content_y']

        for line in spec['lines']:
            self.fonts.base5.text(line, spec['content_x'], ty, 1)
            ty += self.fonts.base5.line_height

        # Botón Aceptar, siempre seleccionado.
        btn = spec['button']

        lcd.rect(btn['x'], btn['y'], btn['w'], btn['h'], 1)
        lcd.fill_rect(
            btn['x'] + 1,
            btn['y'] + 1,
            btn['w'] - 2,
            btn['h'] - 2,
            1
        )

        self.fonts.base5.text(
            btn['label'],
            btn['text_x'],
            btn['text_y'],
            0
        )

    # ==================================================
    # INFO FIRE & FORGET
    # ==================================================

    def show_toast(self, text, timeout_ms=None):
        """Muestra un toast del sistema.
        El nombre explicito `show_toast` reemplaza a `show_system_info`
        y mantiene compatibilidad con llamadas antiguas.
        """
        if self.lcd.top_modal() == self.lcd.LAYER_SYSTEM_OVERLAY:
            self.lcd.close_system_modal()

        spec = self.layout_info(text)

        if not self.lcd.open_system_modal(spec['rect']):
            return

        self.draw_info(spec, self.lcd.LAYER_SYSTEM_OVERLAY)

        self._info_owner = 'system'
        self._info_opened = time.ticks_ms()
        self._info_timeout = self.INFO_TIMEOUT_MS if timeout_ms is None else timeout_ms

        self.lcd.show()

    def show_plugin_info(self, text):
        """Muestra un INFO de plugin. Se cierra solo a los 3000 ms
        (o al timeout pasado, para modales de espera tipo F-37)."""
        if self.lcd.top_modal() == self.lcd.LAYER_SYSTEM_OVERLAY:
            return

        if self.lcd.top_modal() == self.lcd.LAYER_PLUGIN_OVERLAY:
            self.lcd.close_plugin_modal()

        spec = self.layout_info(text)

        if not self.lcd.open_plugin_modal(spec['rect']):
            return

        self.draw_info(spec, self.lcd.LAYER_PLUGIN_OVERLAY)

        self._info_owner = 'plugin'
        self._info_opened = time.ticks_ms()
        self._info_timeout = self.INFO_TIMEOUT_MS

        self.lcd.show()

    def close_any_info(self):
        if self._info_owner == 'system':
            self.lcd.close_system_modal()
        elif self._info_owner == 'plugin':
            self.lcd.close_plugin_modal()

        self._info_owner = None

    def tick(self, key=False, atras=False):
        """Actualiza modales INFO.

        Devuelve:
            None      -> no hay INFO activo
            'active'  -> INFO sigue activo
            'closed'  -> INFO se cerró en este tick
        """
        if self._info_owner is None:
            return None

        now = time.ticks_ms()

        if key or atras or time.ticks_diff(now, self._info_opened) >= self._info_timeout:
            self.close_any_info()
            return 'closed'

        return 'active'