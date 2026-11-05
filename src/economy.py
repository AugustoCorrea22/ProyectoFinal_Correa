"""
Fase 4: Economía, Cuotas y Bucle de Tienda
Establece la condición de victoria/derrota y el progreso entre niveles:
1. Sistema de Cuotas por Nivel (LevelQuota y EconomyManager): Meta de fichas a acumular
   y límite de giros permitidos por nivel.
2. Evaluación de Fin de Nivel: Verifica si el jugador alcanza y paga la cuota para
   avanzar a la tienda o si pierde la partida (Game Over).
3. Bucle de Tienda (ShopManager y BetUpgradeManager): Fase de compra entre niveles
   donde el jugador gasta sus fichas sobrantes en adquirir nuevos Comodines, Cartas de
   Tarot, Fichas Especiales o subir el nivel de sus apuestas.
"""

from dataclasses import dataclass
from enum import Enum
import random
from typing import Dict, List, Optional, Union
from src.board import BetType, SpecialChipType
from src.modifiers import (
    JOKER_CATALOG,
    TAROT_CATALOG,
    JokerManager,
    JokerType,
    SpecialChipManager,
    TarotManager,
    TarotType,
)


# =============================================================================
# 4.1 Sistema de Cuotas por Nivel y Gestor de Nivel de Apuestas
# =============================================================================

class GamePhase(str, Enum):
    BETTING_AND_SPINNING = "BETTING_AND_SPINNING"
    SHOP = "SHOP"
    GAME_OVER = "GAME_OVER"
    VICTORY = "VICTORY"


@dataclass(frozen=True)
class LevelQuota:
    """Define las metas y límites de un nivel específico."""
    level: int
    target_quota: int
    max_spins: int
    min_profit_required: int = 50


@dataclass(frozen=True)
class LevelEvaluationResult:
    """Resultado de la verificación de fin de nivel."""
    level: int
    passed: bool
    game_over: bool
    quota_paid: int
    surplus_chips: int
    next_phase: GamePhase
    rescued: bool = False  # Superada gracias al Ángel de la Guarda
    bonus_money: int = 0   # Dinero extra de Comodines al superarla


BET_UPGRADE_ODDS_STEP: Dict[BetType, int] = {
    BetType.STRAIGHT: 5,  # +5 a 1 por cada nivel extra en Plenos (35 -> 40 -> 45...)
    BetType.DOZEN: 1,     # +1 a 1 por cada nivel extra en Docenas (2 -> 3 -> 4...)
    BetType.COLUMN: 1,    # +1 a 1 por cada nivel extra en Columnas (2 -> 3 -> 4...)
    BetType.COLOR: 1,     # +1 a 1 por cada nivel extra en Color (1 -> 2 -> 3...)
    BetType.PARITY: 1,    # +1 a 1 por cada nivel extra en Par/Impar (1 -> 2 -> 3...)
}


class BetUpgradeManager:
    """
    Gestiona el nivel de cada categoría de apuesta en el tapete.
    Subir el nivel de una apuesta incrementa su pago base en PayoutCalculator.
    """

    def __init__(self) -> None:
        self._levels: Dict[BetType, int] = {bt: 1 for bt in BetType}

    def get_level(self, bet_type: BetType) -> int:
        return self._levels[bet_type]

    def upgrade_bet(self, bet_type: BetType) -> int:
        """Incrementa en +1 el nivel del tipo de apuesta indicado y devuelve el nuevo nivel."""
        self._levels[bet_type] += 1
        return self._levels[bet_type]

    def get_odds_bonus(self, bet_type: BetType) -> int:
        """Devuelve el bono adicional de multiplicador (X a 1) según el nivel actual."""
        extra_levels = self._levels[bet_type] - 1
        return extra_levels * BET_UPGRADE_ODDS_STEP[bet_type]

    def get_all_bonuses(self) -> Dict[BetType, int]:
        """Diccionario listo para inyectar como `bet_level_bonuses` en PayoutCalculator."""
        return {bt: self.get_odds_bonus(bt) for bt in BetType}


# =============================================================================
# 4.2 Evaluación de Fin de Nivel y Economía del Jugador
# =============================================================================

