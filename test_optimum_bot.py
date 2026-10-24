import threading
import unittest
from unittest import mock

import requests

from optimum_bot import SETUP_RULES, Bot, extract_codes, mint_calldata, order_codes


class Ordering(unittest.TestCase):
    def test_single(self):
        self.assertEqual(order_codes(['A']), ['A'])

    def test_starts_75_percent_down_then_climbs(self):
        codes = ['C%d' % i for i in range(8)]
        self.assertEqual(order_codes(codes), ['C6', 'C7', 'C5', 'C4', 'C3', 'C2', 'C1', 'C0'])

    def test_every_code_tried_once(self):
        codes = ['C%d' % i for i in range(13)]
        self.assertEqual(sorted(order_codes(codes)), sorted(codes))


class Extraction(unittest.TestCase):
    def test_code_list_post(self):
        text = ('First batch of TurboCards access codes are LIVE! FCFS\n\n'
                'TURBO-7X2K\nOPT4M9Q2\nab12cd34\n\nMint: https://turbocards.getoptimum.xyz/optimum @get_optimum')
        strong, weak = extract_codes(text)
        self.assertEqual(strong, ['TURBO-7X2K', 'OPT4M9Q2', 'ab12cd34'])
        self.assertNotIn('LIVE', weak)

    def test_ignores_times_dates_links(self):
        strong, weak = extract_codes('Codes drop Friday 10/23 at 12PM ET, 3pm UTC. https://x.com/abc123def')
        self.assertEqual(strong, [])
        self.assertEqual(weak, [])

    def test_announcement_post_has_no_strong_codes(self):
        text = ('Some codes are being sent out to our early supporters, others will be released over twitter & '
                'Discord drops.\n\nMint page: https://turbocards.getoptimum.xyz/optimum\nDiscord: '
                'https://discord.gg/4m2QKpGW8g\n\nThe first set of access codes drops this Friday 10/23')
        self.assertEqual(extract_codes(text)[0], [])

    def test_all_caps_word_is_weak(self):
        self.assertEqual(extract_codes('Code: SPEEDRUN'), ([], ['SPEEDRUN']))

    def test_drop_1_post_finds_all_20_codes(self):
        # Real Drop #1 post; 3 of its codes are letters-only mixed case and were missed.
        codes = ['ZZm7dvp2Mnza', 'J-SHWlW_9VUL', 'xOqFybMt32jQ', 'mkCiMgbE19Ut', 'btjzW8-c6g5G', 'DiR0wmmMB8Ue',
                 'YhtfxV-11x3U', 'wGwHjwEXNE8u', '0QAhFtKCfJpu', 'AGE2zDSTNKvK', 'dozMopS2KkAA', 'Ve3wla_zgpac',
                 'RYV20H9Nzc5P', 'Qr8I0C492mcl', 'YUlxl-EhvegG', 'JhRTQ_ed1FGC', '5DBDZKa0iPo7', 'ewMTEfknxu_M',
                 'l2KUSisEp7El', 'ixcKpRTDJZWG']
        text = 'Drop number 1. 20 TurboCard codes. First come, first served.\n\n' + '\n'.join(codes)
        self.assertEqual(extract_codes(text), (codes, []))

    def test_camel_case_words_are_not_codes(self):
        self.assertEqual(extract_codes('Reminder: Soulbound cards, MetaMask or LayerZero wallets'), ([], []))


def fake_bot(http_codes, rule_statuses=()):
    """Bot with Snag stubbed out: each code attempt returns the next HTTP code, each rule poll the next status."""
    bot = Bot.__new__(Bot)
    bot.state, bot.claim_lock, bot.tried = {}, threading.Lock(), []
    http_codes, rule_statuses = list(http_codes), list(rule_statuses)

    def try_code(code):
        bot.tried.append(code)
        return code, http_codes.pop(0), {}
    bot.try_code = try_code
    bot.wait_rule = lambda *a: {'status': rule_statuses.pop(0) if rule_statuses else 'completed'}
    bot.ensure_minted = bot.save = lambda: None
    return bot


@mock.patch('optimum_bot.log')
class Claiming(unittest.TestCase):
    CODES = (['A', 'B', 'C', 'D'], [])  # order_codes tries D, C, B, A

    def test_stops_after_first_accepted_code(self, _):
        bot = fake_bot([200, 200, 200])
        bot.claim(self.CODES, 'post')
        self.assertEqual(bot.tried, ['D'])
        self.assertEqual(bot.state['code_claimed']['codes'], ['D'])

    def test_rejected_code_moves_to_next(self, _):
        bot = fake_bot([400, 200])
        bot.claim(self.CODES, 'post')
        self.assertEqual(bot.tried, ['D', 'C'])

    def test_code_used_up_while_queued_moves_to_next(self, _):
        bot = fake_bot([200, 200], ['failed', 'completed'])
        bot.claim(self.CODES, 'post')
        self.assertEqual(bot.state['code_claimed']['codes'], ['C'])

    @mock.patch('optimum_bot.time.sleep')
    def test_rate_limit_waits_then_retries_same_code(self, sleep, _):
        bot = fake_bot([429, 200])
        bot.claim(self.CODES, 'post')
        self.assertEqual(bot.tried, ['D', 'D'])
        sleep.assert_called_once_with(61)


