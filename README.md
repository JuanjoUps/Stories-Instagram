# Generador de stories con Villita (audio + subtitulos)

Pipeline completo, gratuito, para generar stories de Instagram (1080x1920)
con imagenes/clips que tu subes, voz en off (Edge TTS) y subtitulos
quemados con banner.

## Como funciona

1. Rellenas el formulario de Apps Script: subes imagenes/clips y escribes
   el texto de cada segmento.
2. Apps Script sube todo a este repositorio de GitHub y dispara el workflow.
3. GitHub Actions genera el audio + subtitulos (Edge TTS) y monta el video
   final con ffmpeg (fundidos entre segmentos, banner de subtitulos).
4. El Action avisa a Apps Script, que te manda el `.mp4` final por correo.
5. Tu lo revisas y lo subes a Instagram a mano.

Coste: 0€. ffmpeg y Edge TTS son gratuitos y sin limite de uso; los minutos
de GitHub Actions que consume un render de story (segundos de CPU) son
insignificantes frente a la cuota gratuita mensual.

## Puesta en marcha

### 1. Este repositorio
- Sube estas carpetas (`scripts/`, `.github/workflows/`) a tu repo de GitHub.
- En **Settings > Secrets and variables > Actions**, anade el secret:
  - `APPS_SCRIPT_WEBHOOK_URL`: la URL de tu Apps Script publicado como
    aplicacion web (paso 3).

### 2. Apps Script
- Crea un proyecto nuevo, pega `apps-script/Code.gs` y `apps-script/formulario.html`.
- En **Configuracion del proyecto > Propiedades del script**, anade:
  - `GITHUB_TOKEN`: el mismo token que ya usas para el formulario de amistosos
    (necesita permiso `repo` y `workflow`).
  - `GITHUB_OWNER`: tu usuario/organizacion de GitHub.
  - `GITHUB_REPO`: el nombre de este repositorio.
  - `EMAIL_DESTINO`: tu correo, donde quieres recibir los videos.

### 3. Publicar Apps Script como aplicacion web
- **Implementar > Nueva implementacion > Aplicacion web**.
- Ejecutar como: tu mismo. Quien tiene acceso: cualquier usuario (o "cualquiera",
  necesario para que GitHub Actions pueda llamar al webhook).
- Copia la URL que te da y pegala como secret `APPS_SCRIPT_WEBHOOK_URL` en GitHub (paso 1).
- Copia tambien la URL de `doGet` (formulario) para tener acceso directo desde el movil.

## Personalizar el estilo

Todo el aspecto visual (tamano del banner, fuente, color, margenes de
seguridad para no chocar con la UI de Instagram, duracion del fundido) se
controla en `scripts/generate_video.py`, en las constantes del principio del
archivo (`BANNER_HEIGHT`, `FADE`, `FontSize`, etc.).

## Reutilizar el clip de Villita

Como ya generaste con Flow unos clips fijos de Villita moviendo la boca,
usalos como `media` en los primeros segmentos de cada guion (por ejemplo
`villita_intro.mp4`), y las imagenes de cards normales (resultado, fecha,
jugador) en los siguientes. El script detecta automaticamente si el
`media` es imagen o video y lo trata igual.
