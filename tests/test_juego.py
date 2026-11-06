"""
Pruebas de las reglas del juego (no abren ventanas). Ejecutar con:
    py -m unittest discover -s tests
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audio import SoundManager
from src.board import BetType, BettingBoard
from src.economy import GamePhase
from src.game_engine import Difficulty, RoguelikeRouletteEngine
from src.hands import HandType, detect_hand
from src.mezclador import _Mixer
from src.modifiers import JokerType, TarotType
from src.savegame import engine_from_dict, engine_to_dict, load_game, save_game
from src.wheel import Color, Parity, RouletteWheel


class TestManos(unittest.TestCase):
    def _hand(self, *bets) -> HandType:
        board = BettingBoard()
        for bet_type, target in bets:
            board.place_bet(bet_type, target, 10)
        return detect_hand(board, RouletteWheel())

    def test_manos_basicas(self) -> None:
        self.assertEqual(self._hand((BetType.COLOR, Color.RED)), HandType.SIMPLE)
        self.assertEqual(self._hand((BetType.COLOR, Color.RED), (BetType.PARITY, Parity.ODD)), HandType.PAR)
        self.assertEqual(
            self._hand((BetType.COLOR, Color.RED), (BetType.PARITY, Parity.ODD), (BetType.DOZEN, 1)), HandType.CERCO
        )
        self.assertEqual(
            self._hand((BetType.STRAIGHT, 32), (BetType.STRAIGHT, 15), (BetType.STRAIGHT, 19)), HandType.VECINOS
        )

    def test_cerco_duplica_al_acertar_las_tres(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1)
        for bet_type, target in ((BetType.COLOR, Color.RED), (BetType.PARITY, Parity.ODD), (BetType.DOZEN, 1)):
            engine.place_bet(bet_type, target, 10)
        report = engine.spin_wheel(forced_pocket=engine.wheel.get_pocket(1))
        self.assertEqual((report.score.chips, report.score.total_mult, report.score.gain), (40, 2.0, 80))


class TestReglasDeMesa(unittest.TestCase):
    def test_apuestas_opuestas_se_bloquean(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1)
        engine.place_bet(BetType.COLOR, Color.RED, 10)
        with self.assertRaises(ValueError):
            engine.place_bet(BetType.COLOR, Color.BLACK, 10)
        self.assertEqual(engine.economy.bankroll, 110)  # la apuesta rechazada no cobra

    def test_mesa_vip_da_una_casilla_mas(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1)
        self.assertEqual(engine.board.max_active_bets, 5)
        engine.jokers.add_joker_by_type(JokerType.VIP_TABLE)
        engine.sync_board_limits()
        self.assertEqual(engine.board.max_active_bets, 6)
        engine.sell_joker(0)
        self.assertEqual(engine.board.max_active_bets, 5)

    def test_combo_secreto_del_8_fuerza_el_8(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1)
        for bet_type, target in (
            (BetType.STRAIGHT, 8), (BetType.COLOR, Color.BLACK), (BetType.PARITY, Parity.EVEN),
            (BetType.DOZEN, 1), (BetType.COLUMN, 2),
        ):
            engine.place_bet(bet_type, target, 5)
        self.assertEqual(engine.spin_wheel().winning_pocket.number, 8)


class TestDificultadYEconomia(unittest.TestCase):
    def test_facil_devuelve_la_mitad(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1, difficulty=Difficulty.EASY)
        engine.place_bet(BetType.COLOR, Color.RED, 20)
        report = engine.spin_wheel(forced_pocket=engine.wheel.get_pocket(2))
        self.assertEqual(report.loss_refund, 10)

    def test_vender_no_cuenta_como_ganancia(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1)
        engine.jokers.add_joker_by_type(JokerType.STRAIGHT_SNIPER)
        self.assertEqual(engine.sell_joker(0), 75)
        self.assertEqual(engine.economy.level_profit, 0)

    def test_tarot_que_no_se_puede_usar_no_se_pierde(self) -> None:
        engine = RoguelikeRouletteEngine(seed=1)
        engine.tarots.add_card_by_type(TarotType.JUDGEMENT)
        for _ in range(engine.jokers.max_slots):
            engine.jokers.add_joker_by_type(JokerType.RED_RUBY)
        with self.assertRaises(ValueError):
            engine.use_tarot_card(0)
        self.assertEqual(len(engine.tarots.hand), 1)


class TestGuardado(unittest.TestCase):
    def test_ida_y_vuelta_conserva_todo(self) -> None:
        original = RoguelikeRouletteEngine(seed=11, difficulty=Difficulty.EASY)
        original.jokers.add_joker_by_type(JokerType.VIP_TABLE)
        original.jokers.add_joker_by_type(JokerType.MELTING_ICE)
        original.place_bet(BetType.COLOR, Color.RED, 10)
        original.spin_wheel(forced_pocket=original.wheel.get_pocket(3))
        original.place_bet(BetType.PARITY, Parity.ODD, 20)
        copia = engine_from_dict(json.loads(json.dumps(engine_to_dict(original))))
        self.assertEqual(engine_to_dict(copia), engine_to_dict(original))
        self.assertEqual(copia.board.max_active_bets, 6)
        self.assertEqual(original.spin_wheel().winning_pocket.number, copia.spin_wheel().winning_pocket.number)

    def test_partida_perdida_se_borra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partida.json"
            engine = RoguelikeRouletteEngine(seed=2)
            save_game(path, engine)
            self.assertIsNotNone(load_game(path))
            engine.economy._phase = GamePhase.GAME_OVER
            save_game(path, engine)
            self.assertFalse(path.exists())


class TestAudio(unittest.TestCase):
    def test_efectos_en_memoria(self) -> None:
        manager = SoundManager(Path("."), Path("."), enabled=False)
        manager.generate_sounds()
        self.assertEqual(len(manager._sounds), 21)

    def test_mezcla_suma_y_recorta(self) -> None:
        from array import array
        mixer = _Mixer()
        loud = array("h", [30000] * 8)
        mixer.play_voice(array("h", [1000] * 8), 0.5)
        mixer.play_voice(loud, 1.0)
        self.assertEqual(mixer.mix(2)[0], 30500)
        mixer.play_voice(loud, 1.0)
        self.assertEqual(mixer.mix(2)[0], 32767)


if __name__ == "__main__":
    unittest.main()
