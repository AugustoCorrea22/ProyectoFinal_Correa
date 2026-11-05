"""
Fase 3: Arquitectura de Modificadores (Comodines, Fichas y Tarots)
Permite romper las reglas tradicionales de la ruleta mediante tres subsistemas:
1. Gestor de Comodines Pasivos (JokerManager): Slots permanentes que leen el resultado
   del tiro y añaden multiplicadores o fichas extra.
2. Gestor de Fichas Especiales (SpecialChipManager): Gestiona el inventario y aplicación
   de fichas con propiedades (TRIPLE_PAYOUT, ZERO_SHIELD, MAGNET_CHIP).
3. Gestor de Cartas de Tarot Consumibles (TarotManager): Cartas de uso único desde la
   mano que modifican el tapete o la rueda para el siguiente tiro (transformar negras en
   rojas, eliminar el cero, duplicar apuestas o convertir impares en pares).
"""

from dataclasses import dataclass
from enum import Enum
import random
from typing import Dict, List, Optional
from src.board import Bet, BetType, BettingBoard, BetTarget, SpecialChipType
from src.payout import SpinEvaluation
from src.wheel import Color, Parity, RouletteWheel


# =============================================================================
# 3.1 Gestor de Comodines Pasivos (Jokers)
# =============================================================================

class JokerType(str, Enum):
    RED_RUBY = "RED_RUBY"            # +50 fichas extra si sale casilla Roja
    BLACK_ONYX = "BLACK_ONYX"        # +50 fichas extra si sale casilla Negra
    ODD_SCHOLAR = "ODD_SCHOLAR"      # Multiplica x2 la ganancia si sale número Impar y ganas
    STRAIGHT_SNIPER = "STRAIGHT_SNIPER"  # +100 fichas y x2 ganancia si aciertas un Pleno
    ZERO_JESTER = "ZERO_JESTER"      # +150 fichas extra cuando sale el 0 verde
    # --- Comodines de mano (+multi según la mano jugada)
    DANCE_PARTNER = "DANCE_PARTNER"  # +1 multi si juegas PAR
    SENTINEL = "SENTINEL"            # +1 multi si juegas CERCO
    BODYGUARD = "BODYGUARD"          # +1.5 multi si juegas ESCOLTA
    NOSY_NEIGHBOR = "NOSY_NEIGHBOR"  # +1 multi si juegas VECINOS
    # --- Fichas y multi condicionales
    EVEN_PERFECT = "EVEN_PERFECT"    # +40 fichas si sale Par y aciertas
    WALL_STREET_BULL = "WALL_STREET_BULL"  # +1 ficha por cada $5 de banca al acertar
    COLLECTOR = "COLLECTOR"          # +0.25 multi por cada comodín equipado
    ECHO = "ECHO"                    # La ganancia de los plenos cuenta doble
    LOADED_DIE = "LOADED_DIE"        # +0 a +2 multi aleatorio al acertar
    # --- Comodines que cambian con el tiempo
    HOT_STREAK = "HOT_STREAK"        # +0.25 multi por cada giro ganado seguido
    MELTING_ICE = "MELTING_ICE"      # +100 fichas al acertar; pierde 20 por giro y se derrite
    LUCKY_CHIP = "LUCKY_CHIP"        # x2 multi al acertar; 1 en 6 de romperse tras cada giro
    # --- Economía y supervivencia
    GOLD_BAR = "GOLD_BAR"            # +$25 al superar cada ciega
    GOLDEN_EGG = "GOLDEN_EGG"        # Su valor de venta sube +$10 por ciega superada
    GUARDIAN_ANGEL = "GUARDIAN_ANGEL"  # Te salva de perder si llegaste al 75% de la meta (se destruye)


@dataclass(frozen=True)
class Joker:
    """Representa un Comodín pasivo que ocupa un slot permanente."""
    joker_type: JokerType
    name: str
    description: str
    cost: int


