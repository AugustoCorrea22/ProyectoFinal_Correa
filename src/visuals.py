"""
Generador visual (Edición Balatro 60 FPS - Alta Legibilidad):
Dibuja con el Canvas de customtkinter (formas suavizadas), sin imágenes:
1. Cartas de Comodines, Tarots y Tienda de alta legibilidad: textos grandes y nítidos,
   insignias de efecto claras (+50 FICHAS, X2 MULTI, etc.).
2. La ruleta como ítems del Canvas: girarla solo cambia ángulos, sin volver a dibujarla.
3. Fichas de casino dinámicas sobre cada casilla apostada del tapete.
"""

import math
from pathlib import Path
import tkinter as tk
from typing import Dict, List, Optional, Tuple

from src.board import SpecialChipType
from src.streaks import StreakState
from src.wheel import EUROPEAN_WHEEL_SEQUENCE, Color, Pocket, RouletteWheel


# Paleta oficial inspirada en Balatro
BALATRO_BG_DARK = (19, 26, 31)
BALATRO_SLATE = (34, 43, 52)
BALATRO_SLATE_DARK = (22, 28, 35)
BALATRO_FELT_GREEN = (29, 82, 61)
BALATRO_BLUE = (0, 157, 255)
BALATRO_RED = (254, 95, 85)
BALATRO_ORANGE = (242, 129, 45)
BALATRO_GOLD = (243, 185, 88)
BALATRO_PURPLE = (155, 89, 182)
BALATRO_TEAL = (43, 182, 115)

COLOR_HEX_MAP: Dict[Color, Tuple[int, int, int]] = {
    Color.GREEN: (43, 182, 115),
    Color.RED: (238, 66, 56),
    Color.BLACK: (36, 44, 54),
}

# Textos grandes de 3 líneas (Título, Bono Gigante, Condición) para lectura directa en cada carta
SHORT_CARD_EFFECTS: Dict[str, Tuple[str, str, str]] = {
    "RED_RUBY": ("RUBI ROJO", "+50 FICHAS", "SI SALE ROJO"),
    "BLACK_ONYX": ("ONIX NEGRO", "+50 FICHAS", "SI SALE NEGRO"),
    "ODD_SCHOLAR": ("ERUDITO IMPAR", "X2 MULTI", "SI SALE IMPAR"),
    "STRAIGHT_SNIPER": ("FRANCOTIRADOR", "+100 Y X2", "EN PLENO"),
    "ZERO_JESTER": ("BUFON CERO", "+150 FICHAS", "SI SALE EL 0"),
    "THE_SUN": ("EL SOL", "NEGRO -> ROJO", "POR 1 TIRO"),
    "THE_FOOL": ("EL LOCO", "BORRA EL 0", "POR 1 TIRO"),
    "WHEEL_OF_FORTUNE": ("LA RUEDA", "APUESTAS X2", "EN LA MESA"),
    "THE_MAGICIAN": ("EL MAGO", "IMPAR -> PAR", "POR 1 TIRO"),
    "TRIPLE_PAYOUT": ("FICHA TRIPLE", "PAGO X3", "EN 1 APUESTA"),
    "ZERO_SHIELD": ("ESCUDO CERO", "PROTEGE EL 0", "REEMBOLSA"),
    "MAGNET_CHIP": ("FICHA IMAN", "+30 FICHAS", "AL GANAR"),
    # Comodines nuevos
    "DANCE_PARTNER": ("PAREJA BAILE", "+1 MULTI", "MANO PAR"),
    "SENTINEL": ("CENTINELA", "+1 MULTI", "MANO CERCO"),
    "BODYGUARD": ("GUARDAESPALDAS", "+1.5 MULTI", "MANO ESCOLTA"),
    "NOSY_NEIGHBOR": ("VECINA", "+1 MULTI", "MANO VECINOS"),
    "EVEN_PERFECT": ("PAR PERFECTO", "+40 FICHAS", "SI SALE PAR"),
    "WALL_STREET_BULL": ("TORO BOLSA", "+1 POR $5", "DE TU BANCA"),
    "COLLECTOR": ("COLECCIONISTA", "+0.25 MULTI", "POR COMODIN"),
    "ECHO": ("ECO", "PLENO X2", "FICHAS DOBLES"),
    "LOADED_DIE": ("DADO CARGADO", "+0 A +2", "MULTI AL AZAR"),
    "HOT_STREAK": ("RACHA ARDIENTE", "+0.25 MULTI", "POR VICTORIA"),
    "MELTING_ICE": ("HELADO", "+100 FICHAS", "SE DERRITE"),
    "LUCKY_CHIP": ("FICHA SUERTE", "X2 MULTI", "1/6 SE ROMPE"),
    "GOLD_BAR": ("LINGOTE", "+$25", "POR CIEGA"),
    "GOLDEN_EGG": ("HUEVO DE ORO", "VENTA +$10", "POR CIEGA"),
    "GUARDIAN_ANGEL": ("ANGEL GUARDA", "TE SALVA", "SI LLEGAS 75%"),
    "VIP_TABLE": ("MESA VIP", "+1 CASILLA", "EN LA MESA"),
    # Tarots nuevos
    "THE_MOON": ("LA LUNA", "ROJO -> NEGRO", "POR 1 TIRO"),
    "THE_HERMIT": ("EL ERMITANO", "BANCA X2", "MAXIMO +$40"),
    "TEMPERANCE": ("TEMPLANZA", "COBRA VENTA", "DE COMODINES"),
    "JUDGEMENT": ("EL JUICIO", "+1 COMODIN", "ALEATORIO"),
    "HIGH_PRIESTESS": ("SACERDOTISA", "+1 NIVEL", "A 2 MANOS"),
    "THE_EMPEROR": ("EL EMPERADOR", "+2 TAROTS", "ALEATORIOS"),
    "THE_HANGED_MAN": ("EL COLGADO", "QUITA 3", "NO APOSTADOS"),
    "THE_STAR": ("LA ESTRELLA", "+1 MULTI", "PROXIMO GIRO"),
}


