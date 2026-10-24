"""Optimum TurboCards FCFS auto-claimer.

Watches @get_optimum through FxTwitter, enters any posted access code on the
Snag-hosted mint page (SIWE wallet session), then mints the soulbound
TurboCard on Ethereum mainnet with the signature Snag returns.
"""
import datetime as dt
import json
import pathlib
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address

ROOT = pathlib.Path(__file__).resolve().parent
WALLET = ROOT / 'wallet.json'
STATE = ROOT / 'state.json'
EVENTS = ROOT / 'events.jsonl'
STATUS = ROOT / 'status.json'

HOST = 'turbocards.getoptimum.xyz'
SITE = 'https://' + HOST
WEBSITE_ID = '968381c2-3ab3-4aa1-a375-a4c226d6e7fc'
ORG_ID = '748e781c-1042-49fc-a494-c7d8d674dcf4'
SCOPE = {'websiteId': WEBSITE_ID, 'organizationId': ORG_ID}
CODE_RULE = '87ed718c-8137-43dd-9669-5fef6c1bfe6b'
SETUP_RULES = {'connect_wallet': '07db3ab4-c743-4bab-8d2a-d0e929375d86',
               'follow_optimum': '9cc29f0a-29d7-4209-92a1-5f385ec730fc'}
CONTRACT_ID = 'b79f779c-e0ee-41a7-b232-8c847384c1f6'
CONTRACT = to_checksum_address('0x29b763df6548920151a60116e502591c33f0241e')
SIWE_STATEMENT = 'Sign in to the app. Powered by Snag Solutions.'

HANDLE = 'get_optimum'
FEED = 'https://api.fxtwitter.com/2/profile/%s/statuses?count=20' % HANDLE
POLL_THREADS = 2          # staggered pollers -> one fresh read roughly every POLL_INTERVAL/2
POLL_INTERVAL = 1.2
FRESH_SECONDS = 600       # ignore posts older than this (restarts, old reposts)

RPCS = ['https://ethereum-rpc.publicnode.com', 'https://eth.llamarpc.com',
        'https://rpc.mevblocker.io', 'https://eth.drpc.org']
MINT_FN = 'mintWithSignature((address,address,uint256,address,string,uint256,address,uint128,uint128,bytes32),bytes)'
MINT_REQ = '(address,address,uint256,address,string,uint256,address,uint128,uint128,bytes32)'
GAS_LIMIT = 400_000
PRIORITY_WEI = 2 * 10**9

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

STOP = {'OPTIMUM', 'TURBO', 'TURBOCARD', 'TURBOCARDS', 'CARDS', 'MINT', 'MINTING', 'CODE', 'CODES',
        'FCFS', 'DISCORD', 'TWITTER', 'LIVE', 'DROP', 'DROPS', 'FIRST', 'COME', 'SERVED', 'ACCESS',
        'ETHEREUM', 'MAINNET', 'WEB3', 'HTTPS', 'TODAY', 'FRIDAY', 'NOW', 'HERE', 'THESE', 'THOSE',
        'GOOD', 'LUCK', 'ENJOY', 'THANKS', 'THANK', 'BELOW', 'ABOVE', 'USE', 'ENTER', 'LINK', 'NFTS'}
TIME_LIKE = re.compile(r'^\d{1,2}(:\d{2})?(AM|PM|UTC|ET|EST|PST)?$', re.I)
WORD_LIKE = re.compile(r'[A-Z]?[a-z]+(?:[A-Z][a-z]+)*')  # Friday, MetaMask, getOptimum


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(kind, **data):
    row = {'at': now(), 'kind': kind, **data}
    line = json.dumps(row, ensure_ascii=False)
    print(line, flush=True)
    with EVENTS.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


# ---------------------------------------------------------------- codes