@mock.patch('optimum_bot.log')
class SetupTasks(unittest.TestCase):
    def bot(self, complete_rule):
        bot = Bot.__new__(Bot)
        bot.state, bot.complete_rule = {}, complete_rule
        bot.wait_rule = lambda *a: {}
        bot.save = lambda: None
        return bot

    def test_each_task_posted_once_even_if_never_completed(self, _):
        posts = []
        bot = self.bot(lambda rule, body=None: posts.append(rule) or (400, {'message': 'Quest not completed'}))
        bot.setup_tasks()
        bot.setup_tasks()  # restart
        self.assertEqual(sorted(posts), sorted(SETUP_RULES.values()))

    def test_task_retried_if_request_got_no_response(self, _):
        def offline(rule, body=None):
            raise requests.ConnectionError('offline')
        bot = self.bot(offline)
        with self.assertRaises(requests.ConnectionError):
            bot.setup_tasks()
        self.assertEqual(bot.state.get('setup', {}), {})


class Calldata(unittest.TestCase):
    def test_matches_real_mint_tx(self):
        data, value = mint_calldata(REAL_SIGNED, REAL_MINTER)
        self.assertEqual(data[:4].hex(), '2c4510f8')
        self.assertEqual(value, 0)
        self.assertEqual('0x' + data.hex(), REAL_INPUT)

    def test_refuses_paid_mint(self):
        paid = {**REAL_SIGNED, 'payload': {**REAL_SIGNED['payload'], 'price': '1000000000000000'}}
        with self.assertRaises(ValueError):
            mint_calldata(paid, REAL_MINTER)

    def test_refuses_mint_to_another_wallet(self):
        with self.assertRaises(ValueError):
            mint_calldata(REAL_SIGNED, '0x' + '11' * 20)


# Mint tx 0xfa480ed7... on mainnet, 23 Oct 2026.
REAL_MINTER = '0xA685932ef2e06a330FcC20499b01E763025Cec5f'
REAL_SIGNED = {'payload': {
    'to': REAL_MINTER,
    'royaltyRecipient': '0x99f87aF396cFb887e1e7B67a0a584922Ccc9e2a5', 'royaltyBps': '0',
    'primarySaleRecipient': '0x99f87aF396cFb887e1e7B67a0a584922Ccc9e2a5',
    'uri': 'ipfs://QmTfqXFNwDWsv2JyEw8NzttSp31A2VRart2LGF2JKP3pfD/0', 'price': '0',
    'currency': '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE', 'validityStartTimestamp': 0,
    'validityEndTimestamp': 1792766676,
    'uid': '0x67a8b72d46f70cc8f5b32d5dfd1fa9fe1dfd63eb31d9fe565a6cff8843c41a74'},
    'signature': '0xb3da37a5082569062d64c7cd38949f30fcf8faa129f28ac73cbb9cecb37ccea97fecc5ec0ed53d3d81f0364874fa044a67fff58074fd72eb5633fe265cb81d041c'}


REAL_INPUT = (
    '0x2c4510f8000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000001e0000000000000000000000000a685932ef2e06a330fcc20499b01e763025cec5f00000000000000000000000099f87af396cfb887e1e7b67a0a584922ccc9e2a5000000000000000000000000000000000000000000000000000000000000000000000000000000000000000099f87af396cfb887e1e7b67a0a584922ccc9e2a500000000000000000000000000000000000000000000000000000000000001400000000000000000000000000000000000000000000000000000000000000000000000000000000000000000eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000006adb72d467a8b72d46f70cc8f5b32d5dfd1fa9fe1dfd63eb31d9fe565a6cff8843c41a740000000000000000000000000000000000000000000000000000000000000037697066733a2f2f516d54667158464e7744577376324a794577384e7a74745370333141325652617274324c4746324a4b50337066442f300000000000000000000000000000000000000000000000000000000000000000000000000000000041b3da37a5082569062d64c7cd38949f30fcf8faa129f28ac73cbb9cecb37ccea97fecc5ec0ed53d3d81f0364874fa044a67fff58074fd72eb5633fe265cb81d041c00000000000000000000000000000000000000000000000000000000000000')

if __name__ == '__main__':
    unittest.main()
