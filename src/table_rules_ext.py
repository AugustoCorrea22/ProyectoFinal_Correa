"""
Extensión limpia de Reglas de Mesa (Partes 1 y 2) para The_Roulete (build 2026-10-29):
1. Registra el Comodín 'Mesa VIP' (VIP_TABLE: +1 casilla máxima activa en mesa) en JokerType,
   JOKER_CATALOG, JokerManager, SHORT_CARD_EFFECTS, Tienda, Colección y Savegame.
2. Sincroniza RoguelikeRouletteEngine con BettingBoard (validación previa de opuestos y cupo 5/5).
3. Actualiza RoguelikeRouletteApp (_draw_bottom_betting_mat_zone y _draw_betting_cell) para
   mostrar la placa 'CASILLAS X/5 | PLENOS P/5' y sombrear apuestas opuestas o sin cupo.
"""

from typing import Optional
import tkinter as tk
from PIL import ImageTk


def _register_vip_table_joker() -> None:
    import src.modifiers as mod
    import src.visuals as vis

    JokerType = mod.JokerType
    if not hasattr(JokerType, "VIP_TABLE"):
        member = str.__new__(JokerType, "VIP_TABLE")
        member._name_ = "VIP_TABLE"
        member._value_ = "VIP_TABLE"
        JokerType._member_map_["VIP_TABLE"] = member
        JokerType._member_names_.append("VIP_TABLE")
        JokerType._value2member_map_["VIP_TABLE"] = member
        type.__setattr__(JokerType, "VIP_TABLE", member)

    vip_enum = JokerType.VIP_TABLE
    if vip_enum not in mod.JOKER_CATALOG:
        mod.JOKER_CATALOG[vip_enum] = mod.Joker(
            joker_type=vip_enum,
            name="Mesa VIP",
            description="Otorga +1 casilla máxima de apuesta activa en el tapete por giro.",
            cost=110,
        )

    vis.SHORT_CARD_EFFECTS["VIP_TABLE"] = ("MESA VIP", "+1 CASILLA", "EN LA MESA")

    def get_bonus_active_bets(self) -> int:
        return sum(1 for j in self._slots if j.joker_type == JokerType.VIP_TABLE)

    mod.JokerManager.get_bonus_active_bets = get_bonus_active_bets


def _patch_game_engine() -> None:
    from src.economy import GamePhase
    import src.game_engine as ge

    Engine = ge.RoguelikeRouletteEngine
    if getattr(Engine, "_table_rules_patched", False):
        return

    orig_init = Engine.__init__
    orig_place_bet = Engine.place_bet
    orig_spin_wheel = Engine.spin_wheel
    orig_buy_shop_item = Engine.buy_shop_item
    orig_sell_joker = getattr(Engine, "sell_joker", None)

    def sync_board_limits(self) -> None:
        bonus = self.jokers.get_bonus_active_bets() if hasattr(self.jokers, "get_bonus_active_bets") else 0
        self.board.set_bonus_active_bets(bonus)

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.sync_board_limits()

    def patched_place_bet(self, bet_type, target, amount, special_chip=None):
        if self.economy.phase != GamePhase.BETTING_AND_SPINNING:
            raise RuntimeError("No puedes colocar apuestas fuera de la fase de juego.")
        self.sync_board_limits()
        self.bosses.validate_bet_allowed(bet_type)
        self.board.validate_bet_restrictions(bet_type, target)
        return orig_place_bet(self, bet_type=bet_type, target=target, amount=amount, special_chip=special_chip)

    def patched_spin_wheel(self, forced_pocket=None):
        self.sync_board_limits()
        if forced_pocket is None and self.board.bets:
            if self.board.is_secret_8_combo_active() and self.wheel.has_pocket(8):
                forced_pocket = self.wheel.get_pocket(8)
            elif self.board.is_cursed_old_9_combo_active() and self.wheel.pockets:
                losing_pockets = [
                    p for p in self.wheel.pockets
                    if not any(bet.covers_pocket(p) for bet in self.board.bets)
                ]
                if losing_pockets:
                    forced_pocket = self.rng.choice(losing_pockets)
                else:
                    forced_pocket = min(
                        self.wheel.pockets,
                        key=lambda p: self.calculator.evaluate_spin(p, self.board).total_return,
                    )
        res = orig_spin_wheel(self, forced_pocket=forced_pocket)
        self.sync_board_limits()
        return res

    def patched_buy_shop_item(self, *args, **kwargs):
        item = orig_buy_shop_item(self, *args, **kwargs)
        self.sync_board_limits()
        return item

    Engine.sync_board_limits = sync_board_limits
    Engine.__init__ = patched_init
    Engine.place_bet = patched_place_bet
    Engine.spin_wheel = patched_spin_wheel
    Engine.buy_shop_item = patched_buy_shop_item

    if orig_sell_joker is not None:
        def patched_sell_joker(self, *args, **kwargs):
            paid = orig_sell_joker(self, *args, **kwargs)
            self.sync_board_limits()
            return paid
        Engine.sell_joker = patched_sell_joker

    Engine._table_rules_patched = True


