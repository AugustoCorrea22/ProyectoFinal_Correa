"""
Fase 2: Sistema de Rachas (Furia y Suertudo)
Introduce la dinámica emocional y la alteración de reglas según los resultados consecutivos:
1. Contador de Victorias y Derrotas consecutivas (StreakTracker).
2. Modo Furia (FURY): Al alcanzar 3 derrotas seguidas, multiplica por 2x la ganancia
   del siguiente tiro acertado y reinicia el contador.
3. Modo Suertudo (LUCKY): Al alcanzar 3 victorias seguidas, altera el generador de la
   rueda para otorgar un +15% (+0.15) de probabilidad a las casillas con apuestas activas.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Set
from src.board import BettingBoard
from src.payout import PayoutCalculator, SpinEvaluation
from src.wheel import Pocket, RouletteWheel


class StreakState(str, Enum):
    NEUTRAL = "NEUTRAL"
    FURY = "FURY"      # Modo Furia (3+ derrotas seguidas)
    LUCKY = "LUCKY"    # Modo Suertudo (3+ victorias seguidas)


@dataclass(frozen=True)
class StreakTurnReport:
    """
    Registro en memoria del resultado de un giro con el estado de rachas antes y después,
    cumpliendo con la Priorización de Feedback Lógico.
    """
    evaluation: SpinEvaluation
    state_before: StreakState
    state_after: StreakState
    fury_consumed: bool
    lucky_applied: bool
    consecutive_wins: int
    consecutive_losses: int


class StreakTracker:
    """
    Rastreador autónomo de rachas de victorias y derrotas consecutivas.
    Gestiona las transiciones entre los estados NEUTRAL, FURY y LUCKY.
    """

    FURY_THRESHOLD: int = 3
    LUCKY_THRESHOLD: int = 3
    FURY_PROFIT_MULTIPLIER: int = 2
    LUCKY_PROBABILITY_BONUS: float = 0.15

    def __init__(self) -> None:
        self._consecutive_wins: int = 0
        self._consecutive_losses: int = 0
        self._state: StreakState = StreakState.NEUTRAL

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def state(self) -> StreakState:
        return self._state

    @property
    def is_fury_active(self) -> bool:
        return self._state == StreakState.FURY

    @property
    def is_lucky_active(self) -> bool:
        return self._state == StreakState.LUCKY

    def get_current_profit_multiplier(self) -> int:
        """
        Devuelve el multiplicador de ganancia para el tiro actual.
        Si el Modo Furia está activo, devuelve 2x; de lo contrario, 1x.
        """
        return self.FURY_PROFIT_MULTIPLIER if self.is_fury_active else 1

    def get_current_lucky_bonus(self) -> float:
        """
        Devuelve el bono de probabilidad (+0.15 en Modo Suertudo, 0.0 en otro caso)
        para las casillas con apuestas activas.
        """
        return self.LUCKY_PROBABILITY_BONUS if self.is_lucky_active else 0.0

    def record_outcome(self, won: bool) -> bool:
        """
        Registra el resultado de un tiro (won=True si acertó, won=False si perdió)
        y actualiza el estado de la racha.
        Devuelve True si en este tiro se consumió la bonificación 2x del Modo Furia.
        """
        if won:
            # Si estaba en Modo Furia y acierta el tiro, se consume el 2x y se reinicia el contador
            if self._state == StreakState.FURY:
                self.reset()
                return True

            self._consecutive_wins += 1
            self._consecutive_losses = 0
            if self._consecutive_wins >= self.LUCKY_THRESHOLD:
                self._state = StreakState.LUCKY
            else:
                self._state = StreakState.NEUTRAL
            return False

        # Caso de derrota
        self._consecutive_losses += 1
        self._consecutive_wins = 0
        if self._consecutive_losses >= self.FURY_THRESHOLD:
            self._state = StreakState.FURY
        else:
            self._state = StreakState.NEUTRAL
        return False

    def reset(self) -> None:
        """Reinicia todos los contadores de racha y regresa al estado NEUTRAL."""
        self._consecutive_wins = 0
        self._consecutive_losses = 0
        self._state = StreakState.NEUTRAL

    def execute_spin(
        self,
        wheel: RouletteWheel,
        board: BettingBoard,
        calculator: PayoutCalculator,
        forced_pocket: Optional[Pocket] = None,
    ) -> StreakTurnReport:
        """
        Ejecuta un giro integrando la rueda, el tablero, la calculadora de pagos
        y el estado actual de rachas (Suertudo / Furia).
        Permite inyectar forced_pocket para pruebas deterministas.
        """
        state_before = self._state
        lucky_bonus = self.get_current_lucky_bonus()
        profit_multiplier = self.get_current_profit_multiplier()

        covered_numbers: Set[int] = board.get_covered_numbers(wheel)
        lucky_applied = bool(lucky_bonus > 0.0 and covered_numbers)

        if forced_pocket is not None:
            winning_pocket = forced_pocket
        else:
            winning_pocket = wheel.spin(
                covered_numbers=covered_numbers if lucky_applied else None,
                lucky_bonus=lucky_bonus if lucky_applied else 0.0,
            )

        evaluation = calculator.evaluate_spin(
            winning_pocket=winning_pocket,
            board=board,
            profit_multiplier=profit_multiplier,
        )

        fury_consumed = self.record_outcome(evaluation.any_bet_won)

        return StreakTurnReport(
            evaluation=evaluation,
            state_before=state_before,
            state_after=self._state,
            fury_consumed=fury_consumed,
            lucky_applied=lucky_applied,
            consecutive_wins=self._consecutive_wins,
            consecutive_losses=self._consecutive_losses,
        )
