"""
Fase 5: Mecánicas de Crupieres Jefe (BossManager)
Agrega eventos de obstáculo cada cierta cantidad de niveles (rondas clave):
1. Modificador de Reglas para Jefes:
   - El Purista (THE_PURIST): Prohíbe todas las apuestas externas (Color, Par/Impar,
     Docenas, Columnas), obligando a jugar solo Plenos.
   - El Cronometrador (THE_CHRONOMANCER): Impone un límite de tiempo real por turno
     medido con el módulo `time`.
2. Eventos de Castigo:
   - El Devorador Verde (THE_GREEN_DEVOURER): Destruye un Comodín pasivo del jugador
     cuando la bola cae en el 0 verde.
   - El Recaudador (THE_TAX_COLLECTOR): Confisca un porcentaje de las fichas en reserva
     cada vez que el jugador falla un tiro.
"""

from dataclasses import dataclass
from enum import Enum
import random
import time
from typing import Dict, List, Optional, Set
from src.board import BetType
from src.economy import EconomyManager
from src.modifiers import Joker, JokerManager
from src.payout import SpinEvaluation


class BossType(str, Enum):
    THE_PURIST = "THE_PURIST"                  # Prohíbe apuestas externas
    THE_CHRONOMANCER = "THE_CHRONOMANCER"      # Impone límite de tiempo por turno
    THE_GREEN_DEVOURER = "THE_GREEN_DEVOURER"  # Destruye comodines cuando sale el 0 verde
    THE_TAX_COLLECTOR = "THE_TAX_COLLECTOR"    # Cobra impuesto de fichas al perder un tiro


OUTSIDE_BET_TYPES: Set[BetType] = {
    BetType.COLOR,
    BetType.PARITY,
}


@dataclass(frozen=True)
class BossCroupier:
    """Define un Crupier Jefe con sus restricciones de reglas y eventos de castigo."""
    boss_type: BossType
    name: str
    description: str
    forbidden_bets: Set[BetType]
    time_limit_seconds: Optional[float] = None


BOSS_CATALOG: Dict[BossType, BossCroupier] = {
    BossType.THE_PURIST: BossCroupier(
        boss_type=BossType.THE_PURIST,
        name="Don Vittore, El Purista",
        description="Prohíbe apuestas simples 1:1 (Rojo/Negro y Par/Impar). Permite Docenas, Columnas y Plenos.",
        forbidden_bets=OUTSIDE_BET_TYPES,
        time_limit_seconds=None,
    ),
    BossType.THE_CHRONOMANCER: BossCroupier(
        boss_type=BossType.THE_CHRONOMANCER,
        name="Madame Chronos",
        description="Impone un límite estricto de 12 segundos para colocar apuestas y girar.",
        forbidden_bets=set(),
        time_limit_seconds=12.0,
    ),
    BossType.THE_GREEN_DEVOURER: BossCroupier(
        boss_type=BossType.THE_GREEN_DEVOURER,
        name="Barón Cero, El Devorador",
        description="Destruye uno de tus Comodines equipados cada vez que sale el 0 verde.",
        forbidden_bets=set(),
        time_limit_seconds=None,
    ),
    BossType.THE_TAX_COLLECTOR: BossCroupier(
        boss_type=BossType.THE_TAX_COLLECTOR,
        name="El Inquisidor de Fichas",
        description="Confisca el 20% de tus fichas en reserva cada vez que fallas un tiro.",
        forbidden_bets=set(),
        time_limit_seconds=None,
    ),
}


@dataclass(frozen=True)
class BossPunishmentReport:
    """Registro en memoria de las acciones punitivas aplicadas por un Crupier Jefe."""
    boss_active: Optional[BossType]
    punishment_triggered: bool
    destroyed_joker: Optional[Joker]
    chips_confiscated: int
    time_expired: bool
    message: str


