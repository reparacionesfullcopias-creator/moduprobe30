# ============================================================
# services.py — Núcleo de servicios en CORE 1 (ModuProbe 2.0)
#   FIX v2.0.2: Motor de audio corregido (sin cambio de frecuencia en finally)
# ============================================================

import _thread
import time
import os
import sys
import gc
import json
import framebuf
from machine import Pin, PWM, SPI
import sdcard
import log

# ============================================================
# CONSTANTES DE SISTEMA
# ============================================================

MODULOS_RESERVADOS = {
    "boot", "main", "time", "os", "sys", "machine", "gc",
    "framebuf", "st7567", "writer", "encoder", "sd_browse",
    "fontbase", "fontnokia", "fontarcade", "json", "gpu",
    "screens", "sdcard", "fonts", "services", "_thread",
    "log", "buttons_pio", "fpsmeter",
    "writer3", "fontbase3", "fontnokia3", "fontarcade3"
}

MAX_NOMBRE_MODULO = 8
MAX_TEXTO_MENU = 12

# F-42: items base del sistema. Siempre al final de la lista virtual del
# dashboard (tools + base); no viven en la SD ni en el indice.
BASE_ITEMS = ("SD Card", "Opciones", "Acerca de")  # F-59: era "Acerca"
BASE_SCRIPTS = ("__sd__", "__opciones__", "__acerca__")

SD_SPI_BUS = 0
SD_CS = 17
SD_MISO = 16
SD_MOSI = 19
SD_SCK = 18
SD_CARD_DETECT = 22
LED_PIN = 25
BUZZER_PIN = 14

# ============================================================
# ESTABILIZACIÓN DE SD
# ============================================================

SD_CD_DEBOUNCE_MS = 750
SD_POST_MOUNT_SETTLE_MS = 120
SD_SCAN_RETRIES_PER_SPEED = 2
SD_SCAN_RETRY_DELAY_MS = 120
SD_MOUNT_ATTEMPTS = [1000000, 500000, 250000]
SD_MOUNT_RETRY_DELAY_MS = 120

# Iconos genéricos (48x48 MONO_VLSB)
HEX_NO_TOOL = "0000000000000000000000000000000000000000000000000000000000000000000000000000000080c06020000000000000000000ffffc36333333363e383030303030303030303030303030303030383c36130180c060301f8f800000000000000000000ffff03060c0c0c060781c0e07038183060000080c06030180c0603010000000000000000ffff00000000000000000000ffffc0e070381c0e07030180c06030180c060301183870e0c080c06030180c0c1c3870e0ffff000000000000000000003f3f0180c06030188c868381808080808080808080808080818180808080808080808080ffff000000000000000000080c060301000000000101010101010101010101010101010101010101010101010101010101000000000000"
HEX_CARD_SD = "0000000000000000000000f80c04c444c404c444c404f414f404c444c404f414f404c444c4c40404fcf80000000000000000000000000000000000ff00000f080f000f080f000f080f000f080f000f080f000f080f0f0000ffff0000000000000000000000000000f008060300000000000000000000000000000000000000000000000000000000ffff0000000000000000000000000000e1111f00f0f818f0f818f8f000e80070d8888800f0f808181060f08888f87000ffff0000000000000000000000000000ff0000000f0f000f0f000f0f40606c4c5a5a727232426062626676363c3c1800ffff00000000000000000000000000001f303030303030303030303030303030303030303030303030303030303030383f1f000000000000"

ICON_NO_TOOL = framebuf.FrameBuffer(bytearray.fromhex(HEX_NO_TOOL), 48, 48, framebuf.MONO_VLSB)
ICON_CARD_SD = framebuf.FrameBuffer(bytearray.fromhex(HEX_CARD_SD), 48, 48, framebuf.MONO_VLSB)


# ============================================================
# BUZÓN  (core 1 → core 0)
# ============================================================

_lock = _thread.allocate_lock()
_tool_activa = None   # F-45: tool en marcha (confinamiento de escritura)
_buzon = {
    'listo': False,
    'sd_presente': False,
    'sd_trabajando': False,
    'indice': b'',            # F-42: indice empaquetado, 8 bytes por nombre
    'n_tools': 0,
    'volumen': 50,
    'contraste': 55,
    'evento_sd': None,
    'dump_log': None,
    'dump_log_res': None,
    'dump_en_curso': False,
    'meta_req': None,         # F-44: .inf pedido por core 0 (FUNC)
    'meta_res': None,
    'meta_en_curso': False,
    'guardar_req': None,      # F-45: escritura mediada (tool o sistema)
    'guardar_res': None,
    'guardar_en_curso': False,
    'icon_req': None,         # F-49: icono del .inf pedido por core 0
    'icon_res': None,
    'icon_en_curso': False,
    'core1_heartbeat_ms': 0,
    'core1_healthy': False,
    'nav_ts': 0,              # F-53: blindaje de navegacion FatFs (sd_browse)
}


# ============================================================
# COLA DE AUDIO  (core 0 → core 1)
# ============================================================

_lock_audio = _thread.allocate_lock()
_cola_audio = []
_volumen = 50
_buzzer = None
_hilo_arrancado = False

_pin_cd = None
_led = None
_sd_montada = False
_cd_estable = 1
_cd_cambiado = False
_cd_tiempo = 0


