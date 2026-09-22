
import time

class LogBuffer:
    __slots__ = ('_buf', '_size', '_idx', '_count', '_enabled', '_total', '_dumped')

    def __init__(self, size=64):
        self._buf = [None] * size
        self._size = size
        self._idx = 0
        self._count = 0
        self._enabled = True
        self._total = 0    # F-54: entradas añadidas desde boot (monótono)
        self._dumped = 0   # F-54: entradas ya volcadas a SD

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def _now(self):
        try:
            return time.ticks_ms()
        except Exception:
            return 0

    def _push(self, entry):
        self._buf[self._idx] = entry
        self._idx = (self._idx + 1) % self._size
        self._count = min(self._count + 1, self._size)
        self._total += 1

    def info(self, msg, *args):
        if not self._enabled:
            return
        try:
            self._push(('I', self._now(), msg % args if args else msg))
        except Exception:
            pass

    def error(self, msg, *args):
        if not self._enabled:
            return
        try:
            self._push(('E', self._now(), msg % args if args else msg))
        except Exception:
            pass

    def crash(self, exc_type, exc_value, tb_text):
        if not self._enabled:
            return
        try:
            self._push(('C', self._now(), '%s: %s' % (exc_type, exc_value)))
            if tb_text:
                for line in str(tb_text).splitlines()[:8]:
                    self._push(('C', self._now(), line))
        except Exception:
            pass

    def dump(self):
        lines = []
        if self._count == 0:
            return lines
        start = self._idx if self._count == self._size else 0
        for i in range(self._count):
            idx = (start + i) % self._size
            entry = self._buf[idx]
            if entry is None:
                continue
            tag, ts, msg = entry
            lines.append((tag, ts, msg))
        return lines

    def dump_to_sd(self, path='/sd/flexprobe.log'):
        # F-54: volcado solo-nuevas. _total/_dumped cuentan de forma
        # monotona; entre dumps solo se escriben las entradas nuevas
        # (antes se re-escribian hasta 64 entradas retenidas en cada
        # volcado, acumulando duplicados en el fichero). Si entre dos
        # dumps se rotaron mas entradas de las retenidas, se marca el
        # hueco en el separador para que el fichero sea honesto.
        total = self._total
        pendientes = total - self._dumped
        if pendientes <= 0:
            return False
        retenidas = self.dump()
        n = min(pendientes, len(retenidas))
        perdidas = pendientes - n
        nuevas = retenidas[len(retenidas) - n:]
        try:
            with open(path, 'a') as f:
                # F-18: separador por volcado (el fichero es append; sin
                # marca, los re-volcados serian ilegibles).
                if perdidas:
                    f.write('--- dump @ %d ms (%d entradas nuevas, %d perdidas por rotacion) ---\n'
                            % (time.ticks_ms(), n, perdidas))
                else:
                    f.write('--- dump @ %d ms (%d entradas nuevas) ---\n'
                            % (time.ticks_ms(), n))
                for tag, ts, msg in nuevas:
                    f.write('%d %s %s\n' % (ts, tag, msg))
        except Exception:
            return False    # sin marcar: el proximo dump reintenta estas entradas
        self._dumped = total
        return True

    def clear(self):
        self._buf = [None] * self._size
        self._idx = 0
        self._count = 0
        self._total = 0
        self._dumped = 0


log = LogBuffer()

# --- Fachada a nivel de módulo ---
# Permite llamar log.info(...) / log.error(...) directamente sobre el módulo.
# Sin esto, esas llamadas lanzan AttributeError (la instancia es log.log).
def info(msg, *args):
    log.info(msg, *args)

def error(msg, *args):
    log.error(msg, *args)

def crash(exc_type, exc_value, tb_text):
    log.crash(exc_type, exc_value, tb_text)

def clear():
    log.clear()

def dump():
    return log.dump()

def dump_to_sd(path='/sd/flexprobe.log'):
    return log.dump_to_sd(path)

def enable():
    log.enable()

def disable():
    log.disable()
