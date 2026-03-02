# CLAUDE.md — Contexto do projeto Xumbr3ga

## O que é

Servidor Python que agrega chat ao vivo de Twitch, Kick e YouTube e distribui via SSE para overlays HTML usados no OBS como Browser Sources.

## Arquivos

```
server.py                     — hub central asyncio (aiohttp)
xumbrega_multichat.html       — painel de chat multi-plataforma
xumbrega_overlay_webcam.html  — overlay da webcam com chat FIFO
messages.jsonl                — histórico persistente (máx 50.000 msgs, trim de 40.000 ao estourar — mantém os 10.000 mais recentes)
```

## Como rodar

```bash
pip install aiohttp
python server.py                                          # canais padrão, sem YouTube
python server.py --yt VIDEO_ID                           # com YouTube
python server.py --tw CANAL --ki CANAL --ki-id ID --yt VIDEO_ID  # tudo customizado
```

Parâmetros: `--tw` (canal Twitch), `--ki` (canal Kick), `--ki-id` (chatroom ID Kick), `--yt` (video ID YouTube).

URLs no OBS:
- `http://localhost:8080/xumbrega_multichat.html`
- `http://localhost:8080/xumbrega_overlay_webcam.html`

## Constantes importantes (topo do server.py)

```python
TW_CH          = 'xumbr3ga'        # canal Twitch — sobrescrito por --tw
KI_CH          = 'xumbr3ga'        # canal Kick — sobrescrito por --ki
KI_CHATROOM_ID = '45573790'        # ID fixo do chatroom Kick — sobrescrito por --ki-id
PUSHER_KEY     = '32cbd69e4b950bf97679'  # chave pública do Pusher do Kick (não muda)
PUSHER_CLUSTER = 'us2'
```

As três primeiras são os valores padrão; em runtime são sobrescritas pelos args de CLI via `argparse`.

## Decisões de arquitetura

### Kick — chatroom ID hardcoded
O `KI_CHATROOM_ID` é fixo e não deve ser buscado via Python.
A API do Kick (`kick.com/api/v2/channels/{canal}`) passa pelo Cloudflare que bloqueia requests Python com 403 (JA3/JA4 TLS fingerprinting).
O Pusher WebSocket (`ws-us2.pusher.com`) não passa pelo Cloudflare e funciona normalmente.

Para obter o chatroom ID de outro canal: abrir `https://kick.com/api/v2/channels/NOME_DO_CANAL` no **browser** e pegar `chatroom.id` do JSON. O ID é permanente.

### Contador de viewers — removido
- Kick: bloqueado pelo Cloudflare
- Twitch: requer OAuth (`helix/streams`)
- YouTube: funcionava via `youtubei/v1/updated_metadata` mas foi removido junto com os outros por inconsistência

### SSE — como funciona
- Browser abre `GET /events` e mantém conexão permanente
- `broadcast(msg)` enfileira o JSON em todos os clientes conectados simultaneamente
- Keepalive a cada 20s (`': keepalive\n\n'`) para detectar desconexão
- Multichat usa `?history=1` para replay do histórico ao conectar
- Overlay não usa histórico — só mensagens ao vivo

### Reconexão com exponential backoff
Todos os loops de plataforma usam o padrão:
```python
backoff = 5
connected = False
try:
    ...
    # ao confirmar conexão:
    connected = True
    backoff = 5
except Exception as e:
    print(f'[xx] erro: {e}')
set_status('xx', False)
await asyncio.sleep(backoff)
if not connected:
    backoff = min(backoff * 2, 60)
```
Reset em 5s quando conecta com sucesso. Dobra até 60s cap em falhas consecutivas.
Útil para falhas de DNS no WSL2 ao trocar de rede.

### Hot-reload
`file_watcher_loop()` verifica mtime dos `.html` a cada 1s.
Se mudou, broadcast `{'p': 'reload'}` → todos os browsers chamam `window.location.reload()`.
Permite editar os HTMLs sem precisar clicar em Refresh no OBS.

### Overlay FIFO — timing dinâmico
```javascript
const MAX_MS = 4000;
const MIN_MS = 300;
function getDisplayMs() {
  return Math.max(MIN_MS, Math.floor(MAX_MS / (queue.length + 1)));
}
```
Chat calmo → cada mensagem fica até 4s. Pico de mensagens → drena rápido (mín 300ms).
A barra de progresso recebe `animation-duration` por inline style para acompanhar o timing real.

### Formato das mensagens SSE
```json
{"p": "tw", "user": "nick", "color": "#ff0000", "html": "texto com <img> de emotes"}
{"p": "ki", ...}
{"p": "yt", ...}
{"p": "sys", "text": "mensagem de sistema"}
{"p": "status", "platform": "tw", "on": true}
{"p": "reload"}
```

## O que NÃO fazer

- **Não buscar chatroom ID do Kick via API** — Cloudflare bloqueia com 403
- **Não usar viewer count do Kick** — mesmo motivo
- **Não adicionar dependências além de `aiohttp`** — o usuário quer manter simples
- **Não usar `curses`** — testado e descartado; usuário prefere terminal simples
- **Não tentar TUI complexo** — terminal tem limitações, não vale a pena
- **Não subir pro git client secrets** — o projeto não usa OAuth por enquanto

## Melhorias futuras discutidas (não implementadas)

- **Viewer count Twitch**: requer cadastro em dev.twitch.tv + Client Credentials OAuth. Simples de implementar se quiser.
- **Viewer count Kick**: requer `curl-cffi` (`pip install curl-cffi`) com `impersonate="chrome120"` para passar pelo Cloudflare.
- **Viewer count YouTube**: funcionava com `youtubei/v1/updated_metadata` sem autenticação.
- **YouTube GQL não-oficial**: `POST https://gql.twitch.tv/gql` com `Client-ID: kimne78kx3ncx6brgo4mv6wki5h1ko` retorna viewer count sem OAuth. Não implementado.
