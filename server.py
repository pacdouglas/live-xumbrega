#!/usr/bin/env python3
"""
Xumbr3ga Chat Hub — servidor central SSE
Browser Sources (porta configurada no dialog, padrão 8080):
  http://localhost:8080/xumbrega_multichat.html
  http://localhost:8080/xumbrega_overlay_webcam.html
"""
import asyncio
import sys
import os
import signal
import json
import html as html_lib
import re
import mimetypes
import datetime
from pathlib import Path
from aiohttp import web, ClientSession, WSMsgType, ClientTimeout

DIR = Path(__file__).parent
TW_CH = 'xumbr3ga'
KI_CH = 'xumbr3ga'
KI_CHATROOM_ID = '45573790'
PUSHER_KEY = '32cbd69e4b950bf97679'
PUSHER_CLUSTER = 'us2'

clients: set[asyncio.Queue] = set()
HISTORY_FILE   = DIR / 'messages.jsonl'
CONFIG_FILE    = DIR / 'config.json'
LOCK_FILE      = DIR / 'server.lock'
HISTORY_LIMIT  = 50_000  # máximo de mensagens no arquivo
HISTORY_TRIM   = 40_000  # quantas remover quando estoura (mantém 10k)
HISTORY_REPLAY = 500     # quantas enviar no SSE ao reconectar
platform_status = {'tw': False, 'ki': False, 'yt': False}
_history_lock: asyncio.Lock | None = None  # initialized in main()
_msg_count = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    return html_lib.escape(str(s or ''), quote=True)


# ── Broadcast & persist ───────────────────────────────────────────────────────

def broadcast(msg: dict):
    """Enqueue msg for all connected SSE clients."""
    data = f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
    dead = set()
    for q in clients:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.add(q)
    for q in dead:
        clients.discard(q)


async def save_message(msg: dict):
    """Append a chat message as one NDJSON line, keeping at most HISTORY_LIMIT lines."""
    global _msg_count
    line = json.dumps(msg, ensure_ascii=False) + '\n'
    async with _history_lock:
        await asyncio.to_thread(_append_line, line)
        _msg_count += 1
        if _msg_count > HISTORY_LIMIT:
            await asyncio.to_thread(_trim_history, HISTORY_TRIM)
            _msg_count -= HISTORY_TRIM
            log('hist', 'WARN', f'limite atingido — {HISTORY_TRIM} mensagens antigas removidas (total: {_msg_count})')


def _append_line(line: str):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(line)


def _trim_history(n: int):
    """Remove as primeiras n linhas do arquivo de histórico."""
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines[n:])


def set_status(p: str, on: bool):
    platform_status[p] = on
    broadcast({'p': 'status', 'platform': p, 'on': on})


def log(platform: str, level: str, msg: str):
    """Log estruturado com timestamp. Níveis: INFO WARN ERROR CHAT."""
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    print(f'{ts} [{platform}] {level} {msg}', flush=True)


# ── Twitch emote rendering ────────────────────────────────────────────────────

def tw_render(text: str, emotes_tag: str) -> str:
    if not emotes_tag:
        return esc(text)
    reps = []
    for part in emotes_tag.split('/'):
        if ':' not in part:
            continue
        id_, positions = part.split(':', 1)
        for pos in positions.split(','):
            if '-' not in pos:
                continue
            s, e = pos.split('-')
            reps.append({'s': int(s), 'e': int(e) + 1, 'id': id_})
    if not reps:
        return esc(text)
    reps.sort(key=lambda x: x['s'])
    out, last = '', 0
    for rep in reps:
        out += esc(text[last:rep['s']])
        alt = esc(text[rep['s']:rep['e']])
        out += (
            f'<img src="https://static-cdn.jtvnw.net/emoticons/v2/{rep["id"]}/default/dark/1.0" '
            f'alt="{alt}" title="{alt}">'
        )
        last = rep['e']
    return out + esc(text[last:])


# ── Kick emote rendering ──────────────────────────────────────────────────────

def ki_render(text: str) -> str:
    parts = re.split(r'(\[emote:\d+:[^\]]+\])', str(text or ''))
    result = []
    for part in parts:
        m = re.match(r'^\[emote:(\d+):([^\]]+)\]$', part)
        if m:
            id_, name = m.group(1), m.group(2)
            result.append(
                f'<img src="https://files.kick.com/emotes/{id_}/fullsize" '
                f'alt=":{esc(name)}:" title=":{esc(name)}:" '
                f'style="height:1.4em;vertical-align:middle;margin:0 2px;">'
            )
        else:
            result.append(esc(part))
    return ''.join(result)