JOKER_CATALOG: Dict[JokerType, Joker] = {
    JokerType.RED_RUBY: Joker(
        joker_type=JokerType.RED_RUBY,
        name="Rubí Carmesí",
        description="Añade +50 fichas extra si la bola cae en Rojo y aciertas.",
        cost=80,
    ),
    JokerType.BLACK_ONYX: Joker(
        joker_type=JokerType.BLACK_ONYX,
        name="Ónix Nocturno",
        description="Añade +50 fichas extra si la bola cae en Negro y aciertas.",
        cost=80,
    ),
    JokerType.ODD_SCHOLAR: Joker(
        joker_type=JokerType.ODD_SCHOLAR,
        name="Erudito Impar",
        description="Multiplica x2 la ganancia de tus apuestas si sale un número Impar.",
        cost=120,
    ),
    JokerType.STRAIGHT_SNIPER: Joker(
        joker_type=JokerType.STRAIGHT_SNIPER,
        name="Francotirador",
        description="Si aciertas un Pleno, suma +100 fichas extra y multiplica x2 la ganancia.",
        cost=150,
    ),
    JokerType.ZERO_JESTER: Joker(
        joker_type=JokerType.ZERO_JESTER,
        name="Bufón del Cero",
        description="Otorga +150 fichas cada vez que la bola cae en el 0 verde.",
        cost=100,
    ),
    JokerType.DANCE_PARTNER: Joker(
        joker_type=JokerType.DANCE_PARTNER,
        name="Pareja de Baile",
        description="+1 multi cuando juegas la mano PAR.",
        cost=70,
    ),
    JokerType.SENTINEL: Joker(
        joker_type=JokerType.SENTINEL,
        name="Centinela",
        description="+1 multi cuando juegas la mano CERCO.",
        cost=80,
    ),
    JokerType.BODYGUARD: Joker(
        joker_type=JokerType.BODYGUARD,
        name="Guardaespaldas",
        description="+1.5 multi cuando juegas la mano ESCOLTA.",
        cost=90,
    ),
    JokerType.NOSY_NEIGHBOR: Joker(
        joker_type=JokerType.NOSY_NEIGHBOR,
        name="Vecina Chismosa",
        description="+1 multi cuando juegas la mano VECINOS.",
        cost=80,
    ),
    JokerType.EVEN_PERFECT: Joker(
        joker_type=JokerType.EVEN_PERFECT,
        name="Par Perfecto",
        description="Añade +40 fichas si la bola cae en un número Par y aciertas.",
        cost=70,
    ),
    JokerType.WALL_STREET_BULL: Joker(
        joker_type=JokerType.WALL_STREET_BULL,
        name="Toro de la Bolsa",
        description="Al acertar, suma +1 ficha por cada $5 que tengas en la banca.",
        cost=110,
    ),
    JokerType.COLLECTOR: Joker(
        joker_type=JokerType.COLLECTOR,
        name="Coleccionista",
        description="+0.25 multi por cada Comodín que tengas equipado (incluido este).",
        cost=90,
    ),
    JokerType.ECHO: Joker(
        joker_type=JokerType.ECHO,
        name="Eco",
        description="La ganancia de tus Plenos acertados cuenta doble.",
        cost=130,
    ),
    JokerType.LOADED_DIE: Joker(
        joker_type=JokerType.LOADED_DIE,
        name="Dado Cargado",
        description="Al acertar, suma entre +0 y +2 multi al azar.",
        cost=60,
    ),
    JokerType.HOT_STREAK: Joker(
        joker_type=JokerType.HOT_STREAK,
        name="Racha Ardiente",
        description="+0.25 multi por cada giro ganado seguido. Se reinicia al perder un giro.",
        cost=100,
    ),
    JokerType.MELTING_ICE: Joker(
        joker_type=JokerType.MELTING_ICE,
        name="Helado de Casino",
        description="+100 fichas al acertar. Pierde 20 fichas por cada giro y se derrite al llegar a 0.",
        cost=60,
    ),
    JokerType.LUCKY_CHIP: Joker(
        joker_type=JokerType.LUCKY_CHIP,
        name="Ficha de la Suerte",
        description="x2 multi al acertar. Tras cada giro tiene 1 en 6 de romperse.",
        cost=90,
    ),
    JokerType.GOLD_BAR: Joker(
        joker_type=JokerType.GOLD_BAR,
        name="Lingote",
        description="Te da +$25 cada vez que superas una ciega.",
        cost=80,
    ),
    JokerType.GOLDEN_EGG: Joker(
        joker_type=JokerType.GOLDEN_EGG,
        name="Huevo de Oro",
        description="No hace nada en la mesa, pero su valor de venta sube +$10 por cada ciega superada.",
        cost=50,
    ),
    JokerType.GUARDIAN_ANGEL: Joker(
        joker_type=JokerType.GUARDIAN_ANGEL,
        name="Ángel de la Guarda",
        description="Si pierdes una ciega habiendo llegado al 75% de la meta, te salva y se destruye.",
        cost=90,
    ),
}

