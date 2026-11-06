"""
Orquestador Lógico Integral (RoguelikeRouletteEngine)
Integra de forma desacoplada las Fases 1 a 5 en memoria antes de conectar la Fase 6 (UI):
- Fase 1: RouletteWheel, BettingBoard, PayoutCalculator
- Fase 2: StreakTracker (Modo Furia 2x y Modo Suertudo +15%)
- Fase 3: JokerManager, SpecialChipManager, TarotManager
- Fase 4: EconomyManager, BetUpgradeManager, ShopManager
- Fase 5: BossManager (Crupieres Jefe y Eventos de Castigo)
"""

from dataclasses import dataclass, field, replace
from enum import Enum
import random
from typing import List, Optional
from src.wheel import Pocket, RouletteWheel
from src.board import Bet, BetTarget, BetType, BettingBoard, SpecialChipType
from src.payout import PayoutCalculator
from src.streaks import StreakState, StreakTracker, StreakTurnReport
from src.modifiers import (
    Joker,
    JokerApplicationResult,
    JokerManager,
    JokerType,
    TarotContext,
    tarot_sell_value,
    SpecialChipManager,
    TarotCard,
    TarotManager,
)
from src.economy import (
    BetUpgradeManager,
    EconomyManager,
    GamePhase,
    LevelEvaluationResult,
    ShopItem,
    ShopManager,
)
from src.bosses import BossManager, BossPunishmentReport
from src.hands import HAND_CATALOG, HandLevelManager, HandType, ScoreBreakdown, best_case_preview, detect_hand, score_spin


class Difficulty(str, Enum):
    """
    FACIL: cada apuesta perdida devuelve la mitad de lo apostado.
    NORMAL: cada apuesta perdida se pierde completa (ruleta clásica).
    """
    EASY = "FACIL"
    NORMAL = "NORMAL"


LOSS_REFUND_RATIO = {
    Difficulty.EASY: 0.5,
    Difficulty.NORMAL: 0.0,
}


@dataclass(frozen=True)
class FullSpinReport:
    """
    Reporte completo en memoria de un giro ejecutado en el motor lógico.
    Contiene toda la telemetría lista para ser consumida por la interfaz (Fase 6).
    """
    winning_pocket: Pocket
    streak_report: StreakTurnReport
    joker_result: JokerApplicationResult
    boss_report: BossPunishmentReport
    chips_returned: int
    bankroll_after: int
    spins_remaining: int
    level_evaluation: Optional[LevelEvaluationResult]
    easter_egg_triggered: bool = False
    loss_refund: int = 0  # Fichas devueltas por apuestas perdidas (modo FACIL)
    score: Optional[ScoreBreakdown] = None  # Desglose FICHAS x MULTI del giro
    destroyed_jokers: List[Joker] = field(default_factory=list)  # Helado derretido, Ficha rota...