# ── YouTube runs rendering ────────────────────────────────────────────────────

def yt_parse_runs(runs: list) -> str:
    parts = []
    for run in runs:
        if isinstance(run.get('text'), str):
            parts.append(esc(run['text']))
        elif 'emoji' in run:
            emoji = run['emoji']
            url = ((emoji.get('image') or {}).get('thumbnails') or [{}])[0].get('url', '')
            name = ((emoji.get('shortcuts') or []) or [emoji.get('emojiId', '')])[0].replace(':', '')
            if url:
                parts.append(
                    f'<img src="{url}" alt="{esc(name)}" title="{esc(name)}" '
                    f'style="height:1.4em;vertical-align:middle;margin:0 2px;">'
                )
            elif name:
                parts.append(esc(name))
    return ''.join(parts)


# ── Twitch IRC helpers ────────────────────────────────────────────────────────

def _tw_tags(line: str) -> dict:
    raw = re.match(r'^@([^ ]+)', line)
    if not raw:
        return {}
    return dict(
        t.split('=', 1) if '=' in t else (t, '')
        for t in raw.group(1).split(';')
    )


# ── Twitch IRC loop ───────────────────────────────────────────────────────────

async def twitch_loop():
    from random import randint
    attempt = 0
    backoff = 5
    while True:
        attempt += 1
        connected = False
        log('tw', 'INFO', f'conectando (tentativa #{attempt}) → wss://irc-ws.chat.twitch.tv')
        try:
            conn_timeout = ClientTimeout(total=None, connect=15, sock_connect=15)
            async with ClientSession(timeout=conn_timeout) as session:
                async with session.ws_connect(
                    'wss://irc-ws.chat.twitch.tv:443',
                    heartbeat=30,  # WS ping automático; reconecta se sem pong em 30s
                ) as ws:
                    await ws.send_str('CAP REQ :twitch.tv/tags twitch.tv/commands')
                    await ws.send_str(f'NICK justinfan{10000 + randint(0, 89999)}')
                    await ws.send_str(f'JOIN #{TW_CH}')
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            for line in msg.data.split('\r\n'):
                                if not line:
                                    continue
                                if line.startswith('PING'):
                                    await ws.send_str('PONG :tmi.twitch.tv')
                                    continue
                                if 'End of /NAMES list' in line:
                                    connected = True
                                    backoff = 5
                                    set_status('tw', True)
                                    broadcast({'p': 'sys', 'text': f'🟣 Twitch conectado — #{TW_CH}'})
                                    log('tw', 'INFO', f'conectado — #{TW_CH}')
                                elif 'USERNOTICE' in line:
                                    tags = _tw_tags(line)
                                    u = tags.get('display-name') or 'Alguém'
                                    msg_id = tags.get('msg-id') or ''
                                    if msg_id in ('sub', 'resub'):
                                        log('tw', 'INFO', f'sub: {u}')
                                        broadcast({'p': 'sys', 'text': f'🟣 {u} assinou na Twitch!'})
                                    elif msg_id in ('subgift', 'anonsubgift'):
                                        log('tw', 'INFO', f'subgift de {u}')
                                        broadcast({'p': 'sys', 'text': f'🟣 {u} deu um sub na Twitch!'})
                                    elif msg_id == 'raid':
                                        viewers = tags.get('msg-param-viewerCount', '?')
                                        log('tw', 'INFO', f'raid de {u} ({viewers} viewers)')
                                        broadcast({'p': 'sys', 'text': f'🟣 Raid de {u} — {viewers} viewers!'})
                                elif 'PRIVMSG' in line:
                                    tags = _tw_tags(line)
                                    user = tags.get('display-name') or 'Anônimo'
                                    color = tags.get('color') or ''
                                    m = re.search(r'PRIVMSG #\S+ :(.+)$', line)
                                    if m:
                                        rendered = tw_render(m.group(1), tags.get('emotes', ''))
                                        chat_msg = {'p': 'tw', 'user': user, 'color': color, 'html': rendered}
                                        log('tw', 'CHAT', f'{user}: {m.group(1)[:80]}')
                                        broadcast(chat_msg)
                                        await save_message(chat_msg)
                                elif 'NOTICE' in line:
                                    m_notice = re.search(r'NOTICE \S+ :(.+)$', line)
                                    if m_notice:
                                        log('tw', 'WARN', f'notice: {m_notice.group(1)}')
                                elif re.search(r' 40[36] ', line):
                                    log('tw', 'ERROR', f'canal inválido ou inexistente: {TW_CH}')
                        elif msg.type == WSMsgType.ERROR:
                            log('tw', 'WARN', f'erro no WebSocket: {ws.exception()}')
                            break
                        elif msg.type == WSMsgType.CLOSED:
                            log('tw', 'WARN', f'WebSocket fechado pelo servidor (código {ws.close_code})')
                            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log('tw', 'ERROR', f'{type(e).__name__}: {e}')
        set_status('tw', False)
        if not connected:
            backoff = min(backoff * 2, 60)
        log('tw', 'WARN', f'desconectado — reconectando em {backoff}s...')
        await asyncio.sleep(backoff)