# Comodines de mano: +multi cuando la mano jugada coincide
HAND_JOKER_BONUS: Dict[JokerType, tuple] = {
    JokerType.DANCE_PARTNER: ("PAR", 1.0),
    JokerType.SENTINEL: ("CERCO", 1.0),
    JokerType.BODYGUARD: ("ESCOLTA", 1.5),
    JokerType.NOSY_NEIGHBOR: ("VECINOS", 1.0),
}

MELTING_ICE_START = 100
MELTING_ICE_STEP = 20
LUCKY_CHIP_BREAK_ODDS = 6
GOLD_BAR_PAYOUT = 25
GOLDEN_EGG_GROWTH = 10


def joker_sell_value(joker: "Joker", sell_bonus: int = 0) -> int:
    """Como en los juegos de cartas roguelike: se vende por la mitad de su costo (+bonos)."""
    return joker.cost // 2 + sell_bonus


@dataclass(frozen=True)
class JokerApplicationResult:
    """Resultado detallado de aplicar los Comodines pasivos tras un giro."""
    base_evaluation: SpinEvaluation
    triggered_jokers: List[JokerType]
    bonus_flat_chips: int
    joker_multiplier: int
    final_total_return: int
    final_net_profit: int
    bonus_add_mult: float = 0.0  # +multi que suman los comodines a la mano