def extract_codes(text):
    """Return (strong, weak) code candidates in posting order."""
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'[@#$]\w+', ' ', text)
    strong, weak = [], []
    for tok in re.findall(r'[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]', text):
        up = tok.upper()
        if up in STOP or TIME_LIKE.match(tok):
            continue
        has_digit = any(c.isdigit() for c in tok)
        has_alpha = any(c.isalpha() for c in tok)
        if has_digit and has_alpha and len(tok) >= 5:
            bucket = strong
        elif not tok.isupper() and not tok.islower() and len(tok) >= 8 and not WORD_LIKE.fullmatch(tok):
            bucket = strong  # letters-only random case, e.g. ixcKpRTDJZWG
        elif has_alpha and tok.isupper() and len(tok) >= 5:
            bucket = weak
        elif tok.isdigit() and 6 <= len(tok) <= 12:
            bucket = weak
        else:
            continue
        if tok not in strong and tok not in weak:
            bucket.append(tok)
    return strong, weak


def order_codes(codes):
    """Start 75% down the list (least contested), run to the end, then climb back up to the top."""
    start = int(len(codes) * 0.75)
    return codes[start:] + codes[:start][::-1]


def post_text(post):
    parts = [post.get('text') or '']
    quote = post.get('quote') or {}
    if (quote.get('author') or {}).get('screen_name', '').lower() == HANDLE:
        parts.append(quote.get('text') or '')
    for m in (post.get('media') or {}).get('all') or []:
        parts.append(m.get('altText') or '')
    return '\n'.join(parts)


# ---------------------------------------------------------------- chain

def rpc(method, params, urls=None):
    last = None
    for url in urls or RPCS:
        try:
            r = requests.post(url, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=8)
            body = r.json()
            if 'result' in body:
                return body['result']
            last = body.get('error')
        except Exception as exc:  # try the next public RPC
            last = str(exc)
    raise RuntimeError('%s failed: %s' % (method, last))


def broadcast(raw_hex):
    """Send to every RPC at once; succeed if any accepts it."""
    def send(url):
        try:
            return url, rpc('eth_sendRawTransaction', [raw_hex], [url])
        except Exception as exc:
            return url, 'ERR ' + str(exc)[:160]
    with ThreadPoolExecutor(len(RPCS)) as pool:
        return list(pool.map(send, RPCS))


def mint_calldata(signed):
    p = signed['payload']
    req = (to_checksum_address(p['to']), to_checksum_address(p['royaltyRecipient']), int(p['royaltyBps']),
           to_checksum_address(p['primarySaleRecipient']), p['uri'], int(p['price']),
           to_checksum_address(p['currency']), int(p['validityStartTimestamp']), int(p['validityEndTimestamp']),
           bytes.fromhex(p['uid'][2:]))
    sig = bytes.fromhex(signed['signature'][2:])
    return keccak(text=MINT_FN)[:4] + abi_encode([MINT_REQ, 'bytes'], [req, sig]), int(p['price'])


# ---------------------------------------------------------------- bot