# ── Kick Pusher loop ──────────────────────────────────────────────────────────

async def kick_loop():
    attempt = 0
    backoff = 5
    while True:
        attempt += 1
        connected = False
        pusher_url = (
            f'wss://ws-{PUSHER_CLUSTER}.pusher.com/app/{PUSHER_KEY}'
            f'?protocol=7&client=py&version=7.6.0'
        )
        log('ki', 'INFO', f'conectando (tentativa #{attempt}) → ws-{PUSHER_CLUSTER}.pusher.com')
        try:
            conn_timeout = ClientTimeout(total=None, connect=15, sock_connect=15)
            async with ClientSession(timeout=conn_timeout) as session:
                async with session.ws_connect(
                    pusher_url,
                    heartbeat=30,  # WS ping automático como backup ao pusher:ping
                ) as ws:
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            ename = event.get('event', '')

                            if ename == 'pusher:connection_established':
                                sock_id = (json.loads(event.get('data') or '{}') or {}).get('socket_id', '?')
                                log('ki', 'INFO', f'Pusher estabelecido (socket_id={sock_id}) — subscrevendo chatroom {KI_CHATROOM_ID}')
                                sub = json.dumps({
                                    'event': 'pusher:subscribe',
                                    'data': {'channel': f'chatrooms.{KI_CHATROOM_ID}.v2'}
                                })
                                await ws.send_str(sub)

                            elif ename == 'pusher_internal:subscription_succeeded':
                                connected = True
                                backoff = 5
                                set_status('ki', True)
                                broadcast({'p': 'sys', 'text': f'🟢 Kick conectado — {KI_CH}'})
                                log('ki', 'INFO', f'conectado — {KI_CH} (chatroom {KI_CHATROOM_ID})')

                            elif ename == 'pusher:error':
                                d = event.get('data') or {}
                                if isinstance(d, str):
                                    try:
                                        d = json.loads(d)
                                    except Exception:
                                        pass
                                code = d.get('code') if isinstance(d, dict) else None
                                errmsg = d.get('message', d) if isinstance(d, dict) else d
                                if code and 4000 <= int(code) < 4100:
                                    # Erros de aplicação Pusher (chave inválida, etc.) — log proeminente
                                    log('ki', 'ERROR', f'Pusher erro fatal (código {code}): {errmsg}')
                                else:
                                    log('ki', 'WARN', f'Pusher erro (código {code}): {errmsg}')

                            elif ename == 'pusher:ping':
                                await ws.send_str(json.dumps({'event': 'pusher:pong', 'data': {}}))

                            elif ename == 'App\\Events\\ChatMessageEvent':
                                d = event.get('data')
                                if isinstance(d, str):
                                    d = json.loads(d)
                                sender = d.get('sender') or {}
                                user = sender.get('username') or sender.get('slug') or 'Anônimo'
                                color = (sender.get('identity') or {}).get('color') or ''
                                text = d.get('content') or ''
                                if text:
                                    chat_msg = {'p': 'ki', 'user': user, 'color': color, 'html': ki_render(text)}
                                    log('ki', 'CHAT', f'{user}: {text[:80]}')
                                    broadcast(chat_msg)
                                    await save_message(chat_msg)

                            elif ename == 'App\\Events\\SubscriptionEvent':
                                d = event.get('data')
                                if isinstance(d, str):
                                    d = json.loads(d)
                                who = (d or {}).get('username', 'Alguém')
                                log('ki', 'INFO', f'sub: {who}')
                                broadcast({'p': 'sys', 'text': f'🟢 {who} assinou no Kick!'})

                            elif ename == 'App\\Events\\GiftedSubscriptionsEvent':
                                d = event.get('data')
                                if isinstance(d, str):
                                    d = json.loads(d)
                                d = d or {}
                                gifted_by = d.get('gifted_by') or 'Alguém'
                                count = len(d.get('gifted_usernames') or []) or 1
                                log('ki', 'INFO', f'gifted subs: {gifted_by} → {count} sub(s)')
                                broadcast({'p': 'sys', 'text': f'🟢 {gifted_by} deu {count} sub(s) no Kick!'})

                        elif msg.type == WSMsgType.ERROR:
                            log('ki', 'WARN', f'erro no WebSocket: {ws.exception()}')
                            break
                        elif msg.type == WSMsgType.CLOSED:
                            log('ki', 'WARN', f'WebSocket fechado pelo servidor (código {ws.close_code})')
                            break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log('ki', 'ERROR', f'{type(e).__name__}: {e}')
        set_status('ki', False)
        if not connected:
            backoff = min(backoff * 2, 60)
        log('ki', 'WARN', f'desconectado — reconectando em {backoff}s...')
        await asyncio.sleep(backoff)


