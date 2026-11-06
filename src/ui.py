"""
Interfaz de The Roulete (customtkinter):
1. Mesa, cartas y ruleta dibujadas en un CTkCanvas con formas suavizadas.
2. La interfaz se escala para llenar la pantalla y se ve nítida con cualquier zoom de Windows.
3. Pantallas: intro, menú, colección, opciones, partida y tienda, con ventanas propias,
   animaciones por tiempo real y la mascota Ruletín.
"""

import copy
import math
import random
from pathlib import Path
import sys
import time
import tkinter as tk
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from src.audio import SoundManager
from src.icono import apply_window_icon
from src.mezclador import mixer
from src.music_jukebox import MusicDirector
from src.board import Bet, BetTarget, BetType, SpecialChipType
from src.bosses import BOSS_CATALOG
from src.economy import GamePhase, ShopItemKind, SPECIAL_CHIP_SHOP_INFO
from src.game_engine import Difficulty, RoguelikeRouletteEngine
from src.hands import HAND_CATALOG, HandType, ScoreBreakdown
from src.savegame import delete_save, load_game, load_settings, read_save_summary, save_game, save_settings
from src.modifiers import JOKER_CATALOG, TAROT_CATALOG
from src.streaks import StreakState
from src.visuals import (
    BALATRO_BLUE,
    BALATRO_GOLD,
    BALATRO_ORANGE,
    BALATRO_PURPLE,
    BALATRO_RED,
    BALATRO_TEAL,
    VisualAssetGenerator,
    WheelView,
)
from src.wheel import Color, Parity, RouletteWheel


FRAME_SECONDS = 1.0 / 60.0  # las animaciones avanzan por tiempo real, no por número de pasos


def _frame_delay_ms(frame_started: float) -> int:
    """Espera restante hasta el próximo frame descontando lo que tardó el dibujado."""
    remaining = FRAME_SECONDS - (time.perf_counter() - frame_started)
    return max(1, int(remaining * 1000))


def _enable_precise_timers() -> None:
    """En Windows, after() se redondea a ~15.6 ms; pedimos 1 ms para animar de verdad a 60 FPS."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_out_quint(t: float) -> float:
    return 1.0 - (1.0 - t) ** 5


def ease_out_back(t: float, overshoot: float = 1.70158) -> float:
    c3 = overshoot + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + overshoot * (t - 1.0) ** 2


class GameCanvas(ctk.CTkCanvas):
    """Canvas suavizado que recuerda qué ítems tienen grosor (para escalarlos sin revisarlos todos)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.widths: Dict[int, float] = {}

    def _create(self, itemType, args, kw):
        item = super()._create(itemType, args, kw)
        width = kw.get("width")
        if width:
            self.widths[item] = (float(width), itemType == "text")
        return item


