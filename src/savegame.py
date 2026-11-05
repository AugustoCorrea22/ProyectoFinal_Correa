"""
Guardado y carga de partidas (JSON)
1. Partida: todo el estado del motor (banca, nivel, giros, comodines con su estado interno,
   tarots, fichas especiales, niveles de manos y apuestas, rachas, tienda, apuestas en la mesa,
   rueda alterada por Tarots y el generador aleatorio) para poder CONTINUAR al reabrir el juego.
2. Ajustes: preferencias del jugador (tutorial visto, Ruletín, dificultad).
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.board import Bet, BetType, SpecialChipType
from src.economy import GamePhase, ShopItem, ShopItemKind
from src.game_engine import Difficulty, RoguelikeRouletteEngine
from src.hands import HandType
from src.modifiers import JokerType, TarotType
from src.streaks import StreakState
from src.wheel import Color, Parity, Pocket

SAVE_VERSION = 1

_PAYLOAD_ENUMS = {
    ShopItemKind.JOKER: JokerType,
    ShopItemKind.TAROT: TarotType,
    ShopItemKind.SPECIAL_CHIP: SpecialChipType,
    ShopItemKind.BET_UPGRADE: BetType,
    ShopItemKind.HAND_UPGRADE: HandType,
}


def _target_to_json(bet: Bet) -> Any:
    return bet.target.value if hasattr(bet.target, "value") else int(bet.target)


def _target_from_json(bet_type: BetType, raw: Any) -> Any:
    if bet_type == BetType.COLOR:
        return Color(raw)
    if bet_type == BetType.PARITY:
        return Parity(raw)
    return int(raw)


def _rng_state_to_json(state) -> list:
    version, internal, gauss = state
    return [version, list(internal), gauss]


def _rng_state_from_json(raw) -> tuple:
    return (raw[0], tuple(raw[1]), raw[2])


def engine_to_dict(engine: RoguelikeRouletteEngine) -> Dict[str, Any]:
    econ = engine.economy
    return {
        "version": SAVE_VERSION,
        "difficulty": engine.difficulty.value,
        "economy": {
            "bankroll": econ._bankroll,
            "level_start_bankroll": econ._level_start_bankroll,
            "current_level": econ._current_level,
            "spins_used": econ._spins_used,
            "phase": econ._phase.value,
        },
        "jokers": [
            {"type": joker.joker_type.value, "state": dict(state)}
            for joker, state in zip(engine.jokers._slots, engine.jokers._state)
        ],
        "tarots": [card.tarot_type.value for card in engine.tarots._hand],
        "wheel_modified": engine.tarots._wheel_modified_this_turn,
        "wheel": [
            {"number": p.number, "color": p.color.value, "parity": p.parity.value if p.parity else None,
             "dozen": p.dozen, "column": p.column}
            for p in engine.wheel.pockets
        ],
        "special_chips": {chip.value: count for chip, count in engine.special_chips._inventory.items()},
        "bet_levels": {bt.value: lvl for bt, lvl in engine.upgrades._levels.items()},
        "hand_levels": {hand.value: lvl for hand, lvl in engine.hands._levels.items()},
        "streaks": {
            "wins": engine.streaks._consecutive_wins,
            "losses": engine.streaks._consecutive_losses,
            "state": engine.streaks._state.value,
        },
        "pending_star_mult": engine._pending_star_mult,
        "bets": [
            {"type": b.bet_type.value, "target": _target_to_json(b), "amount": b.amount,
             "special": b.special_chip.value if b.special_chip else None}
            for b in engine.board.bets
        ],
        "shop": [
            {"kind": item.kind.value, "payload": item.payload.value, "name": item.name,
             "description": item.description, "cost": item.cost}
            for item in engine.shop.offers
        ],
        "rng": _rng_state_to_json(engine.rng.getstate()),
    }


def engine_from_dict(data: Dict[str, Any]) -> RoguelikeRouletteEngine:
    if data.get("version") != SAVE_VERSION:
        raise ValueError("La partida guardada es de una versión distinta del juego.")
    engine = RoguelikeRouletteEngine(difficulty=Difficulty(data["difficulty"]))
    engine.rng.setstate(_rng_state_from_json(data["rng"]))

    econ = engine.economy
    e = data["economy"]
    econ._bankroll = int(e["bankroll"])
    econ._level_start_bankroll = int(e["level_start_bankroll"])
    econ._current_level = int(e["current_level"])
    econ._spins_used = int(e["spins_used"])
    econ._phase = GamePhase(e["phase"])
    engine.bosses.setup_level_boss(econ.current_level)

    for entry in data["jokers"]:
        engine.jokers.add_joker_by_type(JokerType(entry["type"]))
        engine.jokers._state[-1].update({k: int(v) for k, v in entry["state"].items()})
    for tarot in data["tarots"]:
        engine.tarots.add_card_by_type(TarotType(tarot))

    if data.get("wheel_modified"):
        engine.wheel._pockets = {
            p["number"]: Pocket(
                number=p["number"],
                color=Color(p["color"]),
                parity=Parity(p["parity"]) if p["parity"] else None,
                dozen=p["dozen"],
                column=p["column"],
            )
            for p in data["wheel"]
        }
        engine.tarots._wheel_modified_this_turn = True

    for chip, count in data["special_chips"].items():
        engine.special_chips._inventory[SpecialChipType(chip)] = int(count)
    for bt, lvl in data["bet_levels"].items():
        engine.upgrades._levels[BetType(bt)] = int(lvl)
    for hand, lvl in data["hand_levels"].items():
        engine.hands._levels[HandType(hand)] = int(lvl)

    s = data["streaks"]
    engine.streaks._consecutive_wins = int(s["wins"])
    engine.streaks._consecutive_losses = int(s["losses"])
    engine.streaks._state = StreakState(s["state"])
    engine._pending_star_mult = float(data.get("pending_star_mult", 0.0))

    # Las fichas de la mesa ya estaban descontadas de la banca al guardarse
    for b in data["bets"]:
        bet_type = BetType(b["type"])
        engine.board.place_bet(
            bet_type,
            _target_from_json(bet_type, b["target"]),
            int(b["amount"]),
            special_chip=SpecialChipType(b["special"]) if b["special"] else None,
        )

    engine.shop.set_custom_offers([
        ShopItem(
            kind=ShopItemKind(item["kind"]),
            payload=_PAYLOAD_ENUMS[ShopItemKind(item["kind"])](item["payload"]),
            name=item["name"],
            description=item["description"],
            cost=int(item["cost"]),
        )
        for item in data["shop"]
    ])
    return engine


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Escritura atómica: si el juego se cierra a mitad, no queda un archivo roto."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def save_game(path: Path, engine: RoguelikeRouletteEngine) -> None:
    if engine.economy.phase == GamePhase.GAME_OVER:
        delete_save(path)
        return
    _write_json(path, engine_to_dict(engine))


def load_game(path: Path) -> Optional[RoguelikeRouletteEngine]:
    """Devuelve la partida guardada, o None si no hay una válida."""
    try:
        return engine_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def read_save_summary(path: Path) -> Optional[Dict[str, int]]:
    """Datos mínimos para mostrar en el menú sin cargar toda la partida."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"level": int(data["economy"]["current_level"]), "bankroll": int(data["economy"]["bankroll"])}
    except Exception:
        return None


def delete_save(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def load_settings(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(path: Path, settings: Dict[str, Any]) -> None:
    try:
        _write_json(path, settings)
    except OSError:
        pass