# ── YouTube polling loop ──────────────────────────────────────────────────────

async def youtube_loop(video_id: str):
    if not video_id:
        return

    YT_HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'en-US,en;q=0.9',
        'Cookie': 'CONSENT=YES+1; SOCS=CAESEwgDEgk1NzM4MTkzMjYaAmVuIAE=',
    }
    req_timeout = ClientTimeout(total=30)
    attempt = 0
    outer_backoff = 5

    while True:
        attempt += 1
        log('yt', 'INFO', f'iniciando (tentativa #{attempt}) — v={video_id}')

        try:
            # Uma session por ciclo de conexão — reutilizada entre token e polls
            async with ClientSession() as session:

                # ── Fase 1: obter token de continuação ────────────────────────────
                continuation = None
                token_backoff = 5
                while not continuation:
                    try:
                        async with session.get(
                            f'https://www.youtube.com/live_chat?v={video_id}&is_popout=1',
                            headers=YT_HEADERS,
                            timeout=req_timeout,
                        ) as r:
                            if r.status == 404:
                                log('yt', 'ERROR', 'HTTP 404 — live não existe ou encerrou permanentemente')
                                set_status('yt', False)
                                return
                            if not r.ok:
                                raise Exception(f'HTTP {r.status}')
                            html = await r.text()
                            continuation = _yt_extract_token(html)
                            if not continuation:
                                raise Exception('token de continuação não encontrado na página')
                            log('yt', 'INFO', f'token obtido: {continuation[:24]}...')
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log('yt', 'ERROR', f'erro ao buscar token: {type(e).__name__}: {e} — tentando em {token_backoff}s')
                        await asyncio.sleep(token_backoff)
                        token_backoff = min(token_backoff * 2, 60)

                set_status('yt', True)
                broadcast({'p': 'sys', 'text': '🔴 YouTube conectado!'})
                log('yt', 'INFO', f'conectado — iniciando polling (v={video_id})')

                # ── Fase 2: polling de mensagens ──────────────────────────────────
                is_first = True
                rate_backoff = 0
                err_backoff = 5

                while True:
                    try:
                        async with session.post(
                            'https://www.youtube.com/youtubei/v1/live_chat/get_live_chat',
                            json={
                                'context': {
                                    'client': {
                                        'clientName': 'WEB',
                                        'clientVersion': '2.20240101.00.00',
                                        'hl': 'en',
                                    }
                                },
                                'continuation': continuation,
                            },
                            headers={'Content-Type': 'application/json'},
                            timeout=req_timeout,
                        ) as resp:
                            if resp.status == 429:
                                rate_backoff = min((rate_backoff or 10) * 2, 120)
                                log('yt', 'WARN', f'429 rate-limit — aguardando {rate_backoff}s')
                                await asyncio.sleep(rate_backoff)
                                continue
                            if resp.status == 404:
                                log('yt', 'INFO', 'poll 404 — live encerrada')
                                set_status('yt', False)
                                broadcast({'p': 'sys', 'text': '🔴 YouTube: live encerrada.'})
                                return
                            if not resp.ok:
                                raise Exception(f'HTTP {resp.status}')
                            data = await resp.json(content_type=None)

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if re.search(r'encerrada|ended|not.?found', str(e), re.I):
                            log('yt', 'INFO', f'live encerrada ({e})')
                            set_status('yt', False)
                            broadcast({'p': 'sys', 'text': '🔴 YouTube: live encerrada.'})
                            return
                        log('yt', 'ERROR', f'{type(e).__name__}: {e} — tentando em {err_backoff}s')
                        await asyncio.sleep(err_backoff)
                        err_backoff = min(err_backoff * 2, 60)
                        continue

                    lcc = (data.get('continuationContents') or {}).get('liveChatContinuation')
                    if not lcc:
                        # YouTube parou de retornar continuação — reconecta do zero
                        log('yt', 'WARN', 'liveChatContinuation ausente — reconectando do zero')
                        break  # sai do loop de poll; outer loop re-busca o token

                    nc = ((lcc.get('continuations') or [{}]))[0]
                    tok = (
                        (nc.get('timedContinuationData') or {}).get('continuation')
                        or (nc.get('invalidationContinuationData') or {}).get('continuation')
                        or (nc.get('reloadContinuationData') or {}).get('continuation')
                    )
                    if tok:
                        continuation = tok

                    poll_ms = (
                        (nc.get('timedContinuationData') or {}).get('timeoutMs')
                        or (nc.get('invalidationContinuationData') or {}).get('timeoutMs')
                        or 5000
                    )

                    if not is_first:
                        for action in lcc.get('actions') or []:
                            await _yt_handle_action(action)
                    is_first = False
                    rate_backoff = 0
                    err_backoff = 5
                    await asyncio.sleep(min(poll_ms, 2000) / 1000)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log('yt', 'ERROR', f'erro inesperado: {type(e).__name__}: {e}')

        set_status('yt', False)
        log('yt', 'WARN', f'desconectado — reconectando em {outer_backoff}s...')
        await asyncio.sleep(outer_backoff)
        outer_backoff = min(outer_backoff * 2, 60)


