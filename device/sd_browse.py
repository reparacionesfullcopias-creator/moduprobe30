# ============================================================
# sd_browse.py — Navegador de archivos SD
#   - Mantiene estado de navegación (ruta, selección, offset, items)
#   - Lista directorios y navega por ellos
#   - NO monta/desmonta la tarjeta (responsabilidad de services.py)
#   - NO controla el LED de estado (responsabilidad de services.py)
#   - NO almacena estado global de UI (responsabilidad de AppState)
#
# Principio: inyección de dependencias para consultar estado de SD.
# ============================================================

import os
import gc


class SDBrowser:
    """Navegador de archivos SD.
    
    Responsabilidades:
    - Mantener estado de navegación (ruta, selección, offset, items).
    - Listar directorios y navegar por ellos.
    
    NO es responsable de:
    - Montar/desmontar la tarjeta (eso lo hace services.py en core 1).
    - Controlar el LED de estado (eso lo hace services.py en core 1).
    - Almacenar el estado global de la UI (eso lo hace AppState en main.py).
    """

    def __init__(self, spi_bus, pin_cs, pin_miso, pin_mosi, pin_sck, baudrate, visibles,
                 sd_presente_fn=None, nav_inicio_fn=None, nav_fin_fn=None):
        # Configuración de Hardware SD (se mantienen por si en el futuro
        # este módulo necesita acceso directo, aunque hoy no monta nada).
        self.spi_bus = spi_bus
        self.pin_cs = pin_cs
        self.pin_miso = pin_miso
        self.pin_mosi = pin_mosi
        self.pin_sck = pin_sck
        self.baudrate = baudrate

        # Función inyectada para consultar si la SD está presente/montada.
        # Por defecto siempre False; main.py debe inyectar services.sd_presente.
        self._sd_presente = sd_presente_fn or (lambda: False)

        # F-53: funciones inyectadas para el blindaje de navegacion.
        # Cada listado declara inicio/fin a core 1 (services.nav_inicio /
        # services.nav_fin) para que posponga cualquier umount de FatFs
        # mientras core 0 esta leyendo. Si no se inyectan, son no-op.
        self._nav_inicio = nav_inicio_fn or (lambda: None)
        self._nav_fin = nav_fin_fn or (lambda: None)

        # Configuración de Interfaz
        self.visibles = visibles

        # Estado de la Interfaz (solo navegación)
        self.ruta_actual = "/sd"
        self.lista_items = []
        self.offset = 0
        self.seleccion = 0
        self.error_sd = ""

    def _listar_ruta(self, ruta):
        items = []
        error = ""
        it = None
        # F-53: blindaje activo durante TODO el ilistdir. Si la tarjeta se
        # extrae a mitad de lectura, el OSError lo captura el except de
        # abajo (degradacion elegante); lo que este blindaje evita es el
        # umount CONCURRENTE de core 1, que si puede tirar un hard fault.
        self._nav_inicio()
        try:
            dirs = []
            files = []
            it = os.ilistdir(ruta)
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                nombre = entry[0]
                tipo = entry[1]
                if tipo == 0x4000:
                    dirs.append(nombre)
                else:
                    files.append(nombre)
            dirs.sort()
            files.sort()
            # Mostramos ".." en todas las carpetas, incluida la raíz, para poder volver al Dashboard
            items.append(("..", True))
            for d in dirs:
                items.append((d, True))
            for f in files:
                items.append((f, False))
        except Exception as e:
            error = str(e)
            print("[SD] Error al listar:", error)
        finally:
            self._nav_fin()
            if it is not None:
                del it
            gc.collect()
        return items, error

    def cargar_sd_y_listar(self):
        """Lista la raíz de /sd.
        
        Verifica primero, a través de la función inyectada, que la SD
        esté realmente montada por el servicio de core 1.
        
        Devuelve True si listó bien, False si hubo error o SD no disponible.
        """
        if not self._sd_presente():
            self.error_sd = "SD no disponible"
            self.lista_items = []
            return False

        self.ruta_actual = "/sd"
        self.seleccion = 0
        self.offset = 0
        items, error = self._listar_ruta(self.ruta_actual)
        if error:
            self.error_sd = error
            self.lista_items = []
            return False
        self.error_sd = ""
        self.lista_items = items
        return True

    def reset_estado(self):
        """Limpia solo el estado de navegación.
        
        No toca estado global de la UI ni intenta desmontar la SD,
        ya que esas responsabilidades pertenecen a otros módulos.
        """
        self.ruta_actual = "/sd"
        self.lista_items = []
        self.seleccion = 0
        self.offset = 0
        self.error_sd = ""

    def _ajustar_scroll(self):
        total = len(self.lista_items)
        if total <= self.visibles:
            self.offset = 0
            return
        if self.seleccion < self.offset:
            self.offset = self.seleccion
        elif self.seleccion >= self.offset + self.visibles:
            self.offset = self.seleccion - self.visibles + 1

    def cambiar_seleccion(self, delta):
        total = len(self.lista_items)
        if total == 0:
            return
        nuevo = self.seleccion + delta
        if nuevo < 0:
            nuevo = 0
        elif nuevo >= total:
            nuevo = total - 1
        if nuevo != self.seleccion:
            self.seleccion = nuevo
            self._ajustar_scroll()

    def _subir_directorio(self):
        """Sube un nivel. Devuelve True si OK, False si hubo error.
        F-16: en la raiz, "subir" resetea el estado de navegacion (posicion
        y seleccion vuelven a cero para la proxima visita) — el cambio de
        modo a dashboard lo decide main.py, como siempre."""
        if self.ruta_actual == "/sd":
            self.reset_estado()
            return True
        partes = self.ruta_actual.split("/")
        if len(partes) <= 2:
            self.ruta_actual = "/sd"
        else:
            partes.pop()
            self.ruta_actual = "/".join(partes)
        items, error = self._listar_ruta(self.ruta_actual)
        if error:
            self.error_sd = error
            self.lista_items = []
            return False
        self.error_sd = ""
        self.lista_items = items
        self.seleccion = 0
        self.offset = 0
        return True

    def entrar_en_seleccion(self):
        """Entra en la selección actual.
        Devuelve True si la navegación continúa, False si hubo error.
        """
        total = len(self.lista_items)
        if total == 0:
            return True
        nombre, es_dir = self.lista_items[self.seleccion]
        if nombre == "..":
            return self._subir_directorio()
        if es_dir:
            nueva_ruta = self.ruta_actual + "/" + nombre
            items, error = self._listar_ruta(nueva_ruta)
            if error:
                self.error_sd = error
                self.lista_items = []
                return False
            self.ruta_actual = nueva_ruta
            self.lista_items = items
            self.seleccion = 0
            self.offset = 0
            self.error_sd = ""
            return True
        ruta_completa = self.ruta_actual + "/" + nombre
        print("[SD] Archivo seleccionado:", ruta_completa)
        # Aquí más adelante podemos devolver la ruta para abrir el archivo
        return True