class RoguelikeRouletteEngine:
    """Controlador principal de la lógica pura del juego."""

    def __init__(self, seed: Optional[int] = None, difficulty: Difficulty = Difficulty.NORMAL) -> None:
        self.difficulty = difficulty
        self.rng = random.Random(seed)
        self.wheel = RouletteWheel(rng=self.rng)
        self.board = BettingBoard()
        self.calculator = PayoutCalculator()
        self.streaks = StreakTracker()
        self.jokers = JokerManager(max_slots=5)
        self.special_chips = SpecialChipManager()
        self.tarots = TarotManager(max_hand_size=3)
        self.upgrades = BetUpgradeManager()
        self.hands = HandLevelManager()
        self._pending_star_mult: float = 0.0  # +multi guardado por La Estrella
        self.economy = EconomyManager(starting_chips=120, base_quota=150, spins_per_level=5)
        self.shop = ShopManager(rng=self.rng)
        self.bosses = BossManager(boss_interval=3, rng=self.rng)

        # Configura si el nivel inicial tiene jefe (normalmente a partir del nivel 3)
        self.bosses.setup_level_boss(self.economy.current_level)
        self.sync_board_limits()

    def sync_board_limits(self) -> None:
        """El cupo de casillas de la mesa depende de los Comodines equipados (Mesa VIP)."""
        self.board.set_bonus_active_bets(self.jokers.get_bonus_active_bets())

    def _resolve_secret_combo(self) -> Optional[Pocket]:
        """Las combinaciones secretas deciden dónde cae la bola."""
        if not self.board.bets:
            return None
        if self.board.is_secret_8_combo_active() and self.wheel.has_pocket(8):
            return self.wheel.get_pocket(8)
        if self.board.is_cursed_old_9_combo_active() and self.wheel.pockets:
            losing_pockets = [
                p for p in self.wheel.pockets
                if not any(bet.covers_pocket(p) for bet in self.board.bets)
            ]
            if losing_pockets:
                return self.rng.choice(losing_pockets)
            return min(
                self.wheel.pockets,
                key=lambda p: self.calculator.evaluate_spin(p, self.board).total_return,
            )
        return None

    def place_bet(
        self,
        bet_type: BetType,
        target: BetTarget,
        amount: int,
        special_chip: Optional[SpecialChipType] = None,
    ) -> Bet:
        """
        Valida reglas de Jefe, descuenta fichas de la banca del jugador y registra
        la apuesta (con o sin Ficha Especial) en el tapete.
        """
        if self.economy.phase != GamePhase.BETTING_AND_SPINNING:
            raise RuntimeError("No puedes colocar apuestas fuera de la fase de juego.")

        self.sync_board_limits()
        self.bosses.validate_bet_allowed(bet_type)
        self.board.validate_bet_restrictions(bet_type, target)
        self.economy.deduct_chips(amount)

        try:
            if special_chip is not None:
                return self.special_chips.place_special_bet(
                    self.board, bet_type, target, amount, special_chip
                )
            return self.board.place_bet(bet_type, target, amount)
        except Exception:
            # Si la apuesta falla por validación, reembolsamos las fichas descontadas
            self.economy.add_chips(amount)
            raise

    def clear_table_and_refund(self) -> None:
        """Devuelve las apuestas actuales del tapete al bankroll antes de girar."""
        refund = self.board.total_wagered
        if refund > 0:
            self.economy.add_chips(refund)
        self.board.clear_bets()

    def use_tarot_card(self, hand_index: int) -> TarotCard:
        """Usa una Carta de Tarot de la mano para alterar la rueda o el tapete."""
        if self.economy.phase != GamePhase.BETTING_AND_SPINNING:
            raise RuntimeError("Solo puedes usar Cartas de Tarot antes de girar.")
        context = TarotContext(
            economy=self.economy,
            joker_mgr=self.jokers,
            hand_mgr=self.hands,
            rng=self.rng,
            on_star=self._add_star_mult,
        )
        return self.tarots.use_card(hand_index, self.wheel, self.board, context)

    def _add_star_mult(self, amount: float) -> None:
        self._pending_star_mult += amount

    @property
    def pending_star_mult(self) -> float:
        return self._pending_star_mult

    def _ensure_can_sell(self) -> None:
        if self.economy.phase not in (GamePhase.BETTING_AND_SPINNING, GamePhase.SHOP):
            raise RuntimeError("No puedes vender en este momento.")

    def sell_joker(self, index: int) -> int:
        """Vende un Comodín por la mitad de su costo (+bonos) y devuelve lo que pagó."""
        self._ensure_can_sell()
        value = self.jokers.sell_value(index)
        self.jokers.remove_joker(index)
        self.economy.add_external_chips(value)
        self.sync_board_limits()
        return value

    def sell_tarot(self, index: int) -> int:
        """Vende una carta de Tarot por la mitad de su costo y devuelve lo que pagó."""
        self._ensure_can_sell()
        card = self.tarots.sell_card(index)
        value = tarot_sell_value(card)
        self.economy.add_external_chips(value)
        return value

    def spin_wheel(self, forced_pocket: Optional[Pocket] = None) -> FullSpinReport:
        """
        Ejecuta el ciclo completo de un giro:
        1. Consume 1 giro del nivel actual.
        2. Aplica probabilidad de Modo Suertudo (+15%) si está activo.
        3. Calcula pagos base + Nivel de Apuesta + Fichas Especiales + Modo Furia (2x).
        4. Aplica Comodines pasivos (multiplicadores y fichas extra).
        5. Acredita el retorno total al bankroll del jugador.
        6. Ejecuta eventos de castigo del Crupier Jefe si aplica.
        7. Restaura modificaciones temporales de Tarot y limpia el tapete.
        8. Si se agotaron los giros del nivel, evalúa automáticamente el fin de nivel.
        """
        self.sync_board_limits()
        if forced_pocket is None:
            forced_pocket = self._resolve_secret_combo()

        if not self.board.bets:
            raise ValueError("Debes colocar al menos una apuesta en el tapete antes de girar.")

        self.economy.consume_spin()

        state_before = self.streaks.state
        lucky_bonus = self.streaks.get_current_lucky_bonus()
        fury_multiplier = self.streaks.get_current_profit_multiplier()

        covered_numbers = self.board.get_covered_numbers(self.wheel)
        lucky_applied = bool(lucky_bonus > 0.0 and covered_numbers)
        easter_egg_active = self.board.is_easter_egg_9_combo_active()

        if forced_pocket is not None:
            winning_pocket = forced_pocket
        elif easter_egg_active and self.wheel.has_pocket(9):
            winning_pocket = self.wheel.get_pocket(9)
        else:
            winning_pocket = self.wheel.spin(
                covered_numbers=covered_numbers if lucky_applied else None,
                lucky_bonus=lucky_bonus if lucky_applied else 0.0,
            )

        # La mano se lee ANTES de resolver (los Tarots pueden alterar la rueda)
        hand = detect_hand(self.board, self.wheel)

        # Pago base sin multiplicadores: Furia y Comodines entran como xMULTI en la puntuación
        base_eval = self.calculator.evaluate_spin(
            winning_pocket=winning_pocket,
            board=self.board,
            bet_level_bonuses=self.upgrades.get_all_bonuses(),
        )

        fury_consumed = self.streaks.record_outcome(base_eval.any_bet_won)
        streak_report = StreakTurnReport(
            evaluation=base_eval,
            state_before=state_before,
            state_after=self.streaks.state,
            fury_consumed=fury_consumed,
            lucky_applied=lucky_applied,
            consecutive_wins=self.streaks.consecutive_wins,
            consecutive_losses=self.streaks.consecutive_losses,
        )

        joker_result = self.jokers.apply_jokers(
            base_eval,
            hand_name=HAND_CATALOG[hand].name,
            bankroll=self.economy.bankroll,
            rng=self.rng,
        )
        star_mult = self._pending_star_mult if base_eval.any_bet_won else 0.0
        self._pending_star_mult = 0.0
        score = score_spin(
            base_eval,
            hand,
            self.hands,
            joker_flat_chips=joker_result.bonus_flat_chips,
            joker_x_mult=joker_result.joker_multiplier,
            fury_x_mult=fury_multiplier,
            joker_add_mult=joker_result.bonus_add_mult + star_mult,
        )
        if score.total_return > 0:
            self.economy.add_chips(score.total_return)

        # Reembolso por dificultad: solo apuestas que perdieron sin devolver nada (no el Escudo 0)
        refund_ratio = LOSS_REFUND_RATIO[self.difficulty]
        loss_refund = sum(
            int(result.bet.amount * refund_ratio)
            for result in base_eval.bet_results
            if not result.won and result.total_return == 0
        )
        if loss_refund > 0:
            self.economy.add_chips(loss_refund)

        boss_report = self.bosses.apply_post_spin_punishment(
            evaluation=base_eval,
            joker_mgr=self.jokers,
            economy=self.economy,
        )

        destroyed_jokers = self.jokers.after_spin(base_eval.any_bet_won, self.rng)

        # Limpieza post-giro
        self.tarots.cleanup_after_spin(self.wheel)
        self.board.clear_bets()

        level_eval: Optional[LevelEvaluationResult] = None
        if self.economy.spins_remaining == 0 or (
            self.economy.bankroll == 0 and not self.economy.can_pay_quota()
        ):
            level_eval = self.finish_current_level()

        self.sync_board_limits()
        return FullSpinReport(
            winning_pocket=winning_pocket,
            streak_report=streak_report,
            joker_result=joker_result,
            boss_report=boss_report,
            chips_returned=score.total_return,
            bankroll_after=self.economy.bankroll,
            spins_remaining=self.economy.spins_remaining,
            level_evaluation=level_eval,
            easter_egg_triggered=easter_egg_active,
            loss_refund=loss_refund,
            score=score,
            destroyed_jokers=destroyed_jokers,
        )

    def finish_current_level(self) -> LevelEvaluationResult:
        """Evalúa el cierre del nivel actual y prepara la tienda si el jugador aprueba."""
        result = self.economy.evaluate_end_of_level()
        if not result.passed and self.jokers.has_joker(JokerType.GUARDIAN_ANGEL):
            quota = self.economy.current_quota
            if self.economy.bankroll >= int(quota.target_quota * 0.75):
                self.jokers.remove_first(JokerType.GUARDIAN_ANGEL)
                result = self.economy.rescue_current_level()
        if result.passed:
            bonus_money = self.jokers.on_level_passed()
            if bonus_money > 0:
                self.economy.add_chips(bonus_money)
                result = replace(result, bonus_money=bonus_money, surplus_chips=self.economy.bankroll)
            self.shop.generate_offers()
        return result

    def buy_shop_item(self, offer_index: int) -> ShopItem:
        """Compra un objeto en la tienda durante la fase entre niveles."""
        item = self.shop.buy_item(
            offer_index=offer_index,
            economy=self.economy,
            joker_mgr=self.jokers,
            tarot_mgr=self.tarots,
            chip_mgr=self.special_chips,
            upgrade_mgr=self.upgrades,
            hand_mgr=self.hands,
        )
        self.sync_board_limits()
        return item

    def current_hand(self) -> HandType:
        """Mano que forman las apuestas actuales del tapete."""
        return detect_hand(self.board, self.wheel)

    def preview_best_score(self) -> Optional[ScoreBreakdown]:
        """Mejor FICHAS x MULTI posible con el tapete actual (sin contar Comodines)."""
        return best_case_preview(
            self.board,
            self.wheel,
            self.hands,
            self.calculator,
            bet_level_bonuses=self.upgrades.get_all_bonuses(),
            fury_x_mult=self.streaks.get_current_profit_multiplier(),
        )

    def start_next_level(self) -> None:
        """Avanza de la tienda al siguiente nivel y configura el Crupier Jefe si toca."""
        self.economy.advance_to_next_level(base_level_stipend=100)
        self.bosses.setup_level_boss(self.economy.current_level)
