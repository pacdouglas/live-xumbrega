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
├── Task: Viewers poll (60s)
├── messages.jsonl  ← histórico NDJSON, zerado a cada start
├── GET /events          → SSE ao vivo
├── GET /events?history=1 → SSE: histórico + ao vivo
└── GET /*               → arquivos estáticos

multichat.html  → EventSource('/events?history=1')
overlay.html    → EventSource('/events')
```

Os HTMLs são consumidores SSE puros — sem conexão direta nas plataformas, sem Pusher JS, sem localStorage.

---

## Arquivos

| Arquivo | Descrição |
|---|---|
| `server.py` | Hub central — conecta nas 3 plataformas, distribui via SSE, salva histórico |
| `xumbr3ga-multichat.html` | Painel de chat multi-plataforma (Twitch + Kick + YouTube + contador de viewers) |
| `xumbrega_overlay_webcam.html` | Frame da webcam com chat FIFO integrado para o OBS |

---

## Como usar

### 1. Instale a dependência

```bash
pip install aiohttp
```

### 2. Inicie o servidor

```bash
# Sem YouTube:
python server.py

# Com YouTube (passe o Video ID da live):
python server.py --yt VIDEO_ID
```

Mantenha a janela aberta durante toda a live. O servidor zera o histórico a cada start.

### 3. Adicione os Browser Sources no OBS

#### Chat completo

- **URL:** `http://localhost:8080/xumbr3ga-multichat.html`
- **Width:** `400` · **Height:** `1080`
- Recebe histórico da sessão ao conectar + mensagens ao vivo
- Exibe contador de viewers (atualizado a cada 60s)

#### Overlay da webcam

- **URL:** `http://localhost:8080/xumbrega_overlay_webcam.html`
- **Width:** `1920` · **Height:** `1080`
- Só mensagens ao vivo (sem histórico)

---

## Plataformas suportadas

| Plataforma | Canal | Como conecta |
|---|---|---|
| **Twitch** | `xumbr3ga` | IRC WebSocket anônimo (automático) |
| **Kick** | `xumbr3ga` | Pusher WebSocket nativo em Python (automático) |
| **YouTube** | — | HTTP polling da live chat (requer `--yt VIDEO_ID`) |

> Para trocar os canais edite as constantes `TW_CH` e `KI_CH` no topo do `server.py`.

---

## Histórico de mensagens

- Salvo em `messages.jsonl` (NDJSON, uma linha por mensagem)
- Zerado automaticamente a cada `python server.py`
- Apenas mensagens de chat são salvas (sys, status e viewers não)
- O multichat replaya o histórico ao conectar/reconectar

---

## Chat overlay FIFO

O overlay da webcam exibe as mensagens com a seguinte lógica:

- Máximo **2 mensagens** visíveis ao mesmo tempo
- Cada mensagem fica **4 segundos** na tela
- Quando a primeira some, a segunda sobe mantendo o tempo restante
- Nova mensagem ocupa o slot de baixo
- Barra de progresso individual por mensagem
- Suporte a emotes da Twitch, Kick e YouTube

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
