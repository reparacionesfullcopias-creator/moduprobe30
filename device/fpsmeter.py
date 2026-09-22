# fpsmeter.py — F-34: medidor de rendimiento in-vivo (TEMPORAL, solo desarrollo)
#
# Uso:
#   1) Ctrl+D -> lanzar la tool "Fps" desde el menu
#   2) Salir con RESET -> OK  (el medidor SIGUE midiendo en el menu)
#   3) Usar el equipo: la consola imprime [FPS] cada ~2 s
#   4) Informe final: Ctrl+C y ejecutar:  import fpsmeter; fpsmeter.report()
#   5) Ctrl+D reinicia limpio y desinstala todo
#
# Notas:
#   - Un menu en reposo no repinta: 0 fps es eficiencia, no cuelgue.
#   - loop/s se infla mientras un modal de error espera tecla.

import time

_st = {'s': None, 'lcd': None}


def install(lcd):
    """Instala el medidor sobre el objeto GPU vivo (idempotente)."""
    if getattr(lcd, '_fps', None) is not None:
        _reset(lcd._fps)
        print("[FPS] ya instalado; contadores reiniciados")
        return

    s = {
        'orig_show': lcd.show,
        't0': time.ticks_ms(), 't_win': time.ticks_ms(),
        'frames': 0, 'frames_win': 0,
        'loop': 0, 'loop_win': 0,
        'sum_us': 0, 'max_us': 0, 'sum_win': 0, 'max_win': 0,
        'n': 0,
    }
    lcd._fps = s
    _st['s'] = s
    _st['lcd'] = lcd

    def show():
        t = time.ticks_us()
        s['orig_show']()
        dt = time.ticks_diff(time.ticks_us(), t)
        s['frames'] += 1
        s['frames_win'] += 1
        s['sum_us'] += dt
        s['sum_win'] += dt
        s['n'] += 1
        if dt > s['max_us']:
            s['max_us'] = dt
        if dt > s['max_win']:
            s['max_win'] = dt
        _ventana(s)

    lcd.show = show
    _patch_loop(s)
    print("[FPS] medidor instalado")


def _patch_loop(s):
    """Cuenta iteraciones del bucle: get_keys se llama 1 vez por iteracion."""
    import buttons_pio
    if getattr(buttons_pio.Keypad, '_fps_patched', False):
        return
    orig_gk = buttons_pio.Keypad.get_keys

    def get_keys(self):
        r = orig_gk(self)
        s2 = _st['s']
        if s2 is not None:
            s2['loop'] += 1
            s2['loop_win'] += 1
            _ventana(s2)
        return r

    buttons_pio.Keypad.get_keys = get_keys
    buttons_pio.Keypad._fps_patched = True


def _ventana(s):
    now = time.ticks_ms()
    d = time.ticks_diff(now, s['t_win'])
    if d < 2000:
        return
    fps = s['frames_win'] * 1000 // d
    lps = s['loop_win'] * 1000 // d
    nf = s['frames_win']
    if nf > 0:
        avg = s['sum_win'] // nf
        print("[FPS] %4d ms | %2d fps | show %d.%03d ms | peor %d.%03d ms | loop %3d/s"
              % (d, fps, avg // 1000, avg % 1000,
                 s['max_win'] // 1000, s['max_win'] % 1000, lps))
    else:
        print("[FPS] %4d ms |  0 fps (sin repintados) | loop %3d/s" % (d, lps))
    s['t_win'] = now
    s['frames_win'] = 0
    s['loop_win'] = 0
    s['sum_win'] = 0
    s['max_win'] = 0


def report():
    """Informe detallado de la sesion. Desde REPL tras Ctrl+C."""
    s = _st['s']
    if s is None:
        print("[FPS] no instalado")
        return
    dur = time.ticks_diff(time.ticks_ms(), s['t0'])
    print("=== INFORME FPS ===")
    print("duracion     : %d.%03d s" % (dur // 1000, dur % 1000))
    if dur > 0 and s['n'] > 0:
        print("frames       : %d  (%d fps promedio sesion)"
              % (s['frames'], s['frames'] * 1000 // dur))
        avg = s['sum_us'] // s['n']
        print("show medio   : %d.%03d ms" % (avg // 1000, avg % 1000))
        print("show PEOR    : %d.%03d ms" % (s['max_us'] // 1000, s['max_us'] % 1000))
        print("loop total   : %d  (%d/s promedio)" % (s['loop'], s['loop'] * 1000 // dur))
    else:
        print("frames       : 0  (no hubo repintados)")
    print("===================")


def _reset(s):
    s['t0'] = time.ticks_ms()
    s['t_win'] = time.ticks_ms()
    s['frames'] = 0
    s['frames_win'] = 0
    s['loop'] = 0
    s['loop_win'] = 0
    s['sum_us'] = 0
    s['max_us'] = 0
    s['sum_win'] = 0
    s['max_win'] = 0
    s['n'] = 0


def uninstall():
    """Restaura lcd.show original. (Normalmente sobra: Ctrl+D limpia todo.)"""
    lcd = _st['lcd']
    s = _st['s']
    if lcd is not None and s is not None:
        lcd.show = s['orig_show']
        del lcd._fps
        _st['s'] = None
        _st['lcd'] = None
        print("[FPS] medidor desinstalado")