# RealtimeTranslator

Interprete automatico em tempo real para macOS. Captura audio do sistema, transcreve, traduz e entrega audio traduzido — funciona com Zoom, Teams, Meet ou qualquer app de videoconferencia.

## Funcionalidades

- **Legenda em tempo real** — texto original + traduzido numa janela flutuante
- **Audio traduzido** — ouve a traducao no lugar do audio original
- **Mic traduzido** — sua voz sai traduzida para os outros participantes
- **12 idiomas** suportados (EN, PT-BR, ES, FR, DE, IT, JA, KO, ZH, RU, NL, PL)
- **Presets** — salve e carregue configuracoes prontas
- **Salvar sessoes** — gere arquivo .txt com transcricao e traducao
- **2 engines de voz** — macOS nativo (gratis) ou OpenAI TTS (melhor qualidade)
- **Controle de volume** independente para audio original e traducao

## Setup

---

## 1. Instalar BlackHole (drivers de audio virtual)

BlackHole cria dispositivos de audio virtuais que permitem o app interceptar o audio do sistema.

```bash
brew install blackhole-2ch
brew install blackhole-16ch
brew install switchaudio-osx
```

### SwitchAudioSource
Utilitario para trocar dispositivos de audio via terminal. O app usa pra:
- Redirecionar saida do sistema pro BlackHole (mutar audio original)
- Redirecionar entrada do sistema pro BlackHole 16ch (suprimir voz real no Mic Out)
- Restaurar tudo ao fechar

**Se os dispositivos nao aparecerem apos instalar**, reinicie o daemon de audio:
```bash
sudo killall coreaudiod
```

**Verificar instalacao:**
```bash
ls /Library/Audio/Plug-Ins/HAL/ | grep BlackHole
# Deve mostrar: BlackHole2ch.driver e BlackHole16ch.driver
```

### O que cada um faz

| Driver | Funcao no app |
|---|---|
| **BlackHole 2ch** | Captura o audio do sistema (o que os outros falam no meeting) |
| **BlackHole 16ch** | Mic virtual — envia sua voz traduzida pro app de meeting |

---

## 2. Criar Multi-Output Device (Audio MIDI Setup)

Isso permite que o audio do sistema va pros seus fones/caixas **e** pro BlackHole ao mesmo tempo.

1. Abrir **Audio MIDI Setup** (Spotlight > digitar "Audio MIDI Setup")
2. Clicar **"+"** no canto inferior esquerdo
3. Selecionar **"Create Multi-Output Device"**
4. Na lista de dispositivos, marcar:
   - **BlackHole 2ch** (obrigatorio — o app le daqui)
   - **Seu dispositivo de saida** (alto-falantes, fones, etc — pra voce continuar ouvindo)
5. Opcional: renomear para "Translator Output" (clique duplo no nome)

> **NOTA**: Voce nao precisa usar o Multi-Output Device como saida padrao.
> O app vai ativar/desativar automaticamente quando necessario.

---

## 3. Python e ambiente virtual

```bash
cd real_time_translator
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 4. API Keys

### 4.1 Criar contas e obter chaves

**Deepgram (Speech-to-Text):**
1. Acessar https://console.deepgram.com/signup
2. Criar conta (pode usar Google/GitHub)
3. No dashboard, ir em **API Keys**
4. Clicar **Create a New API Key**
5. Dar um nome (ex: "RealtimeTranslator") e copiar a chave
6. Free tier: $200 de credito inicial (~550 horas de audio)

**DeepL (Traducao):**
1. Acessar https://www.deepl.com/pro-api
2. Clicar **Sign up for free**
3. Criar conta (pede cartao mas nao cobra no plano free)
4. No dashboard, ir em **API Keys** e copiar a chave
5. Free tier: 500.000 caracteres/mes

**OpenAI (Text-to-Speech):**
1. Acessar https://platform.openai.com/signup
2. Criar conta
3. Ir em **API Keys** > **Create new secret key**
4. Copiar a chave
5. Preco: $15/1M caracteres (sem free tier, mas custo baixo)

### 4.2 Configurar o arquivo .env

```bash
cp .env.example .env
```

Editar `.env` e colar suas chaves:
```
DEEPGRAM_API_KEY=sua_chave_aqui
DEEPL_API_KEY=sua_chave_aqui
OPENAI_API_KEY=sua_chave_aqui
```

---

## 5. Verificar dispositivos de audio

```bash
source venv/bin/activate
python config.py
```

Saida esperada (exemplo):
```
============================================================
  DISPOSITIVOS DE AUDIO