class BossManager:
    """
    Gestiona la aparición de Crupieres Jefe cada N niveles, valida sus restricciones
    de apuestas/tiempo y ejecuta sus eventos de castigo tras cada giro.
    """

    def __init__(
        self,
        boss_interval: int = 3,
        rng: Optional[random.Random] = None,
    ) -> None:
        if boss_interval <= 0:
            raise ValueError("El intervalo de niveles para Jefes debe ser mayor a 0.")
        self._boss_interval: int = boss_interval
        self._rng: random.Random = rng if rng is not None else random.Random()
        self._active_boss: Optional[BossCroupier] = None
        self._turn_start_time: Optional[float] = None

    @property
    def active_boss(self) -> Optional[BossCroupier]:
        return self._active_boss

    @property
    def is_boss_active(self) -> bool:
        return self._active_boss is not None

    def is_boss_level(self, level: int) -> bool:
        """Determina si el nivel actual corresponde a una ronda clave de Jefe."""
        return level > 0 and (level % self._boss_interval == 0)

    def setup_level_boss(
        self,
        level: int,
        forced_boss: Optional[BossType] = None,
    ) -> Optional[BossCroupier]:
        """
        Activa un Crupier Jefe si el nivel es múltiplo de `boss_interval`
        (o si se fuerza uno para pruebas), o limpia el jefe activo en niveles normales.
        """
        if forced_boss is not None:
            self._active_boss = BOSS_CATALOG[forced_boss]
            self.start_turn_timer()
            return self._active_boss

        if self.is_boss_level(level):
            rotation: List[BossType] = list(BossType)
            index = ((level // self._boss_interval) - 1) % len(rotation)
            self._active_boss = BOSS_CATALOG[rotation[index]]
            self.start_turn_timer()
            return self._active_boss

        self._active_boss = None
        self._turn_start_time = None
        return None

    def start_turn_timer(self) -> None:
        """Inicia el cronómetro del turno actual usando `time.perf_counter()`."""
        self._turn_start_time = time.perf_counter()

    def get_elapsed_turn_time(self) -> float:
        """Devuelve los segundos transcurridos desde que inició el turno."""
        if self._turn_start_time is None:
            return 0.0
        return max(0.0, time.perf_counter() - self._turn_start_time)

    def is_turn_time_expired(self, simulated_elapsed: Optional[float] = None) -> bool:
        """
        Verifica si se agotó el límite de tiempo impuesto por el Jefe activo.
        Permite inyectar `simulated_elapsed` para pruebas deterministas instantáneas.
        """
        if self._active_boss is None or self._active_boss.time_limit_seconds is None:
            return False
        elapsed = (
            simulated_elapsed
            if simulated_elapsed is not None
            else self.get_elapsed_turn_time()
        )
        return elapsed > self._active_boss.time_limit_seconds

    def validate_bet_allowed(
        self,
        bet_type: BetType,
        simulated_elapsed: Optional[float] = None,
    ) -> None:
        """
        Valida si el Crupier Jefe actual permite registrar una apuesta de tipo `bet_type`
        y si el jugador sigue dentro del límite de tiempo del turno.
        """
        if self._active_boss is None:
            return

        if self.is_turn_time_expired(simulated_elapsed=simulated_elapsed):
            raise TimeoutError(
                f"¡Tiempo agotado! {self._active_boss.name} bloqueó tus apuestas por exceder "
                f"los {self._active_boss.time_limit_seconds}s."
            )

        if bet_type in self._active_boss.forbidden_bets:
            raise PermissionError(
                f"Regla de Jefe activa ({self._active_boss.name}): "
                f"La apuesta {bet_type.value} está prohibida en este nivel."
            )

    def apply_post_spin_punishment(
        self,
        evaluation: SpinEvaluation,
        joker_mgr: JokerManager,
        economy: EconomyManager,
        simulated_elapsed: Optional[float] = None,
    ) -> BossPunishmentReport:
        """
        Ejecuta las acciones punitivas del Crupier Jefe tras resolver el giro:
        - THE_GREEN_DEVOURER: Si salió el 0 verde, destruye un Comodín pasivo.
        - THE_TAX_COLLECTOR: Si ninguna apuesta ganó, confisca 20% de las fichas en reserva.
        - THE_CHRONOMANCER: Si se giró fuera de tiempo, confisca 25% de las fichas como multa.
        """
        if self._active_boss is None:
            return BossPunishmentReport(
                boss_active=None,
                punishment_triggered=False,
                destroyed_joker=None,
                chips_confiscated=0,
                time_expired=False,
                message="Sin jefe activo.",
            )

        btype = self._active_boss.boss_type
        time_expired = self.is_turn_time_expired(simulated_elapsed=simulated_elapsed)

        # Reiniciamos el reloj para el próximo turno del nivel
        self.start_turn_timer()

        # 1. Castigo del Devorador Verde: destruye comodín al salir el 0 verde
        if btype == BossType.THE_GREEN_DEVOURER and evaluation.winning_pocket.number == 0:
            destroyed = joker_mgr.destroy_random_joker(rng=self._rng)
            if destroyed is not None:
                return BossPunishmentReport(
                    boss_active=btype,
                    punishment_triggered=True,
                    destroyed_joker=destroyed,
                    chips_confiscated=0,
                    time_expired=False,
                    message=f"¡{self._active_boss.name} destruyó tu comodín '{destroyed.name}' por salir el 0 verde!",
                )
            return BossPunishmentReport(
                boss_active=btype,
                punishment_triggered=False,
                destroyed_joker=None,
                chips_confiscated=0,
                time_expired=False,
                message="Salió el 0 verde, pero no tenías comodines para destruir.",
            )

        # 2. Castigo del Recaudador: impuesto del 20% al fallar un tiro
        if btype == BossType.THE_TAX_COLLECTOR and not evaluation.any_bet_won:
            tax = int(economy.bankroll * 0.20)
            if tax > 0:
                economy.deduct_chips(tax)
            return BossPunishmentReport(
                boss_active=btype,
                punishment_triggered=tax > 0,
                destroyed_joker=None,
                chips_confiscated=tax,
                time_expired=False,
                message=f"¡{self._active_boss.name} confiscó {tax} fichas por tiro fallido!",
            )

        # 3. Castigo del Cronometrador si el giro ocurrió con el tiempo vencido
        if btype == BossType.THE_CHRONOMANCER and time_expired:
            penalty = int(economy.bankroll * 0.25)
            if penalty > 0:
                economy.deduct_chips(penalty)
            return BossPunishmentReport(
                boss_active=btype,
                punishment_triggered=True,
                destroyed_joker=None,
                chips_confiscated=penalty,
                time_expired=True,
                message=f"¡Excediste el tiempo límite! {self._active_boss.name} te multó con {penalty} fichas.",
            )

        return BossPunishmentReport(
            boss_active=btype,
            punishment_triggered=False,
            destroyed_joker=None,
            chips_confiscated=0,
            time_expired=time_expired,
            message="Sin castigo en este tiro.",
        )
