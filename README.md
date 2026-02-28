# Xumbr3ga Stream Overlays

Overlays feitos pra live — chat multi-plataforma e webcam com chat integrado, conectando **Twitch**, **Kick** e **YouTube** ao vivo.

---

## Arquivos

| Arquivo | Descrição |
|---|---|
| `server.py` | Servidor local (porta 8080) — serve os overlays e faz proxy do YouTube |
| `xumbr3ga-multichat.html` | Painel de chat multi-plataforma completo (Twitch + Kick + YouTube) |
| `xumbrega_overlay_webcam.html` | Frame da webcam com chat FIFO integrado para usar no OBS |

---

## Como usar

### 1. Inicie o servidor

```bash
python server.py
```

Mantenha a janela aberta durante toda a live.

### 2. Adicione os Browser Sources no OBS

#### Chat completo (`xumbr3ga-multichat.html`)

> Painel com todas as mensagens das três plataformas em tempo real.

- **URL:** `http://localhost:8080/xumbr3ga-multichat.html`
- **Width:** `400` · **Height:** `1080` (ou o tamanho do seu painel)
- Na primeira abertura, uma tela de configuração vai aparecer para você inserir o ID do canal do Kick e o Video ID do YouTube

#### Overlay da webcam (`xumbrega_overlay_webcam.html`)

> Moldura roxa para a webcam com chat aparecendo abaixo — mostra 2 mensagens por vez, cada uma por 4 segundos, com barra de progresso individual.

- **URL sem YouTube:**
  ```
  http://localhost:8080/xumbrega_overlay_webcam.html
  ```
- **URL com YouTube** (adicione o Video ID da sua live):
  ```
  http://localhost:8080/xumbrega_overlay_webcam.html?yt=VIDEO_ID
  ```
- **Width:** `1920` · **Height:** `1080`
- Twitch e Kick conectam automaticamente

---

## Plataformas suportadas

| Plataforma | Canal padrão | Como conecta |
|---|---|---|
| **Twitch** | `xumbr3ga` | IRC WebSocket (automático) |
| **Kick** | `xumbr3ga` | Pusher (automático, detecta o chatroom ID) |
| **YouTube** | — | Polling via proxy local (requer Video ID da live) |

> Para trocar os canais da Twitch/Kick, edite as constantes `TW_CH` e `KI_CH` nos arquivos HTML.

---

## Chat overlay FIFO

O overlay da webcam exibe as mensagens do chat com a seguinte lógica:

- Máximo **2 mensagens** visíveis ao mesmo tempo
- Cada mensagem fica **4 segundos** na tela
- Quando a primeira some, a segunda **sobe** mantendo o tempo restante dela
- Uma nova mensagem ocupa o slot de baixo
- Barra de progresso individual em cada mensagem
- Suporte a emotes da Twitch, Kick e YouTube

---

## Requisitos

- Python 3.x
- Conexão com internet (para Twitch IRC, Kick Pusher e YouTube)
- OBS com Browser Source

---

## Fontes usadas

- [Orbitron](https://fonts.google.com/specimen/Orbitron) — nicks e badges
- [Rajdhani](https://fonts.google.com/specimen/Rajdhani) — texto das mensagens

Carregadas via Google Fonts (requer internet).