============================================================

  [ 0]    OUT  Seu Dispositivo de Audio
  [ 1] IN/OUT  BlackHole 2ch << BlackHole 2ch
  [ 2] IN/OUT  BlackHole 16ch << BlackHole 16ch
  [ 3]    OUT  Multi-Output Device
  ...
```

Se BlackHole nao aparece: `sudo killall coreaudiod` e rode novamente.

---

## 6. Testar captura de audio

### Testar captura do sistema (BlackHole)
Toque algum audio (YouTube, musica) e rode:
```bash
python audio_capture.py
```
Deve mostrar barras de nivel RMS. Se tudo zero, o Multi-Output Device nao esta ativo como saida.

### Testar microfone
```bash
python audio_capture.py --mic
```
Fale algo — deve mostrar niveis.

### Testar ambos
```bash
python audio_capture.py --both
```

---

## Como funciona no dia a dia

O app gerencia tudo automaticamente:

| Situacao | O que acontece |
|---|---|
| App **fechado** | Tudo normal — audio e mic funcionam como sempre |
| App **aberto**, nenhum modo ativo | Nada muda no audio |
| Ativou **"Legenda"** | Captura audio pra mostrar texto, sem mexer no som |
| Ativou **"Audio Traduzido"** | Troca saida pro Multi-Output, captura e traduz |
| Ativou **"Mic Traduzido"** | Redireciona mic pro BlackHole 16ch |
| **Fechou** o app | Restaura tudo ao estado original |

> Voce usa o computador normalmente. So ativa a traducao quando precisa.

---

## Permissoes do macOS

O macOS exige permissoes explicitas para acessar microfone e controlar audio.
**Conceda todas as permissoes abaixo antes de usar o app.**

### 1. Microfone (obrigatorio)
O app precisa acessar o microfone para o modo "Mic Out".

**System Settings > Privacy & Security > Microphone**
- Autorizar o **Terminal** (ou o app de terminal/IDE que voce usa)
- Se usar VS Code, autorizar tambem o **Visual Studio Code**

> O macOS mostra um popup pedindo permissao na primeira vez que o app tenta acessar o mic.
> Se voce negou sem querer, va em System Settings e ative manualmente.

### 2. Acessibilidade (pode ser necessario)
Alguns controles de audio do sistema (como trocar dispositivo via SwitchAudioSource) podem pedir acesso de acessibilidade.

**System Settings > Privacy & Security > Accessibility**
- Autorizar o **Terminal** (se solicitado)

### 3. Automacao (pode ser necessario)
Se o app usar `osascript` para controle de volume:

**System Settings > Privacy & Security > Automation**
- Autorizar o **Terminal** a controlar **System Events**

### Como verificar se as permissoes estao corretas
```bash
# Testar acesso ao microfone
python audio_capture.py --mic

# Testar troca de dispositivo de audio
SwitchAudioSource -s "BlackHole 2ch" -t output
SwitchAudioSource -s "Seu Dispositivo de Audio" -t output  # restaurar
```

---

## Troubleshooting

### BlackHole nao aparece nos dispositivos
```bash
# Verificar se o driver esta instalado
ls /Library/Audio/Plug-Ins/HAL/ | grep BlackHole

# Reiniciar daemon de audio
sudo killall coreaudiod
```

### Audio nao esta sendo capturado (RMS = 0)
1. Verificar se o Multi-Output Device esta como saida do sistema
2. Verificar se BlackHole 2ch esta marcado no Multi-Output Device
3. Verificar se tem audio tocando

### Permissao de microfone negada
O macOS pode pedir permissao de microfone pro Terminal/IDE.
Ir em **System Settings > Privacy & Security > Microphone** e autorizar.

### Audio nao volta ao normal ao fechar o app
Se o app crashar sem restaurar o audio:
```bash
# Restaurar saida para seus fones/caixas
SwitchAudioSource -s "Seu Dispositivo de Audio" -t output

# Restaurar entrada para seu microfone real
SwitchAudioSource -s "Seu Dispositivo de Audio" -t input
```

### App nao consegue trocar dispositivo de audio
Verificar se `switchaudio-osx` esta instalado:
```bash
which SwitchAudioSource || brew install switchaudio-osx
```