class EconomyManager:
    """
    Controla las fichas del jugador (bankroll), la cuota del nivel actual,
    la ganancia mínima obligatoria por Ciega, los giros restantes y la
    transición entre ronda de juego, tienda y derrota.
    """

    def __init__(
        self,
        starting_chips: int = 100,
        base_quota: int = 150,
        spins_per_level: int = 5,
    ) -> None:
        if starting_chips <= 0:
            raise ValueError("Las fichas iniciales deben ser mayores a 0.")
        self._bankroll: int = starting_chips
        self._level_start_bankroll: int = starting_chips
        self._base_quota: int = base_quota
        self._spins_per_level: int = spins_per_level
        self._current_level: int = 1
        self._spins_used: int = 0
        self._phase: GamePhase = GamePhase.BETTING_AND_SPINNING

    @property
    def bankroll(self) -> int:
        return self._bankroll

    @property
    def level_start_bankroll(self) -> int:
        return self._level_start_bankroll

    @property
    def level_profit(self) -> int:
        """Ganancia neta obtenida dentro del nivel actual respecto a su banca inicial."""
        return self._bankroll - self._level_start_bankroll

    @property
    def min_profit_required(self) -> int:
        """Ganancia neta mínima obligatoria que se debe generar en el nivel actual."""
        return self.current_quota.min_profit_required

    @property
    def current_level(self) -> int:
        return self._current_level

    @property
    def spins_used(self) -> int:
        return self._spins_used

    @property
    def spins_remaining(self) -> int:
        return max(0, self.current_quota.max_spins - self._spins_used)

    @property
    def phase(self) -> GamePhase:
        return self._phase

    @property
    def current_quota(self) -> LevelQuota:
        """Calcula la cuota escalada y la ganancia mínima obligatoria para el nivel actual."""
        # Escala progresiva balanceada: Nivel 1 = 150, Nivel 2 = 213, Nivel 3 (Jefe) = 280...
        target = int(self._base_quota * (1 + 0.42 * (self._current_level - 1) ** 1.05))
        min_profit = max(40, int(target * 0.25))
        # En rondas de Crupier Jefe (cada 3 niveles) otorgamos +2 giros adicionales (7 giros)
        level_spins = self._spins_per_level + (2 if self._current_level % 3 == 0 else 0)
        return LevelQuota(
            level=self._current_level,
            target_quota=target,
            max_spins=level_spins,
            min_profit_required=min_profit,
        )

    def add_chips(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("La cantidad a añadir no puede ser negativa.")
        self._bankroll += amount

    def add_external_chips(self, amount: int) -> None:
        """
        Dinero que no viene de jugar (vender cartas, El Ermitaño, La Templanza):
        suma a la banca, pero NO cuenta para la ganancia mínima de la ciega.
        """
        self.add_chips(amount)
        if self._phase == GamePhase.BETTING_AND_SPINNING:
            self._level_start_bankroll += amount

    def deduct_chips(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("La cantidad a descontar no puede ser negativa.")
        if amount > self._bankroll:
            raise ValueError(
                f"Fichas insuficientes: intentaste gastar {amount}, tienes {self._bankroll}."
            )
        self._bankroll -= amount

    def consume_spin(self) -> None:
        """Registra que se ha utilizado un giro en el nivel actual."""
        if self._phase != GamePhase.BETTING_AND_SPINNING:
            raise RuntimeError("No se puede girar fuera de la fase de apuestas.")
        if self.spins_remaining <= 0:
            raise RuntimeError("No quedan giros disponibles en este nivel.")
        self._spins_used += 1

    def has_met_min_profit(self) -> bool:
        """Verifica si el jugador ya generó la ganancia neta mínima obligatoria de esta Ciega."""
        return self.level_profit >= self.current_quota.min_profit_required

    def can_pay_quota(self) -> bool:
        """
        Indica si el jugador cumple ambos requisitos para superar la Ciega actual:
        1. Tener suficientes fichas totales para pagar la cuota (bankroll >= target_quota).
        2. Haber ganado al menos el mínimo de ganancia neta exigido en este nivel
           (level_profit >= min_profit_required), evitando pasar rondas sin jugar.
        """
        quota = self.current_quota
        return (
            self._bankroll >= quota.target_quota
            and self.level_profit >= quota.min_profit_required
        )

    def evaluate_end_of_level(self) -> LevelEvaluationResult:
        """
        Evalúa el fin del nivel actual:
        - Si el jugador tiene fichas suficientes (>= target_quota) Y alcanzó la ganancia
          mínima obligatoria del nivel (>= min_profit_required), paga la cuota,
          conserva las fichas sobrantes y pasa a la fase de TIENDA (SHOP).
        - En caso contrario, pierde la partida (GAME_OVER).
        """
        quota = self.current_quota.target_quota
        if self.can_pay_quota():
            self._bankroll -= quota
            self._phase = GamePhase.SHOP
            return LevelEvaluationResult(
                level=self._current_level,
                passed=True,
                game_over=False,
                quota_paid=quota,
                surplus_chips=self._bankroll,
                next_phase=self._phase,
            )

        self._phase = GamePhase.GAME_OVER
        return LevelEvaluationResult(
            level=self._current_level,
            passed=False,
            game_over=True,
            quota_paid=0,
            surplus_chips=self._bankroll,
            next_phase=self._phase,
        )

    def rescue_current_level(self) -> LevelEvaluationResult:
        """Pasa la ciega sin pagar la cuota (rescate del Ángel de la Guarda)."""
        self._phase = GamePhase.SHOP
        return LevelEvaluationResult(
            level=self._current_level,
            passed=True,
            game_over=False,
            quota_paid=0,
            surplus_chips=self._bankroll,
            next_phase=self._phase,
            rescued=True,
        )

    def advance_to_next_level(self, base_level_stipend: int = 100) -> LevelQuota:
        """
        Sale de la tienda, avanza al siguiente nivel, reinicia el contador de giros,
        otorga el estipendio base de operación y fija la nueva banca base del nivel
        para exigir ganancia neta fresca en la siguiente Ciega.
        """
        if self._phase != GamePhase.SHOP:
            raise RuntimeError("Solo se puede avanzar de nivel desde la fase de Tienda.")
        self._current_level += 1
        self._spins_used = 0
        self._bankroll += base_level_stipend
        self._level_start_bankroll = self._bankroll
        self._phase = GamePhase.BETTING_AND_SPINNING
        return self.current_quota


# =============================================================================
# 4.3 Bucle de Tienda (ShopManager)
# =============================================================================

class ShopItemKind(str, Enum):
    JOKER = "JOKER"
    TAROT = "TAROT"
    SPECIAL_CHIP = "SPECIAL_CHIP"
    BET_UPGRADE = "BET_UPGRADE"
    HAND_UPGRADE = "HAND_UPGRADE"


ShopPayload = Union[JokerType, TarotType, SpecialChipType, BetType, "HandType"]


@dataclass(frozen=True)
class ShopItem:
    """Representa una oferta disponible para comprar en la Tienda."""
    kind: ShopItemKind
    payload: ShopPayload
    name: str
    description: str
    cost: int


SPECIAL_CHIP_SHOP_INFO: Dict[SpecialChipType, tuple[str, str, int]] = {
    SpecialChipType.TRIPLE_PAYOUT: (
        "Ficha Triple",
        "Triplica (x3) la ganancia de la apuesta donde se coloque.",
        45,
    ),
    SpecialChipType.ZERO_SHIELD: (
        "Ficha Escudo Cero",
        "Protege una apuesta contra el 0 verde reembolsando el monto.",
        30,
    ),
    SpecialChipType.MAGNET_CHIP: (
        "Ficha Imán",
        "Suma +30 fichas extra si la apuesta resulta ganadora.",
        35,
    ),
}


class ShopManager:
    """
    Gestiona el catálogo y las compras de la Tienda entre niveles.
    Permite gastar las fichas sobrantes en Comodines, Cartas de Tarot,
    Fichas Especiales o subir el nivel de las apuestas.
    """

    def __init__(self, rng: Optional[random.Random] = None) -> None:
        self._rng: random.Random = rng if rng is not None else random.Random()
        self._offers: List[ShopItem] = []

    @property
    def offers(self) -> List[ShopItem]:
        return list(self._offers)

    def generate_offers(self) -> List[ShopItem]:
        """
        Genera un escaparate variado con 1 Comodín, 1 Carta de Tarot,
        1 Ficha Especial y 1 Mejora de Nivel de Apuesta.
        """
        joker_type = self._rng.choice(list(JokerType))
        joker = JOKER_CATALOG[joker_type]

        tarot_type = self._rng.choice(list(TarotType))
        tarot = TAROT_CATALOG[tarot_type]

        chip_type = self._rng.choice(list(SpecialChipType))
        chip_name, chip_desc, chip_cost = SPECIAL_CHIP_SHOP_INFO[chip_type]

        from src.hands import HAND_CATALOG, HAND_LEVEL_STEP
        upgradable_hands = [hand for hand, info in HAND_CATALOG.items() if info.upgradable]
        hand_type = self._rng.choice(upgradable_hands)
        hand_info = HAND_CATALOG[hand_type]

        self._offers = [
            ShopItem(
                kind=ShopItemKind.JOKER,
                payload=joker_type,
                name=joker.name,
                description=joker.description,
                cost=joker.cost,
            ),
            ShopItem(
                kind=ShopItemKind.TAROT,
                payload=tarot_type,
                name=tarot.name,
                description=tarot.description,
                cost=tarot.cost,
            ),
            ShopItem(
                kind=ShopItemKind.SPECIAL_CHIP,
                payload=chip_type,
                name=chip_name,
                description=chip_desc,
                cost=chip_cost,
            ),
            ShopItem(
                kind=ShopItemKind.HAND_UPGRADE,
                payload=hand_type,
                name=f"Mejorar {hand_info.name}",
                description=(
                    f"+{HAND_LEVEL_STEP} multi{' por acierto' if hand_info.per_hit else ''} "
                    f"en la mano {hand_info.name}. Permanente."
                ),
                cost=60,
            ),
        ]
        return self.offers

    def set_custom_offers(self, offers: List[ShopItem]) -> None:
        """Permite inyectar ofertas específicas en la tienda (útil para pruebas o eventos)."""
        self._offers = list(offers)

    def buy_item(
        self,
        offer_index: int,
        economy: EconomyManager,
        joker_mgr: JokerManager,
        tarot_mgr: TarotManager,
        chip_mgr: SpecialChipManager,
        upgrade_mgr: BetUpgradeManager,
        hand_mgr=None,
    ) -> ShopItem:
        """
        Compra el artículo en `offer_index` descontando su costo de las fichas sobrantes
        del jugador y entregando el efecto/objeto al gestor correspondiente.
        """
        if economy.phase != GamePhase.SHOP:
            raise RuntimeError("Solo se pueden comprar objetos durante la fase de Tienda.")
        if not (0 <= offer_index < len(self._offers)):
            raise IndexError("Índice de oferta de tienda inválido.")

        item = self._offers[offer_index]
        if economy.bankroll < item.cost:
            raise ValueError(
                f"Fichas sobrantes insuficientes para comprar '{item.name}' "
                f"(Costo: {item.cost}, Disponibles: {economy.bankroll})."
            )

        # Aplicamos primero la entrega del objeto (para validar espacio en slots/mano antes de cobrar)
        if item.kind == ShopItemKind.JOKER:
            assert isinstance(item.payload, JokerType)
            joker_mgr.add_joker_by_type(item.payload)
        elif item.kind == ShopItemKind.TAROT:
            assert isinstance(item.payload, TarotType)
            tarot_mgr.add_card_by_type(item.payload)
        elif item.kind == ShopItemKind.SPECIAL_CHIP:
            assert isinstance(item.payload, SpecialChipType)
            chip_mgr.add_chip(item.payload, count=1)
        elif item.kind == ShopItemKind.BET_UPGRADE:
            assert isinstance(item.payload, BetType)
            upgrade_mgr.upgrade_bet(item.payload)
        elif item.kind == ShopItemKind.HAND_UPGRADE:
            if hand_mgr is None:
                raise ValueError("Falta el gestor de manos para aplicar la mejora.")
            hand_mgr.upgrade(item.payload)

        economy.deduct_chips(item.cost)
        return self._offers.pop(offer_index)