class JokerManager:
    """Gestiona los slots permanentes de Comodines pasivos del jugador."""

    def __init__(self, max_slots: int = 5) -> None:
        if max_slots <= 0:
            raise ValueError("El número máximo de slots de comodines debe ser mayor a 0.")
        self._max_slots: int = max_slots
        self._slots: List[Joker] = []
        # Estado propio de cada slot (contadores de comodines que cambian con el tiempo)
        self._state: List[Dict[str, int]] = []

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def jokers(self) -> List[Joker]:
        return list(self._slots)

    def add_joker(self, joker: Joker) -> None:
        """Añade un Comodín a los slots activos si hay espacio disponible."""
        if len(self._slots) >= self._max_slots:
            raise ValueError(f"No hay slots disponibles (máximo {self._max_slots}).")
        self._slots.append(joker)
        state: Dict[str, int] = {"sell_bonus": 0, "streak": 0}
        if joker.joker_type == JokerType.MELTING_ICE:
            state["chips"] = MELTING_ICE_START
        self._state.append(state)

    def add_joker_by_type(self, joker_type: JokerType) -> Joker:
        """Crea y equipa un Comodín a partir de su tipo en el catálogo."""
        joker = JOKER_CATALOG[joker_type]
        self.add_joker(joker)
        return joker

    def remove_joker(self, index: int) -> Joker:
        """Elimina y devuelve el Comodín en el índice indicado."""
        if not (0 <= index < len(self._slots)):
            raise IndexError("Índice de comodín fuera de rango.")
        self._state.pop(index)
        return self._slots.pop(index)

    def sell_value(self, index: int) -> int:
        if not (0 <= index < len(self._slots)):
            raise IndexError("Índice de comodín fuera de rango.")
        return joker_sell_value(self._slots[index], self._state[index].get("sell_bonus", 0))

    def slot_state(self, index: int) -> Dict[str, int]:
        return dict(self._state[index])

    def has_joker(self, joker_type: JokerType) -> bool:
        return any(j.joker_type == joker_type for j in self._slots)

    def remove_first(self, joker_type: JokerType) -> Optional[Joker]:
        for idx, joker in enumerate(self._slots):
            if joker.joker_type == joker_type:
                return self.remove_joker(idx)
        return None

    def after_spin(self, any_won: bool, rng: Optional[random.Random] = None) -> List[Joker]:
        """Actualiza los comodines que cambian con cada giro. Devuelve los que se destruyeron."""
        chooser = rng if rng is not None else random.Random()
        destroyed_idx: List[int] = []
        for idx, joker in enumerate(self._slots):
            state = self._state[idx]
            jtype = joker.joker_type
            if jtype == JokerType.HOT_STREAK:
                state["streak"] = state.get("streak", 0) + 1 if any_won else 0
            elif jtype == JokerType.MELTING_ICE:
                state["chips"] = state.get("chips", MELTING_ICE_START) - MELTING_ICE_STEP
                if state["chips"] <= 0:
                    destroyed_idx.append(idx)
            elif jtype == JokerType.LUCKY_CHIP:
                if chooser.randrange(LUCKY_CHIP_BREAK_ODDS) == 0:
                    destroyed_idx.append(idx)
        destroyed = [self._slots[i] for i in destroyed_idx]
        for idx in reversed(destroyed_idx):
            self.remove_joker(idx)
        return destroyed

    def on_level_passed(self) -> int:
        """Efectos al superar una ciega. Devuelve el dinero extra que generan los comodines."""
        money = 0
        for idx, joker in enumerate(self._slots):
            if joker.joker_type == JokerType.GOLD_BAR:
                money += GOLD_BAR_PAYOUT
            elif joker.joker_type == JokerType.GOLDEN_EGG:
                self._state[idx]["sell_bonus"] = self._state[idx].get("sell_bonus", 0) + GOLDEN_EGG_GROWTH
        return money

    def destroy_random_joker(self, rng: Optional[random.Random] = None) -> Optional[Joker]:
        """Destruye un Comodín aleatorio (usado por eventos de castigo de Jefes)."""
        if not self._slots:
            return None
        chooser = rng if rng is not None else random.Random()
        idx = chooser.randrange(len(self._slots))
        return self.remove_joker(idx)

    def apply_jokers(
        self,
        evaluation: SpinEvaluation,
        hand_name: Optional[str] = None,
        bankroll: int = 0,
        rng: Optional[random.Random] = None,
    ) -> JokerApplicationResult:
        """
        Lee el resultado del tiro y aplica los efectos pasivos de todos los
        Comodines equipados en orden: fichas extra, +multi y xmulti.
        """
        chooser = rng if rng is not None else random.Random()
        add_mult = 0.0
        pocket = evaluation.winning_pocket
        any_won = evaluation.any_bet_won
        won_straight = any(
            res.won and res.bet.bet_type == BetType.STRAIGHT
            for res in evaluation.bet_results
        )

        triggered: List[JokerType] = []
        bonus_flat_chips = 0
        joker_multiplier = 1

        for slot_idx, joker in enumerate(self._slots):
            jtype = joker.joker_type
            if jtype == JokerType.RED_RUBY and any_won and pocket.color == Color.RED:
                bonus_flat_chips += 50
                triggered.append(jtype)
            elif jtype == JokerType.BLACK_ONYX and any_won and pocket.color == Color.BLACK:
                bonus_flat_chips += 50
                triggered.append(jtype)
            elif jtype == JokerType.ODD_SCHOLAR and any_won and pocket.parity == Parity.ODD:
                joker_multiplier *= 2
                triggered.append(jtype)
            elif jtype == JokerType.STRAIGHT_SNIPER and won_straight:
                bonus_flat_chips += 100
                joker_multiplier *= 2
                triggered.append(jtype)
            elif jtype == JokerType.ZERO_JESTER and pocket.number == 0:
                bonus_flat_chips += 150
                triggered.append(jtype)
            elif jtype in HAND_JOKER_BONUS and hand_name == HAND_JOKER_BONUS[jtype][0] and any_won:
                add_mult += HAND_JOKER_BONUS[jtype][1]
                triggered.append(jtype)
            elif jtype == JokerType.EVEN_PERFECT and any_won and pocket.parity == Parity.EVEN:
                bonus_flat_chips += 40
                triggered.append(jtype)
            elif jtype == JokerType.WALL_STREET_BULL and any_won and bankroll >= 5:
                bonus_flat_chips += bankroll // 5
                triggered.append(jtype)
            elif jtype == JokerType.COLLECTOR and any_won:
                add_mult += 0.25 * len(self._slots)
                triggered.append(jtype)
            elif jtype == JokerType.ECHO and won_straight:
                bonus_flat_chips += sum(
                    res.net_profit for res in evaluation.bet_results
                    if res.won and res.bet.bet_type == BetType.STRAIGHT
                )
                triggered.append(jtype)
            elif jtype == JokerType.LOADED_DIE and any_won:
                add_mult += chooser.choice((0.0, 0.5, 1.0, 1.5, 2.0))
                triggered.append(jtype)
            elif jtype == JokerType.HOT_STREAK and any_won:
                streak = self._state[slot_idx].get("streak", 0)
                if streak > 0:
                    add_mult += 0.25 * streak
                    triggered.append(jtype)
            elif jtype == JokerType.MELTING_ICE and any_won:
                bonus_flat_chips += self._state[slot_idx].get("chips", MELTING_ICE_START)
                triggered.append(jtype)
            elif jtype == JokerType.LUCKY_CHIP and any_won:
                joker_multiplier *= 2
                triggered.append(jtype)

        # Calculamos el retorno final combinando el retorno base, el multiplicador de comodines y fichas extra
        winning_stakes = sum(res.bet.amount for res in evaluation.bet_results if res.won)
        shielded_stakes = sum(
            res.total_return for res in evaluation.bet_results if not res.won and res.total_return > 0
        )
        base_winning_profit = sum(res.net_profit for res in evaluation.bet_results if res.won)

        final_winning_profit = (base_winning_profit * joker_multiplier) + bonus_flat_chips
        final_total_return = winning_stakes + shielded_stakes + final_winning_profit
        final_net_profit = final_total_return - evaluation.total_wagered

        return JokerApplicationResult(
            base_evaluation=evaluation,
            triggered_jokers=triggered,
            bonus_flat_chips=bonus_flat_chips,
            joker_multiplier=joker_multiplier,
            final_total_return=final_total_return,
            final_net_profit=final_net_profit,
            bonus_add_mult=add_mult,
        )


