# Xumbr3ga Stream Overlays

Overlays feitos pra live — chat multi-plataforma e webcam com chat integrado, conectando **Twitch**, **Kick** e **YouTube** ao vivo.

---

## Arquitetura

```
python server.py --yt LIVE_ID

server.py (hub central)
├── Task: Twitch IRC WebSocket
├── Task: Kick Pusher WebSocket
├── Task: YouTube HTTP polling
├── Task: File watcher (hot-reload, 1s)
├── messages.jsonl  ← histórico NDJSON, zerado a cada start
├── GET /events           → SSE ao vivo
├── GET /events?history=1 → SSE: histórico + ao vivo
└── GET /*                → arquivos estáticos

multichat.html → EventSource('/events?history=1')
overlay.html   → EventSource('/events')
```

Os HTMLs são consumidores SSE puros — sem conexão direta nas plataformas, sem Pusher JS, sem localStorage.

---

## Arquivos

| Arquivo | Descrição |
|---|---|
| `server.py` | Hub central — conecta nas 3 plataformas, distribui via SSE, salva histórico |
| `xumbrega_multichat.html` | Painel de chat multi-plataforma (Twitch + Kick + YouTube) |
| `xumbrega_overlay_webcam.html` | Frame da webcam com chat FIFO integrado para o OBS |

---

## Como usar

### 1. Instale a dependência

```bash
pip install aiohttp
```

### 2. Inicie o servidor

```bash
# Mínimo (Twitch + Kick padrão, sem YouTube):
python server.py

# Com YouTube (passe o Video ID da live):
python server.py --yt VIDEO_ID

# Canais customizados:
python server.py --tw outro_canal --ki outro_canal --ki-id CHATROOM_ID --yt VIDEO_ID
```

**Parâmetros disponíveis:**

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--tw CANAL` | `xumbr3ga` | Canal Twitch |
| `--ki CANAL` | `xumbr3ga` | Canal Kick |
| `--ki-id ID` | `45573790` | Chatroom ID do Kick (fixo por canal) |
| `--yt VIDEO_ID` | _(desativado)_ | Video ID da live no YouTube |

Mantenha a janela aberta durante toda a live. O servidor zera o histórico a cada start.
Para encerrar, pressione `Ctrl+C` — o servidor desconecta todos os clientes SSE antes de fechar.

### 3. Adicione os Browser Sources no OBS

#### Chat completo

- **URL:** `http://localhost:8080/xumbrega_multichat.html`
- **Width:** `400` · **Height:** `1080`
- Recebe histórico da sessão ao conectar + mensagens ao vivo

#### Overlay da webcam

- **URL:** `http://localhost:8080/xumbrega_overlay_webcam.html`
- **Width:** `1920` · **Height:** `1080`
- Só mensagens ao vivo (sem histórico)

---

## Plataformas suportadas

| Plataforma | Canal | Como conecta |
|---|---|---|
| **Twitch** | `--tw` (padrão: `xumbr3ga`) | IRC WebSocket anônimo (automático) |
| **Kick** | `--ki` + `--ki-id` (padrão: `xumbr3ga` / `45573790`) | Pusher WebSocket direto (automático) |
| **YouTube** | `--yt VIDEO_ID` | HTTP polling da live chat (desativado se omitido) |

> O chatroom ID do Kick (`--ki-id`) é fixo por canal e não pode ser buscado via API (bloqueio Cloudflare). Para encontrar o ID de outro canal, inspecione o tráfego WebSocket do Kick no browser.

---

## Reconexão automática

Todos os loops de plataforma usam **exponential backoff** em caso de falha de rede:

- Primeira tentativa falhou → aguarda 5s
- Segunda falhou → 10s → 20s → 40s → máximo 60s
- Ao conectar com sucesso, o backoff reseta para 5s

Falhas temporárias de DNS (comuns no WSL2 ao trocar de rede) se recuperam automaticamente.

---

## Hot-reload

O servidor monitora todos os `.html` da pasta a cada segundo. Se qualquer arquivo for salvo, todos os browsers/OBS conectados recarregam automaticamente — sem precisar clicar em Refresh no OBS.

---

## Chat overlay FIFO

O overlay da webcam exibe mensagens com timing dinâmico baseado no tamanho da fila:

| Mensagens na fila | Duração exibida |
|---|---|
| 0 | 4000ms (máximo) |
| 1 | 2000ms |
| 3 | 1000ms |
| 7 | 500ms |
| 13+ | 300ms (mínimo) |

Isso cria o efeito de scroll rápido durante picos de chat (ex: galera spamando KKKK), sem travar em mensagens individuais quando o chat está calmo.

- Máximo **2 mensagens** visíveis ao mesmo tempo
- A barra de progresso de cada mensagem acompanha o timing real
- Quando a primeira some, a segunda sobe mantendo o tempo restante
- Suporte a emotes da Twitch, Kick e YouTube

---

## Histórico de mensagens

- Salvo em `messages.jsonl` (NDJSON, uma linha por mensagem)
- Zerado automaticamente a cada `python server.py`
- Apenas mensagens de chat são salvas (sys e status não)
- O multichat replaya o histórico ao conectar/reconectar

---

## Requisitos

- Python 3.11+
- `aiohttp` (`pip install aiohttp`)
- Conexão com internet
- OBS com Browser Source

---

## Fontes

- [Orbitron](https://fonts.google.com/specimen/Orbitron) — nicks e badges
- [Rajdhani](https://fonts.google.com/specimen/Rajdhani) — texto das mensagens

Carregadas via Google Fonts (requer internet).
