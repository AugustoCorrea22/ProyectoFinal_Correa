"""
Manos de Ruleta y Puntuación FICHAS x MULTI (estilo Balatro)
La forma en que el jugador combina sus apuestas en el tapete forma una "mano":
- SIMPLE:   cualquier jugada sin combinación (pago clásico, multi 1).
- PAR:      exactamente 2 apuestas que comparten al menos un número.
- CERCO:    3 o 4 apuestas externas (color, paridad, docena, columna) con un número en común.
- ESCOLTA:  1 pleno + 2 a 4 apuestas externas que también cubren ese número.
- VECINOS:  3 a 5 plenos que están juntos en la rueda física.
- COMBO 9:  el combo secreto del 9 (9 + Rojo + Col 3 + Impar + 1ra Docena).

Puntuación de un giro:
    FICHAS = ganancia base de las apuestas acertadas + fichas extra de Comodines
    MULTI  = multi de la mano (+multi)  x  Modo Furia y Comodines (xmulti)
    GANANCIA = FICHAS x MULTI
En las manos combinadas, cada apuesta acertada a la vez suma multi; en VECINOS
el multi se aplica al acertar cualquiera de los plenos.
Las manos se mejoran en la Tienda (+0.25 multi por nivel).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set

from src.board import Bet, BetType, BettingBoard
from src.payout import SpinEvaluation
from src.wheel import EUROPEAN_WHEEL_SEQUENCE, RouletteWheel


class HandType(str, Enum):
    SIMPLE = "SIMPLE"
    PAR = "PAR"
    CERCO = "CERCO"
    ESCOLTA = "ESCOLTA"
    VECINOS = "VECINOS"
    COMBO_9 = "COMBO 9"


@dataclass(frozen=True)
class HandInfo:
    name: str
    rule: str             # Cómo se forma (para la guía del jugador)
    base_mult: float      # +multi por acierto extra (o multi fijo en VECINOS)
    per_hit: bool         # True: suma por cada acierto extra; False: multi fijo al acertar
    upgradable: bool


HAND_CATALOG: Dict[HandType, HandInfo] = {
    HandType.SIMPLE: HandInfo(
        "SIMPLE", "Cualquier jugada sin combinacion. Pago clasico.", 0.0, True, False
    ),
    HandType.PAR: HandInfo(
        "PAR", "2 apuestas que comparten al menos un numero.", 0.5, True, True
    ),
    HandType.CERCO: HandInfo(
        "CERCO", "3 o 4 apuestas externas (color, par/impar, docena, columna) con un numero en comun.",
        0.5, True, True,
    ),
    HandType.ESCOLTA: HandInfo(
        "ESCOLTA", "1 pleno + 2 a 4 apuestas externas que tambien cubren ese numero.", 0.5, True, True
    ),
    HandType.VECINOS: HandInfo(
        "VECINOS", "3 a 5 plenos que estan juntos en la rueda (no en el tapete).", 1.25, False, True
    ),
    HandType.COMBO_9: HandInfo(
        "COMBO 9", "El combo secreto: 9 + Rojo + Col 3 + Impar + 1ra Docena.", 1.0, True, False
    ),
}

HAND_LEVEL_STEP = 0.25
OUTSIDE_TYPES = {BetType.COLOR, BetType.PARITY, BetType.DOZEN, BetType.COLUMN}


class HandLevelManager:
    """Niveles de cada mano; cada nivel extra suma +0.25 al multi de esa mano."""

    def __init__(self) -> None:
        self._levels: Dict[HandType, int] = {hand: 1 for hand in HandType}

    def get_level(self, hand: HandType) -> int:
        return self._levels[hand]

    def upgrade(self, hand: HandType) -> int:
        if not HAND_CATALOG[hand].upgradable:
            raise ValueError(f"La mano {HAND_CATALOG[hand].name} no se puede mejorar.")
        self._levels[hand] += 1
        return self._levels[hand]

    def mult_value(self, hand: HandType) -> float:
        """Multi de la mano según su nivel (por acierto extra, o fijo en VECINOS)."""
        info = HAND_CATALOG[hand]
        if not info.upgradable:
            return info.base_mult
        return info.base_mult + HAND_LEVEL_STEP * (self._levels[hand] - 1)


def _covered(bet: Bet, wheel: RouletteWheel) -> Set[int]:
    return {p.number for p in wheel.pockets if bet.covers_pocket(p)}


def _are_wheel_neighbors(numbers: Sequence[int]) -> bool:
    """True si los números forman un tramo continuo en la rueda europea (circular)."""
    positions = sorted(EUROPEAN_WHEEL_SEQUENCE.index(n) for n in numbers)
    size = len(EUROPEAN_WHEEL_SEQUENCE)
    gaps = [(positions[(i + 1) % len(positions)] - positions[i]) % size for i in range(len(positions))]
    # Un tramo continuo tiene todos los saltos de 1 salvo uno (el que "da la vuelta")
    return sorted(gaps)[:-1] == [1] * (len(positions) - 1)


def detect_hand(board: BettingBoard, wheel: RouletteWheel) -> HandType:
    """Identifica la mano formada por las apuestas del tapete."""
    bets: List[Bet] = list(board.bets)
    if board.is_easter_egg_9_combo_active():
        return HandType.COMBO_9
    if len(bets) < 2:
        return HandType.SIMPLE

    straights = [b for b in bets if b.bet_type == BetType.STRAIGHT]
    outside = [b for b in bets if b.bet_type in OUTSIDE_TYPES]

    if len(straights) == len(bets) and 3 <= len(bets) <= 5:
        numbers = [int(b.target) for b in straights]
        if all(n in EUROPEAN_WHEEL_SEQUENCE for n in numbers) and _are_wheel_neighbors(numbers):
            return HandType.VECINOS
        return HandType.SIMPLE

    common = set.intersection(*(_covered(b, wheel) for b in bets))
    if not common:
        return HandType.SIMPLE
    if len(bets) == 2:
        return HandType.PAR
    if len(straights) == 0 and 3 <= len(outside) <= 4:
        return HandType.CERCO
    if len(straights) == 1 and 2 <= len(outside) <= 4:
        return HandType.ESCOLTA
    return HandType.SIMPLE


def hand_multiplier(hand: HandType, hits: int, levels: HandLevelManager) -> float:
    """+multi de la mano para una cantidad de apuestas acertadas a la vez."""
    if hits <= 0 or hand == HandType.SIMPLE:
        return 1.0
    info = HAND_CATALOG[hand]
    value = levels.mult_value(hand)
    if not info.per_hit:
        return value
    return 1.0 + value * (hits - 1)


@dataclass(frozen=True)
class ScoreBreakdown:
    """Desglose completo de un giro: lo que el HUD anima como FICHAS x MULTI."""
    hand: HandType
    hand_level: int
    hits: int
    chips: int            # Ganancia base de las apuestas + fichas extra de Comodines
    hand_mult: float      # +multi de la mano
    joker_add_mult: float  # +multi de Comodines y La Estrella
    x_mult: int           # xmulti de Modo Furia y Comodines
    total_mult: float
    gain: int             # chips x total_mult
    total_return: int     # Fichas apostadas que se recuperan + ganancia


def score_spin(
    evaluation: SpinEvaluation,
    hand: HandType,
    levels: HandLevelManager,
    joker_flat_chips: int = 0,
    joker_x_mult: int = 1,
    fury_x_mult: int = 1,
    joker_add_mult: float = 0.0,
) -> ScoreBreakdown:
    """Calcula FICHAS x MULTI de un giro ya evaluado por PayoutCalculator (sin multiplicadores)."""
    winners = [r for r in evaluation.bet_results if r.won]
    hits = len(winners)
    chips = sum(r.net_profit for r in winners) + joker_flat_chips
    hand_mult = hand_multiplier(hand, hits, levels)
    x_mult = joker_x_mult * fury_x_mult
    total_mult = (hand_mult + joker_add_mult) * x_mult
    gain = int(round(chips * total_mult))

    winning_stakes = sum(r.bet.amount for r in winners)
    shielded = sum(r.total_return for r in evaluation.bet_results if not r.won and r.total_return > 0)
    return ScoreBreakdown(
        hand=hand,
        hand_level=levels.get_level(hand),
        hits=hits,
        chips=chips,
        hand_mult=hand_mult,
        joker_add_mult=joker_add_mult,
        x_mult=x_mult,
        total_mult=total_mult,
        gain=gain,
        total_return=winning_stakes + shielded + gain,
    )


def best_case_preview(
    board: BettingBoard,
    wheel: RouletteWheel,
    levels: HandLevelManager,
    calculator,
    bet_level_bonuses: Optional[Dict[BetType, int]] = None,
    fury_x_mult: int = 1,
) -> Optional[ScoreBreakdown]:
    """Mejor resultado posible del tapete actual (sin Comodines), para mostrarlo antes de girar."""
    if not board.bets:
        return None
    hand = detect_hand(board, wheel)
    best: Optional[ScoreBreakdown] = None
    for pocket in wheel.pockets:
        evaluation = calculator.evaluate_spin(pocket, board, bet_level_bonuses=bet_level_bonuses)
        score = score_spin(evaluation, hand, levels, fury_x_mult=fury_x_mult)
        if best is None or score.gain > best.gain:
            best = score
    return best