def _yt_extract_token(html: str) -> str:
    patterns = [
        r'"reloadContinuationData"\s*:\s*\{\s*"continuation"\s*:\s*"([^"]{20,})"',
        r'"timedContinuationData"\s*:\s*\{[^}]{0,200}"continuation"\s*:\s*"([^"]{20,})"',
        r'"invalidationContinuationData"\s*:\s*\{[^}]{0,200}"continuation"\s*:\s*"([^"]{20,})"',
        r'"continuation"\s*:\s*"([^"]{20,})"[^}]{0,100}"clickTrackingParams"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ''


async def _yt_handle_action(action: dict):
    item = (action.get('addChatItemAction') or {}).get('item') or {}
    msg = item.get('liveChatTextMessageRenderer')
    paid = item.get('liveChatPaidMessageRenderer')
    mem = item.get('liveChatMembershipItemRenderer')

    if msg:
        user = (msg.get('authorName') or {}).get('simpleText') or 'Anônimo'
        html = yt_parse_runs(msg.get('message', {}).get('runs') or [])
        if html.strip():
            chat_msg = {'p': 'yt', 'user': user, 'color': '', 'html': html}
            log('yt', 'CHAT', f'{user}: {html[:80]}')
            broadcast(chat_msg)
            await save_message(chat_msg)

    if paid:
        user = (paid.get('authorName') or {}).get('simpleText') or 'Anônimo'
        amount = (paid.get('purchaseAmountText') or {}).get('simpleText') or ''
        broadcast({'p': 'sys', 'text': f'🔴 Super Chat de {esc(user)}: {esc(amount)}'})
        html = yt_parse_runs(paid.get('message', {}).get('runs') or [])
        if html.strip():
            chat_msg = {'p': 'yt', 'user': user, 'color': '#ffcc44', 'html': html}
            broadcast(chat_msg)
            await save_message(chat_msg)

    if mem:
        user = (mem.get('authorName') or {}).get('simpleText') or 'Alguém'
        broadcast({'p': 'sys', 'text': f'🔴 {esc(user)} se tornou membro!'})


# ── File watcher (hot-reload) ─────────────────────────────────────────────────

async def file_watcher_loop():
    """Detecta mudanças nos .html e manda reload SSE para todos os clientes."""
    mtimes: dict[Path, float] = {}
    for f in DIR.glob('*.html'):
        try:
            mtimes[f] = f.stat().st_mtime
        except OSError:
            pass

    while True:
        await asyncio.sleep(1)
        for f in DIR.glob('*.html'):
            try:
                mtime = f.stat().st_mtime
                if f in mtimes and mtimes[f] != mtime:
                    log('watch', 'INFO', f'{f.name} modificado — recarregando clientes')
                    broadcast({'p': 'reload'})
                mtimes[f] = mtime
            except OSError:
                pass



# ── SSE endpoint ──────────────────────────────────────────────────────────────

async def events_handler(request: web.Request) -> web.StreamResponse:
    want_history = request.rel_url.query.get('history') == '1'
    resp = web.StreamResponse(headers={
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'X-Accel-Buffering': 'no',
    })
    await resp.prepare(request)

    # Stream last HISTORY_REPLAY lines of history
    if want_history:
        def read_lines():
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE, encoding='utf-8') as f:
                    return [l.strip() for l in f if l.strip()][-HISTORY_REPLAY:]
            return []
        for line in await asyncio.to_thread(read_lines):
            await resp.write(f'data: {line}\n\n'.encode())

    # Send current platform status
    for p, on in platform_status.items():
        status_msg = json.dumps({'p': 'status', 'platform': p, 'on': on})
        await resp.write(f'data: {status_msg}\n\n'.encode())

    # Subscribe to live broadcasts
    ua_raw = request.headers.get('User-Agent', '')
    if 'OBS' in ua_raw:
        client_id = 'OBS'
    elif 'Firefox' in ua_raw:
        client_id = 'Firefox'
    elif 'Chrome' in ua_raw:
        client_id = 'Chrome'
    elif ua_raw:
        client_id = ua_raw[:30]
    else:
        client_id = 'desconhecido'

    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    clients.add(q)
    log('sse', 'INFO', f'conectado — {client_id} | history={want_history} | total={len(clients)}')
    try:
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=20)
            except asyncio.TimeoutError:
                # Keepalive ping — detecta desconexão mesmo sem mensagens chegando
                await resp.write(b': keepalive\n\n')
                continue
            if data is None:  # sentinel de shutdown
                break
            await resp.write(data.encode())
    except (ConnectionResetError, asyncio.CancelledError, Exception):
        pass
    finally:
        clients.discard(q)
        log('sse', 'INFO', f'desconectado — {client_id} | total={len(clients)}')
    return resp