# ============================================================
# API PÚBLICA DE AUDIO
# ============================================================

# F-13: tope de cola. Evita crecimiento sin limite si la UI encola
# mas rapido de lo que se reproduce. Se descartan las notas MAS VIEJAS:
# el feedback sonoro debe corresponder a la accion mas reciente.

_COLA_AUDIO_MAX = 16

def reproducir(freq, dur_ms=60, adsr=None):
    with _lock_audio:
        if len(_cola_audio) >= _COLA_AUDIO_MAX:
            _cola_audio.pop(0)
        _cola_audio.append((freq, dur_ms, adsr))

def reproducir_melodia(notas):
    with _lock_audio:
        for n in notas:
            if len(n) == 2:
                item = (n[0], n[1], None)
            else:
                item = (n[0], n[1], n[2])
            if len(_cola_audio) >= _COLA_AUDIO_MAX:
                _cola_audio.pop(0)
            _cola_audio.append(item)

def set_volumen(v):
    global _volumen
    _volumen = max(0, min(100, v))

# F-05: contador de generacion de audio. Cada silenciar() lo incrementa;
# _tocar_nota captura su valor al entrar y se aborta si cambia.
_generacion_audio = 0

def silenciar():
    """Apagado inmediato del buzzer: limpia la cola Y aborta la nota en curso."""
    global _generacion_audio
    with _lock_audio:
        _cola_audio.clear()
        _generacion_audio += 1
    if _buzzer is not None:
        try:
            _buzzer.duty_u16(0)
        except Exception:
            pass


# ============================================================
# API PÚBLICA DE LECTURA DEL BUZÓN
# ============================================================

def sd_lista():
    with _lock:
        return _buzon['listo']

def sd_presente():
    with _lock:
        return _buzon['sd_presente']
    
def sd_trabajando():
    with _lock:
        return _buzon['sd_trabajando']

def n_tools():
    """F-42: numero de tools publicadas en el indice."""
    with _lock:
        return _buzon['n_tools']

def obtener_config():
    with _lock:
        return (_buzon['volumen'], _buzon['contraste'])

def _nombre_en(pos, indice, n):
    """Nombre en la posicion virtual pos (tools + 3 base); None fuera de rango."""
    if 0 <= pos < n:
        return indice[pos * 8:pos * 8 + 8].rstrip(b'\x00').decode()
    if n <= pos < n + 3:
        return BASE_ITEMS[pos - n]
    return None

