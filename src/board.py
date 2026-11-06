"""
Fase 1.2: Tablero de Apuestas (BettingBoard)
Permite registrar y validar apuestas en el tapete para las categorías base:
- Plenos (STRAIGHT): Número específico del 0 al 36
- Color (COLOR): RED o BLACK
- Par/Impar (PARITY): EVEN o ODD
- Docenas (DOZEN): 1 (1-12), 2 (13-24), 3 (25-36)
- Columnas (COLUMN): 1, 2, 3

Incluye las reglas de restricción de mesa estilo Balatro/Casino:
1. Exclusión mutua de apuestas externas opuestas:
   - ROJO bloquea NEGRO (y viceversa).
   - PAR bloquea IMPAR (y viceversa).
   - Máximo 1 Docena activa y máximo 1 Columna activa por giro.
2. Límite de casillas activas y Plenos por giro:
   - Máximo 5 casillas distintas activas en mesa por defecto (+bonos de Comodines como Mesa VIP).
   - Máximo 5 Plenos individuales por giro.
   - Apilar más fichas sobre una casilla ya activa siempre está permitido.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Union
from src.wheel import Color, Parity, Pocket, RouletteWheel


DEFAULT_MAX_ACTIVE_BETS: int = 5
DEFAULT_MAX_STRAIGHT_BETS: int = 5

_DOZEN_NAMES = {
    1: "1ra Docena (1-12)",
    2: "2da Docena (13-24)",
    3: "3ra Docena (25-36)",
}


class BetType(str, Enum):
    STRAIGHT = "STRAIGHT"  # Pleno (0-36)
    COLOR = "COLOR"        # Rojo / Negro
    PARITY = "PARITY"      # Par / Impar
    DOZEN = "DOZEN"        # Docena (1, 2, 3)
    COLUMN = "COLUMN"      # Columna (1, 2, 3)


class SpecialChipType(str, Enum):
    """Tipos de Fichas Especiales (Fase 3.2) que alteran el comportamiento de una apuesta."""
    TRIPLE_PAYOUT = "TRIPLE_PAYOUT"  # Triplica el pago (3x ganancia) de la apuesta si acierta
    ZERO_SHIELD = "ZERO_SHIELD"      # Protege contra el 0 verde (reembolsa la apuesta si sale 0)
    MAGNET_CHIP = "MAGNET_CHIP"      # Suma +30 fichas extra planas si la apuesta resulta ganadora


BetTarget = Union[int, Color, Parity, str]


@dataclass(frozen=True)
class Bet:
    """Representa una apuesta individual colocada sobre el tapete."""
    bet_type: BetType
    target: Union[int, Color, Parity]
    amount: int
    special_chip: Optional[SpecialChipType] = None

    def covers_pocket(self, pocket: Pocket) -> bool:
        """Determina si esta apuesta resulta ganadora para una casilla dada."""
        if self.bet_type == BetType.STRAIGHT:
            return pocket.number == self.target
        if self.bet_type == BetType.COLOR:
            return pocket.color == self.target
        if self.bet_type == BetType.PARITY:
            return pocket.parity is not None and pocket.parity == self.target
        if self.bet_type == BetType.DOZEN:
            return pocket.dozen is not None and pocket.dozen == self.target
        if self.bet_type == BetType.COLUMN:
            return pocket.column is not None and pocket.column == self.target
        return False


class BettingBoard:
    """
    Gestiona las apuestas activas en el tapete para la ronda actual.
    Valida montos, objetivos, exclusión de apuestas externas opuestas y cupo máximo de casillas.
    """

    # Permite restaurar una partida guardada sin volver a aplicar las reglas de mesa
    _global_suspend_restrictions: bool = False

    def __init__(
        self,
        max_active_bets: int = DEFAULT_MAX_ACTIVE_BETS,
        max_straight_bets: int = DEFAULT_MAX_STRAIGHT_BETS,
    ) -> None:
        if max_active_bets <= 0:
            raise ValueError("El máximo de casillas activas debe ser mayor a 0.")
        if max_straight_bets <= 0:
            raise ValueError("El máximo de plenos por giro debe ser mayor a 0.")
        self._bets: List[Bet] = []
        self._base_max_active_bets = max_active_bets
        self._bonus_active_bets = 0
        self._max_straight_bets = max_straight_bets

    @property
    def bets(self) -> List[Bet]:
        """Devuelve una copia de la lista de apuestas registradas."""
        return list(self._bets)

    @property
    def total_wagered(self) -> int:
        """Suma total de fichas apostadas actualmente en el tapete."""
        return sum(bet.amount for bet in self._bets)

    @property
    def active_bets_count(self) -> int:
        """Cantidad de casillas distintas con apuestas activas en el tapete."""
        return len(self._bets)

    @property
    def straight_bets_count(self) -> int:
        """Cantidad de números individuales (Plenos) apostados actualmente."""
        return sum(1 for bet in self._bets if bet.bet_type == BetType.STRAIGHT)

    @property
    def max_active_bets(self) -> int:
        """Cupo máximo total de casillas activas en mesa para el giro actual."""
        return max(1, self._base_max_active_bets + self._bonus_active_bets)

    @property
    def bonus_active_bets(self) -> int:
        """Casillas extra otorgadas por Comodines o mejoras (ej. Mesa VIP)."""
        return self._bonus_active_bets

    def set_bonus_active_bets(self, bonus: int) -> None:
        """Actualiza el bono dinámico de casillas máximas activas en mesa."""
        self._bonus_active_bets = max(0, int(bonus))

    @property
    def max_straight_bets(self) -> int:
        """Cupo máximo de números individuales (Plenos) permitidos por giro."""
        return self._max_straight_bets

    def has_bet(self, bet_type: BetType, target: BetTarget) -> bool:
        """Indica si la casilla dada ya tiene fichas colocadas en este giro."""
        normalized_target = self._validate_and_normalize_target(bet_type, target)
        return any(
            b.bet_type == bet_type and b.target == normalized_target
            for b in self._bets
        )

    def is_opposing_outside_blocked(self, bet_type: BetType, target: BetTarget) -> bool:
        """
        Devuelve True únicamente si la casilla está bloqueada por una apuesta externa
        opuesta/excluyente (Color opuesto, Paridad opuesta, otra Docena u otra Columna).
        """
        normalized_target = self._validate_and_normalize_target(bet_type, target)
        if any(b.bet_type == bet_type and b.target == normalized_target for b in self._bets):
            return False
        if bet_type in (BetType.COLOR, BetType.PARITY, BetType.DOZEN, BetType.COLUMN):
            return any(b.bet_type == bet_type and b.target != normalized_target for b in self._bets)
        return False

    def get_bet_restriction_reason(
        self,
        bet_type: BetType,
        target: BetTarget,
    ) -> Optional[str]:
        """
        Evalúa las reglas de restricción de mesa para `(bet_type, target)`.
        Devuelve `None` si la apuesta está permitida (incluyendo apilar más fichas sobre
        una casilla ya activa), o un mensaje explicativo si está bloqueada.
        """
        normalized_target = self._validate_and_normalize_target(bet_type, target)

        if self._global_suspend_restrictions:
            return None

        # Apilar fichas sobre una casilla ya activa siempre está permitido
        for existing in self._bets:
            if existing.bet_type == bet_type and existing.target == normalized_target:
                return None

        # Apuestas externas opuestas
        if bet_type == BetType.COLOR:
            for existing in self._bets:
                if existing.bet_type == BetType.COLOR and existing.target != normalized_target:
                    if existing.target == Color.RED:
                        return "Regla de mesa: no puedes apostar a NEGRO si ya tienes fichas en ROJO."
                    return "Regla de mesa: no puedes apostar a ROJO si ya tienes fichas en NEGRO."

        elif bet_type == BetType.PARITY:
            for existing in self._bets:
                if existing.bet_type == BetType.PARITY and existing.target != normalized_target:
                    if existing.target == Parity.EVEN:
                        return "Regla de mesa: no puedes apostar a IMPAR si ya tienes fichas en PAR."
                    return "Regla de mesa: no puedes apostar a PAR si ya tienes fichas en IMPAR."

        elif bet_type == BetType.DOZEN:
            for existing in self._bets:
                if existing.bet_type == BetType.DOZEN and existing.target != normalized_target:
                    chosen = _DOZEN_NAMES.get(int(existing.target), f"Docena {existing.target}")
                    return (
                        "Regla de mesa: solo puedes apostar a 1 Docena por giro (ya tienes fichas en "
                        f"{chosen})."
                    )

        elif bet_type == BetType.COLUMN:
            for existing in self._bets:
                if existing.bet_type == BetType.COLUMN and existing.target != normalized_target:
                    return (
                        "Regla de mesa: solo puedes apostar a 1 Columna por giro (ya tienes fichas en Columna "
                        f"{existing.target})."
                    )

        # Cupo de Plenos
        if bet_type == BetType.STRAIGHT and self.straight_bets_count >= self.max_straight_bets:
            return (
                f"Límite de Plenos alcanzado ({self.straight_bets_count}/{self.max_straight_bets} números individuales por giro). Apila más fichas en tus números ya elegidos."
            )

        # Cupo de casillas
        if self.active_bets_count >= self.max_active_bets:
            return (
                f"Límite de mesa alcanzado ({self.active_bets_count}/{self.max_active_bets} casillas activas). Apila más fichas en tus casillas actuales o equipa 'Mesa VIP' (+1 casilla)."
            )

        return None

    def validate_bet_restrictions(self, bet_type: BetType, target: BetTarget) -> None:
        """Lanza ValueError con la explicación de la regla si la casilla está restringida."""
        reason = self.get_bet_restriction_reason(bet_type, target)
        if reason is not None:
            raise ValueError(reason)

    def place_bet(
        self,
        bet_type: BetType,
        target: BetTarget,
        amount: int,
        special_chip: Optional[SpecialChipType] = None,
    ) -> Bet:
        """
        Valida y registra una apuesta en el tapete.
        Si ya existe una apuesta idéntica (mismo tipo y objetivo), acumula su monto
        y actualiza o conserva la ficha especial asociada.
        Si es una casilla nueva, verifica las reglas de opuestos y cupo máximo de casillas.
        """
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("El monto de la apuesta debe ser un número entero mayor a 0.")

        normalized_target = self._validate_and_normalize_target(bet_type, target)

        # Si ya existe una apuesta al mismo tipo y objetivo, sumamos las fichas
        for idx, existing in enumerate(self._bets):
            if existing.bet_type == bet_type and existing.target == normalized_target:
                merged_chip = special_chip if special_chip is not None else existing.special_chip
                updated_bet = Bet(
                    bet_type=bet_type,
                    target=normalized_target,
                    amount=existing.amount + amount,
                    special_chip=merged_chip,
                )
                self._bets[idx] = updated_bet
                return updated_bet

        self.validate_bet_restrictions(bet_type, normalized_target)

        new_bet = Bet(
            bet_type=bet_type,
            target=normalized_target,
            amount=amount,
            special_chip=special_chip,
        )
        self._bets.append(new_bet)
        return new_bet

    def multiply_all_bets(self, factor: int) -> None:
        """Multiplica el monto de todas las apuestas activas en el tapete (ej. Tarot)."""
        if factor <= 0:
            raise ValueError("El factor de multiplicación de apuestas debe ser mayor a 0.")
        self._bets = [
            Bet(
                bet_type=b.bet_type,
                target=b.target,
                amount=b.amount * factor,
                special_chip=b.special_chip,
            )
            for b in self._bets
        ]

    def get_covered_numbers(self, wheel: RouletteWheel) -> Set[int]:
        """
        Devuelve el conjunto de números de la rueda que están cubiertos
        por al menos una apuesta activa en el tapete.
        """
        covered: Set[int] = set()
        for pocket in wheel.pockets:
            if any(bet.covers_pocket(pocket) for bet in self._bets):
                covered.add(pocket.number)
        return covered

    def clear_bets(self) -> None:
        """Limpia todas las apuestas del tapete tras finalizar un tiro."""
        self._bets.clear()

    def is_secret_8_combo_active(self) -> bool:
        """
        Detecta de forma silenciosa la nueva combinación exacta de 5 casillas:
        - Pleno 8 (STRAIGHT, 8)
        - Negro (COLOR, Color.BLACK)
        - Par (PARITY, Parity.EVEN)
        - 1ra Docena (DOZEN, 1)
        - 2da Columna (COLUMN, 2)
        Solo se activa cuando en la mesa están únicamente esas 5 casillas.
        """
        if len(self._bets) != 5:
            return False
        required = {
            (BetType.STRAIGHT, 8),
            (BetType.COLOR, Color.BLACK),
            (BetType.PARITY, Parity.EVEN),
            (BetType.DOZEN, 1),
            (BetType.COLUMN, 2),
        }
        active_pairs = {(bet.bet_type, bet.target) for bet in self._bets}
        return active_pairs == required

    def is_cursed_old_9_combo_active(self) -> bool:
        """
        Detecta de forma silenciosa la antigua combinación exacta de 5 casillas:
        - Pleno 9 (STRAIGHT, 9)
        - Rojo (COLOR, Color.RED)
        - Columna 3 (COLUMN, 3)
        - Impar (PARITY, Parity.ODD)
        - 1ra Docena (DOZEN, 1)
        Cuando están únicamente esas 5 casillas en la mesa, el giro fuerza derrota total.
        """
        if len(self._bets) != 5:
            return False
        cursed = {
            (BetType.STRAIGHT, 9),
            (BetType.COLOR, Color.RED),
            (BetType.COLUMN, 3),
            (BetType.PARITY, Parity.ODD),
            (BetType.DOZEN, 1),
        }
        active_pairs = {(bet.bet_type, bet.target) for bet in self._bets}
        return active_pairs == cursed

    def is_easter_egg_9_combo_active(self) -> bool:
        """
        El antiguo Easter Egg visible del 9 quedó desactivado permanentemente
        para que ninguna mano ni cartel revele combinaciones secretas.
        """
        return False

    @staticmethod
    def _validate_and_normalize_target(
        bet_type: BetType, target: BetTarget
    ) -> Union[int, Color, Parity]:
        if bet_type == BetType.STRAIGHT:
            if not isinstance(target, int) or isinstance(target, bool) or not (0 <= target <= 36):
                raise ValueError(f"Pleno inválido: {target}. Debe ser un entero entre 0 y 36.")
            return target

        if bet_type == BetType.COLOR:
            if isinstance(target, str):
                try:
                    target = Color(target.upper())
                except ValueError as exc:
                    raise ValueError(f"Color inválido: {target}.") from exc
            if target not in (Color.RED, Color.BLACK):
                raise ValueError(
                    f"Apuesta de color inválida: {target}. Solo se permite RED o BLACK."
                )
            return target

        if bet_type == BetType.PARITY:
            if isinstance(target, str):
                try:
                    target = Parity(target.upper())
                except ValueError as exc:
                    raise ValueError(f"Paridad inválida: {target}.") from exc
            if target not in (Parity.EVEN, Parity.ODD):
                raise ValueError(
                    f"Apuesta de paridad inválida: {target}. Solo se permite EVEN o ODD."
                )
            return target

        if bet_type == BetType.DOZEN:
            if not isinstance(target, int) or isinstance(target, bool) or target not in (1, 2, 3):
                raise ValueError(
                    f"Docena inválida: {target}. Debe ser 1 (1-12), 2 (13-24) o 3 (25-36)."
                )
            return target

        if bet_type == BetType.COLUMN:
            if not isinstance(target, int) or isinstance(target, bool) or target not in (1, 2, 3):
                raise ValueError(f"Columna inválida: {target}. Debe ser 1, 2 o 3.")
            return target

        raise ValueError(f"Tipo de apuesta desconocido: {bet_type}")
