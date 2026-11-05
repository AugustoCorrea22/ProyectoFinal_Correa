"""
Fase 1.1: Simulador de Rueda (RouletteWheel)
Define las 37 casillas (0 al 36) de la ruleta europea, sus propiedades
matemáticas (color, paridad, docena, columna) y la generación aleatoria de tiros.
"""

from dataclasses import dataclass
from enum import Enum
import random
from typing import Dict, List, Optional, Set


# Orden real de los bolsillos en la rueda europea (sentido horario desde el 0)
EUROPEAN_WHEEL_SEQUENCE: List[int] = [
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
    5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
]


class Color( str, Enum ):
    GREEN = "GREEN"
    RED = "RED"
    BLACK = "BLACK"


class Parity( str, Enum ):
    EVEN = "EVEN"  # Par
    ODD = "ODD"    # Impar


# Números rojos oficiales en una ruleta europea estándar
RED_NUMBERS: Set[int] = {
    1, 3, 5, 7, 9, 12, 14, 16, 18,
    19, 21, 23, 25, 27, 30, 32, 34, 36
}


@dataclass(frozen=True)
class Pocket:
    """Representa una casilla individual de la rueda de la ruleta."""
    number: int
    color: Color
    parity: Optional[Parity]
    dozen: Optional[int]   # 1 (1-12), 2 (13-24), 3 (25-36), o None para el 0
    column: Optional[int]  # 1, 2, 3, o None para el 0


class RouletteWheel:
    """
    Simulador de rueda de ruleta europea de 37 casillas (0 al 36).
    Mantiene las casillas desacopladas de cualquier interfaz gráfica.
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self._rng: random.Random = rng if rng is not None else random.Random()
        self._pockets: Dict[int, Pocket] = self._build_standard_pockets()

    @staticmethod
    def _build_standard_pockets() -> Dict[int, Pocket]:
        pockets: Dict[int, Pocket] = {}
        for num in range(37):
            if num == 0:
                pockets[num] = Pocket(
                    number=0,
                    color=Color.GREEN,
                    parity=None,
                    dozen=None,
                    column=None,
                )
            else:
                color = Color.RED if num in RED_NUMBERS else Color.BLACK
                parity = Parity.EVEN if num % 2 == 0 else Parity.ODD
                dozen = (num - 1) // 12 + 1
                column = (num - 1) % 3 + 1
                pockets[num] = Pocket(
                    number=num,
                    color=color,
                    parity=parity,
                    dozen=dozen,
                    column=column,
                )
        return pockets

    @property
    def pockets(self) -> List[Pocket]:
        """Devuelve la lista ordenada de casillas activas en la rueda."""
        return [self._pockets[n] for n in sorted(self._pockets.keys())]

    def get_pocket(self, number: int) -> Pocket:
        """Obtiene la información de una casilla por su número (0-36)."""
        if number not in self._pockets:
            raise ValueError(f"El número {number} no está activo en la rueda.")
        return self._pockets[number]

    def has_pocket(self, number: int) -> bool:
        """Indica si un número específico sigue activo en la rueda."""
        return number in self._pockets

    def remove_pocket(self, number: int) -> None:
        """Elimina temporalmente una casilla de la rueda (ej. Tarot que elimina el 0)."""
        if number in self._pockets:
            del self._pockets[number]

    def transform_color(self, from_color: Color, to_color: Color) -> int:
        """
        Transforma todas las casillas activas de `from_color` a `to_color`.
        Devuelve la cantidad de casillas modificadas.
        """
        changed = 0
        for num, pocket in list(self._pockets.items()):
            if pocket.color == from_color:
                self._pockets[num] = Pocket(
                    number=pocket.number,
                    color=to_color,
                    parity=pocket.parity,
                    dozen=pocket.dozen,
                    column=pocket.column,
                )
                changed += 1
        return changed

    def transform_parity(self, from_parity: Parity, to_parity: Parity) -> int:
        """
        Transforma todas las casillas activas con `from_parity` a `to_parity`.
        Devuelve la cantidad de casillas modificadas.
        """
        changed = 0
        for num, pocket in list(self._pockets.items()):
            if pocket.parity == from_parity:
                self._pockets[num] = Pocket(
                    number=pocket.number,
                    color=pocket.color,
                    parity=to_parity,
                    dozen=pocket.dozen,
                    column=pocket.column,
                )
                changed += 1
        return changed

    def reset_wheel(self) -> None:
        """Restaura las 37 casillas originales de la ruleta europea estándar."""
        self._pockets = self._build_standard_pockets()

    def calculate_spin_weights(
        self,
        covered_numbers: Optional[Set[int]] = None,
        lucky_bonus: float = 0.0,
    ) -> List[float]:
        """
        Calcula la probabilidad/peso de cada casilla activa en la rueda.
        Si lucky_bonus > 0 y existen casillas con apuestas activas (covered_numbers),
        otorga un +lucky_bonus (ej. +0.15 para +15%) de probabilidad total al conjunto
        de casillas apostadas, repartiendo el resto entre las no apostadas.
        """
        active_pockets = self.pockets
        n_total = len(active_pockets)
        if n_total == 0:
            raise RuntimeError("No hay casillas activas en la rueda.")

        if not covered_numbers or lucky_bonus <= 0.0:
            return [1.0 / n_total] * n_total

        active_numbers = {p.number for p in active_pockets}
        valid_covered = covered_numbers.intersection(active_numbers)
        k_covered = len(valid_covered)

        # Si ninguna o todas las casillas están cubiertas, la distribución sigue siendo uniforme
        if k_covered == 0 or k_covered == n_total:
            return [1.0 / n_total] * n_total

        base_covered_prob = k_covered / n_total
        target_covered_prob = min(1.0, base_covered_prob + lucky_bonus)
        target_uncovered_prob = max(0.0, 1.0 - target_covered_prob)

        weight_per_covered = target_covered_prob / k_covered
        weight_per_uncovered = target_uncovered_prob / (n_total - k_covered)

        return [
            weight_per_covered if p.number in valid_covered else weight_per_uncovered
            for p in active_pockets
        ]

    def spin(
        self,
        covered_numbers: Optional[Set[int]] = None,
        lucky_bonus: float = 0.0,
    ) -> Pocket:
        """
        Gira la rueda y devuelve una casilla ganadora.
        Por defecto es equiprobable entre las casillas activas; si se activa el
        Modo Suertudo (lucky_bonus > 0), altera el generador con pesos ponderados.
        """
        active_pockets = self.pockets
        if not active_pockets:
            raise RuntimeError("No hay casillas activas en la rueda para girar.")

        if covered_numbers and lucky_bonus > 0.0:
            weights = self.calculate_spin_weights(covered_numbers, lucky_bonus)
            return self._rng.choices(active_pockets, weights=weights, k=1)[0]

        return self._rng.choice(active_pockets)
