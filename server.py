#!/usr/bin/env python3
"""
Xumbr3ga Chat Hub — servidor central SSE
Uso: python server.py --yt LIVE_VIDEO_ID
Browser Sources:
  http://localhost:8080/xumbr3ga-multichat.html
  http://localhost:8080/xumbrega_overlay_webcam.html
"""
import asyncio
import argparse
import json
import html as html_lib
import re
import mimetypes
from pathlib import Path
from aiohttp import web, ClientSession, WSMsgType

DIR = Path(__file__).parent
TW_CH = 'xumbr3ga'
KI_CH = 'xumbr3ga'
PUSHER_KEY = '32cbd69e4b950bf97679'
PUSHER_CLUSTER = 'us2'

clients: set[asyncio.Queue] = set()
HISTORY_FILE = DIR / 'messages.jsonl'
platform_status = {'tw': False, 'ki': False, 'yt': False}
_history_lock: asyncio.Lock | None = None  # initialized in main()


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
    """Append a chat message as one NDJSON line."""
    line = json.dumps(msg, ensure_ascii=False) + '\n'
    async with _history_lock:
        await asyncio.to_thread(_append_line, line)


def _append_line(line: str):
    with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
        f.write(line)


def set_status(p: str, on: bool):
    platform_status[p] = on
    broadcast({'p': 'status', 'platform': p, 'on': on})


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
    import random
    while True:
        try:
            async with ClientSession() as session:
                async with session.ws_connect('wss://irc-ws.chat.twitch.tv:443') as ws:
                    await ws.send_str('CAP REQ :twitch.tv/tags twitch.tv/commands')
                    nick = f'justinfan{10000 + random.randint(0, 89999)}'
                    await ws.send_str(f'NICK {nick}')
                    await ws.send_str(f'JOIN #{TW_CH}')
                    print(f'[tw] conectando como {nick}...', flush=True)
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            for line in msg.data.split('\r\n'):
                                if not line:
                                    continue
                                if line.startswith('PING'):
                                    await ws.send_str('PONG :tmi.twitch.tv')
                                    continue
                                if 'End of /NAMES list' in line:
                                    set_status('tw', True)
                                    broadcast({'p': 'sys', 'text': f'🟣 Twitch conectado — #{TW_CH}'})
                                    print(f'[tw] conectado — #{TW_CH}', flush=True)
                                    continue
                                if 'PRIVMSG' in line:
                                    tags = _tw_tags(line)
                                    user = tags.get('display-name') or 'Anônimo'
                                    color = tags.get('color') or ''
                                    m = re.search(r'PRIVMSG #\S+ :(.+)$', line)
                                    if m:
                                        rendered = tw_render(m.group(1), tags.get('emotes', ''))
                                        chat_msg = {'p': 'tw', 'user': user, 'color': color, 'html': rendered}
                                        print(f'[tw] {user}: {m.group(1)[:60]}', flush=True)
                                        broadcast(chat_msg)
                                        await save_message(chat_msg)
                                    continue
                                if 'USERNOTICE' in line:
                                    tags = _tw_tags(line)
                                    u = tags.get('display-name') or 'Alguém'
                                    msg_id = tags.get('msg-id') or ''
                                    if msg_id in ('sub', 'resub'):
                                        print(f'[tw] sub: {u}', flush=True)
                                        broadcast({'p': 'sys', 'text': f'🟣 {u} assinou na Twitch!'})
                                    elif msg_id in ('subgift', 'anonsubgift'):
                                        print(f'[tw] subgift: {u}', flush=True)
                                        broadcast({'p': 'sys', 'text': f'🟣 {u} deu um sub na Twitch!'})
                                    elif msg_id == 'raid':
                                        viewers = tags.get('msg-param-viewerCount', '?')
                                        print(f'[tw] raid de {u} ({viewers} viewers)', flush=True)
                                        broadcast({'p': 'sys', 'text': f'🟣 Raid de {u} — {viewers} viewers!'})
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
        except Exception as e:
            print(f'[tw] erro: {e}', flush=True)
        print('[tw] desconectado, reconectando em 5s...', flush=True)
        set_status('tw', False)
        await asyncio.sleep(5)


# ── Kick Pusher loop ──────────────────────────────────────────────────────────

KICK_HEADERS = {
    'Accept': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://kick.com/',
    'Origin': 'https://kick.com',
}