# =============================================================================
# 3.2 Gestor de Fichas Especiales (SpecialChipManager)
# =============================================================================

class SpecialChipManager:
    """
    Gestiona el inventario de Fichas Especiales del jugador y permite
    colocarlas en el tablero de apuestas consumiéndolas del inventario.
    """

    def __init__(self) -> None:
        self._inventory: Dict[SpecialChipType, int] = {
            chip_type: 0 for chip_type in SpecialChipType
        }

    def add_chip(self, chip_type: SpecialChipType, count: int = 1) -> None:
        if count <= 0:
            raise ValueError("La cantidad de fichas especiales a añadir debe ser mayor a 0.")
        self._inventory[chip_type] = self._inventory.get(chip_type, 0) + count

    def get_count(self, chip_type: SpecialChipType) -> int:
        return self._inventory.get(chip_type, 0)

    def place_special_bet(
        self,
        board: BettingBoard,
        bet_type: BetType,
        target: BetTarget,
        amount: int,
        chip_type: SpecialChipType,
    ) -> Bet:
        """Consume una ficha especial del inventario y registra la apuesta con su propiedad."""
        if self.get_count(chip_type) <= 0:
            raise ValueError(f"No tienes fichas especiales de tipo {chip_type} en el inventario.")
        bet = board.place_bet(
            bet_type=bet_type,
            target=target,
            amount=amount,
            special_chip=chip_type,
        )
        self._inventory[chip_type] -= 1
        return bet


