# Xumbr3ga Stream Overlays

Overlays feitos pra live — chat multi-plataforma e webcam com chat integrado, conectando **Twitch**, **Kick** e **YouTube** ao vivo.

---

## Arquitetura

```
python server.py

[ dialog tkinter — configura plataformas e canais ]

server.py (hub central)
├── Task: Twitch IRC WebSocket       (se habilitado)
├── Task: Kick Pusher WebSocket      (se habilitado)
├── Task: YouTube HTTP polling       (se habilitado)
├── Task: File watcher (hot-reload, 1s)
├── config.json     ← configurações persistidas (canais, checkboxes)
├── messages.jsonl  ← histórico NDJSON persistente (máx 50k msgs)
├── GET /events           → SSE ao vivo
├── GET /events?history=1 → SSE: últimas 500 msgs + ao vivo
└── GET /*                → arquivos estáticos

multichat.html → EventSource('/events?history=1')
overlay.html   → EventSource('/events')
```

Os HTMLs são consumidores SSE puros — sem conexão direta nas plataformas, sem Pusher JS, sem localStorage.

---

## Arquivos

| Arquivo | Descrição |
|---|---|
| `server.py` | Hub central — conecta nas plataformas, distribui via SSE, salva histórico |
| `xumbrega_multichat.html` | Painel de chat multi-plataforma (Twitch + Kick + YouTube) |
| `xumbrega_overlay_webcam.html` | Frame da webcam com chat FIFO integrado para o OBS |
| `config.json` | Configurações persistidas (gerado automaticamente) |
| `messages.jsonl` | Histórico de mensagens (gerado automaticamente) |
| `server.lock` | Lock de instância única (gerado automaticamente, apagado ao encerrar) |

---

## Como usar

### 1. Instale a dependência

```bash
pip install aiohttp
```

### 2. Inicie o servidor

```bash
python server.py
```

Um **dialog de configuração** abre automaticamente a cada início:

| Campo | Persiste? | Descrição |
|---|---|---|
| ✅ Twitch (checkbox + canal) | sim | Habilita Twitch e define o canal |
| ✅ Kick (checkbox + canal + chatroom ID) | sim | Habilita Kick e define canal e ID |
| ☐ YouTube (checkbox + video ID) | checkbox sim, ID não | Video ID muda a cada live |
| Porta | sim | Porta HTTP do servidor (padrão: `8080`) |

- Campos persistidos em `config.json` — pré-preenchidos na próxima abertura
- **Resetar padrões** preenche tudo com os dados da xumbr3ga
- Se nenhuma plataforma estiver marcada ao confirmar, o programa encerra
- Se um checkbox estiver marcado com campo em branco, mostra erro e volta ao dialog

Mantenha a janela aberta durante toda a live. O histórico persiste entre sessões.
Para encerrar, pressione `Ctrl+C` — o servidor desconecta todos os clientes SSE antes de fechar.

Apenas uma instância pode rodar por máquina. Tentar abrir uma segunda exibe um erro e encerra.

> **Linux:** requer `python3-tk` (`sudo apt install python3-tk`). No Windows já vem com o Python.

### 3. Adicione os Browser Sources no OBS

#### Chat completo

- **URL:** `http://localhost:PORTA/xumbrega_multichat.html`
- **Width:** `400` · **Height:** `1080`
- Recebe histórico da sessão ao conectar + mensagens ao vivo

#### Overlay da webcam

- **URL:** `http://localhost:PORTA/xumbrega_overlay_webcam.html`
- **Width:** `1920` · **Height:** `1080`
- Só mensagens ao vivo (sem histórico)

---

## Plataformas suportadas

| Plataforma | Canal | Como conecta |
|---|---|---|
| **Twitch** | canal configurado no dialog | IRC WebSocket anônimo (automático) |
| **Kick** | canal + chatroom ID configurados no dialog | Pusher WebSocket direto (automático) |
| **YouTube** | video ID informado no dialog a cada live | HTTP polling da live chat |

> O chatroom ID do Kick é fixo por canal e não pode ser buscado via API em Python (bloqueio Cloudflare). Para encontrar o ID de outro canal, abra no **browser** (não no Python):
>
> ```
> https://kick.com/api/v2/channels/NOME_DO_CANAL
> ```
>
> Procure pelo campo `"chatroom"` → `"id"` no JSON retornado. Exemplo:
>
> ```json
> { "chatroom": { "id": 45573790, ... } }
> ```
>
> Esse ID é permanente — não muda entre lives.

---

## Eventos especiais

Além das mensagens de chat, o servidor detecta e exibe automaticamente eventos de engajamento como mensagens de sistema nos overlays:

| Plataforma | Evento | Exibição |
|---|---|---|
| Twitch | Sub / Resub | `🟣 Nick assinou na Twitch!` |
| Twitch | Subgift | `🟣 Nick deu um sub na Twitch!` |
| Twitch | Raid | `🟣 Raid de Nick — X viewers!` |
| Kick | Sub | `🟢 Nick assinou no Kick!` |
| Kick | Gifted subs | `🟢 Nick deu N sub(s) no Kick!` |
| YouTube | Super Chat | `🔴 Super Chat de Nick: R$ X` + mensagem com cor dourada |
| YouTube | Novo membro | `🔴 Nick se tornou membro!` |

Nenhuma configuração adicional é necessária — tudo é detectado automaticamente via IRC/Pusher/polling.

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
- **Persiste entre sessões** — ao iniciar uma nova live os comentários anteriores já estão disponíveis no multichat
- Limite de **50.000 mensagens** — ao estourar, mantém apenas as 10.000 mais recentes
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