async def kick_loop():
    while True:
        try:
            async with ClientSession() as session:
                # Get chatroom ID
                async with session.get(
                    f'https://kick.com/api/v2/channels/{KI_CH}',
                    headers=KICK_HEADERS
                ) as r:
                    if not r.ok:
                        raise Exception(f'HTTP {r.status}')
                    data = await r.json(content_type=None)
                    chatroom_id = str((data.get('chatroom') or {}).get('id', ''))
                    print(f'[ki] chatroom ID: {chatroom_id}', flush=True)

                if not chatroom_id:
                    raise Exception('chatroom ID não encontrado')

                # Connect to Pusher WebSocket without the Pusher JS lib
                url = (
                    f'wss://ws-{PUSHER_CLUSTER}.pusher.com/app/{PUSHER_KEY}'
                    f'?protocol=7&client=py&version=7.6.0'
                )
                async with session.ws_connect(url) as ws:
                    async for msg in ws:
                        if msg.type == WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            ename = event.get('event', '')

                            if ename == 'pusher:connection_established':
                                sub = json.dumps({
                                    'event': 'pusher:subscribe',
                                    'data': {'channel': f'chatrooms.{chatroom_id}.v2'}
                                })
                                await ws.send_str(sub)

                            elif ename == 'pusher_internal:subscription_succeeded':
                                set_status('ki', True)
                                broadcast({'p': 'sys', 'text': f'🟢 Kick conectado — {KI_CH}'})
                                print(f'[ki] conectado — {KI_CH}', flush=True)

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
                                    print(f'[ki] {user}: {text[:60]}', flush=True)
                                    broadcast(chat_msg)
                                    await save_message(chat_msg)

                            elif ename == 'App\\Events\\SubscriptionEvent':
                                d = event.get('data')
                                if isinstance(d, str):
                                    d = json.loads(d)
                                who = (d or {}).get('username', 'Alguém')
                                print(f'[ki] sub: {who}', flush=True)
                                broadcast({'p': 'sys', 'text': f'🟢 {who} assinou no Kick!'})

                            elif ename == 'App\\Events\\GiftedSubscriptionsEvent':
                                d = event.get('data')
                                if isinstance(d, str):
                                    d = json.loads(d)
                                d = d or {}
                                gifted_by = d.get('gifted_by') or 'Alguém'
                                count = len(d.get('gifted_usernames') or []) or 1
                                broadcast({'p': 'sys', 'text': f'🟢 {gifted_by} deu {count} sub(s) no Kick!'})

                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                            break

        except Exception as e:
            print(f'[ki] erro: {e}', flush=True)
        print('[ki] desconectado, reconectando em 5s...', flush=True)
        set_status('ki', False)
        await asyncio.sleep(5)


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

    # Extract initial continuation token
    continuation = None
    while not continuation:
        try:
            async with ClientSession() as session:
                async with session.get(
                    f'https://www.youtube.com/live_chat?v={video_id}&is_popout=1',
                    headers=YT_HEADERS
                ) as r:
                    if not r.ok:
                        raise Exception(f'HTTP {r.status}')
                    html = await r.text()
                    continuation = _yt_extract_token(html)
                    if not continuation:
                        raise Exception('continuation token não encontrado')
        except Exception as e:
            print(f'[yt] connect error: {e}', flush=True)
            await asyncio.sleep(10)

    set_status('yt', True)
    broadcast({'p': 'sys', 'text': '🔴 YouTube conectado!'})
    print(f'[yt] conectado — video_id={video_id}', flush=True)

    is_first = True
    backoff = 0

    while True:
        try:
            async with ClientSession() as session:
                async with session.post(
                    'https://www.youtube.com/youtubei/v1/live_chat/get_live_chat',
                    json={
                        'context': {
                            'client': {'clientName': 'WEB', 'clientVersion': '2.20240101.00.00', 'hl': 'en'}
                        },
                        'continuation': continuation,
                    },
                    headers={'Content-Type': 'application/json'},
                ) as resp:
                    if resp.status == 429:
                        backoff = min((backoff or 10000) * 2, 60000)
                        print(f'[yt] 429 rate limit — backoff {backoff/1000}s', flush=True)
                        await asyncio.sleep(backoff / 1000)
                        continue
                    if not resp.ok:
                        raise Exception(f'HTTP {resp.status}')
                    data = await resp.json(content_type=None)

            lcc = (data.get('continuationContents') or {}).get('liveChatContinuation')
            if not lcc:
                raise Exception('liveChatContinuation ausente')

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
            backoff = 0
            await asyncio.sleep(min(poll_ms, 2000) / 1000)

        except Exception as e:
            if re.search(r'404|ended|not.*found', str(e), re.I):
                set_status('yt', False)
                broadcast({'p': 'sys', 'text': '🔴 YouTube: live encerrada.'})
                return
            print(f'[yt] poll error: {e}', flush=True)
            await asyncio.sleep(8)


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
            print(f'[yt] {user}: {html[:60]}', flush=True)
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


# ── Viewers polling loop ──────────────────────────────────────────────────────