# =============================================================================
# 3.3 Gestor de Cartas de Tarot Consumibles (TarotManager)
# =============================================================================

class TarotType(str, Enum):
    THE_SUN = "THE_SUN"                        # Transforma todas las casillas negras en rojas por 1 tiro
    THE_FOOL = "THE_FOOL"                      # Elimina el 0 verde de la rueda por 1 tiro
    WHEEL_OF_FORTUNE = "WHEEL_OF_FORTUNE"      # Duplica el monto de las apuestas del tapete
    THE_MAGICIAN = "THE_MAGICIAN"              # Transforma los números impares en pares por 1 tiro
    THE_MOON = "THE_MOON"                      # Transforma las casillas rojas en negras por 1 tiro
    THE_HERMIT = "THE_HERMIT"                  # Duplica tu banca (máximo +$40)
    TEMPERANCE = "TEMPERANCE"                  # Te paga el valor de venta de tus comodines (máx $50)
    JUDGEMENT = "JUDGEMENT"                    # Crea un comodín aleatorio (si hay espacio)
    HIGH_PRIESTESS = "HIGH_PRIESTESS"          # Sube 1 nivel a 2 manos aleatorias
    THE_EMPEROR = "THE_EMPEROR"                # Crea 2 cartas de tarot aleatorias (si hay espacio)
    THE_HANGED_MAN = "THE_HANGED_MAN"          # Quita 3 números NO apostados de la rueda por 1 tiro
    THE_STAR = "THE_STAR"                      # +1 multi en el siguiente giro


@dataclass(frozen=True)
class TarotCard:
    """Representa una Carta de Tarot consumible de uso único."""
    tarot_type: TarotType
    name: str
    description: str
    cost: int