# ── Static file handler ───────────────────────────────────────────────────────

async def static_handler(request: web.Request) -> web.Response:
    path = request.match_info.get('path', '') or 'xumbrega_multichat.html'
    fpath = (DIR / path).resolve()
    if not fpath.is_relative_to(DIR):
        raise web.HTTPForbidden()
    if not fpath.is_file():
        raise web.HTTPNotFound()
    mime, _ = mimetypes.guess_type(str(fpath))
    if fpath.suffix == '.html':
        mime = 'text/html'
    data = await asyncio.to_thread(fpath.read_bytes)
    charset = 'utf-8' if (mime or '').startswith('text/') else None
    return web.Response(body=data, content_type=mime or 'application/octet-stream', charset=charset)


# ── Single-instance lock ──────────────────────────────────────────────────────

def acquire_lock() -> bool:
    """Tenta adquirir o lock. Retorna False se outra instância estiver rodando."""
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)  # sinal 0 só verifica existência do processo
            return False     # processo existe — outra instância ativa
        except OSError:
            pass  # lock stale — processo morreu, pode sobrescrever
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock():
    try:
        if LOCK_FILE.exists() and int(LOCK_FILE.read_text().strip()) == os.getpid():
            LOCK_FILE.unlink()
    except Exception:
        pass


# ── Config persist ────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(data: dict):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log('cfg', 'ERROR', f'erro ao salvar: {e}')


# ── Startup dialog ─────────────────────────────────────────────────────────────