class Bot:
    def __init__(self):
        wallet = read_json(WALLET, None)
        if not wallet:
            acct = Account.create()
            wallet = {'address': acct.address, 'private_key': '0x' + bytes(acct.key).hex(), 'created_at': now()}
            write_json(WALLET, wallet)
            WALLET.chmod(0o600)
            log('wallet_created', address=acct.address)
        self.acct = Account.from_key(wallet['private_key'])
        self.state = read_json(STATE, {})
        self.http = requests.Session()
        self.http.headers.update({'User-Agent': UA, 'Origin': SITE, 'Referer': SITE + '/optimum'})
        self.user_id = None
        self.claim_lock = threading.Lock()
        self.seen = set()
        self.seen_lock = threading.Lock()
        self.health = {'started_at': now(), 'wallet': self.acct.address}

    def save(self):
        write_json(STATE, self.state)

    # ---- Snag session
    def login(self):
        s = self.http
        s.cookies.clear()
        csrf = s.get(SITE + '/api/auth/csrf', timeout=10).json()['csrfToken']
        issued = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (time.time_ns() // 10**6 % 1000)
        msg = {'domain': HOST, 'address': self.acct.address, 'statement': SIWE_STATEMENT, 'uri': SITE,
               'version': '1', 'chainId': 1, 'nonce': csrf, 'issuedAt': issued}
        text = ('%(domain)s wants you to sign in with your Ethereum account:\n%(address)s\n\n%(statement)s\n\n'
                'URI: %(uri)s\nVersion: %(version)s\nChain ID: %(chainId)d\nNonce: %(nonce)s\nIssued At: %(issuedAt)s') % msg
        sig = '0x' + bytes(self.acct.sign_message(encode_defunct(text=text)).signature).hex()
        form = {'message': json.dumps(msg, separators=(',', ':')), 'accessToken': sig, 'signature': sig,
                'walletAddress': self.acct.address, 'redirect': 'false', 'callbackUrl': '/protected',
                'csrfToken': csrf, 'json': 'true'}
        r = s.post(SITE + '/api/auth/callback/credentials', data=form, timeout=15, allow_redirects=False)
        session = s.get(SITE + '/api/auth/session', timeout=10).json() or {}
        self.user_id = (session.get('user') or {}).get('id')
        if not self.user_id:
            raise RuntimeError('Snag login failed: HTTP %s %s' % (r.status_code, r.text[:300]))
        log('logged_in', user_id=self.user_id, address=self.acct.address)
        return session

    def api(self, method, path, retry_auth=True, **kw):
        r = self.http.request(method, SITE + path, timeout=kw.pop('timeout', 10), **kw)
        if r.status_code == 401 and retry_auth:
            self.login()
            return self.api(method, path, retry_auth=False, **kw)
        return r

    def complete_rule(self, rule_id, body=None):
        r = self.api('POST', '/api/loyalty/rules/%s/complete' % rule_id, json=body or {})
        try:
            data = r.json()
        except ValueError:
            data = {'raw': r.text[:300]}
        return r.status_code, data

    def rule_status(self, rule_id):
        r = self.api('GET', '/api/loyalty/rules/status', params={**SCOPE, 'userId': self.user_id})
        for row in (r.json() or {}).get('data', []) if r.ok else []:
            if row.get('loyaltyRuleId') == rule_id:
                return row
        return {}

    def wait_rule(self, rule_id, seconds=25):
        end = time.monotonic() + seconds
        row = {}
        while time.monotonic() < end:
            row = self.rule_status(rule_id)
            if row.get('status') in ('completed', 'failed'):
                return row
            time.sleep(0.3)
        return row

    def setup_tasks(self):
        # POST each task once, even if it didn't complete: a second POST leaves
        # Snag's link_click follow task stuck for good. Codes claim fine without it.
        posted = self.state.setdefault('setup', {})
        for name, rule in SETUP_RULES.items():
            if posted.get(name):
                continue
            code, data = self.complete_rule(rule)
            posted[name] = now()
            self.save()
            row = self.wait_rule(rule, 15) if code < 300 else {}
            log('setup_task', task=name, http=code, response=data, status=row)

    # ---- code claiming
    def try_code(self, code):
        status, data = self.complete_rule(CODE_RULE, {'verificationCode': code})
        log('code_attempt', code=code, http=status, response=data)
        return code, status, data

    def claim(self, codes, source):
        with self.claim_lock:
            if self.state.get('code_claimed'):
                log('skip_codes_already_claimed', codes=codes, source=source)
                return self.ensure_minted()
            strong, weak = codes
            queue = order_codes(strong) + weak[:6]
            log('claiming', order=queue, source=source)
            # One code at a time: parallel waves burned 2 codes on Drop #1 and tripped
            # Snag's "too many attempts, try again in 1 minute" lock.
            for code in queue:
                _, status, _ = self.try_code(code)
                if status == 429:
                    time.sleep(61)
                    _, status, _ = self.try_code(code)
                if status >= 300:
                    continue
                row = self.wait_rule(CODE_RULE)
                log('code_rule_status', code=code, status=row)
                # 'failed' means the code got used up while queued; anything else, go mint.
                if row.get('status') != 'failed':
                    self.state['code_claimed'] = {'codes': [code], 'source': source, 'at': now()}
                    self.save()
                    log('CODE_CLAIMED', code=code, source=source)
                    return self.ensure_minted()
            log('no_code_worked', tried=queue, source=source)

    # ---- minting
    def available_assets(self):
        r = self.api('GET', '/api/minting/assets', params={
            'limit': 100, 'mintingContractIds': CONTRACT_ID, **SCOPE,
            'status': 'active', 'isListed': 'true', 'isHidden': 'false'})
        assets = [a['id'] for a in (r.json() or {}).get('data', []) if not a.get('quantityMinted')]
        random.shuffle(assets)  # avoid racing everyone else for the first card in the list
        return assets

    def ensure_minted(self):
        """Retry until minted: waits indefinitely for funding, gives up after 20 real failures."""
        failures = 0
        while not self.state.get('minted') and failures < 20:
            try:
                result = self.mint_once()
            except Exception as exc:
                result = False
                log('mint_error', failures=failures, error=str(exc)[:400])
            if result is True:
                return
            if result == 'unfunded':
                time.sleep(15)
                continue
            failures += 1
            time.sleep(3 if failures < 5 else 20)
        if not self.state.get('minted'):
            log('MINT_GAVE_UP')

    def mint_once(self):
        base_fee = int(rpc('eth_getBlockByNumber', ['latest', False])['baseFeePerGas'], 16)
        max_fee = base_fee * 2 + PRIORITY_WEI
        balance = int(rpc('eth_getBalance', [self.acct.address, 'latest']), 16)
        if balance < GAS_LIMIT * max_fee:
            if not self.health.get('fund_alerted'):
                log('FUND_WALLET', address=self.acct.address, balance_eth=balance / 1e18,
                    need_eth=GAS_LIMIT * max_fee / 1e18)
                self.health['fund_alerted'] = now()
            return 'unfunded'
        for asset in self.available_assets()[:8]:
            r = self.api('POST', '/api/minting/contracts/mint',
                         json={'quantity': 1, 'assetId': asset, 'contractId': CONTRACT_ID})
            if r.status_code != 202:
                log('mint_request_rejected', asset=asset, http=r.status_code, body=r.text[:300])
                if r.status_code in (400, 409, 429):
                    continue
                return False
            status_id = r.json()['mintingContractAssetMintStatusId']
            signed = self.wait_signature(status_id)
            if not signed:
                continue
            log('mint_signature', asset=asset, payload=signed.get('payload'))
            data, value = mint_calldata(signed)
            nonce = int(rpc('eth_getTransactionCount', [self.acct.address, 'pending']), 16)
            tx = {'chainId': 1, 'type': 2, 'nonce': nonce, 'to': CONTRACT, 'value': value, 'data': data,
                  'gas': GAS_LIMIT, 'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': PRIORITY_WEI}
            raw = bytes(self.acct.sign_transaction(tx).raw_transaction)
            tx_hash = '0x' + keccak(raw).hex()
            log('mint_tx_sent', asset=asset, tx=tx_hash, results=broadcast('0x' + raw.hex()))
            receipt = self.wait_receipt(tx_hash)
            ok = receipt and receipt.get('status') == '0x1'
            self.api('POST', '/api/minting/status/%s' % status_id,
                     json={'status': 'minted' if ok else 'failed', 'txHash': tx_hash})
            if ok:
                self.state['minted'] = {'tx': tx_hash, 'asset': asset, 'at': now()}
                self.save()
                log('MINTED', tx=tx_hash, etherscan='https://etherscan.io/tx/' + tx_hash)
                return True
            log('mint_tx_failed', tx=tx_hash, receipt=receipt)
        return False

    def wait_signature(self, status_id, seconds=60):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            data = self.api('GET', '/api/minting/status/%s' % status_id).json()
            if data.get('signature'):
                return json.loads(data['signature'])
            if data.get('status') == 'failed':
                log('mint_status_failed', status=data)
                return None
            time.sleep(0.3)
        log('mint_signature_timeout', status_id=status_id)
        return None

    def wait_receipt(self, tx_hash, seconds=180):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                receipt = rpc('eth_getTransactionReceipt', [tx_hash])
                if receipt:
                    return receipt
            except Exception:
                pass
            time.sleep(2)
        return None

    # ---- X watching
    def ingest(self, posts, first):
        cutoff = time.time() - FRESH_SECONDS
        for post in posts:
            pid = post.get('id')
            with self.seen_lock:
                if not pid or pid in self.seen:
                    continue
                self.seen.add(pid)
            if (post.get('created_timestamp') or 0) < cutoff:
                continue
            if first and (post.get('created_timestamp') or 0) < time.time() - 120:
                continue
            threading.Thread(target=self.handle_post, args=(post,), daemon=True).start()

    def handle_post(self, post):
        text = post_text(post)
        codes = extract_codes(text)
        log('new_post', url=post.get('url'), text=text[:1000], strong=codes[0], weak=codes[1],
            has_media=bool((post.get('media') or {}).get('all')))
        if codes[0] or codes[1]:
            self.claim(codes, post.get('url'))

    def poller(self, offset):
        fx = requests.Session()
        fx.headers['User-Agent'] = 'optimum-turbocards-watch/1.0'
        time.sleep(offset)
        first = True
        while True:
            started = time.monotonic()
            try:
                r = fx.get(FEED, timeout=6)
                if r.status_code == 200:
                    self.ingest(r.json().get('results') or [], first)
                    first = False
                    self.health['last_feed_ok'] = now()
                elif r.status_code == 429:
                    self.health['last_feed_error'] = '429 at ' + now()
                    time.sleep(15)
                else:
                    self.health['last_feed_error'] = 'HTTP %s at %s' % (r.status_code, now())
            except Exception as exc:
                self.health['last_feed_error'] = '%s at %s' % (str(exc)[:120], now())
            time.sleep(max(0.0, POLL_INTERVAL - (time.monotonic() - started)))

    def keepalive(self):
        while True:
            time.sleep(60)
            try:
                session = self.api('GET', '/api/auth/session').json() or {}
                if not (session.get('user') or {}).get('id'):
                    self.login()
                self.health['session_ok'] = now()
            except Exception as exc:
                self.health['session_error'] = '%s at %s' % (str(exc)[:120], now())
            try:
                self.health['balance_eth'] = int(rpc('eth_getBalance', [self.acct.address, 'latest']), 16) / 1e18
            except Exception:
                pass
            self.health.update(state=self.state, updated_at=now())
            write_json(STATUS, self.health)

    def run(self):
        self.login()
        self.setup_tasks()
        if self.state.get('code_claimed') and not self.state.get('minted'):
            threading.Thread(target=self.ensure_minted, daemon=True).start()
        for i in range(POLL_THREADS):
            threading.Thread(target=self.poller, args=(i * POLL_INTERVAL / POLL_THREADS,), daemon=True).start()
        log('watching', handle=HANDLE, wallet=self.acct.address, pollers=POLL_THREADS, interval=POLL_INTERVAL)
        self.keepalive()


def selftest():
    bot = Bot()
    out = {'wallet': bot.acct.address, 'selector_ok': keccak(text=MINT_FN)[:4].hex() == '2c4510f8'}
    session = bot.login()
    out['session_user'] = (session.get('user') or {}).get('id')
    bot.setup_tasks()
    out['setup'] = bot.state.get('setup')
    probe = 'ZZTEST' + str(random.randint(100000, 999999))
    out['bogus_code'] = bot.complete_rule(CODE_RULE, {'verificationCode': probe})
    out['code_rule_status'] = bot.rule_status(CODE_RULE)
    out['assets_available_page1'] = len(bot.available_assets())
    out['balance_eth'] = int(rpc('eth_getBalance', [bot.acct.address, 'latest']), 16) / 1e18
    out['base_fee_gwei'] = int(rpc('eth_getBlockByNumber', ['latest', False])['baseFeePerGas'], 16) / 1e9
    r = requests.get(FEED, timeout=10)
    out['feed'] = [r.status_code, len((r.json() or {}).get('results') or []) if r.ok else 0]
    print(json.dumps(out, indent=1, default=str))


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        selftest()
    else:
        Bot().run()
