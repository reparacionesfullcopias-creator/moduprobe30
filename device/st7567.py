import time
import framebuf


class ST7567_SPI(framebuf.FrameBuffer):
    def __init__(self, width, height, spi, dc, rst=None, cs=None, col_offset=0, row_offset=0):
        # El área dibujable real de esta pantalla es 132x64.
        # Si se pasa 132x65, se limita a 132x64 para evitar página 9.
        if height > 64:
            height = 64

        self.width = width
        self.height = height
        self.spi = spi
        self.dc = dc
        self.rst = rst
        self.cs = cs

        # Para 64px de alto, pages = 8.
        self.pages = (self.height + 7) // 8

        # Buffer de pantalla:
        # 132 * 8 = 1056 bytes para 132x64.
        self.buffer = bytearray(self.pages * self.width)
        self.view = memoryview(self.buffer)

        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)

        # Offsets para ajustar la imagen a la pantalla física.
        self.col_offset = col_offset & 0x7F
        self.row_offset = row_offset & 0x3F
        
        # F-35: comandos de pagina precomputados (col_offset es fijo tras
        # el init) + vistas de datos pre-sliceadas. show() pasa a 16
        # transacciones SPI sin allocs (antes: 24 write_cmd + 8 views).
        col = self.col_offset
        hi = 0x10 | ((col >> 4) & 0x0F)
        lo = 0x00 | (col & 0x0F)
        self._cmd_pages = [bytearray((0xB0 + p, hi, lo)) for p in range(self.pages)]
        self._data_pages = [
            self.view[p * self.width:(p + 1) * self.width]
            for p in range(self.pages)
        ]

        # Buffer reutilizable para comandos.
        self._cmd_buf = bytearray(1)

        # Estado inicial de pines de control.
        self.dc.value(1)
        if self.cs is not None:
            self.cs.value(1)

        self.init_display()

    def write_cmd(self, cmd):
        self._cmd_buf[0] = cmd
        self.dc.value(0)

        if self.cs is not None:
            self.cs.value(0)

        self.spi.write(self._cmd_buf)

        if self.cs is not None:
            self.cs.value(1)

    def write_data(self, data):
        self.dc.value(1)

        if self.cs is not None:
            self.cs.value(0)

        self.spi.write(data)

        if self.cs is not None:
            self.cs.value(1)

    def init_display(self):
        if self.rst is not None:
            self.rst.value(0)
            time.sleep_ms(50)
            self.rst.value(1)
            time.sleep_ms(50)

        # Display OFF durante la inicialización.
        self.write_cmd(0xAE)

        # Software reset.
        self.write_cmd(0xE2)
        time.sleep_ms(20)

        # Bias 1/9.
        self.write_cmd(0xA2)

        # Dirección ADC normal.
        self.write_cmd(0xA0)

        # Dirección de salida COM inversa.
        self.write_cmd(0xC8)

        # Relación de regulación interna.
        self.write_cmd(0x23)

        # Configurar contraste electrónico.
        self.write_cmd(0x81)
        self.write_cmd(0x3F)

        # Circuito de potencia encendido.
        self.write_cmd(0x2F)
        time.sleep_ms(10)

        # Línea de inicio de pantalla.
        self.write_cmd(0x40 | (self.row_offset & 0x3F))

        # Modo de visualización normal.
        self.write_cmd(0xA4)

        # No invertir colores.
        self.write_cmd(0xA6)

        # Limpiar RAM antes de encender la pantalla.
        self.fill(0)
        self.show()

        # Display ON.
        self.write_cmd(0xAF)
        time.sleep_ms(10)

    def contrast(self, value):
        self.write_cmd(0x81)
        self.write_cmd(value & 0x3F)

    def show(self):
        # F-35: 8 paginas x (1 transaccion de comandos + 1 de datos).
        # Comandos y slices de datos precomputados en __init__: cero
        # allocs por show. El ST7567 interpreta cada byte con DC=0 como
        # comando, por lo que los 3 comandos caben en una transaccion.
        dc = self.dc
        cs = self.cs
        spi = self.spi

        for i in range(self.pages):
            dc.value(0)
            if cs is not None:
                cs.value(0)
            spi.write(self._cmd_pages[i])

            dc.value(1)
            spi.write(self._data_pages[i])

            if cs is not None:
                cs.value(1)