def _hex(rgb: Tuple[int, ...]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _is_dark(color: str) -> bool:
    return int(color[1:3], 16) + int(color[3:5], 16) + int(color[5:7], 16) < 120


def _rounded_points(x1, y1, x2, y2, r) -> List[float]:
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class VisualAssetGenerator:
    """
    Dibuja toda la estética de Balatro con ítems del Canvas de Tkinter: cartas legibles,
    botones píldora, fichas sobre el tapete, logo, Ruletín y la ruleta.
    Las coordenadas son de diseño: la interfaz luego las escala al tamaño de la pantalla.
    """

    RULETIN_SIZE = (170, 190)

    def __init__(self, wheel_size: int = 228) -> None:
        self.design_wheel_size: int = wheel_size
        self.wheel_size: int = wheel_size
        self.scale: float = 1.0
        self.canvas = None

    def bind_canvas(self, canvas) -> None:
        self.canvas = canvas

    def set_wheel_size(self, wheel_size: int) -> None:
        """Tamaño real de la ruleta en pantalla (de ahí salen los radios de la bola)."""
        self.wheel_size = wheel_size

    def _font(self, size: float) -> Tuple[str, int, str]:
        """Fuente en píxeles (tamaño negativo en Tk), ajustada a la escala de pantalla."""
        return ("Segoe UI", -max(5, round(size * self.scale)), "bold")

    def _circle(self, x, y, radius, color, tags=()):
        """Círculo suavizado: customtkinter lo dibuja como una letra de su fuente de formas."""
        px = max(1, round(radius * self.scale))
        return self._circle_px(x, y, px, color, tags)

    def _circle_px(self, x, y, px, color, tags=()):
        # Llamada directa a Tcl: se crean cientos por pantalla y así cuesta la mitad
        canvas = self.canvas
        return canvas.tk.getint(canvas.tk.call(
            canvas._w, "create", "text", x, y, "-text", canvas._get_char_from_radius(px), "-fill", color,
            "-font", ("CustomTkinter_shapes_font", -2 * px), "-tags", tags,
        ))

    def _disc(self, cx, cy, radius, fill, outline="", width=0, tags=()) -> None:
        if outline and width:
            self._circle(cx, cy, radius, outline, tags)
            self._circle(cx, cy, radius - max(1, round(width * self.scale)) / self.scale, fill, tags)
        else:
            self._circle(cx, cy, radius, fill, tags)

    def _solid_rrect(self, x1, y1, x2, y2, r, color, tags, k) -> None:
        # Se ajusta a la grilla de píxeles para que rectángulos y esquinas calcen sin costuras
        x1, y1, x2, y2 = (round(v * k) / k for v in (x1, y1, x2, y2))
        if x2 <= x1 or y2 <= y1:
            return
        px = max(0, min(round(r * k), int((x2 - x1) * k / 2), int((y2 - y1) * k / 2)))
        rd = px / k
        call, widget = self.canvas.tk.call, self.canvas._w
        call(widget, "create", "rectangle", x1 + rd, y1, x2 - rd, y2, "-fill", color, "-width", 0, "-tags", tags)
        if px:
            call(widget, "create", "rectangle", x1, y1 + rd, x2, y2 - rd, "-fill", color, "-width", 0, "-tags", tags)
            for cx, cy in ((x1 + rd, y1 + rd), (x2 - rd, y1 + rd), (x1 + rd, y2 - rd), (x2 - rd, y2 - rd)):
                self._circle_px(cx, cy, px, color, tags)

    def _rrect_fast(self, x1, y1, x2, y2, r, fill, outline="", width=0, tags=()):
        """Rectángulo redondeado de un solo ítem (sombras y marcos sin relleno)."""
        return self.canvas.create_polygon(
            _rounded_points(x1, y1, x2, y2, r), smooth=True, splinesteps=8,
            fill=fill, outline=outline, width=width, tags=tags,
        )

    def _rrect(self, x1, y1, x2, y2, r, fill, outline="", width=0, tags=(), k=None, fill_tags=()) -> None:
        """Rectángulo redondeado suavizado. Con k=1 las coordenadas ya son píxeles de pantalla."""
        k = self.scale if k is None else k
        if not fill:
            self._rrect_fast(x1, y1, x2, y2, r, fill, outline, width, tags)
        elif outline and width:
            w = max(1, round(width * k)) / k
            if _is_dark(outline):
                # Un borde oscuro sobre fondo oscuro no necesita suavizado: basta un solo ítem
                self.canvas.create_polygon(
                    _rounded_points(x1, y1, x2, y2, r), smooth=True, splinesteps=8, fill=outline, width=0, tags=tags
                )
            else:
                self._solid_rrect(x1, y1, x2, y2, r, outline, tags, k)
            self._solid_rrect(x1 + w, y1 + w, x2 - w, y2 - w, max(0, r - w), fill, tuple(tags) + tuple(fill_tags), k)
        else:
            self._solid_rrect(x1, y1, x2, y2, r, fill, tuple(tags) + tuple(fill_tags), k)

    def draw_button(self, cx, cy, width, height, bg_rgb, tags, face_tag=None) -> None:
        """Botón píldora moderno estilo Balatro: sombra dura y cara con borde oscuro."""
        x0, y0 = cx - width / 2, cy - (height + 6) / 2
        self._rrect(x0 + 2, y0 + 5, x0 + width - 2, y0 + height + 4, 12, "#0A0E12", tags=tags)
        self._rrect(
            x0 + 2, y0 + 2, x0 + width - 2, y0 + height + 1, 12, _hex(bg_rgb),
            outline="#141A20", width=3, tags=tags, fill_tags=(face_tag,) if face_tag else (),
        )

    def _chip_ticks(self, cx, cy, r_inner, r_outer, step, width, tags) -> None:
        for a_deg in range(0, 360, step):
            rad = math.radians(a_deg)
            self.canvas.create_line(
                cx + math.cos(rad) * r_inner, cy + math.sin(rad) * r_inner,
                cx + math.cos(rad) * r_outer, cy + math.sin(rad) * r_outer,
                fill="#FFFFFF", width=width, tags=tags,
            )

    def draw_chip(self, center_x, center_y, denomination, base_rgb, size=42, selected=False, tags=()) -> None:
        """Ficha de póker/casino con relieve y muescas."""
        x0, y0 = center_x - (size + 4) / 2, center_y - (size + 6) / 2
        y_off = 0 if selected else 3
        oval = self.canvas.create_oval
        oval(x0 + 4, y0 + 6, x0 + size, y0 + size + 4, fill="#080C10", outline="", tags=tags)
        cx, cy = x0 + size / 2.0, y0 + (size - 4) / 2.0 + y_off
        self._disc(cx, cy, (size - 4) / 2.0, _hex(base_rgb), "#FFEB6E" if selected else "#F5F5F0", 3, tags)
        r_outer = (size - 8) / 2.0
        self._chip_ticks(cx, cy, r_outer - 3, r_outer, 45, 3, tags)
        self._disc(cx, cy, (size - 20) / 2.0, "#181E26", "#F3B958", 2, tags)
        self.canvas.create_text(
            cx, cy, text="ALL" if denomination < 0 else f"${denomination}",
            fill="#F3B958" if denomination < 0 else "#FFFFFF",
            font=self._font(11 if 0 < denomination < 100 else 9), tags=tags,
        )

    def draw_bet_token(self, center_x, center_y, amount, special_chip=None, size=32, tags=()) -> None:
        """Ficha apostada que aparece DIRECTAMENTE sobre la casilla del tapete."""
        if amount >= 100:
            rgb = (155, 89, 182)
        elif amount >= 50:
            rgb = (243, 185, 88)
        elif amount >= 25:
            rgb = (43, 182, 115)
        elif amount >= 10:
            rgb = (0, 157, 255)
        else:
            rgb = (254, 95, 85)
        ring = "#FFFFFF"
        if special_chip == SpecialChipType.TRIPLE_PAYOUT:
            ring = "#FFD700"
        elif special_chip == SpecialChipType.ZERO_SHIELD:
            ring = "#50FFA0"
        elif special_chip == SpecialChipType.MAGNET_CHIP:
            ring = "#B478FF"

        x0, y0 = center_x - (size + 4) / 2, center_y - (size + 4) / 2
        oval = self.canvas.create_oval
        oval(x0 + 3, y0 + 4, x0 + size + 1, y0 + size + 2, fill="#05080C", outline="", tags=tags)
        cx, cy = x0 + size / 2.0, y0 + size / 2.0
        self._disc(cx, cy, (size - 2) / 2.0, _hex(rgb), ring, 2, tags)
        r_m = (size - 4) / 2.0
        self._chip_ticks(cx, cy, r_m - 2.5, r_m, 60, 2, tags)
        self._disc(cx, cy, (size - 10) / 2.0, "#141920", "#F3B958", 1, tags)
        label = f"${amount}" if amount < 100 else f"{amount}"
        self.canvas.create_text(
            cx, cy, text=label, fill="#FFFFFF", font=self._font(9 if len(label) <= 3 else 8), tags=tags
        )

    def draw_card(
        self,
        x,
        y,
        title,
        subtitle,
        badge_text,
        accent_rgb,
        symbol_key,
        price=None,
        width=112,
        height=122,
        highlighted=False,
        tags=(),
    ) -> None:
        """
        Carta estilo Balatro con TEXTO MUY GRANDE Y LEGIBLE: cinta con el tipo y precio,
        nombre, caja central oscura con el BONO GIGANTE y la condición abajo.
        """
        accent = _hex(accent_rgb)
        center = x + (width - 4) / 2
        text = self.canvas.create_text

        # Sombra dura, cuerpo y cinta superior
        self._rrect(x + 4, y + 5, x + width - 1, y + height - 1, 11, "#080A0E", tags=tags)
        self._rrect(
            x + 1, y + 1, x + width - 5, y + height - 5, 11, "#F8F4E8",
            outline="#F3B958" if highlighted else "#141A22", width=3, tags=tags,
        )
        self._rrect(x + 5, y + 5, x + width - 9, y + 26, 6, accent, tags=tags)
        font_badge = self._font(11)
        if price is None:
            text(center, y + 7, anchor="n", text=badge_text, fill="#FFFFFF", font=font_badge, tags=tags)
        else:
            text(x + 10, y + 7, anchor="nw", text=badge_text, fill="#FFFFFF", font=font_badge, tags=tags)

        short_name, big_formula, condition_txt = SHORT_CARD_EFFECTS.get(
            symbol_key,
            (title.upper()[:12], f"NV+ {symbol_key[:6]}", "SUBE EL PAGO"),
        )
        if badge_text == "NIVEL+":
            short_name = f"NV+ {symbol_key[:8]}"
            big_formula = "MAS PAGO"
            condition_txt = "PERMANENTE"
        elif badge_text == "MANO+":
            short_name = f"MANO {symbol_key}"
            big_formula = "+0.25 MULTI"
            condition_txt = "PERMANENTE"

        # 1. Nombre de la carta sobre el fondo crema
        text(center, y + 30, anchor="n", text=short_name, fill="#10141A", font=self._font(11), tags=tags)

        # 2. Bloque central oscuro con el EFECTO en letra grande y dorada
        mid_top, mid_bot = y + 48, y + height - 28
        self._rrect(x + 6, mid_top, x + width - 10, mid_bot, 7, "#141B24", outline=accent, width=2, tags=tags)
        formula_size = 13 if len(big_formula) <= 10 else 11
        if width >= 150:  # En las cartas grandes (tienda, inspector) el efecto crece con la carta
            fit_width = (width - 28) / (len(big_formula) * 0.62)
            formula_size = max(formula_size, int(min(fit_width, (mid_bot - mid_top) * 0.45)))
        text(
            center, (mid_top + mid_bot) / 2 - 2, text=big_formula, fill="#F3B958",
            font=self._font(formula_size), tags=tags,
        )

        # 3. Condición en la base
        text(center, y + height - 23, anchor="n", text=condition_txt, fill=accent, font=self._font(10), tags=tags)

        # Etiqueta de precio amarilla en la esquina superior derecha
        if price is not None:
            self._rrect(
                x + width - 50, y + 3, x + width - 5, y + 25, 6, "#F3B958",
                outline="#12161C", width=2, tags=tags,
            )
            text(x + width - 27, y + 6, anchor="n", text=f"${price}", fill="#12161C", font=font_badge, tags=tags)

    def draw_logo(self, center_x, center_y, title="THE ROULETE", width=700, height=130, tags=()) -> None:
        """Logo en cartas arqueadas."""
        x0, y0 = center_x - width / 2, center_y - height / 2
        self._rrect(x0 + 22, y0 + 24, x0 + width - 18, y0 + height - 14, 28, "#080C10", tags=tags)
        self._rrect(
            x0 + 18, y0 + 18, x0 + width - 22, y0 + height - 20, 28, "#161D26",
            outline="#F3B958", width=4, tags=tags,
        )
        chars = list(title)
        card_w, card_h, spacing = 52, 80, 58
        start_x = x0 + (width - len(chars) * spacing) // 2 + 4
        base_y = y0 + (height - card_h) // 2
        palette = [(254, 95, 85), (32, 40, 50), (0, 157, 255), (230, 155, 30)]
        for idx, ch in enumerate(chars):
            if ch == " ":
                continue
            cx = start_x + idx * spacing
            cy = base_y + int(math.sin((idx / max(1, len(chars) - 1)) * math.pi) * -12) + 4
            col = _hex(palette[idx % len(palette)])
            self._rrect(cx + 4, cy + 6, cx + card_w + 4, cy + card_h + 6, 9, "#080A0E", tags=tags)
            self._rrect(cx, cy, cx + card_w, cy + card_h, 9, "#F6F0E1", outline="#181E26", width=3, tags=tags)
            self._rrect(cx + 4, cy + 4, cx + card_w - 4, cy + card_h - 4, 6, "", outline=col, width=2, tags=tags)
            self._circle(cx + 10, cy + 10, 3, col, tags)
            self.canvas.create_text(
                cx + card_w / 2, cy + card_h / 2 - 2, text=ch, fill=col, font=self._font(38), tags=tags
            )

    # -------------------------------------------------------------------------
    # Ruletín: la mascota guía del casino
    # -------------------------------------------------------------------------
    def load_ruletin_photo(self, image_path: Optional[Path]):
        """Imagen propia del jugador (PNG o GIF), reducida o ampliada para entrar en el recuadro."""
        if image_path is None or not image_path.exists():
            return None
        try:
            photo = tk.PhotoImage(file=str(image_path))
        except tk.TclError:
            return None
        box_w, box_h = (v * self.scale for v in self.RULETIN_SIZE)
        ratio = max(photo.width() / box_w, photo.height() / box_h)
        if ratio > 1:
            return photo.subsample(math.ceil(ratio))
        return photo.zoom(max(1, int(1 / ratio)))

    def draw_ruletin(self, x0, y0, tags=()) -> None:
        """Ruletín provisorio: cabeza-ruleta con gorro de bufón y gran sonrisa."""
        h = self.RULETIN_SIZE[1]
        canvas = self.canvas
        ink = "#10141A"
        cx, cy, r = x0 + 85, y0 + 112, 62
        oval = canvas.create_oval
        # Sombra
        oval(cx - 50, y0 + h - 16, cx + 50, y0 + h - 4, fill="#3A3D40", outline="", tags=tags)
        # Gorro de bufón (dos puntas con cascabeles)
        canvas.create_polygon(
            cx - 52, cy - 38, cx - 70, cy - 98, cx - 6, cy - 52, fill="#EE4238", outline=ink, width=3, tags=tags
        )
        canvas.create_polygon(
            cx + 52, cy - 38, cx + 70, cy - 98, cx + 6, cy - 52, fill="#009DFF", outline=ink, width=3, tags=tags
        )
        self._disc(cx - 70, cy - 100, 10, "#F3B958", ink, 3, tags)
        self._disc(cx + 70, cy - 100, 10, "#F3B958", ink, 3, tags)
        # Aro de ruleta (casillas rojas y negras)
        self._circle(cx, cy, r + 4, ink, tags)
        for i in range(18):
            col = "#2BB673" if i == 13 else ("#EE4238" if i % 2 == 0 else "#242C36")
            canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r, start=-i * 20, extent=-20, style=tk.PIESLICE,
                fill=col, outline="", tags=tags,
            )
        oval(cx - r, cy - r, cx + r, cy + r, fill="", outline="#F3B958", width=4, tags=tags)
        # Cara
        fr = 44
        self._disc(cx, cy, fr, "#F8EED6", "#F3B958", 3, tags)
        for ex in (-16, 16):
            oval(cx + ex - 9, cy - 22, cx + ex + 9, cy - 2, fill="#FFFFFF", outline=ink, width=2, tags=tags)
            self._circle(cx + ex, cy - 10.5, 4.5, ink, tags)
        self._circle(cx - 25, cy + 6, 5, "#F5A79E", tags)
        self._circle(cx + 25, cy + 6, 5, "#F5A79E", tags)
        canvas.create_arc(
            cx - 20, cy - 4, cx + 20, cy + 28, start=180, extent=180, style=tk.PIESLICE,
            fill="#8C1E28", outline=ink, width=2, tags=tags,
        )
        # Bola blanca de la ruleta como nariz
        self._disc(cx, cy, 6, "#FFFFFF", "#009DFF", 2, tags)

    # -------------------------------------------------------------------------
    # Geometría de la ruleta
    # -------------------------------------------------------------------------
    def wheel_geometry(self) -> Tuple[float, float, float]:
        """Devuelve (radio_exterior, radio_interior, radio_pista_bola) en píxeles de pantalla."""
        outer_r = self.wheel_size // 2 - 12
        inner_r = int(outer_r * 0.57)
        ball_r = (outer_r + inner_r) / 2.0 - 11
        return float(outer_r), float(inner_r), ball_r

    @staticmethod
    def _wheel_sequence(wheel: RouletteWheel) -> Tuple[Dict[int, Pocket], List[int]]:
        active_map: Dict[int, Pocket] = {p.number: p for p in wheel.pockets}
        sequence = [num for num in EUROPEAN_WHEEL_SEQUENCE if num in active_map]
        return active_map, sequence

    def pocket_center_angle(
        self, wheel: RouletteWheel, number: int, rotation_deg: float = 0.0
    ) -> Optional[float]:
        """Ángulo (grados, horario desde las 3 en punto) del centro de un bolsillo."""
        _, sequence = self._wheel_sequence(wheel)
        if number not in sequence:
            return None
        step_angle = 360.0 / len(sequence)
        return rotation_deg + (sequence.index(number) + 0.5) * step_angle

    def create_ball(self, x, y, tags=()) -> Tuple[int, int]:
        """Bola de la ruleta: dos círculos suavizados que se mueven juntos."""
        return self._circle(x, y, 7.5, "#009DFF", tags), self._circle(x, y, 5.0, "#FFFFFF", tags)

    def create_wheel_view(
        self, wheel, cx, cy, rotation_deg=0.0, winning_number=None, streak_state=StreakState.NEUTRAL, tags=()
    ) -> "WheelView":
        return WheelView(self, wheel, cx, cy, rotation_deg, winning_number, streak_state, tags)


