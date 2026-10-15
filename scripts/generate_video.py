#!/usr/bin/env python3
"""
Genera un vídeo de story de Instagram (1080x1920) a partir de un guion JSON,
con audio TTS (Google Gemini TTS si hay API key, con respaldo automatico en
Edge TTS gratuito), subtítulos quemados sobre un banner semitransparente, y
fundido de entrada/salida entre segmentos.

Uso:
    python generate_video.py guion.json salida/story_final.mp4

Formato de guion.json:
{
  "segmentos": [
    {
      "media": "assets/villita_intro.mp4",
      "texto": "Hoy arranca la pretemporada del Benjamin",
      "voz": "es-ES-AlvaroNeural"
    },
    {
      "media": "assets/card_fecha.png",
      "texto": "Inicio: 7 de septiembre. Plazas disponibles",
      "voz": "es-ES-AlvaroNeural"
    }
  ]
}

Voz:
  Si existe la variable de entorno GOOGLE_TTS_API_KEY, cada segmento se
  intenta generar con Google Gemini TTS (voz "Achird", con acento de Espana
  y estilo de dibujos animados) para que suene igual que el resto de videos
  del club. Si la peticion falla (sin internet, cuota agotada, error de la
  API, etc.) o no hay API key configurada, se usa automaticamente el Edge
  TTS de siempre (el comportamiento que ya funcionaba antes).

Requisitos: pip install edge-tts   |   ffmpeg instalado en el sistema
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import edge_tts

WIDTH, HEIGHT = 1080, 1920
FADE = 0.4                   # segundos de fundido en cada corte (efecto "cortinilla")
BANNER_HEIGHT = 340           # alto del banner de subtitulos (px)
BANNER_BOTTOM_MARGIN = 260    # margen desde abajo para no chocar con la UI de Instagram
FONT_SIZE = 58                 # tamano de fuente de los subtitulos (px, sobre lienzo de 1920 de alto)
FPS = 24                       # fotogramas por segundo del zoom Ken Burns en modo sueno
COLA_SEGUNDOS = 0.6           # margen extra tras terminar el audio de cada segmento
VOZ_PITCH = "+25Hz"            # tono mas agudo, efecto "personaje de dibujos animados" (solo Edge TTS)
VOZ_RATE = "+8%"               # ligeramente mas rapido, mas energico (solo Edge TTS)

# --- Google Gemini TTS (misma voz que el telediario / Villa TV) ---------
GOOGLE_TTS_API_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "")
GOOGLE_TTS_VOZ = os.environ.get("GOOGLE_TTS_VOZ", "Achird")
GOOGLE_TTS_MODELO = os.environ.get("GOOGLE_TTS_MODELO", "gemini-3.8-flash-tts")
GOOGLE_TTS_ESTILO = os.environ.get(
    "GOOGLE_TTS_ESTILO",
    "con acento de España (castellano peninsular, nada de acento latinoamericano), "
    "como un personaje masculino de dibujos animados para niños: chico joven, "
    "simpático y entusiasta, voz clara y juvenil, entonación muy expresiva, ritmo vivo",
)
GOOGLE_TTS_URL = os.environ.get(
    "GOOGLE_TTS_URL", "https://generativelanguage.googleapis.com/v1beta/interactions"
)
GOOGLE_TTS_PAUSA_SEGUNDOS = 6.5   # limite gratuito: ~10 peticiones/minuto
GOOGLE_TTS_MAX_REINTENTOS = 3

USAR_GOOGLE_TTS = bool(GOOGLE_TTS_API_KEY)

_ultima_llamada_google = 0.0


def _pedir_audio_google(texto):
    """Llama a la API de Google Gemini TTS y devuelve el audio en bytes (wav)."""
    global _ultima_llamada_google

    cuerpo = {
        "model": GOOGLE_TTS_MODELO,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": texto,
                        "annotations": [
                            {"type": "speech_metadata", "style": GOOGLE_TTS_ESTILO}
                        ],
                    }
                ],
            }
        ],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": GOOGLE_TTS_VOZ}]},
    }
    data = json.dumps(cuerpo).encode("utf-8")

    ultimo_error = None
    for intento in range(GOOGLE_TTS_MAX_REINTENTOS):
        espera = GOOGLE_TTS_PAUSA_SEGUNDOS - (time.time() - _ultima_llamada_google)
        if espera > 0:
            time.sleep(espera)

        req = urllib.request.Request(
            GOOGLE_TTS_URL,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": GOOGLE_TTS_API_KEY,
            },
        )
        _ultima_llamada_google = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                respuesta = json.loads(resp.read().decode("utf-8"))
            for paso in respuesta.get("steps", []):
                for parte in paso.get("content", []):
                    if parte.get("type") == "audio":
                        return base64.b64decode(parte["data"])
            raise RuntimeError("respuesta de Google TTS sin audio")
        except urllib.error.HTTPError as e:
            ultimo_error = e
            if e.code == 429 and intento < GOOGLE_TTS_MAX_REINTENTOS - 1:
                # limite de peticiones por minuto: esperar y reintentar
                time.sleep(GOOGLE_TTS_PAUSA_SEGUNDOS)
                continue
            raise
        except Exception as e:
            ultimo_error = e
            raise

    raise RuntimeError(f"Google TTS: demasiados reintentos ({ultimo_error})")


def _srt_simple(texto, duracion, out_srt):
    """Crea un SRT de una sola linea que cubre toda la duracion del audio.

    Google TTS no devuelve tiempos por palabra como Edge TTS, asi que no se
    puede resaltar palabra a palabra; se muestra el texto completo durante
    todo el segmento."""

    def fmt(s):
        s = max(s, 0)
        h, resto = divmod(s, 3600)
        m, s = divmod(resto, 60)
        ms = int(round((s - int(s)) * 1000))
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"

    with open(out_srt, "w", encoding="utf-8") as f:
        f.write(f"1\n{fmt(0)} --> {fmt(duracion)}\n{texto}\n")


def generar_audio_srt_google(texto, out_mp3, out_srt):
    """Genera audio con Google Gemini TTS (voz Achird) y un SRT simple."""
    audio_bytes = _pedir_audio_google(texto)

    tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        tmp_wav.write(audio_bytes)
        tmp_wav.close()
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav.name, str(out_mp3)],
            check=True, capture_output=True,
        )
    finally:
        try:
            os.unlink(tmp_wav.name)
        except OSError:
            pass

    _srt_simple(texto, duracion_audio(out_mp3), out_srt)


async def _generar_audio_srt_edge(texto, voz, out_mp3, out_srt, pitch=VOZ_PITCH, rate=VOZ_RATE):
    communicate = edge_tts.Communicate(texto, voz, rate=rate, pitch=pitch)
    submaker = edge_tts.SubMaker()
    with open(out_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write(submaker.get_srt())


def generar_audio_srt(texto, voz, out_mp3, out_srt, pitch=VOZ_PITCH, rate=VOZ_RATE):
    """Genera audio + subtitulos para un segmento.

    Intenta primero Google Gemini TTS (voz Achird) si hay GOOGLE_TTS_API_KEY
    configurada. Si falla por cualquier motivo (sin conexion, cuota, error
    de la API...) o no hay API key, usa Edge TTS como respaldo, exactamente
    como funcionaba antes."""
    if USAR_GOOGLE_TTS:
        try:
            generar_audio_srt_google(texto, out_mp3, out_srt)
            print("  -> audio generado con Google TTS (voz Achird)")
            return
        except Exception as e:
            print(f"  -> Google TTS fallo ({e}); usando Edge TTS de respaldo")

    asyncio.run(_generar_audio_srt_edge(texto, voz, out_mp3, out_srt, pitch, rate))


def duracion_audio(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True
    )
    return float(r.stdout.strip())


def es_video(path):
    return Path(path).suffix.lower() in (".mp4", ".mov", ".m4v")


def construir_segmento(media_path, audio_path, srt_path, duracion, out_path, con_subtitulos=True, modo_sueno=False):
    """Crea un clip de duracion fija: imagen o video, redimensionado a
    1080x1920, con audio TTS, fundido de entrada/salida, y opcionalmente
    subtitulo quemado sobre un banner semitransparente.

    Si modo_sueno=True, aplica zoom lento tipo Ken Burns (solo en imagenes
    fijas, para darles movimiento), resplandor difuminado (glow) y vineta
    en los bordes, como una secuencia de ensueno/flashback cinematografica."""

    if con_subtitulos:
        banner_y = HEIGHT - BANNER_BOTTOM_MARGIN - BANNER_HEIGHT
        srt_escapado = str(srt_path).replace("\\", "/").replace(":", "\\:")
        subtitulos_parte = (
            f"drawbox=x=0:y={banner_y}:w={WIDTH}:h={BANNER_HEIGHT}:color=black@0.45:t=fill,"
            f"subtitles={srt_escapado}:force_style="
            f"'FontName=Arial,FontSize={FONT_SIZE},PrimaryColour=&HFFFFFF&,"
            f"OutlineColour=&H000000&,BorderStyle=1,Outline=4,"
            f"Alignment=2,MarginV={BANNER_BOTTOM_MARGIN + 40},"
            f"PlayResX={WIDTH},PlayResY={HEIGHT}',"
        )
    else:
        subtitulos_parte = ""

    es_img = not es_video(media_path)

    if modo_sueno:
        # Zoom Ken Burns (solo si es imagen fija; un video ya tiene movimiento propio)
        if es_img:
            frames = max(1, round(duracion * FPS))
            zoom_parte = (
                f"scale={WIDTH * 2}:{HEIGHT * 2},"
                f"zoompan=z='min(zoom+0.0015,1.3)':d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS},"
            )
        else:
            zoom_parte = ""

        # Tono calido suave + resplandor difuminado (glow) + vineta en los bordes
        filter_complex = (
            f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,{zoom_parte}"
            f"eq=saturation=0.75:gamma=1.08:contrast=0.95,"
            f"colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.06:gm=0.01:bm=-0.06[tono];"
            f"[tono]split[orig][toblur];"
            f"[toblur]gblur=sigma=16[blurred];"
            f"[orig][blurred]blend=all_mode=screen:all_opacity=0.4[glow];"
            f"[glow]vignette=angle=PI/4:mode=forward,"
            f"{subtitulos_parte}"
            f"fade=t=in:st=0:d={FADE},fade=t=out:st={duracion - FADE}:d={FADE}[outv]"
        )

        if es_img:
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", str(media_path),
                "-i", str(audio_path),
                "-t", str(duracion),
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "1:a",
                "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                "-shortest",
                str(out_path)
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(media_path),
                "-i", str(audio_path),
                "-t", str(duracion),
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "1:a",
                "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                str(out_path)
            ]
        subprocess.run(cmd, check=True)
        return

    # Camino normal, sin modo sueno (igual que antes)
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"{subtitulos_parte}"
        f"fade=t=in:st=0:d={FADE},fade=t=out:st={duracion - FADE}:d={FADE}"
    )

    if es_video(media_path):
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(media_path),
            "-i", str(audio_path),
            "-t", str(duracion),
            "-vf", vf,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            str(out_path)
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(media_path),
            "-i", str(audio_path),
            "-t", str(duracion),
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-shortest",
            str(out_path)
        ]
    subprocess.run(cmd, check=True)


def concatenar(clips, out_path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for c in clips:
            f.write(f"file '{Path(c).resolve()}'\n")
        listado = f.name
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listado,
         "-c", "copy", str(out_path)],
        check=True
    )


def main():
    if len(sys.argv) != 3:
        print("Uso: python generate_video.py guion.json salida/story_final.mp4")
        sys.exit(1)

    guion_path, salida_path = sys.argv[1], sys.argv[2]
    guion = json.loads(Path(guion_path).read_text(encoding="utf-8"))
    base_dir = Path(guion_path).parent  # las rutas "media" son relativas al guion
    con_subtitulos = guion.get("subtitulos", True)

    if USAR_GOOGLE_TTS:
        print("Voz: Google Gemini TTS (Achird) con respaldo en Edge TTS")
    else:
        print("Voz: Edge TTS (no hay GOOGLE_TTS_API_KEY configurada)")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips = []
        for i, seg in enumerate(guion["segmentos"]):
            mp3 = tmp / f"seg{i}.mp3"
            srt = tmp / f"seg{i}.srt"
            generar_audio_srt(
                seg["texto"], seg.get("voz", "es-ES-AlvaroNeural"), mp3, srt,
                pitch=seg.get("pitch", VOZ_PITCH), rate=seg.get("rate", VOZ_RATE)
            )
            dur = duracion_audio(mp3) + COLA_SEGUNDOS
            clip = tmp / f"clip{i}.mp4"
            media_path = base_dir / seg["media"]
            construir_segmento(media_path, mp3, srt, dur, clip, con_subtitulos, seg.get("sueno", False))
            clips.append(clip)

        Path(salida_path).parent.mkdir(parents=True, exist_ok=True)
        concatenar(clips, salida_path)

    print(f"Video generado: {salida_path}")


if __name__ == "__main__":
    main()