TAROT_CATALOG: Dict[TarotType, TarotCard] = {
    TarotType.THE_SUN: TarotCard(
        tarot_type=TarotType.THE_SUN,
        name="El Sol",
        description="Transforma todas las casillas Negras en Rojas para el siguiente tiro.",
        cost=50,
    ),
    TarotType.THE_FOOL: TarotCard(
        tarot_type=TarotType.THE_FOOL,
        name="El Loco",
        description="Elimina la casilla del 0 verde de la rueda para el siguiente tiro.",
        cost=45,
    ),
    TarotType.WHEEL_OF_FORTUNE: TarotCard(
        tarot_type=TarotType.WHEEL_OF_FORTUNE,
        name="La Rueda de la Fortuna",
        description="Duplica el valor de todas las apuestas activas en el tapete.",
        cost=60,
    ),
    TarotType.THE_MAGICIAN: TarotCard(
        tarot_type=TarotType.THE_MAGICIAN,
        name="El Mago",
        description="Transforma todas las casillas Impares en Pares para el siguiente tiro.",
        cost=50,
    ),
    TarotType.THE_MOON: TarotCard(
        tarot_type=TarotType.THE_MOON,
        name="La Luna",
        description="Transforma todas las casillas Rojas en Negras para el siguiente tiro.",
        cost=50,
    ),
    TarotType.THE_HERMIT: TarotCard(
        tarot_type=TarotType.THE_HERMIT,
        name="El Ermitaño",
        description="Duplica tu banca actual (máximo +$40).",
        cost=45,
    ),
    TarotType.TEMPERANCE: TarotCard(
        tarot_type=TarotType.TEMPERANCE,
        name="La Templanza",
        description="Te paga el valor de venta de todos tus Comodines, sin venderlos (máximo $50).",
        cost=40,
    ),
    TarotType.JUDGEMENT: TarotCard(
        tarot_type=TarotType.JUDGEMENT,
        name="El Juicio",
        description="Crea un Comodín aleatorio (necesitas un espacio libre).",
        cost=60,
    ),
    TarotType.HIGH_PRIESTESS: TarotCard(
        tarot_type=TarotType.HIGH_PRIESTESS,
        name="La Sacerdotisa",
        description="Sube 1 nivel a 2 manos aleatorias (PAR, CERCO, ESCOLTA o VECINOS).",
        cost=55,
    ),
    TarotType.THE_EMPEROR: TarotCard(
        tarot_type=TarotType.THE_EMPEROR,
        name="El Emperador",
        description="Crea 2 cartas de Tarot aleatorias (si hay espacio en tu mano).",
        cost=50,
    ),
    TarotType.THE_HANGED_MAN: TarotCard(
        tarot_type=TarotType.THE_HANGED_MAN,
        name="El Colgado",
        description="Quita 3 números que NO apostaste de la rueda para el siguiente tiro.",
        cost=55,
    ),
    TarotType.THE_STAR: TarotCard(
        tarot_type=TarotType.THE_STAR,
        name="La Estrella",
        description="+1 multi en tu siguiente giro.",
        cost=45,
    ),
}


def tarot_sell_value(card: TarotCard) -> int:
    return card.cost // 2


@dataclass(frozen=True)
class TarotContext:
    """Lo que algunas cartas necesitan para actuar fuera de la rueda y el tapete."""
    economy: object = None          # EconomyManager
    joker_mgr: object = None        # JokerManager
    hand_mgr: object = None         # HandLevelManager
    rng: Optional[random.Random] = None
    on_star: Optional[object] = None  # callback(add_mult) para La Estrella