class WheelView:
    """
    Ruleta hecha de ítems del Canvas: para girarla solo se cambia el ángulo de cada
    bolsillo y de su número, sin volver a dibujarla (por eso anima a 60 FPS).
    """

    TAG = "wheel_view"

    def __init__(self, visuals, wheel, cx, cy, rotation_deg, winning_number, streak_state, tags) -> None:
        self.visuals = visuals
        canvas = self.canvas = visuals.canvas
        tags = (self.TAG,) + tuple(tags)
        size = visuals.design_wheel_size
        outer_r = size // 2 - 12
        inner_r = int(outer_r * 0.57)
        hub_r = int(outer_r * 0.25)
        self._text_r = (outer_r + inner_r) / 2.0 + 9
        self._ball_r = (outer_r + inner_r) / 2.0 - 11

        active_map, sequence = visuals._wheel_sequence(wheel)
        has_winner = winning_number is not None and winning_number in active_map
        self._step = 360.0 / len(sequence) if sequence else 0.0

        visuals._circle(cx + 2, cy + 4, outer_r + 8, "#080C10", tags)
        visuals._disc(cx, cy, outer_r + 8, "#141A22", "#F3B958", 4, tags)
        # Referencia invisible: de aquí se lee el centro aunque la mesa se esté moviendo
        self._anchor = canvas.create_oval(
            cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r, fill="", outline="", tags=tags,
        )

        self._arcs: List[int] = []
        self._labels: List[int] = []
        self._winner_index = None
        font_num = visuals._font(9)
        for idx, num in enumerate(sequence):
            is_win = has_winner and num == winning_number
            if is_win:
                self._winner_index = idx
            self._arcs.append(canvas.create_arc(
                cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                start=-idx * self._step, extent=-self._step, style=tk.PIESLICE,
                fill="#F3B958" if is_win else _hex(COLOR_HEX_MAP[active_map[num].color]),
                outline="#F3B958" if is_win else "#10141A", width=3 if is_win else 1, tags=tags,
            ))
        for idx, num in enumerate(sequence):
            self._labels.append(canvas.create_text(
                cx, cy, text=str(num), fill="#0F1216" if idx == self._winner_index else "#FAF8F0",
                font=font_num, tags=tags,
            ))
        self._ball = visuals.create_ball(cx, cy, tags) if self._winner_index is not None else ()

        visuals._disc(cx, cy, inner_r, "#1D523D", "#F3B958", 3, tags)
        visuals._disc(cx, cy, hub_r, "#141A22", "#F3B958", 3, tags)
        canvas.create_text(
            cx, cy, text=str(winning_number) if has_winner else "SPIN", fill="#F3B958",
            font=visuals._font(18 if has_winner else 11), tags=tags,
        )

        half = size / 2
        if streak_state == StreakState.FURY:
            frames = ((2, "#E0402E", 6, 18), (8, "#D97A1F", 5, 18), (14, "#A98A4F", 3, 18))
        elif streak_state == StreakState.LUCKY:
            frames = ((3, "#F3B958", 5, 18), (10, "#B9A974", 3, 14))
        else:
            frames = ()
        for inset, color, stroke, radius in frames:
            visuals._rrect_fast(
                cx - half + inset, cy - half + inset, cx + half - inset - 1, cy + half - inset - 1,
                radius, "", outline=color, width=stroke, tags=tags,
            )

        self._place(rotation_deg, cx, cy, 1.0)

    def center(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.canvas.coords(self._anchor)
        return (x1 + x2) / 2, (y1 + y2) / 2

    def move_center_to(self, x: float, y: float) -> None:
        """Mueve toda la ruleta (temblor del Modo Furia)."""
        cx, cy = self.center()
        self.canvas.move(self.TAG, x - cx, y - cy)

    def update(self, rotation_deg: float) -> None:
        cx, cy = self.center()
        self._place(rotation_deg, cx, cy, self.visuals.scale)

    def _place(self, rotation_deg: float, cx: float, cy: float, k: float) -> None:
        canvas = self.canvas
        rotation = rotation_deg % 360.0
        step = self._step
        text_r = self._text_r * k
        # Tk mide los ángulos en sentido antihorario; la ruleta gira en sentido horario
        label_angle = -rotation % 360.0
        for idx, (arc, label) in enumerate(zip(self._arcs, self._labels)):
            start = rotation + idx * step
            canvas.itemconfigure(arc, start=-start)
            rad = math.radians(start + step / 2.0)
            canvas.coords(label, cx + math.cos(rad) * text_r, cy + math.sin(rad) * text_r)
            canvas.itemconfigure(label, angle=label_angle)
        if self._ball:
            rad = math.radians(rotation + (self._winner_index + 0.5) * step)
            bx = cx + math.cos(rad) * self._ball_r * k
            by = cy + math.sin(rad) * self._ball_r * k
            for item in self._ball:
                canvas.coords(item, bx, by)
