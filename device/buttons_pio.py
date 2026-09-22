# ============================================================
# buttons_pio.py — Lector de teclado 74HC165D vía PIO
#   - Lee el estado del teclado y lo deposita en la FIFO del PIO.
#   - Python solo drena la FIFO y obtiene el último byte "foto".
#
# Pines: GP2 = SH/LD, GP3 = CLK, GP4 = Q7
# PIO0 SM0 reservado para el sistema.
# ============================================================

import rp2
from machine import Pin

# ============================================================
# CONSTANTES DE TECLADO (bitmasks)
# ============================================================

KEY_DOWN   = 0x02
KEY_LEFT   = 0x04
KEY_BACK   = 0x08
KEY_RIGHT  = 0x10
KEY_OK     = 0x20
KEY_UP     = 0x40
KEY_FUNC   = 0x80

ALL_KEYS = KEY_DOWN | KEY_LEFT | KEY_BACK | KEY_RIGHT | KEY_OK | KEY_UP | KEY_FUNC

@rp2.asm_pio(
    sideset_init=(rp2.PIO.OUT_HIGH, rp2.PIO.OUT_LOW),
    autopush=True,
    push_thresh=8
)
def pio_74hc165():
    wrap_target()
    nop()                      .side(0b00) # SH/LD=0, CLK=0 (Carga paralela)
    nop()                      .side(0b01) # SH/LD=1, CLK=0 (Modo shift)
    set(x, 7)                  .side(0b01)
    
    label("bucle_bits")
    in_(pins, 1)               .side(0b01) # Leer Q7, CLK=0
    jmp(x_dec, "bucle_bits")  .side(0b11) # CLK=1 (Desplaza siguiente bit)
    wrap()

class Keypad:
    def __init__(self, sh_ld_pin=2, clk_pin=3, q7_pin=4, sm_id=0):
        if clk_pin != sh_ld_pin + 1:
            raise ValueError("CLK debe ser SH/LD + 1 para side-set consecutivo.")
        
        self._data_pin = Pin(q7_pin, Pin.IN)
        
        self.sm = rp2.StateMachine(
            sm_id,
            pio_74hc165,
            freq=5000,
            in_base=self._data_pin,
            sideset_base=Pin(sh_ld_pin)
        )
        self.sm.active(1)
        self._last_keys = 0xFF

    def get_keys(self):
        """Devuelve el byte "foto" más reciente del teclado."""
        while self.sm.rx_fifo():
            self._last_keys = self.sm.get() & 0xFF
        return self._last_keys

    def deinit(self):
        """Desactiva la máquina de estados PIO y restablece los pines GPIO."""
        self.sm.active(0)
        # FIX: Se reconfigura el objeto Pin existente directamente mediante .init()
        self._data_pin.init(mode=Pin.IN, pull=None)