def _patch_savegame() -> None:
    import src.savegame as sg
    from src.board import BettingBoard

    if getattr(sg, "_table_rules_patched", False):
        return

    orig_engine_from_dict = sg.engine_from_dict

    def patched_engine_from_dict(data):
        BettingBoard._global_suspend_restrictions = True
        try:
            eng = orig_engine_from_dict(data)
        finally:
            BettingBoard._global_suspend_restrictions = False
        if eng is not None and hasattr(eng, "sync_board_limits"):
            eng.sync_board_limits()
        return eng

    sg.engine_from_dict = patched_engine_from_dict
    sg._table_rules_patched = True


def _patch_audio_quality() -> None:
    import src.audio as audio_mod

    SM = audio_mod.SoundManager
    if getattr(SM, "_hq_audio_patched", False):
        return

    orig_sm_init = SM.__init__

    def patched_sm_init(self, assets_dir, enabled: bool = True):
        if enabled and getattr(audio_mod, "HAS_PYGAME", False):
            try:
                pg = audio_mod.pygame
                if not pg.mixer.get_init():
                    pg.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            except Exception:
                pass
        orig_sm_init(self, assets_dir, enabled=enabled)

    SM.__init__ = patched_sm_init
    SM._hq_audio_patched = True


def _patch_ui() -> None:
    import src.ui as ui_mod
    from src.board import Bet, BetTarget, BetType
    from src.music_jukebox import install_jukebox

    App = ui_mod.RoguelikeRouletteApp
    if getattr(App, "_table_rules_patched", False):
        return

    orig_app_init = App.__init__
    orig_draw_mat = App._draw_bottom_betting_mat_zone

    def patched_app_init(self, *args, **kwargs):
        orig_app_init(self, *args, **kwargs)
        install_jukebox(self)

    def patched_draw_bottom_betting_mat_zone(self) -> None:
        if hasattr(self.engine, "sync_board_limits"):
            self.engine.sync_board_limits()
        orig_draw_mat(self)

        # Placa de cupo de la mesa
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
            plaque_cx, plaque_y1 + 13,
            text="CASILLAS",
            fill="#F3B958",
            font=self._font(7),
            tags=("ui_elem", "table_limit_plaque"),
        )
        self.canvas.create_text(
            plaque_cx, plaque_y1 + 35,
            text=f"{used_cells} / {max_cells}",
            fill=count_color,
            font=self._font(12),
            tags=("ui_elem", "table_limit_plaque"),
        )
        self.canvas.create_text(
            plaque_cx, plaque_y1 + 56,
            text=f"PLENOS {used_str}/{max_str}",
            fill="#FE5F55" if used_str >= max_str else "#8AA0B5",
            font=self._font(6),
            tags=("ui_elem", "table_limit_plaque"),
        )
        self._bind_card_hover_inspector(
            "table_limit_plaque",
            f"REGLAS DE MESA ({used_cells}/{max_cells} CASILLAS)",
            f"Máx. {max_cells} casillas distintas y {max_str} Plenos por giro. Apuestas opuestas (Rojo/Negro, Par/Impar, >1 Docena o >1 Columna) se bloquean entre sí. Puedes apilar todas las fichas que quieras sobre tus casillas ya activas.",
        )

    def patched_draw_betting_cell(
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
            token_key = ("bet_token", active_bet.amount, active_bet.special_chip)
            if token_key not in self._photo_cache:
                token_pil = self.visuals.get_bet_chip_token(
                    amount=active_bet.amount,
                    special_chip=active_bet.special_chip,
                    size=32,
                    scale=self.ui_scale,
                )
                self._photo_cache[token_key] = ImageTk.PhotoImage(token_pil)
            token_photo = self._photo_cache[token_key]
            chip_x = cx + 8 if cell_width <= 75 else cx + 38
            self.canvas.create_image(chip_x, cy + 2, image=token_photo, tags=("ui_elem", tag))

        self.canvas.tag_bind(tag, "<Button-1>", lambda _e, bt=bet_type, tg=target: self._place_bet_ui(bt, tg))
        if not is_blocked and not cap_blocked:
            self._register_hover(tag, x1, y1, x2, y2, lift=2)
        self.canvas.tag_bind(tag, "<Enter>", lambda _e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind(tag, "<Leave>", lambda _e: self.canvas.config(cursor=""))

    App.__init__ = patched_app_init
    App._draw_bottom_betting_mat_zone = patched_draw_bottom_betting_mat_zone
    App._draw_betting_cell = patched_draw_betting_cell
    App._table_rules_patched = True


def install() -> None:
    _register_vip_table_joker()
    _patch_audio_quality()
    _patch_game_engine()
    _patch_savegame()
    _patch_ui()
