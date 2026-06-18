# RealtimeTranslator — Spec do Projeto

App macOS que intercepta o áudio do sistema em tempo real, traduz e entrega o áudio traduzido no lugar do original — funcionando com Zoom, Teams, Meet ou qualquer outro programa de videoconferência.

---

## Objetivo

Intérprete automático bidirecional que opera na camada de áudio do sistema:

1. **O que você ouve** — captura o áudio remoto (o que os outros falam), traduz e entrega o áudio traduzido nos seus fones
2. **O que você fala** — captura sua voz, traduz e entrega o áudio traduzido como seu microfone virtual para o app de meeting

Tudo isso transparente para o app de videoconferência — funciona com qualquer um, sem plugins.

---

## Modos de Operação

O app oferece 3 modos independentes que o usuário liga/desliga:

| Modo | O que faz | Latência |
|---|---|---|
| **Legenda** | Exibe texto original + traduzido numa janela flutuante | ~400–800ms |
| **Áudio traduzido (entrada)** | Você ouve a tradução no lugar do áudio original | ~1–1.5s |
| **Mic traduzido (saída)** | Sua voz sai traduzida para os outros participantes | ~1–1.5s |

### Combinações de uso

- **Só legenda** — reunião onde você entende inglês mas quer apoio visual
- **Legenda + áudio** — você quer ouvir tudo traduzido em PT-BR
- **Legenda + áudio + mic** — intérprete bidirecional completo
- **Só mic** — você entende inglês mas quer falar em PT e os outros ouvirem em EN

---

## Arquitetura

### Caminho de ENTRADA (o que você ouve)

```
App de meeting (Zoom, Teams, Meet...)
        ↓ áudio remoto
Multi-Output Device (BlackHole 2ch + seus fones)
        ↓
App captura do BlackHole 2ch
        ↓
Deepgram STT (WebSocket streaming)
        ↓
DeepL tradução EN → PT-BR
        ↓
   ┌────────────────────┐
   │ Modo Legenda:      │ → Janela flutuante (texto)
   │ Modo Áudio:        │ → TTS → Reproduz nos fones (substitui original)
   └────────────────────┘
```

### Caminho de SAÍDA (o que você fala)

```
Seu microfone real
        ↓
App captura do mic
        ↓
Deepgram STT (WebSocket streaming)
        ↓
DeepL tradução PT-BR → EN
        ↓
TTS (gera áudio em inglês)
        ↓
Output → BlackHole 16ch (mic virtual)
        ↓
App de meeting recebe como se fosse seu microfone
```

### Roteamento de áudio no macOS

```
Dispositivos necessários (Audio MIDI Setup):

1. Multi-Output Device (para captura de entrada)
   ├── BlackHole 2ch  ← app lê daqui (áudio remoto)
   └── Fones/caixas   ← você continua ouvindo normalmente

2. BlackHole 16ch (mic virtual para saída)
   ← app escreve aqui (voz traduzida)
   → meeting app usa como "microfone"

Configuração no sistema:
- Saída do sistema: Multi-Output Device
- No app de meeting: microfone = BlackHole 16ch
```

---

## Stack

| Camada | Tecnologia | Motivo |
|---|---|---|
| Captura áudio remoto | BlackHole 2ch | Driver virtual gratuito, captura output do sistema |
| Mic virtual | BlackHole 16ch | Segundo canal virtual para saída traduzida |
| Leitura do stream | `sounddevice` (Python) | Simples, baixa latência, acessa dispositivos do sistema |
| Speech-to-Text | Deepgram Nova-2 (WebSocket) | Menor latência do mercado (~300ms), streaming nativo |
| Tradução | DeepL API | Melhor qualidade EN↔PT-BR, free tier generoso |
| Text-to-Speech | OpenAI TTS / macOS `say` | OpenAI: boa qualidade + baixa latência. macOS: fallback gratuito |
| UI | PyQt5 | Janela flutuante, always-on-top, controles visuais |
| Configuração | `.env` file | Chaves de API fora do código |

---

