"""
Fase 6.1 y 6.2 (Edición Balatro 60 FPS - Alta Legibilidad y Cero Emojis):
Generador visual con Pillow y tqdm:
1. Cartas de Comodines, Tarots y Tienda de alta legibilidad: textos grandes y nítidos,
   insignias de efecto claras (+50 FICHAS, X2 MULTI, etc.) y cero emojis rotos.
2. Pre-caché de frames de rotación de la ruleta para animaciones fluidas a 60 FPS
   sin reconstruir la interfaz en cada fotograma.
3. Fichas de casino dinámicas sobre cada casilla apostada del tapete.
"""

import math
import random
import sys
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from tqdm import tqdm
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


class _ScaledDraw:
    """
    Envoltorio de ImageDraw que multiplica coordenadas, radios y grosores por `k`.
    Permite dibujar en "unidades de diseño" y obtener la imagen nítida a cualquier
    escala de pantalla (sin estirar un bitmap pequeño).
    """

    def __init__(self, draw: ImageDraw.ImageDraw, k: float) -> None:
        self._d = draw
        self._k = k

    def _xy(self, xy):
        return [v * self._k for v in xy]

    def _w(self, width: float) -> int:
        return max(1, round(width * self._k)) if width else 0

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1):
        self._d.rounded_rectangle(self._xy(xy), radius=radius * self._k, fill=fill, outline=outline,
                                  width=self._w(width))

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self._d.ellipse(self._xy(xy), fill=fill, outline=outline, width=self._w(width))

    def line(self, xy, fill=None, width=1):
        self._d.line(self._xy(xy), fill=fill, width=self._w(width))

    def polygon(self, xy, fill=None, outline=None, width=1):
        points = [(x * self._k, y * self._k) for x, y in xy]
        self._d.polygon(points, fill=fill, outline=outline, width=self._w(width))

    def pieslice(self, xy, start, end, fill=None, outline=None, width=1):
        self._d.pieslice(self._xy(xy), start, end, fill=fill, outline=outline, width=self._w(width))

    def arc(self, xy, start, end, fill=None, width=1):
        self._d.arc(self._xy(xy), start, end, fill=fill, width=self._w(width))

    def text(self, xy, text, fill=None, font=None, anchor=None):
        self._d.text((xy[0] * self._k, xy[1] * self._k), text, fill=fill, font=font, anchor=anchor)

    def textbbox(self, xy, text, font=None):
        box = self._d.textbbox((xy[0] * self._k, xy[1] * self._k), text, font=font)
        return tuple(v / self._k for v in box)


def _scaled_canvas(width: float, height: float, scale: float) -> Tuple[Image.Image, _ScaledDraw]:
    img = Image.new("RGBA", (max(1, round(width * scale)), max(1, round(height * scale))), (0, 0, 0, 0))
    return img, _ScaledDraw(ImageDraw.Draw(img), scale)


def calculate_screen_shake_offset(
    streak_state: StreakState,
    intensity: int = 8,
    rng: Optional[random.Random] = None,
) -> Tuple[int, int]:
    """
    Calcula el desplazamiento (dx, dy) de temblor de pantalla (Screen Shake).
    Solo aplica desplazamiento no nulo cuando el Modo Furia está activo.
    """
    if streak_state != StreakState.FURY or intensity <= 0:
        return (0, 0)
    chooser = rng if rng is not None else random.Random()
    dx = chooser.randint(-intensity, intensity)
    dy = chooser.randint(-intensity, intensity)
    return (dx, dy)