def ventana(seleccion, ancho=11):
    """F-42: ventana de 'ancho' nombres consecutivos de la lista virtual
    (tools + base) que contiene 'seleccion'. Devuelve (inicio_absoluto,
    lista de nombres). Se calcula sobre el indice empaquetado (8 B/nombre):
    en RAM solo se materializa la ventana, nunca el menu completo.
    No toca la SD: lectura de memoria bajo lock, coste microsegundos."""
    with _lock:
        indice = _buzon['indice']
        n = _buzon['n_tools']
        total = n + 3
        if seleccion < 0:
            seleccion = 0
        if seleccion >= total:
            seleccion = total - 1
        if total <= ancho:
            ini = 0
            fin = total
        else:
            ini = seleccion - (ancho // 2)
            if ini < 0:
                ini = 0
            if ini > total - ancho:
                ini = total - ancho
            fin = ini + ancho
        nombres = []
        for pos in range(ini, fin):
            nom = _nombre_en(pos, indice, n)
            if nom and pos < n:
                nom = _formatear_nombre(nom)  # display: "Rraid", no "rraid"
            nombres.append(nom)
    return ini, nombres

def salto_alfabetico(seleccion, direccion):
    """F-42: salto alfabetico sobre la lista virtual (tools + base).
    direccion: -1 arriba, +1 abajo. Sin wrap-around: en el borde se queda.
    (Mismo comportamiento que el antiguo _salto_alfabetico de main.py,
    ahora calculado aqui sobre el indice empaquetado.)"""
    with _lock:
        indice = _buzon['indice']
        n = _buzon['n_tools']
        total = n + 3
        if seleccion < 0 or seleccion >= total:
            return seleccion
        actual = _nombre_en(seleccion, indice, n)
        if not actual:
            return seleccion
        inicial = actual[0].upper()
        if direccion > 0:
            for i in range(seleccion + 1, total):
                nom = _nombre_en(i, indice, n)
                if nom and nom[0].upper() != inicial:
                    return i
            return total - 1
        else:
            for i in range(seleccion - 1, -1, -1):
                nom = _nombre_en(i, indice, n)
                if nom and nom[0].upper() != inicial:
                    return i
            return 0

def nombre_tool(pos):
    """F-42: nombre de modulo de la tool en la posicion virtual pos.
    None si la posicion es un item base o esta fuera de rango."""
    with _lock:
        indice = _buzon['indice']
        n = _buzon['n_tools']
        if 0 <= pos < n:
            return _nombre_en(pos, indice, n)
    return None

def consumir_evento_sd():
    with _lock:
        ev = _buzon['evento_sd']
        _buzon['evento_sd'] = None
        return ev

def solicitar_dump_log(path='/sd/flexprobe.log'):
    # F-18: core 0 NUNCA toca la SD (asimetria de propiedad). Pide el
    # volcado por buzon; core 1 lo ejecuta en su bucle.
    with _lock:
        _buzon['dump_log'] = path

def consumir_resultado_dump():
    # F-18: resultado del ultimo volcado (True/False) o None si no hay.
    # Se consume al leer (patron de consumir_evento_sd).
    with _lock:
        r = _buzon['dump_log_res']
        if r is not None:
            _buzon['dump_log_res'] = None
        return r


def solicitar_metadata(nombre_modulo):
    # F-44: core 0 pide a core 1 el .inf de una tool (asimetria de
    # propiedad de la SD). El resultado llega por 'meta_res'.
    with _lock:
        _buzon['meta_res'] = None  # descartar resultado viejo sin consumir
        _buzon['meta_req'] = nombre_modulo

def consumir_metadata():
    # F-44: metadata del .inf de la ultima peticion (dict con claves
    # name/version/description/copyright/icon) o None si aun no llega.
    with _lock:
        r = _buzon['meta_res']
        if r is not None:
            _buzon['meta_res'] = None
        return r


def solicitar_icono(nombre_modulo, pos):
    # F-49: core 0 pide el icono del .inf de una tool (lectura en el
    # tiempo de ocio de core 1; best-effort silencioso, sin toast: si
    # falla el dashboard se queda con el icono generico). 'pos' viaja en
    # la respuesta para descartarla si el cursor ya se movio.
    with _lock:
        _buzon['icon_res'] = None  # descartar resultado viejo sin consumir
        _buzon['icon_req'] = (nombre_modulo, pos)

def consumir_icono():
    # F-49: (pos, nombre, bytearray|None) de la ultima peticion, o None.
    with _lock:
        r = _buzon['icon_res']
        if r is not None:
            _buzon['icon_res'] = None
        return r


def _fichero_seguro(nombre):
    # F-45: nombre de fichero valido para escribir dentro de la carpeta
    # de la tool. Sin rutas, sin retrocesos, sin ocultos.
    if not nombre or len(nombre) > 64:
        return False
    if '/' in nombre or '\\' in nombre or '..' in nombre:
        return False
    if nombre[0] == '.':
        return False
    return True

def fijar_tool_activa(nombre):
    # F-45: main.py notifica que tool esta en marcha (o None al cerrar).
    # El confinamiento de escritura se aplica en core 1 contra este valor.
    global _tool_activa
    with _lock:
        _tool_activa = nombre

def guardar_archivo(nombre_fich, datos, append=False):
    # F-45: escritura mediada para TOOLS. Escribe SOLO dentro de
    # /sd/tools/<tool_activa>/ (confinamiento validado en core 1).
    # Devuelve False inmediato si no hay tool activa o el nombre no es
    # seguro; el resultado real (True/False) llega por 'guardar_res'.
    # Trozos recomendados <= 4 KB (entre trozo y trozo el bucle de core 1
    # alimenta el heartbeat).
    with _lock:
        tool = _tool_activa
    if not tool or not _fichero_seguro(nombre_fich):
        return False
    with _lock:
        _buzon['guardar_res'] = None  # descartar resultado viejo sin consumir
        _buzon['guardar_req'] = {'tool': tool, 'fich': nombre_fich,
                                 'datos': datos, 'append': append}
    return True

def guardar_config_sistema(volumen, contraste):
    # F-45: guardado de la config del sistema (flexprob.cfg). Mismo camino
    # mediado sin confinamiento de tool. Antes lo escribia core 0
    # directamente (guardar_config); con esto core 1 es el UNICO escritor
    # de la SD, serializado en su bucle.
    datos = json.dumps({'volumen': volumen, 'contraste': contraste})
    with _lock:
        _buzon['guardar_res'] = None  # descartar resultado viejo sin consumir
        _buzon['guardar_req'] = {'tool': None, 'ruta': '/sd/flexprob.cfg',
                                 'datos': datos, 'append': False}

def consumir_resultado_guardado():
    # F-45: resultado de la ultima escritura mediada (True/False) o None.
    with _lock:
        r = _buzon['guardar_res']
        if r is not None:
            _buzon['guardar_res'] = None
        return r


def _latir():
    """F-32: publica el latido del core 1 en el buzon.

    Se llama desde el bucle de servicios y desde dentro de _tocar_nota:
    una nota larga NO es una caida (antes, una cola de audio cuyo total
    superaba el umbral dejaba de alimentar el latido y disparaba el
    falso "Servicios no Responde").
    """
    with _lock:
        _buzon['core1_heartbeat_ms'] = time.ticks_ms()
        _buzon['core1_healthy'] = True


def core1_healthy(threshold_ms=3000):
    """F-32: health-check del core 1 mediante heartbeat del buzon.
    Durante una operacion intencionalmente larga (montaje/escaneo de la
    SD o volcado de log), core 1 NO debe contarse como caido: ese estado
    tiene prioridad sobre el umbral de latido.
    """
    with _lock:
        if (_buzon['sd_trabajando'] or _buzon['dump_en_curso']
                or _buzon['meta_en_curso'] or _buzon['guardar_en_curso']
                or _buzon['icon_en_curso']):
            _buzon['core1_healthy'] = True
            return True
        last = _buzon['core1_heartbeat_ms']
        now = time.ticks_ms()
        stale = time.ticks_diff(now, last) > threshold_ms
        _buzon['core1_healthy'] = not stale
        return not stale


# F-46: watchdog. main (core 0) lo crea y es su dueno; core 1 recibe la
# referencia SOLO para alimentarlo en los tramos largos de montaje y
# escaneo de la SD, donde su bucle esta bloqueado y no publica latido.
_wdt = None

def registrar_wdt(w):
    global _wdt
    _wdt = w

def _feed_wdt():
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass


# ============================================================
# MOTOR DE AUDIO (corre en core 1)  —  FIX v2.0.2
# ============================================================

def _tocar_nota(freq, dur_ms, adsr=None):
    # F-05: se captura la generacion al entrar (junto al volumen, un solo lock).
    with _lock_audio:
        vol = _volumen
        gen = _generacion_audio

    if vol <= 0 or freq <= 0 or dur_ms <= 0:
        return

    max_duty = int((vol / 100) * 32768)

    if adsr is None:
        a, d, s_pct, r = 2, 0, 100, 3
    else:
        try:
            a, d, s_pct, r = adsr
        except Exception:
            a, d, s_pct, r = 2, 0, 100, 3

    a = max(0, int(a))
    d = max(0, int(d))
    s_pct = max(0, min(100, int(s_pct)))
    r = max(0, int(r))

    total_env = a + d + r
    if total_env > dur_ms:
        factor = dur_ms / total_env if total_env > 0 else 0
        a = int(a * factor)
        d = int(d * factor)
        r = int(r * factor)
        while a + d + r > dur_ms and r > 0:
            r -= 1
        while a + d + r > dur_ms and d > 0:
            d -= 1
        while a + d + r > dur_ms and a > 0:
            a -= 1

    sus_duty = max_duty * s_pct // 100

    # Lectura sin lock: es un int, el GIL garantiza atomicidad.
    # El lock solo protege la cola (operaciones sobre listas).
    def _abortado():
        return _generacion_audio != gen

    try:
        _buzzer.freq(freq)

        if a > 0:
            for i in range(1, a + 1):
                if _abortado():
                    return
                _buzzer.duty_u16(max_duty * i // a)
                time.sleep_ms(1)
                _latir()
        else:
            _buzzer.duty_u16(max_duty)

        if d > 0:
            for i in range(1, d + 1):
                if _abortado():
                    return
                _buzzer.duty_u16(max_duty - (max_duty - sus_duty) * i // d)
                time.sleep_ms(1)
                _latir()

        restante = dur_ms - a - d - r
        if restante > 0:
            _buzzer.duty_u16(sus_duty)
            # F-05/F-07: sustain en tramos de 50 ms -> abortable en <=50 ms
            # (y deja listo el punto donde el WDT se alimentara en su dia).
            while restante > 0:
                if _abortado():
                    return
                paso = 50 if restante > 50 else restante
                time.sleep_ms(paso)
                restante -= paso
                _latir()

        if r > 0:
            for i in range(1, r + 1):
                if _abortado():
                    return
                _buzzer.duty_u16(sus_duty - sus_duty * i // r)
                time.sleep_ms(1)
                _latir()

    finally:
        # Apagado seguro: solo duty 0, NO cambiar frecuencia
        try:
            if _buzzer is not None:
                _buzzer.duty_u16(0)
        except Exception:
            pass


# ============================================================
# HELPERS GENERALES
# ============================================================

def _errno(e):
    try:
        return e.args[0]
    except Exception:
        return None

def _nombre_importable(nombre):
    if not nombre or len(nombre) > MAX_NOMBRE_MODULO:
        return False
    if nombre[0].isdigit():
        return False
    for c in nombre:
        if not (('a' <= c <= 'z') or ('A' <= c <= 'Z') or ('0' <= c <= '9') or c == '_'):
            return False
    return True

def _formatear_nombre(nombre_modulo):
    texto = nombre_modulo.replace("_", " ").strip()
    if not texto:
        return nombre_modulo
    salida = ""
    for parte in texto.split(" "):
        if not parte:
            continue
        if salida:
            salida += " "
        salida += parte[0].upper() + parte[1:]
    return salida

def _leer_metadata(ruta_base, nombre_modulo):
    meta = {
        "name": _formatear_nombre(nombre_modulo),
        "icon": None,
        "version": "",
        "description": "",
        "copyright": ""
    }
    try:
        with open(ruta_base + "/" + nombre_modulo + ".inf", "r") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            if data.get("name"):
                meta["name"] = str(data["name"])[:MAX_TEXTO_MENU * 2]
            if data.get("icon"):
                meta["icon"] = str(data["icon"])
            if data.get("version"):
                meta["version"] = str(data["version"])[:20]
            if data.get("description"):
                meta["description"] = str(data["description"])[:500]
            if data.get("copyright"):
                meta["copyright"] = str(data["copyright"])[:60]
    except Exception:
        pass
    try:
        with open(ruta_base + "/" + nombre_modulo + ".manifest.json", "r") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            if data.get("api_version"):
                meta["api_version"] = str(data["api_version"])[:20]
            if data.get("requires_adc"):
                meta["requires_adc"] = [int(p) for p in data.get("requires_adc", []) if str(p).isdigit()]
            if data.get("requires_pio"):
                meta["requires_pio"] = [int(p) for p in data.get("requires_pio", []) if str(p).isdigit()]
            if data.get("min_firmware"):
                meta["min_firmware"] = str(data["min_firmware"])[:20]
    except Exception:
        pass
    return meta


def _leer_icono(nombre_modulo):
    # F-49: icono del .inf de una tool. Estricto: solo 48x48 MONO_VLSB
    # (exactamente 576 hex chars = 288 bytes; el slot del dashboard es
    # fijo). Ausente, vacio o invalido -> None (icono generico).
    try:
        with open("/sd/tools/" + nombre_modulo + "/" + nombre_modulo
                  + ".inf", "r") as f:
            data = json.loads(f.read())
        icono = data.get("icon") if isinstance(data, dict) else None
        if isinstance(icono, str) and len(icono) == 576:
            return bytearray.fromhex(icono)
    except Exception:
        pass
    return None


# ============================================================
# ESCANEO DE TOOLS
# ============================================================

def _escanear_tools_once():
    """F-43: escaneo carpeta-only. Cada tool es una carpeta
    /sd/tools/<nombre>/ que contiene <nombre>.py (y opcionalmente
    <nombre>.inf y auxiliares, que NUNCA se escanean).
    Devuelve (indice, estable): indice = bytearray con 8 bytes por nombre
    (orden alfabetico) o None si no hay carpeta de tools; estable = False
    si el listado fallo a mitad (reintento a otra velocidad).
    Sin lectura de .inf ni iconos en el escaneo: la metadata se lee bajo
    peticion (F-44) y el icono del dashboard es estatico.
    """
    nombres = []
    no_py = 0
    planos = 0
    try:
        st = os.stat("/sd/tools")
        if (st[0] & 0xF000) != 0x4000:
            return None, True
    except OSError as e:
        if _errno(e) == 2:
            return None, True
        return None, False

    entries = None
    try:
        entries = os.ilistdir("/sd/tools")
        for entry in entries:
            try:
                arch = entry[0]
                tipo = entry[1] if len(entry) > 1 else None
            except Exception:
                continue

            if not arch or arch.startswith(".") or arch.startswith("_"):
                log.info("Scan tools: '%s' descartado (oculto)", arch)
                continue

            if tipo == 0x4000:
                # Carpeta candidata a tool.
                if not _nombre_importable(arch):
                    log.info("Scan tools: '%s' descartado (no importable, %d chars)",
                             arch, len(arch))
                    continue
                if arch.lower() in MODULOS_RESERVADOS:
                    log.info("Scan tools: '%s' descartado (nombre reservado)", arch)
                    continue
                try:
                    st = os.stat("/sd/tools/" + arch + "/" + arch + ".py")
                    if (st[0] & 0xF000) != 0x8000:
                        log.info("Scan tools: '%s' descartado (sin script principal)", arch)
                        continue
                except OSError:
                    log.info("Scan tools: '%s' descartado (sin script principal)", arch)
                    continue
                nombres.append(arch)
            else:
                # Ficheros sueltos: nunca son tools en formato carpeta.
                if tipo == 0x8000 and arch.lower().endswith(".py"):
                    planos += 1
                    log.info("Scan tools: '%s' formato plano: migrar a carpeta", arch)
                else:
                    no_py += 1
    except OSError:
        return None, False
    finally:
        if entries is not None:
            try:
                del entries
            except Exception:
                pass
    if no_py:
        log.info("Scan tools: %d entradas no-.py ignoradas", no_py)
    nombres.sort(key=str.lower)
    indice = bytearray(len(nombres) * 8)
    for i in range(len(nombres)):
        b = nombres[i].encode()
        indice[i * 8:i * 8 + len(b)] = b
    gc.collect()
    return indice, True

def _escanear_tools_estable():
    last_tools = []
    for intento in range(SD_SCAN_RETRIES_PER_SPEED):
        _feed_wdt()  # F-46: feed por tramo entre reintentos de escaneo
        tools, estable = _escanear_tools_once()
        last_tools = tools
        if estable:
            return tools, True
        print("[SERV] Escaneo de tools inestable, reintento", intento + 1)
        if intento < SD_SCAN_RETRIES_PER_SPEED - 1:
            time.sleep_ms(SD_SCAN_RETRY_DELAY_MS)
    return last_tools, False


# ============================================================
# GESTOR SD (corre en core 1)
# ============================================================

def _montar_sd_speed(baud):
    global _sd_montada
    # F-52: el LED ya no parpadea por intento; la lectura completa lo
    # mantiene solido (_montar_y_publicar).
    spi = None
    try:
        try:
            os.umount("/sd")
        except OSError:
            pass
        cs = Pin(SD_CS, Pin.OUT, value=1)
        spi = SPI(SD_SPI_BUS, baudrate=baud, polarity=0, phase=0, sck=Pin(SD_SCK), mosi=Pin(SD_MOSI), miso=Pin(SD_MISO, Pin.IN))
        sd = sdcard.SDCard(spi, cs, baudrate=baud)
        vfs = os.VfsFat(sd)
        os.mount(vfs, "/sd")
        _sd_montada = True
        print("[SERV] SD montada a", baud, "Hz")
        return True
    except Exception as e:
        print("[SERV] Error montando SD a", baud, "Hz:", e)
        _sd_montada = False
        if spi is not None:
            try:
                spi.deinit()
            except Exception:
                pass
        return False
    finally:
        gc.collect()

def _desmontar_sd():
    global _sd_montada
    if _sd_montada:
        try:
            os.umount("/sd")
            print("[SERV] SD desmontada.")
        except Exception:
            pass
        _sd_montada = False
        gc.collect()


# ============================================================
# F-53: BLINDAJE DE NAVEGACION (core 0 lee FatFs via sd_browse)
# ============================================================

# El browser de core 0 declara inicio/fin de cada ilistdir via el buzon.
# Mientras el blindaje este fresco, core 1 pospone el umount por
# extraccion (el evento se reintenta en _poll_sd cada 50 ms). Asi el
# teardown de FatFs jamas convive con un ilistdir de core 0: sin ese
# serialismo, umount + iterador abierto en core 0 puede tirar un hard
# fault que ni el supervisor F-04b puede capturar.
# El blindaje caduca solo (NAV_SHIELD_MS): si core 0 muriera a mitad de
# listado, el desmonte se ejecuta igual.

NAV_SHIELD_MS = 3000

def nav_inicio():
    """Core 0 (sd_browse): declara inicio de listado FatFs."""
    with _lock:
        _buzon['nav_ts'] = time.ticks_ms()

def nav_fin():
    """Core 0 (sd_browse): declara fin del listado FatFs."""
    with _lock:
        _buzon['nav_ts'] = 0

def _nav_activo():
    """Core 1: hay una lectura de navegacion vigente (con caducidad)?"""
    ts = 0
    with _lock:
        ts = _buzon['nav_ts']
    if ts == 0:
        return False
    return time.ticks_diff(time.ticks_ms(), ts) < NAV_SHIELD_MS

# ============================================================
# RANGOS VALIDOS DE CONFIGURACION (F-10: fuente unica de verdad)
# main.py y screens.py derivan de estos valores (F-19 unificara).
# ============================================================
VOLUMEN_MIN = 0
VOLUMEN_MAX = 100
CONTRASTE_MIN = 45
CONTRASTE_MAX = 63

def _cargar_config_sd():
    try:
        with open("/sd/flexprob.cfg", "r") as f:
            data = json.load(f)
        vol = data.get("volumen", 50)
        con = data.get("contraste", 55)
    except Exception:
        return (50, 55)

    # Validacion de tipo: un .cfg editado a mano puede traer strings,
    # null o floats. Sin esto, max(0, "ochenta" - 5) revienta main.
    try:
        vol = int(vol)
    except Exception:
        vol = 50
    try:
        con = int(con)
    except Exception:
        con = 55

    # Clamp a rango de hardware/UI
    vol = max(VOLUMEN_MIN, min(VOLUMEN_MAX, vol))
    con = max(CONTRASTE_MIN, min(CONTRASTE_MAX, con))
    return (vol, con)

def _publicar_menu(indice, evento=None, cargar_cfg=False):
    """F-42: publica el indice empaquetado y el total en el buzon.
    'indice' es un bytearray (8 bytes/nombre) o None si no hay carpeta de
    tools. Los items base no viajan: son constantes (BASE_ITEMS)."""
    montada = _sd_montada
    n = 0 if indice is None else len(indice) // 8

    with _lock:
        _buzon['sd_presente'] = montada
        _buzon['sd_trabajando'] = False
        _buzon['indice'] = indice if indice is not None else b''
        _buzon['n_tools'] = n

        if cargar_cfg and montada:
            vol, con = _cargar_config_sd()
            _buzon['volumen'] = vol
            _buzon['contraste'] = con
            # F-15: core 1 (dueno del audio) aplica el volumen al cargar
            # la config. UNA sola aplicacion por arranque: main ya no la
            # repite, solo LEE estos valores para su UI.
            set_volumen(vol)

        _buzon['listo'] = True
        if evento:
            _buzon['evento_sd'] = evento

def _montar_y_publicar(evento=None, cargar_cfg=False):
    """F-52: envoltura de lectura de SD. Enciende el LED GP25 durante
    TODA la operacion (montaje + escaneo) y lo apaga al terminar, con
    exito o sin el (cubre boot, insercion y re-escaneo)."""
    _led.value(1)
    try:
        _montar_y_publicar_impl(evento, cargar_cfg)
    finally:
        _led.value(0)

def _montar_y_publicar_impl(evento=None, cargar_cfg=False):
    speeds = SD_MOUNT_ATTEMPTS
    for idx, baud in enumerate(speeds):
        _feed_wdt()  # F-46: feed por tramo entre velocidades de montaje
        if not _montar_sd_speed(baud):
            time.sleep_ms(SD_MOUNT_RETRY_DELAY_MS)
            continue
        # F-46: montaje OK; antes del settle + escaneo (tramo largo)
        _feed_wdt()
        time.sleep_ms(SD_POST_MOUNT_SETTLE_MS)
        if _pin_cd.value() != 0:
            _desmontar_sd()
            _publicar_menu(None, evento='extraida', cargar_cfg=False)
            return
        tools, estable = _escanear_tools_estable()
        if estable:
            _publicar_menu(tools, evento=evento if _sd_montada else None, cargar_cfg=cargar_cfg)
            return
        if idx < len(speeds) - 1:
            print("[SERV] Escaneo inestable a", baud, "Hz; probando velocidad menor")
            _desmontar_sd()
            time.sleep_ms(SD_SCAN_RETRY_DELAY_MS)
        else:
            print("[SERV] Escaneo inestable incluso a baja velocidad")
            _publicar_menu(tools, evento=evento, cargar_cfg=cargar_cfg)
            return
    # F-37: el montaje fallo a todas las velocidades. Resultado honesto
    # (solo fuera del boot, donde evento=None y el flujo no cambia):
    #   - tarjeta ya retirada (CD=1) -> 'extraida'
    #   - tarjeta sigue puesta       -> 'error_sd' (nuevo)
    if evento == 'insertada' and _pin_cd.value() != 0:
        _publicar_menu(None, evento='extraida', cargar_cfg=False)
        return
    ev = 'error_sd' if evento == 'insertada' else evento
    # F-57: publicar 'ev' (antes se calculaba y se descartaba: el evento
    # 'error_sd' nunca salia y main mostraba "Sin Tools" con tarjeta
    # puesta e ilegible).
    _publicar_menu(None, evento=ev, cargar_cfg=cargar_cfg)


# ============================================================
# CARD DETECT
# ============================================================

def _inicializar_card_detect():
    global _cd_estable, _cd_cambiado, _cd_tiempo
    _cd_estable = _pin_cd.value()
    _cd_cambiado = False
    _cd_tiempo = time.ticks_ms()

def _scan_inicial():
    _inicializar_card_detect()
    if _cd_estable == 0:
        _montar_y_publicar(evento=None, cargar_cfg=True)
    else:
        _publicar_menu(None, evento=None, cargar_cfg=False)

def _beep_deteccion():
    # F-52: pitido de deteccion de la SD, sincrono con el toast "Leyendo
    # tarjeta SD..." de core 0. Se toca DIRECTO (sin cola): el bucle que
    # drena la cola es este mismo hilo y esta a punto de bloquearse en
    # el montaje/escaneo; por cola, la nota sonaria al final de la
    # lectura. _tocar_nota alimenta el latido (sin falsa alarma F-32).
    try:
        _tocar_nota(2900, 80)
    except Exception:
        pass

def _on_insertar():
    # F-37: senal de trabajo ANTES de montar -> feedback temprano en core 0.
    with _lock:
        _buzon['sd_trabajando'] = True
    # F-52: beep de deteccion + LED solido desde ya (la envoltura de
    # _montar_y_publicar lo apaga al terminar, con exito o sin el).
    _led.value(1)
    _beep_deteccion()
    _montar_y_publicar(evento='insertada', cargar_cfg=False)

def _on_extraer():
    _desmontar_sd()
    _publicar_menu(None, evento='extraida', cargar_cfg=False)

def _poll_sd():
    global _cd_estable, _cd_cambiado, _cd_tiempo
    raw = _pin_cd.value()
    ahora = time.ticks_ms()
    if raw != _cd_estable:
        if not _cd_cambiado:
            _cd_cambiado = True
            _cd_tiempo = ahora
        elif time.ticks_diff(ahora, _cd_tiempo) >= SD_CD_DEBOUNCE_MS:
            # F-53: extraccion con lectura de navegacion en curso en core 0.
            # El evento NO se consume: se reintenta en el proximo poll
            # (50 ms) hasta que caduque el blindaje (max NAV_SHIELD_MS).
            # Si la tarjeta se reinserta antes de cometer el evento, el
            # estado estable nunca cambio y no se dispara remontaje.
            if raw != 0 and _nav_activo():
                return
            _cd_cambiado = False
            _cd_estable = raw
            if raw == 0:
                _on_insertar()
            else:
                _on_extraer()
    else:
        _cd_cambiado = False


# ============================================================
# BUCLE PRINCIPAL DEL SERVICIO (core 1)
# ============================================================

def _bucle_servicios():
    global _buzzer, _pin_cd, _led

    _buzzer = PWM(Pin(BUZZER_PIN))
    _buzzer.freq(2000)
    _buzzer.duty_u16(0)

    _pin_cd = Pin(SD_CARD_DETECT, Pin.IN)
    _led = Pin(LED_PIN, Pin.OUT, value=0)

    # F-14: si el escaneo inicial falla, el hilo SOBREVIVE y _poll_sd
    # seguira vigiando el CD (antes: muerte silenciosa en el arranque).
    try:
        _scan_inicial()
    except Exception as e:
        print("[SERV] Excepcion en escaneo inicial:", e)

    ultimo_poll_sd = time.ticks_ms()

    while True:
        try:
            # F-32: latido del core 1. Dentro de _tocar_nota tambien se
            # alimenta: una nota larga no es una caida.
            _latir()

            trabajo = False

            # 1) Drenar la cola de audio
            while True:
                with _lock_audio:
                    nota = _cola_audio.pop(0) if _cola_audio else None
                if nota is None:
                    break
                trabajo = True
                _tocar_nota(*nota)

            # 2) Vigilar la SD cada 50 ms
            ahora = time.ticks_ms()
            if time.ticks_diff(ahora, ultimo_poll_sd) >= 50:
                ultimo_poll_sd = ahora
                _poll_sd()

            # 3) Volcado de log solicitado por core 0 (F-18)
            pide_dump = None
            with _lock:
                pide_dump = _buzon['dump_log']
                if pide_dump:
                    _buzon['dump_log'] = None
            if pide_dump:
                trabajo = True
                # F-32: un volcado sobre SD moribunda puede estancar el
                # bucle; se marca como ocupacion intencional para que core
                # 0 no lo cuente como caida (patron de sd_trabajando).
                with _lock:
                    _buzon['dump_en_curso'] = True
                try:
                    ok_dump = log.dump_to_sd(pide_dump)
                finally:
                    with _lock:
                        _buzon['dump_en_curso'] = False
                if ok_dump:
                    log.info("Log volcado a %s", pide_dump)
                    with _lock:
                        _buzon['dump_log_res'] = True
                else:
                    log.error("Fallo volcado de log a %s", pide_dump)
                    with _lock:
                        _buzon['dump_log_res'] = False

            # 4) Metadata .inf pedida por core 0 al pulsar FUNC (F-44)
            pide_meta = None
            with _lock:
                pide_meta = _buzon['meta_req']
                if pide_meta:
                    _buzon['meta_req'] = None
            if pide_meta:
                trabajo = True
                # F-32: una lectura sobre SD moribunda puede estancar el
                # bucle; ocupacion intencional (patron de dump_en_curso).
                with _lock:
                    _buzon['meta_en_curso'] = True
                try:
                    meta = _leer_metadata("/sd/tools/" + pide_meta, pide_meta)
                finally:
                    with _lock:
                        _buzon['meta_en_curso'] = False
                with _lock:
                    _buzon['meta_res'] = meta

            # 5) Escritura mediada (F-45): tool (confinada a su carpeta)
            # o sistema. Core 1 es el UNICO escritor de la SD.
            pide_guardar = None
            with _lock:
                pide_guardar = _buzon['guardar_req']
                if pide_guardar:
                    _buzon['guardar_req'] = None
            if pide_guardar:
                trabajo = True
                if pide_guardar['tool']:
                    ruta_g = None
                    if _fichero_seguro(pide_guardar['fich']):
                        ruta_g = ("/sd/tools/" + pide_guardar['tool']
                                  + "/" + pide_guardar['fich'])
                else:
                    ruta_g = pide_guardar['ruta']
                if ruta_g:
                    ok_g = False
                    # F-32: ocupacion intencional durante la escritura
                    with _lock:
                        _buzon['guardar_en_curso'] = True
                    try:
                        modo_g = 'ab' if pide_guardar['append'] else 'wb'
                        with open(ruta_g, modo_g) as f_g:
                            f_g.write(pide_guardar['datos'])
                        ok_g = True
                    except Exception as e:
                        log.error("Fallo escritura %s: %s", ruta_g, e)
                        ok_g = False
                    finally:
                        with _lock:
                            _buzon['guardar_en_curso'] = False
                else:
                    ok_g = False
                with _lock:
                    _buzon['guardar_res'] = ok_g

            # 6) Icono del .inf pedido por core 0 (F-49, best-effort
            # silencioso; con SD moribunda el blindaje evita la falsa
            # alarma y el dashboard se queda con el icono generico).
            pide_icono = None
            with _lock:
                pide_icono = _buzon['icon_req']
                if pide_icono:
                    _buzon['icon_req'] = None
            if pide_icono:
                trabajo = True
                with _lock:
                    _buzon['icon_en_curso'] = True
                try:
                    buf_icono = _leer_icono(pide_icono[0])
                finally:
                    with _lock:
                        _buzon['icon_en_curso'] = False
                with _lock:
                    _buzon['icon_res'] = (pide_icono[1], pide_icono[0],
                                          buf_icono)

            # 7) Sleep adaptativo
            if trabajo:
                time.sleep_ms(2)
            else:
                time.sleep_ms(20)
        except Exception as e:
            # F-14: red de seguridad del core 1. Antes, cualquier excepcion
            # aqui mataba el hilo en silencio (sin audio, sin eventos SD).
            # Ahora: evidencia en consola, aviso luminico y el hilo sigue.
            # Exception, NO BaseException: consistente con I5.
            # F-52a: traceback completo a consola (la cadena exacta de la
            # excepcion; clave para diagnosticar el RecursionError).
            print("[SERV] Excepcion en bucle servicios:", e)
            sys.print_exception(e)
            for _ in range(3):
                _led.value(1)
                time.sleep_ms(80)
                _led.value(0)
                time.sleep_ms(80)
            time.sleep_ms(500)

def iniciar_servicios():
    global _hilo_arrancado
    if _hilo_arrancado:
        return
    _hilo_arrancado = True
    # F-52a: el hilo de servicios ejecuta la cadena Python mas profunda
    # del sistema (~10 frames en el escaneo: bucle->poll->on_insertar->
    # montar->impl->estable->once->log->push) sobre la profundidad C de
    # FatFs dentro del montaje. Con el stack por defecto del hilo esa
    # cadena rozaba el limite y la envoltura F-52 (+1 frame) lo paso:
    # "maximum recursion depth exceeded" justo tras "SD montada".
    # Margen explicito de 16 KB para la pila del hilo (coste: ~16 KB de
    # heap menos en mem_free; recortable tras validar si sobra).
    try:
        _thread.stack_size(16384)
    except Exception:
        pass
    _thread.start_new_thread(_bucle_servicios, ())