## Estrutura de Arquivos

```
realtime-translator/
├── main.py                  # Entry point — orquestra todos os módulos
├── audio_capture.py         # Captura stream do BlackHole (entrada)
├── audio_output.py          # Reproduz áudio traduzido / envia pro mic virtual
├── transcriber.py           # Integração Deepgram WebSocket (STT)
├── translator.py            # Integração DeepL API
├── tts.py                   # Text-to-Speech (OpenAI TTS + fallback macOS)
├── ui.py                    # Janela flutuante de legendas + controles
├── config.py                # Leitura do .env e configurações
├── requirements.txt
├── .env.example
└── README.md
```

---

## Funcionalidades do MVP

### Core
- [ ] Capturar áudio remoto via BlackHole 2ch
- [ ] Capturar áudio do microfone local
- [ ] Transcrever em tempo real via Deepgram WebSocket (2 streams simultâneos)
- [ ] Traduzir cada segmento via DeepL (EN→PT e PT→EN)
- [ ] Gerar áudio traduzido via TTS
- [ ] Reproduzir áudio traduzido nos fones (modo áudio entrada)
- [ ] Enviar áudio traduzido pro BlackHole 16ch (modo mic virtual)

### UI
- [ ] Janela flutuante always-on-top com legendas (original + traduzido)
- [ ] Toggle independente para cada modo (legenda / áudio / mic)
- [ ] Toggle de direção: EN→PT ou PT→EN
- [ ] Botão pausar/retomar
- [ ] Indicador visual de status (capturando, traduzindo, erro)
- [ ] Controle de volume do áudio traduzido

### Controle de áudio
- [ ] No modo "áudio traduzido": abaixar/mutar volume do original, tocar tradução
- [ ] No modo "mic traduzido": capturar mic real, enviar voz traduzida pelo mic virtual
- [ ] Seletor de dispositivo de áudio (mic real, saída de áudio)

---

## Funcionalidades Futuras (pós-MVP)

- Clonagem de voz — traduzir mantendo o timbre da voz original (ElevenLabs Voice Clone)
- Detecção automática de idioma
- Suporte a múltiplos idiomas além de EN↔PT
- Histórico da sessão — salvar transcrição + tradução em arquivo
- Hotkey global para pausar/retomar sem abrir o app
- Menubar icon para controle rápido
- Empacotamento como `.app` nativo do macOS
- Perfis por reunião (configurações salvas por contexto)

---

## Pré-requisitos de Setup

### 1. BlackHole (drivers de áudio virtual)
```bash
brew install blackhole-2ch
brew install blackhole-16ch
```

Após instalar, configurar no **Audio MIDI Setup** do Mac:

**Multi-Output Device (para captura de entrada):**
1. Abrir Audio MIDI Setup
2. Clicar "+" → Create Multi-Output Device
3. Marcar: BlackHole 2ch + seu dispositivo de saída (fones/caixas)
4. Definir como saída padrão do sistema

**Mic virtual (para saída traduzida):**
- No app de meeting, selecionar "BlackHole 16ch" como microfone
- O app vai escrever o áudio traduzido nesse dispositivo

### 2. Python e dependências
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Variáveis de ambiente
Copiar `.env.example` para `.env` e preencher:
```
DEEPGRAM_API_KEY=your_key_here
DEEPL_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
```

---

## APIs necessárias

### Deepgram (Speech-to-Text)
- Criar conta em: https://deepgram.com
- Plano gratuito: $200 de crédito inicial (~550 horas de áudio)
- Modelo: `nova-2`
- Modo: WebSocket streaming (2 conexões simultâneas: uma para entrada, uma para mic)
- Idiomas: `en-US` para stream em inglês, `pt-BR` para stream em português

### DeepL (Tradução)
- Criar conta em: https://www.deepl.com/pro-api
- Plano gratuito: 500.000 caracteres/mês
- Pares: `EN` → `PT-BR` e `PT-BR` → `EN`