class RoguelikeRouletteApp:
    """Interfaz estilo Balatro con Menú de Colección visual y Pantalla Completa para 'The Roulete'."""

    WIDTH = 1268
    HEIGHT = 680
    WHEEL_DESIGN_SIZE = 228

    def __init__(self, root: ctk.CTk, seed: Optional[int] = None) -> None:
        self.root = root
        self.root.title("The Roulete")
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.root.configure(fg_color="#0F1419")
        self.root.resizable(True, True)
        apply_window_icon(self.root)

        self.seed = seed
        self.difficulty: Difficulty = Difficulty.NORMAL
        self.engine = RoguelikeRouletteEngine(seed=seed, difficulty=self.difficulty)
        self.visuals = VisualAssetGenerator(wheel_size=self.WHEEL_DESIGN_SIZE)

        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).resolve().parent
        else:
            base_dir = Path(__file__).resolve().parent.parent
        self.base_dir = base_dir
        self._save_path = base_dir / "save" / "partida.json"
        self._settings_path = base_dir / "save" / "ajustes.json"
        self._in_game_session: bool = False
        self._ruletin_image_path = next(
            (base_dir / "assets" / f"ruletin.{ext}" for ext in ("png", "gif")
             if (base_dir / "assets" / f"ruletin.{ext}").exists()),
            None,
        )
        self.audio = SoundManager(
            audio_dir=base_dir / "assets",
            cache_dir=base_dir / "save" / "musica",
            enabled=True,
        )
        self.audio.generate_sounds()
        self.music_director: Optional[MusicDirector] = None

        # Estados de pantalla y navegación
        self.in_intro_screen: bool = True
        self.intro_spinning: bool = False
        self.intro_landed_zero: bool = False
        self.intro_wheel_angle: float = 0.0
        self.in_main_menu: bool = True
        self.in_collection_menu: bool = False
        self.in_options_menu: bool = False
        self.is_fullscreen: bool = False
        self.offset_x: int = 0
        self.offset_y: int = 0
        # Escala de la interfaz: el diseño es de WIDTH x HEIGHT y se agranda para llenar la pantalla
        self.ui_scale: float = 1.0

        # Ventanas de diálogo propias (dibujadas en el canvas, sin ventanas de Windows)
        self._modal: Optional[Dict] = None
        self._modal_queue: List[Dict] = []

        # Carta seleccionada/inspeccionada actualmente en el Menú de Colección
        first_joker = list(JOKER_CATALOG.values())[0]
        self.collection_selected: Tuple[str, str, str, Tuple[int, int, int], str, Optional[int]] = (
            first_joker.name,
            first_joker.description,
            "JOKER",
            BALATRO_RED,
            first_joker.joker_type.value,
            first_joker.cost,
        )

        # Estado de juego y animaciones
        self.menu_angle: float = 0.0
        self.selected_chip_amount: int = 10
        self.selected_special_chip: Optional[SpecialChipType] = None
        self.wheel_angle: float = 0.0
        self.last_winning_number: Optional[int] = None
        self.is_spinning: bool = False
        self.floating_banner_text: str = ""
        self.floating_banner_color: str = "#F3B958"
        self.last_score: Optional[ScoreBreakdown] = None  # Último FICHAS x MULTI resuelto
        self._chips_text_item: Optional[int] = None
        self._mult_text_item: Optional[int] = None
        self.selected_card: Optional[Tuple[str, int]] = None  # ("joker"|"tarot", índice) para usar/vender
        self.collection_page: int = 0
        self.shop_note: str = ""

        # Ruletín: mascota guía con globo de texto y voz de balbuceo
        self.ruletin_enabled: bool = True
        self._ruletin: Optional[Dict] = None
        self._ruletin_queue: List[Dict] = []
        self._ruletin_said: set = set()
        self._ruletin_photo = None
        self._ruletin_photo_scale: float = 0.0
        self._ruletin_has_sprite: bool = False
        self._ruletin_text_item: Optional[int] = None
        self._ruletin_hint_item: Optional[int] = None
        self._tutorial_pending: bool = True

        # Preferencias guardadas: tutorial visto, Ruletín, dificultad y comentarios ya dichos
        settings = load_settings(self._settings_path)
        self._tutorial_pending = not settings.get("tutorial_done", False)
        self.ruletin_enabled = bool(settings.get("ruletin_enabled", True))
        self.audio.music_playing = bool(settings.get("music_enabled", True))
        self._music_volume = float(settings.get("music_volume", 1.0))
        self._effects_volume = float(settings.get("effects_volume", 1.0))
        mixer.set_music_gain(self._music_volume)
        mixer.effects_gain = self._effects_volume
        self._ruletin_said = set(settings.get("ruletin_said", []))
        try:
            self.difficulty = Difficulty(settings.get("difficulty", Difficulty.NORMAL.value))
        except ValueError:
            self.difficulty = Difficulty.NORMAL
        self.engine.difficulty = self.difficulty
        self._spin_skip_requested: bool = False
        self._spin_skip_allowed: bool = False
        self.shown_bankroll: Optional[int] = None
        self._bankroll_target: Optional[int] = None
        self._banner_items: bool = False
        self._last_popped_banner: str = ""
        self._result_wheel: Optional[RouletteWheel] = None  # rueda tal como estaba al girar (Tarots)

        # Motor de animaciones por tiempo: clave -> token activo (cancelable)
        self._anims: Dict[str, object] = {}
        self._hover_regions: List[Tuple[str, Tuple[int, int, int, int], int]] = []
        self._hover_offsets: Dict[str, float] = {}
        self._hover_faces: Dict[str, Tuple[str, str, str]] = {}  # botón -> (etiqueta, color, color al pasar)
        self._embedded: List = []  # Controles de customtkinter de la pantalla actual
        self._autosave_job: Optional[str] = None
        self._hovered_tag: Optional[str] = None
        self._bankroll_text_item: Optional[int] = None
        self._spin_ball_item: Optional[int] = None
        self._menu_last_time: float = time.perf_counter()

        # Ruleta en pantalla: se gira sin volver a dibujarla
        self._wheel_view: Optional[WheelView] = None
        self._col_preview_title_item: Optional[int] = None
        self._col_preview_cat_item: Optional[int] = None
        self._col_preview_desc_item: Optional[int] = None


        self.canvas = GameCanvas(
            self.root,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg="#152A20",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.visuals.bind_canvas(self.canvas)

        # Atajos de teclado para Pantalla Completa (F11), Intro (Enter/Espacio) y Salir/Volver (Escape)
        self.root.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.root.bind("<Escape>", self._on_escape_pressed)
        self.root.bind("<Return>", self._on_enter_or_space_pressed)
        self.root.bind("<space>", self._on_enter_or_space_pressed)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.root.protocol("WM_DELETE_WINDOW", self._quit_game)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Button-1>", self._on_canvas_click, add="+")
        self.canvas.bind("<Leave>", lambda _e: self._set_hovered(None))

        self.music_director = MusicDirector(self)
        self._redraw()
        self._animate_menu_loop()
        self._schedule_timer_tick()

    # =========================================================================
    # Gestión de Pantalla Completa y Escalado / Centrado Dinámico (Sin Flicker)
    # =========================================================================
    def toggle_fullscreen(self) -> None:
        if self.is_spinning or self.intro_spinning:
            return
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)
        self.audio.play("chip")
        self._update_offsets()
        self._redraw()

    def _on_escape_pressed(self, _event) -> None:
        if self._modal is not None:
            self._close_dialog()
        elif self._ruletin is not None:
            self._ruletin_skip_all()
        elif self.in_intro_screen and not self.intro_spinning:
            self._finish_intro_screen()
        elif self.in_collection_menu:
            self._close_collection_menu()
        elif self.in_options_menu:
            self._close_options_menu()
        elif self.is_fullscreen:
            self.toggle_fullscreen()

    def _on_enter_or_space_pressed(self, _event) -> None:
        if self._modal is not None:
            self._close_dialog()
        elif self._ruletin is not None:
            self._ruletin_advance()
        elif self.in_intro_screen and not self.intro_spinning and not self.intro_landed_zero:
            self._start_intro_spin()

    def _on_canvas_resize(self, _event) -> None:
        # Durante un giro no se re-escala (la ruleta animada usa la imagen actual); al terminar se redibuja
        if self.is_spinning or self.intro_spinning:
            return
        before = (self.ui_scale, self.offset_x, self.offset_y)
        self._update_offsets()
        if (self.ui_scale, self.offset_x, self.offset_y) != before:
            self._redraw()

    def _update_offsets(self) -> None:
        # No llamamos a update_idletasks() durante el redibujado para evitar parpadeos de pantalla en Windows
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        w = cw if cw > 100 else self.WIDTH
        h = ch if ch > 100 else self.HEIGHT
        scale = round(max(0.6, min(w / self.WIDTH, h / self.HEIGHT)), 3)
        if scale != self.ui_scale:
            self._apply_ui_scale(scale)
        self.offset_x = max(0, int((w - self.WIDTH * scale) / 2))
        self.offset_y = max(0, int((h - self.HEIGHT * scale) / 2))

    def _apply_ui_scale(self, scale: float) -> None:
        """Cambia la escala: los dibujos y las fuentes se recalculan nítidos al nuevo tamaño."""
        self.ui_scale = scale
        self.visuals.scale = scale
        self.visuals.set_wheel_size(max(80, round(self.WHEEL_DESIGN_SIZE * scale)))

    def _font(self, size: float) -> Tuple[str, int, str]:
        """Fuente en tamaño de diseño, ajustada a la escala de pantalla."""
        return ("Segoe UI", -max(8, round(size * 4 / 3 * self.ui_scale)), "bold")

    def _to_screen(self, x: float, y: float) -> Tuple[float, float]:
        return x * self.ui_scale + self.offset_x, y * self.ui_scale + self.offset_y

    def _finalize_layer(self, tag: str) -> None:
        """Lleva ítems dibujados en coordenadas de diseño a la pantalla: escala + centrado."""
        k = self.ui_scale
        if k != 1.0:
            self.canvas.scale(tag, 0, 0, k, k)
            widths = self.canvas.widths
            for item in self.canvas.find_withtag(tag):
                entry = widths.pop(item, None)
                if entry is not None:
                    self.canvas.itemconfigure(item, width=entry[0] * k if entry[1] else max(1.0, entry[0] * k))
        if self.offset_x or self.offset_y:
            self.canvas.move(tag, self.offset_x, self.offset_y)

    # =========================================================================
    # Motor de Animaciones por Tiempo Real (tweens cancelables a ~60 FPS)
    # =========================================================================
    def _start_anim(
        self,
        key: str,
        duration: float,
        on_frame: Callable[[float], None],
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Llama on_frame(t) con t en [0, 1] según el tiempo transcurrido. Reemplaza la anim. con la misma clave."""
        token = object()
        self._anims[key] = token
        start = time.perf_counter()

        def step() -> None:
            if self._anims.get(key) is not token:
                return
            frame_started = time.perf_counter()
            t = min(1.0, (time.perf_counter() - start) / duration) if duration > 0 else 1.0
            on_frame(t)
            if t >= 1.0:
                if self._anims.get(key) is token:
                    del self._anims[key]
                if on_done is not None:
                    on_done()
            else:
                self.root.after(_frame_delay_ms(frame_started), step)

        step()

    def _cancel_anim(self, key: str) -> None:
        self._anims.pop(key, None)

    # --- Hover con elevación suave (estilo Balatro) --------------------------
    def _register_hover(self, tag: str, x1: float, y1: float, x2: float, y2: float, lift: int) -> None:
        """Zona de hover en coordenadas de diseño; la detección usa la caja en reposo para no temblar en bordes."""
        self._hover_regions.append((tag, (int(x1), int(y1), int(x2), int(y2)), lift))

    def _hover_hit(self, px: float, py: float) -> Optional[str]:
        for tag, (x1, y1, x2, y2), _lift in reversed(self._hover_regions):
            if x1 <= px <= x2 and y1 <= py <= y2:
                return tag
        return None

    def _on_canvas_motion(self, event) -> None:
        if self._modal is not None or (self._ruletin is not None and self._ruletin["blocking"]):
            return
        px = (self.canvas.canvasx(event.x) - self.offset_x) / self.ui_scale
        py = (self.canvas.canvasy(event.y) - self.offset_y) / self.ui_scale
        self._set_hovered(self._hover_hit(px, py))

    def _set_hovered(self, tag: Optional[str], instant: bool = False) -> None:
        if tag == self._hovered_tag:
            return
        if self._hovered_tag is not None:
            self._animate_lift(self._hovered_tag, 0.0, instant)
            face = self._hover_faces.get(self._hovered_tag)
            if face:
                self.canvas.itemconfigure(face[0], fill=face[1])
        self._hovered_tag = tag
        if tag is not None:
            face = self._hover_faces.get(tag)
            if face:
                self.canvas.itemconfigure(face[0], fill=face[2])
            lift = next((lf for tg, _b, lf in self._hover_regions if tg == tag), 0)
            self._animate_lift(tag, -float(lift), instant)

    def _animate_lift(self, tag: str, target: float, instant: bool = False) -> None:
        start = self._hover_offsets.get(tag, 0.0)
        if abs(start - target) < 0.01:
            return
        movable = f"{tag}&&!shadow"

        def frame(t: float) -> None:
            cur = self._hover_offsets.get(tag, 0.0)
            new = start + (target - start) * ease_out_cubic(t)
            self.canvas.move(movable, 0, (new - cur) * self.ui_scale)
            self._hover_offsets[tag] = new

        if instant:
            frame(1.0)
        else:
            self._start_anim(f"lift:{tag}", 0.12, frame)

    # --- Pequeños efectos reutilizables --------------------------------------
    def _bump_item(self, item, key: str, dy: float = -8.0, duration: float = 0.24) -> None:
        """Pequeño salto de un ítem que vuelve a su lugar con rebote (feedback de selección)."""
        if item is None:
            return
        state = {"off": 0.0}

        def frame(t: float) -> None:
            new = dy * self.ui_scale * (1.0 - ease_out_back(t, 2.2))
            self.canvas.move(item, 0, new - state["off"])
            state["off"] = new

        self._start_anim(key, duration, frame)

    def _pop_in(self, tag: str) -> None:
        """Entrada con rebote: el grupo sube desde un poco más abajo y se pasa apenas."""
        distance = 26.0 * self.ui_scale
        state = {"off": distance}
        self.canvas.move(tag, 0, distance)

        def frame(t: float) -> None:
            new = distance * (1.0 - ease_out_back(t, 2.2))
            self.canvas.move(tag, 0, new - state["off"])
            state["off"] = new

        self._start_anim(f"pop:{tag}", 0.3, frame)

    # =========================================================================
    # Ventanas de Diálogo Propias (reemplazan a las ventanas emergentes de Windows)
    # =========================================================================
    DIALOG_ACCENTS = {"info": "#009DFF", "warning": "#F3B958", "error": "#FE5F55", "success": "#2BB673"}

    def _show_dialog(
        self,
        title: str,
        message: str,
        kind: str = "info",
        on_close: Optional[Callable[[], None]] = None,
        button_text: str = "ACEPTAR",
        buttons: Optional[List[Tuple[str, str, Optional[Callable[[], None]]]]] = None,
    ) -> None:
        """
        Encola una ventana modal dibujada en el canvas con el estilo del juego.
        `buttons` = [(texto, color, acción)] para ventanas con varias opciones (Esc = cancelar).
        """
        self._modal_queue.append(
            {"title": title, "message": message, "kind": kind, "on_close": on_close, "button": button_text,
             "buttons": buttons}
        )
        if self._modal is None:
            self._open_next_dialog()

    def _show_info(self, title: str, message: str) -> None:
        self._show_dialog(title, message, "info")

    def _show_warning(self, title: str, message: str) -> None:
        self._show_dialog(title, message, "warning")

    def _show_error(self, title: str, message: str) -> None:
        self._show_dialog(title, message, "error")

    def _open_next_dialog(self) -> None:
        if not self._modal_queue:
            self._modal = None
            return
        self._modal = self._modal_queue.pop(0)
        self._set_hovered(None)
        self.canvas.config(cursor="")
        self._draw_modal_layer(animate=True)

    def _close_dialog(self, action: Optional[Callable[[], None]] = None) -> None:
        modal = self._modal
        if modal is None:
            return
        self._cancel_anim("pop:modal_box")
        self.canvas.delete("modal_layer")
        self._modal = None
        self.audio.play("chip")
        if modal["on_close"] is not None:
            modal["on_close"]()
        if action is not None:
            action()
        if self._modal is None:
            self._open_next_dialog()

    def _draw_modal_layer(self, animate: bool) -> None:
        """Fondo oscurecido que bloquea los clics + panel centrado con título, mensaje y botón."""
        modal = self._modal
        if modal is None:
            return
        self.canvas.delete("modal_layer")
        k = self.ui_scale
        cw = max(self.canvas.winfo_width(), int(self.WIDTH * k))
        ch = max(self.canvas.winfo_height(), int(self.HEIGHT * k))
        accent = self.DIALOG_ACCENTS.get(modal["kind"], "#F3B958")

        overlay = self.canvas.create_rectangle(
            0, 0, cw, ch, fill="#05080B", stipple="gray50", width=0, tags=("modal_layer",)
        )
        # El overlay se "come" los clics y el hover para que no lleguen a la mesa de abajo
        self.canvas.tag_bind(overlay, "<Button-1>", lambda _e: None)

        box_tags = ("modal_layer", "modal_box")
        cx, _ = self._to_screen(self.WIDTH / 2, 0)
        box_w = 560 * k
        title_font, msg_font = self._font(19), self._font(12)
        title_text = modal["title"].upper()
        title_h = self._text_height(title_text, title_font, box_w - 60 * k)
        msg_h = self._text_height(modal["message"], msg_font, box_w - 70 * k)
        pad = 26 * k
        btn_h = 48 * k
        box_h = pad + title_h + 18 * k + msg_h + 26 * k + btn_h + pad
        _, cy = self._to_screen(0, self.HEIGHT / 2)
        x1, y1 = cx - box_w / 2, cy - box_h / 2
        x2, y2 = cx + box_w / 2, cy + box_h / 2

        rrect = self.visuals._rrect
        rrect(x1 + 6 * k, y1 + 8 * k, x2 + 6 * k, y2 + 8 * k, 20 * k, "#05080B", tags=box_tags, k=1.0)
        rrect(x1, y1, x2, y2, 20 * k, "#1D2630", outline=accent, width=max(2, 4 * k), tags=box_tags, k=1.0)
        rrect(x1 + 14 * k, y1 + 10 * k, x2 - 14 * k, y1 + 16 * k, 3 * k, accent, tags=box_tags, k=1.0)
        self.canvas.create_text(
            cx, y1 + pad + title_h / 2, text=title_text, fill=accent, font=title_font,
            width=box_w - 60 * k, justify=tk.CENTER, tags=box_tags,
        )
        self.canvas.create_text(
            cx, y1 + pad + title_h + 18 * k + msg_h / 2, text=modal["message"], fill="#FFFFFF", font=msg_font,
            width=box_w - 70 * k, justify=tk.CENTER, tags=box_tags,
        )

        buttons = modal.get("buttons") or [(modal["button"], accent, None)]
        btn_w = (220 if len(buttons) == 1 else 230) * k
        gap = 24 * k
        total_w = len(buttons) * btn_w + (len(buttons) - 1) * gap
        btn_cy = y2 - pad - btn_h / 2
        for i, (label, color, action) in enumerate(buttons):
            btn_cx = cx - total_w / 2 + btn_w / 2 + i * (btn_w + gap)
            btn_tag = f"modal_btn_{i}"
            btn_tags = box_tags + (btn_tag,)
            rrect(
                btn_cx - btn_w / 2 + 3 * k, btn_cy - btn_h / 2 + 5 * k, btn_cx + btn_w / 2 + 3 * k,
                btn_cy + btn_h / 2 + 5 * k, 14 * k, "#0A0E12", tags=box_tags, k=1.0,
            )
            rrect(
                btn_cx - btn_w / 2, btn_cy - btn_h / 2, btn_cx + btn_w / 2, btn_cy + btn_h / 2,
                14 * k, color, outline="#10151A", width=max(1, 3 * k), tags=btn_tags, k=1.0,
            )
            r, g, b = (int(color[j:j + 2], 16) for j in (1, 3, 5))
            text_col = "#14181E" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"
            self.canvas.create_text(
                btn_cx, btn_cy, text=label, fill=text_col, font=self._font(14), tags=btn_tags,
            )
            self.canvas.tag_bind(btn_tag, "<Button-1>", lambda _e, act=action: self._close_dialog(act))
            self.canvas.tag_bind(btn_tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(btn_tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))

        if animate:
            self._pop_in("modal_box")

    def _text_height(self, text: str, font, width: float = 0) -> float:
        """Alto que ocupará un texto ya con su ancho de ajuste."""
        item = self.canvas.create_text(0, 0, text=text, font=font, width=width)
        x1, y1, x2, y2 = self.canvas.bbox(item)
        self.canvas.delete(item)
        return y2 - y1

    def _slide_in_screen(self, dy: float = 22.0, duration: float = 0.28) -> None:
        """Transición de cambio de pantalla: la interfaz nueva sube y se asienta suavemente."""
        dy *= self.ui_scale
        state = {"off": dy}
        self.canvas.move("ui_elem", 0, dy)

        def frame(t: float) -> None:
            new = dy * (1.0 - ease_out_cubic(t))
            self.canvas.move("ui_elem", 0, new - state["off"])
            state["off"] = new

        self._start_anim("transition", duration, frame)

    def _animate_bankroll_towards(self, target: int) -> None:
        """Contador de banca que rueda hasta el valor real (estilo Balatro)."""
        if self.shown_bankroll is None:
            self.shown_bankroll = target
            return
        if self.shown_bankroll == target:
            return
        if self._bankroll_target == target and "bankroll" in self._anims:
            return
        self._bankroll_target = target
        start_val = self.shown_bankroll
        duration = min(0.9, 0.3 + abs(target - start_val) / 400.0)

        def frame(t: float) -> None:
            self.shown_bankroll = round(start_val + (target - start_val) * ease_out_cubic(t))
            if self._bankroll_text_item is not None:
                self.canvas.itemconfig(self._bankroll_text_item, text=f"${self.shown_bankroll}")

        self._start_anim("bankroll", duration, frame)

    # =========================================================================
    # Primitivas de Dibujo Modernas Estilo Balatro
    # =========================================================================
    def _draw_rounded_rect(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int = 12,
        fill: str = "#1E2730",
        outline: str = "#10151A",
        width: int = 3,
        shadow: bool = True,
        tags: Tuple[str, ...] = (),
    ) -> None:
        base_tags = ("ui_elem",) + tags
        if shadow:
            self.visuals._rrect_fast(
                x1 + 4, y1 + 5, x2 + 4, y2 + 5, radius, "#090C10", tags=base_tags + ("shadow",)
            )
        self.visuals._rrect(x1, y1, x2, y2, radius, fill, outline=outline, width=width, tags=base_tags)

    def _draw_pill_button(
        self,
        cx: int,
        cy: int,
        w: int,
        h: int,
        text: str,
        bg_rgb: Tuple[int, int, int],
        tag: str,
        command: Callable[[], None],
        font_size: int = 12,
        fg_rgb: Tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        face_tag = f"{tag}_face"
        self.visuals.draw_button(cx, cy, w, h, bg_rgb, tags=("ui_elem", tag), face_tag=face_tag)
        self._hover_faces[tag] = (
            face_tag,
            f"#{bg_rgb[0]:02x}{bg_rgb[1]:02x}{bg_rgb[2]:02x}",
            "#%02x%02x%02x" % tuple(min(255, c + 24) for c in bg_rgb),
        )
        fg_hex = f"#{fg_rgb[0]:02x}{fg_rgb[1]:02x}{fg_rgb[2]:02x}"
        self.canvas.create_text(
            cx,
            cy - 1,
            text=text,
            fill=fg_hex,
            font=self._font(font_size),
            tags=("ui_elem", tag),
        )
        self.canvas.tag_bind(tag, "<Button-1>", lambda _e: command())
        self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))
        self._register_hover(tag, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, lift=3)

    def _bind_card_hover_inspector(self, tag: str, title: str, description: str) -> None:
        def on_enter(_event) -> None:
            if self._modal is not None:
                return
            self.canvas.config(cursor="hand2")
            self.canvas.delete("hover_card_popup")
            px1, py1, px2, py2 = 430, 168, 920, 260
            self.visuals._rrect_fast(px1 + 4, py1 + 5, px2 + 4, py2 + 5, 14, "#090C10", tags=("hover_card_popup",))
            self.visuals._rrect(
                px1, py1, px2, py2, 14, "#11171D", outline="#F3B958", width=4, tags=("hover_card_popup",)
            )
            self.canvas.create_text(
                (px1 + px2) // 2, py1 + 26,
                text=title.upper(),
                fill="#F3B958",
                font=self._font(14),
                tags=("hover_card_popup",),
            )
            self.canvas.create_text(
                (px1 + px2) // 2, py1 + 60,
                text=description,
                fill="#FFFFFF",
                font=self._font(12),
                width=460,
                justify=tk.CENTER,
                tags=("hover_card_popup",),
            )
            self._finalize_layer("hover_card_popup")

        def on_leave(_event) -> None:
            self.canvas.config(cursor="")
            self.canvas.delete("hover_card_popup")

        self.canvas.tag_bind(tag, "<Enter>", on_enter)
        self.canvas.tag_bind(tag, "<Leave>", on_leave)

    # =========================================================================
    # Renderizado Principal con Doble Búfer (Cero Parpadeo al Cliquear)
    # =========================================================================
    def _redraw(self, transition: bool = False) -> None:
        if self.music_director is not None:
            self.music_director.update()
        # Marcamos los elementos anteriores como "old_frame" y los borramos recién AL FINAL
        # después de haber dibujado el nuevo frame encima, evitando que el monitor parpadee en negro.
        self.canvas.addtag_all("old_frame")
        self.canvas.dtag("old_frame", "ui_elem")

        self._wheel_view = None
        self._col_preview_title_item = None
        self._col_preview_cat_item = None
        self._col_preview_desc_item = None
        self._bankroll_text_item = None
        self._chips_text_item = None
        self._mult_text_item = None
        self._spin_ball_item = None

        # Las animaciones atadas a ítems del frame viejo dejan de tener sentido
        for key in [k for k in self._anims if k.startswith(("lift:", "pop:", "bump:")) or k == "transition"]:
            del self._anims[key]
        self._hover_regions = []
        self._hover_offsets = {}
        self._hover_faces = {}
        old_widgets, self._embedded = self._embedded, []
        self._hovered_tag = None

        self._update_offsets()

        if self.in_intro_screen:
            self._draw_intro_screen()
        elif self.in_collection_menu:
            self._draw_collection_screen()
        elif self.in_options_menu:
            self._draw_options_screen()
        elif self.in_main_menu:
            self._draw_main_menu()
        else:
            self._draw_game_screen()

        # Escalamos la capa interactiva al tamaño de la pantalla y la centramos
        self._finalize_layer("ui_elem")

        # Eliminamos el frame viejo una vez que el nuevo ya está completamente dibujado en pantalla
        self.canvas.delete("old_frame")
        self.canvas.widths.clear()
        for widget in old_widgets:
            widget.destroy()

        if transition:
            self._slide_in_screen()
        elif self._banner_items and self.floating_banner_text != self._last_popped_banner:
            self._pop_in("banner")
        self._last_popped_banner = self.floating_banner_text if self._banner_items else ""
        self._banner_items = False

        if self._is_game_screen():
            self._schedule_autosave()
        if self._ruletin is not None:
            self._draw_ruletin_layer()
        if self._modal is not None:
            self._draw_modal_layer(animate=False)
            return

        # Si el mouse ya está sobre algo, lo dejamos levantado sin re-animar (evita "saltos" al hacer clic)
        try:
            px = (self.canvas.winfo_pointerx() - self.canvas.winfo_rootx() - self.offset_x) / self.ui_scale
            py = (self.canvas.winfo_pointery() - self.canvas.winfo_rooty() - self.offset_y) / self.ui_scale
            self._set_hovered(self._hover_hit(px, py), instant=True)
        except tk.TclError:
            pass

    # -------------------------------------------------------------------------
    # 0. ANIMACIÓN DE ENTRADA: "¿GIRAR?" -> BOLA GIRA Y CAE EN EL 0 -> MENÚ
    # -------------------------------------------------------------------------
    def _open_intro_screen(self) -> None:
        self.audio.play("chip")
        self.in_intro_screen = True
        self.intro_spinning = False
        self.intro_landed_zero = False
        self.intro_wheel_angle = 0.0
        self._redraw(transition=True)

    def _finish_intro_screen(self) -> None:
        if not self.in_intro_screen:
            return
        self._cancel_anim("intro_spin")
        self.intro_spinning = False
        self.intro_landed_zero = False
        self.in_intro_screen = False
        self.in_main_menu = True
        self._redraw(transition=True)

    def _draw_intro_screen(self) -> None:
        full_w = max(self.WIDTH, self.canvas.winfo_width())
        full_h = max(self.HEIGHT, self.canvas.winfo_height())
        bg_cx, bg_cy = full_w // 2, full_h // 2

        # Escenario oscuro de casino con foco central sobre la ruleta
        self.canvas.create_rectangle(0, 0, full_w, full_h, fill="#0E1714", width=0)
        ring_palette = ["#111E19", "#152620", "#192F27", "#1E382E", "#162A22"]
        max_r = int(max(full_w, full_h) * 0.72)
        for idx, r in enumerate(range(max_r, 90, -110)):
            col = ring_palette[idx % len(ring_palette)]
            self.canvas.create_oval(
                bg_cx - r, bg_cy - r, bg_cx + r, bg_cy + r, fill=col, outline="#244236", width=2
            )

        cx = self.WIDTH // 2
        cy = self.HEIGHT // 2

        # Logo superior y pedestal circular dorado en el centro para la ruleta de entrada
        self.visuals.draw_logo(cx, 78, tags=("ui_elem",))
        wheel_cx, wheel_cy = cx, cy - 10
        self._draw_rounded_rect(
            wheel_cx - 155, wheel_cy - 155, wheel_cx + 155, wheel_cy + 155,
            radius=32, fill="#16222B", outline="#F3B958", width=4, tags=("intro_spin_trigger",)
        )

        # Renderizamos la ruleta (cuando aterriza muestra el 0 iluminado)
        win_num = 0 if self.intro_landed_zero else None
        self._wheel_view = self.visuals.create_wheel_view(
            self.engine.wheel, wheel_cx, wheel_cy, self.intro_wheel_angle, win_num, StreakState.LUCKY,
            tags=("ui_elem", "intro_spin_trigger"),
        )

        # Bola blanca de ruleta lista en la pista superior antes de girar
        if not self.intro_landed_zero and not self.intro_spinning:
            ball_start_rad = math.radians(-90.0)
            ball_r = self._ball_track_radius() / self.ui_scale
            bx = wheel_cx + math.cos(ball_start_rad) * ball_r
            by = wheel_cy + math.sin(ball_start_rad) * ball_r
            self.visuals.create_ball(bx, by, tags=("ui_elem", "intro_spin_trigger"))

        # Botones centrales "¿GIRAR?" y "JUGAR" debajo de la rueda
        if not self.intro_spinning and not self.intro_landed_zero:
            self._draw_pill_button(
                cx - 140, wheel_cy + 205, 250, 56, "¿GIRAR INTRO?", BALATRO_GOLD,
                "btn_intro_spin", self._start_intro_spin, font_size=17, fg_rgb=(18, 22, 28)
            )
            self._draw_pill_button(
                cx + 140, wheel_cy + 205, 250, 56, "JUGAR AHORA", BALATRO_BLUE,
                "btn_intro_play_now", self._play_default, font_size=17
            )
            self.canvas.create_text(
                cx, wheel_cy + 254,
                text="Haz clic en '¿GIRAR INTRO?' para lanzar la bola al 0 o en 'JUGAR AHORA' para ir a la mesa",
                fill="#A8C5B8", font=self._font(11), tags=("ui_elem",)
            )
            self.canvas.tag_bind("intro_spin_trigger", "<Button-1>", lambda _e: self._start_intro_spin())
            self.canvas.tag_bind("intro_spin_trigger", "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind("intro_spin_trigger", "<Leave>", lambda _e: self.canvas.config(cursor=""))
        elif self.intro_spinning:
            self._draw_rounded_rect(
                cx - 210, wheel_cy + 178, cx + 210, wheel_cy + 234,
                radius=16, fill="#1D2630", outline="#F3B958", width=3
            )
            self.canvas.create_text(
                cx, wheel_cy + 206,
                text="GIRANDO LA BOLA...",
                fill="#F3B958", font=self._font(16), tags=("ui_elem",)
            )
        else:
            self._draw_rounded_rect(
                cx - 270, wheel_cy + 178, cx + 270, wheel_cy + 236,
                radius=16, fill="#153828", outline="#2BB673", width=4
            )
            self.canvas.create_text(
                cx, wheel_cy + 207,
                text="¡CAYO EN EL 0 VERDE!  -  BIENVENIDO A THE ROULETE",
                fill="#F3B958", font=self._font(14), tags=("ui_elem",)
            )

    def _start_intro_spin(self) -> None:
        if self.intro_spinning or self.intro_landed_zero:
            return
        self.intro_spinning = True
        self.audio.play("chip")
        self._redraw()

        def on_done() -> None:
            # La bola aterrizó en el 0 verde: iluminamos el 0 y abrimos el Menú Principal
            self.intro_spinning = False
            self.intro_landed_zero = True
            self.intro_wheel_angle = 0.0
            self.audio.play("lucky")
            self.root.after(140, lambda: self.audio.play("win"))
            self._redraw()
            self.root.after(1000, self._finish_intro_screen)

        # La rueda da 2 vueltas completas y se detiene en 0 grados; la bola cae en el bolsillo 0
        self._run_wheel_spin(
            key="intro_spin",
            wheel=self.engine.wheel,
            start_angle=0.0,
            total_rotation=360.0,
            duration=4.5,
            target_number=0,
            center=(self.WIDTH // 2, self.HEIGHT // 2 - 10),
            streak_state=StreakState.LUCKY,
            set_angle=lambda a: setattr(self, "intro_wheel_angle", a),
            on_done=on_done,
            shake=False,
        )

    # -------------------------------------------------------------------------
    # Giro animado compartido (intro y partida): rueda + bola que cae en el bolsillo
    # -------------------------------------------------------------------------
    def _ball_track_radius(self) -> float:
        outer_r, _inner_r, _ball_r = self.visuals.wheel_geometry()
        return outer_r - 7.0

    def _run_wheel_spin(
        self,
        key: str,
        wheel: RouletteWheel,
        start_angle: float,
        total_rotation: float,
        duration: float,
        target_number: int,
        center: Tuple[int, int],
        streak_state: StreakState,
        set_angle: Callable[[float], None],
        on_done: Callable[[], None],
        shake: bool,
        slow_tail: float = 1.0,
    ) -> None:
        """
        La rueda gira en sentido horario desacelerando (ease-out quint) y la bola gira en sentido
        contrario por la pista exterior, pierde velocidad, cae al anillo de bolsillos con un pequeño
        rebote y termina EXACTAMENTE sobre el bolsillo ganador, girando junto con la rueda.
        """
        track_r = self._ball_track_radius()
        _outer, _inner, pocket_r = self.visuals.wheel_geometry()
        pocket_step = 360.0 / max(1, len(wheel.pockets))

        def pocket_mid(rot: float) -> float:
            mid = self.visuals.pocket_center_angle(wheel, target_number, rot)
            return mid if mid is not None else rot

        # Desfase inicial positivo => la bola sale de arriba y recorre ~3 vueltas en sentido antihorario
        ball_offset = ((-90.0 - pocket_mid(start_angle)) % 360.0) + 3 * 360.0
        settle_t = 0.80
        drop_start, drop_end = 0.55, 0.84
        k = self.ui_scale
        state = {"last_pocket": None, "last_tick": 0.0}

        def frame(t: float) -> None:
            angle = start_angle + total_rotation * ease_out_quint(t)
            set_angle(angle % 360.0)

            eb = ease_out_cubic(min(1.0, t / settle_t))
            ball_deg = pocket_mid(angle) + (1.0 - eb) * ball_offset
            if t <= drop_start:
                r = track_r
            else:
                u = min(1.0, (t - drop_start) / (drop_end - drop_start))
                r = track_r + (pocket_r - track_r) * ease_out_back(u, 2.4)

            # Temblor suave (senoidal y amortiguado) solo en Modo Furia
            dx = dy = 0.0
            if shake:
                amp = 6.0 * k * (1.0 - t)
                dx = amp * math.sin(t * 2 * math.pi * 11)
                dy = amp * math.cos(t * 2 * math.pi * 8)

            wcx = center[0] * k + self.offset_x + dx
            wcy = center[1] * k + self.offset_y + dy
            view = self._wheel_view
            if view is not None:
                view.move_center_to(wcx, wcy)
                view.update(angle)

            rad = math.radians(ball_deg)
            bx = wcx + math.cos(rad) * r
            by = wcy + math.sin(rad) * r
            if not self.canvas.find_withtag("spin_ball"):
                self._spin_ball_item = self.visuals.create_ball(0, 0, tags=("ui_elem", "spin_ball"))
            for item in self._spin_ball_item:
                self.canvas.coords(item, bx, by)
            self.canvas.tag_raise("spin_ball")

            # "Tic" cada vez que la bola cruza un separador de bolsillo (se espacia al frenar)
            pocket_idx = math.floor((ball_deg - angle) / pocket_step)
            now = time.perf_counter()
            if pocket_idx != state["last_pocket"]:
                if state["last_pocket"] is not None and now - state["last_tick"] > 0.05:
                    self.audio.play("tick")
                    state["last_tick"] = now
                state["last_pocket"] = pocket_idx

        def finish() -> None:
            self.canvas.delete("spin_ball")
            self._spin_ball_item = None
            on_done()

        # Reloj propio: permite frenar el tramo final (cámara lenta) y acelerar con un clic
        slow_from = 0.6
        token = object()
        self._anims[key] = token
        clock = {"t": 0.0, "last": time.perf_counter()}
        self._spin_skip_requested = False

        def step() -> None:
            if self._anims.get(key) is not token:
                return
            now = time.perf_counter()
            dt = min(0.1, now - clock["last"])
            clock["last"] = now
            rate = 1.0 / duration
            if slow_tail > 1.0 and clock["t"] >= slow_from:
                rate /= slow_tail
            if self._spin_skip_requested:
                rate = max(rate, 1.6)  # lo que falte se resuelve en ~0.6 s
            clock["t"] = min(1.0, clock["t"] + dt * rate)
            frame(clock["t"])
            if clock["t"] >= 1.0:
                del self._anims[key]
                finish()
            else:
                self.root.after(_frame_delay_ms(now), step)

        step()

    # =========================================================================
    # RULETÍN: mascota guía con globo de texto (estilo tutorial de cartas)
    # =========================================================================
    RULETIN_CPS = 42.0  # letras por segundo al escribir

    def ruletin_say(
        self,
        lines: List[str],
        blocking: bool = False,
        key: Optional[str] = None,
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Ruletín dice una o varias frases. `blocking` = tutorial (oscurece y espera clics);
        si no, es un comentario que se va solo. `key` evita repetir un comentario de una sola vez.
        """
        if key is not None:
            if key in self._ruletin_said:
                return
            self._ruletin_said.add(key)
            self._save_settings()
        if not self.ruletin_enabled:
            if on_done is not None:
                on_done()
            return
        message = {"lines": list(lines), "blocking": blocking, "on_done": on_done}
        if self._ruletin is None:
            self._ruletin_open(message)
        elif blocking or self._ruletin["blocking"]:
            self._ruletin_queue.append(message)
        else:
            # Un comentario nuevo reemplaza al anterior (no se acumulan)
            self._ruletin_close(run_callback=True, open_next=False)
            self._ruletin_open(message)

    def _ruletin_open(self, message: Dict) -> None:
        message.update(idx=0, chars=0.0, entered_at=time.perf_counter(), done_typing=False, token=object())
        self._ruletin = message
        if message["blocking"]:
            self._set_hovered(None)
        self._draw_ruletin_layer()
        self._ruletin_start_loops()

    def _ruletin_close(self, run_callback: bool = True, open_next: bool = True) -> None:
        message = self._ruletin
        if message is None:
            return
        self._cancel_anim("ruletin_loop")
        self.canvas.delete("ruletin_layer")
        self._ruletin = None
        self._ruletin_has_sprite = False
        self._ruletin_text_item = None
        self._ruletin_hint_item = None
        if run_callback and message["on_done"] is not None:
            message["on_done"]()
        if open_next and self._ruletin is None and self._ruletin_queue:
            self._ruletin_open(self._ruletin_queue.pop(0))

    def _ruletin_advance(self) -> None:
        """Clic: si está escribiendo, completa la frase; si terminó, pasa a la siguiente."""
        msg = self._ruletin
        if msg is None:
            return
        line = msg["lines"][msg["idx"]]
        if msg["chars"] < len(line):
            msg["chars"] = float(len(line))
            return
        if msg["idx"] + 1 < len(msg["lines"]):
            msg["idx"] += 1
            msg["chars"] = 0.0
            msg["done_typing"] = False
            msg["token"] = object()
            self.audio.play("chip")
            self._draw_ruletin_layer()
            return
        self._ruletin_close()

    def _ruletin_skip_all(self) -> None:
        self._ruletin_queue = [m for m in self._ruletin_queue if not m["blocking"]]
        self._ruletin_close()

    def _draw_ruletin_layer(self) -> None:
        """Ruletín abajo a la izquierda y su globo de texto con colita apuntándole."""
        msg = self._ruletin
        if msg is None:
            return
        self.canvas.delete("ruletin_layer")
        k = self.ui_scale
        tags = ("ruletin_layer",)

        if msg["blocking"]:
            cw = max(self.canvas.winfo_width(), int(self.WIDTH * k))
            ch = max(self.canvas.winfo_height(), int(self.HEIGHT * k))
            overlay = self.canvas.create_rectangle(
                0, 0, cw, ch, fill="#05080B", stipple="gray25", width=0, tags=tags
            )
            self.canvas.tag_bind(overlay, "<Button-1>", lambda _e: self._ruletin_advance())

        # Imagen propia si existe; si no, el Ruletín dibujado (entra en el recuadro abajo a la izquierda)
        sprite_tags = tags + ("ruletin_click", "ruletin_sprite")
        if self._ruletin_photo_scale != self.ui_scale:
            self._ruletin_photo = self.visuals.load_ruletin_photo(self._ruletin_image_path)
            self._ruletin_photo_scale = self.ui_scale
        if self._ruletin_photo is not None:
            sx, sy = self._to_screen(140, 676)
            self.canvas.create_image(sx, sy, anchor="s", image=self._ruletin_photo, tags=sprite_tags)
        else:
            self.visuals.draw_ruletin(55, 486, tags=sprite_tags)
            self._finalize_layer("ruletin_sprite")
        msg["sprite_offset"] = 0.0
        self._ruletin_has_sprite = True

        # Globo: se mide con la frase completa para que no cambie de tamaño mientras escribe
        line = msg["lines"][msg["idx"]]
        bx1, _ = self._to_screen(236, 0)
        bx2, by2 = self._to_screen(700, 640)
        pad = 20 * k
        name_font, text_font = self._font(11), self._font(14)
        text_width = bx2 - bx1 - 2 * pad
        name_h = self._text_height("RULETÍN", name_font)
        text_h = self._text_height(line, text_font, text_width)
        hint_h = 18 * k
        by1 = by2 - (pad + name_h + 6 * k + text_h + 10 * k + hint_h + pad * 0.6)

        rrect = self.visuals._rrect
        rrect(bx1 + 6 * k, by1 + 8 * k, bx2 + 6 * k, by2 + 8 * k, 22 * k, "#05080B", tags=tags, k=1.0)
        self.canvas.create_polygon(
            bx1 + 6 * k, by2 - 52 * k, bx1 - 26 * k, by2 - 22 * k, bx1 + 6 * k, by2 - 26 * k,
            fill="#05080B", outline="", tags=tags
        )
        click_tags = tags + ("ruletin_click",)
        rrect(bx1, by1, bx2, by2, 22 * k, "#F8F4E8", outline="#1D2630", width=max(2, 4 * k), tags=click_tags, k=1.0)
        self.canvas.create_polygon(
            bx1 + 2 * k, by2 - 58 * k, bx1 - 30 * k, by2 - 30 * k, bx1 + 2 * k, by2 - 32 * k,
            fill="#F8F4E8", outline="#1D2630", width=max(2, 4 * k), tags=click_tags
        )
        # Tapamos el borde del globo donde nace la colita
        self.canvas.create_line(
            bx1 + 2 * k, by2 - 55 * k, bx1 + 2 * k, by2 - 35 * k, fill="#F8F4E8",
            width=max(3, 6 * k), tags=tags
        )
        self.canvas.create_text(
            bx1 + pad, by1 + pad, anchor="nw", text="RULETÍN", fill="#E63E34", font=name_font, tags=click_tags,
        )
        text_item = self.canvas.create_text(
            bx1 + pad, by1 + pad + name_h + 6 * k, anchor="nw", text=line, fill="#1D2630", font=text_font,
            width=text_width, tags=click_tags,
        )

        progress = f"{msg['idx'] + 1}/{len(msg['lines'])}   " if len(msg["lines"]) > 1 else ""
        skip = "  -  ESC PARA SALTAR" if msg["blocking"] else ""
        self._ruletin_hint_item = self.canvas.create_text(
            bx2 - pad, by2 - pad * 0.6, anchor="se", text=f"{progress}CLIC PARA SEGUIR >{skip}",
            fill="#8A93A0", font=self._font(9), tags=tags + ("ruletin_click",),
            state="normal" if msg["done_typing"] else "hidden",
        )
        if msg["blocking"]:
            skip_w, skip_h = 190 * k, 34 * k
            kx2, ky2 = bx2, by1 - 10 * k
            skip_tags = tags + ("ruletin_skip",)
            rrect(kx2 - skip_w + 3 * k, ky2 - skip_h + 4 * k, kx2 + 3 * k, ky2 + 4 * k, 12 * k,
                  "#05080B", tags=tags, k=1.0)
            rrect(kx2 - skip_w, ky2 - skip_h, kx2, ky2, 12 * k, "#E63E34", outline="#1D2630",
                  width=max(2, 3 * k), tags=skip_tags, k=1.0)
            self.canvas.create_text(
                kx2 - skip_w / 2, ky2 - skip_h / 2, text="SALTAR TUTORIAL", fill="#FFFFFF",
                font=self._font(11), tags=skip_tags
            )
            self.canvas.tag_bind("ruletin_skip", "<Button-1>", lambda _e: self._ruletin_skip_all())
            self.canvas.tag_bind("ruletin_skip", "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind("ruletin_skip", "<Leave>", lambda _e: self.canvas.config(cursor=""))

        self._ruletin_text_item = text_item
        self.canvas.itemconfig(text_item, text=line[: int(msg["chars"])])
        self.canvas.tag_bind("ruletin_click", "<Button-1>", lambda _e: self._ruletin_advance())
        self.canvas.tag_bind("ruletin_click", "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind("ruletin_click", "<Leave>", lambda _e: self.canvas.config(cursor=""))

    def _ruletin_start_loops(self) -> None:
        """Un solo bucle: escribe letra por letra con voz, y hace que Ruletín rebote/entre."""
        token = object()
        self._anims["ruletin_loop"] = token
        state = {"last": time.perf_counter(), "blip_at": 0}

        def step() -> None:
            if self._anims.get("ruletin_loop") is not token or self._ruletin is None:
                return
            msg = self._ruletin
            now = time.perf_counter()
            dt = min(0.1, now - state["last"])
            state["last"] = now
            line = msg["lines"][msg["idx"]]
            k = self.ui_scale

            typing = msg["chars"] < len(line)
            if typing:
                before = int(msg["chars"])
                msg["chars"] = min(float(len(line)), msg["chars"] + dt * self.RULETIN_CPS)
                after = int(msg["chars"])
                if self._ruletin_text_item is not None and after != before:
                    self.canvas.itemconfig(self._ruletin_text_item, text=line[:after])
                    # Una sílaba de voz cada ~3 letras (no en espacios ni signos)
                    new_letters = sum(1 for ch in line[before:after] if ch.isalnum())
                    state["blip_at"] += new_letters
                    if state["blip_at"] >= 3:
                        state["blip_at"] = 0
                        self.audio.play(f"ruletin_{random.randrange(10)}")
            elif not msg["done_typing"]:
                msg["done_typing"] = True
                if self._ruletin_hint_item is not None:
                    self.canvas.itemconfig(self._ruletin_hint_item, state="normal")
                if not msg["blocking"] and msg["idx"] == len(msg["lines"]) - 1:
                    done_token = msg["token"]
                    self.root.after(3200, lambda: self._ruletin_auto_close(done_token))

            # Entrada desde abajo con rebote + balanceo (más vivo mientras habla)
            if self._ruletin_has_sprite:
                elapsed = now - msg["entered_at"]
                enter = 170 * k * (1.0 - ease_out_back(min(1.0, elapsed / 0.45), 1.8))
                if typing:
                    bob = -abs(math.sin(elapsed * math.pi * 7.0)) * 7 * k
                else:
                    bob = math.sin(elapsed * math.pi * 1.6) * 3 * k
                self.canvas.move("ruletin_sprite", 0, enter + bob - msg["sprite_offset"])
                msg["sprite_offset"] = enter + bob
            self.root.after(_frame_delay_ms(now), step)

        step()

    def _ruletin_auto_close(self, token: object) -> None:
        msg = self._ruletin
        if msg is not None and not msg["blocking"] and msg["token"] is token:
            self._ruletin_close()

    def _on_tutorial_finished(self) -> None:
        self._tutorial_pending = False
        self._save_settings()

    def _start_tutorial(self) -> None:
        self._tutorial_pending = False
        self.ruletin_say(
            [
                "¡Hola, hola! Soy RULETÍN, el crupier más simpático de todo el casino. ¡Bienvenido a The Roulete!",
                "Tu objetivo es simple: llegar a la META DE LA CIEGA antes de quedarte sin giros. La tienes en grande a la derecha.",
                "Elige una ficha abajo y haz clic en el tapete para apostar. Puedes poner fichas en varias casillas.",
                "Aquí está el truco: si combinas apuestas que comparten números, formas una MANO. PAR, CERCO, ESCOLTA, VECINOS...",
                "Cada apuesta que acierta a la vez sube tu MULTI. GANANCIA = FICHAS x MULTI. ¡Mira la caja de la izquierda antes de girar!",
                "Si superas la ciega se abre la TIENDA: Comodines, Tarots y mejoras de mano. Y lo que no uses, lo puedes vender.",
                "Si te pierdes, haz clic en la caja FICHAS x MULTI. Mucha suerte... la vas a necesitar. ¡Je, je!",
            ],
            blocking=True,
            on_done=self._on_tutorial_finished,
        )

    def _on_toggle_ruletin(self) -> None:
        self.ruletin_enabled = not self.ruletin_enabled
        self._save_settings()
        if not self.ruletin_enabled:
            self._ruletin_queue.clear()
            self._ruletin_close(open_next=False)
        self.audio.play("chip")
        self._redraw()
        if self.ruletin_enabled:
            self.ruletin_say(["¡Volví! Te extrañaba... bueno, un poquito."])

    def _on_repeat_tutorial(self) -> None:
        self._tutorial_pending = True
        self.ruletin_enabled = True
        self._save_settings()
        self._redraw()
        self.ruletin_say(["¡Trato hecho! Te lo explico todo apenas te sientes en la mesa."])

    def _on_canvas_click(self, _event) -> None:
        if self.is_spinning and self._spin_skip_allowed and not self._spin_skip_requested:
            self._spin_skip_requested = True
            self.canvas.itemconfig("dramatic_hint", text="ACELERANDO...")

    def _is_decisive_last_spin(self) -> bool:
        """Último giro de la ronda y, aun contando lo apostado, todavía no se llega a la meta."""
        econ = self.engine.economy
        quota = econ.current_quota
        wagered = self.engine.board.total_wagered
        reached = (
            econ.bankroll + wagered >= quota.target_quota
            and econ.level_profit + wagered >= quota.min_profit_required
        )
        return econ.spins_remaining == 1 and not reached

    def _start_dramatic_effects(self) -> None:
        """Oscurece la mesa, deja la ruleta iluminada y anuncia el ÚLTIMO GIRO."""
        k = self.ui_scale
        cw = max(self.canvas.winfo_width(), int(self.WIDTH * k))
        ch = max(self.canvas.winfo_height(), int(self.HEIGHT * k))
        self.canvas.create_rectangle(
            0, 0, cw, ch, fill="#05080B", stipple="gray50", width=0, tags=("dramatic",)
        )
        if self._wheel_view is not None:
            self.canvas.tag_raise(WheelView.TAG)
        self.canvas.create_text(
            757, 112, text="ULTIMO GIRO", fill="#FE5F55", font=self._font(30),
            tags=("dramatic", "dramatic_txt", "dramatic_title")
        )
        self.canvas.create_text(
            757, 143, text="CLIC PARA ACELERAR", fill="#FFFFFF", font=self._font(10),
            tags=("dramatic", "dramatic_txt", "dramatic_hint")
        )
        self._finalize_layer("dramatic_txt")
        self._pop_in("dramatic_title")
        self.audio.play("fury")
        self.ruletin_say([random.choice((
            "¡Último giro! No mires... bueno, sí, mira.",
            "Todo o nada, amigo. ¡Que ruede!",
            "Cruza los dedos. Yo cruzo los dos que tengo.",
        ))])

    # -------------------------------------------------------------------------
    # 1. MENÚ DE INICIO ESTILO BALATRO ("The Roulete")
    # -------------------------------------------------------------------------
    def _draw_main_menu(self) -> None:
        full_w = max(self.WIDTH, self.canvas.winfo_width())
        full_h = max(self.HEIGHT, self.canvas.winfo_height())
        bg_cx, bg_cy = full_w // 2, full_h // 2

        ring_palette = ["#1A2E26", "#261B26", "#182834", "#2D1C22", "#14221E"]
        max_r = int(max(full_w, full_h) * 0.75)
        for idx, r in enumerate(range(max_r, 80, -95)):
            col = ring_palette[idx % len(ring_palette)]
            self.canvas.create_oval(
                bg_cx - r, bg_cy - r, bg_cx + r, bg_cy + r, fill=col, outline="#2C423B", width=2
            )

        cx = self.WIDTH // 2
        self.visuals.draw_logo(cx, 82, tags=("ui_elem",))

        self._draw_rounded_rect(
            cx - 210, 148, cx + 210, 180, radius=14, fill="#1E2730", outline="#F3B958", width=2
        )
        self.canvas.create_text(
            cx, 164, text="ROGUELIKE CASINO DECKBUILDER", fill="#F3B958",
            font=self._font(12), tags=("ui_elem",)
        )

        self._draw_rounded_rect(
            cx - 145, 194, cx + 145, 480, radius=24, fill="#16222B", outline="#F3B958", width=3
        )
        self._wheel_view = self.visuals.create_wheel_view(
            self.engine.wheel, cx, 337, self.menu_angle, 0, StreakState.LUCKY, tags=("ui_elem",)
        )

        self.visuals.draw_card(
            cx - 255 - 68, 337 - 76, "Rubi Carmesi", "+50 Fichas en Rojo", "JOKER", BALATRO_RED, "RED_RUBY",
            width=136, height=152, highlighted=True, tags=("ui_elem",)
        )
        self.visuals.draw_card(
            cx + 255 - 68, 337 - 76, "El Sol", "Negras a Rojas", "TAROT", BALATRO_PURPLE, "THE_SUN",
            width=136, height=152, highlighted=True, tags=("ui_elem",)
        )

        # Dock inferior: CONTINUAR + NUEVA PARTIDA (si hay partida guardada) o JUGAR
        self._draw_rounded_rect(
            cx - 560, 502, cx + 560, 586,
            radius=20, fill="#1D2630", outline="#10151A", width=4
        )
        summary = read_save_summary(self._save_path)
        if summary is not None:
            self.canvas.create_text(
                cx, 491, text=f"PARTIDA GUARDADA:  CIEGA {summary['level']}  -  BANCA ${summary['bankroll']}",
                fill="#F3B958", font=self._font(10), tags=("ui_elem",)
            )
            buttons = [
                (180, "CONTINUAR", BALATRO_BLUE, "btn_menu_continue", self._continue_game, 15, (255, 255, 255)),
                (200, "NUEVA PARTIDA", BALATRO_TEAL, "btn_menu_new", self._on_new_game_clicked, 13, (255, 255, 255)),
            ]
        else:
            buttons = [(210, "JUGAR", BALATRO_BLUE, "btn_menu_play", self._new_game, 15, (255, 255, 255))]
        buttons += [
            (170, "COLECCION", BALATRO_PURPLE, "btn_menu_col", self._open_collection_menu,
             13, (255, 255, 255)),
            (160, "OPCIONES", (52, 73, 94), "btn_menu_opts", self._open_options_menu, 13, (255, 255, 255)),
            (150, "VER INTRO", BALATRO_GOLD, "btn_menu_intro", self._open_intro_screen, 12, (20, 24, 30)),
            (120, "SALIR", BALATRO_RED, "btn_menu_quit", self._quit_game, 13, (255, 255, 255)),
        ]
        gap = 22
        total_w = sum(b[0] for b in buttons) + gap * (len(buttons) - 1)
        x = cx - total_w / 2
        for width, label, rgb, tag, command, size, fg in buttons:
            self._draw_pill_button(x + width / 2, 544, width, 52, label, rgb, tag, command, font_size=size, fg_rgb=fg)
            x += width + gap

    # -------------------------------------------------------------------------
    # 1b. MENÚ DE OPCIONES (Dificultad, Música y Pantalla)
    # -------------------------------------------------------------------------
    DIFFICULTY_INFO = {
        Difficulty.EASY: (
            "FACIL", BALATRO_TEAL,
            "Si una apuesta sale mal, te devolvemos\nla MITAD de lo que apostaste.",
            "Ideal para aprender y experimentar.",
        ),
        Difficulty.NORMAL: (
            "NORMAL", BALATRO_RED,
            "Si una apuesta sale mal, pierdes\nTODO lo que apostaste.",
            "La ruleta clasica, sin red de seguridad.",
        ),
    }

    def _open_options_menu(self) -> None:
        self.audio.play("chip")
        self.in_options_menu = True
        self._redraw(transition=True)

    def _close_options_menu(self) -> None:
        self.audio.play("chip")
        self.in_options_menu = False
        self._redraw(transition=True)

    def _set_difficulty(self, difficulty: Difficulty) -> None:
        if difficulty == self.difficulty:
            return
        self.difficulty = difficulty
        self.engine.difficulty = difficulty
        self._save_settings()
        self.audio.play("buy" if difficulty == Difficulty.EASY else "chip")
        self._redraw()

    def _draw_options_screen(self) -> None:
        full_w = max(self.WIDTH, self.canvas.winfo_width())
        full_h = max(self.HEIGHT, self.canvas.winfo_height())
        self.canvas.create_rectangle(0, 0, full_w, full_h, fill="#161F28", width=0)
        cx = self.WIDTH // 2

        self._draw_rounded_rect(24, 10, self.WIDTH - 24, 62, radius=14, fill="#1E2834", outline="#F3B958", width=3)
        self.canvas.create_text(
            48, 36, anchor="w", text="OPCIONES", fill="#F3B958", font=self._font(15), tags=("ui_elem",)
        )
        self._draw_pill_button(
            self.WIDTH - 120, 36, 150, 36, "VOLVER", BALATRO_RED, "btn_opts_back",
            self._close_options_menu, font_size=12
        )

        # --- Dificultad: dos tarjetas grandes seleccionables
        self.canvas.create_text(
            cx, 100, text="DIFICULTAD", fill="#FFFFFF", font=self._font(18), tags=("ui_elem",)
        )
        self.canvas.create_text(
            cx, 128, text="Elige cuanto te cuesta una apuesta perdida. Puedes cambiarla cuando quieras.",
            fill="#8AA0B5", font=self._font(10), tags=("ui_elem",)
        )
        card_w, card_h, gap = 400, 230, 50
        for idx, diff in enumerate((Difficulty.EASY, Difficulty.NORMAL)):
            name, rgb, rule, flavor = self.DIFFICULTY_INFO[diff]
            accent = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
            selected = diff == self.difficulty
            x1 = cx - card_w - gap / 2 if idx == 0 else cx + gap / 2
            y1 = 152
            x2, y2 = x1 + card_w, y1 + card_h
            tag = f"opt_diff_{diff.name}"
            self._draw_rounded_rect(
                x1, y1, x2, y2, radius=18, fill="#1D2630" if selected else "#141B22",
                outline="#F3B958" if selected else "#2E3D4C", width=5 if selected else 3, tags=(tag,)
            )
            self._draw_rounded_rect(
                x1 + 18, y1 + 18, x2 - 18, y1 + 72, radius=12, fill=accent, outline="#10151A",
                width=2, shadow=False, tags=(tag,)
            )
            self.canvas.create_text(
                (x1 + x2) / 2, y1 + 45, text=name, fill="#FFFFFF", font=self._font(26), tags=("ui_elem", tag)
            )
            self.canvas.create_text(
                (x1 + x2) / 2, y1 + 122, text=rule, fill="#FFFFFF", font=self._font(13),
                justify=tk.CENTER, tags=("ui_elem", tag)
            )
            self.canvas.create_text(
                (x1 + x2) / 2, y1 + 170, text=flavor, fill="#8AA0B5", font=self._font(10), tags=("ui_elem", tag)
            )
            badge_txt, badge_col = ("SELECCIONADA", "#F3B958") if selected else ("CLIC PARA ELEGIR", "#5C6F82")
            self.canvas.create_text(
                (x1 + x2) / 2, y1 + 204, text=badge_txt, fill=badge_col, font=self._font(11), tags=("ui_elem", tag)
            )
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, d=diff: self._set_difficulty(d))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))
            self._register_hover(tag, x1, y1, x2, y2, lift=6)

        self._draw_options_panel(cx)

        self._draw_pill_button(
            cx, 616, 300, 56, "LISTO", BALATRO_BLUE, "btn_opts_done",
            self._close_options_menu, font_size=17
        )

    def _draw_options_panel(self, cx: int) -> None:
        """Panel con controles reales de customtkinter: interruptores, deslizadores y botón."""
        widget_scale = ctk.ScalingTracker.get_widget_scaling(self.root)
        k = self.ui_scale / widget_scale
        font = ctk.CTkFont(family="Segoe UI", size=max(9, round(15 * k)), weight="bold")
        gold, panel_bg = "#F3B958", "#1D2630"

        panel = ctk.CTkFrame(
            self.canvas, width=round(860 * k), height=round(158 * k), fg_color=panel_bg, bg_color="#161F28",
            corner_radius=round(16 * k), border_width=max(1, round(2 * k)), border_color="#2E3D4C",
        )
        panel.grid_propagate(False)
        for col, weight in enumerate((0, 1, 0, 0)):
            panel.grid_columnconfigure(col, weight=weight)
        panel.grid_rowconfigure((0, 1), weight=1)
        pad_x, pad_y = round(22 * k), round(10 * k)

        def switch(text, value, command, row, col):
            widget = ctk.CTkSwitch(
                panel, text=text, font=font, text_color="#FFFFFF", progress_color="#2BB673",
                switch_width=round(46 * k), switch_height=round(24 * k), command=command,
            )
            if value:
                widget.select()
            widget.grid(row=row, column=col, padx=pad_x, pady=pad_y, sticky="w")
            return widget

        def slider(value, command, row):
            widget = ctk.CTkSlider(
                panel, from_=0, to=1, height=round(20 * k), progress_color=gold, button_color=gold,
                button_hover_color="#FFD27F", fg_color="#0B0F14", command=command,
            )
            widget.set(value)
            widget.grid(row=row, column=1, padx=(0, pad_x), pady=pad_y, sticky="ew")

        switch("MÚSICA", self.audio.music_playing, self._on_option_music, 0, 0)
        slider(self._music_volume, self._on_music_volume, 0)
        label = ctk.CTkLabel(panel, text="EFECTOS", font=font, text_color="#FFFFFF")
        label.grid(row=1, column=0, padx=pad_x, pady=pad_y, sticky="w")
        slider(self._effects_volume, self._on_effects_volume, 1)
        switch("PANTALLA COMPLETA (F11)", self.is_fullscreen, self.toggle_fullscreen, 0, 2)
        switch("RULETÍN", self.ruletin_enabled, self._on_toggle_ruletin, 1, 2)
        ctk.CTkButton(
            panel, text="REPETIR\nTUTORIAL", font=font, width=round(130 * k), height=round(70 * k),
            fg_color="#2BB673", hover_color="#37D088", text_color="#FFFFFF", corner_radius=round(12 * k),
            command=self._on_repeat_tutorial,
        ).grid(row=0, column=3, rowspan=2, padx=(0, pad_x), pady=pad_y)

        self.canvas.create_window(cx, 492, window=panel, tags=("ui_elem",))
        self._embedded.append(panel)

    def _on_option_music(self) -> None:
        self.audio.toggle_ambient_music()
        self._save_settings()

    def _on_music_volume(self, value: float) -> None:
        self._music_volume = float(value)
        mixer.set_music_gain(self._music_volume)
        self._save_settings()

    def _on_effects_volume(self, value: float) -> None:
        self._effects_volume = float(value)
        mixer.effects_gain = self._effects_volume
        self._save_settings()

    # -------------------------------------------------------------------------
    # 2. MENÚ DE COLECCIÓN ESTILO BALATRO (Álbum Visual + Inspector Gigante)
    # -------------------------------------------------------------------------
    def _open_collection_menu(self) -> None:
        if self.is_spinning:
            return
        self.audio.play("tarot")
        self.in_collection_menu = True
        self._redraw(transition=True)

    def _close_collection_menu(self) -> None:
        self.audio.play("chip")
        self.in_collection_menu = False
        self._redraw(transition=True)

    def _draw_collection_screen(self) -> None:
        full_w = max(self.WIDTH, self.canvas.winfo_width())
        full_h = max(self.HEIGHT, self.canvas.winfo_height())
        self.canvas.create_rectangle(0, 0, full_w, full_h, fill="#161F28", width=0)

        # Cabecera superior del álbum de Colección
        self._draw_rounded_rect(24, 10, self.WIDTH - 24, 62, radius=14, fill="#1E2834", outline="#F3B958", width=3)
        self.canvas.create_text(
            48, 36, anchor="w",
            text="COLECCION DE LA CASA  -  PASA EL MOUSE O HAZ CLIC EN CUALQUIER CARTA",
            fill="#F3B958", font=self._font(13), tags=("ui_elem",)
        )
        self._draw_pill_button(
            self.WIDTH - 120, 36, 150, 36, "VOLVER", BALATRO_RED, "btn_col_back",
            self._close_collection_menu, font_size=12
        )

        # Columna Izquierda: álbum con pestañas (x=24..832)
        self._draw_rounded_rect(24, 72, 832, self.HEIGHT - 12, radius=18, fill="#152E24", outline="#0D1D16", width=3)
        tabs = (
            (0, f"COMODINES ({len(JOKER_CATALOG)})"),
            (1, f"TAROTS Y FICHAS ({len(TAROT_CATALOG) + len(SPECIAL_CHIP_SHOP_INFO)})"),
            (2, "JEFES Y REGLAS"),
        )
        for page, label in tabs:
            active = page == self.collection_page
            self._draw_pill_button(
                150 + page * 250, 98, 230, 34, label, BALATRO_GOLD if active else (52, 73, 94),
                f"col_tab_{page}", lambda p=page: self._set_collection_page(p), font_size=11,
                fg_rgb=(20, 24, 30) if active else (255, 255, 255),
            )

        # Cuadrícula de cartas: 5 columnas
        grid: List[Tuple[str, str, str, str, Tuple[int, int, int], str, Optional[int]]] = []
        if self.collection_page == 0:
            for jk in JOKER_CATALOG.values():
                grid.append(("JOKER", jk.name, jk.description, "COMODIN PASIVO", BALATRO_RED, jk.joker_type.value, jk.cost))
        elif self.collection_page == 1:
            for tr in TAROT_CATALOG.values():
                grid.append(("TAROT", tr.name, tr.description, "CARTA DE TAROT (USO UNICO)", BALATRO_PURPLE,
                             tr.tarot_type.value, tr.cost))
            for sc_type, (sc_name, sc_desc, sc_cost) in SPECIAL_CHIP_SHOP_INFO.items():
                grid.append(("FICHA", sc_name, sc_desc, "FICHA ESPECIAL DE MESA", BALATRO_BLUE, sc_type.value, sc_cost))

        # Si no entran 4 filas, las cartas se achican para que ninguna quede cortada
        card_w, card_h, row_step = (142, 118, 126) if len(grid) <= 20 else (142, 102, 108)
        for idx, (badge, name, desc, category, rgb, key, cost) in enumerate(grid):
            cx = 44 + (idx % 5) * 154
            cy = 124 + (idx // 5) * row_step
            tag = f"col_card_{idx}"
            self.visuals.draw_card(
                cx, cy, title=name, subtitle=desc, badge_text=badge, accent_rgb=rgb, symbol_key=key,
                price=cost, width=card_w, height=card_h, highlighted=True, tags=("ui_elem", tag),
            )
            self._register_hover(tag, cx, cy, cx + card_w, cy + card_h, lift=6)
            self._bind_collection_card_preview(tag, name, desc, category, rgb, key, cost)

        if self.collection_page == 2:
            self.canvas.create_text(
                44, 136, anchor="w", text="CRUPIERES JEFE DE CIEGA (4/4)  -  APARECEN CADA 3 NIVELES",
                fill="#FE5F55", font=self._font(11), tags=("ui_elem",)
            )
            for idx, boss in enumerate(BOSS_CATALOG.values()):
                bx1 = 44 + idx * 192
                by1 = 152
                bx2 = bx1 + 180
                by2 = 282
                tag = f"col_boss_{idx}"
                self._draw_rounded_rect(
                    bx1, by1, bx2, by2, radius=12, fill="#8F241E", outline="#F3B958", width=3, tags=(tag,)
                )
                self.canvas.create_text(
                    (bx1 + bx2) // 2, by1 + 18, text="JEFE DE CASINO",
                    fill="#F3B958", font=self._font(9), tags=("ui_elem", tag)
                )
                self.canvas.create_text(
                    (bx1 + bx2) // 2, by1 + 44, text=boss.name,
                    fill="#FFFFFF", font=self._font(10), width=164, justify=tk.CENTER, tags=("ui_elem", tag)
                )
                self.canvas.create_text(
                    (bx1 + bx2) // 2, by1 + 88, text=boss.description,
                    fill="#FFE5E0", font=self._font(8), width=164, justify=tk.CENTER, tags=("ui_elem", tag)
                )
                self._bind_collection_card_preview(
                    tag, boss.name, boss.description, "CRUPIER JEFE (CADA 3 NIVELES)", BALATRO_RED,
                    boss.boss_type.value, None
                )
                self._register_hover(tag, bx1, by1, bx2, by2, lift=5)

            self._draw_rounded_rect(44, 300, 812, 650, radius=12, fill="#10221A", outline="#2BB673", width=2)
            self.canvas.create_text(
                428, 320, text="REGLAS DE LA MESA", fill="#2BB673", font=self._font(12), tags=("ui_elem",)
            )
            self.canvas.create_text(
                64, 480, anchor="w",
                text=(
                    "• MANOS: la forma en que combinas tus apuestas (PAR, CERCO, ESCOLTA, VECINOS) da MULTI.\n"
                    "   GANANCIA = FICHAS x MULTI. Haz clic en la caja FICHAS x MULTI de la partida para ver la guia.\n\n"
                    "• MODO FURIA (3 derrotas seguidas): x2 MULTI en el siguiente tiro acertado.\n\n"
                    "• MODO SUERTUDO (3 victorias seguidas): +15% de probabilidad para tus casillas.\n\n"
                    "• VENDER: haz clic en un Comodin o Tarot de la partida para venderlo por la mitad de su costo.\n\n"
                    "• FICHA ALL IN: apuesta toda tu banca de un solo clic."
                ),
                fill="#E0F7EA", font=self._font(10), width=730, justify=tk.LEFT, tags=("ui_elem",)
            )

        # Columna Derecha: INSPECTOR GIGANTE DE CARTA ESTILO BALATRO (x=848..1244)
        self._draw_rounded_rect(848, 72, self.WIDTH - 24, self.HEIGHT - 12, radius=18, fill="#1D2630", outline="#F3B958", width=4)
        self.canvas.create_text(
            1046, 100, text="INSPECTOR DE CARTA", fill="#F3B958",
            font=self._font(13), tags=("ui_elem",)
        )

        sel_title, sel_desc, sel_cat, sel_rgb, sel_key, sel_price = self.collection_selected
        self._draw_collection_big_card(sel_title, sel_desc, sel_cat, sel_rgb, sel_key, sel_price)

        # Caja de texto gigante debajo de la carta ampliada
        self._draw_rounded_rect(868, 352, self.WIDTH - 44, 594, radius=14, fill="#12181F", outline="#2E3D4C", width=3)

        self._col_preview_cat_item = self.canvas.create_text(
            1046, 380, text=sel_cat, fill="#80E5FF",
            font=self._font(10), tags=("ui_elem",)
        )
        self._col_preview_title_item = self.canvas.create_text(
            1046, 414, text=sel_title.upper(), fill="#F3B958",
            font=self._font(15), width=330, justify=tk.CENTER, tags=("ui_elem",)
        )
        self._col_preview_desc_item = self.canvas.create_text(
            1046, 496, text=sel_desc, fill="#FFFFFF",
            font=self._font(13), width=325, justify=tk.CENTER, tags=("ui_elem",)
        )

        self._draw_pill_button(
            1046, 630, 260, 42, "VOLVER AL JUEGO", BALATRO_BLUE, "btn_col_back_bottom",
            self._close_collection_menu, font_size=12
        )

    def _set_collection_page(self, page: int) -> None:
        if page == self.collection_page:
            return
        self.collection_page = page
        self.audio.play("chip")
        self._redraw()

    def _draw_collection_big_card(self, title, desc, category, accent_rgb, symbol_key, price) -> None:
        self.visuals.draw_card(
            1046 - 105, 228 - 108, title=title, subtitle=desc, badge_text=category.split()[0][:7],
            accent_rgb=accent_rgb, symbol_key=symbol_key, price=price, width=210, height=216,
            highlighted=True, tags=("ui_elem", "col_preview_card"),
        )

    def _bind_collection_card_preview(
        self,
        tag: str,
        title: str,
        desc: str,
        category: str,
        accent_rgb: Tuple[int, int, int],
        symbol_key: str,
        price: Optional[int],
    ) -> None:
        def on_inspect(_event) -> None:
            self.canvas.config(cursor="hand2")
            new_sel = (title, desc, category, accent_rgb, symbol_key, price)
            if new_sel == self.collection_selected:
                return
            self.collection_selected = new_sel
            self.canvas.delete("col_preview_card")
            self._draw_collection_big_card(title, desc, category, accent_rgb, symbol_key, price)
            self._finalize_layer("col_preview_card")
            if self._col_preview_cat_item is not None:
                cost_str = f"  |  COSTO EN TIENDA: ${price}" if price is not None else ""
                self.canvas.itemconfig(self._col_preview_cat_item, text=f"{category}{cost_str}")
            if self._col_preview_title_item is not None:
                self.canvas.itemconfig(self._col_preview_title_item, text=title.upper())
            if self._col_preview_desc_item is not None:
                self.canvas.itemconfig(self._col_preview_desc_item, text=desc)
            self._bump_item("col_preview_card", "bump:col_preview")

        self.canvas.tag_bind(tag, "<Enter>", on_inspect)
        self.canvas.tag_bind(tag, "<Button-1>", on_inspect)
        self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))

    # -------------------------------------------------------------------------
    # 3. PANTALLA DE PARTIDA ESTILO BALATRO
    # -------------------------------------------------------------------------
    def _draw_game_screen(self) -> None:
        full_w = max(self.WIDTH, self.canvas.winfo_width())
        full_h = max(self.HEIGHT, self.canvas.winfo_height())
        self.canvas.create_rectangle(0, 0, full_w, full_h, fill="#1B4332", width=0)

        self._draw_left_hud_panel()
        self._draw_top_cards_zone()
        if self.engine.economy.phase == GamePhase.SHOP:
            self._draw_shop_screen()
        else:
            self._draw_middle_wheel_zone()
            self._draw_bottom_betting_mat_zone()

    def _draw_left_hud_panel(self) -> None:
        econ = self.engine.economy
        quota = econ.current_quota
        streak = self.engine.streaks
        boss = self.engine.bosses.active_boss

        self._draw_rounded_rect(12, 10, 282, self.HEIGHT - 10, radius=18, fill="#1D2630", outline="#0E1318", width=4)

        self.canvas.create_text(
            26, 28, anchor="w", text="THE ROULETE", fill="#F3B958",
            font=self._font(14), tags=("ui_elem",)
        )
        self._draw_pill_button(
            232, 28, 76, 26, "MENU", (52, 73, 94), "btn_hud_menu", self._show_main_menu, font_size=10
        )

        # 1. Tarjeta de Ciega / Jefe
        blind_col = "#D6453D" if boss is not None else "#E57C23"
        self._draw_rounded_rect(22, 48, 272, 184, radius=14, fill=blind_col, outline="#10151A", width=3)

        title_txt = f"JEFE NV.{econ.current_level}" if boss else f"CIEGA NIVEL {econ.current_level}"
        self.canvas.create_text(
            147, 66, text=title_txt, fill="#FFFFFF", font=self._font(13), tags=("ui_elem",)
        )

        if boss is not None:
            rem_s = (
                f" ({max(0.0, boss.time_limit_seconds - self.engine.bosses.get_elapsed_turn_time()):.1f}s)"
                if boss.time_limit_seconds is not None
                else ""
            )
            desc_txt = f"{boss.name}{rem_s}\n{boss.description}"
        else:
            desc_txt = "Alcanza la cuota de fichas antes de quedarte sin giros."

        self.canvas.create_text(
            147, 94, text=desc_txt, fill="#FFF5D6", font=self._font(8),
            width=230, justify=tk.CENTER, tags=("ui_elem",)
        )

        self._draw_rounded_rect(32, 118, 262, 178, radius=10, fill="#141B22", outline="#0E1318", width=2, shadow=False)
        self.canvas.create_text(
            147, 134, text=f"META: ${quota.target_quota}",
            fill="#FE5F55", font=self._font(17), tags=("ui_elem",)
        )
        prof_now = max(0, econ.level_profit)
        prof_req = quota.min_profit_required
        prof_col = "#2BB673" if econ.has_met_min_profit() else "#F3B958"
        self.canvas.create_text(
            147, 154, text="GANANCIA MINIMA EN RONDA:",
            fill="#9FB1C1", font=self._font(8), tags=("ui_elem",)
        )
        self.canvas.create_text(
            147, 168, text=f"+${prof_now} / +${prof_req}",
            fill=prof_col, font=self._font(11), tags=("ui_elem",)
        )

        # 2. Barra de progreso hacia la Cuota y Ganancia Mínima
        self._draw_rounded_rect(22, 192, 272, 240, radius=12, fill="#141B22", outline="#0E1318", width=3)
        self.canvas.create_text(
            147, 204, text="PROGRESO DE CIEGA", fill="#AAB8C5", font=self._font(8), tags=("ui_elem",)
        )

        quota_ratio = min(1.0, econ.bankroll / max(1, quota.target_quota))
        profit_ratio = min(1.0, max(0, econ.level_profit) / max(1, quota.min_profit_required))
        ratio = min(quota_ratio, profit_ratio)
        self._draw_rounded_rect(34, 215, 260, 233, radius=8, fill="#0B0F14", outline="#2C3A47", width=1, shadow=False)
        if ratio > 0:
            bar_w = max(16, int(226 * ratio))
            bar_col = "#2BB673" if econ.can_pay_quota() else "#009DFF"
            self._draw_rounded_rect(
                34, 215, 34 + bar_w, 233, radius=8, fill=bar_col, outline="", width=0, shadow=False
            )
        status_pct_txt = (
            f"LISTO PARA COBRAR (100%)"
            if econ.can_pay_quota()
            else f"Ganancia: +${prof_now}/+${prof_req} ({int(ratio * 100)}%)"
        )
        self.canvas.create_text(
            147, 224, text=status_pct_txt,
            fill="#FFFFFF", font=self._font(8), tags=("ui_elem",)
        )

        # 3. Caja Balatro: [ FICHAS ] X [ MULTI ] con la mano actual (clic = guía de manos)
        hand_title, chips_val, mult_val = self._score_box_values()
        self._draw_rounded_rect(
            22, 248, 272, 336, radius=14, fill="#141B22", outline="#0E1318", width=3, tags=("hud_score",)
        )
        self.canvas.create_text(
            147, 261, text=hand_title, fill="#F3B958", font=self._font(9), tags=("ui_elem", "hud_score")
        )

        self._draw_rounded_rect(
            32, 274, 130, 326, radius=12, fill="#009DFF", outline="#0A1017", width=2, tags=("hud_score",)
        )
        self.canvas.create_text(
            81, 285, text="FICHAS", fill="#D6F0FF", font=self._font(8), tags=("ui_elem", "hud_score")
        )
        self._chips_text_item = self.canvas.create_text(
            81, 307, text=str(chips_val), fill="#FFFFFF", font=self._font(16), tags=("ui_elem", "hud_score")
        )

        self.canvas.create_text(
            147, 300, text="X", fill="#FE5F55", font=self._font(16), tags=("ui_elem", "hud_score")
        )

        self._draw_rounded_rect(
            164, 274, 262, 326, radius=12, fill="#FE5F55", outline="#0A1017", width=2, tags=("hud_score",)
        )
        self.canvas.create_text(
            213, 285, text="MULTI", fill="#FFE3E0", font=self._font(8), tags=("ui_elem", "hud_score")
        )
        self._mult_text_item = self.canvas.create_text(
            213, 307, text=self._fmt_mult(mult_val), fill="#FFFFFF", font=self._font(16),
            tags=("ui_elem", "hud_score")
        )
        self.canvas.tag_bind("hud_score", "<Button-1>", lambda _e: self._show_hands_guide())
        self.canvas.tag_bind("hud_score", "<Enter>", lambda _e: self.canvas.config(cursor="question_arrow"))
        self.canvas.tag_bind("hud_score", "<Leave>", lambda _e: self.canvas.config(cursor=""))

        # 4. Cajas gemelas: GIROS y RACHA
        self._draw_rounded_rect(22, 344, 142, 410, radius=12, fill="#24303C", outline="#10151A", width=3)
        self.canvas.create_text(
            82, 358, text="GIROS", fill="#AAB8C5", font=self._font(8), tags=("ui_elem",)
        )
        self.canvas.create_text(
            82, 386, text=str(econ.spins_remaining), fill="#009DFF",
            font=self._font(19), tags=("ui_elem",)
        )

        streak_bg = (
            "#8F241E" if streak.state == StreakState.FURY
            else ("#7D5F14" if streak.state == StreakState.LUCKY else "#24303C")
        )
        self._draw_rounded_rect(152, 344, 272, 410, radius=12, fill=streak_bg, outline="#10151A", width=3)
        self.canvas.create_text(
            212, 358, text="RACHA", fill="#AAB8C5", font=self._font(8), tags=("ui_elem",)
        )
        if streak.state == StreakState.FURY:
            st_txt, st_col = "FURIA 2X", "#FF9A8B"
        elif streak.state == StreakState.LUCKY:
            st_txt, st_col = "+15% SUERTE", "#FFE57F"
        else:
            st_txt, st_col = f"W:{streak.consecutive_wins} | L:{streak.consecutive_losses}", "#F3B958"
        self.canvas.create_text(
            212, 386, text=st_txt, fill=st_col, font=self._font(10), tags=("ui_elem",)
        )

        # 5. Caja de Banca ($)
        self._draw_rounded_rect(22, 418, 272, 484, radius=14, fill="#141B22", outline="#0E1318", width=3)
        self.canvas.create_text(
            147, 432, text="BANCA DISPONIBLE", fill="#AAB8C5", font=self._font(8), tags=("ui_elem",)
        )
        self._animate_bankroll_towards(econ.bankroll)
        self._bankroll_text_item = self.canvas.create_text(
            147, 459, text=f"${self.shown_bankroll}", fill="#F3B958",
            font=self._font(22), tags=("ui_elem",)
        )

        # 6. Botones del Sidebar (incluyendo Colección y Pantalla Completa)
        self._draw_pill_button(
            147, 512, 244, 36, "COBRAR Y TIENDA", BALATRO_TEAL, "btn_hud_shop",
            self._on_finish_level_early, font_size=11
        )

        self._draw_pill_button(
            147, 554, 244, 34, "COLECCION DE CARTAS", BALATRO_BLUE, "btn_hud_rules",
            self._open_collection_menu, font_size=10
        )

        mus_txt = "MUSICA: ON" if self.audio.music_playing else "MUSICA: OFF"
        self._draw_pill_button(
            147, 594, 244, 34, mus_txt, BALATRO_ORANGE, "btn_hud_music",
            self._on_toggle_music, font_size=10
        )

        fs_txt = "MODO VENTANA (F11)" if self.is_fullscreen else "PANTALLA COMPLETA (F11)"
        self._draw_pill_button(
            147, 634, 244, 34, fs_txt, BALATRO_PURPLE, "btn_hud_fs",
            self.toggle_fullscreen, font_size=9
        )

    @staticmethod
    def _fmt_mult(value: float) -> str:
        return f"{value:g}" if value == int(value) else f"{value:.2f}".rstrip("0")

    def _score_box_values(self) -> Tuple[str, int, float]:
        """Qué muestra la caja FICHAS x MULTI: la mejor jugada posible antes de girar, o el último giro."""
        if self.engine.board.bets:
            hand = self.engine.current_hand()
            preview = self.engine.preview_best_score()
            name = HAND_CATALOG[hand].name
            level = self.engine.hands.get_level(hand)
            level_txt = f" NV.{level}" if HAND_CATALOG[hand].upgradable else ""
            if preview is None:
                return f"MANO: {name}{level_txt}", 0, 1.0
            return f"MANO: {name}{level_txt}  -  SI ACIERTAS", preview.chips, preview.total_mult
        if self.last_score is not None:
            sc = self.last_score
            hits_txt = "1 ACIERTO" if sc.hits == 1 else f"{sc.hits} ACIERTOS"
            return f"{HAND_CATALOG[sc.hand].name}  -  {hits_txt}", sc.chips, sc.total_mult
        return "ARMA TU MANO EN EL TAPETE (?)", 0, 1.0

    def _show_hands_guide(self) -> None:
        if self._modal is not None or self.is_spinning:
            return
        lines = []
        for hand, info in HAND_CATALOG.items():
            if hand == HandType.SIMPLE:
                lines.append(f"SIMPLE: {info.rule}")
                continue
            if hand == HandType.COMBO_9:
                continue
            value = self._fmt_mult(self.engine.hands.mult_value(hand))
            bonus = f"+{value} multi por acierto" if info.per_hit else f"multi x{value} al acertar"
            lines.append(f"{info.name} (Nv.{self.engine.hands.get_level(hand)}): {info.rule}  ->  {bonus}")
        self._show_dialog(
            "MANOS DE RULETA",
            "La forma en que combinas tus apuestas forma una MANO.\n"
            "GANANCIA = FICHAS x MULTI. Cada apuesta acertada a la vez sube el MULTI.\n\n"
            + "\n\n".join(lines)
            + "\n\nMejora tus manos en la Tienda. Modo Furia y Comodines multiplican el MULTI.",
            "info",
            button_text="ENTENDIDO",
        )

    def _animate_score_count(self, score: ScoreBreakdown) -> None:
        """Estilo Balatro: las FICHAS suben desde 0 y luego el MULTI 'golpea'."""
        def chips_frame(t: float) -> None:
            if self._chips_text_item is not None:
                self.canvas.itemconfig(self._chips_text_item, text=str(round(score.chips * ease_out_cubic(t))))

        def mult_hit() -> None:
            if score.total_mult > 1:
                self.audio.play("mult_ding")

            def mult_frame(t: float) -> None:
                if self._mult_text_item is not None:
                    size = 16 + 9 * (1.0 - ease_out_cubic(t))
                    self.canvas.itemconfig(self._mult_text_item, font=self._font(size))

            self._start_anim("score_mult", 0.35, mult_frame)

        if self._chips_text_item is not None:
            self.canvas.itemconfig(self._chips_text_item, text="0")
        self._start_anim("score_chips", 0.55, chips_frame, mult_hit)

    def _draw_top_cards_zone(self) -> None:
        self._draw_rounded_rect(296, 10, 892, 144, radius=16, fill="#143325", outline="#0D2118", width=3)
        jokers = self.engine.jokers.jokers
        self.canvas.create_text(
            312, 23, anchor="w",
            text=f"COMODINES ({len(jokers)}/{self.engine.jokers.max_slots}) - CLIC PARA VENDER",
            fill="#F3B958", font=self._font(9), tags=("ui_elem",)
        )

        for idx in range(self.engine.jokers.max_slots):
            x = 312 + idx * 114
            y = 34
            if idx < len(jokers):
                jk = jokers[idx]
                selected = self.selected_card == ("joker", idx)
                value = self.engine.jokers.sell_value(idx)
                tag = f"joker_slot_{idx}"
                self.visuals.draw_card(
                    x, y - (6 if selected else 0),
                    title=jk.name,
                    subtitle=jk.description,
                    badge_text="JOKER",
                    accent_rgb=BALATRO_RED,
                    symbol_key=jk.joker_type.value,
                    width=106,
                    height=102,
                    highlighted=selected,
                    tags=("ui_elem", tag),
                )
                self.canvas.tag_bind(tag, "<Button-1>", lambda _e, i=idx: self._toggle_card_selection("joker", i))
                self._bind_card_hover_inspector(
                    tag, f"COMODIN: {jk.name}", f"{jk.description}\nValor de venta: ${value}"
                )
                if selected:
                    self._draw_pill_button(
                        x + 53, y + 80, 96, 26, f"VENDER ${value}", BALATRO_GOLD, f"sell_joker_{idx}",
                        lambda i=idx: self._on_sell_card("joker", i), font_size=10, fg_rgb=(20, 24, 30)
                    )
                else:
                    self._register_hover(tag, x, y, x + 106, y + 102, lift=6)
            else:
                self._draw_rounded_rect(
                    x, y + 2, x + 100, y + 98, radius=10, fill="#10291E", outline="#24523D", width=2, shadow=False
                )
                self.canvas.create_text(
                    x + 50, y + 50, text="JOKER\nVACIO", fill="#2A6148",
                    font=self._font(9), justify=tk.CENTER, tags=("ui_elem",)
                )

        self._draw_rounded_rect(904, 10, 1256, 144, radius=16, fill="#143325", outline="#0D2118", width=3)
        tarots = self.engine.tarots.hand
        self.canvas.create_text(
            920, 23, anchor="w",
            text=f"TAROT ({len(tarots)}/{self.engine.tarots.max_hand_size}) - CLIC PARA USAR O VENDER",
            fill="#D69EFF", font=self._font(9), tags=("ui_elem",)
        )

        for idx in range(self.engine.tarots.max_hand_size):
            x = 920 + idx * 110
            y = 34
            if idx < len(tarots):
                tr = tarots[idx]
                selected = self.selected_card == ("tarot", idx)
                value = tr.cost // 2
                tag = f"tarot_slot_{idx}"
                self.visuals.draw_card(
                    x, y - (6 if selected else 0),
                    title=tr.name,
                    subtitle=tr.description,
                    badge_text="TAROT",
                    accent_rgb=BALATRO_PURPLE,
                    symbol_key=tr.tarot_type.value,
                    width=104,
                    height=102,
                    highlighted=True,
                    tags=("ui_elem", tag),
                )
                self.canvas.tag_bind(tag, "<Button-1>", lambda _e, i=idx: self._toggle_card_selection("tarot", i))
                self._bind_card_hover_inspector(
                    tag, f"TAROT: {tr.name}", f"{tr.description}\nValor de venta: ${value}"
                )
                if selected:
                    self._draw_pill_button(
                        x + 52, y + 50, 90, 26, "USAR", BALATRO_TEAL, f"use_tarot_{idx}",
                        lambda i=idx: self._on_use_selected_tarot(i), font_size=10
                    )
                    self._draw_pill_button(
                        x + 52, y + 80, 90, 26, f"VENDER ${value}", BALATRO_GOLD, f"sell_tarot_{idx}",
                        lambda i=idx: self._on_sell_card("tarot", i), font_size=10, fg_rgb=(20, 24, 30)
                    )
                else:
                    self._register_hover(tag, x, y, x + 104, y + 102, lift=6)
            else:
                self._draw_rounded_rect(
                    x, y + 2, x + 98, y + 98, radius=10, fill="#10291E", outline="#24523D", width=2, shadow=False
                )
                self.canvas.create_text(
                    x + 49, y + 50, text="TAROT\nVACIO", fill="#2A6148",
                    font=self._font(9), justify=tk.CENTER, tags=("ui_elem",)
                )

    def _toggle_card_selection(self, kind: str, index: int) -> None:
        if self.is_spinning or self._modal is not None:
            return
        self.selected_card = None if self.selected_card == (kind, index) else (kind, index)
        self.canvas.delete("hover_card_popup")
        self.audio.play("chip")
        self._redraw()

    def _on_sell_card(self, kind: str, index: int) -> None:
        if self.is_spinning:
            return
        try:
            if kind == "joker":
                name = self.engine.jokers.jokers[index].name
                value = self.engine.sell_joker(index)
            else:
                name = self.engine.tarots.hand[index].name
                value = self.engine.sell_tarot(index)
        except Exception as exc:
            self.audio.play("loss")
            self._show_warning("No se pudo vender", str(exc))
            return
        self.selected_card = None
        self.audio.play("buy")
        self.floating_banner_text = f"VENDISTE {name.upper()}\n+${value}"
        self.ruletin_say(["Vender también es estrategia. Ojo: ese dinero no cuenta como ganancia de la ronda."],
                         key="first_sale")
        self.floating_banner_color = "#F3B958"
        self.canvas.delete("hover_card_popup")
        self._redraw()

    def _on_use_selected_tarot(self, index: int) -> None:
        self.selected_card = None
        self.canvas.delete("hover_card_popup")
        self._on_use_tarot_index(index)

    def _draw_middle_wheel_zone(self) -> None:
        self._draw_rounded_rect(296, 152, 1256, 404, radius=18, fill="#163829", outline="#0D2118", width=3)

        # Columna izquierda: solo las dos acciones principales
        self._draw_pill_button(
            455, 238, 272, 80, "GIRAR RULETA", BALATRO_RED, "btn_spin_wheel",
            self._on_spin_clicked, font_size=21
        )
        self._draw_pill_button(
            455, 334, 272, 52, "LIMPIAR APUESTAS", (58, 76, 94), "btn_clear_bets",
            self._on_clear_bets, font_size=13
        )

        # Centro: la ruleta
        wheel_cx, wheel_cy = 757, 278
        self._draw_rounded_rect(
            wheel_cx - 138, wheel_cy - 118, wheel_cx + 138, wheel_cy + 118,
            radius=22, fill="#10291E", outline="#F3B958", width=3
        )

        streak_state = self.engine.streaks.state
        self._wheel_view = self.visuals.create_wheel_view(
            self._result_wheel if self._result_wheel is not None else self.engine.wheel,
            wheel_cx, wheel_cy, self.wheel_angle, self.last_winning_number, streak_state, tags=("ui_elem",),
        )

        if self.floating_banner_text and not self.is_spinning:
            n_lines = self.floating_banner_text.count("\n") + 1
            half_h = 10 + 9 * n_lines
            self._draw_rounded_rect(
                wheel_cx - 125, wheel_cy - half_h, wheel_cx + 125, wheel_cy + half_h,
                radius=10, fill="#141B22", outline=self.floating_banner_color, width=3, tags=("banner",)
            )
            self.canvas.create_text(
                wheel_cx, wheel_cy, text=self.floating_banner_text,
                fill=self.floating_banner_color, font=self._font(11),
                justify=tk.CENTER, tags=("ui_elem", "banner")
            )
            self._banner_items = True

        # Derecha: la meta de la ciega en grande
        self._draw_quota_goal_panel()

    def _draw_quota_goal_panel(self) -> None:
        econ = self.engine.economy
        quota = econ.current_quota
        met = econ.can_pay_quota()
        x1, y1, x2, y2 = 914, 162, 1244, 394
        cx = (x1 + x2) // 2
        self._draw_rounded_rect(
            x1, y1, x2, y2, radius=14, fill="#1D2630", outline="#2BB673" if met else "#F3B958", width=3
        )
        self.canvas.create_text(
            cx, 186, text="META DE LA CIEGA", fill="#F3B958", font=self._font(13), tags=("ui_elem",)
        )
        self.canvas.create_text(
            cx, 236, text=f"${quota.target_quota}", fill="#2BB673" if met else "#FE5F55",
            font=self._font(44), tags=("ui_elem",)
        )
        self.canvas.create_text(
            cx, 282, text=f"TU BANCA: ${econ.bankroll}", fill="#FFFFFF", font=self._font(12), tags=("ui_elem",)
        )

        bar_x1, bar_x2, bar_y1, bar_y2 = x1 + 22, x2 - 22, 298, 318
        self._draw_rounded_rect(
            bar_x1, bar_y1, bar_x2, bar_y2, radius=9, fill="#0B0F14", outline="#2C3A47", width=1, shadow=False
        )
        ratio = min(1.0, econ.bankroll / max(1, quota.target_quota))
        if ratio > 0:
            fill_w = max(18, int((bar_x2 - bar_x1) * ratio))
            self._draw_rounded_rect(
                bar_x1, bar_y1, bar_x1 + fill_w, bar_y2, radius=9,
                fill="#2BB673" if ratio >= 1.0 else "#009DFF", outline="", width=0, shadow=False
            )

        prof_now = max(0, econ.level_profit)
        prof_req = quota.min_profit_required
        self.canvas.create_text(
            cx, 340, text="GANANCIA MINIMA EN ESTA RONDA", fill="#9FB1C1", font=self._font(9), tags=("ui_elem",)
        )
        self.canvas.create_text(
            cx, 366, text=f"+${prof_now} / +${prof_req}",
            fill="#2BB673" if econ.has_met_min_profit() else "#F3B958",
            font=self._font(17), tags=("ui_elem",)
        )

    def _draw_shop_screen(self) -> None:
        """Tienda a pantalla completa: solo aparece cuando se supera la ciega."""
        econ = self.engine.economy
        self._draw_rounded_rect(
            296, 152, 1256, self.HEIGHT - 10, radius=18, fill="#1D2630", outline="#F3B958", width=4
        )
        self.canvas.create_text(
            776, 186, text="TIENDA DEL CASINO", fill="#F3B958", font=self._font(24), tags=("ui_elem",)
        )
        self.canvas.create_text(
            776, 216,
            text=(
                f"CIEGA {econ.current_level} SUPERADA  -  Compra mejoras antes de la siguiente ronda"
                + (f"  -  {self.shop_note}" if self.shop_note else "")
            ),
            fill="#A8C5B8", font=self._font(11), tags=("ui_elem",)
        )

        color_by_kind = {
            ShopItemKind.JOKER: (BALATRO_RED, "JOKER"),
            ShopItemKind.TAROT: (BALATRO_PURPLE, "TAROT"),
            ShopItemKind.SPECIAL_CHIP: (BALATRO_BLUE, "FICHA"),
            ShopItemKind.BET_UPGRADE: (BALATRO_TEAL, "NIVEL+"),
            ShopItemKind.HAND_UPGRADE: (BALATRO_TEAL, "MANO+"),
        }
        offers = self.engine.shop.offers
        card_w, card_h, gap = 176, 196, 40
        if not offers:
            self.canvas.create_text(
                776, 380, text="YA COMPRASTE TODO LO DE ESTA TIENDA", fill="#8AA0B5",
                font=self._font(14), tags=("ui_elem",)
            )
        start_x = 776 - (len(offers) * card_w + (len(offers) - 1) * gap) / 2
        for idx, item in enumerate(offers):
            x = start_x + idx * (card_w + gap)
            y = 240
            affordable = econ.bankroll >= item.cost
            accent, badge = color_by_kind.get(item.kind, (BALATRO_GOLD, "ITEM"))
            sym_key = item.payload.value if hasattr(item.payload, "value") else str(item.payload)
            tag = f"shop_card_{idx}"
            self.visuals.draw_card(
                x, y,
                title=item.name,
                subtitle=item.description,
                badge_text=badge,
                accent_rgb=accent,
                symbol_key=sym_key,
                price=item.cost,
                width=card_w,
                height=card_h,
                highlighted=affordable,
                tags=("ui_elem", tag),
            )
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, i=idx: self._on_buy_shop_index(i))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))
            self._register_hover(tag, x, y, x + card_w, y + card_h, lift=8)

            self.canvas.create_text(
                x + card_w / 2, y + card_h + 12, text=item.description, fill="#E6EEF5", anchor="n",
                font=self._font(9), width=card_w + 20, justify=tk.CENTER, tags=("ui_elem",)
            )
            self._draw_pill_button(
                x + card_w / 2, y + card_h + 84, 164, 40, f"COMPRAR ${item.cost}",
                BALATRO_TEAL if affordable else (70, 80, 92), f"shop_buy_{idx}",
                lambda i=idx: self._on_buy_shop_index(i), font_size=12
            )

        self._draw_pill_button(
            776, 626, 340, 56, "SIGUIENTE RONDA", BALATRO_GOLD, "btn_next_lvl",
            self._on_next_level, font_size=17, fg_rgb=(20, 24, 30)
        )

    def _draw_bottom_betting_mat_zone(self) -> None:
        self._draw_rounded_rect(296, 412, 1256, self.HEIGHT - 10, radius=18, fill="#143325", outline="#0D2118", width=3)

        self.canvas.create_text(
            316, 439, anchor="w", text="ELIGE FICHA:", fill="#F3B958",
            font=self._font(11), tags=("ui_elem",)
        )

        chip_colors = {
            5: (254, 95, 85),
            10: (0, 157, 255),
            25: (43, 182, 115),
            50: (243, 185, 88),
            100: (155, 89, 182),
            -1: (210, 35, 45),
        }
        for idx, (denom, rgb) in enumerate(chip_colors.items()):
            cx = 434 + idx * 54
            is_sel = denom == self.selected_chip_amount
            cy = 436 if is_sel else 440
            if is_sel:
                self.visuals._circle(cx, cy, 25, "#F3B958", tags=("ui_elem",))
            tag = f"select_chip_{denom}"
            self.visuals.draw_chip(cx, cy, denom, rgb, size=42, selected=is_sel, tags=("ui_elem", tag))
            self._register_hover(tag, cx - 22, cy - 22, cx + 22, cy + 22, lift=4)
            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, d=denom: self._select_chip_denom(d))
            self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))

        spec_opts: List[Tuple[Optional[SpecialChipType], str, Tuple[int, int, int]]] = [
            (None, "Ficha Normal", (52, 73, 94)),
            (SpecialChipType.TRIPLE_PAYOUT, f"Triple x3 ({self.engine.special_chips.get_count(SpecialChipType.TRIPLE_PAYOUT)})", BALATRO_ORANGE),
            (SpecialChipType.ZERO_SHIELD, f"Escudo 0 ({self.engine.special_chips.get_count(SpecialChipType.ZERO_SHIELD)})", BALATRO_TEAL),
            (SpecialChipType.MAGNET_CHIP, f"Iman +30 ({self.engine.special_chips.get_count(SpecialChipType.MAGNET_CHIP)})", BALATRO_PURPLE),
        ]
        for idx, (sc_type, label, rgb) in enumerate(spec_opts):
            px = 820 + idx * 112
            py = 439
            is_active = sc_type == self.selected_special_chip
            btn_rgb = BALATRO_GOLD if is_active else rgb
            fg_col = (20, 24, 30) if is_active else (255, 255, 255)
            self._draw_pill_button(
                px, py, 106, 28, label, btn_rgb, f"spec_chip_{idx}",
                lambda s=sc_type: self._select_special_chip(s), font_size=9, fg_rgb=fg_col
            )

        active_bets_map: Dict[Tuple[BetType, BetTarget], Bet] = {
            (b.bet_type, b.target): b for b in self.engine.board.bets
        }

        mat_x0 = 312
        mat_y0 = 466
        zero_w = 52
        cell_w = 63
        cell_h = 39
        gap = 3

        zero_h = cell_h * 3 + gap * 2
        if self.engine.wheel.has_pocket(0):
            self._draw_betting_cell(
                x1=mat_x0,
                y1=mat_y0,
                x2=mat_x0 + zero_w,
                y2=mat_y0 + zero_h,
                label="0",
                fill_hex="#2BB673",
                bet_type=BetType.STRAIGHT,
                target=0,
                active_bet=active_bets_map.get((BetType.STRAIGHT, 0)),
                tag="cell_straight_0",
            )
        else:
            self._draw_rounded_rect(
                mat_x0, mat_y0, mat_x0 + zero_w, mat_y0 + zero_h,
                radius=10, fill="#2A323B", outline="#14191F", width=2
            )
            self.canvas.create_text(
                mat_x0 + zero_w // 2, mat_y0 + zero_h // 2, text="X",
                fill="#778899", font=self._font(14), tags=("ui_elem",)
            )

        grid_start_x = mat_x0 + zero_w + gap
        for num in range(1, 37):
            col_idx = (num - 1) // 3
            row_idx = 2 - ((num - 1) % 3)
            cx1 = grid_start_x + col_idx * (cell_w + gap)
            cy1 = mat_y0 + row_idx * (cell_h + gap)
            pocket = self.engine.wheel.get_pocket(num)
            fill_hex = "#E63E34" if pocket.color == Color.RED else "#242E3A"

            self._draw_betting_cell(
                x1=cx1,
                y1=cy1,
                x2=cx1 + cell_w,
                y2=cy1 + cell_h,
                label=str(num),
                fill_hex=fill_hex,
                bet_type=BetType.STRAIGHT,
                target=num,
                active_bet=active_bets_map.get((BetType.STRAIGHT, num)),
                tag=f"cell_straight_{num}",
            )

        col_x1 = grid_start_x + 12 * (cell_w + gap)
        col_w = 70
        for c_idx in (1, 2, 3):
            r_idx = 3 - c_idx
            cy1 = mat_y0 + r_idx * (cell_h + gap)
            self._draw_betting_cell(
                x1=col_x1,
                y1=cy1,
                x2=col_x1 + col_w,
                y2=cy1 + cell_h,
                label=f"COL {c_idx}",
                fill_hex="#1E5E44",
                bet_type=BetType.COLUMN,
                target=c_idx,
                active_bet=active_bets_map.get((BetType.COLUMN, c_idx)),
                tag=f"cell_col_{c_idx}",
            )

        row_dozens_y = mat_y0 + zero_h + gap + 1
        row_h = 33
        dozen_w = 4 * (cell_w + gap) - gap

        for d_idx, d_label in (
            (1, "1ra DOCENA (1-12)"),
            (2, "2da DOCENA (13-24)"),
            (3, "3ra DOCENA (25-36)"),
        ):
            dx1 = grid_start_x + (d_idx - 1) * 4 * (cell_w + gap)
            self._draw_betting_cell(
                x1=dx1,
                y1=row_dozens_y,
                x2=dx1 + dozen_w,
                y2=row_dozens_y + row_h,
                label=d_label,
                fill_hex="#1E5E44",
                bet_type=BetType.DOZEN,
                target=d_idx,
                active_bet=active_bets_map.get((BetType.DOZEN, d_idx)),
                tag=f"cell_dozen_{d_idx}",
            )

        row_ext_y = row_dozens_y + row_h + gap + 1
        ext_w = 3 * (cell_w + gap) - gap
        ext_defs: List[Tuple[str, BetType, BetTarget, str, str]] = [
            ("ROJO", BetType.COLOR, Color.RED, "#E63E34", "cell_color_red"),
            ("NEGRO", BetType.COLOR, Color.BLACK, "#242E3A", "cell_color_black"),
            ("PAR (EVEN)", BetType.PARITY, Parity.EVEN, "#0088DE", "cell_parity_even"),
            ("IMPAR (ODD)", BetType.PARITY, Parity.ODD, "#8E44AD", "cell_parity_odd"),
        ]
        for idx, (lbl, btype, target, hex_col, tag) in enumerate(ext_defs):
            ex1 = grid_start_x + idx * 3 * (cell_w + gap)
            self._draw_betting_cell(
                x1=ex1,
                y1=row_ext_y,
                x2=ex1 + ext_w,
                y2=row_ext_y + row_h,
                label=lbl,
                fill_hex=hex_col,
                bet_type=btype,
                target=target,
                active_bet=active_bets_map.get((btype, target)),
                tag=tag,
            )
        self._draw_table_limit_plaque()

    def _draw_table_limit_plaque(self) -> None:
        """Placa con el cupo de casillas y plenos de la mesa."""
        board = self.engine.board
        used_cells = board.active_bets_count
        max_cells = board.max_active_bets
        used_str = board.straight_bets_count
        max_str = board.max_straight_bets
        is_full = used_cells >= max_cells
        has_vip_bonus = board.bonus_active_bets > 0

        plaque_outline = "#FE5F55" if is_full else ("#2BB673" if has_vip_bonus else "#F3B958")
        count_color = "#FE5F55" if is_full else ("#4EE09B" if has_vip_bonus else "#FFFFFF")

        plaque_x1, plaque_x2 = 1159, 1229
        plaque_y1, plaque_y2 = 593, 663
        plaque_cx = (plaque_x1 + plaque_x2) // 2

        self._draw_rounded_rect(
            plaque_x1, plaque_y1, plaque_x2, plaque_y2,
            radius=9, fill="#121C24", outline=plaque_outline, width=2, tags=("table_limit_plaque",)
        )
        self.canvas.create_text(
            plaque_cx, plaque_y1 + 13, text="CASILLAS", fill="#F3B958",
            font=self._font(7), tags=("ui_elem", "table_limit_plaque"),
        )
        self.canvas.create_text(
            plaque_cx, plaque_y1 + 35, text=f"{used_cells} / {max_cells}", fill=count_color,
            font=self._font(12), tags=("ui_elem", "table_limit_plaque"),
        )
        self.canvas.create_text(
            plaque_cx, plaque_y1 + 56, text=f"PLENOS {used_str}/{max_str}",
            fill="#FE5F55" if used_str >= max_str else "#8AA0B5",
            font=self._font(6), tags=("ui_elem", "table_limit_plaque"),
        )
        self._bind_card_hover_inspector(
            "table_limit_plaque",
            f"REGLAS DE MESA ({used_cells}/{max_cells} CASILLAS)",
            f"Máx. {max_cells} casillas distintas y {max_str} Plenos por giro. Apuestas opuestas (Rojo/Negro, Par/Impar, >1 Docena o >1 Columna) se bloquean entre sí. Puedes apilar todas las fichas que quieras sobre tus casillas ya activas.",
        )

    def _draw_betting_cell(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        label: str,
        fill_hex: str,
        bet_type: BetType,
        target: BetTarget,
        active_bet: Optional[Bet],
        tag: str,
    ) -> None:
        boss = self.engine.bosses.active_boss
        boss_blocked = boss is not None and bet_type in boss.forbidden_bets
        opposing_blocked = (
            not boss_blocked
            and active_bet is None
            and self.engine.board.is_opposing_outside_blocked(bet_type, target)
        )
        cap_blocked = (
            not boss_blocked
            and not opposing_blocked
            and active_bet is None
            and self.engine.board.get_bet_restriction_reason(bet_type, target) is not None
        )
        is_blocked = boss_blocked or opposing_blocked

        if boss_blocked:
            fill_hex = "#1D232A"
            outline_col = "#6E2B2B"
            outline_w = 2
        elif opposing_blocked:
            fill_hex = "#1B222A"
            outline_col = "#6B4E2E"
            outline_w = 2
        elif cap_blocked:
            fill_hex = "#182028"
            outline_col = "#0E1419"
            outline_w = 2
        else:
            outline_col = "#F3B958" if active_bet is not None else "#10161C"
            outline_w = 3 if active_bet is not None else 2

        self._draw_rounded_rect(
            x1, y1, x2, y2, radius=9, fill=fill_hex, outline=outline_col,
            width=outline_w, shadow=True, tags=(tag,)
        )

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        cell_width = x2 - x1

        if is_blocked:
            txt_col = "#8A6868" if boss_blocked else "#A48B6E"
            if cell_width >= 110:
                blocked_txt = f"{label} (BLOQ.)"
            elif cell_width >= 68:
                blocked_txt = f"{label}\nBLOQ."
            else:
                blocked_txt = label
            self.canvas.create_text(
                cx, cy, text=blocked_txt, fill=txt_col, justify=tk.CENTER,
                font=self._font(8 if "\n" in blocked_txt else 9), tags=("ui_elem", tag)
            )
        elif cap_blocked:
            self.canvas.create_text(
                cx, cy, text=label, fill="#637485", font=self._font(10), tags=("ui_elem", tag)
            )
        elif active_bet is None:
            self.canvas.create_text(
                cx, cy, text=label, fill="#FFFFFF", font=self._font(11), tags=("ui_elem", tag)
            )
        else:
            self.canvas.create_text(
                x1 + 6, y1 + 4, anchor="nw", text=label, fill="#FFEFA6",
                font=self._font(8), tags=("ui_elem", tag)
            )
            chip_x = cx + 8 if cell_width <= 75 else cx + 38
            self.visuals.draw_bet_token(
                chip_x, cy + 2, active_bet.amount, active_bet.special_chip, size=32, tags=("ui_elem", tag)
            )

        self.canvas.tag_bind(tag, "<Button-1>", lambda _e, bt=bet_type, tg=target: self._place_bet_ui(bt, tg))
        if not is_blocked and not cap_blocked:
            self._register_hover(tag, x1, y1, x2, y2, lift=2)
        self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))

    # =========================================================================
    # Animaciones Fluidas a 60 FPS (Actualización In-Place sin borrar el Canvas)
    # =========================================================================
    def _animate_menu_loop(self) -> None:
        now = time.perf_counter()
        dt = min(0.1, now - self._menu_last_time)
        self._menu_last_time = now
        if (
            self.in_main_menu
            and not self.in_intro_screen
            and not self.in_collection_menu
            and self._wheel_view is not None
        ):
            # Giro continuo a velocidad constante (grados por segundo), independiente de los FPS
            self.menu_angle = (self.menu_angle + 40.0 * dt) % 360.0
            self._wheel_view.update(self.menu_angle)
            self.root.after(_frame_delay_ms(now), self._animate_menu_loop)
            return
        self.root.after(100, self._animate_menu_loop)

    def _on_spin_clicked(self) -> None:
        if self.is_spinning or self.engine.economy.phase != GamePhase.BETTING_AND_SPINNING:
            return
        if not self.engine.board.bets:
            self.audio.play("loss")
            self._show_info("Mesa vacia", "Coloca al menos una ficha en el tapete inferior antes de girar.")
            return

        self.last_winning_number = None
        self.floating_banner_text = ""
        self._result_wheel = None
        self.selected_card = None
        self._redraw()

        # El resultado se resuelve ANTES de animar para que la bola caiga en el número real.
        # Guardamos la rueda tal como estaba (los Tarots de 1 tiro se restauran al resolver).
        spin_wheel_view = copy.deepcopy(self.engine.wheel)
        decisive = self._is_decisive_last_spin()
        try:
            report = self.engine.spin_wheel()
        except Exception as exc:
            self._show_error("Error en giro", str(exc))
            self._redraw()
            return

        self._autosave()
        self.is_spinning = True
        self._result_wheel = spin_wheel_view
        self._spin_skip_allowed = decisive
        if decisive:
            self._start_dramatic_effects()
        streak_state = report.streak_report.state_before
        self._run_wheel_spin(
            key="game_spin",
            wheel=spin_wheel_view,
            start_angle=self.wheel_angle,
            total_rotation=540.0,
            duration=6.0,
            target_number=report.winning_pocket.number,
            center=(757, 278),
            streak_state=streak_state,
            set_angle=lambda a: setattr(self, "wheel_angle", a),
            on_done=lambda: self._on_spin_finished(report),
            shake=streak_state == StreakState.FURY,
            slow_tail=2.0 if decisive else 1.0,
        )

    def _on_spin_finished(self, report) -> None:
        self.is_spinning = False
        pocket = report.winning_pocket
        self.last_winning_number = pocket.number

        eval_res = report.streak_report.evaluation
        jk_res = report.joker_result
        score = report.score
        self.last_score = score
        self._spin_skip_allowed = False

        refund_txt = f"\nTE DEVOLVEMOS +${report.loss_refund}" if report.loss_refund > 0 else ""
        if report.chips_returned > 0:
            if report.easter_egg_triggered:
                self.floating_banner_text = f"COMBO SECRETO #9!\n+${report.chips_returned}!"
            else:
                self.floating_banner_text = f"#{pocket.number}  +${report.chips_returned}!"
            self.floating_banner_text += refund_txt
            self.floating_banner_color = "#F3B958"
        else:
            if report.loss_refund > 0:
                self.floating_banner_text = f"#{pocket.number} ({pocket.color.value})  SIN PREMIO{refund_txt}"
                self.floating_banner_color = "#F2812D"
            else:
                self.floating_banner_text = f"#{pocket.number} ({pocket.color.value})\nSIN PREMIO"
                self.floating_banner_color = "#FE5F55"

        if report.easter_egg_triggered:
            self.audio.play("lucky")
        elif jk_res.triggered_jokers:
            self.audio.play("joker")
        elif eval_res.any_bet_won:
            self.audio.play("mult_ding")

        if report.streak_report.state_after == StreakState.FURY:
            self.root.after(140, lambda: self.audio.play("fury"))
        elif report.streak_report.state_after == StreakState.LUCKY:
            self.root.after(140, lambda: self.audio.play("lucky"))
        elif eval_res.any_bet_won:
            self.root.after(140, lambda: self.audio.play("win"))
        else:
            self.audio.play("loss")

        if score is not None and score.gain > 0 and score.total_mult != 1:
            self.floating_banner_text += (
                f"\n{HAND_CATALOG[score.hand].name}: {score.chips} x {self._fmt_mult(score.total_mult)} = ${score.gain}"
            )
        if report.boss_report.punishment_triggered:
            self.floating_banner_text += "\nCASTIGO DEL JEFE"
        for joker in report.destroyed_jokers:
            self.floating_banner_text += f"\n{joker.name.upper()} SE DESTRUYO"
        self._redraw()
        if score is not None and score.chips > 0:
            self._animate_score_count(score)
        if score is not None and score.gain > 0 and score.total_mult >= 2 and report.level_evaluation is None:
            self.ruletin_say([random.choice((
                f"¡ESO! {HAND_CATALOG[score.hand].name} con multi x{self._fmt_mult(score.total_mult)}. ¡Así se juega!",
                "¡Mira ese MULTI! Me estás dejando sin trabajo.",
                "Combinación de manual. Hasta el jefe aplaudiría... bueno, no.",
            ))])
        elif report.destroyed_jokers:
            self.ruletin_say([f"Adiós, {report.destroyed_jokers[0].name}. Nada dura para siempre en el casino."])
        if report.boss_report.punishment_triggered and report.level_evaluation is None:
            self._show_warning("Castigo del Jefe", report.boss_report.message)
        if report.level_evaluation is not None:
            # Dejamos ver el resultado del giro antes de pasar a la tienda (o a la derrota)
            level_result = report.level_evaluation
            self.root.after(1600, lambda: self._handle_level_evaluation_popup(level_result.passed, level_result))

    # =========================================================================
    # Eventos de Usuario
    # =========================================================================
    def _show_main_menu(self) -> None:
        if self.is_spinning:
            return
        self._autosave()
        self.in_intro_screen = False
        self.in_main_menu = True
        self.in_collection_menu = False
        self._redraw(transition=True)

    def _is_game_screen(self) -> bool:
        return (
            self._in_game_session
            and not self.in_intro_screen
            and not self.in_main_menu
            and not self.in_collection_menu
            and not self.in_options_menu
        )

    def _schedule_autosave(self) -> None:
        """Guarda poco después del último cambio, para no escribir el disco en cada clic."""
        if self._autosave_job is not None:
            self.root.after_cancel(self._autosave_job)
        self._autosave_job = self.root.after(400, self._autosave)

    def _autosave(self) -> None:
        if self._autosave_job is not None:
            self.root.after_cancel(self._autosave_job)
            self._autosave_job = None
        if not self._in_game_session:
            return
        try:
            save_game(self._save_path, self.engine)
        except OSError:
            pass

    def _save_settings(self) -> None:
        save_settings(self._settings_path, {
            "tutorial_done": not self._tutorial_pending,
            "ruletin_enabled": self.ruletin_enabled,
            "music_enabled": self.audio.music_playing,
            "music_volume": self._music_volume,
            "effects_volume": self._effects_volume,
            "difficulty": self.difficulty.value,
            "ruletin_said": sorted(self._ruletin_said),
        })

    def _quit_game(self) -> None:
        self._autosave()
        self._save_settings()
        self.root.destroy()

    def _reset_table_ui(self) -> None:
        self.last_score = None
        self.selected_card = None
        self.last_winning_number = None
        self._result_wheel = None
        self.floating_banner_text = ""
        self.shown_bankroll = None
        self.shop_note = ""

    def _play_default(self) -> None:
        """JUGAR AHORA: continúa la partida guardada si existe; si no, empieza una nueva."""
        if read_save_summary(self._save_path) is not None:
            self._continue_game()
        else:
            self._new_game()

    def _continue_game(self) -> None:
        engine = load_game(self._save_path)
        if engine is None:
            self._new_game()
            self._show_warning(
                "No se pudo continuar",
                "La partida guardada está dañada o es de otra versión. Empezamos una partida nueva.",
            )
            return
        engine.difficulty = self.difficulty
        self.engine = engine
        self._reset_table_ui()
        self._start_game_from_menu()

    def _on_new_game_clicked(self) -> None:
        if read_save_summary(self._save_path) is None:
            self._new_game()
            return
        self._show_dialog(
            "¿EMPEZAR DE NUEVO?",
            "Ya tienes una partida guardada. Si empiezas una nueva, la guardada se pierde.",
            "warning",
            buttons=[
                ("SÍ, NUEVA PARTIDA", "#FE5F55", self._new_game),
                ("CANCELAR", "#34495E", None),
            ],
        )

    def _new_game(self) -> None:
        delete_save(self._save_path)
        self.engine = RoguelikeRouletteEngine(seed=self.seed, difficulty=self.difficulty)
        self._reset_table_ui()
        self._start_game_from_menu()

    def _start_game_from_menu(self) -> None:
        self._in_game_session = True
        self._cancel_anim("intro_spin")
        self.intro_spinning = False
        self.audio.play("win")
        self.in_intro_screen = False
        self.in_main_menu = False
        self.in_collection_menu = False
        self._redraw(transition=True)
        if self._tutorial_pending and self.ruletin_enabled:
            self.root.after(450, self._start_tutorial)

    def _select_chip_denom(self, denom: int) -> None:
        if self.is_spinning:
            return
        self.selected_chip_amount = denom
        self.audio.play("chip")
        self._redraw()

    def _select_special_chip(self, sc_type: Optional[SpecialChipType]) -> None:
        if self.is_spinning:
            return
        self.selected_special_chip = sc_type
        self.audio.play("chip")
        self._redraw()

    def _on_toggle_music(self) -> None:
        self.audio.toggle_ambient_music()
        self._save_settings()
        if not self.is_spinning:
            self._redraw()

    def _place_bet_ui(self, bet_type: BetType, target: BetTarget) -> None:
        if self.is_spinning or self.engine.economy.phase != GamePhase.BETTING_AND_SPINNING:
            return
        try:
            if self.selected_chip_amount == -1:
                wager_amount = self.engine.economy.bankroll
                if wager_amount <= 0:
                    self.audio.play("loss")
                    self._show_warning(
                        "Sin fichas para ALL IN",
                        "No te quedan fichas disponibles en la banca para hacer ALL IN.",
                    )
                    return
            else:
                wager_amount = self.selected_chip_amount

            self.engine.place_bet(
                bet_type=bet_type,
                target=target,
                amount=wager_amount,
                special_chip=self.selected_special_chip,
            )
            self.floating_banner_text = ""
            self.audio.play("chip")
            self._redraw()
        except Exception as exc:
            self.audio.play("loss")
            self._show_warning("Apuesta Bloqueada", str(exc))

    def _on_clear_bets(self) -> None:
        if self.is_spinning:
            return
        self.engine.clear_table_and_refund()
        self.floating_banner_text = ""
        self.audio.play("clear")
        self._redraw()

    def _on_use_tarot_index(self, index: int) -> None:
        if self.is_spinning:
            return
        try:
            card = self.engine.use_tarot_card(index)
            self._result_wheel = None
            self.selected_card = None
            self.audio.play("tarot")
            self.floating_banner_text = f"TAROT: {card.name.upper()}"
            self.floating_banner_color = "#D69EFF"
            self._redraw()
        except Exception as exc:
            self.audio.play("loss")
            self._show_warning("Tarot", str(exc))
            self._redraw()

    def _on_finish_level_early(self) -> None:
        if self.is_spinning or self.engine.economy.phase != GamePhase.BETTING_AND_SPINNING:
            return
        self.engine.clear_table_and_refund()
        econ = self.engine.economy
        quota = econ.current_quota
        if not econ.can_pay_quota():
            self.audio.play("loss")
            self._redraw()
            if econ.bankroll < quota.target_quota:
                self._show_warning(
                    "Cuota Incompleta",
                    f"Necesitas tener al menos ${quota.target_quota} en tu banca "
                    f"para pagar la entrada de esta Ciega (tienes ${econ.bankroll}).",
                )
            else:
                self._show_warning(
                    "Ganancia Minima Incompleta",
                    f"No puedes pasar de Ciega solo con dinero ahorrado de rondas anteriores.\n\n"
                    f"Debes generar al menos +${quota.min_profit_required} de ganancia neta "
                    f"jugando en este nivel.\n"
                    f"Ganancia actual en esta ronda: +${max(0, econ.level_profit)} / +${quota.min_profit_required}",
                )
            return
        res = self.engine.finish_current_level()
        self.audio.play("win")
        self._handle_level_evaluation_popup(res.passed, res)

    def _handle_level_evaluation_popup(self, passed: bool, result=None) -> None:
        self.selected_card = None
        if passed:
            bonus = getattr(result, "bonus_money", 0) if result is not None else 0
            self.shop_note = f"LINGOTE: +${bonus}" if bonus else ""
            if result is not None and getattr(result, "rescued", False):
                self.root.after(400, lambda: self._show_dialog(
                    "EL ANGEL DE LA GUARDA TE SALVO",
                    "No llegaste a la meta, pero estabas cerca (75% o mas).\n"
                    "Pasas a la tienda sin pagar la cuota. El Angel se fue con la ciega.",
                    "success",
                ))
            self.floating_banner_text = "CIEGA SUPERADA"
            self.floating_banner_color = "#2BB673"
            self.audio.play("buy")
            self._redraw(transition=True)
            self.ruletin_say(
                ["¡Bienvenido a mi tienda! Compra con cabeza... y si algo no te sirve, lo vendes."],
                key="first_shop",
            )
        else:
            self.audio.play("loss")
            self.ruletin_say([random.choice((
                "¡Ay! La casa siempre gana... esta vez. ¡A la próxima arma mejores manos!",
                "Uf, eso dolió. Respira hondo, que la ruleta no guarda rencor.",
                "Perder es parte del juego. ¡Ganar también! Vamos otra vez.",
            ))])
            self._show_error(
                "DERROTA - CIEGA NO SUPERADA",
                "Te quedaste sin giros antes de cubrir la cuota y la ganancia minima obligatoria de la ronda.\n\n"
                "Iniciando una nueva partida.",
            )
            self.engine = RoguelikeRouletteEngine(seed=self.seed, difficulty=self.difficulty)
            self.last_score = None
            self.selected_card = None
            self.last_winning_number = None
            self._result_wheel = None
            self.floating_banner_text = "NUEVA PARTIDA"
            self._redraw()

    def _on_buy_shop_index(self, index: int) -> None:
        if self.is_spinning:
            return
        try:
            item = self.engine.buy_shop_item(index)
            self.audio.play("buy")
            self.floating_banner_text = f"COMPRADO: {item.name.upper()}"
            self.floating_banner_color = "#2BB673"
            self._redraw()
        except Exception as exc:
            self.audio.play("loss")
            self._show_warning("Tienda Casino", str(exc))

    def _on_next_level(self) -> None:
        try:
            self.engine.start_next_level()
            self.selected_card = None
            self.last_winning_number = None
            self.last_score = None
            self._result_wheel = None
            self.floating_banner_text = f"NIVEL {self.engine.economy.current_level}"
            self.floating_banner_color = "#F3B958"
            self.audio.play("mult_ding")
            self._redraw(transition=True)
            boss = self.engine.bosses.active_boss
            if boss is not None:
                self.audio.play("fury")
                self.ruletin_say(["Uy... ese crupier jefe no juega limpio. ¡Lee bien su regla antes de apostar!"])
                self._show_error(
                    "CIEGA DE JEFE",
                    f"Nivel {self.engine.economy.current_level}: {boss.name}\n\n{boss.description}",
                )
        except Exception as exc:
            self._show_warning("Aviso", str(exc))

    def _schedule_timer_tick(self) -> None:
        if (
            not self.in_main_menu
            and not self.in_collection_menu
            and not self.is_spinning
            and self.engine.economy.phase == GamePhase.BETTING_AND_SPINNING
            and self.engine.bosses.active_boss is not None
            and self.engine.bosses.active_boss.time_limit_seconds is not None
        ):
            self._redraw()
        self.root.after(500, self._schedule_timer_tick)


def run_gui() -> None:
    _enable_precise_timers()
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    RoguelikeRouletteApp(root)
    # customtkinter restaura la ventana al oscurecer la barra de título: se maximiza después
    root.after(300, lambda: root.state("zoomed"))
    root.mainloop()
