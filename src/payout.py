"""
Fase 1.3: Calculadora de Pagos Tradicionales (PayoutCalculator)
Compara el número ganador contra las apuestas colocadas en el tapete y
calcula el retorno de fichas base según la tabla de pagos de la ruleta europea:
- Pleno (STRAIGHT): paga 35 a 1 (retorno total = apuesta * 36)
- Docena (DOZEN):   paga 2 a 1  (retorno total = apuesta * 3)
- Columna (COLUMN): paga 2 a 1  (retorno total = apuesta * 3)
- Color (COLOR):    paga 1 a 1  (retorno total = apuesta * 2)
- Par/Impar (PARITY): paga 1 a 1 (retorno total = apuesta * 2)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from src.board import Bet, BetType, BettingBoard, SpecialChipType
from src.wheel import Pocket


TRADITIONAL_ODDS: Dict[BetType, int] = {
    BetType.STRAIGHT: 35,
    BetType.DOZEN: 2,
    BetType.COLUMN: 2,
    BetType.COLOR: 1,
    BetType.PARITY: 1,
}


@dataclass(frozen=True)
class BetResult:
    """Resultado individual de una apuesta evaluada contra la casilla ganadora."""
    bet: Bet
    won: bool
    odds: int          # Multiplicador de ganancia tradicional (ej. 35 en Pleno)
    net_profit: int    # Ganancia neta (+apuesta * odds si gana, -apuesta si pierde)
    total_return: int  # Fichas totales que regresan al jugador (apuesta + ganancia si gana, 0 si pierde)


@dataclass(frozen=True)
class SpinEvaluation:
    """Resumen completo de pagos tras evaluar un giro de la ruleta."""
    winning_pocket: Pocket
    bet_results: List[BetResult]
    total_wagered: int
    total_return: int
    net_profit: int
    profit_multiplier: int = 1

    @property
    def any_bet_won(self) -> bool:
        """Indica si al menos una de las apuestas colocadas acertó."""
        return any(result.won for result in self.bet_results)

    @property
    def is_net_win(self) -> bool:
        """Indica si el jugador obtuvo un retorno mayor a cero o acertó alguna apuesta."""
        return self.any_bet_won


class PayoutCalculator:
    """Calculadora desacoplada para evaluar pagos base de la ruleta."""

    @staticmethod
    def get_base_odds(bet_type: BetType) -> int:
        """Obtiene el multiplicador tradicional (X a 1) para un tipo de apuesta."""
        if bet_type not in TRADITIONAL_ODDS:
            raise ValueError(f"Tipo de apuesta sin pago definido: {bet_type}")
        return TRADITIONAL_ODDS[bet_type]

    def evaluate_spin(
        self,
        winning_pocket: Pocket,
        board: BettingBoard,
        profit_multiplier: int = 1,
        bet_level_bonuses: Optional[Dict[BetType, int]] = None,
    ) -> SpinEvaluation:
        """
        Compara la casilla ganadora contra todas las apuestas activas del tablero
        y calcula el retorno de fichas base y la ganancia neta.
        Soporta:
        - profit_multiplier (ej. 2x en Modo Furia o por Comodines)
        - Fichas Especiales por apuesta (TRIPLE_PAYOUT, ZERO_SHIELD, MAGNET_CHIP)
        - Bonos por nivel de apuesta (bet_level_bonuses)
        """
        if profit_multiplier < 1:
            raise ValueError("El multiplicador de ganancia debe ser al menos 1.")

        results: List[BetResult] = []
        total_wagered = 0
        total_return = 0
        easter_egg_all_win = board.is_easter_egg_9_combo_active()

        for bet in board.bets:
            base_odds = self.get_base_odds(bet.bet_type)
            level_bonus = (bet_level_bonuses or {}).get(bet.bet_type, 0)
            odds = base_odds + level_bonus

            won = True if easter_egg_all_win else bet.covers_pocket(winning_pocket)
            total_wagered += bet.amount

            if won:
                chip_multiplier = 3 if bet.special_chip == SpecialChipType.TRIPLE_PAYOUT else 1
                flat_chip_bonus = 30 if bet.special_chip == SpecialChipType.MAGNET_CHIP else 0
                profit = (bet.amount * odds * chip_multiplier + flat_chip_bonus) * profit_multiplier
                returned_chips = bet.amount + profit  # Recupera su ficha apostada + el premio
            else:
                if bet.special_chip == SpecialChipType.ZERO_SHIELD and winning_pocket.number == 0:
                    # Escudo contra el 0 verde: protege la apuesta y devuelve el monto apostado
                    profit = 0
                    returned_chips = bet.amount
                else:
                    profit = -bet.amount
                    returned_chips = 0

            total_return += returned_chips
            results.append(
                BetResult(
                    bet=bet,
                    won=won,
                    odds=odds,
                    net_profit=profit,
                    total_return=returned_chips,
                )
            )

        net_profit = total_return - total_wagered
        return SpinEvaluation(
            winning_pocket=winning_pocket,
            bet_results=results,
            total_wagered=total_wagered,
            total_return=total_return,
            net_profit=net_profit,
            profit_multiplier=profit_multiplier,
        )