class TarotManager:
    """
    Gestiona la mano de Cartas de Tarot consumibles y sus efectos temporales
    sobre el tapete o la rueda para el siguiente tiro.
    """

    def __init__(self, max_hand_size: int = 3) -> None:
        if max_hand_size <= 0:
            raise ValueError("El tamaño máximo de la mano de Tarot debe ser mayor a 0.")
        self._max_hand_size: int = max_hand_size
        self._hand: List[TarotCard] = []
        self._wheel_modified_this_turn: bool = False

    @property
    def max_hand_size(self) -> int:
        return self._max_hand_size

    @property
    def hand(self) -> List[TarotCard]:
        return list(self._hand)

    @property
    def wheel_modified_this_turn(self) -> bool:
        return self._wheel_modified_this_turn

    def add_card(self, card: TarotCard) -> None:
        if len(self._hand) >= self._max_hand_size:
            raise ValueError(f"La mano de Tarot está llena (máximo {self._max_hand_size}).")
        self._hand.append(card)

    def add_card_by_type(self, tarot_type: TarotType) -> TarotCard:
        card = TAROT_CATALOG[tarot_type]
        self.add_card(card)
        return card

    def use_card(
        self,
        hand_index: int,
        wheel: RouletteWheel,
        board: BettingBoard,
        context: Optional[TarotContext] = None,
    ) -> TarotCard:
        """
        Consume la carta de Tarot en `hand_index` y aplica su modificación
        a la rueda o al tapete para el siguiente tiro.
        """
        if not (0 <= hand_index < len(self._hand)):
            raise IndexError("Índice de carta de Tarot fuera de rango.")

        card = self._hand[hand_index]
        ttype = card.tarot_type
        ctx = context if context is not None else TarotContext()
        rng = ctx.rng if ctx.rng is not None else random.Random()

        # Validaciones antes de consumir la carta (si falla, la carta vuelve a la mano)
        if ttype in (TarotType.THE_HERMIT, TarotType.TEMPERANCE) and ctx.economy is None:
            raise ValueError("Esta carta solo se puede usar durante la partida.")
        if ttype == TarotType.JUDGEMENT:
            if ctx.joker_mgr is None or len(ctx.joker_mgr.jokers) >= ctx.joker_mgr.max_slots:
                raise ValueError("No tienes espacio libre para un Comodín nuevo.")
        if ttype == TarotType.THE_EMPEROR and len(self._hand) - 1 >= self._max_hand_size:
            raise ValueError("Tu mano de Tarot está llena.")
        if ttype == TarotType.HIGH_PRIESTESS and ctx.hand_mgr is None:
            raise ValueError("Esta carta solo se puede usar durante la partida.")

        self._hand.pop(hand_index)

        if ttype == TarotType.THE_SUN:
            wheel.transform_color(Color.BLACK, Color.RED)
            self._wheel_modified_this_turn = True
        elif ttype == TarotType.THE_FOOL:
            wheel.remove_pocket(0)
            self._wheel_modified_this_turn = True
        elif ttype == TarotType.WHEEL_OF_FORTUNE:
            board.multiply_all_bets(2)
        elif ttype == TarotType.THE_MAGICIAN:
            wheel.transform_parity(Parity.ODD, Parity.EVEN)
            self._wheel_modified_this_turn = True
        elif ttype == TarotType.THE_MOON:
            wheel.transform_color(Color.RED, Color.BLACK)
            self._wheel_modified_this_turn = True
        elif ttype == TarotType.THE_HERMIT:
            ctx.economy.add_external_chips(min(ctx.economy.bankroll, 40))
        elif ttype == TarotType.TEMPERANCE:
            total = sum(ctx.joker_mgr.sell_value(i) for i in range(len(ctx.joker_mgr.jokers))) if ctx.joker_mgr else 0
            ctx.economy.add_external_chips(min(total, 50))
        elif ttype == TarotType.JUDGEMENT:
            ctx.joker_mgr.add_joker_by_type(rng.choice(list(JokerType)))
        elif ttype == TarotType.HIGH_PRIESTESS:
            from src.hands import HAND_CATALOG
            options = [hand for hand, info in HAND_CATALOG.items() if info.upgradable]
            for hand in rng.sample(options, 2):
                ctx.hand_mgr.upgrade(hand)
        elif ttype == TarotType.THE_EMPEROR:
            pool = [t for t in TarotType if t != TarotType.THE_EMPEROR]
            for _ in range(2):
                if len(self._hand) < self._max_hand_size:
                    self.add_card_by_type(rng.choice(pool))
        elif ttype == TarotType.THE_HANGED_MAN:
            covered = board.get_covered_numbers(wheel)
            candidates = [p.number for p in wheel.pockets if p.number not in covered]
            for number in rng.sample(candidates, min(3, len(candidates))):
                wheel.remove_pocket(number)
            self._wheel_modified_this_turn = True
        elif ttype == TarotType.THE_STAR:
            if ctx.on_star is not None:
                ctx.on_star(1.0)

        return card

    def sell_card(self, hand_index: int) -> TarotCard:
        """Quita la carta de la mano (el motor acredita su valor de venta)."""
        if not (0 <= hand_index < len(self._hand)):
            raise IndexError("Índice de carta de Tarot fuera de rango.")
        return self._hand.pop(hand_index)

    def cleanup_after_spin(self, wheel: RouletteWheel) -> None:
        """
        Restaura la rueda a su estado original si fue alterada temporalmente
        por una carta de Tarot en el tiro que acaba de concluir.
        """
        if self._wheel_modified_this_turn:
            wheel.reset_wheel()
            self._wheel_modified_this_turn = False