def ask_startup_config() -> dict | None:
    """
    Abre dialog de configuração da live.
    Retorna dict com as opções, ou None se nenhuma plataforma foi selecionada.
    """
    try:
        import tkinter as tk
        cfg = load_config()
        result = [None]

        root = tk.Tk()
        root.title('Configurar Live')
        root.resizable(False, False)
        root.lift()
        root.focus_force()

        # ── Twitch ──
        tw_on = tk.BooleanVar(value=cfg.get('tw_on', True))
        tw_ch = tk.StringVar(value=cfg.get('tw_channel', 'xumbr3ga'))

        tw_frame = tk.Frame(root)
        tw_frame.pack(fill='x', padx=16, pady=(12, 2))
        tk.Checkbutton(tw_frame, text='Twitch', variable=tw_on,
                       width=7, anchor='w').pack(side='left')
        tk.Label(tw_frame, text='canal:').pack(side='left', padx=(8, 2))
        tk.Entry(tw_frame, textvariable=tw_ch, width=22).pack(side='left')

        tk.Frame(root, height=1, bg='#cccccc').pack(fill='x', padx=16, pady=8)

        # ── Kick ──
        ki_on = tk.BooleanVar(value=cfg.get('ki_on', True))
        ki_ch = tk.StringVar(value=cfg.get('ki_channel', 'xumbr3ga'))
        ki_id = tk.StringVar(value=cfg.get('ki_chatroom_id', '45573790'))

        ki_frame = tk.Frame(root)
        ki_frame.pack(fill='x', padx=16, pady=2)
        tk.Checkbutton(ki_frame, text='Kick', variable=ki_on,
                       width=7, anchor='w').pack(side='left')
        tk.Label(ki_frame, text='canal:').pack(side='left', padx=(8, 2))
        tk.Entry(ki_frame, textvariable=ki_ch, width=22).pack(side='left')

        ki_id_frame = tk.Frame(root)
        ki_id_frame.pack(fill='x', padx=16, pady=(0, 2))
        tk.Label(ki_id_frame, text='         chatroom ID:').pack(side='left', padx=(8, 2))
        tk.Entry(ki_id_frame, textvariable=ki_id, width=22).pack(side='left')
        tk.Label(root,
                 text='         abra kick.com/api/v2/channels/CANAL no browser → campo "chatroom.id"',
                 fg='gray', font=('TkDefaultFont', 8), anchor='w').pack(fill='x', padx=16, pady=(0, 4))

        # ── Separator ──
        tk.Frame(root, height=1, bg='#cccccc').pack(fill='x', padx=16, pady=8)

        # ── YouTube ──
        yt_on = tk.BooleanVar(value=cfg.get('yt_on', False))
        yt_id = tk.StringVar(value=cfg.get('yt_video_id', ''))

        yt_frame = tk.Frame(root)
        yt_frame.pack(fill='x', padx=16, pady=2)
        tk.Checkbutton(yt_frame, text='YouTube', variable=yt_on,
                       width=7, anchor='w').pack(side='left')
        tk.Label(yt_frame, text='video ID:').pack(side='left', padx=(8, 2))
        tk.Entry(yt_frame, textvariable=yt_id, width=22).pack(side='left')

        tk.Label(root, text='ex: youtube.com/watch?v=Fpfdw0iXuv8  (o código após "v=")',
                 fg='gray', font=('TkDefaultFont', 8)).pack(padx=16, pady=(0, 8))

        # ── Porta ──
        tk.Frame(root, height=1, bg='#cccccc').pack(fill='x', padx=16, pady=8)

        port_val = tk.StringVar(value=str(cfg.get('port', 8080)))
        port_frame = tk.Frame(root)
        port_frame.pack(fill='x', padx=16, pady=(0, 8))
        tk.Label(port_frame, text='Porta:', width=9, anchor='w').pack(side='left')
        tk.Entry(port_frame, textvariable=port_val, width=8).pack(side='left')

        # ── OK ──
        def confirm(e=None):
            from tkinter import messagebox
            has_tw = tw_on.get()
            has_ki = ki_on.get()
            has_yt = yt_on.get()

            if has_tw and not tw_ch.get().strip():
                messagebox.showerror('Erro', 'Twitch marcado mas canal em branco.')
                return
            if has_ki and not ki_ch.get().strip():
                messagebox.showerror('Erro', 'Kick marcado mas canal em branco.')
                return
            if has_ki and not ki_id.get().strip():
                messagebox.showerror('Erro', 'Kick marcado mas chatroom ID em branco.')
                return
            if has_yt and not yt_id.get().strip():
                messagebox.showerror('Erro', 'YouTube marcado mas video ID em branco.')
                return
            if not has_tw and not has_ki and not has_yt:
                root.destroy()
                return  # result stays None
            try:
                port = int(port_val.get().strip())
                if not (1 <= port <= 65535):
                    raise ValueError
            except ValueError:
                messagebox.showerror('Erro', 'Porta inválida. Use um número entre 1 e 65535.')
                return

            result[0] = {
                'tw':         has_tw,
                'tw_channel': tw_ch.get().strip(),
                'ki':         has_ki,
                'ki_channel': ki_ch.get().strip(),
                'ki_id':      ki_id.get().strip(),
                'yt':         yt_id.get().strip() if has_yt else '',
                'port':       port,
            }
            save_config({
                'tw_on':          has_tw,
                'tw_channel':     result[0]['tw_channel'],
                'ki_on':          has_ki,
                'ki_channel':     result[0]['ki_channel'],
                'ki_chatroom_id': result[0]['ki_id'],
                'yt_on':          has_yt,
                'yt_video_id':    result[0]['yt'],
                'port':           port,
            })
            root.destroy()

        def reset_defaults():
            tw_on.set(True);  tw_ch.set('xumbr3ga')
            ki_on.set(True);  ki_ch.set('xumbr3ga'); ki_id.set('45573790')
            yt_on.set(False); yt_id.set('')
            port_val.set('8080')

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=(0, 12))
        tk.Button(btn_frame, text='Resetar padrões', command=reset_defaults).pack(side='left', padx=(0, 8))
        tk.Button(btn_frame, text='OK', width=12, command=confirm).pack(side='left')

        root.bind('<Return>', confirm)
        root.protocol('WM_DELETE_WINDOW', root.destroy)  # X = cancelar, não sobe

        root.mainloop()
        return result[0]

    except Exception as e:
        log('start', 'WARN', f'dialog indisponível ({e}) — usando config salva ou padrões')
        cfg = load_config()
        return {
            'tw':         cfg.get('tw_on', True),
            'tw_channel': cfg.get('tw_channel', 'xumbr3ga'),
            'ki':         cfg.get('ki_on', True),
            'ki_channel': cfg.get('ki_channel', 'xumbr3ga'),
            'ki_id':      cfg.get('ki_chatroom_id', '45573790'),
            'yt':         cfg.get('yt_video_id', '') if cfg.get('yt_on', False) else '',
            'port':       cfg.get('port', 8080),
        }


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(cfg: dict):
    global _history_lock, TW_CH, KI_CH, KI_CHATROOM_ID, _msg_count
    _history_lock = asyncio.Lock()

    TW_CH          = cfg['tw_channel']
    KI_CH          = cfg['ki_channel']
    KI_CHATROOM_ID = cfg['ki_id']
    video_id       = cfg['yt']
    port           = cfg['port']

    # Inicializa contador com número real de linhas existentes
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding='utf-8') as f:
            _msg_count = sum(1 for line in f if line.strip())
    else:
        _msg_count = 0

    app = web.Application()
    app.router.add_get('/events', events_handler)
    app.router.add_get('/{path:.*}', static_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', port)
    await site.start()

    W = 66
    h = lambda s: f'  ║{s:<{W}}║'
    print()
    print(f'  ╔{"═"*W}╗')
    print(h('       Xumbr3ga Chat Hub — Servidor ativo'))
    print(f'  ╠{"═"*W}╣')
    print(h(f'  Multi Chat:  http://localhost:{port}/xumbrega_multichat.html'))
    print(h(f'  Overlay:     http://localhost:{port}/xumbrega_overlay_webcam.html'))
    if cfg['tw']:
        print(h(f'  Twitch:      #{TW_CH}'))
    if cfg['ki']:
        print(h(f'  Kick:        {KI_CH}'))
    if video_id:
        print(h(f'  YouTube ID:  {video_id}'))
    print(f'  ╠{"═"*W}╣')
    print(h('  Mantenha esta janela aberta durante a live'))
    print(f'  ╚{"═"*W}╝')
    print()

    if cfg['tw']:
        asyncio.create_task(twitch_loop())
    if cfg['ki']:
        asyncio.create_task(kick_loop())
    if video_id:
        asyncio.create_task(youtube_loop(video_id))
    asyncio.create_task(file_watcher_loop())

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    except NotImplementedError:
        pass  # Windows — Ctrl+C encerra via KeyboardInterrupt normalmente

    await stop.wait()

    # 1. Para de aceitar novas conexões
    await site.stop()

    # 2. Desconecta todos os clientes SSE via sentinel
    for q in list(clients):
        try:
            q.put_nowait(None)
        except asyncio.QueueFull:
            pass

    # 3. Aguarda os clientes saírem (até 2s)
    for _ in range(40):
        if not clients:
            break
        await asyncio.sleep(0.05)

    # 4. Cancela tasks restantes (loops de plataforma, etc.)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # 5. Cleanup final
    await runner.cleanup()


if __name__ == '__main__':
    if not acquire_lock():
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Servidor já rodando', 'Já existe uma instância do servidor rodando nesta máquina.')
            root.destroy()
        except Exception:
            print('ERRO: Já existe uma instância do servidor rodando nesta máquina.')
        sys.exit(1)

    try:
        cfg = ask_startup_config()
        if cfg is None:
            print('Nenhuma plataforma selecionada. Encerrando.')
            sys.exit(0)
        asyncio.run(main(cfg))
        print('Servidor encerrado.')
    finally:
        release_lock()