### OpenAI TTS (Text-to-Speech)
- Usar API key existente da OpenAI
- Modelo: `tts-1` (otimizado para latência) ou `tts-1-hd` (qualidade)
- Vozes: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
- Fallback: `say` do macOS (gratuito, qualidade inferior)

---

## requirements.txt

```
sounddevice==0.5.1
deepgram-sdk==3.7.7
deepl==1.18.0
openai==1.82.0
python-dotenv==1.0.0
PyQt5==5.15.11
numpy==2.2.6
aiohttp==3.11.18
```

---

## Modelos de IA disponíveis

### Speech-to-Text

| Modelo | Latência | Qualidade | Preço | Nota |
|---|---|---|---|---|
| **Deepgram Nova-2** | ~300ms | boa | $0.006/min | **Escolhido** — WebSocket streaming nativo |
| Whisper (OpenAI) | ~1–3s | excelente | $0.006/min | Lento demais pra tempo real |
| Google STT Streaming | ~400ms | muito boa | $0.016/min | Alternativa viável |
| Azure Speech | ~300ms | muito boa | $1/hora | Caro |

### Tradução

| Modelo | Qualidade EN↔PT | Preço | Nota |
|---|---|---|---|
| **DeepL** | excelente | 500k chars/mês grátis | **Escolhido** — melhor PT-BR |
| Google Translate API | muito boa | $20/1M chars | Alternativa |
| GPT-4o mini | muito boa | barato | Possível mas mais lento |

### Text-to-Speech

| Modelo | Naturalidade | Latência | Preço | Nota |
|---|---|---|---|---|
| **OpenAI TTS** | muito boa | ~300ms | $15/1M chars | **Escolhido** — bom equilíbrio |
| ElevenLabs | excelente | ~500ms | $5/mês | Premium/clonagem de voz |
| Google TTS | boa | ~200ms | $4/1M chars | Alternativa |
| macOS `say` | razoável | ~100ms | gratuito | **Fallback** |

---

## Considerações de Latência

### Modo Legenda

| Etapa | Latência |
|---|---|
| Captura + buffer | ~100ms |
| Deepgram STT | ~200–400ms |
| DeepL tradução | ~100–300ms |
| Render na UI | ~16ms |
| **Total** | **~400–800ms** |

### Modo Áudio / Mic Traduzido

| Etapa | Latência |
|---|---|
| Captura + buffer | ~100ms |
| Deepgram STT | ~200–400ms |
| DeepL tradução | ~100–300ms |
| TTS (OpenAI) | ~300–500ms |
| Playback | ~50ms |
| **Total** | **~750ms–1.3s** |

Ambos aceitáveis. Intérpretes humanos operam com delay de ~2-4s.

---

## Limitações conhecidas

- **Múltiplas vozes simultâneas** — STT pode confundir falantes; funciona melhor com um falante por vez
- **Delay no modo mic** — ~1-1.5s de atraso entre você falar e o outro ouvir; aceitável mas perceptível
- **BlackHole setup manual** — exige configuração no Audio MIDI Setup na primeira vez
- **Vocabulário técnico** — termos específicos podem precisar de Deepgram custom vocabulary
- **Custo com uso pesado** — em uso intensivo, as APIs pagas (Deepgram, OpenAI TTS) geram custo
- **Sem app nativo** — roda via terminal; empacotamento como `.app` é etapa futura
- **Modo áudio entrada** — para mutar o original e tocar só a tradução, o volume do sistema precisa ser gerenciado pelo app

---

## Próximos passos de implementação

1. Implementar `config.py` — leitura do `.env`
2. Implementar `audio_capture.py` — captura de áudio (BlackHole + mic)
3. Implementar `transcriber.py` — Deepgram WebSocket streaming
4. Implementar `translator.py` — DeepL API
5. Implementar `tts.py` — OpenAI TTS + fallback macOS
6. Implementar `audio_output.py` — reprodução nos fones + mic virtual
7. Implementar `ui.py` — janela flutuante com controles
8. Implementar `main.py` — orquestração de tudo
9. Testar cada modo individualmente
10. Testar numa call real