async def viewers_loop(video_id: str):
    while True:
        await asyncio.sleep(60)
        tw_count = ki_count = yt_count = 0
        try:
            async with ClientSession() as session:
                # Kick
                try:
                    async with session.get(
                        f'https://kick.com/api/v2/channels/{KI_CH}',
                        headers=KICK_HEADERS
                    ) as r:
                        if r.ok:
                            d = await r.json(content_type=None)
                            ki_count = int(d.get('viewer_count') or 0)
                except Exception as e:
                    print(f'[viewers/ki] {e}', flush=True)

                # YouTube
                if video_id:
                    try:
                        async with session.post(
                            'https://www.youtube.com/youtubei/v1/updated_metadata',
                            json={
                                'context': {
                                    'client': {'clientName': 'WEB', 'clientVersion': '2.20240101.00.00'}
                                },
                                'videoId': video_id,
                            },
                            headers={'Content-Type': 'application/json'},
                        ) as r:
                            if r.ok:
                                d = await r.json(content_type=None)
                                vc = d.get('viewCount') or {}
                                if isinstance(vc, dict):
                                    vr = vc.get('videoViewCountRenderer') or {}
                                    txt = (
                                        (vr.get('viewCount') or vr.get('shortViewCount') or {})
                                        .get('simpleText', '0')
                                    )
                                else:
                                    txt = str(vc)
                                yt_count = int(re.sub(r'[^\d]', '', txt) or 0)
                    except Exception as e:
                        print(f'[viewers/yt] {e}', flush=True)

        except Exception as e:
            print(f'[viewers] {e}', flush=True)

        total = tw_count + ki_count + yt_count
        print(f'[viewers] tw={tw_count} ki={ki_count} yt={yt_count} total={total}', flush=True)
        broadcast({'p': 'viewers', 'tw': tw_count, 'yt': yt_count, 'ki': ki_count, 'total': total})


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

    # Stream history line by line without loading the whole file
    if want_history:
        def read_lines():
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE, encoding='utf-8') as f:
                    return [l.strip() for l in f if l.strip()]
            return []
        for line in await asyncio.to_thread(read_lines):
            await resp.write(f'data: {line}\n\n'.encode())

    # Send current platform status
    for p, on in platform_status.items():
        status_msg = json.dumps({'p': 'status', 'platform': p, 'on': on})
        await resp.write(f'data: {status_msg}\n\n'.encode())

    # Subscribe to live broadcasts
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    clients.add(q)
    print(f'[sse] cliente conectado (history={want_history}, total={len(clients)})', flush=True)
    try:
        while True:
            try:
                data = await asyncio.wait_for(q.get(), timeout=20)
            except asyncio.TimeoutError:
                # Keepalive ping — detecta desconexão mesmo sem mensagens chegando
                await resp.write(b': keepalive\n\n')
                continue
            await resp.write(data.encode())
    except (ConnectionResetError, asyncio.CancelledError, Exception):
        pass
    finally:
        clients.discard(q)
        print(f'[sse] cliente desconectado (total={len(clients)})', flush=True)
    return resp


# ── Static file handler ───────────────────────────────────────────────────────

async def static_handler(request: web.Request) -> web.Response:
    path = request.match_info.get('path', '') or 'xumbr3ga-multichat.html'
    fpath = (DIR / path).resolve()
    if not str(fpath).startswith(str(DIR)):
        raise web.HTTPForbidden()
    if not fpath.is_file():
        raise web.HTTPNotFound()
    mime, _ = mimetypes.guess_type(str(fpath))
    if fpath.suffix == '.html':
        mime = 'text/html'
    data = await asyncio.to_thread(fpath.read_bytes)
    charset = 'utf-8' if (mime or '').startswith('text/') else None
    return web.Response(body=data, content_type=mime or 'application/octet-stream', charset=charset)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    global _history_lock
    _history_lock = asyncio.Lock()

    parser = argparse.ArgumentParser(description='Xumbr3ga Chat Hub')
    parser.add_argument('--yt', default='', help='YouTube live video ID')
    args = parser.parse_args()
    video_id = args.yt.strip()

    # Zero history on start
    open(HISTORY_FILE, 'w').close()

    app = web.Application()
    app.router.add_get('/events', events_handler)
    app.router.add_get('/{path:.*}', static_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    await site.start()

    print()
    print('  ╔══════════════════════════════════════════════════════╗')
    print('  ║       Xumbr3ga Chat Hub — Servidor ativo             ║')
    print('  ╠══════════════════════════════════════════════════════╣')
    print('  ║  Multi Chat:  http://localhost:8080/xumbr3ga-multichat.html      ║')
    print('  ║  Overlay:     http://localhost:8080/xumbrega_overlay_webcam.html ║')
    if video_id:
        print(f'  ║  YouTube ID:  {video_id:<40} ║')
    else:
        print('  ║  YouTube:     desativado                                            ║')
        print('  ║  Para ativar: python server.py --yt VIDEO_ID                        ║')
        print('  ║  Ex:          python server.py --yt Fpfdw0iXuv8                     ║')
    print('  ╠══════════════════════════════════════════════════════╣')
    print('  ║  Mantenha esta janela aberta durante a live          ║')
    print('  ╚══════════════════════════════════════════════════════╝')
    print()

    asyncio.create_task(twitch_loop())
    asyncio.create_task(kick_loop())
    if video_id:
        asyncio.create_task(youtube_loop(video_id))
    asyncio.create_task(viewers_loop(video_id))

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Servidor encerrado.')