class VisualAssetGenerator:
    """
    Motor gráfico con Pillow para renderizar toda la estética de Balatro:
    cartas legibles, botones píldora, fichas sobre el tapete y frames de ruleta a 60 FPS.
    """

    def __init__(self, wheel_size: int = 260) -> None:
        self.wheel_size: int = wheel_size
        self.chip_icons: Dict[int, Image.Image] = {}
        self.streak_overlays: Dict[StreakState, Image.Image] = {}
        self._bet_token_cache: Dict[Tuple[int, Optional[SpecialChipType], int, float], Image.Image] = {}
        self._wheel_layer_cache: Dict[Tuple, Image.Image] = {}

    def preload_assets_with_progress(self, show_progress: bool = True) -> None:
        """Pre-genera los iconos de fichas y overlays de racha mostrando barra `tqdm`."""
        tasks = [
            ("chip_5", 5, (254, 95, 85)),
            ("chip_10", 10, (0, 157, 255)),
            ("chip_25", 25, (43, 182, 115)),
            ("chip_50", 50, (243, 185, 88)),
            ("chip_100", 100, (155, 89, 182)),
            ("overlay_fury", StreakState.FURY, None),
            ("overlay_lucky", StreakState.LUCKY, None),
        ]

        iterator = tqdm(
            tasks,
            desc="Generando graficos Balatro con Pillow (Fase 6)",
            disable=(not show_progress or sys.stderr is None),
            leave=False,
        )
        for kind, key, color in iterator:
            if kind.startswith("chip_") and isinstance(key, int) and color is not None:
                self.chip_icons[key] = self.render_chip_icon(key, color, size=50)
            elif kind == "overlay_fury":
                self.streak_overlays[StreakState.FURY] = self.render_fury_overlay(
                    self.wheel_size, self.wheel_size
                )
            elif kind == "overlay_lucky":
                self.streak_overlays[StreakState.LUCKY] = self.render_lucky_overlay(
                    self.wheel_size, self.wheel_size
                )

    def set_wheel_size(self, wheel_size: int) -> None:
        """Cambia el tamaño de la ruleta (escala de pantalla) y regenera sus capas."""
        if wheel_size == self.wheel_size:
            return
        self.wheel_size = wheel_size
        self._wheel_layer_cache.clear()
        self.streak_overlays[StreakState.FURY] = self.render_fury_overlay(wheel_size, wheel_size)
        self.streak_overlays[StreakState.LUCKY] = self.render_lucky_overlay(wheel_size, wheel_size)

    @staticmethod
    def _get_font(size: float, bold: bool = True) -> ImageFont.ImageFont:
        size = max(1, round(size))
        font_names = ["segoeuib.ttf", "arialbd.ttf", "consolab.ttf", "segoeui.ttf", "arial.ttf"]
        if not bold:
            font_names = ["segoeui.ttf", "arial.ttf", "consola.ttf"] + font_names
        for name in font_names:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    @classmethod
    def render_balatro_button(
        cls,
        width: int,
        height: int,
        text: str,
        bg_rgb: Tuple[int, int, int],
        fg_rgb: Tuple[int, int, int] = (255, 255, 255),
        hovered: bool = False,
        font_size: int = 13,
        radius: int = 12,
        scale: float = 1.0,
    ) -> Image.Image:
        """Renderiza un botón píldora moderno estilo Balatro sin emojis ni biseles antiguos."""
        img, draw = _scaled_canvas(width, height + 6, scale)

        y_off = 0 if hovered else 2
        shadow_y = 5

        draw.rounded_rectangle(
            (2, shadow_y, width - 2, height + shadow_y - 1),
            radius=radius,
            fill=(10, 14, 18, 230),
        )

        fill_col = (
            min(255, bg_rgb[0] + 24),
            min(255, bg_rgb[1] + 24),
            min(255, bg_rgb[2] + 24),
            255,
        ) if hovered else bg_rgb + (255,)

        border_col = (255, 235, 130, 255) if hovered else (20, 26, 32, 255)

        draw.rounded_rectangle(
            (2, y_off, width - 2, height + y_off - 1),
            radius=radius,
            fill=fill_col,
            outline=border_col,
            width=3,
        )

        font = cls._get_font(font_size * scale, bold=True)
        if text:
            tx = width // 2
            ty = y_off + height // 2
            draw.text((tx, ty), text, fill=fg_rgb + (255,), font=font, anchor="mm")
        return img

    @classmethod
    def render_chip_icon(
        cls,
        denomination: int,
        base_rgb: Tuple[int, int, int],
        size: int = 50,
        selected: bool = False,
        scale: float = 1.0,
    ) -> Image.Image:
        """Renderiza una ficha de póker/casino con relieve y muescas."""
        img, draw = _scaled_canvas(size + 4, size + 6, scale)

        y_off = 0 if selected else 3
        draw.ellipse((4, 6, size, size + 4), fill=(8, 12, 16, 220))

        outline_col = (255, 235, 110, 255) if selected else (245, 245, 240, 255)
        draw.ellipse(
            (2, y_off, size - 2, size + y_off - 4),
            fill=base_rgb + (255,),
            outline=outline_col,
            width=3,
        )

        cx, cy = size / 2.0, (size - 4) / 2.0 + y_off
        r_outer = (size - 8) / 2.0
        for a_deg in range(0, 360, 45):
            rad = math.radians(a_deg)
            x1 = cx + math.cos(rad) * (r_outer - 3)
            y1 = cy + math.sin(rad) * (r_outer - 3)
            x2 = cx + math.cos(rad) * r_outer
            y2 = cy + math.sin(rad) * r_outer
            draw.line((x1, y1, x2, y2), fill=(255, 255, 255, 255), width=3)

        draw.ellipse(
            (10, y_off + 8, size - 10, size + y_off - 12),
            fill=(24, 30, 38, 255),
            outline=(243, 185, 88, 255),
            width=2,
        )

        font = cls._get_font((12 if (0 < denomination < 100) else 10) * scale, bold=True)
        text = "ALL" if denomination < 0 else f"${denomination}"
        ts_col = (243, 185, 88, 255) if denomination < 0 else (255, 255, 255, 255)
        draw.text((cx, cy), text, fill=ts_col, font=font, anchor="mm")
        return img

    def get_bet_chip_token(
        self,
        amount: int,
        special_chip: Optional[SpecialChipType] = None,
        size: int = 32,
        scale: float = 1.0,
    ) -> Image.Image:
        """
        Devuelve la imagen de la ficha apostada que aparece DIRECTAMENTE sobre la casilla
        del tapete donde el jugador colocó su dinero.
        """
        key = (amount, special_chip, size, scale)
        if key in self._bet_token_cache:
            return self._bet_token_cache[key]

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

        img, draw = _scaled_canvas(size + 4, size + 4, scale)

        draw.ellipse((3, 4, size + 1, size + 2), fill=(5, 8, 12, 220))

        ring_col = (255, 255, 255, 255)
        if special_chip == SpecialChipType.TRIPLE_PAYOUT:
            ring_col = (255, 215, 0, 255)
        elif special_chip == SpecialChipType.ZERO_SHIELD:
            ring_col = (80, 255, 160, 255)
        elif special_chip == SpecialChipType.MAGNET_CHIP:
            ring_col = (180, 120, 255, 255)

        draw.ellipse((1, 1, size - 1, size - 1), fill=rgb + (255,), outline=ring_col, width=2)

        cx, cy = size / 2.0, size / 2.0
        r_m = (size - 4) / 2.0
        for a_deg in range(0, 360, 60):
            rad = math.radians(a_deg)
            draw.line(
                (
                    cx + math.cos(rad) * (r_m - 2.5),
                    cy + math.sin(rad) * (r_m - 2.5),
                    cx + math.cos(rad) * r_m,
                    cy + math.sin(rad) * r_m,
                ),
                fill=(255, 255, 255, 255),
                width=2,
            )

        draw.ellipse(
            (5, 5, size - 5, size - 5),
            fill=(20, 25, 32, 255),
            outline=(243, 185, 88, 255),
            width=1,
        )

        label = f"${amount}" if amount < 100 else f"{amount}"
        font = self._get_font((9 if len(label) <= 3 else 8) * scale, bold=True)
        draw.text((cx, cy), label, fill=(255, 255, 255, 255), font=font, anchor="mm")

        self._bet_token_cache[key] = img
        return img

    @classmethod
    def render_balatro_card(
        cls,
        title: str,
        subtitle: str,
        badge_text: str,
        accent_rgb: Tuple[int, int, int],
        symbol_key: str,
        price: Optional[int] = None,
        width: int = 112,
        height: int = 122,
        highlighted: bool = False,
        scale: float = 1.0,
    ) -> Image.Image:
        """
        Renderiza una carta estilo Balatro con TEXTO MUY GRANDE Y LEGIBLE:
        - Cinta superior sólida con el tipo de carta y precio.
        - Nombre de la carta en grande arriba.
        - Caja central oscura con el BONO GIGANTE (+50 FICHAS, X2 MULTI, etc.).
        - Condición clara en grande en la parte inferior.
        """
        img, draw = _scaled_canvas(width, height, scale)

        # Sombra dura
        draw.rounded_rectangle(
            (4, 5, width - 1, height - 1),
            radius=11,
            fill=(8, 10, 14, 235),
        )

        border_col = (243, 185, 88, 255) if highlighted else (20, 26, 34, 255)
        draw.rounded_rectangle(
            (1, 1, width - 5, height - 5),
            radius=11,
            fill=(248, 244, 232, 255),
            outline=border_col,
            width=3,
        )

        # Cinta superior con categoría
        draw.rounded_rectangle(
            (5, 5, width - 9, 26),
            radius=6,
            fill=accent_rgb + (255,),
        )
        font_badge = cls._get_font(11 * scale, bold=True)
        b_box = draw.textbbox((0, 0), badge_text, font=font_badge)
        bw = b_box[2] - b_box[0]
        tx_badge = (width - 4 - bw) / 2 if price is None else 10
        draw.text((tx_badge, 7), badge_text, fill=(255, 255, 255, 255), font=font_badge)

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

        # 1. Nombre de la carta en letra grande oscura sobre el fondo crema
        font_name = cls._get_font(11 * scale, bold=True)
        n_box = draw.textbbox((0, 0), short_name, font=font_name)
        nw = n_box[2] - n_box[0]
        draw.text(((width - 4 - nw) / 2, 30), short_name, fill=(16, 20, 26, 255), font=font_name)

        # 2. Bloque central oscuro con el EFECTO EN LETRA GRANDE Y DORADA
        mid_top = 48
        mid_bot = height - 28
        draw.rounded_rectangle(
            (6, mid_top, width - 10, mid_bot),
            radius=7,
            fill=(20, 27, 36, 255),
            outline=accent_rgb + (255,),
            width=2,
        )

        font_big = cls._get_font((13 if len(big_formula) <= 10 else 11) * scale, bold=True)
        f_box = draw.textbbox((0, 0), big_formula, font=font_big)
        fw = f_box[2] - f_box[0]
        fh = f_box[3] - f_box[1]
        f_y = mid_top + ((mid_bot - mid_top) - fh) / 2 - 2
        draw.text(((width - 4 - fw) / 2, f_y), big_formula, fill=(243, 185, 88, 255), font=font_big)

        # 3. Condición en la base en letra clara y nítida
        font_cond = cls._get_font(10 * scale, bold=True)
        c_box = draw.textbbox((0, 0), condition_txt, font=font_cond)
        cw = c_box[2] - c_box[0]
        draw.text(((width - 4 - cw) / 2, height - 23), condition_txt, fill=accent_rgb + (255,), font=font_cond)

        # Etiqueta de precio amarilla en la esquina superior derecha
        if price is not None:
            price_str = f"${price}"
            draw.rounded_rectangle(
                (width - 50, 3, width - 5, 25),
                radius=6,
                fill=(243, 185, 88, 255),
                outline=(18, 22, 28, 255),
                width=2,
            )
            p_box = draw.textbbox((0, 0), price_str, font=font_badge)
            pw = p_box[2] - p_box[0]
            draw.text((width - 27 - pw / 2, 6), price_str, fill=(18, 22, 28, 255), font=font_badge)

        return img

    # -------------------------------------------------------------------------
    # Ruletín: la mascota guía del casino
    # -------------------------------------------------------------------------
    RULETIN_SIZE = (170, 190)

    @classmethod
    def load_ruletin_sprite(cls, image_path: Optional["Path"], scale: float = 1.0) -> Image.Image:
        """
        Usa la imagen del jugador (assets/ruletin.png) si existe; si no, dibuja un Ruletín provisorio.
        A una imagen sin transparencia se le quita el fondo liso desde las esquinas.
        """
        box_w, box_h = (round(v * scale) for v in cls.RULETIN_SIZE)
        if image_path is not None and image_path.exists():
            try:
                img = Image.open(image_path).convert("RGBA")
                if img.getextrema()[3][0] == 255:  # sin píxeles transparentes: quitamos el fondo
                    w, h = img.size
                    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                        if img.getpixel(corner)[3] != 0:
                            ImageDraw.floodfill(img, corner, (0, 0, 0, 0), thresh=48)
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)
                img.thumbnail((box_w, box_h), Image.LANCZOS)
                canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
                canvas.alpha_composite(img, ((box_w - img.width) // 2, box_h - img.height))
                return canvas
            except Exception:
                pass
        return cls.render_ruletin_placeholder(scale)

    @classmethod
    def render_ruletin_placeholder(cls, scale: float = 1.0) -> Image.Image:
        """Ruletín provisorio: cabeza-ruleta con gorro de bufón y gran sonrisa."""
        w, h = cls.RULETIN_SIZE
        img, d = _scaled_canvas(w, h, scale)
        cx, cy, r = 85, 112, 62
        # Sombra
        d.ellipse((cx - 50, h - 16, cx + 50, h - 4), fill=(5, 8, 12, 150))
        # Gorro de bufón (dos puntas con cascabeles)
        d.polygon([(cx - 52, cy - 38), (cx - 70, cy - 98), (cx - 6, cy - 52)], fill=(238, 66, 56, 255),
                  outline=(16, 20, 26, 255), width=3)
        d.polygon([(cx + 52, cy - 38), (cx + 70, cy - 98), (cx + 6, cy - 52)], fill=(0, 157, 255, 255),
                  outline=(16, 20, 26, 255), width=3)
        d.ellipse((cx - 80, cy - 110, cx - 60, cy - 90), fill=(243, 185, 88, 255), outline=(16, 20, 26, 255), width=3)
        d.ellipse((cx + 60, cy - 110, cx + 80, cy - 90), fill=(243, 185, 88, 255), outline=(16, 20, 26, 255), width=3)
        # Aro de ruleta (casillas rojas y negras)
        d.ellipse((cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4), fill=(16, 20, 26, 255))
        for i in range(18):
            col = (238, 66, 56, 255) if i % 2 == 0 else (36, 44, 54, 255)
            if i == 13:
                col = (43, 182, 115, 255)
            d.pieslice((cx - r, cy - r, cx + r, cy + r), i * 20, i * 20 + 20, fill=col)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(243, 185, 88, 255), width=4)
        # Cara
        fr = 44
        d.ellipse((cx - fr, cy - fr, cx + fr, cy + fr), fill=(248, 238, 214, 255), outline=(243, 185, 88, 255), width=3)
        for ex in (-16, 16):
            d.ellipse((cx + ex - 9, cy - 22, cx + ex + 9, cy - 2), fill=(255, 255, 255, 255), outline=(16, 20, 26, 255), width=2)
            d.ellipse((cx + ex - 4, cy - 15, cx + ex + 4, cy - 6), fill=(16, 20, 26, 255))
        d.ellipse((cx - 30, cy + 2, cx - 20, cy + 10), fill=(254, 140, 130, 200))
        d.ellipse((cx + 20, cy + 2, cx + 30, cy + 10), fill=(254, 140, 130, 200))
        d.pieslice((cx - 20, cy - 4, cx + 20, cy + 28), 0, 180, fill=(140, 30, 40, 255), outline=(16, 20, 26, 255), width=2)
        # Bola blanca de la ruleta como nariz
        d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(255, 255, 255, 255), outline=(0, 157, 255, 255), width=2)
        return img

    @staticmethod
    def render_fury_overlay(width: int, height: int) -> Image.Image:
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        layers = [
            (2, (254, 65, 45, 225), 6),
            (8, (255, 130, 25, 175), 5),
            (14, (243, 185, 88, 125), 3),
        ]
        for inset, rgba, stroke in layers:
            draw.rounded_rectangle(
                (inset, inset, width - inset - 1, height - inset - 1),
                radius=18,
                outline=rgba,
                width=stroke,
            )
        return img.filter(ImageFilter.GaussianBlur(radius=1.0))

    @staticmethod
    def render_lucky_overlay(width: int, height: int) -> Image.Image:
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((3, 3, width - 4, height - 4), radius=18, outline=(243, 185, 88, 235), width=5)
        draw.rounded_rectangle((10, 10, width - 11, height - 11), radius=14, outline=(255, 240, 150, 160), width=3)
        return img

    # -------------------------------------------------------------------------
    # Ruleta en capas: base estática + disco giratorio supermuestreado + tapa estática.
    # El disco se dibuja UNA vez a 2x y en cada frame solo se rota y reduce, así el
    # giro es continuo (sin saltos de 5 grados) y los bordes salen antialiasados.
    # -------------------------------------------------------------------------
    WHEEL_SUPERSAMPLE = 2

    def wheel_geometry(self) -> Tuple[float, float, float]:
        """Devuelve (radio_exterior, radio_interior, radio_pista_bola) en píxeles a 1x."""
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

    def _wheel_base_layer(self) -> Image.Image:
        key = ("base",)
        if key in self._wheel_layer_cache:
            return self._wheel_layer_cache[key]
        s = self.WHEEL_SUPERSAMPLE
        size = self.wheel_size * s
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size // 2
        outer_r = (self.wheel_size // 2 - 12) * s
        draw.ellipse(
            (cx - outer_r - 6 * s, cy - outer_r - 4 * s, cx + outer_r + 10 * s, cy + outer_r + 12 * s),
            fill=(8, 12, 16, 220),
        )
        draw.ellipse(
            (cx - outer_r - 8 * s, cy - outer_r - 8 * s, cx + outer_r + 8 * s, cy + outer_r + 8 * s),
            fill=(20, 26, 34, 255),
            outline=(243, 185, 88, 255),
            width=4 * s,
        )
        img = img.reduce(s)
        self._wheel_layer_cache[key] = img
        return img

    def _wheel_disc_layer(self, wheel: RouletteWheel, winning_number: Optional[int]) -> Image.Image:
        """Disco de bolsillos sin rotar (dibujado a 2x). Se cachea por colores de bolsillos y ganador."""
        active_map, sequence = self._wheel_sequence(wheel)
        key = (
            "disc",
            tuple((n, active_map[n].color) for n in sequence),
            winning_number,
        )
        if key in self._wheel_layer_cache:
            return self._wheel_layer_cache[key]

        s = self.WHEEL_SUPERSAMPLE
        size = self.wheel_size * s
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size / 2.0
        outer_1x, inner_1x, ball_1x = self.wheel_geometry()
        outer_r, inner_r = outer_1x * s, inner_1x * s
        font_num = self._get_font(9 * s, bold=True)

        n_sectors = len(sequence)
        if n_sectors > 0:
            step_angle = 360.0 / n_sectors
            for idx, num in enumerate(sequence):
                pocket = active_map[num]
                start_a = idx * step_angle
                is_win = winning_number is not None and num == winning_number
                fill_rgb = (243, 185, 88) if is_win else COLOR_HEX_MAP[pocket.color]
                draw.pieslice(
                    (cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r),
                    start=start_a,
                    end=start_a + step_angle,
                    fill=fill_rgb + (255,),
                    outline=(243, 185, 88, 255) if is_win else (16, 20, 26, 255),
                    width=(3 if is_win else 1) * s,
                )

                mid_rad = math.radians(start_a + step_angle / 2.0)
                text_r = (outer_r + inner_r) / 2.0 + 9 * s
                text_col = (15, 18, 22, 255) if is_win else (250, 248, 240, 255)
                draw.text(
                    (cx + math.cos(mid_rad) * text_r, cy + math.sin(mid_rad) * text_r),
                    str(num), fill=text_col, font=font_num, anchor="mm",
                )

                if is_win:
                    ball_r = ball_1x * s
                    bx = cx + math.cos(mid_rad) * ball_r
                    by = cy + math.sin(mid_rad) * ball_r
                    draw.ellipse(
                        (bx - 7 * s, by - 7 * s, bx + 7 * s, by + 7 * s),
                        fill=(255, 255, 255, 255),
                        outline=(0, 157, 255, 255),
                        width=3 * s,
                    )

        # Se guarda ya reducido a 1x (antialiasado); rotar a 1x es ~4 veces más barato
        img = img.reduce(s)
        if len(self._wheel_layer_cache) > 64:
            self._wheel_layer_cache.clear()
        self._wheel_layer_cache[key] = img
        return img

    def _wheel_top_layer(self, center_label: Optional[str], streak_state: StreakState) -> Image.Image:
        """Centro verde, buje y texto central + overlay de racha (no giran)."""
        key = ("top", center_label, streak_state)
        if key in self._wheel_layer_cache:
            return self._wheel_layer_cache[key]
        s = self.WHEEL_SUPERSAMPLE
        size = self.wheel_size * s
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size / 2.0
        outer_1x, inner_1x, _ = self.wheel_geometry()
        inner_r = inner_1x * s
        hub_r = int(outer_1x * 0.25) * s
        draw.ellipse(
            (cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r),
            fill=(29, 82, 61, 255),
            outline=(243, 185, 88, 255),
            width=3 * s,
        )
        draw.ellipse(
            (cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r),
            fill=(20, 26, 34, 255),
            outline=(243, 185, 88, 255),
            width=3 * s,
        )
        if center_label is not None:
            f_center = self._get_font(18 * s, bold=True)
            draw.text((cx, cy), center_label, fill=(243, 185, 88, 255), font=f_center, anchor="mm")
        else:
            f_center = self._get_font(11 * s, bold=True)
            draw.text((cx, cy), "SPIN", fill=(243, 185, 88, 255), font=f_center, anchor="mm")
        img = img.reduce(s)

        size_1x = self.wheel_size
        if streak_state in self.streak_overlays:
            img = Image.alpha_composite(img, self.streak_overlays[streak_state])
        elif streak_state == StreakState.FURY:
            img = Image.alpha_composite(img, self.render_fury_overlay(size_1x, size_1x))
        elif streak_state == StreakState.LUCKY:
            img = Image.alpha_composite(img, self.render_lucky_overlay(size_1x, size_1x))

        self._wheel_layer_cache[key] = img
        return img

    def render_roulette_wheel_image(
        self,
        wheel: RouletteWheel,
        rotation_deg: float = 0.0,
        winning_number: Optional[int] = None,
        streak_state: StreakState = StreakState.NEUTRAL,
    ) -> Image.Image:
        """
        Compone la ruleta a cualquier ángulo (continuo, sin cuantizar) a partir de capas
        cacheadas: solo el disco de bolsillos se rota en cada frame.
        """
        active_map, _ = self._wheel_sequence(wheel)
        has_winner = winning_number is not None and winning_number in active_map
        disc = self._wheel_disc_layer(wheel, winning_number if has_winner else None)

        angle = rotation_deg % 360.0
        if abs(angle) > 1e-3:
            center = (self.wheel_size / 2.0, self.wheel_size / 2.0)
            disc = disc.rotate(-angle, resample=Image.BICUBIC, center=center)

        img = self._wheel_base_layer().copy()
        img.alpha_composite(disc)
        img.alpha_composite(
            self._wheel_top_layer(str(winning_number) if has_winner else None, streak_state)
        )
        return img

    @classmethod
    def render_balatro_logo(
        cls,
        title: str = "THE ROULETE",
        width: int = 760,
        height: int = 150,
        scale: float = 1.0,
    ) -> Image.Image:
        """Renderiza el logo 'THE ROULETE' en cartas arqueadas sin caracteres especiales rotos."""
        img, draw = _scaled_canvas(width, height, scale)

        draw.rounded_rectangle(
            (22, 24, width - 18, height - 14),
            radius=28,
            fill=(8, 12, 16, 220),
        )
        draw.rounded_rectangle(
            (18, 18, width - 22, height - 20),
            radius=28,
            fill=(22, 29, 38, 245),
            outline=(243, 185, 88, 255),
            width=4,
        )

        chars = list(title)
        card_w = 52
        card_h = 80
        spacing = 58
        total_w = len(chars) * spacing
        start_x = (width - total_w) // 2 + 4
        base_y = (height - card_h) // 2

        font_letter = cls._get_font(38 * scale, bold=True)
        palette = [
            (254, 95, 85),
            (32, 40, 50),
            (0, 157, 255),
            (230, 155, 30),
        ]

        for idx, ch in enumerate(chars):
            if ch == " ":
                continue
            cx = start_x + idx * spacing
            arch_offset = int(math.sin((idx / max(1, len(chars) - 1)) * math.pi) * -12) + 4
            cy = base_y + arch_offset

            draw.rounded_rectangle(
                (cx + 4, cy + 6, cx + card_w + 4, cy + card_h + 6),
                radius=9,
                fill=(8, 10, 14, 220),
            )
            col = palette[idx % len(palette)]
            draw.rounded_rectangle(
                (cx, cy, cx + card_w, cy + card_h),
                radius=9,
                fill=(246, 240, 225, 255),
                outline=(24, 30, 38, 255),
                width=3,
            )
            draw.rounded_rectangle(
                (cx + 4, cy + 4, cx + card_w - 4, cy + card_h - 4),
                radius=6,
                outline=col + (210,),
                width=2,
            )

            # Punto decorativo de esquina en vez de glifos que puedan fallar
            draw.ellipse((cx + 7, cy + 7, cx + 13, cy + 13), fill=col + (255,))

            bbox = draw.textbbox((0, 0), ch, font=font_letter)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
            draw.text(
                (cx + (card_w - lw) / 2, cy + (card_h - lh) / 2 - 2),
                ch,
                fill=col + (255,),
                font=font_letter,
            )

        return img
