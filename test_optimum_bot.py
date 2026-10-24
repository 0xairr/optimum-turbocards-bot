import unittest

from optimum_bot import extract_codes, mint_calldata, order_codes


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


class Calldata(unittest.TestCase):
    def test_matches_real_mint_tx(self):
        # Mint tx 0xfa480ed7... on mainnet, 23 Oct 2026.
        signed = {'payload': {
            'to': '0xA685932ef2e06a330FcC20499b01E763025Cec5f',
            'royaltyRecipient': '0x99f87aF396cFb887e1e7B67a0a584922Ccc9e2a5', 'royaltyBps': '0',
            'primarySaleRecipient': '0x99f87aF396cFb887e1e7B67a0a584922Ccc9e2a5',
            'uri': 'ipfs://QmTfqXFNwDWsv2JyEw8NzttSp31A2VRart2LGF2JKP3pfD/0', 'price': '0',
            'currency': '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE', 'validityStartTimestamp': 0,
            'validityEndTimestamp': 1792766676,
            'uid': '0x67a8b72d46f70cc8f5b32d5dfd1fa9fe1dfd63eb31d9fe565a6cff8843c41a74'},
            'signature': '0xb3da37a5082569062d64c7cd38949f30fcf8faa129f28ac73cbb9cecb37ccea97fecc5ec0ed53d3d81f0364874fa044a67fff58074fd72eb5633fe265cb81d041c'}
        data, value = mint_calldata(signed)
        self.assertEqual(data[:4].hex(), '2c4510f8')
        self.assertEqual(value, 0)
        self.assertEqual('0x' + data.hex(), REAL_INPUT)


REAL_INPUT = (
    '0x2c4510f8000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000001e0000000000000000000000000a685932ef2e06a330fcc20499b01e763025cec5f00000000000000000000000099f87af396cfb887e1e7b67a0a584922ccc9e2a5000000000000000000000000000000000000000000000000000000000000000000000000000000000000000099f87af396cfb887e1e7b67a0a584922ccc9e2a500000000000000000000000000000000000000000000000000000000000001400000000000000000000000000000000000000000000000000000000000000000000000000000000000000000eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000006adb72d467a8b72d46f70cc8f5b32d5dfd1fa9fe1dfd63eb31d9fe565a6cff8843c41a740000000000000000000000000000000000000000000000000000000000000037697066733a2f2f516d54667158464e7744577376324a794577384e7a74745370333141325652617274324c4746324a4b50337066442f300000000000000000000000000000000000000000000000000000000000000000000000000000000041b3da37a5082569062d64c7cd38949f30fcf8faa129f28ac73cbb9cecb37ccea97fecc5ec0ed53d3d81f0364874fa044a67fff58074fd72eb5633fe265cb81d041c00000000000000000000000000000000000000000000000000000000000000')

if __name__ == '__main__':
    unittest.main()
